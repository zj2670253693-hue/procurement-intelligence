"""Bounded ZIP validation before any archive member is written or parsed."""
from dataclasses import dataclass
from pathlib import PurePosixPath
import hashlib
import re
import stat
import struct
import unicodedata
import zipfile

from .contracts import PlatformError

BLOCK_BYTES = 1024**2


def safe_member_name(name):
    if not isinstance(name, str) or not name or len(name) > 512:
        raise PlatformError("unsafe_archive_name")
    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if (normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {"..", "."} or ":" in part or part.endswith((".", " ")) for part in parts)
            or any(unicodedata.category(char).startswith("C") for char in normalized)):
        raise PlatformError("unsafe_archive_name")
    for part in parts:
        if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I):
            raise PlatformError("unsafe_archive_name")
    return normalized


def copy_bounded(source, target, limit):
    count = 0
    digest = hashlib.sha256()
    while True:
        block = source.read(min(BLOCK_BYTES, limit - count + 1))
        if not block:
            break
        if not isinstance(block, bytes):
            raise PlatformError("invalid_upload_stream")
        count += len(block)
        if count > limit:
            raise PlatformError("expanded_byte_limit", 413)
        digest.update(block)
        if target is not None:
            target.write(block)
    return count, digest.hexdigest()


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    basename: str
    bytes: int
    is_directory: bool


@dataclass(frozen=True)
class ArchiveManifest:
    members: tuple[ArchiveMember, ...]
    expanded_bytes: int


def _metadata_cap(path, max_entries):
    """Bound central-directory allocation before ZipFile eagerly loads it; supports ZIP64."""
    with open(path, "rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - 65557))
        tail = stream.read(65557)
        pos = tail.rfind(b"PK\x05\x06")
        if pos < 0 or pos + 22 > len(tail):
            raise PlatformError("invalid_zip")
        end = struct.unpack("<4s4H2LH", tail[pos:pos + 22])
        if pos + 22 + end[-1] != len(tail) or end[1] != 0 or end[2] != 0:
            raise PlatformError("invalid_zip")
        entries, central_bytes = end[4], end[5]
        if entries == 0xFFFF or central_bytes == 0xFFFFFFFF or end[6] == 0xFFFFFFFF:
            absolute = size - len(tail) + pos
            if absolute < 20:
                raise PlatformError("invalid_zip")
            stream.seek(absolute - 20)
            locator = struct.unpack("<4sLQL", stream.read(20))
            if locator[0] != b"PK\x06\x07" or locator[1] != 0 or locator[3] != 1:
                raise PlatformError("invalid_zip")
            stream.seek(locator[2])
            record = struct.unpack("<4sQ2H2L4Q", stream.read(56))
            if record[0] != b"PK\x06\x06" or record[4] != 0 or record[5] != 0:
                raise PlatformError("invalid_zip")
            entries, central_bytes = record[7], record[8]
        if entries > max_entries or central_bytes > 16 * BLOCK_BYTES:
            raise PlatformError("archive_metadata_limit", 413)


def inspect_archive(path, limits, *, kind="outer"):
    if kind not in {"outer", "attachment"}:
        raise PlatformError("invalid_archive_kind")
    max_entries = limits.attachment_entries if kind == "attachment" else limits.outer_entries
    members, names, expanded = [], set(), 0
    try:
        _metadata_cap(path, max_entries)
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > max_entries:
                raise PlatformError("archive_entry_limit", 413)
            for info in infos:
                safe = safe_member_name(info.orig_filename)
                directory = info.is_dir()
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise PlatformError("archive_link_or_special_file")
                if info.flag_bits & 1:
                    raise PlatformError("encrypted_archive")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise PlatformError("unsupported_zip_compression")
                basename = PurePosixPath(safe).name
                if directory:
                    members.append(ArchiveMember(info.filename, basename, 0, True))
                    continue
                if basename.casefold() in names:
                    raise PlatformError("duplicate_archive_basename", 409)
                names.add(basename.casefold())
                max_member = (limits.file_bytes if kind == "outer" and basename.lower().endswith(".zip")
                              else limits.member_bytes)
                if info.file_size > max_member:
                    raise PlatformError("archive_member_limit", 413)
                if info.file_size and (not info.compress_size or info.file_size > info.compress_size * limits.compression_ratio):
                    raise PlatformError("archive_compression_ratio", 413)
                remaining = min(max_member, limits.expanded_bytes - expanded)
                with archive.open(info) as source:
                    actual, _ = copy_bounded(source, None, remaining)
                if actual != info.file_size:
                    raise PlatformError("archive_size_mismatch")
                expanded += actual
                members.append(ArchiveMember(info.filename, basename, actual, False))
    except PlatformError:
        raise
    except (OSError, ValueError, RuntimeError, struct.error, zipfile.BadZipFile, EOFError) as exc:
        raise PlatformError("invalid_zip") from exc
    return ArchiveManifest(tuple(members), expanded)
