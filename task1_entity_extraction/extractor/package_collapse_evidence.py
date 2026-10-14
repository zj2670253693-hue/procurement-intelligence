"""Source-proven package collapse, never zip unrelated labels and prices."""
from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata

from extractor.awarded_rate_tiers import _award_sections, _evidence, _package_ids
from extractor.item_total_evidence import _aligned, _item_regions, _PACKAGE_LINE, _END, _TABLE_REFERENCE
from extractor.supplier_rules import _fields, _key, _name, _scope, _supplier, _SECTION
from extractor.total_award_rules import _AWARD_LABEL, _literal_amount
from models.schemas import ExtractedEntity


_SUPPLIER_LABEL = r"(?:供应商名称|中标成交供应商名称|中标供应商名称|成交供应商名称)"
_NAMES = {"名称", "货物名称", "服务名称", "工程名称", "产品名称", "商品名称", "采购标的", "标的名称"}
_SUPPLIERS = {"供应商", "供应商名称", "中标供应商", "成交供应商"}
_PACKAGES = {"包号", "采购包", "合同包", "标包"}
_OWNER_DECLARATION = re.compile(r"(?:中标人|成交人|(?:中标(?:成交)?|成交)?供应商(?:名称)?|供货商)(?:[:：]|为|$)")
_REFERENCES = {"", "-", "/", "详见附件", "见附件", "详见采购文件", "详见招标文件", "详见磋商文件", "详见中标文件"}
_NEGATIVE = re.compile(r"预算|最高限价|候选人|未中标|未成交|模板|范本|投标报价|评审报价")


@dataclass(frozen=True)
class _Award:
    package: str
    label: str
    supplier: str
    amount: str


@dataclass(frozen=True)
class _Item:
    package: str
    name: str
    supplier: str


def _single_package(value: str) -> str:
    ids = _package_ids(value)
    return next(iter(ids)) if len(ids) == 1 else ""


def _display_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    for reference in sorted(_REFERENCES - {"", "-", "/"}, key=len, reverse=True):
        if value.endswith(reference):
            value = value[:-len(reference)].rstrip("。;；，, ")
            break
    return value


def _number(value: str) -> Decimal | None:
    value = _key(value).replace(",", "")
    match = re.fullmatch(r"[￥¥]?(\d+(?:\.\d+)?)(?:\((亿元|万元|元)\)|(亿元|万元|元))(?:\(人民币\))?", value)
    if not match:
        return None
    return Decimal(match[1]) * {"亿元": Decimal(100000000), "万元": Decimal(10000), "元": Decimal(1)}[match[2] or match[3]]


def _raw_item_regions(body: str) -> list[list[str]]:
    """Keep rejected primary evidence distinct from an absent item section."""
    regions, lines, active = [], [], False
    for raw in body.splitlines():
        heading = _SECTION.fullmatch(_key(raw))
        title = heading[1].rstrip(":") if heading else ""
        boundary = (title == "主要标的信息" or _END.fullmatch(title)
                    or re.fullmatch(r"(?:中标(?:\(成交\))?|成交)(?:信息|结果)|采购结果", title))
        if boundary:
            if active:
                regions.append(lines)
            active, lines = title == "主要标的信息", []
        elif active and raw.strip():
            lines.append(raw.strip())
    if active:
        regions.append(lines)
    return regions


def _awards(body: str, tables: list) -> list[_Award]:
    awards = []
    sections = _award_sections(body)
    if not sections:
        # Minimal legacy inputs only have a proof when the existing explicit
        # awarded-tier validator already establishes project/package/owner.
        if _evidence(body):
            sections = [[v.strip() for v in body.splitlines() if v.strip()]]
    for lines in sections:
        if (_NEGATIVE.search("\n".join(lines))
                or any(len(_package_ids(line)) > 1 for line in lines)):
            return []
        # Unknown owner declarations are negative evidence, not another
        # source format silently dropped while certifying the known prefix.
        for line in lines:
            normalized = _key(line)
            if ("|" not in normalized and _OWNER_DECLARATION.search(normalized)
                    and not re.match(_SUPPLIER_LABEL + ":", normalized)):
                return []
        for table in tables:
            for row in table:
                for cell in row:
                    normalized = _key(cell)
                    if _OWNER_DECLARATION.search(normalized) and normalized not in _SUPPLIERS:
                        return []
        package, label, current = "", "", []

        def flush():
            if not current:
                return
            suppliers = _fields(current, _SUPPLIER_LABEL)
            prices = _fields(current, _AWARD_LABEL.pattern)
            if suppliers:
                supplier = _supplier(suppliers[0]) if len(suppliers) == 1 else ""
                amount = _literal_amount(prices[0]) if len(prices) == 1 else ""
                awards.append(_Award(package, label, supplier, amount))

        if len(sections) == 1 and not any(_PACKAGE_LINE.fullmatch(_key(line)) for line in lines):
            # A unique package in an explicitly declared project is identity,
            # not a numeric row index. Do not mine addresses or attachment text.
            projects = _fields(lines, r"(?:采购)?项目名称")
            if len(projects) == 1 and _single_package(projects[0]):
                package, label = _single_package(projects[0]), _display_name(projects[0])
        for line in lines:
            normalized = _key(line)
            if _PACKAGE_LINE.fullmatch(normalized):
                flush()
                package, label, current = _single_package(normalized), _display_name(line).rstrip(":"), []
            elif re.match(_SUPPLIER_LABEL + ":", normalized):
                flush()
                current = [line]
            elif current:
                current.append(line)
        flush()
        # Complete award matrices are a second literal witness, not another
        # owner selected by physical position or by a project total.
        for table in tables:
            if not table:
                continue
            header = tuple(_key(cell) for cell in table[0])
            si = [i for i, cell in enumerate(header) if cell in _SUPPLIERS]
            ai = [i for i, cell in enumerate(header) if _AWARD_LABEL.fullmatch(cell)]
            pi = [i for i, cell in enumerate(header) if cell in _PACKAGES]
            if len(si) != 1 or len(ai) != 1:
                continue
            if len(pi) > 1:
                return []
            if any(len(row) != len(header) for row in table[1:]):
                return []
            witnesses = {tuple(_key(cell) for cell in row) for row in table[1:]}
            body_rows = set()
            for _, (row_package, _), cells in _aligned(header, lines):
                body_rows.add(cells)
                if cells not in witnesses:
                    return []
                if pi:
                    prefix = "标包:" if header[pi[0]] == "标包" else "采购包"
                    literal = _single_package(cells[pi[0]]) or _single_package(prefix + cells[pi[0]])
                    if not literal or (row_package and literal != row_package):
                        return []
                    row_package = literal
                match = _AWARD_LABEL.fullmatch(header[ai[0]])
                amount = _literal_amount(cells[ai[0]], match[1] or "")
                awards.append(_Award(row_package, "", _supplier(cells[si[0]]), amount))
            if body_rows != witnesses:
                return []
    # Repeated body/table witnesses of the same declaration are not separate
    # awards; preserve conflicting prices/owners and unsupported declarations.
    unique = {}
    for award in awards:
        key = (award.package, award.supplier, _number(award.amount))
        if key not in unique or (award.label and not unique[key].label):
            unique[key] = award
    return list(unique.values())


def _items(body: str, tables: list) -> tuple[list[_Item], bool]:
    regions = _item_regions(body)
    items, seen, recognized = [], set(), False
    covered = {i: set() for i in range(len(regions))}
    headers = {}
    for table in tables:
        if not table:
            continue
        header = tuple(_key(cell) for cell in table[0])
        ni = [i for i, cell in enumerate(header) if cell in _NAMES]
        si = [i for i, cell in enumerate(header) if cell in _SUPPLIERS]
        if len(ni) == len(si) == 1:
            if (len(table) < 2 or len(set(header)) != len(header)
                    or any(len(row) != len(header) for row in table[1:])):
                return [], False
            headers.setdefault(header, set()).update(tuple(_key(c) for c in row) for row in table[1:])
    for header, witnesses in headers.items():
        ni = next(i for i, c in enumerate(header) if c in _NAMES)
        si = next(i for i, c in enumerate(header) if c in _SUPPLIERS)
        pi = [i for i, c in enumerate(header) if c in _PACKAGES]
        if len(pi) > 1:
            return [], False
        body_rows = set()
        for region_index, lines in enumerate(regions):
            for position, (context, owner), cells in _aligned(header, lines):
                recognized = True
                body_rows.add(cells)
                identity = (region_index, position, header)
                if identity in seen:
                    continue
                seen.add(identity)
                covered[region_index].update(range(position, position + (1 if "|" in lines[position] else len(header))))
                name, supplier = _display_name(cells[ni]), _supplier(cells[si])
                ids = _package_ids(name) | ({context} if context else set())
                if pi:
                    prefix = "标包:" if header[pi[0]] == "标包" else "采购包"
                    literal = _single_package(cells[pi[0]]) or _single_package(prefix + cells[pi[0]])
                    if not literal:
                        return [], False
                    ids.add(literal)
                if (cells not in witnesses or len(ids) != 1 or not _name(name) or not supplier
                        or (owner and owner != _key(supplier))
                        or any(c not in _REFERENCES for i, c in enumerate(cells)
                               if i not in {ni, si} and header[i] not in {"序号", "品目号", *_PACKAGES})):
                    return [], False
                items.append(_Item(next(iter(ids)), name, supplier))
        if body_rows != witnesses:
            return [], False
    if headers:
        for region_index, lines in enumerate(regions):
            for position, line in enumerate(lines):
                normalized = _key(line)
                if position in covered[region_index] or tuple(normalized.split("|")) in headers:
                    continue
                if _PACKAGE_LINE.fullmatch(normalized):
                    end = next((i for i in range(position + 1, len(lines))
                                if _PACKAGE_LINE.fullmatch(_key(lines[i]))), len(lines))
                    if not any(i in covered[region_index] for i in range(position + 1, end)):
                        return [], False
                elif ("|" in normalized or _package_ids(normalized)
                      or _OWNER_DECLARATION.search(normalized)
                      or re.match(r"(?:名称|货物名称|服务名称|工程名称|标的名称):", normalized)):
                    return [], False
    if not headers:
        # Exact headerless legacy pipe rows, not arbitrary package mentions.
        for lines in regions:
            for line in lines:
                cells = [_key(c) for c in line.split("|")]
                if len(cells) >= 4 and re.fullmatch(r"[0-9]+", cells[0]):
                    recognized = True
                    name, supplier = _display_name(cells[2]), _supplier(cells[1])
                    package = _single_package(name)
                    if not package or not supplier or any(c not in _REFERENCES for c in cells[3:]):
                        return [], False
                    items.append(_Item(package, name, supplier))
                elif fields := _fields([line], r"(?:名称|货物名称|服务名称|工程名称|标的名称)"):
                    for name in fields:
                        if _single_package(name):
                            items.append(_Item(_single_package(name), _display_name(name), ""))
    # A repeated raw item name, including a partial row not yielded above,
    # cannot become unique merely because its positive prefix parsed.
    raw_counts = {}
    for item in items:
        raw_counts[item.name] = sum(_name(c) == _name(item.name) for lines in regions for line in lines for c in line.split("|"))
    if any(raw_counts[item.name] > 1 for item in items):
        return [], False
    return items, bool(regions) and (recognized or bool(items))


def collapse_proven_packages(entities: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    body, tables, valid = _scope(source_text)
    if not valid and (tables or re.search(r"(?m)^\s*---", source_text)):
        return entities
    raw_regions = _raw_item_regions(body)
    if (any(_NEGATIVE.search("\n".join(lines)) for lines in raw_regions)
            or any(_TABLE_REFERENCE.search(_key(line)) and _NEGATIVE.search(_key(line))
                   for line in body.splitlines())):
        return entities
    awards = _awards(body, tables)
    if not awards or any(not a.supplier or _number(a.amount) is None for a in awards):
        return entities
    items, consistent = _items(body, tables)
    if not consistent:
        # Explicit awarded package blocks can stand on their own; fee/score/
        # attachment labels and unowned item rows cannot supply this identity.
        primary_claims = any("|" in line or re.match(r"(?:名称|货物名称|服务名称|标的名称)[:：]", line)
                             for region in raw_regions for line in region)
        if items or primary_claims or any(not a.package or not a.label for a in awards):
            return entities
        items = [_Item(a.package, a.label, a.supplier) for a in awards]
    matched, used = [], set()
    for item in items:
        matches = [i for i, award in enumerate(awards)
                   if (not award.package or award.package == item.package)
                   and (not item.supplier or _key(award.supplier) == _key(item.supplier))]
        if len(matches) != 1:
            return entities
        index = matches[0]
        if index in used or any(i.package == item.package for i, _ in matched):
            return entities
        matched.append((item, awards[index]))
        used.add(index)
    if used != set(range(len(awards))):
        return entities
    return [ExtractedEntity(product_name=item.name, brand=award.supplier,
                            total_price=unicodedata.normalize("NFKC", award.amount))
            for item, award in matched]
