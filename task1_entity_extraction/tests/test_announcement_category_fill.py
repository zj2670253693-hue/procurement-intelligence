"""Base-only category fallback, with explicit source boundaries and no guessing."""

import json

import pytest

from extractor.llm_extractor import LLMExtractor
from test_dual_layer_pipeline import RecordingExtractor, make_pipeline, write_attachment_zip, write_html
from test_legacy_base_layer import _RecordingExtractor, _pipeline, _write_announcement


def metadata(value="服务/其他服务", project="虚构项目", number=1):
    return f"--- HTML表格{number} ---\n公告信息：\n采购项目名称 | {project}\n品目 | {value}\n采购单位 | 虚构单位"


def source(value="服务/其他服务", tail=""):
    return "名称：虚构服务\n成交金额：100元\n\n" + metadata(value) + "\n" + tail


def extract(monkeypatch, mode, text, categories):
    extractor = object.__new__(LLMExtractor)
    response = {"detail_entities" if mode == "details" else "entities": [
        {"product_name": "虚构服务", "category": category, "brand": "虚构供应商",
         "spec_model": "X-1", "unit_price": "100元", "quantity": "1项", "total_price": "100元",
         "package_name": "包A", "source_page": "3"} for category in categories
    ]}
    # Replace only the external model call; parsing, cleanup and dedup stay real.
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps(response, ensure_ascii=False))
    if mode == "legacy":
        result = extractor.extract(text, announcement_id="fictional-category-fill")
        assert result.success, result.error_message
        return result.entities
    if mode == "summary":
        result = extractor.extract_summary(text, has_tables=True, detail_status="available")
        assert result.success, result.error_message
        return result.entities
    details, status, error = extractor.extract_details(texts={"明细.txt": text}, tables={})
    assert status == "available", error
    return details


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("category", [
    "服务/其他服务", "其他计算机软件",
    "货物/设备/办公设备/液晶显示器,货物/设备/通信设备/光通信设备",
])
def test_base_fills_only_empty_category_from_declared_announcement_table(monkeypatch, mode, category):
    rows = extract(monkeypatch, mode, source(category), [""])
    assert rows[0].model_dump() == {
        "product_name": "虚构服务", "category": category, "brand": "虚构供应商",
        "spec_model": "X-1", "unit_price": "100元", "quantity": "1项", "total_price": "100元",
    }


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("text", [
    "品目：其他服务\n名称：虚构服务\n成交金额：100元",
    "公告信息：\n采购项目名称 | 虚构项目\n品目 | 其他服务\n成交金额：100元",
    "--- HTML表格1 ---\n品目 | 其他服务\n成交金额：100元",
    "--- HTML表格1 ---\n公告信息：\n品目 | 其他服务\n成交金额：100元",
    source("其他服务", "\n--- HTML表格2 ---\n公告信息：\n采购项目名称 | 另一个项目\n品目 | 其他服务"),
    source("其他服务", "\n" + metadata("其他软件", number=2)),
    source("其他服务", "\n品目名称：特定行级品目"),
    source("其他服务", "\n采购标的 | 品目名称\n虚构服务 | 软件服务"),
    source("其他服务", "\n采购标的：虚构服务 | 品目名称：特定服务"),
    source("其他服务", "\n采购标的 | 名称：虚构服务；品目名称：特定服务"),
    source("其他服务", "\n--- HTML表格2 ---\n采购标的 | 名称：虚构服务；品目名称：特定服务"),
    source("其他服务", "\n--- HTML表格2 ---\n采购标的 | 品目名称\n虚构服务 | 软件服务"),
    source("其他服务", "\n--- 附件：明细.txt ---\n采购标的 | 品目编号\n虚构服务 | C100"),
    source("其他服务", "\n--- HTML表格2 ---\n类型 | 名称\n服务类 | 虚构服务"),
    source("其他服务", "\n--- HTML表格2 ---\n服务类\n名称：虚构服务"),
    "名称：虚构服务\n成交金额：100元\n--- 附件：其他公告.txt ---\n" + metadata(),
])
def test_missing_scope_or_row_category_or_conflicting_metadata_defers_fill(monkeypatch, mode, text):
    assert extract(monkeypatch, mode, text, [""])[0].category == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("value", [
    "", "未公示", "无", "公告中品目未公示", "详见附件", "-", "/",
    "不适用", "未公布", "暂无", "未提供", "N/A", "null", "以附件为准",
])
def test_placeholder_metadata_is_not_a_category(monkeypatch, mode, value):
    assert extract(monkeypatch, mode, source(value), [""])[0].category == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_nonempty_category_stays_with_its_original_source_value(monkeypatch, mode):
    text = source("服务/其他服务", "\n品目名称 | 采购标的\n特定服务 | 虚构服务")
    assert extract(monkeypatch, mode, text, ["特定服务"])[0].category == "特定服务"


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_fill_does_not_trigger_second_dedup(monkeypatch, mode):
    rows = extract(monkeypatch, mode, source(), ["", "服务/其他服务"])
    assert len(rows) == 2
    assert [row.category for row in rows] == ["服务/其他服务", "服务/其他服务"]


def test_attachment_details_do_not_inherit_announcement_category(monkeypatch):
    rows = extract(monkeypatch, "details", source(), [""])
    assert rows[0].category == ""
    assert (rows[0].package_name, rows[0].source_file, rows[0].source_page) == ("包A", "明细.txt", "3")


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("project", ["-", "详见附件", "未公示", "N/A"])
def test_placeholder_project_name_does_not_establish_announcement_identity(monkeypatch, mode, project):
    text = "名称：虚构服务\n成交金额：100元\n" + metadata(project=project)
    assert extract(monkeypatch, mode, text, [""])[0].category == ""


def test_legacy_resume_invalidates_category_fill_rule_version(tmp_path, monkeypatch):
    import pipeline as module
    html = _write_announcement(tmp_path, "fictional-category-fill-legacy")
    pipeline = _pipeline(tmp_path, _RecordingExtractor())
    first = pipeline.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(module, "ANNOUNCEMENT_CATEGORY_RULE_VERSION", "test-fill-next", raising=False)
    second = pipeline.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash


def test_summary_resume_invalidates_fill_but_does_not_rerun_unchanged_details(tmp_path, monkeypatch):
    import pipeline as module
    html = write_html(tmp_path, "fictional-category-fill-both", "测试设备，成交金额100元")
    write_attachment_zip(html, "测试设备 | 100元 | 1台")
    pipeline = make_pipeline(tmp_path, RecordingExtractor())
    first = pipeline.process_single_file(str(html), run_mode="both")
    monkeypatch.setattr(module, "ANNOUNCEMENT_CATEGORY_RULE_VERSION", "test-fill-next", raising=False)
    second = pipeline.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash == second.detail_input_hash
