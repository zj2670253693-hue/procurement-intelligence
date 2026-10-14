"""Resumable bounded uploads; no model calls, legacy writes or shared output."""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import zipfile

from .archive_safety import copy_bounded, inspect_archive, safe_member_name
from .auth import authorize_upload, require_owner
from .contracts import (PlatformError, PreparedInput, strict_json_loads,
                        canonical_bytes, validate_task_id, validate_announcement_id)
from .job_store import transaction, _sha
from .paths import reject_link_ancestors


@dataclass(frozen=True)
class UploadFile:
    file_id: str
    name: str
    bytes: int
    received_parts: tuple[int, ...]


@dataclass(frozen=True)
class UploadSession:
    job: object
    files: tuple[UploadFile, ...]


@dataclass(frozen=True)
class PartReceipt:
    file_id: str
    part_no: int
    sha256: str
    bytes: int
    reused: bool


@dataclass(frozen=True)
class InputRef:
    aid: str
    suffix: str
    path: Path
    member: str | None
    bytes: int


def _hash_file(path):
    with open(path, "rb") as stream:
        return copy_bounded(stream, None, path.stat().st_size)[1]


def _leaf(name):
    safe = safe_member_name(name)
    if "/" in safe:
        raise PlatformError("upload_name_must_be_basename")
    suffix = Path(safe).suffix.lower()
    if suffix not in {".html", ".htm", ".zip"}:
        raise PlatformError("unsupported_upload_type")
    if suffix != ".zip":
        validate_announcement_id(Path(safe).stem)
    return safe, suffix


class UploadService:
    def __init__(self, store, paths, limits, *, secure_transport=False,
                 public_demo=False, guest_source_hash=None, extract_authorizer=None):
        self.store = store
        self.paths = paths
        self.limits = limits
        self.secure_transport = secure_transport
        self.public_demo = public_demo
        self.guest_source_hash = guest_source_hash
        self.extract_authorizer = extract_authorizer

    def _free(self, additional):
        probe = self.paths.root
        while not probe.exists():
            probe = probe.parent
        if shutil.disk_usage(probe).free < additional + self.limits.reserve_disk_bytes:
            raise PlatformError("disk_reserve_required", 503)

    def _disk_bytes(self, task_id):
        task = self.paths.task(task_id)
        root = task.received.parent
        count = 0
        if root.exists():
            for path in root.rglob("*"):
                reject_link_ancestors(path)
                if path.is_file():
                    count += path.stat().st_size
        return count

    def _reserve(self, cursor, row, additional):
        self._free(additional)
        runtime = self.store._runtime(cursor)
        desired = max(row["reserved_bytes"], self._disk_bytes(row["task_id"]) + additional)
        if desired > self.limits.job_disk_bytes:
            raise PlatformError("job_disk_quota", 413)
        increase = desired - row["reserved_bytes"]
        if runtime["reserved_bytes"] + increase > self.limits.global_disk_bytes:
            raise PlatformError("global_disk_quota", 413)
        if increase:
            cursor.execute("UPDATE t3_jobs SET reserved_bytes=%s WHERE task_id=%s", (desired, row["task_id"]))
            cursor.execute("UPDATE t3_runtime_state SET reserved_bytes=reserved_bytes+%s WHERE singleton_id=1", (increase,))
            row["reserved_bytes"] = desired

    def begin(self, actor, spec, key):
        seen = set()
        for file in spec.files:
            name, suffix = _leaf(file.name)
            identity = (Path(name).stem.casefold(), "html" if suffix in {".htm", ".html"} else "zip")
            if identity in seen:
                raise PlatformError("duplicate_input_name", 409)
            seen.add(identity)
            if type(file.bytes) is not int or file.bytes <= 0 or file.bytes > self.limits.file_bytes:
                raise PlatformError("upload_file_limit", 413)
            if suffix in {".html", ".htm"} and file.bytes > self.limits.html_bytes:
                raise PlatformError("html_byte_limit", 413)
            if len(file.part_hashes) != (file.bytes + self.limits.chunk_bytes - 1) // self.limits.chunk_bytes:
                raise PlatformError("invalid_part_manifest")
            for digest in file.part_hashes:
                _sha(digest)
        if actor.role != "guest":
            authorize_upload(actor, spec, self.secure_transport)
        if spec.mode == "extract":
            if self.extract_authorizer is None:
                raise PlatformError("paid_inference_disabled", 403)
            self.extract_authorizer(actor, spec)
        self._free(sum(file.bytes for file in spec.files))
        job = self.store.create_receiving(actor, spec, key, guest_source_hash=self.guest_source_hash, public_demo=self.public_demo)
        task = self.paths.task(job.task_id)
        task.received.mkdir(parents=True, exist_ok=True, mode=0o700)
        reject_link_ancestors(task.received)
        with transaction(self.store.connection) as cursor:
            self.store._runtime(cursor)
            cursor.execute("SELECT * FROM t3_upload_files WHERE task_id=%s ORDER BY original_name", (job.task_id,))
            registered = cursor.fetchall()
            if not registered:
                for file in spec.files:
                    cursor.execute("INSERT INTO t3_upload_files(task_id,file_id,original_name,expected_bytes,part_hashes) VALUES(%s,%s,%s,%s,%s)",
                                   (job.task_id, secrets.token_hex(16), file.name, file.bytes,
                                    canonical_bytes(file.part_hashes).decode("utf-8")))
                cursor.execute("SELECT * FROM t3_upload_files WHERE task_id=%s ORDER BY original_name", (job.task_id,))
                registered = cursor.fetchall()
            slots = []
            by_name = {file.name: index for index, file in enumerate(spec.files)}
            for file in sorted(registered, key=lambda file: by_name[file["original_name"]]):
                cursor.execute("SELECT part_no FROM t3_upload_parts WHERE task_id=%s AND file_id=%s ORDER BY part_no", (job.task_id, file["file_id"]))
                slots.append(UploadFile(file["file_id"], file["original_name"], file["expected_bytes"],
                                        tuple(part["part_no"] for part in cursor.fetchall())))
        return UploadSession(job, tuple(slots))

    def _part_path(self, task_id, file_id, part_no):
        validate_task_id(file_id)
        return reject_link_ancestors(self.paths.task(task_id).received / "parts" / file_id / (str(part_no) + ".part"))

    def put_part(self, actor, task_id, file_id, part_no, stream):
        if type(part_no) is not int or part_no < 0:
            raise PlatformError("invalid_part_number")
        validate_task_id(file_id)
        path = self._part_path(task_id, file_id, part_no)
        created = False
        try:
            with transaction(self.store.connection) as cursor:
                self.store._runtime(cursor)
                row = self.store._row(cursor, task_id, lock=True)
                require_owner(actor, row["owner_id"])
                if row["status"] != "receiving":
                    raise PlatformError("upload_not_receiving", 409)
                cursor.execute("SELECT * FROM t3_upload_files WHERE task_id=%s AND file_id=%s", (task_id, file_id))
                file = cursor.fetchone()
                if not file:
                    raise PlatformError("upload_file_not_found", 404)
                hashes = strict_json_loads(file["part_hashes"])
                if part_no >= len(hashes):
                    raise PlatformError("invalid_part_number")
                expected = min(self.limits.chunk_bytes, file["expected_bytes"] - part_no * self.limits.chunk_bytes)
                cursor.execute("SELECT * FROM t3_upload_parts WHERE task_id=%s AND file_id=%s AND part_no=%s", (task_id, file_id, part_no))
                old = cursor.fetchone()
                if old:
                    actual, digest = copy_bounded(stream, None, expected)
                    if actual != old["bytes"] or digest != old["sha256"]:
                        raise PlatformError("part_hash_conflict", 409)
                    return PartReceipt(file_id, part_no, digest, actual, True)
                if path.exists():
                    # An orphan is an unconfirmed local temporary chunk, never a user original.
                    path.unlink()
                self._reserve(cursor, row, expected)
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                reject_link_ancestors(path)
                with open(path, "xb") as target:
                    created = True
                    actual, digest = copy_bounded(stream, target, expected)
                    target.flush()
                    os.fsync(target.fileno())
                if actual != expected or digest != hashes[part_no]:
                    raise PlatformError("part_hash_conflict", 409)
                cursor.execute("INSERT INTO t3_upload_parts(task_id,file_id,part_no,sha256,bytes,relative_path) VALUES(%s,%s,%s,%s,%s,%s)",
                               (task_id, file_id, part_no, digest, actual, path.relative_to(self.paths.root).as_posix()))
                cursor.execute("UPDATE t3_upload_files SET received_bytes=received_bytes+%s WHERE task_id=%s AND file_id=%s", (actual, task_id, file_id))
                cursor.execute("UPDATE t3_jobs SET received_bytes=received_bytes+%s WHERE task_id=%s", (actual, task_id))
                return PartReceipt(file_id, part_no, digest, actual, False)
        except BaseException:
            if created and path.exists():
                reject_link_ancestors(path)
                path.unlink()
            raise

    def _assembled_path(self, task_id, file):
        validate_task_id(file["file_id"])
        _, suffix = _leaf(file["original_name"])
        return reject_link_ancestors(self.paths.task(task_id).received / "files" / (file["file_id"] + suffix))

    def _assemble(self, actor, task_id):
        with self.store.connection.cursor() as cursor:
            row = self.store._row(cursor, task_id)
            require_owner(actor, row["owner_id"])
            cursor.execute("SELECT * FROM t3_upload_files WHERE task_id=%s ORDER BY file_id", (task_id,))
            files = cursor.fetchall()
        for file in files:
            destination = self._assembled_path(task_id, file)
            if file["sha256"]:
                if not destination.is_file() or _hash_file(destination) != file["sha256"]:
                    raise PlatformError("received_file_integrity", 409)
                continue
            with self.store.connection.cursor() as cursor:
                cursor.execute("SELECT * FROM t3_upload_parts WHERE task_id=%s AND file_id=%s ORDER BY part_no", (task_id, file["file_id"]))
                parts = cursor.fetchall()
            hashes = strict_json_loads(file["part_hashes"])
            if [part["part_no"] for part in parts] != list(range(len(hashes))):
                raise PlatformError("upload_parts_missing", 409)
            temporary = destination.with_suffix(destination.suffix + ".assembling")
            reject_link_ancestors(temporary)
            if temporary.exists():
                temporary.unlink()
            self._reserve_disk(task_id, file["expected_bytes"])
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                with open(temporary, "xb") as target:
                    total = 0
                    for part in parts:
                        source_path = self._part_path(task_id, file["file_id"], part["part_no"])
                        with open(source_path, "rb") as source:
                            size, digest = copy_bounded(source, target, part["bytes"])
                        if size != part["bytes"] or digest != part["sha256"]:
                            raise PlatformError("received_part_integrity", 409)
                        total += size
                    target.flush()
                    os.fsync(target.fileno())
                if total != file["expected_bytes"]:
                    raise PlatformError("received_file_size", 409)
                digest = _hash_file(temporary)
                if destination.exists():
                    if _hash_file(destination) != digest:
                        raise PlatformError("received_file_integrity", 409)
                    temporary.unlink()
                else:
                    temporary.rename(destination)
                with transaction(self.store.connection) as cursor:
                    self.store._runtime(cursor)
                    row = self.store._row(cursor, task_id, lock=True)
                    if row["status"] != "receiving" or row["cancel_requested"]:
                        raise PlatformError("upload_not_receiving", 409)
                    cursor.execute("UPDATE t3_upload_files SET sha256=%s,relative_path=%s WHERE task_id=%s AND file_id=%s",
                                   (digest, destination.relative_to(self.paths.root).as_posix(), task_id, file["file_id"]))
                file["sha256"] = digest
                # Only remove verified temporary chunks after final file registration commits.
                for part in parts:
                    source_path = self._part_path(task_id, file["file_id"], part["part_no"])
                    if source_path.exists():
                        source_path.unlink()
            except BaseException:
                if temporary.exists():
                    temporary.unlink()
                # Original chunks and a completed assembly are preserved on ambiguous SQL commit.
                raise
        return files

    def _reserve_disk(self, task_id, additional):
        # Commit reservation BEFORE file writes; failed/uncertain later SQL cannot unreserve bytes.
        with transaction(self.store.connection) as cursor:
            self.store._runtime(cursor)
            row = self.store._row(cursor, task_id, lock=True)
            if row["cancel_requested"] or row["status"] not in {"receiving", "queued", "running"}:
                raise PlatformError("upload_not_receiving", 409)
            self._reserve(cursor, row, additional)

    @contextmanager
    def _completion_lock(self, task_id):
        path = reject_link_ancestors(self.paths.task(task_id).received / ".completion.lock")
        with open(path, "a+b") as handle:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, 2) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise PlatformError("upload_completion_in_progress", 409) from exc
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise PlatformError("upload_completion_in_progress", 409) from exc
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def _open_ref(self, ref):
        with ExitStack() as stack:
            if ref.member is None:
                stream = stack.enter_context(open(ref.path, "rb"))
            else:
                archive = stack.enter_context(zipfile.ZipFile(ref.path))
                stream = stack.enter_context(archive.open(ref.member))
            yield stream

    def _copy_ref(self, job, ref, destination):
        with self._open_ref(ref) as source:
            if destination.exists():
                size, expected_sha = copy_bounded(source, None, ref.bytes)
                if size != ref.bytes or _hash_file(destination) != expected_sha:
                    raise PlatformError("canonical_input_conflict", 409)
                return expected_sha
            self._reserve_disk(job.task_id, ref.bytes)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            reject_link_ancestors(destination)
            try:
                with open(destination, "xb") as target:
                    size, digest = copy_bounded(source, target, ref.bytes)
                    target.flush()
                    os.fsync(target.fileno())
                if size != ref.bytes:
                    raise PlatformError("canonical_input_size")
            except BaseException:
                if destination.exists():
                    destination.unlink()
                raise
            return digest

    def prepare_inputs(self, job):
        paths = self.paths.task(job.task_id)
        with self.store.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM t3_upload_files WHERE task_id=%s ORDER BY original_name", (job.task_id,))
            files = cursor.fetchall()
        refs, expanded, future_expansion = {}, 0, 0

        def register(name, source, member, size):
            suffix = Path(name).suffix.lower()
            if suffix not in {".html", ".htm", ".zip"}:
                raise PlatformError("unsupported_outer_member")
            aid = validate_announcement_id(Path(name).stem)
            kind = "html" if suffix in {".html", ".htm"} else "zip"
            key = (aid, kind)
            if key in refs:
                raise PlatformError("duplicate_input_name", 409)
            if kind == "html" and size > self.limits.html_bytes:
                raise PlatformError("html_byte_limit", 413)
            refs[key] = InputRef(aid, kind, source, member, size)

        for file in files:
            path = self._assembled_path(job.task_id, file)
            if not file["sha256"] or not path.is_file() or _hash_file(path) != file["sha256"]:
                raise PlatformError("received_file_integrity", 409)
            name = file["original_name"]
            suffix = Path(name).suffix.lower()
            canonical_zip = suffix == ".zip" and re.fullmatch(r"t[0-9]{8}_[0-9]+", Path(name).stem)
            if suffix != ".zip" or canonical_zip:
                register(name, path, None, file["expected_bytes"])
            else:
                archive = inspect_archive(path, self.limits)
                expanded += archive.expanded_bytes
                for member in archive.members:
                    if not member.is_directory:
                        register(member.basename, path, member.name, member.bytes)
        aids = sorted({aid for aid, kind in refs if kind == "html"})
        if not aids or len(aids) > self.limits.announcements:
            raise PlatformError("announcement_count_limit", 413)
        if any(aid not in aids for aid, kind in refs if kind == "zip"):
            raise PlatformError("attachment_without_html")
        prepared, diagnostics = [], []
        for aid in aids:
            html_ref = refs[(aid, "html")]
            html_path = reject_link_ancestors(paths.inputs / (aid + ".html"))
            html_sha = self._copy_ref(job, html_ref, html_path)
            try:
                html_path.read_bytes().decode("utf-8-sig", errors="strict")
            except UnicodeError as exc:
                raise PlatformError("html_not_utf8") from exc
            attachment = refs.get((aid, "zip"))
            zip_path, zip_sha = None, None
            if attachment:
                zip_path = reject_link_ancestors(paths.inputs / (aid + ".zip"))
                zip_sha = self._copy_ref(job, attachment, zip_path)
                inner_bytes = inspect_archive(zip_path, self.limits, kind="attachment").expanded_bytes
                expanded += inner_bytes
                future_expansion += inner_bytes
            else:
                diagnostics.append({"announcement_id": aid, "code": "attachment_not_provided"})
            if expanded > self.limits.expanded_bytes:
                raise PlatformError("expanded_byte_limit", 413)
            prepared.append(PreparedInput(aid, html_path, zip_path, html_sha, zip_sha))
        with transaction(self.store.connection) as cursor:
            self.store._runtime(cursor)
            row = self.store._row(cursor, job.task_id, lock=True)
            # Future parser expansion has a reservation, in addition to all retained actual files.
            self._reserve(cursor, row, future_expansion)
            cursor.execute("UPDATE t3_jobs SET expanded_bytes=%s,diagnostics_json=%s WHERE task_id=%s",
                           (expanded, canonical_bytes(diagnostics).decode("utf-8"), job.task_id))
        return prepared, diagnostics

    def complete(self, actor, task_id):
        job = self.store.get(actor, task_id)
        if job.status != "receiving":
            return job
        with self._completion_lock(task_id):
            files = self._assemble(actor, task_id)
            prepared, _ = self.prepare_inputs(job)
            manifest = {"files": sorted(({"name": file["original_name"], "bytes": file["expected_bytes"], "sha256": file["sha256"]} for file in files), key=lambda item: item["name"]),
                        "inputs": [item.to_dto() for item in prepared]}
            return self.store.finalize(task_id, manifest)
