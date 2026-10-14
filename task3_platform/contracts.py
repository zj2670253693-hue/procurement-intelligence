"""Shared internal contracts; public DTOs are explicit allowlists."""
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import re


class PlatformError(Exception):
    """Stable public code, never an upstream exception/path/credential."""
    def __init__(self, code: str, http_status: int = 400):
        self.code = code
        self.http_status = http_status
        super().__init__(code)


def _identifier(value, pattern, code):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise PlatformError(code)
    return value


def validate_task_id(value):
    return _identifier(value, r"[a-f0-9]{32}", "invalid_task_id")


def validate_dataset_id(value):
    return _identifier(value, r"up_[a-f0-9]{32}", "invalid_dataset_id")


def validate_announcement_id(value):
    if not isinstance(value, str) or len(value) > 128:
        raise PlatformError("invalid_announcement_id")
    return _identifier(value, r"t[0-9]{8}_[0-9]+", "invalid_announcement_id")


def _canonical(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PlatformError("non_finite_business_value")
        return format(value, "f")
    if isinstance(value, list) or isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise PlatformError("invalid_business_key")
        return {key: _canonical(item) for key, item in value.items()}
    raise PlatformError("unsupported_business_value")


def canonical_bytes(value):
    return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def strict_json_loads(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise PlatformError("duplicate_json_key")
            result[key] = item
        return result

    def nonfinite(value):
        raise PlatformError("non_finite_business_value")

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_float=Decimal,
                          parse_constant=nonfinite)
    except (ValueError, UnicodeError) as exc:
        raise PlatformError("invalid_json") from exc


MODES = frozenset({"parse_only", "extract"})
STATUSES = frozenset({"receiving", "queued", "running", "validating", "validated_only",
                      "completed", "partial", "failed", "cancelled", "needs_confirmation"})
PHASES = frozenset({"preflight", "parsing", "extraction", "entity_import",
                    "relation_build", "relation_import", "export"})


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str

    def __post_init__(self):
        if self.role not in {"guest", "reviewer", "admin"}:
            raise PlatformError("invalid_role")


@dataclass(frozen=True)
class ModelBudget:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int

    def __post_init__(self):
        if any(type(value) is not int or value < 0 for value in
               (self.max_calls, self.max_input_tokens, self.max_output_tokens)):
            raise PlatformError("invalid_model_budget")


@dataclass(frozen=True)
class FileSpec:
    name: str
    bytes: int
    part_hashes: tuple[str, ...]


@dataclass(frozen=True)
class UploadSpec:
    files: tuple[FileSpec, ...]
    mode: str
    processing_fingerprint: str
    budget: ModelBudget | None = None

    def __post_init__(self):
        if self.mode not in MODES:
            raise PlatformError("invalid_mode")
        _identifier(self.processing_fingerprint, r"[a-f0-9]{64}", "invalid_processing_fingerprint")


@dataclass(frozen=True)
class Job:
    task_id: str
    upload_dataset_id: str
    owner_id: str
    mode: str
    status: str
    phase: str
    input_fingerprint: str | None
    processing_fingerprint: str
    epoch: int


@dataclass(frozen=True)
class Lease:
    task_id: str
    epoch: int
    worker_token: str


@dataclass(frozen=True)
class PreparedInput:
    announcement_id: str
    html_path: Path
    zip_path: Path | None
    html_sha: str
    zip_sha: str | None

    def to_dto(self):
        return {"announcement_id": self.announcement_id,
                "html_sha": self.html_sha, "zip_sha": self.zip_sha}


@dataclass(frozen=True)
class Checkpoint:
    announcement_id: str
    input_fingerprint: str
    processing_fingerprint: str
    result_sha256: str
    db_state: str
    epoch: int = 0


@dataclass(frozen=True)
class Artifact:
    name: str
    relative_path: str
    sha256: str
    bytes: int
    kind: str
