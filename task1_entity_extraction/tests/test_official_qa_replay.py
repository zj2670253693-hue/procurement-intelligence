"""Offline replay must verify bounded evidence and never change frozen inputs."""
import importlib
import json
from pathlib import Path
import sys

import pytest
from models.schemas import ExtractionResult

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def replay():
    assert importlib.util.find_spec("replay_v12") is not None, "offline replay not implemented"
    return importlib.import_module("replay_v12")


def test_replay_rejects_duplicate_announcement_ids(tmp_path):
    with pytest.raises(ValueError, match="duplicate_announcement_id"):
        replay().transform_records([ExtractionResult(announcement_id="s"), ExtractionResult(announcement_id="s")], tmp_path)


def test_unread_html_is_kept_and_reported_not_silently_discarded(tmp_path):
    original = ExtractionResult(announcement_id="missing", success=True)
    results, report = replay().transform_records([original], tmp_path)
    assert results == [original]
    assert report["source_failures"] == [{"announcement_id": "missing", "reason": "html_missing_or_unreadable"}]
    assert report["new_model_calls"] == 0


def test_cli_hash_guard_rejects_changed_input_before_creating_output(tmp_path):
    module = replay()
    input_file = tmp_path / "cache.json"
    input_file.write_text("[]", encoding="utf-8")
    target = tmp_path / "never_created"
    with pytest.raises(ValueError, match="input_sha256_mismatch"):
        module.run_replay(input_file, tmp_path, target, expected_sha256="0" * 64)
    assert not target.exists()


def test_evidence_page_binding_requires_zip_and_pdf_hashes(tmp_path):
    import zipfile
    module = replay()
    with zipfile.ZipFile(tmp_path / "s.zip", "w") as archive:
        archive.writestr("采购.pdf", b"not-a-real-pdf")
    evidence = {"cases": [{"cache_id": "s", "zip_sha256": "0" * 64, "pdf_sha256": "0" * 64,
                            "source_file": "采购.pdf", "physical_page_number": 4,
                            "heading_text": "采购需求", "parsed_table_source": [["名称"], ["产品"]]}]}
    with pytest.raises(ValueError, match="attachment_zip_sha256_mismatch"):
        module.verified_attachment_sources(evidence, tmp_path)


def test_replay_preserves_unrecognized_probe_diagnostics(tmp_path):
    module = replay()
    original = [{"announcement_id": "missing", "error": "probe diagnostic", "future_metadata": {"kept": True}}]
    cache = tmp_path / "input.json"
    cache.write_text(json.dumps(original), encoding="utf-8")
    target = tmp_path / "out"
    module.run_replay(cache, tmp_path, target, expected_sha256=module.file_sha(cache))
    output = json.loads((target / "cached_v12_1038.json").read_text(encoding="utf-8"))
    assert output[0]["error"] == "probe diagnostic"
    assert output[0]["future_metadata"] == {"kept": True}
    assert json.loads(cache.read_text(encoding="utf-8")) == original
