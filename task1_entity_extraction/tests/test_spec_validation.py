"""Specs are literal attributes, not citations, promises or procurement IDs."""

import json

import pytest

from extractor.llm_extractor import LLMExtractor


def _run(monkeypatch, mode, specs, source, *, status="available", name="测试设备（包1）"):
    extractor = object.__new__(LLMExtractor)
    rows = [{
        "product_name": name, "category": "", "brand": "测试供应商",
        "spec_model": spec, "unit_price": "100元", "quantity": "1台",
        "total_price": "100元", "package_name": "包1", "source_page": "2",
    } for spec in specs]
    payload = {"detail_entities" if mode == "details" else "entities": rows}
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps(payload, ensure_ascii=False))
    if mode == "legacy":
        result = extractor.extract(source, announcement_id="fictional-spec")
        assert result.success, result.error_message
        return result.entities
    if mode == "summary":
        result = extractor.extract_summary(source, has_tables=True, detail_status=status,
                                           announcement_id="fictional-spec")
        assert result.success, result.error_message
        return result.entities
    entities, detail_status, error = extractor.extract_details(
        texts={"清单.txt": source}, tables={}, announcement_id="fictional-spec",
    )
    assert detail_status == "available", error
    return entities


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
@pytest.mark.parametrize("spec", [
    "详见附件", "详见磋商文件第二章采购需求", "按照招标要求提供",
    "详见招标文件。", "参见采购文件第三章技术要求", "详见包1报价明细表",
])
def test_all_entrypoints_clear_pure_spec_reference_or_promise(monkeypatch, mode, spec):
    result = _run(monkeypatch, mode, [spec], "名称 | 测试设备\n成交金额：100元")
    assert result[0].spec_model == ""


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
@pytest.mark.parametrize("spec", [
    "BeneVision V700", "定制化", "25kg/袋，符合GB/T1354标准",
    "型号X100，其他参数详见附件", "详见附件，型号X100",
    "软件V2.0；带宽100Mbps", "附件缺失",
    "按照招标要求提供，配置32GB内存和1TB硬盘",
])
def test_real_spec_or_spec_with_reference_note_is_not_cleared(monkeypatch, mode, spec):
    result = _run(monkeypatch, mode, [spec], "规格型号：" + spec + "\n成交金额：100元")
    assert result[0].spec_model == spec


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
def test_pure_package_label_is_not_a_spec(monkeypatch, mode):
    result = _run(monkeypatch, mode, ["包1"], "包号：包1\n名称：网络线路租赁\n成交金额：100元")
    assert result[0].spec_model == ""


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
def test_literal_model_not_deleted_just_because_it_looks_like_a_package_label(monkeypatch, mode):
    result = _run(monkeypatch, mode, ["包1"], "规格型号 | 包1\n成交金额：100元")
    assert result[0].spec_model == "包1"


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
def test_clearing_specs_does_not_merge_previously_distinct_rows(monkeypatch, mode):
    result = _run(monkeypatch, mode, ["详见附件", "按照招标要求提供"],
                  "名称：测试设备\n成交金额：100元")
    assert len(result) == 2
    assert [row.spec_model for row in result] == ["", ""]
    assert [row.brand for row in result] == ["测试供应商", "测试供应商"]
    assert [(row.unit_price, row.quantity, row.total_price) for row in result] == [
        ("100元", "1台", "100元"), ("100元", "1台", "100元"),
    ]


@pytest.mark.parametrize("status", ["missing", "unreadable"])
def test_missing_attachment_reference_keeps_existing_display(monkeypatch, status):
    result = _run(monkeypatch, "summary", ["详见附件2"],
                  "规格型号：详见附件2\n成交金额：100元", status=status)
    assert result[0].spec_model == "附件缺失"


def test_detail_spec_cleanup_keeps_source_metadata(monkeypatch):
    result = _run(monkeypatch, "details", ["详见附件"], "名称：测试设备\n成交金额：100元")
    assert result[0].spec_model == ""
    assert (result[0].package_name, result[0].source_file, result[0].source_page) == (
        "包1", "清单.txt", "2",
    )


def test_chinese_package_numbers_do_not_match_a_longer_package_number(monkeypatch):
    result = _run(monkeypatch, "details", ["包十"], "包号：包十一",
                  name="设备（包十一）")
    # The helper fixture's own package is 包1, also different from 包十.
    assert result[0].spec_model == "包十"


def test_another_packages_label_in_the_source_is_not_current_row_evidence(monkeypatch):
    result = _run(monkeypatch, "summary", ["包2"], "包号：包2\n包号：包1",
                  name="设备（包1）")
    assert result[0].spec_model == "包2"


def test_package_like_literal_model_in_table_with_note_is_kept(monkeypatch):
    result = _run(monkeypatch, "summary", ["包1"],
                  "名称 | 规格型号\n设备 | 包1（其他参数详见附件）\n成交金额：100元")
    assert result[0].spec_model == "包1"
