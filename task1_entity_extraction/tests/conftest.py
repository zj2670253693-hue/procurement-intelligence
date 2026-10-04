import json
import os
import secrets
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _temp_dir_is_readable() -> bool:
    probe = None
    try:
        probe = tempfile.mkdtemp()
        with os.scandir(probe):
            pass
        return True
    except OSError:
        return False
    finally:
        if probe:
            try:
                os.rmdir(probe)
            except OSError:
                pass


if not _temp_dir_is_readable():
    def _portable_mkdtemp(suffix=None, prefix=None, dir=None):
        """Keep the parent directory's permissions instead of an owner-only ACL.

        Some confined Windows environments cannot read a directory that
        ``tempfile.mkdtemp`` creates. Creating it with inherited permissions
        keeps pytest's ``tmp_path`` fixtures usable there; the shim is unused
        wherever the standard helper already works.
        """
        base = dir or tempfile.gettempdir()
        prefix = prefix if prefix is not None else tempfile.gettempprefix()
        suffix = suffix if suffix is not None else ""
        for _ in range(100):
            candidate = os.path.join(base, f"{prefix}{secrets.token_hex(6)}{suffix}")
            try:
                os.mkdir(candidate)
            except FileExistsError:
                continue
            return candidate
        raise FileExistsError(f"无法在 {base} 下创建临时目录")

    tempfile.mkdtemp = _portable_mkdtemp


FIELDS = (
    "product_name",
    "category",
    "brand",
    "spec_model",
    "unit_price",
    "quantity",
    "total_price",
)


def make_review_item(data=None, **overrides):
    item = {
        "id": overrides.pop("id", 1),
        "initial_verdict": overrides.pop("initial_verdict", "modified"),
        "is_added": overrides.pop("is_added", False),
        "is_deleted": overrides.pop("is_deleted", False),
        "data": data
        if data is not None
        else {
            "product_name": "测试产品",
            "category": "测试品目",
            "brand": "测试供应商",
            "spec_model": "X-1",
            "unit_price": "100元",
            "quantity": "1台",
            "total_price": "100元",
        },
    }
    item.update(overrides)
    return item


def make_review_case(case_no: int, items: list[dict] | None = None) -> dict:
    return {
        "id": case_no,
        "announcement_id": f"fictional-{case_no:03d}",
        "items": items if items is not None else [make_review_item(id=case_no)],
    }


def make_review_cases(count: int = 40) -> list[dict]:
    return [make_review_case(case_no) for case_no in range(1, count + 1)]


def write_review_json(tmp_path: Path, cases: list[dict]) -> Path:
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps({"cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )
    return path
