"""Short-lived parser; no SDK, SQL, inherited secrets, or outbound sockets."""
from dataclasses import asdict
import builtins
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import zipfile

from .contracts import PlatformError, canonical_bytes, content_sha256, strict_json_loads
from .paths import reject_link_ancestors
from .archive_safety import inspect_archive, copy_bounded
from .entity_step import ParsedAnnouncement

ROOT = Path(__file__).resolve().parents[1]


def _deny_external():
    def deny(*args, **kwargs):
        raise RuntimeError("parser_network_forbidden")
    socket.create_connection = deny
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"openai", "pymysql", "extractor", "database"}:
            raise ImportError("parser_external_import_forbidden")
        return original(name, *args, **kwargs)
    builtins.__import__ = guarded


def _parse(prepared, limits):
    from data_loader.html_parser import HTMLAnnouncementParser
    from data_loader.attachment_parser import AttachmentParser, AttachmentParseOutcome, classify_attachment_status
    data = prepared.html_path.read_bytes()
    if len(data) > limits.html_bytes or __import__("hashlib").sha256(data).hexdigest() != prepared.html_sha:
        raise PlatformError("parser_input_integrity")
    content = HTMLAnnouncementParser().parse_html(data.decode("utf-8-sig"), prepared.announcement_id)
    texts, tables, archive_files, parsed_files, failed_files = {}, {}, [], [], []
    if prepared.zip_path:
        archive = inspect_archive(prepared.zip_path, limits, kind="attachment")
        with open(prepared.zip_path, "rb") as archive_stream:
            archive_sha = copy_bounded(archive_stream, None, limits.file_bytes)[1]
        if archive_sha != prepared.zip_sha:
            raise PlatformError("parser_input_integrity")
        parser, accumulated = AttachmentParser(), 0
        with zipfile.ZipFile(prepared.zip_path) as source:
            for member in archive.members:
                if member.is_directory:
                    continue
                archive_files.append(member.name)
                ext = Path(member.name).suffix.lower()
                if ext not in parser.SUPPORTED_EXTENSIONS:
                    continue
                try:
                    with source.open(member.name) as stream:
                        import io
                        output = io.BytesIO()
                        copy_bounded(stream, output, limits.member_bytes)
                        text, table = parser._parse_file(output.getvalue(), ext, member.name)
                    size = len(canonical_bytes({"text": text, "table": table}))
                    if accumulated + size > limits.member_bytes:
                        raise ValueError("parsed_payload_limit")
                    accumulated += size
                    if text:
                        texts[member.name] = text
                    if table:
                        tables[member.name] = table
                    (parsed_files if text or table else failed_files).append(member.name)
                except Exception:
                    failed_files.append(member.name)
    status = ("available" if parsed_files else "unreadable") if prepared.zip_path else classify_attachment_status(content.html_text, False, None)
    outcome = AttachmentParseOutcome(texts, tables, archive_files, parsed_files, failed_files, status,
                                    "attachment_parse_incomplete" if failed_files else "")
    return {"content": content.model_dump(), "attachments": asdict(outcome),
            "input_fingerprint": content_sha256(prepared.to_dto())}


def run_parse_child(prepared, limits, *, pulse=lambda: None):
    from .contracts import PreparedInput
    from models.schemas import AnnouncementContent
    from data_loader.attachment_parser import AttachmentParseOutcome
    parent = reject_link_ancestors(prepared.html_path.parent.parent / "extraction" / "parsed")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    request_path = parent / (prepared.announcement_id + ".request.json")
    response_path = parent / (prepared.announcement_id + ".json")
    request = {"prepared": {**prepared.to_dto(), "html_path": str(prepared.html_path),
                            "zip_path": str(prepared.zip_path) if prepared.zip_path else None}, "limits": asdict(limits)}
    request_path.write_bytes(canonical_bytes(request))
    temporary_root = reject_link_ancestors(parent / (prepared.announcement_id + ".tmp"))
    temporary_root.mkdir(exist_ok=True, mode=0o700)
    environment = {key: value for key, value in os.environ.items() if key.upper() in
                   {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
    environment.update(PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / "task1_entity_extraction"))),
                       PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1",
                       TEMP=str(temporary_root), TMP=str(temporary_root), TMPDIR=str(temporary_root))
    child = subprocess.Popen([sys.executable, "-B", "-m", "task3_platform.parse_child", str(request_path), str(response_path)],
                             cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    start = time.monotonic()
    try:
        while child.poll() is None:
            pulse()
            remaining = limits.parse_seconds - (time.monotonic() - start)
            if remaining <= 0:
                raise PlatformError("parse_timeout", 408)
            try:
                child.wait(timeout=min(limits.heartbeat_seconds, remaining))
            except subprocess.TimeoutExpired:
                pass
        if child.returncode != 0 or not response_path.is_file() or response_path.stat().st_size > limits.member_bytes + limits.html_bytes:
            raise PlatformError("parse_child_failed")
        response = strict_json_loads(response_path.read_bytes())
        if response["input_fingerprint"] != content_sha256(prepared.to_dto()):
            raise PlatformError("parser_input_integrity")
        return ParsedAnnouncement(AnnouncementContent(**response["content"]), AttachmentParseOutcome(**response["attachments"]), response["input_fingerprint"])
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def main():
    from .contracts import PreparedInput
    from .limits import Limits
    _deny_external()
    request = strict_json_loads(Path(sys.argv[1]).read_bytes())
    item = request["prepared"]
    prepared = PreparedInput(item["announcement_id"], Path(item["html_path"]), Path(item["zip_path"]) if item["zip_path"] else None, item["html_sha"], item["zip_sha"])
    response = _parse(prepared, Limits(**request["limits"]))
    encoded = canonical_bytes(response)
    if len(encoded) > request["limits"]["member_bytes"] + request["limits"]["html_bytes"]:
        raise PlatformError("parsed_payload_limit", 413)
    Path(sys.argv[2]).write_bytes(encoded)


if __name__ == "__main__":
    main()
