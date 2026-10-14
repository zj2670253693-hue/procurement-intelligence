from dataclasses import asdict, is_dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
from .contracts import SERIALIZATION_VERSION

def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("nonfinite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if value == 0 else text

def _plain(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, float):
        raise TypeError("float is forbidden")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON keys must be strings")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError("unsupported canonical value")

def canonical_bytes(value: object) -> bytes:
    return (json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def implementation_fingerprint(package_root: Path) -> dict:
    return {"source_sha256": {p.relative_to(package_root).as_posix(): sha256(p.read_bytes()) for p in sorted(package_root.rglob("*.py"))}, "python": platform.python_version(), "dependencies": "stdlib", "serialization": SERIALIZATION_VERSION}

def make_dataset_id(input_manifest: dict, implementation: dict, schema_version: str, rule_version: str) -> str:
    return "ds_" + sha256(canonical_bytes({"inputs": input_manifest, "implementation": implementation, "schema": schema_version, "rules": rule_version}))

def make_record_id(dataset_id: str, kind: str, natural_key: tuple[str, ...]) -> str:
    return kind + "_" + sha256(canonical_bytes([dataset_id, kind, natural_key]))
