"""采购包级基础结果的输入构造与确定性校验。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata

from models.schemas import (
    DetailStatus,
    ExtractedEntity,
    deduplicate_entities,
)


_REFERENCE_VALUES = {
    "详见附件",
    "见附件",
    "详见采购文件",
    "详见招标文件",
    "详见磋商文件",
    "按招标要求提供",
    "按采购要求提供",
    "按采购文件要求",
}
_ATTACHMENT_MISSING_FIELDS = {"spec_model", "unit_price", "quantity"}
_TRANSACTION_AMOUNT_PATTERN = re.compile(
    r"(?:中标[（(]?成交[）)]?金额|中标金额|成交金额|中标价|成交价)"
    r"[^0-9]{0,20}"
    r"([-+]?\d[\d,]*(?:\.\d+)?)\s*[（(]?\s*(亿元|万元|万|元)?",
)


def build_summary_source(html_text: str, html_tables: list) -> str:
    """只把 HTML 正文和 HTML 表格组成基础层输入。"""
    parts = [str(html_text or "").strip()]
    for table_index, table in enumerate(html_tables or [], start=1):
        rows = [
            " | ".join(str(cell or "").strip() for cell in row)
            for row in table
            if row
        ]
        if rows:
            parts.append(f"--- HTML表格{table_index} ---\n" + "\n".join(rows))
    return "\n\n".join(part for part in parts if part)


def _normalize_reference(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    return normalized.rstrip("。；;，,")


def _parse_amount(value: str, unit: str = "") -> Decimal | None:
    normalized = unicodedata.normalize("NFKC", str(value or "")).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", normalized)
    if not match:
        return None
    try:
        amount = Decimal(match.group(0))
    except InvalidOperation:
        return None

    combined = f"{normalized}{unit}"
    if "亿元" in combined:
        amount *= Decimal("100000000")
    elif "万元" in combined or unit == "万":
        amount *= Decimal("10000")
    return amount.normalize()


def _explicit_transaction_amounts(source_text: str) -> set[Decimal]:
    amounts = set()
    normalized = unicodedata.normalize("NFKC", str(source_text or ""))
    for match in _TRANSACTION_AMOUNT_PATTERN.finditer(normalized):
        amount = _parse_amount(match.group(1), match.group(2) or "")
        if amount is not None:
            amounts.add(amount)
    return amounts


def sanitize_summary_entities(
    entities: list[ExtractedEntity],
    *,
    detail_status: DetailStatus,
    source_text: str,
) -> list[ExtractedEntity]:
    """清除引用性占位词和非明确成交金额，再稳定去重。"""
    explicit_amounts = _explicit_transaction_amounts(source_text)
    cleaned = []
    for original in entities:
        entity = original.model_copy(deep=True)
        for field in (
            "product_name",
            "category",
            "brand",
            "spec_model",
            "unit_price",
            "quantity",
        ):
            value = _normalize_reference(getattr(entity, field))
            if value not in _REFERENCE_VALUES:
                continue
            if (
                "附件" in value
                and field in _ATTACHMENT_MISSING_FIELDS
                and detail_status in {"missing", "unreadable"}
            ):
                setattr(entity, field, "附件缺失")
            else:
                setattr(entity, field, "")

        if entity.total_price:
            amount = _parse_amount(entity.total_price)
            if amount is None or amount not in explicit_amounts:
                entity.total_price = ""

        if any(getattr(entity, field) for field in (
            "product_name",
            "category",
            "brand",
            "spec_model",
            "unit_price",
            "quantity",
            "total_price",
        )):
            cleaned.append(entity)
    return deduplicate_entities(cleaned)
