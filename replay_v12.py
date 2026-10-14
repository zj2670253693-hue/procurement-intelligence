"""Offline transform of frozen task1 rows. No model, database, OCR or gold access."""
import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sys
import zipfile

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "task1_entity_extraction"))
from data_loader.html_parser import HTMLAnnouncementParser
from extractor.official_qa import apply_official_qa
from extractor.official_qa_money import OFFICIAL_QA_RULE_VERSION
from models.schemas import ExtractionResult


def file_sha(path):
    with Path(path).open("rb") as stream:
        import hashlib
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
        raise ValueError("invalid_announcement_id")
    return value


def verified_attachment_sources(evidence, html_root):
    sources = {}
    for case in evidence.get("cases", []):
        aid = _identifier(case["cache_id"])
        archive_path = Path(html_root) / (aid + ".zip")
        if file_sha(archive_path) != case["zip_sha256"]:
            raise ValueError("attachment_zip_sha256_mismatch")
        wanted = case["source_file"]
        with zipfile.ZipFile(archive_path) as archive:
            matches = []
            for info in archive.infolist():
                names = [info.filename]
                try:
                    names.append(info.filename.encode("cp437").decode("gbk"))
                except (UnicodeError, LookupError):
                    pass
                if any(PurePosixPath(n).name == wanted for n in names):
                    matches.append(info)
            if len(matches) != 1 or matches[0].file_size > 64 * 1024 * 1024:
                raise ValueError("attachment_member_not_unique_or_too_large")
            with archive.open(matches[0]) as member:
                data = member.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024 or sha256(data).hexdigest() != case["pdf_sha256"]:
                raise ValueError("attachment_pdf_sha256_mismatch")
        text = case["heading_text"] + "\n" + case.get("table_context_text", "") + "\n" + case["full_procurement_budget_table_text"]
        text += "\n" + "\n".join(" | ".join(str(cell or "") for cell in row) for row in case["parsed_table_source"])
        page = str(case["physical_page_number"])
        sources.setdefault(aid, {}).setdefault(wanted, {"pages": {}})["pages"][page] = text
    return sources


def transform_records(records, html_root, attachment_sources=None):
    ids = [_identifier(r.announcement_id) for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_announcement_id")
    parser = HTMLAnnouncementParser()
    results, changes, failures, source_hashes = [], [], [], {}
    for original in records:
        path = Path(html_root) / (original.announcement_id + ".html")
        try:
            before_sha = file_sha(path)
            content = parser.parse_file(str(path))
        except (OSError, ValueError):
            failures.append({"announcement_id": original.announcement_id, "reason": "html_missing_or_unreadable"})
            results.append(original.model_copy(deep=True))
            continue
        revised = apply_official_qa(original, content, attachment_sources=(attachment_sources or {}).get(original.announcement_id, {}))
        if file_sha(path) != before_sha:
            raise ValueError("html_changed_during_replay")
        source_hashes[original.announcement_id] = before_sha
        before = {"entities": [e.model_dump() for e in original.entities], "detail_entities": [e.model_dump() for e in original.detail_entities]}
        after = {"entities": [e.model_dump() for e in revised.entities], "detail_entities": [e.model_dump() for e in revised.detail_entities]}
        if before != after:
            changes.append({"announcement_id": original.announcement_id, "before": before, "after": after})
        # Preserve model-production cache fingerprints: this is a transform,
        # not a claim that the new prompt produced fresh model output.
        if revised.summary_input_hash != original.summary_input_hash or revised.detail_input_hash != original.detail_input_hash:
            raise ValueError("model_fingerprint_was_rewritten")
        results.append(revised)
    exclusions = Counter(row["classification"] for result in results
                         for row in result.extra_info.get("official_qa", {}).get("excluded_details", []))
    report = {"rule_version": OFFICIAL_QA_RULE_VERSION, "announcements": len(results),
              "before_base_count": sum(len(r.entities) for r in records), "after_base_count": sum(len(r.entities) for r in results),
              "before_detail_count": sum(len(r.detail_entities) for r in records), "after_detail_count": sum(len(r.detail_entities) for r in results),
              "changed_announcements": len(changes), "changed_ids": [c["announcement_id"] for c in changes],
              "detail_exclusion_reasons": dict(exclusions),
              "unverified_detail_count": sum(r.extra_info.get("official_qa", {}).get("unverified_detail_count", 0) for r in results),
              "source_failures": failures, "source_html_sha256": source_hashes,
              "new_model_calls": 0, "gold_accessed": False, "database_accessed": False,
              "cache_kind": "frozen_model_output_plus_v12_deterministic_transform", "changes": changes}
    return results, report


def run_replay(input_path, html_root, output_dir, *, expected_sha256, evidence_path=None):
    input_path, output_dir = Path(input_path), Path(output_dir)
    if file_sha(input_path) != expected_sha256:
        raise ValueError("input_sha256_mismatch")
    rows = json.loads(input_path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("expected_frozen_record_array")
    evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8")) if evidence_path else {}
    sources = verified_attachment_sources(evidence, html_root)
    records = [ExtractionResult.model_validate(row) for row in rows]
    transformed, report = transform_records(records, html_root, sources)
    if file_sha(input_path) != expected_sha256:
        raise ValueError("frozen_input_changed_during_replay")
    output_dir.mkdir(parents=True, exist_ok=False)
    output = output_dir / "cached_v12_1038.json"
    # Keep probe-only/future diagnostic keys, without treating them as entity
    # evidence. Pydantic deliberately ignores them in the runtime model.
    output.write_text(json.dumps([{**raw, **result.model_dump()} for raw, result in zip(rows, transformed)],
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    report["input_sha256"] = expected_sha256
    report["output_sha256"] = file_sha(output)
    (output_dir / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in {"changes", "source_html_sha256"}}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--input", required=True, type=Path)
    args.add_argument("--html-root", required=True, type=Path)
    args.add_argument("--output-dir", required=True, type=Path)
    args.add_argument("--expected-input-sha256", required=True)
    args.add_argument("--attachment-evidence", type=Path)
    ns = args.parse_args()
    if not ns.output_dir.resolve().is_relative_to((ROOT / "verification").resolve()):
        raise ValueError("output_must_be_in_this_candidate_verification_directory")
    def blocked(*args, **kwargs):
        raise RuntimeError("offline_replay_forbids_network")
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    summary = run_replay(ns.input, ns.html_root, ns.output_dir, expected_sha256=ns.expected_input_sha256, evidence_path=ns.attachment_evidence)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
