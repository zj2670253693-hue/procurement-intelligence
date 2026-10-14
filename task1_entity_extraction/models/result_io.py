"""双层提取结果的兼容 JSON 读写。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.schemas import ExtractionResult


def parse_extraction_result(payload: Mapping[str, Any]) -> ExtractionResult:
    """读取新旧 raw result；旧载荷保持待迁移状态。"""
    if not isinstance(payload, Mapping):
        raise ValueError("提取结果项必须是对象")

    data = dict(payload)
    data.setdefault("schema_version", 1)
    data.setdefault("detail_entities", [])
    data.setdefault("detail_status", None)
    data.setdefault("detail_error_message", "")
    data.setdefault("summary_input_hash", "")
    data.setdefault("detail_input_hash", "")
    if "error_message" not in data:
        data["error_message"] = data.get("error") or ""
    return ExtractionResult(**data)


def serialize_extraction_result(
    result: ExtractionResult,
    *,
    require_resolved: bool = False,
) -> dict:
    """输出稳定公开结构，不写入模型原始响应。"""
    if require_resolved and result.detail_status is None:
        raise ValueError(f"公告 {result.announcement_id} 的附件状态仍待迁移")

    return {
        "schema_version": result.schema_version,
        "announcement_id": result.announcement_id,
        "success": result.success,
        "error": result.error_message,
        "entities": [entity.model_dump() for entity in result.entities],
        "detail_entities": [entity.model_dump() for entity in result.detail_entities],
        "detail_status": result.detail_status,
        "detail_error_message": result.detail_error_message,
        "summary_input_hash": result.summary_input_hash,
        "detail_input_hash": result.detail_input_hash,
        "extra_info": result.extra_info,
    }
