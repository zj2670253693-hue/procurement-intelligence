"""Source-only, conservative award-supplier fill for base results, not details."""

from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata

from models.schemas import ExtractedEntity


SUPPLIER_FILL_RULE_VERSION = "explicit-supplier-fill-v1"
_MARKER = re.compile(r"^---[^\n]*---[ \t]*$", re.MULTILINE)
_HTML = re.compile(r"---\s*HTML表格[1-9][0-9]*\s*---")
_REFERENCES = {"", "详见附件", "见附件", "详见采购文件", "详见招标文件",
               "详见磋商文件", "详见中标文件"}
_MISSING = {"无", "未公示", "未公布", "不适用", "暂无", "未提供", "-", "/", "n/a", "null"}
_NAMES = {"名称", "货物名称", "服务名称", "工程名称", "产品名称"}
_SUPPLIERS = {"供应商名称", "中标供应商", "成交供应商"}
_AWARD = r"(?:中标\(成交\)金额|中标/成交金额|中标金额|成交金额)"
_AWARD_HEADER = re.compile(_AWARD + r"(?:\((?:单位:)?(?:亿元|万元|元)\))?$")
_PACKAGE = re.compile(r"(?:第([0-9一二三四五六七八九十]+)包|采购包([0-9一二三四五六七八九十]+)|标包:?([A-Za-z0-9]+))[:：]?$")
_SECTION = re.compile(r"^[一二三四五六七八九十0-9]+[、.](?=\D)(.+)$")
_BID = re.compile(r"投标报价|评审报价|报价排名|未中标|未成交|候选人")


def _key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def _usable(value: str) -> bool:
    normalized = _key(value).rstrip("。.;；，,")
    return (bool(normalized) and normalized not in _REFERENCES
            and normalized.lower() not in _MISSING and not re.match(r"(?:详见|参见|见附件)", normalized)
            and not re.match(r"以附件为准(?:$|[(:,;])", normalized)
            and not re.match(r"(?:未公示|未公布|暂无|不适用|未提供|无)(?:$|[(:,;])", normalized))


def _supplier(value: str) -> str:
    if not _usable(value) or re.search(r"供应商地址|供应商名称|中标金额|成交金额|[:：|]", value):
        return ""
    return value.strip()


def _name(value: str) -> str:
    # Remove a full trailing citation, never infer a shorter product name.
    value = _key(value)
    for reference in sorted(_REFERENCES - {""}, key=len, reverse=True):
        if value.endswith(reference):
            value = value[:-len(reference)].rstrip("，,;；。")
            break
    return value if _usable(value) else ""


def _package(value: str) -> str:
    match = _PACKAGE.fullmatch(_key(value))
    if not match:
        return ""
    number = match[1] or match[2]
    if number:
        chinese = {c: str(i) for i, c in enumerate("一二三四五六七八九十", 1)}
        return "包:" + chinese.get(number, number.lstrip("0") or "0")
    return "标包:" + match[3].upper()


def _money(value: str, header: str = "") -> Decimal | None:
    text = _key(value)
    match = re.fullmatch(r"[￥¥]?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(亿元|万元|元)?(?:\(人民币\))?", text)
    if not match:
        return None
    header_unit = next((u for u in ("亿元", "万元", "元") if "(" + u + ")" in _key(header)), "")
    unit = match[2] or header_unit
    if not unit or (match[2] and header_unit and match[2] != header_unit):
        return None
    return Decimal(match[1].replace(",", "")) * {"亿元": Decimal(100000000), "万元": Decimal(10000), "元": Decimal(1)}[unit]


@dataclass(frozen=True)
class _Item:
    name: str
    package: str
    supplier: str = ""
    row: bool = False


@dataclass(frozen=True)
class _Award:
    package: str
    supplier: str
    amount: Decimal | None


def _scope(source: str) -> tuple[str, list[list[list[str]]], bool]:
    source = (source or "").replace("\r\n", "\n")
    markers = list(_MARKER.finditer(source))
    cutoff = next((m.start() for m in markers if "附件" in m.group() or "全页原生文本" in m.group()), len(source))
    markers = [m for m in markers if m.start() < cutoff]
    body = source[:markers[0].start() if markers else cutoff]
    if any(not _HTML.fullmatch(marker.group().strip()) for marker in markers):
        return body, [], False
    tables = []
    projects = []
    for i, marker in enumerate(markers):
        if not _HTML.fullmatch(marker.group().strip()):
            continue
        end = markers[i + 1].start() if i + 1 < len(markers) else cutoff
        rows = [[cell.strip() for cell in line.split("|")]
                for line in source[marker.end():end].splitlines() if line.strip()]
        tables.append(rows)
        if rows and _key(rows[0][0]).rstrip(":") == "公告信息":
            names = [r[1] for r in rows[1:] if len(r) == 2 and _key(r[0]) == "采购项目名称"]
            if len(names) != 1 or not _usable(names[0]):
                return body, tables, False
            projects.append(_key(names[0]))
    valid = bool(projects) and len(set(projects)) == 1
    body_projects = [m[1] for line in body.splitlines() if (
        m := re.fullmatch(r"(?:[一二三四五六七八九十0-9]+[、.])?(?:采购)?项目名称:(.+)", _key(line))
    )]
    body_lines = [_key(line) for line in body.splitlines() if line.strip()]
    for i, line in enumerate(body_lines):
        parts = line.split("|")
        if len(parts) == 2 and parts[0].rstrip(":") in {"采购项目名称", "项目名称"}:
            body_projects.append(parts[1])
        if line.rstrip(":") in {"采购项目名称", "项目名称"}:
            body_projects.append(body_lines[i + 1] if i + 1 < len(body_lines) else "")
    valid = valid and all(project == projects[0] for project in body_projects)
    return body, tables, valid


def _body_sections(body: str) -> list[tuple[str, list[str]]]:
    sections, role, lines = [], "", []
    for raw in body.splitlines():
        normalized = _key(raw)
        heading = _SECTION.fullmatch(normalized)
        if heading:
            if role:
                sections.append((role, lines))
            title = heading[1].rstrip(":")
            role = "award" if re.fullmatch(r"(?:中标(?:\(成交\))?|成交)信息", title) else (
                "item" if title == "主要标的信息" else ""
            )
            lines = []
        elif _BID.search(normalized) and role == "award":
            sections.append((role, lines))
            role, lines = "", []
        elif role and normalized:
            lines.append(raw.strip())
    if role:
        sections.append((role, lines))
    return sections


def _blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    blocks, package, current = [], "", []
    for line in lines:
        label = _package(line)
        if label:
            if package:
                blocks.append((package, current))
            package, current = label, []
        elif package:
            current.append(line)
    if package:
        blocks.append((package, current))
    return blocks


def _fields(lines: list[str], label: str) -> list[str]:
    values = []
    for i, line in enumerate(lines):
        parts = re.split(r"[:：]", line, maxsplit=1)
        if len(parts) == 2 and re.fullmatch(label, _key(parts[0])):
            value = parts[1].strip()
            if not value and i + 1 < len(lines) and ":" not in _key(lines[i + 1]):
                value = lines[i + 1]
            elif value and i + 1 < len(lines) and _key(lines[i + 1]) in {"元", "万元", "亿元"}:
                value += lines[i + 1]
            values.append(value)
    return values


def _aligned_rows(header: list[str], lines: list[str]) -> set[tuple[str, tuple[str, ...]]]:
    """Keep complete pipe/vertical rows, cell alignment and package boundaries."""
    found = set()
    package, pipe_header, vertical = "", False, []
    for line in lines:
        label = _package(line)
        if label:
            package, pipe_header = label, False
            vertical.append(("", label))
        elif "|" in line:
            values = [_key(cell) for cell in line.split("|")]
            if values == header:
                pipe_header = True
            elif pipe_header and len(values) == len(header):
                found.add((package, tuple(values)))
            vertical.append(("", package))
        else:
            vertical.append((_key(line), package))
    size = len(header)
    for i in range(len(vertical) - size + 1):
        if [cell for cell, _ in vertical[i:i + size]] != header:
            continue
        for j in range(i + size, len(vertical) - size + 1, size):
            group = vertical[j:j + size]
            values = [cell for cell, _ in group]
            if not all(values) or values == header:
                break
            if len({p for _, p in group}) == 1:
                found.add((group[0][1], tuple(values)))
    return found


def _row_packages(header: list[str], cells: list[str], sections: list[tuple[str, list[str]]]) -> set[str]:
    wanted = tuple(_key(cell) for cell in cells)
    return {package for role, lines in sections if role == "item"
            for package, values in _aligned_rows(header, lines) if values == wanted}


def _item_package(header: list[str], cells: list[str], name: str) -> str:
    labels = [i for i, c in enumerate(header) if c in {"标包", "采购包", "包号"}]
    if len(labels) == 1:
        index = labels[0]
        prefix = "标包:" if header[index] == "标包" else "采购包"
        return _package(cells[index]) or _package(prefix + _key(cells[index]))
    labels = re.findall(r"第[0-9一二三四五六七八九十]+包|采购包[0-9一二三四五六七八九十]+|标包:[A-Za-z0-9]+", name)
    return _package(labels[0]) if len(labels) == 1 else ""


def _catalog(body: str, tables: list[list[list[str]]]) -> tuple[set[_Item], set[_Award]]:
    sections = _body_sections(body)
    items, awards, columns, item_names = set(), set(), [], set()
    for rows in tables:
        if not rows:
            continue
        header = [_key(c) for c in rows[0]]
        # Skip parent/merged tables instead of mining their flattened children.
        if len(header) < 2 or any(_BID.search(c) for c in header):
            continue
        names = [i for i, c in enumerate(header) if c in _NAMES]
        suppliers = [i for i, c in enumerate(header) if c in _SUPPLIERS]
        amounts = [i for i, c in enumerate(header) if _AWARD_HEADER.fullmatch(c)]
        for cells in rows[1:]:
            if len(cells) != len(header) or [_key(c) for c in cells] == header:
                continue
            if len(names) == 1 and _name(cells[names[0]]):
                name = _name(cells[names[0]])
                item_names.add((name, _key(cells[names[0]])))
                if len(suppliers) == 1:
                    literal_package = _item_package(header, cells, name)
                    wanted = tuple(_key(cell) for cell in cells)
                    # A serialized table can be a subset of the body. Keep
                    # unmatched same-name body rows as negative evidence too.
                    for role, lines in sections:
                        if role != "item":
                            continue
                        for package, values in _aligned_rows(header, lines):
                            if _name(values[names[0]]) == name and values != wanted:
                                items.add(_Item(name, package or _item_package(header, list(values), name), "", True))
                    for package in _row_packages(header, cells, sections):
                        if package and literal_package and package != literal_package:
                            items.add(_Item(name, "conflicting_row_package", "", True))
                        else:
                            items.add(_Item(name, package or literal_package,
                                            _supplier(cells[suppliers[0]]), True))
            elif len(suppliers) == len(amounts) == 1:
                si, ai = suppliers[0], amounts[0]
                supplier, amount = _supplier(cells[si]), _money(cells[ai], header[ai])
                if supplier and amount is not None:
                    columns.append((supplier, amount, header, cells, si, ai))
    for role, lines in sections:
        for package, block in _blocks(lines):
            if role == "item":
                declared = _fields(block, r"(?:名称|货物名称|服务名称|工程名称|产品名称)")
                for name in declared:
                    if _name(name):
                        items.add(_Item(_name(name), package))
                for name, raw in item_names:
                    if raw in {_key(line) for line in block}:
                        items.add(_Item(name, package))
            else:
                suppliers = _fields(block, r"供应商名称")
                amounts = _fields(block, _AWARD)
                if len(suppliers) > 1 or len(amounts) > 1 or any(not _supplier(s) for s in suppliers):
                    awards.add(_Award(package, "", None))  # Preserve ambiguity, including alternate formats.
                if len(suppliers) == len(amounts) == 1 and _supplier(suppliers[0]):
                    value = re.sub(r"^\(可填写下浮率、折扣率或费率\)[:：]", "", _key(amounts[0]))
                    awards.add(_Award(package, _supplier(suppliers[0]), _money(value)))
                for supplier, amount, header, cells, si, ai in columns:
                    source_rows = {values for _, values in _aligned_rows(header, block)}
                    # Missing money does not erase an explicit competing row.
                    if len(source_rows) > 1 or any(not _supplier(r[si]) for r in source_rows):
                        awards.add(_Award(package, "", None))
                    if tuple(_key(cell) for cell in cells) in source_rows:
                        awards.add(_Award(package, supplier, amount))
    return items, awards


def supplier_fill_decisions(entities: list[ExtractedEntity], *, source_text: str) -> list[tuple[str, str]]:
    """Per-row literal supplier and source-only decision; never use gold or order."""
    body, tables, valid = _scope(source_text)
    items, awards = _catalog(body, tables) if valid else (set(), set())
    decisions = []
    for entity in entities:
        if _key(entity.brand).rstrip("。;；，,") not in _REFERENCES:
            decisions.append(("", "preserve_nonempty_brand"))
            continue
        if not valid:
            decisions.append(("", "ambiguous_announcement_scope"))
            continue
        matches = {item for item in items if item.name == _name(entity.product_name) and item.name}
        direct = {item for item in matches if item.row}
        packages = {item.package for item in matches if item.package}
        linked = {award for award in awards if award.package in packages}
        if direct:
            suppliers = {item.supplier for item in direct} | {a.supplier for a in linked}
            chosen = next(iter(direct)).supplier if (
                len(direct) == 1 and len(suppliers) == 1 and len(packages) <= 1
            ) else ""
            decisions.append((chosen, "unique_same_item_row" if chosen else "conflicting_item_suppliers"))
            continue
        if not matches or not packages or {a.package for a in linked} != packages:
            decisions.append(("", "no_unique_item_award_link"))
            continue
        if any(not a.supplier for a in linked) or any(len({a for a in linked if a.package == p}) != 1 for p in packages):
            decisions.append(("", "conflicting_package_awards"))
            continue
        if len(packages) == 1:
            chosen = next(iter(linked)).supplier
        else:
            amount = _money(entity.total_price)
            selected = [a for a in linked if amount is not None and a.amount == amount]
            chosen = selected[0].supplier if len(selected) == 1 and all(a.amount is not None for a in linked) else ""
        decisions.append((chosen, "unique_package_award" if chosen else "ambiguous_package_amount"))
    return decisions


def fill_supplier_entities(entities: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    """Copy base entities after existing dedup; only brand can change, no dedup."""
    decisions = supplier_fill_decisions(entities, source_text=source_text)
    cleaned = []
    for original, (supplier, _) in zip(entities, decisions):
        entity = original.model_copy(deep=True)
        if supplier:
            entity.brand = supplier
        cleaned.append(entity)
    return cleaned
