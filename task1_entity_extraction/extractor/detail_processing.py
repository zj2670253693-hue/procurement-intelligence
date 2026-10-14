"""附件明细文本分块与旧单层结果迁移。"""

from __future__ import annotations

import config
from models.schemas import (
    DetailStatus,
    DetailedEntity,
    ExtractionResult,
    deduplicate_detail_entities,
)


def chunk_attachment_text(
    text: str,
    max_length: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """按字符上限分块，优先在换行处分割并保证游标前进。"""
    text = str(text or "").strip()
    if not text:
        return []
    max_length = max_length or config.MAX_TEXT_LENGTH_PER_REQUEST
    overlap = config.TEXT_CHUNK_OVERLAP if overlap is None else overlap
    max_length = max(1, int(max_length))
    overlap = max(0, min(int(overlap), max_length - 1))

    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_length)
        if end < len(text):
            newline = text.rfind("\n", start + max_length // 2, end)
            if newline > start:
                end = newline + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def migrate_legacy_entities(
    result: ExtractionResult,
    *,
    detail_status: DetailStatus,
) -> ExtractionResult:
    """把旧单层结果转为附件候选；不可读/无附件时不伪造明细。"""
    migrated = result.model_copy(deep=True)
    if migrated.schema_version == 1:
        if detail_status == "available":
            candidates = [
                DetailedEntity(**entity.model_dump())
                for entity in migrated.entities
            ]
            migrated.detail_entities = deduplicate_detail_entities(
                [*migrated.detail_entities, *candidates]
            )
        migrated.entities = []
        migrated.schema_version = 2
    migrated.detail_status = detail_status
    return migrated
