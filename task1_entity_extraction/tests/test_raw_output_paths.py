"""Raw-result path handling for the dual-layer writer."""

from pathlib import Path

from models.schemas import ExtractedEntity, ExtractionResult
from output.writer import ResultWriter


def _result() -> ExtractionResult:
    return ExtractionResult(
        announcement_id="fictional-001",
        entities=[ExtractedEntity(product_name="虚构标的")],
    )


def test_bare_filename_lands_in_output_dir(tmp_path: Path):
    writer = ResultWriter(str(tmp_path))

    writer.write_raw_results([_result()], filename="raw.json")

    assert (tmp_path / "raw.json").exists()


def test_relative_path_with_directory_is_not_nested_under_output_dir(tmp_path: Path):
    writer = ResultWriter(str(tmp_path / "output"))

    writer.write_raw_results([_result()], filename="output/raw.json")

    assert (tmp_path / "output" / "raw.json").exists()
    assert not (tmp_path / "output" / "output").exists()


def test_duplicated_output_dir_prefix_is_collapsed(tmp_path: Path):
    writer = ResultWriter(str(tmp_path / "output"))

    writer.write_raw_results([_result()], filename="output/output/raw.json")

    assert (tmp_path / "output" / "raw.json").exists()


def test_absolute_path_is_used_as_given(tmp_path: Path):
    writer = ResultWriter(str(tmp_path / "output"))
    target = tmp_path / "elsewhere" / "raw.json"
    target.parent.mkdir(parents=True)

    writer.write_raw_results([_result()], filename=str(target))

    assert target.exists()


def test_no_temporary_file_is_left_behind(tmp_path: Path):
    writer = ResultWriter(str(tmp_path / "output"))

    writer.write_raw_results([_result()], filename="output/raw.json")

    leftovers = [
        path.name
        for path in (tmp_path / "output").iterdir()
        if path.name != "raw.json"
    ]
    assert leftovers == []


def test_pipeline_resume_finds_raw_file_when_caller_passes_output_dir(tmp_path: Path):
    from pipeline import EntityExtractionPipeline

    pipeline = EntityExtractionPipeline.__new__(EntityExtractionPipeline)
    pipeline.writer = ResultWriter(str(tmp_path / "output"))
    pipeline.writer.write_raw_results([_result()], filename="output/raw_dual.json")

    previous, skip_ids = pipeline._load_previous_results(
        raw_output_filename="output/raw_dual.json",
    )

    assert skip_ids == {"fictional-001"}
    assert [item.announcement_id for item in previous] == ["fictional-001"]


def test_pipeline_checkpoint_sits_beside_the_raw_file(tmp_path: Path):
    from pipeline import EntityExtractionPipeline

    pipeline = EntityExtractionPipeline.__new__(EntityExtractionPipeline)
    pipeline.writer = ResultWriter(str(tmp_path / "output"))

    checkpoint = pipeline._checkpoint_path("output/raw_dual.json", "both")

    assert checkpoint == tmp_path / "output" / "raw_dual.both.checkpoint.json"
