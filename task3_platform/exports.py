"""Task-owned independent exports and exact raw OOXML readback."""
from dataclasses import asdict
from decimal import Decimal
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile
from xml.etree import ElementTree as ET

from .contracts import Artifact, PlatformError, canonical_bytes, strict_json_loads, validate_task_id
from .paths import reject_link_ancestors
from .uploads import _hash_file

ROOT = Path(__file__).resolve().parents[1]
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NUMERIC_HEADERS = {"序号", "单价", "总价", "中标金额", "报价（万元）", "得分"}


def verify_workbook(path, tables, headers):
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [sheet.attrib["name"] for sheet in workbook.findall(f"{{{MAIN}}}sheets/{{{MAIN}}}sheet")]
        if names != ["总表", "标的表", "投标人表"]:
            raise PlatformError("workbook_sheet_mismatch", 409)
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        gray = styles.find(f"{{{MAIN}}}fills")[3].find(f"{{{MAIN}}}patternFill/{{{MAIN}}}fgColor")
        if gray is None or gray.attrib.get("rgb") != "FFA6A6A6":
            raise PlatformError("workbook_gray_style_mismatch", 409)
        for number, name in enumerate(names, 1):
            sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
            rows = sheet.findall(f"{{{MAIN}}}sheetData/{{{MAIN}}}row")
            expected = [headers[name]] + [[str(row.get(key, "")) for key in headers[name]] for row in tables[name]]
            if len(rows) != len(expected) or list(sheet.iter(f"{{{MAIN}}}f")):
                raise PlatformError("workbook_row_or_formula_mismatch", 409)
            for row_no, (cells, values) in enumerate(zip(rows, expected), 1):
                children = cells.findall(f"{{{MAIN}}}c")
                if len(children) != len(values):
                    raise PlatformError("workbook_cell_mismatch", 409)
                for cell, value, header in zip(children, values, headers[name]):
                    numeric = row_no > 1 and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)) and (header in NUMERIC_HEADERS or header == "数量" and value.isascii() and value.isdecimal())
                    if (numeric and cell.attrib.get("t") not in (None, "n")) or (not numeric and cell.attrib.get("t") != "inlineStr"):
                        raise PlatformError("workbook_cell_type_mismatch", 409)
                    if row_no == 1 and header == "URL" and cell.attrib.get("s") != "2":
                        raise PlatformError("workbook_gray_style_mismatch", 409)
                    text = cell.find(f"{{{MAIN}}}v") if cell.attrib.get("t") != "inlineStr" else cell.find(f"{{{MAIN}}}is/{{{MAIN}}}t")
                    saved = text.text or "" if text is not None else ""
                    if saved != value:
                        raise PlatformError("workbook_value_mismatch", 409)
    return True


def build_exports(job, inputs, raw_path, destination, *, pulse=lambda: None, timeout=360, max_output_bytes=512 * 1024**2):
    validate_task_id(job.task_id)
    destination = reject_link_ancestors(Path(destination))
    task_root = destination.parent
    raw_path = reject_link_ancestors(Path(raw_path))
    if destination.name != "exports" or task_root.name != job.task_id or not raw_path.is_relative_to(task_root / "extraction"):
        raise PlatformError("export_scope_mismatch", 403)
    if raw_path.stat().st_size > 256 * 1024**2:
        raise PlatformError("export_input_limit", 413)
    if not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
        raise PlatformError("export_output_limit", 413)
    fingerprints = {}
    for prepared in inputs:
        expected = task_root / "inputs" / (prepared.announcement_id + ".html")
        if prepared.html_path != expected or _hash_file(reject_link_ancestors(expected)) != prepared.html_sha:
            raise PlatformError("export_source_integrity", 409)
        fingerprints[prepared.announcement_id] = prepared.html_sha
    raw_hash = _hash_file(raw_path)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    request_path = task_root / "extraction/exports.request.json"
    request_path.write_bytes(canonical_bytes({"task_id": job.task_id, "mode": job.mode, "raw_path": str(raw_path), "max_output_bytes": max_output_bytes,
        "destination": str(destination), "sources": [{"announcement_id": item.announcement_id, "html_path": str(item.html_path), "html_sha": item.html_sha} for item in inputs]}))
    environment = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
    environment.update(PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / "task1_entity_extraction"))),
                       PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1")
    child = subprocess.Popen([sys.executable, "-B", "-m", "task3_platform.export_child", str(request_path)],
                             cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    started = time.monotonic()
    try:
        while child.poll() is None:
            pulse()
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise PlatformError("export_timeout", 408)
            try:
                child.wait(timeout=min(10, remaining))
            except subprocess.TimeoutExpired:
                pass
        if child.returncode != 0:
            # Discarding this made a real failure undiagnosable in production.
            detail = b""
            if child.stderr is not None:
                try:
                    detail = child.stderr.read() or b""
                except Exception:
                    detail = b""
            trace = detail.decode("utf-8", "replace").strip().splitlines()
            raise PlatformError("export_child_failed:" + (trace[-1][:200] if trace else "no_stderr"))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if child.stderr is not None:
            child.stderr.close()
    projection = strict_json_loads((destination / "standard_projection.json").read_bytes())
    verify_workbook(destination / "standard.xlsx", projection["tables"], projection["headers"])
    if _hash_file(raw_path) != raw_hash or any(_hash_file(task_root / "inputs" / (aid + ".html")) != value for aid, value in fingerprints.items()):
        raise PlatformError("export_input_drift", 409)
    report = strict_json_loads((destination / "export_report.json").read_bytes())
    if report["raw_sha256"] != raw_hash or report["source_hashes"] != fingerprints:
        raise PlatformError("export_receipt_mismatch", 409)
    kinds = {"standard.xlsx": "standard_workbook", "raw_results.json": "raw_result", "errors.json": "error_report",
             "errors.csv": "error_report", "standard_projection.json": "projection", "export_report.json": "verification"}
    artifacts = []
    runtime_root = task_root.parent.parent
    for name in (*kinds, "总表.csv", "标的表.csv", "投标人表.csv"):
        path = reject_link_ancestors(destination / name)
        if not path.is_file():
            raise PlatformError("export_artifact_missing", 409)
        artifacts.append(Artifact(name, path.relative_to(runtime_root).as_posix(), _hash_file(path), path.stat().st_size, kinds.get(name, "standard_csv")))
    return artifacts, report
