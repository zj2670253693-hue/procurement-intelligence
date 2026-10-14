"""Legacy base layer: score-grade granularity from HTML plus attachments.

The 40-case human gold follows the announcement's own item granularity, and some
item-level prices exist only inside attachments. ``--mode legacy`` therefore
keeps the proven single-layer extraction as the scored base layer, while still
producing the attachment detail layer for exhibition.
"""

import threading
import json
from pathlib import Path

from data_loader.html_parser import HTMLAnnouncementParser
from models.schemas import ExtractedEntity, ExtractionResult
from output.writer import ResultWriter
from pipeline import EntityExtractionPipeline


def _write_announcement(tmp_path: Path, announcement_id: str) -> Path:
    combined = tmp_path / "combined"
    combined.mkdir(parents=True, exist_ok=True)
    html = combined / f"{announcement_id}.html"
    html.write_text(
        "<html><body><table><tr><td>项目名称</td><td>虚构信息化项目</td></tr></table>"
        "<p>供应商名称：虚构供应商甲</p>"
        "<p>中标（成交）金额：100.0000（万元）</p>"
        "<p>货物名称：虚构货物，数量：详见附件，单价：详见附件</p>"
        "</body></html>",
        encoding="utf-8",
    )
    return html


class _RecordingExtractor:
    """Records which extraction entry point the pipeline used."""

    def __init__(self):
        self.legacy_calls = []
        self.summary_calls = []
        self.detail_calls = []

    def extract(self, text, has_tables=False, announcement_id=""):
        self.legacy_calls.append({"text": text, "has_tables": has_tables})
        return ExtractionResult(
            announcement_id=announcement_id,
            entities=[
                ExtractedEntity(product_name="虚构货物", brand="虚构供应商甲"),
                ExtractedEntity(product_name="虚构货物二", brand="虚构供应商甲"),
            ],
            success=True,
        )

    def extract_summary(self, text, *, has_tables, detail_status, announcement_id=""):
        self.summary_calls.append({"text": text})
        return ExtractionResult(
            announcement_id=announcement_id,
            entities=[ExtractedEntity(product_name="虚构包级汇总")],
            success=True,
        )

    def extract_details(self, *, texts, tables, announcement_id=""):
        self.detail_calls.append({"texts": texts})
        return [], "unreadable", ""

    def extract_extra_info(self, text, announcement_id=""):
        return {}


def _pipeline(tmp_path: Path, extractor) -> EntityExtractionPipeline:
    pipeline = EntityExtractionPipeline.__new__(EntityExtractionPipeline)
    pipeline.html_parser = HTMLAnnouncementParser()
    pipeline.extractor = extractor
    pipeline.writer = ResultWriter(str(tmp_path / "output"))
    pipeline.extract_extra = False
    pipeline.max_workers = 1
    pipeline._db_lock = threading.Lock()
    pipeline.db_enabled = False
    pipeline.db = None
    pipeline.repository = None
    return pipeline


def test_legacy_mode_uses_single_layer_extraction_over_html_and_attachments(tmp_path):
    announcement_id = "fictional-legacy-001"
    _write_announcement(tmp_path, announcement_id)
    extractor = _RecordingExtractor()

    pipeline = _pipeline(tmp_path, extractor)
    results = pipeline.run(
        input_path=str(tmp_path / "combined"),
        run_mode="legacy",
    )

    assert len(results) == 1
    assert extractor.summary_calls == [], "legacy mode must not use the HTML-only summary layer"
    assert len(extractor.legacy_calls) == 1
    assert [row.product_name for row in results[0].entities] == ["虚构货物", "虚构货物二"]


def test_both_mode_still_uses_the_html_only_summary_layer(tmp_path):
    announcement_id = "fictional-both-001"
    _write_announcement(tmp_path, announcement_id)
    extractor = _RecordingExtractor()

    pipeline = _pipeline(tmp_path, extractor)
    results = pipeline.run(
        input_path=str(tmp_path / "combined"),
        run_mode="both",
    )

    assert len(extractor.summary_calls) == 1
    assert extractor.legacy_calls == []
    assert [row.product_name for row in results[0].entities] == ["虚构包级汇总"]


def test_legacy_mode_keeps_base_rows_when_detail_extraction_fails(tmp_path):
    announcement_id = "fictional-legacy-002"
    _write_announcement(tmp_path, announcement_id)
    extractor = _RecordingExtractor()

    pipeline = _pipeline(tmp_path, extractor)
    results = pipeline.run(
        input_path=str(tmp_path / "combined"),
        run_mode="legacy",
    )

    assert results[0].success is True
    assert len(results[0].entities) == 2
    assert results[0].detail_entities == []


def test_legacy_checkpoint_name_is_distinct_from_both_mode(tmp_path):
    pipeline = _pipeline(tmp_path, _RecordingExtractor())

    legacy = pipeline._checkpoint_path("raw_results.json", "legacy")
    both = pipeline._checkpoint_path("raw_results.json", "both")

    assert legacy != both
    assert "legacy" in legacy.name


def test_legacy_resume_reruns_after_category_prompt_changes(tmp_path, monkeypatch):
    from extractor import prompts

    html = _write_announcement(tmp_path, "fictional-prompt-resume")
    extractor = _RecordingExtractor()
    pipeline = _pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + "\n新的品目规则")
    second = pipeline.process_single_file(
        str(html), run_mode="legacy", previous_result=first, resume=True,
    )
    assert len(extractor.legacy_calls) == 2
    assert first.summary_input_hash != second.summary_input_hash


def test_legacy_resume_reruns_after_spec_rule_changes(tmp_path, monkeypatch):
    import pipeline as pipeline_module

    html = _write_announcement(tmp_path, "fictional-spec-rule-resume")
    extractor = _RecordingExtractor()
    pipeline = _pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(pipeline_module, "SPEC_RULE_VERSION", "test-next-spec-rule", raising=False)
    second = pipeline.process_single_file(
        str(html), run_mode="legacy", previous_result=first, resume=True,
    )
    assert len(extractor.legacy_calls) == 2
    assert first.summary_input_hash != second.summary_input_hash


def test_legacy_pipeline_passes_known_missing_attachment_status(tmp_path, monkeypatch):
    from extractor.llm_extractor import LLMExtractor

    html = tmp_path / "fictional-missing-spec.html"
    html.write_text('<html><body><p>名称：测试设备；规格型号：详见附件2</p></body></html>', encoding="utf-8")
    extractor = object.__new__(LLMExtractor)
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({
        "entities": [{"product_name": "测试设备", "spec_model": "详见附件2"}],
    }))
    result = _pipeline(tmp_path, extractor).process_single_file(str(html), run_mode="legacy")
    assert result.detail_status == "missing"
    assert result.entities[0].spec_model == "附件缺失"


def test_legacy_resume_reruns_when_attachment_status_changes_without_text_change(tmp_path, monkeypatch):
    import pipeline as pipeline_module

    html = _write_announcement(tmp_path, "fictional-status-resume")
    extractor = _RecordingExtractor()
    pipeline = _pipeline(tmp_path, extractor)
    monkeypatch.setattr(pipeline_module, "classify_attachment_status", lambda *args: "missing")
    first = pipeline.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(pipeline_module, "classify_attachment_status", lambda *args: "no_attachment")
    second = pipeline.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    assert len(extractor.legacy_calls) == 2
    assert first.summary_input_hash != second.summary_input_hash


def test_legacy_category_guard_can_see_metadata_outside_main_body(tmp_path, monkeypatch):
    from extractor.llm_extractor import LLMExtractor

    html = tmp_path / "fictional-metadata.html"
    html.write_text(
        '<table><tr><td>品目</td><td>服务/其他服务</td></tr></table>'
        '<div class="vF_detail_content">名称：虚构服务；供应商：虚构公司</div>',
        encoding="utf-8",
    )
    extractor = object.__new__(LLMExtractor)
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({
        "entities": [{"product_name": "虚构服务", "category": "服务/其他服务"}],
    }))
    result = _pipeline(tmp_path, extractor).process_single_file(str(html), run_mode="legacy")
    assert result.entities[0].category == "服务/其他服务"


def test_legacy_source_contains_attachment_table_when_text_is_empty(tmp_path):
    from models.schemas import AnnouncementContent
    from extractor.summary_rules import build_legacy_source

    content = AnnouncementContent(
        announcement_id="fictional-table-only", html_text="正文中的虚构项目",
        html_tables=[[["品目", "服务/其他服务"]]],
        attachment_texts={"附件.txt": "附件正文"},
        attachment_tables={"附件.xlsx": [[["品目名称", "采购标的"], ["其他服务", "虚构服务"]]]},
    )
    source = build_legacy_source(content)
    assert "正文中的虚构项目" in source
    assert "品目 | 服务/其他服务" in source
    assert "附件正文" in source
    assert "其他服务 | 虚构服务" in source


def test_legacy_source_does_not_repeat_attachment_table_already_in_text():
    from models.schemas import AnnouncementContent
    from extractor.summary_rules import build_legacy_source

    content = AnnouncementContent(
        announcement_id="fictional-overlapping-table", html_text="虚构正文",
        attachment_texts={"明细.xlsx": "品目名称 | 采购标的\n其他服务 | 虚构服务"},
        attachment_tables={"明细.xlsx": [[["品目名称", "采购标的"], ["其他服务", "虚构服务"]]]},
    )
    source = build_legacy_source(content)
    assert source.count("品目名称 | 采购标的") == 1
    assert source.count("其他服务 | 虚构服务") == 1


def test_failed_legacy_rerun_does_not_stamp_new_prompt_hash_on_old_rows(tmp_path, monkeypatch):
    from extractor import prompts

    html = _write_announcement(tmp_path, "fictional-legacy-fallback-hash")
    extractor = _RecordingExtractor()
    pipeline = _pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + "\n新的品目规则")
    calls = []

    def failing_extract(text, has_tables=False, announcement_id=""):
        calls.append(text)
        return ExtractionResult(announcement_id=announcement_id, success=False, error_message="API unavailable")

    monkeypatch.setattr(extractor, "extract", failing_extract)
    second = pipeline.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    third = pipeline.process_single_file(str(html), run_mode="legacy", previous_result=second, resume=True)
    assert second.entities == first.entities
    assert third.entities == first.entities
    assert len(calls) == 2
    assert second.summary_input_hash == first.summary_input_hash
