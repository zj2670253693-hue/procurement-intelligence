"""Legacy base layer: score-grade granularity from HTML plus attachments.

The 40-case human gold follows the announcement's own item granularity, and some
item-level prices exist only inside attachments. ``--mode legacy`` therefore
keeps the proven single-layer extraction as the scored base layer, while still
producing the attachment detail layer for exhibition.
"""

import threading
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
