"""Load human-reviewed ground truth from review JSON or the legacy Excel file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import config


FIELDS = (
    "product_name",
    "category",
    "brand",
    "spec_model",
    "unit_price",
    "quantity",
    "total_price",
)
FIELD_LABELS = tuple(config.FIELD_LABELS[field] for field in FIELDS)
REVIEWED_VERDICTS = {
    "正确",
    "错误",
    "修改",
    "已修改",
    "新增",
    "删除",
    "删除该行",
    "delete",
    "该公告无标的物",
    "无标的物",
}
DELETED_VERDICTS = {"删除", "删除该行", "delete"}
UNPUBLISHED_PLACEHOLDER = "未公示"


class GroundTruthFormatError(ValueError):
    """Raised when a human-review file cannot safely become ground truth."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def load_review_json(path: Path, *, expected_cases: int = 40) -> list[dict]:
    """Convert a review export into evaluator ground-truth dictionaries."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise GroundTruthFormatError(f"找不到人工金标文件: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GroundTruthFormatError(f"人工金标不是合法 UTF-8 JSON: {path.name}") from exc

    if not isinstance(payload, dict):
        raise GroundTruthFormatError("人工金标顶层必须是 JSON 对象")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise GroundTruthFormatError("人工金标必须包含 cases 数组")
    if len(cases) != expected_cases:
        raise GroundTruthFormatError(
            f"人工金标应包含 {expected_cases} 个公告，实际为 {len(cases)} 个"
        )

    seen_ids: set[str] = set()
    ground_truths: list[dict] = []
    for case_index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise GroundTruthFormatError(f"第 {case_index} 个 case 必须是 JSON 对象")
        announcement_id = _text(case.get("announcement_id"))
        if not announcement_id:
            raise GroundTruthFormatError(f"第 {case_index} 个 case 的 announcement_id 为空")
        if announcement_id in seen_ids:
            raise GroundTruthFormatError(f"公告ID重复: {announcement_id}")
        seen_ids.add(announcement_id)

        items = case.get("items")
        if not isinstance(items, list):
            raise GroundTruthFormatError(f"公告 {announcement_id} 的 items 必须是数组")

        entities: list[dict[str, str]] = []
        for item_index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise GroundTruthFormatError(
                    f"公告 {announcement_id} 的第 {item_index} 个 item 必须是对象"
                )
            if item.get("is_deleted") is True or _text(item.get("initial_verdict")) in DELETED_VERDICTS:
                continue
            data = item.get("data")
            if not isinstance(data, dict):
                raise GroundTruthFormatError(
                    f"公告 {announcement_id} 的第 {item_index} 个 item.data 必须是对象"
                )

            entity: dict[str, str] = {}
            for field in FIELDS:
                value = _text(data.get(field))
                if value == UNPUBLISHED_PLACEHOLDER:
                    raise GroundTruthFormatError(
                        f"公告 {announcement_id} 的字段 {field} 仍为“未公示”，请先清洗为空值"
                    )
                entity[field] = value
            if any(entity.values()):
                entities.append(entity)

        ground_truths.append(
            {"announcement_id": announcement_id, "entities": entities}
        )
    return ground_truths


def load_review_excel(path: Path, *, require_reviewed: bool = True) -> list[dict]:
    """Load the legacy manually reviewed Excel workbook."""
    path = Path(path)
    try:
        frame = pd.read_excel(path, sheet_name="标注(待核验)").fillna("")
    except FileNotFoundError as exc:
        raise GroundTruthFormatError(f"找不到验证集文件: {path}") from exc
    except (ValueError, OSError) as exc:
        raise GroundTruthFormatError(f"无法读取验证集 Excel: {path.name}") from exc

    required_columns = {"公告ID", "核验结论", *FIELD_LABELS}
    missing_columns = sorted(required_columns - set(frame.columns))
    if missing_columns:
        raise GroundTruthFormatError(
            f"验证集 Excel 缺少列: {', '.join(missing_columns)}"
        )

    if require_reviewed:
        verdicts = frame["核验结论"].astype(str).str.strip()
        unreviewed = frame.index[~verdicts.isin(REVIEWED_VERDICTS)].tolist()
        if unreviewed:
            preview = "、".join(str(index + 2) for index in unreviewed[:10])
            raise GroundTruthFormatError(
                f"验证集还有 {len(unreviewed)} 行未完成人工核验"
                f"（Excel 行号示例：{preview}）。请先填写“核验结论”，"
                "或仅在调试时使用 --allow-unreviewed。"
            )

    grouped: dict[str, dict] = {}
    for _, row in frame.iterrows():
        announcement_id = _text(row.get("公告ID"))
        if not announcement_id:
            continue
        verdict = _text(row.get("核验结论"))
        if verdict in DELETED_VERDICTS:
            continue
        entity = {
            field: _text(row.get(label))
            for field, label in zip(FIELDS, FIELD_LABELS)
        }
        if not any(entity.values()):
            continue
        grouped.setdefault(
            announcement_id,
            {"announcement_id": announcement_id, "entities": []},
        )["entities"].append(entity)
    return list(grouped.values())


def load_ground_truth(
    path: Path,
    *,
    expected_cases: int = 40,
    require_reviewed: bool = True,
) -> list[dict]:
    """Load ground truth by exact file suffix."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        return load_review_json(path, expected_cases=expected_cases)
    if suffix == ".xlsx":
        return load_review_excel(path, require_reviewed=require_reviewed)
    raise GroundTruthFormatError(
        f"不支持的金标文件格式 {suffix or '<无后缀>'}；仅支持 .json 或 .xlsx"
    )
