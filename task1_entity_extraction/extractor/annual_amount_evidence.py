"""Restore a literal /年 award to an equivalent, already nonempty base total.

This narrow pass does not fill missing totals or infer ownership from amounts.
Unsupported declarations remain vetoes; attachment text is never evidence.
"""
from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata

from extractor.supplier_rules import _body_sections, _key, _scope, _supplier
from extractor.total_award_rules import _items
from models.schemas import ExtractedEntity


ANNUAL_AMOUNT_RULE_VERSION = "explicit-owned-annual-amount-v1"
_NUMBER = r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
_PLAIN = re.compile(r"[￥¥]?" + _NUMBER + r"\s*(?:(亿元|万元|元)|\(\s*(亿元|万元|元)\s*\))(?:\s*\(人民币\))?$")
_ANNUAL = re.compile(r"[￥¥]?" + _NUMBER + r"\s*(亿元|万元|元)\s*/\s*年$")
_PACKAGE = r"(?:第([0-9]+|[一二三四五六七八九十])包|采购包([0-9]+|[一二三四五六七八九十])|包([0-9]+|[一二三四五六七八九十]))"
_SECTION = re.compile(r"^[一二三四五六七八九十0-9]+[、.](.+)$")
_OWNERS = {"供应商名称", "成交供应商", "中标供应商"}
_AMOUNTS = {"中标(成交)金额", "中标/成交金额", "中标金额", "成交金额", "中标价", "成交价"}
_DECLARATION = re.compile(r"供应商名称|成交供应商|中标供应商|中标人|成交人|中标单位|成交单位|中标\(成交\)金额|中标/成交金额|(?:中标|成交)(?:总)?(?:金额|价|报价)")
_NEGATIVE = re.compile(r"预算|最高限价|候选人|投标报价|未中标|未成交|非最终|非成交|并非.*成交|不是.*成交|撤销|更正")
_SCALE = {"亿元": Decimal(100000000), "万元": Decimal(10000), "元": Decimal(1)}


@dataclass(frozen=True)
class _AnnualAward:
    package: str
    supplier: str
    amount: Decimal
    literal: str


def _amount(value: str, *, annual: bool = False) -> Decimal | None:
    # Do not remove whitespace inside a number, repair commas or accept suffixes.
    text = unicodedata.normalize("NFKC", value or "").strip()
    match = (_ANNUAL if annual else _PLAIN).fullmatch(text)
    if not match:
        return None
    unit = match[2] if annual else (match[2] or match[3])
    return Decimal(match[1].replace(",", "")) * _SCALE[unit]


def _identity(value: str, project: str, *, numbered_heading: bool = False) -> str:
    text = _key(value)
    if numbered_heading:
        text = re.sub(r"^[0-9]+[.、]", "", text)
    if not text.startswith(project):
        return ""
    tail = text[len(project):]
    if tail.startswith("(") and tail.endswith(")"):
        tail = tail[1:-1]
    match = re.fullmatch(_PACKAGE, tail)
    if not match:
        return ""
    number = next(group for group in match.groups() if group)
    if number.isascii() and number.isdigit():
        return str(int(number)) if int(number) > 0 else ""
    return str("一二三四五六七八九十".index(number) + 1)


def _field(line: str) -> tuple[str, str]:
    parts = re.split(r"[:：]", line, maxsplit=1)
    return (_key(parts[0]), parts[1].strip()) if len(parts) == 2 else ("", "")


def _main_item_owners_agree(body: str, tables: list, project: str, awards: list[_AnnualAward]) -> bool:
    """Keep parsed item ownership and unparsed table declarations as vetoes."""
    for table in tables:
        if not table or _key(table[0][0]).rstrip(":") == "公告信息":
            continue  # Announcement aggregate metadata is not a package award.
        for row in table:
            for cell in row:
                label = _key(cell).rstrip(":")
                # Supplier headers are reconciled through the complete item rows
                # below. Other owner/amount tables are unsupported, not absent.
                if label not in _OWNERS and _DECLARATION.search(label):
                    return False
    if not any(role == "item" for role, _ in _body_sections(body)):
        return True
    items, consistent = _items(body, tables)
    if not consistent:
        return False
    for item in items:
        packages = {_identity(cell, project) for cell in item.cells} - {""}
        if item.package in {"invalid_package", "conflicting_package"}:
            return False
        if item.package.startswith("包:"):
            packages.add(item.package.removeprefix("包:"))
        if len(packages) > 1:
            return False
        for award in awards:
            if award.package in packages and item.supplier != award.supplier:
                return False
    return True


def _catalog(body: str, tables: list, project: str) -> list[_AnnualAward]:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if _NEGATIVE.search(_key(body)):
        return []
    # Standalone primary package contexts are currently unsupported. They must
    # veto a correction rather than lose their ownership when a supplier is read.
    if any(re.fullmatch(_PACKAGE, _key(line).rstrip(":")) for line in lines):
        return []
    roles, role = [], ""
    for line in lines:
        heading = _SECTION.fullmatch(_key(line))
        if heading and not _identity(line, project, numbered_heading=True):
            title = heading[1].rstrip(":")
            role = "award" if title in {"中标(成交)信息", "中标信息", "成交信息"} else (
                "supplement" if title in {"其它补充事宜", "其他补充事宜"} else (
                    "item" if title == "主要标的信息" else ""))
        roles.append(role)

    annual, consumed = [], set()
    for i in range(len(lines) - 2):
        package = _identity(lines[i], project, numbered_heading=True)
        if not package or roles[i] not in {"award", "supplement"}:
            continue
        owner_label, owner_value = _field(lines[i + 1])
        money_label, money_value = _field(lines[i + 2])
        supplier = _supplier(_key(owner_value))
        amount = _amount(money_value, annual=True)
        if (owner_label in _OWNERS and money_label in _AMOUNTS and supplier
                and amount is not None and roles[i:i + 3] == [roles[i]] * 3):
            annual.append(_AnnualAward(package, supplier, amount, money_value))
            consumed.update((i + 1, i + 2))
    if not annual:
        return []
    # A repeated package declaration, even if identical, does not prove uniqueness.
    if len({award.package for award in annual}) != len(annual):
        return []
    if not _main_item_owners_agree(body, tables, project, annual):
        return []

    # The ordinary award header can corroborate a value, never assign a package.
    # An unsupported owner/amount statement anywhere in the body must not vanish.
    primary, supplier, money, current_role = [], "", [], ""

    def finish() -> bool:
        if supplier:
            if len(money) != 1 or money[0] is None:
                return False
            primary.append((supplier, money[0]))
        return True

    for i, line in enumerate(lines):
        if roles[i] != current_role:
            if not finish():
                return []
            supplier, money, current_role = "", [], roles[i]
        if i in consumed:
            continue
        label, value = _field(line)
        if current_role == "award" and label == "供应商名称":
            if not finish():
                return []
            supplier, money = _supplier(_key(value)), []
            if not supplier:
                return []
            consumed.add(i)
        elif current_role == "award" and label in _AMOUNTS and supplier:
            money.append(_amount(value))
            consumed.add(i)
    if not finish():
        return []
    item_headers = {tuple(_key(cell) for cell in row) for table in tables for row in table
                    if any(_key(cell) in _OWNERS for cell in row)}
    for i, line in enumerate(lines):
        # Only verified main-item headers explain a bare header occurrence.
        is_item_header = roles[i] == "item" and (
            _key(line) in _OWNERS
            or tuple(_key(cell) for cell in line.split("|")) in item_headers
        )
        if _DECLARATION.search(_key(line)) and i not in consumed and not is_item_header:
            return []
    for supplier, amount in primary:
        matching = [award for award in annual if award.supplier == supplier]
        if len(matching) != 1 or matching[0].amount != amount:
            return []
    return annual


def restore_proven_annual_amounts(entities: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    """Change only a proven equivalent nonempty total; preserve order and input."""
    if not re.search(r"元\s*[/／]\s*年", source_text or ""):
        return entities
    body, tables, valid = _scope(source_text)
    if not valid:
        return entities
    projects = {_key(row[1]) for table in tables if table and _key(table[0][0]).rstrip(":") == "公告信息"
                for row in table[1:] if len(row) == 2 and _key(row[0]) == "采购项目名称"}
    if len(projects) != 1:
        return entities
    project = next(iter(projects))
    awards = _catalog(body, tables, project)
    restored = []
    for original in entities:
        entity = original.model_copy(deep=True)
        package = _identity(entity.product_name, project)
        owner = _supplier(_key(entity.brand))
        matching = [award for award in awards if package and award.package == package and owner == award.supplier]
        if len(matching) == 1 and _amount(entity.total_price) == matching[0].amount:
            entity.total_price = matching[0].literal
        restored.append(entity)
    return restored
