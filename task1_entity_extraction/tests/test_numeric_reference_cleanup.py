"""Whole citations are not prices/quantities; mixed real values must survive."""

import json

import pytest

from extractor.llm_extractor import LLMExtractor
from test_dual_layer_pipeline import RecordingExtractor, make_pipeline, write_attachment_zip, write_html
from test_legacy_base_layer import _RecordingExtractor, _pipeline, _write_announcement


def extract_rows(monkeypatch, mode, rows, status="available"):
    extractor = object.__new__(LLMExtractor)
    key = "detail_entities" if mode == "details" else "entities"
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({key: rows}, ensure_ascii=False))
    source = "采购标的：测试设备\n规格型号：X-1\n成交金额：100元\n单价及数量信息见采购清单"
    if mode == "legacy":
        result = extractor.extract(source, detail_status=status, announcement_id="fictional-reference")
        assert result.success, result.error_message
        return result.entities
    if mode == "summary":
        result = extractor.extract_summary(source, has_tables=True, detail_status=status,
                                           announcement_id="fictional-reference")
        assert result.success, result.error_message
        return result.entities
    entities, actual_status, error = extractor.extract_details(
        texts={"清单.txt": source}, tables={}, announcement_id="fictional-reference")
    assert actual_status == "available", error
    return entities


def row(unit_price, quantity):
    return {"product_name": "测试设备", "category": "", "brand": "真实供应商",
            "spec_model": "X-1", "unit_price": unit_price, "quantity": quantity,
            "total_price": "100元", "package_name": "包1", "source_page": "2"}


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
@pytest.mark.parametrize("reference", [
    "详见附件", "见附件", "详见附件2。", "具体详见本附件A",
    "详见采购文件", "参见招标文件第三章采购需求", "详见包1报价明细表",
    "按招标要求提供", "按采购要求提供", "按采购文件要求",
])
def test_every_entrypoint_clears_whole_numeric_citations(monkeypatch, mode, reference):
    entities = extract_rows(monkeypatch, mode, [row(reference, reference)])
    assert (entities[0].unit_price, entities[0].quantity) == ("", "")
    assert (entities[0].product_name, entities[0].brand, entities[0].spec_model, entities[0].total_price) == (
        "测试设备", "真实供应商", "X-1", "100元")


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("status,want", [
    ("missing", "附件缺失"), ("unreadable", "附件缺失"),
    ("available", ""), ("no_attachment", ""), ("extraction_failed", ""), (None, ""),
])
def test_only_known_attachment_failure_marks_citation_missing(monkeypatch, mode, status, want):
    entities = extract_rows(monkeypatch, mode, [row("详见附件2", "见附件")], status=status)
    assert (entities[0].unit_price, entities[0].quantity) == (want, want)


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_procurement_file_citation_is_not_attachment_failure(monkeypatch, mode):
    entities = extract_rows(monkeypatch, mode, [row("详见采购文件", "详见招标文件")], status="missing")
    assert (entities[0].unit_price, entities[0].quantity) == ("", "")


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
@pytest.mark.parametrize("price,quantity", [
    ("100元，详见附件", "1台（详见附件）"),
    ("详见附件，100元", "详见附件：2套"),
    ("100元/年", "1338人"),
    ("附件缺失", "附件缺失"),
    ("100元", "1.0000项"),
])
def test_actual_numeric_values_or_values_with_notes_are_untouched(monkeypatch, mode, price, quantity):
    entities = extract_rows(monkeypatch, mode, [row(price, quantity)], status="missing")
    assert (entities[0].unit_price, entities[0].quantity) == (price, quantity)


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
def test_cleanup_does_not_merge_rows_that_become_identical(monkeypatch, mode):
    entities = extract_rows(monkeypatch, mode, [row("详见附件", "见附件"), row("见附件", "详见附件")])
    assert len(entities) == 2
    assert [(entity.unit_price, entity.quantity) for entity in entities] == [("", ""), ("", "")]
    if mode == "details":
        assert [(entity.package_name, entity.source_file, entity.source_page) for entity in entities] == [
            ("包1", "清单.txt", "2"), ("包1", "清单.txt", "2")]


def test_legacy_resume_reruns_when_numeric_reference_rule_changes(tmp_path, monkeypatch):
    import pipeline as pipeline_module
    html = _write_announcement(tmp_path, "fictional-reference-resume")
    extractor = _RecordingExtractor()
    pipeline = _pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(pipeline_module, "NUMERIC_REFERENCE_RULE_VERSION", "test-reference-next", raising=False)
    second = pipeline.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    assert len(extractor.legacy_calls) == 2
    assert first.summary_input_hash != second.summary_input_hash


def test_summary_and_details_resume_rerun_when_numeric_reference_rule_changes(tmp_path, monkeypatch):
    import pipeline as pipeline_module
    html = write_html(tmp_path, "fictional-reference-both-resume", "测试设备，成交金额100元")
    write_attachment_zip(html, "测试设备 | 100元 | 1台")
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html), run_mode="both")
    monkeypatch.setattr(pipeline_module, "NUMERIC_REFERENCE_RULE_VERSION", "test-reference-next", raising=False)
    second = pipeline.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert len(extractor.summary_calls) == 2
    assert len(extractor.detail_calls) == 2
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash != second.detail_input_hash
