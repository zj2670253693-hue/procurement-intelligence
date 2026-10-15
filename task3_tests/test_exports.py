import hashlib
import json
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET
import pytest
from task3_tests.test_datasets import DatasetConnection
from task3_tests.test_jobs import spec

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


@pytest.fixture
def export_input(platform_module, tmp_path, actor_a, clock):
    connection = DatasetConnection(tmp_path / "exports.sqlite")
    jobs = platform_module("job_store").JobStore(connection, clock)
    c = platform_module("contracts")
    job = jobs.create_receiving(actor_a, spec(c, mode="extract"), "export")
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime")
    task = paths.task(job.task_id)
    task.inputs.mkdir(parents=True)
    html = task.inputs / "t20990101_1.html"
    html.write_text("""<h2>合成项目中标公告</h2><div class='vF_detail_content'>
      <table><tr><th>公告信息：</th></tr><tr><td>采购项目名称</td><td>合成项目</td></tr><tr><td>采购单位</td><td>合成单位</td></tr></table>
      <p>三、中标（成交）信息：</p><p>供应商名称：合成供应商</p><p>中标金额：1.23000000万元</p>
      <p>四、主要标的信息：</p><table><tr><th>货物名称</th><th>品牌</th><th>规格型号</th><th>数量</th><th>单价(万元)</th><th>总价(万元)</th></tr>
      <tr><td>合成设备</td><td>甲牌</td><td>A1</td><td>1套</td><td>1.23000000</td><td>1.23000000</td></tr></table></div>""", encoding="utf-8")
    prepared = c.PreparedInput(html.stem, html, None, hashlib.sha256(html.read_bytes()).hexdigest(), None)
    jobs.finalize(job.task_id, {"files": [], "inputs": [prepared.to_dto()]})
    lease = jobs.claim("worker")
    task.extraction.mkdir()
    raw = task.extraction / "raw_results.json"
    data = [{"announcement_id": html.stem, "success": True,
             "entities": [{"product_name": "合成设备", "category": "设备", "brand": "甲牌", "spec_model": "A1",
                           "quantity": "1套", "unit_price": "1.23000000万元", "total_price": "1.23000000万元"}],
             "detail_entities": [], "extra_info": {}}]
    raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    yield job, [prepared], raw, task, jobs, paths, lease
    connection.db.close()


def test_saved_three_tables_exact_decimals_and_empty(platform_module, export_input):
    module = platform_module("exports")
    job, inputs, raw, task, *_ = export_input
    artifacts, report = module.build_exports(job, inputs, raw, task.exports)
    workbook = task.exports / "standard.xlsx"
    assert workbook.is_file()
    assert any(artifact.kind == "standard_workbook" for artifact in artifacts)
    with zipfile.ZipFile(workbook) as archive:
        wb = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet_names = [sheet.attrib["name"] for sheet in wb.findall("{%s}sheets/{%s}sheet" % (MAIN, MAIN))]
        assert sheet_names == ["总表", "标的表", "投标人表"]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        cells = {cell.attrib["r"]: cell for cell in sheet.iter("{%s}c" % MAIN)}
        assert cells["I2"].find("{%s}v" % MAIN).text == "1.23000000"
        assert cells["H2"].find("{%s}is/{%s}t" % (MAIN, MAIN)).text == "1套"
        assert not list(sheet.iter("{%s}f" % MAIN))
        first = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        url_header = next(cell for cell in first.iter("{%s}c" % MAIN) if cell.attrib["r"] == "B1")
        assert url_header.attrib["s"] == "2"
    assert report["row_counts"]["标的表"] == 1


def test_zero_results_produce_real_header_only_workbook(platform_module, export_input):
    module = platform_module("exports")
    job, inputs, raw, task, *_ = export_input
    raw.write_bytes(b"[]")
    _, report = module.build_exports(job, inputs, raw, task.exports)
    assert report["row_counts"] == {"总表": 0, "标的表": 0, "投标人表": 0}
    with zipfile.ZipFile(task.exports / "standard.xlsx") as archive:
        for index in range(1, 4):
            sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            assert len(sheet.findall("{%s}sheetData/{%s}row" % (MAIN, MAIN))) == 1


def test_formula_text_is_never_written_as_formula(platform_module, export_input):
    module = platform_module("exports")
    job, inputs, raw, task, *_ = export_input
    data = json.loads(raw.read_bytes())
    data[0]["entities"][0]["product_name"] = "=1+1"
    raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    module.build_exports(job, inputs, raw, task.exports)
    with zipfile.ZipFile(task.exports / "standard.xlsx") as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        cell = next(cell for cell in sheet.iter("{%s}c" % MAIN) if cell.attrib["r"] == "D2")
        assert cell.attrib["t"] == "inlineStr" and cell.find("{%s}is/{%s}t" % (MAIN, MAIN)).text == "=1+1"
        assert not list(sheet.iter("{%s}f" % MAIN))


def test_download_scope_and_tamper(platform_module, export_input, actor_a, actor_b):
    exports = platform_module("exports")
    service_module = platform_module("artifacts")
    job, inputs, raw, task, jobs, paths, lease = export_input
    artifacts, _ = exports.build_exports(job, inputs, raw, task.exports)
    service = service_module.ArtifactService(jobs, paths)
    service.register(lease, artifacts)
    verified = service.resolve(actor_a, job.upload_dataset_id, "standard.xlsx")
    assert verified.path == task.exports / "standard.xlsx"
    with pytest.raises(platform_module("contracts").PlatformError):
        service.resolve(actor_b, job.upload_dataset_id, "standard.xlsx")
    with pytest.raises(platform_module("contracts").PlatformError):
        service.resolve(actor_a, job.upload_dataset_id, "../raw_results.json")
    verified.path.write_bytes(b"tampered")
    with pytest.raises(platform_module("contracts").PlatformError) as rejected:
        service.resolve(actor_a, job.upload_dataset_id, "standard.xlsx")
    assert rejected.value.code == "artifact_integrity"


def test_partial_diagnostics_are_not_lost(platform_module, export_input):
    module = platform_module("exports")
    job, inputs, raw, task, *_ = export_input
    data = json.loads(raw.read_bytes())
    data[0]["stage_status"] = "partial"
    data[0]["diagnostics"] = [{"code": "attachment_parse_failed"}, {"code": "extra_schema_invalid"}]
    raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _, report = module.build_exports(job, inputs, raw, task.exports)
    errors = json.loads((task.exports / "errors.json").read_bytes())
    assert {error["code"] for error in errors} == {"attachment_parse_failed", "extra_schema_invalid"}
    assert report["incomplete"] is True


def test_pipeline_diagnostic_flags_export_actual_failure_only(platform_module,export_input):
    module=platform_module("exports")
    job,inputs,raw,task,*_=export_input
    data=json.loads(raw.read_bytes())
    data[0]["diagnostics"]={"schema_valid":True,"extra_valid":False,"coverage_gap":False,"parser_partial":False,"detail_partial":False}
    data[0]["stage_status"]="partial"
    raw.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    module.build_exports(job,inputs,raw,task.exports)
    errors=json.loads((task.exports/"errors.json").read_bytes())
    assert [error["code"] for error in errors]==["extra_extraction_failed"]


def test_export_quota_and_retry_same_inputs(platform_module, export_input):
    module = platform_module("exports")
    job, inputs, raw, task, *_ = export_input
    with pytest.raises(platform_module("contracts").PlatformError):
        module.build_exports(job, inputs, raw, task.exports, max_output_bytes=32)
    assert sum(path.stat().st_size for path in task.exports.iterdir() if path.is_file()) <= 32
    first, _ = module.build_exports(job, inputs, raw, task.exports)
    second, _ = module.build_exports(job, inputs, raw, task.exports)
    assert first == second


def test_readback_rejects_numeric_type_and_gray_style_tamper(platform_module, export_input):
    module = platform_module("exports")
    job, inputs, raw, task, *_ = export_input
    module.build_exports(job, inputs, raw, task.exports)
    projection = json.loads((task.exports / "standard_projection.json").read_bytes())
    workbook = task.exports / "standard.xlsx"
    with zipfile.ZipFile(workbook) as archive:
        content = {name: archive.read(name) for name in archive.namelist()}
    content["xl/worksheets/sheet1.xml"] = content["xl/worksheets/sheet1.xml"].replace(b'r="B1" s="2"', b'r="B1" s="1"')
    altered = task.exports / "altered.xlsx"
    with zipfile.ZipFile(altered, "w") as archive:
        for name, value in content.items():
            archive.writestr(name, value)
    with pytest.raises(platform_module("contracts").PlatformError):
        module.verify_workbook(altered, projection["tables"], projection["headers"])


def test_export_child_stderr_is_surfaced_not_discarded():
    """A bare export_child_failed made a real production failure undiagnosable."""
    source = (Path(__file__).resolve().parents[1] / "task3_platform/exports.py").read_text(encoding="utf-8")
    assert "stderr=subprocess.PIPE" in source
    assert "stderr=subprocess.DEVNULL" not in source
    assert 'raise PlatformError("export_child_failed:"' in source
    assert "no_stderr" in source
    assert "child.stderr.close()" in source
