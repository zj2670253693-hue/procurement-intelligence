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
_SUPPLIER_PATTERN = re.compile(
    r"供应商名称[:：]\s*([^\n|]+?)\s*(?=供应商名称|中标|成交|$|\n)",
)
_AMOUNT_TEXT_PATTERN = re.compile(
    r"(?:中标[（(]?成交[）)]?金额|中标金额|成交金额|中标价|成交价)"
    r"[^0-9]{0,20}"
    r"([-+]?\d[\d,]*(?:\.\d+)?\s*[（(]?\s*(?:亿元|万元|万|元)?\s*[）)]?)",
)
_PACKAGE_NAME_PATTERN = re.compile(
    r"([^|\n]{0,80}?(?:第\s*[0-9一二三四五六七八九十]+\s*(?:包|标段|分包)"
    r"|采购包\s*[0-9一二三四五六七八九十]+)[^|\n]{0,30})",
)
# 「主要标的信息」里逐条列出标的物的表格行：以序号开头、含多个单元格，
# 且品名列是真实标的名称（而不是「详见附件」这类引用）。
_ITEM_ROW_PATTERN = re.compile(
    r"^\s*[0-9]+\s*\|[^|\n]*\|[^|\n]*?(?!详见附件)[^\s|][^|\n]*\|",
    re.MULTILINE,
)
_REFERENCE_ROW_PATTERN = re.compile(r"^\s*[0-9]+\s*\|[^|\n]*\|[^|\n]*详见附件", re.MULTILINE)


def _supplier_amounts(source_text: str) -> list[tuple[str, str]]:
    """按「供应商名称 + 中标金额」的成对出现顺序取出（供应商, 金额原文）。"""
    names = [match.group(1).strip() for match in _SUPPLIER_PATTERN.finditer(source_text)]
    amounts = [
        unicodedata.normalize("NFKC", match.group(1)).strip()
        for match in _AMOUNT_TEXT_PATTERN.finditer(source_text)
    ]
    return list(zip(names, amounts))


def _package_labels(source_text: str) -> list[str]:
    labels = []
    for match in _PACKAGE_NAME_PATTERN.finditer(source_text):
        raw = match.group(1)
        label = unicodedata.normalize("NFKC", raw).strip()
        # 表格里的包级行常写作「项目名（第N包）详见附件」，去掉尾部引用词
        for reference in _REFERENCE_VALUES:
            if label.endswith(reference):
                label = label[: -len(reference)].strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _announcement_is_package_only(source_text: str) -> bool:
    """判断公告是否只给出采购包级信息（明细全部指向附件）。"""
    if not _package_labels(source_text):
        return False
    if _supplier_amounts(source_text) and not _has_item_rows(source_text):
        return True
    return False


def _has_item_rows(source_text: str) -> bool:
    """是否存在真实逐条明细行（区别于写「详见附件」的包级行）。"""
    for match in _ITEM_ROW_PATTERN.finditer(source_text):
        row = match.group(0)
        if _REFERENCE_ROW_PATTERN.match(row):
            continue
        if _reference_only_cells(row):
            continue
        return True
    return False


def _reference_only_cells(row: str) -> bool:
    """行内除序号、供应商外的正文单元格是否全是引用文字。"""
    cells = [cell.strip() for cell in row.split("|")]
    body = [cell for cell in cells[2:] if cell]  # 跳过序号与供应商
    return bool(body) and all(
        _normalize_reference(cell) in _REFERENCE_VALUES for cell in body
    )



def _collapse_to_packages(
    entities: list[ExtractedEntity],
    *,
    source_text: str,
) -> list[ExtractedEntity]:
    """把附件展开出的多行收敛成「一行一个采购包」。"""
    pairs = _supplier_amounts(source_text)
    labels = _package_labels(source_text)
    if not pairs or not labels:
        return entities

    collapsed: list[ExtractedEntity] = []
    for index, (supplier, amount_text) in enumerate(pairs):
        label = labels[index] if index < len(labels) else labels[-1]
        if not re.search(r"第\s*[0-9一二三四五六七八九十]+\s*包|采购包", label):
            label = f"{label}（第{index + 1}包）"
        collapsed.append(
            ExtractedEntity(
                product_name=label,
                brand=supplier,
                total_price=amount_text,
            )
        )
    return collapsed or entities



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
    cleaned = deduplicate_entities(cleaned)

    # 公告只给包级信息时，收敛成一行一个采购包，避免附件明细撑出数百行误报。
    if _announcement_is_package_only(source_text):
        cleaned = _collapse_to_packages(cleaned, source_text=source_text)
    return cleaned
