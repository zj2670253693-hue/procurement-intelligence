"""Production consumers must execute the policy and version all changed caches."""
import json
from copy import deepcopy
import pytest

import pipeline as pipeline_module
from extractor.llm_extractor import LLMExtractor
from models.schemas import DetailedEntity, ExtractedEntity, ExtractionResult
from tests.test_dual_layer_pipeline import RecordingExtractor, make_pipeline, write_attachment_zip, write_html
from tests.test_official_qa_v12 import content_with_rows
from extractor.summary_rules import build_summary_source


def test_native_legacy_extract_also_uses_product_money_scope(monkeypatch):
    content = content_with_rows([["3", "第3包-甲公司", "审计设备", "品牌甲", "X-1", "3", "250000"]])
    instance = object.__new__(LLMExtractor)
    monkeypatch.setattr(instance, "_call_llm", lambda *args: json.dumps({"entities": [{"product_name": "审计设备", "brand": "品牌甲", "total_price": "150万元"}]}))
    result = instance.extract(build_summary_source(content.html_text, content.html_tables))
    assert result.entities[0].total_price == "750000元"


def test_finish_hook_partitions_plans_before_raw_or_repository_save(tmp_path):
    extractor = RecordingExtractor()
    instance = make_pipeline(tmp_path, extractor)
    content = content_with_rows([])
    content.attachment_texts = {"需求.txt": "采购需求\n产品名称 | 数量 | 预算价\n计划设备 | 1 | 100"}
    original = ExtractionResult(announcement_id="synthetic", extra_info={"project_amount": "150万元"},
                                detail_entities=[DetailedEntity(product_name="计划设备", source_file="需求.txt", total_price="100元")])
    saved = deepcopy(original)
    captured = []

    class Repository:
        def save_extraction_result(self, **kwargs):
            captured.append(kwargs["result"].model_copy(deep=True))

    instance.repository = Repository()
    revised = instance._finish_single_result(original, content, tmp_path / "synthetic.html")
    assert revised.detail_entities == [] and original == saved
    assert captured[0].detail_entities == []
    assert revised.extra_info["project_amount"] == "150万元"
    assert revised.extra_info["official_qa"]["excluded_details"][0]["entity"]["product_name"] == "计划设备"


@pytest.mark.parametrize("mode", ["both", "legacy"])
def test_changed_qa_rule_invalidates_base_and_detail_cache(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(pipeline_module, "OFFICIAL_QA_RULE_VERSION", "qa-test-1", raising=False)
    html = write_html(tmp_path, "synthetic-cache", "采购包一成交结果")
    write_attachment_zip(html)
    extractor = RecordingExtractor(detail_count=3)
    if mode == "legacy":
        extractor.extract = lambda text, **kwargs: extractor.extract_summary(text, has_tables=kwargs.get("has_tables", False), detail_status="available", announcement_id=kwargs.get("announcement_id", ""))
    instance = make_pipeline(tmp_path, extractor)
    first = instance.process_single_file(str(html), run_mode=mode)
    same = instance.process_single_file(str(html), run_mode=mode, previous_result=first, resume=True)
    assert len(extractor.summary_calls) == len(extractor.detail_calls) == 1
    monkeypatch.setattr(pipeline_module, "OFFICIAL_QA_RULE_VERSION", "qa-test-2", raising=False)
    changed = instance.process_single_file(str(html), run_mode=mode, previous_result=same, resume=True)
    assert len(extractor.summary_calls) == len(extractor.detail_calls) == 2
    assert changed.summary_input_hash != first.summary_input_hash
    assert changed.detail_input_hash != first.detail_input_hash


def test_failed_new_rule_extraction_keeps_previous_summary_hash_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "OFFICIAL_QA_RULE_VERSION", "qa-test-1", raising=False)
    html = write_html(tmp_path, "synthetic-retry", "采购包一成交结果")
    extractor = RecordingExtractor()
    instance = make_pipeline(tmp_path, extractor)
    first = instance.process_single_file(str(html), run_mode="summary")
    monkeypatch.setattr(pipeline_module, "OFFICIAL_QA_RULE_VERSION", "qa-test-2", raising=False)
    extractor.summary_failure = True
    fallback = instance.process_single_file(str(html), run_mode="summary", previous_result=first, resume=True)
    assert len(extractor.summary_calls) == 2
    assert fallback.summary_input_hash == first.summary_input_hash
    instance.process_single_file(str(html), run_mode="summary", previous_result=fallback, resume=True)
    assert len(extractor.summary_calls) == 3


@pytest.mark.parametrize("mode", ["summary", "legacy"])
def test_finish_does_not_erase_original_same_name_model_ambiguity(tmp_path, monkeypatch, mode):
    content = content_with_rows([["3", "第3包-甲公司", "设备", "品牌甲", "X-1", "3", "100"]])
    native = object.__new__(LLMExtractor)
    rows = [{"product_name": "设备", "brand": "品牌甲", "spec_model": "X-1", "total_price": t} for t in ("", "")]
    monkeypatch.setattr(native, "_call_llm", lambda *a: json.dumps({"entities": rows}, ensure_ascii=False))
    pipe = make_pipeline(tmp_path, native)
    monkeypatch.setattr(pipe.html_parser, "parse_file", lambda *a: content)
    result = pipe.process_single_file(str(tmp_path / "synthetic.html"), run_mode=mode)
    assert len(result.entities) == 1
    assert result.entities[0].total_price == ""


def test_legacy_finish_supplies_source_to_preserve_physical_award_duplicates(tmp_path):
    from extractor.cross_package_duplicates import deduplicate_with_source_evidence
    content = content_with_rows([["甲公司", "", "", "1", "28.1万元", "28.1万元"],
                                 ["甲公司", "", "", "1", "28.1万元", "28.1万元"],
                                 ["乙公司", "", "", "1", "43.2万元", "43.2万元"]],
        headers=["供应商", "商品名称", "规格型号", "数量", "单价", "总价"],
        award_rows=[("甲公司", "28.1万元"), ("甲公司", "28.1万元"), ("乙公司", "43.2万元")])
    pipe = make_pipeline(tmp_path, RecordingExtractor())
    saved = []
    class Repository:
        def save_extraction_result(self, *, result, summary_source, **kwargs):
            saved.extend(deduplicate_with_source_evidence(result.entities, source_text=summary_source))
    pipe.repository = Repository()
    result = pipe._finish_single_result(ExtractionResult(announcement_id="synthetic"), content,
                                      tmp_path / "synthetic.html", summary_source="")
    assert len(result.entities) == len(saved) == 3
