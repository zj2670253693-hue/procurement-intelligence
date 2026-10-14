import threading
import sys
import zipfile
from pathlib import Path

import pytest

from data_loader.html_parser import HTMLAnnouncementParser
from main import load_include_ids, parse_args
from models.schemas import DetailedEntity, ExtractedEntity, ExtractionResult
from output.writer import ResultWriter
from pipeline import EntityExtractionPipeline


class RecordingExtractor:
    def __init__(self, *, detail_count=1, summary_failure=False, detail_failure=False):
        self.detail_count = detail_count
        self.summary_failure = summary_failure
        self.detail_failure = detail_failure
        self.summary_calls = []
        self.detail_calls = []

    def extract_summary(
        self,
        text,
        *,
        has_tables,
        detail_status,
        announcement_id="",
    ):
        self.summary_calls.append(
            {
                "text": text,
                "has_tables": has_tables,
                "detail_status": detail_status,
                "announcement_id": announcement_id,
            }
        )
        if self.summary_failure:
            return ExtractionResult(
                announcement_id=announcement_id,
                detail_status=detail_status,
                success=False,
                error_message="fictional summary failure",
            )
        return ExtractionResult(
            announcement_id=announcement_id,
            detail_status=detail_status,
            entities=[
                ExtractedEntity(
                    product_name="采购包一",
                    spec_model=(
                        "附件缺失"
                        if detail_status in {"missing", "unreadable"}
                        else ""
                    ),
                )
            ],
        )

    def extract_details(self, *, texts, tables, announcement_id=""):
        self.detail_calls.append(
            {
                "texts": dict(texts),
                "tables": dict(tables),
                "announcement_id": announcement_id,
            }
        )
        if self.detail_failure:
            return [], "extraction_failed", "fictional detail failure"
        return (
            [
                DetailedEntity(
                    package_name="采购包一",
                    product_name=f"附件设备-{index:02d}",
                    source_file="明细.txt",
                )
                for index in range(1, self.detail_count + 1)
            ],
            "available",
            "",
        )


def make_pipeline(tmp_path: Path, extractor: RecordingExtractor):
    pipeline = object.__new__(EntityExtractionPipeline)
    pipeline.html_parser = HTMLAnnouncementParser()
    pipeline.extractor = extractor
    pipeline.writer = ResultWriter(tmp_path / "output")
    pipeline.extract_extra = False
    pipeline.max_workers = 1
    pipeline.repository = None
    pipeline.db = None
    pipeline._db_lock = threading.Lock()
    return pipeline


def write_html(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.html"
    path.write_text(f"<html><body><p>{body}</p></body></html>", encoding="utf-8")
    return path


def write_attachment_zip(html_path: Path, text: str = "附件设备甲") -> Path:
    zip_path = html_path.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("明细.txt", text)
    return zip_path


def test_summary_never_receives_attachment_text_and_fifty_details_stay_separate(tmp_path):
    html_path = write_html(tmp_path, "fictional-001", "采购包一成交结果")
    write_attachment_zip(html_path, "附件设备甲")
    extractor = RecordingExtractor(detail_count=50)
    pipeline = make_pipeline(tmp_path, extractor)

    result = pipeline.process_single_file(str(html_path), run_mode="both")

    assert "附件设备甲" not in extractor.summary_calls[0]["text"]
    assert extractor.detail_calls[0]["texts"]["明细.txt"] == "附件设备甲"
    assert len(result.entities) == 1
    assert len(result.detail_entities) == 50
    assert result.success is True
    assert result.detail_status == "available"


def test_referenced_but_absent_zip_marks_missing_and_uses_attachment_missing(tmp_path):
    html_path = write_html(tmp_path, "fictional-002", "采购包一规格型号详见附件")
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)

    result = pipeline.process_single_file(str(html_path), run_mode="both")

    assert extractor.summary_calls[0]["detail_status"] == "missing"
    assert result.detail_status == "missing"
    assert result.entities[0].spec_model == "附件缺失"
    assert extractor.detail_calls == []


def test_summary_mode_migrates_legacy_attachment_rows_to_details(tmp_path):
    html_path = write_html(tmp_path, "fictional-003", "采购包一成交结果")
    write_attachment_zip(html_path)
    previous = ExtractionResult(
        announcement_id="fictional-003",
        schema_version=1,
        entities=[
            ExtractedEntity(product_name="旧设备甲"),
            ExtractedEntity(product_name="旧设备乙"),
        ],
        detail_status=None,
    )
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)

    result = pipeline.process_single_file(
        str(html_path),
        run_mode="summary",
        previous_result=previous,
    )

    assert [item.product_name for item in result.entities] == ["采购包一"]
    assert [item.product_name for item in result.detail_entities] == [
        "旧设备甲",
        "旧设备乙",
    ]
    assert extractor.detail_calls == []
    assert result.schema_version == 2


def test_failed_summary_uses_only_verified_v2_fallback(tmp_path):
    html_path = write_html(tmp_path, "fictional-004", "采购包一成交结果")
    previous = ExtractionResult(
        announcement_id="fictional-004",
        schema_version=2,
        entities=[ExtractedEntity(product_name="已验证采购包")],
        detail_status="no_attachment",
        success=True,
    )
    extractor = RecordingExtractor(summary_failure=True)
    pipeline = make_pipeline(tmp_path, extractor)

    result = pipeline.process_single_file(
        str(html_path),
        run_mode="summary",
        previous_result=previous,
    )

    assert [item.product_name for item in result.entities] == ["已验证采购包"]
    assert result.success is True
    assert "rerun" in result.error_message.lower()


def test_failed_summary_never_republishes_legacy_rows_as_summary(tmp_path):
    html_path = write_html(tmp_path, "fictional-005", "采购包一成交结果")
    previous = ExtractionResult(
        announcement_id="fictional-005",
        schema_version=1,
        entities=[ExtractedEntity(product_name="旧附件设备")],
        detail_status=None,
    )
    extractor = RecordingExtractor(summary_failure=True)
    pipeline = make_pipeline(tmp_path, extractor)

    result = pipeline.process_single_file(
        str(html_path),
        run_mode="summary",
        previous_result=previous,
    )

    assert result.entities == []
    assert result.detail_entities == []
    assert result.success is False


def test_detail_failure_does_not_mark_successful_summary_as_failed(tmp_path):
    html_path = write_html(tmp_path, "fictional-006", "采购包一成交结果")
    write_attachment_zip(html_path)
    extractor = RecordingExtractor(detail_failure=True)
    pipeline = make_pipeline(tmp_path, extractor)

    result = pipeline.process_single_file(str(html_path), run_mode="both")

    assert result.success is True
    assert len(result.entities) == 1
    assert result.detail_status == "extraction_failed"
    assert result.detail_error_message == "fictional detail failure"


def test_resume_skips_unchanged_successful_summary_layer(tmp_path):
    html_path = write_html(tmp_path, "fictional-007", "采购包一成交结果")
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html_path), run_mode="summary")

    second = pipeline.process_single_file(
        str(html_path),
        run_mode="summary",
        previous_result=first,
        resume=True,
    )

    assert len(extractor.summary_calls) == 1
    assert second.summary_input_hash == first.summary_input_hash
    assert second.entities == first.entities


def test_resume_reruns_summary_after_category_prompt_changes(tmp_path, monkeypatch):
    from extractor import prompts

    html_path = write_html(tmp_path, "fictional-summary-prompt-resume", "采购包一")
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html_path), run_mode="summary")
    monkeypatch.setattr(prompts, "SUMMARY_SYSTEM_PROMPT", prompts.SUMMARY_SYSTEM_PROMPT + "\n新的品目规则")
    second = pipeline.process_single_file(
        str(html_path), run_mode="summary", previous_result=first, resume=True,
    )
    assert len(extractor.summary_calls) == 2
    assert first.summary_input_hash != second.summary_input_hash


def test_resume_reruns_details_after_category_prompt_changes(tmp_path, monkeypatch):
    from extractor import prompts

    html_path = write_html(tmp_path, "fictional-detail-prompt-resume", "采购包一")
    write_attachment_zip(html_path)
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html_path), run_mode="both")
    monkeypatch.setattr(prompts, "DETAIL_SYSTEM_PROMPT", prompts.DETAIL_SYSTEM_PROMPT + "\n新的品目规则")
    second = pipeline.process_single_file(
        str(html_path), run_mode="both", previous_result=first, resume=True,
    )
    assert len(extractor.summary_calls) == 1
    assert len(extractor.detail_calls) == 2
    assert first.detail_input_hash != second.detail_input_hash


def test_resume_reruns_both_layers_after_spec_rule_changes(tmp_path, monkeypatch):
    import pipeline as pipeline_module

    html_path = write_html(tmp_path, "fictional-spec-rule-resume", "采购包一")
    write_attachment_zip(html_path)
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html_path), run_mode="both")
    monkeypatch.setattr(pipeline_module, "SPEC_RULE_VERSION", "test-next-spec-rule", raising=False)
    second = pipeline.process_single_file(
        str(html_path), run_mode="both", previous_result=first, resume=True,
    )
    assert len(extractor.summary_calls) == 2
    assert len(extractor.detail_calls) == 2
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash != second.detail_input_hash


def test_failed_summary_rerun_does_not_stamp_new_prompt_hash_on_old_rows(tmp_path, monkeypatch):
    from extractor import prompts

    html_path = write_html(tmp_path, "fictional-summary-fallback-hash", "采购包一")
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)
    first = pipeline.process_single_file(str(html_path), run_mode="summary")
    monkeypatch.setattr(prompts, "SUMMARY_SYSTEM_PROMPT", prompts.SUMMARY_SYSTEM_PROMPT + "\n新的品目规则")
    extractor.summary_failure = True
    second = pipeline.process_single_file(str(html_path), run_mode="summary", previous_result=first, resume=True)
    third = pipeline.process_single_file(str(html_path), run_mode="summary", previous_result=second, resume=True)
    assert second.entities == first.entities
    assert third.entities == first.entities
    assert len(extractor.summary_calls) == 3
    assert second.summary_input_hash == first.summary_input_hash


def test_directory_include_ids_only_processes_selected_announcements(tmp_path):
    write_html(tmp_path, "fictional-008", "采购包一")
    write_html(tmp_path, "fictional-009", "采购包二")
    extractor = RecordingExtractor()
    pipeline = make_pipeline(tmp_path, extractor)

    results = pipeline.process_directory(
        str(tmp_path),
        include_ids={"fictional-009"},
        run_mode="summary",
    )

    assert [item.announcement_id for item in results] == ["fictional-009"]


def test_checkpoint_name_is_isolated_by_output_and_layer(tmp_path):
    pipeline = make_pipeline(tmp_path, RecordingExtractor())

    path = pipeline._checkpoint_path("pilot.json", "summary")

    assert path.name == "pilot.summary.checkpoint.json"


def test_cli_parses_dual_layer_options_and_rejects_duplicate_ids(tmp_path):
    ids_path = tmp_path / "ids.txt"
    ids_path.write_text("fictional-001\nfictional-002\n", encoding="utf-8")
    args = parse_args(
        [
            "--input",
            str(tmp_path),
            "--mode",
            "summary",
            "--previous-results",
            "old.json",
            "--raw-output",
            "pilot.json",
            "--include-ids-file",
            str(ids_path),
        ]
    )

    assert args.mode == "summary"
    assert args.previous_results == "old.json"
    assert args.raw_output == "pilot.json"
    assert load_include_ids(ids_path) == {"fictional-001", "fictional-002"}

    ids_path.write_text("fictional-001\nfictional-001\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_include_ids(ids_path)


def test_upload_task_reports_summary_and_detail_counts(tmp_path, monkeypatch):
    from api.routers import task3
    from api.services.task_manager import TaskManager

    manager = TaskManager()
    task_id = manager.create("fictional", total_files=1, run_extraction=True)
    result = ExtractionResult(
        announcement_id="fictional-010",
        entities=[ExtractedEntity(product_name="采购包一")],
        detail_entities=[
            DetailedEntity(product_name="设备甲"),
            DetailedEntity(product_name="设备乙"),
        ],
        detail_status="available",
    )

    class FakePipeline:
        def __init__(self, **_kwargs):
            self.db = None

        def process_directory(self, _path, progress_callback=None, **_kwargs):
            progress_callback(1, 1, 1, result)
            return [result]

    class FakeWriter:
        def __init__(self, *_args, **_kwargs):
            pass

        def write(self, *_args, **_kwargs):
            return "fictional.csv"

        def write_raw_results(self, *_args, **_kwargs):
            return "fictional.json"

    monkeypatch.setattr(task3, "task_manager", manager)
    monkeypatch.setattr(sys.modules["pipeline"], "EntityExtractionPipeline", FakePipeline)
    monkeypatch.setattr(sys.modules["output.writer"], "ResultWriter", FakeWriter)
    monkeypatch.setattr(task3.config, "OUTPUT_DIR", tmp_path)
    # Explicit opt-in is synthetic: the pipeline/writer above are test doubles.
    monkeypatch.setenv('PAID_INFERENCE_ENABLED','1')
    monkeypatch.setenv('MODEL_RUN_TOKEN','synthetic-test-only')

    task3._run_task(task_id, tmp_path, run_extraction=True)

    task = manager.get(task_id)
    assert task["entities"] == 1
    assert task["detail_entities"] == 2
