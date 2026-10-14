"""Legacy package-award proof helpers; package copying is disabled in V12."""

from dataclasses import dataclass
import re
import unicodedata

from extractor.quantity_units import _body_tables, _column, _row_package
from extractor.supplier_rules import _aligned_rows, _body_sections, _key, _name, _package, _scope, _supplier
from models.schemas import ExtractedEntity


TOTAL_AWARD_RULE_VERSION = "item-money-not-package-award-v12"
_NAMES = {"名称", "货物名称", "服务名称", "工程名称", "产品名称", "采购标的", "标的名称"}
_SUPPLIERS = {"供应商名称", "中标供应商", "成交供应商"}
_BRANDS = {"货物品牌", "品牌", "产品品牌"}
_PREFIX = re.compile(r"^(第[0-9一二三四五六七八九十]+包)(.*)$")
_AWARD_LABEL = re.compile(r"(?:中标\(成交\)金额|中标/成交金额|中标金额|成交金额|中标价|成交价)(?:\((亿元|万元|元)\))?$")
_AWARD_PREFIX = re.compile(r"^(?:中标\(成交\)金额|中标/成交金额|中标金额|成交金额|中标价|成交价)")
_AMOUNT = re.compile(r"[￥¥]?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?:(亿元|万元|元)|\((亿元|万元|元)\))?(?:\(人民币\))?$")
_INVALID = {"invalid_package", "conflicting_package"}


@dataclass(frozen=True)
class _Item:
    name: str
    package: str
    supplier: str
    brand: str
    cells: tuple[str, ...]
    multiple_products: bool


@dataclass(frozen=True)
class _Award:
    package: str
    supplier: str
    amount: str


def _label(value: str) -> tuple[str, str]:
    """An anchored package label is identity, never a row index or inference."""
    text = _key(value)
    match = _PREFIX.fullmatch(text)
    if not match:
        return "", _supplier(text)
    tail = match[2]
    if re.search(r"第[0-9一二三四五六七八九十]+包|采购包[0-9一二三四五六七八九十]+|标包:", tail):
        return "conflicting_package", ""
    if tail.startswith(("-", "—", ":")):
        company = tail[1:]
    elif tail.count(":") == 1:
        _, company = tail.split(":", 1)
    else:
        company = ""
    return _package(match[1]), _supplier(company)


def _items(body: str, tables: list[list[list[str]]]) -> tuple[set[_Item], bool]:
    """Require complete main-item rows in both body and serialized HTML."""
    headers = {tuple(_key(cell) for cell in row) for table in tables for row in table
               if len(row) > 1 and _column([_key(cell) for cell in row], _NAMES) is not None}
    serialized, complete = set(), True
    for table in tables:
        header = None
        for row in table:
            cells = tuple(_key(cell) for cell in row)
            if cells in headers:
                header = cells
                continue
            if header and any(cells):
                if len(cells) != len(header) or not all(cells):
                    complete = False
                else:
                    serialized.add((header, cells))
    items, proven = set(), set()
    for role, lines in _body_sections(body):
        if role != "item":
            continue
        normalized = [_key(line) for line in lines]
        for i, line in enumerate(normalized):
            if tuple(line.split("|")) in headers or any(
                    tuple(normalized[i:i + len(header)]) == header for header in headers):
                break
            if not _package(line):
                complete = False
        for header, segment in _body_tables(lines, headers):
            complete = complete and _complete_segment(header, segment)
            ni, si = _column(header, _NAMES), _column(header, _SUPPLIERS)
            for body_package, cells in _aligned_rows(header, segment):
                if ni is None or cells[ni] in _NAMES:
                    continue
                proven.add((tuple(header), cells))
                raw_supplier = cells[si] if si is not None else ""
                label_package, supplier = _label(raw_supplier)
                row_package = _row_package(header, list(cells), cells[ni], raw_supplier)
                packages = {p for p in (body_package, label_package, row_package) if p}
                package = next(iter(packages)) if len(packages) == 1 else ("conflicting_package" if packages else "")
                brands = [i for i, h in enumerate(header) if h in _BRANDS]
                brand = cells[brands[0]] if len(brands) == 1 else ("ambiguous_brand_columns" if brands else supplier)
                items.add(_Item(_name(cells[ni]), package, supplier, brand, cells, bool(re.search(r"[;；、,，]", cells[ni]))))
    return items, complete and bool(proven) and proven == serialized


def _complete_segment(header: list[str], lines: list[str]) -> bool:
    """Unparsed declarations/partial rows cannot prove a single-item package."""
    normalized = [_key(line) for line in lines]
    width, i = len(header), 0
    while i < len(normalized):
        line = normalized[i]
        if _package(line):
            i += 1
        elif "|" in line:
            cells = line.split("|")
            if len(cells) != width or not all(cells):
                return False
            i += 1
        elif normalized[i:i + width] == header:
            i += width
        else:
            cells = normalized[i:i + width]
            if (len(cells) != width or not all(cells)
                    or any("|" in cell or _package(cell) for cell in cells)
                    or any(re.match(r"(?:货物|服务|工程|产品)?名称:", cell) for cell in cells)):
                return False
            i += width
    return True


def _literal_amount(value: str, header_unit: str = "") -> str:
    text = unicodedata.normalize("NFKC", value).strip()
    match = _AMOUNT.fullmatch(text)
    if not match:
        return ""
    unit = match[2] or match[3] or header_unit
    if not unit or (header_unit and (match[2] or match[3]) and unit != header_unit):
        return ""
    # No numeric rewrite, multiplication, allocation, or currency conversion.
    return value.strip() if match[2] or match[3] else value.strip() + unit


def _awards(body: str) -> list[_Award]:
    awards = []
    for role, lines in _body_sections(body):
        if role != "award":
            continue
        # Unsupported declared rows/line-wrapped amounts remain negative
        # evidence rather than disappearing from the uniqueness check.
        if any(_AWARD_LABEL.fullmatch(_key(line).rstrip(":"))
               or _key(line).startswith(("中标供应商", "成交供应商"))
               or (_AWARD_PREFIX.match(_key(line)) and not _AWARD_LABEL.fullmatch(_key(line).split(":", 1)[0]))
               or ("|" in line and ("供应商名称" in line or "金额" in line)) for line in lines):
            awards.append(_Award("invalid_package", "", ""))
        contexts = {_package(line) for line in lines if _package(line)}
        blocks, current = [], []
        for line in lines:
            if (_key(line).rstrip(":") == "供应商名称" or _key(line).startswith("供应商名称:")) and current:
                blocks.append(current)
                current = []
            current.append(line)
        if current:
            blocks.append(current)
        for block in blocks:
            suppliers, amounts = [], []
            for i, line in enumerate(block):
                parts = re.split(r"[:：]", line, maxsplit=1)
                if _key(parts[0]) == "供应商名称":
                    value = parts[1].strip() if len(parts) == 2 else ""
                    value = value or (block[i + 1] if i + 1 < len(block) else "")
                    suppliers.append(_label(value))
                elif len(parts) == 2 and (match := _AWARD_LABEL.fullmatch(_key(parts[0]))):
                    amounts.append(_literal_amount(parts[1], match[1] or ""))
            if len(suppliers) == 1:
                package, company = suppliers[0]
                if contexts and contexts != {package}:
                    package = "conflicting_package"
                awards.append(_Award(package, company, amounts[0] if len(amounts) == 1 else ""))
    return awards


def total_award_decisions(entities: list[ExtractedEntity], *, source_text: str) -> list[tuple[str, str]]:
    body, tables, valid = _scope(source_text)
    items, consistent = _items(body, tables) if valid else (set(), False)
    awards = _awards(body) if valid else []
    decisions = []
    for entity in entities:
        if entity.total_price != "":
            decisions.append(("", "preserve_nonempty_total"))
            continue
        if not valid or not consistent:
            decisions.append(("", "unproven_html_item_scope"))
            continue
        matching = {item for item in items if item.name and item.name == _name(entity.product_name)}
        if len(matching) != 1:
            decisions.append(("", "no_unique_item_owner"))
            continue
        owner = next(iter(matching))
        package_items = {item for item in items if item.package == owner.package}
        if (not owner.package or owner.package in _INVALID or len(package_items) != 1
                or any(not item.package or item.package in _INVALID for item in items)
                or owner.multiple_products or not owner.supplier
                or not owner.brand or owner.brand == "ambiguous_brand_columns"
                or not _name(owner.brand) or _key(entity.brand) != owner.brand):
            decisions.append(("", "not_single_item_package_owner"))
            continue
        candidates = [award for award in awards if award.package == owner.package]
        if (len(candidates) != 1 or any(not award.package or award.package in _INVALID for award in awards)
                or candidates[0].supplier != owner.supplier or not candidates[0].amount):
            decisions.append(("", "no_unique_explicit_package_award"))
            continue
        # One publicly listed item does not prove its price equals the package
        # award: taxes/services or undisclosed items may account for the rest.
        decisions.append(("", "package_award_is_not_item_total_evidence"))
    return decisions


def fill_empty_award_totals(entities: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    """Clone-only compatibility entry point; item proof runs after cleanup."""
    # Kept for call compatibility. Item literals/arithmetic are handled by
    # revise_base_entities with the original model multiplicity, AFTER cleanup.
    # Computing here would let an earlier dedup erase ambiguity before proof.
    return [entity.model_copy(deep=True) for entity in entities]
