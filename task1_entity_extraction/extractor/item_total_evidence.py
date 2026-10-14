"""Restore only existing totals proved by uniquely aligned awarded HTML rows.

Never construct a price, use a price to select an owner, or change row count.
Unknown layouts and conflicting witnesses fail closed to the old summary.
"""
from collections import Counter
from dataclasses import dataclass
import re

from extractor.awarded_rate_tiers import _package_ids
from extractor.supplier_rules import _AWARD_HEADER, _SECTION, _key, _money, _scope, _supplier, _usable
from models.schemas import ExtractedEntity


ITEM_TOTAL_RULE_VERSION = "existing-service-table-literal-lot-v3"


_NAMES = {"名称", "货物名称", "服务名称", "工程名称", "产品名称", "采购标的", "标的名称", "产品服务名称"}
_BRANDS = {"品牌", "货物品牌", "供应商名称", "中标供应商", "成交供应商"}
_SUPPLIERS = {"供应商名称", "中标供应商", "成交供应商"}
_SPECS = {"规格型号", "货物型号", "型号"}
_PACKAGES = {"包号", "采购包", "合同包", "标包"}
_TOTAL = re.compile(r"(?:总价|金额|总金额|合价)(?:\((?:亿元|万元|元)\))?$")
_NEGATIVE = re.compile(r"预算|最高限价|限价|模板|范本|候选人|未中标|未成交|投标报价|评审报价|报价排名")
_PACKAGE_LINE = re.compile(r"(?:第[0-9一二三四五六七八九十]+包|(?:采购包|合同包|包)[0-9一二三四五六七八九十]+|标包:?[A-Za-z0-9]+)(?:\(.+\))?:?$")
_OWNER = re.compile(r"(?:货物|服务|工程)类\((.+)\)$")
_END = re.compile(r"(?:评审专家.*|代理服务.*|公告期限|其他补充事宜|凡对本次公告.*|联系方式.*)$")
_TABLE_REFERENCE = re.compile(r"主要标的信息|(?:以上|上述|本|此|该)表|(?:以上|上述)(?:金额|价格|报价|内容)")
_LOT_SUFFIX = re.compile(r"([^()]+)\(([0-9一二三四五六七八九十]+标段)\)$")
_NOT_AWARD = re.compile(r"(?:并非|不是|不属于|非)(?:最终|本项目)?(?:中标(?:\(成交\))?|成交)(?:金额|价|结果)")


@dataclass(frozen=True)
class _Row:
    name: str
    package: str
    brands: tuple[str, ...]
    spec: str
    value: str
    header: str
    witnessed: bool


def _item_regions(body: str, *, service_layout: bool = False) -> list[list[str]]:
    regions, award, role, lines = [], False, "", []
    # An explicit disclaimer referring to this table remains authoritative
    # after a normal section boundary. Ordinary competitor/fee tables in
    # supplementary sections are not themselves such a disclaimer.
    if any(_TABLE_REFERENCE.search(_key(line)) and (
               _NEGATIVE.search(_key(line)) or (service_layout and _NOT_AWARD.search(_key(line))))
           for line in body.splitlines()):
        return []
    for raw in body.splitlines():
        line = _key(raw)
        heading = _SECTION.fullmatch(line)
        title = heading[1].rstrip(":") if heading else ""
        is_award = bool(re.fullmatch(r"(?:中标(?:\(成交\))?|成交)(?:信息|结果)|采购结果", title))
        if not role and not award and re.search(r"候选人(?:公示|公告|名单)", line):
            return []
        if (is_award or title == "主要标的信息" or _END.fullmatch(title)
                or (service_layout and title == "评审得分排名")):
            if role == "award":
                award = bool(lines) and not _NEGATIVE.search("\n".join(lines)) and any("供应商" in _key(v) for v in lines)
            elif role == "item" and award and not _NEGATIVE.search("\n".join(lines)):
                regions.append(lines)
            role = "award" if is_award else ("item" if title == "主要标的信息" else "")
            lines = []
        elif role and line:
            # Unknown/numbered qualifiers remain in the region, never discard
            # a negative tail and then certify its apparently positive prefix.
            lines.append(raw.strip())
    if role == "item" and award and not _NEGATIVE.search("\n".join(lines)):
        regions.append(lines)
    return regions


def _one_package(value: str) -> str:
    ids = _package_ids(value)
    return next(iter(ids)) if len(ids) == 1 else ""


def _aligned(header: tuple[str, ...], lines: list[str]):
    """Yield complete pipe/vertical rows with occurrence and package context."""
    size = len(header)
    values = [_key(line) for line in lines]
    contexts, package, owner = [], "", ""
    for value in values:
        if _PACKAGE_LINE.fullmatch(value):
            package, owner = _one_package(value), ""
        elif match := _OWNER.fullmatch(value):
            owner = match[1]
        contexts.append((package, owner))
    for index, value in enumerate(values):
        if tuple(value.split("|")) == header:
            for position in range(index + 1, len(values)):
                cells = tuple(values[position].split("|"))
                if "|" not in values[position] or cells == header or len(cells) != size:
                    break
                yield position, contexts[position], cells
        elif tuple(values[index:index + size]) == header:
            for position in range(index + size, len(values) - size + 1, size):
                cells = tuple(values[position:position + size])
                if (cells == header or any(not c or "|" in c or _PACKAGE_LINE.fullmatch(c)
                                          or _OWNER.fullmatch(c) for c in cells)
                        or len(set(contexts[position:position + size])) != 1):
                    break
                yield position, contexts[position], cells


def _catalog(source: str) -> list[_Row]:
    body, tables, valid = _scope(source)
    if not valid:
        return []
    regions = _item_regions(body)
    body_cells = [_key(cell) for lines in regions for line in lines for cell in line.split("|")]
    body_name_counts = Counter(body_cells)
    labeled_claims = [cell for cell in body_cells if ":" in cell]
    catalog, seen = [], set()
    witnesses_by_header, invalid_headers = {}, set()
    for table in tables:
        if not table:
            continue
        header = tuple(_key(c) for c in table[0])
        cells = [tuple(_key(c) for c in row) for row in table[1:]]
        if (len(table) < 2 or any(len(row) != len(header) for row in table[1:])
                or len(cells) != len(set(cells))):
            invalid_headers.add(header)
        else:
            witnesses_by_header.setdefault(header, set()).update(
                tuple(_key(c) for c in row) for row in table[1:]
            )
    for table in tables:
        if not table:
            continue
        header = tuple(_key(c) for c in table[0])
        names = [i for i, c in enumerate(header) if c in _NAMES]
        categories = [i for i, c in enumerate(header) if c == "品目名称"]
        totals = [i for i, c in enumerate(header) if _TOTAL.fullmatch(c)]
        brands = [i for i, c in enumerate(header) if c in _BRANDS]
        suppliers = [i for i, c in enumerate(header) if c in _SUPPLIERS]
        specs = [i for i, c in enumerate(header) if c in _SPECS]
        packages = [i for i, c in enumerate(header) if c in _PACKAGES]
        if (len(names) != 1 or len(totals) != 1 or len(specs) > 1 or len(packages) > 1
                or len(set(header)) != len(header) or any(_NEGATIVE.search(c) for c in header)
                or header in invalid_headers):
            continue
        witnesses = witnesses_by_header.get(header, set())
        body_rows = set()
        for region_index, lines in enumerate(regions):
            for position, (context, owner), cells in _aligned(header, lines):
                body_rows.add(cells)
                identity = (region_index, position, header)
                if identity in seen:
                    continue  # Parent/child serialization repeats one body row.
                seen.add(identity)
                name = cells[names[0]]
                if not _usable(name):
                    continue
                literal = ""
                if packages:
                    pi = packages[0]
                    prefix = "标包:" if header[pi] == "标包" else "采购包"
                    literal = _one_package(cells[pi]) or _one_package(prefix + cells[pi])
                name_ids = _package_ids(name)
                ids = {p for p in (context, literal) if p} | name_ids
                conflict = (len(ids) > 1 or (bool(packages) and not literal) or len(name_ids) > 1
                            or (bool(owner) and bool(suppliers)
                                and any(cells[i] != owner for i in suppliers)))
                owners = tuple(cells[i] for i in brands if _usable(cells[i]))
                if not brands and owner:
                    owners = (owner,)
                # Discount only this complete, independently witnessed row's
                # explicit category echo. Other mentions stay veto evidence.
                category_echo = (len(categories) == 1 and cells[categories[0]] == name
                                 and all(cells) and cells in witnesses and not conflict
                                 and not any(name in claim for claim in labeled_claims))
                catalog.append(_Row(name, next(iter(ids)) if len(ids) == 1 else "",
                                    owners, cells[specs[0]] if specs else "",
                                    cells[totals[0]], header[totals[0]],
                                    cells in witnesses and not conflict
                                    and body_name_counts[name] - int(category_echo) == 1))
        # A detached serialized competitor is negative evidence too. It may
        # not vanish simply because another row has a positive body witness.
        for cells in witnesses - body_rows:
            name = cells[names[0]]
            if _usable(name):
                catalog.append(_Row(name, "", (), "", cells[totals[0]], header[totals[0]], False))
    return catalog


def _literal_lot(value: str) -> tuple[str, str]:
    """Only one terminal literal lot label; never infer from row or price."""
    text = _key(value)
    match = _LOT_SUFFIX.fullmatch(text)
    if not match or text.count("标段") != 1 or _package_ids(text):
        return "", ""
    return match[1], match[2]


def _service_awards(body: str, tables: list[list[list[str]]]):
    """One complete awarded matrix, independently identical to its body."""
    regions, current, collecting = [], [], False
    for raw in body.splitlines():
        line = _key(raw)
        heading = _SECTION.fullmatch(line)
        title = heading[1].rstrip(":") if heading else ""
        if re.fullmatch(r"(?:中标(?:\(成交\))?|成交)(?:信息|结果)|采购结果", title):
            if collecting:
                regions.append(current)
            current, collecting = [], True
        elif title == "主要标的信息" and collecting:
            regions.append(current)
            current, collecting = [], False
        elif collecting and line:
            current.append(line)
    if collecting:
        regions.append(current)
    if len(regions) != 1 or any(_NEGATIVE.search(v) for v in regions[0]):
        return None
    candidates = []
    for table in tables:
        if not table:
            continue
        header = tuple(_key(v) for v in table[0])
        suppliers = [i for i, v in enumerate(header) if v in _SUPPLIERS]
        amounts = [i for i, v in enumerate(header) if _AWARD_HEADER.fullmatch(v)]
        if not suppliers or not amounts:
            continue
        if (len(suppliers) != 1 or len(amounts) != 1 or len(set(header)) != len(header)
                or any(_NEGATIVE.search(v) for v in header) or len(table) < 2):
            return None
        rows = [tuple(_key(v) for v in row) for row in table[1:]]
        if any(len(row) != len(header) or not all(row) for row in rows):
            return None  # Missing competing declarations cannot disappear.
        if regions[0] != [*header, *(v for row in rows for v in row)]:
            return None
        candidates.append((header, rows, suppliers[0], amounts[0]))
    if len(candidates) != 1:
        return None
    header, rows, supplier_index, amount_index = candidates[0]
    awards = {}
    for row in rows:
        company, lot = _literal_lot(row[supplier_index])
        if not lot or not _supplier(company) or lot in awards:
            return None
        # Identity is fixed before reading its price; a missing price vetoes.
        amount = _money(row[amount_index], header[amount_index])
        if amount is None:
            return None
        awards[lot] = (company, amount)
    return awards


def _service_catalog(source: str) -> list[_Row]:
    """Strict service-title layout, allowing only an explicitly empty serial.

    This separate branch leaves all previously supported layouts unchanged.
    Full source rows are not repaired or shifted: their exact nonempty-cell
    projection must follow the complete header in the main-items body.
    """
    body, tables, valid = _scope(source)
    if not valid:
        return []
    regions = _item_regions(body, service_layout=True)
    awards = _service_awards(body, tables)
    if (len(regions) != 1 or not awards
            or sum(bool(t) and tuple(_key(v) for v in t[0]) == ("服务类",) for t in tables) != 1):
        return []
    body_cells = [_key(v) for region in regions for line in region for v in line.split("|")]
    counts = Counter(body_cells)
    # All serialized layouts are competitor evidence, including unknown titles
    # and partial rows. Do not mine them as positive proof or discard them.
    source_counts = Counter(_key(v) for table in tables for row in table for v in row)
    claims = [v for v in body_cells if ":" in v]
    catalog = []
    for table in tables:
        if not table or tuple(_key(v) for v in table[0]) != ("服务类",):
            continue
        if len(table) < 3:
            return []
        header = tuple(_key(v) for v in table[1])
        names = [i for i, v in enumerate(header) if v in _NAMES]
        totals = [i for i, v in enumerate(header) if _TOTAL.fullmatch(v)]
        if (header.count("序号") != 1 or len(names) != 1 or len(totals) != 1
                or len(set(header)) != len(header) or any(_NEGATIVE.search(v) for v in header)
                or any(v in _BRANDS or v in _SPECS or v in _PACKAGES for v in header)):
            return []  # No generalization to alternate ownership/spec layouts.
        serial = header.index("序号")
        rows = [tuple(_key(v) for v in row) for row in table[2:]]
        if any(len(row) != len(header) or row[serial] != ""
               or not all(v for i, v in enumerate(row) if i != serial) for row in rows):
            return []
        projection = [*header, *(v for row in rows for i, v in enumerate(row) if i != serial)]
        # Unknown declarations/package context cannot be dropped from proof.
        complete_body = [_key(v) for v in regions[0]] == ["服务类", *projection]
        lot_counts = Counter(_literal_lot(row[names[0]])[1] for row in rows)
        for row in rows:
            name = row[names[0]]
            _, lot = _literal_lot(name)
            owner = awards.get(lot)
            # The same lot fixes the owner first, never amount or row order.
            amount = _money(row[totals[0]], header[totals[0]])
            proved = (complete_body and _usable(name) and counts[name] == source_counts[name] == 1
                      and lot_counts[lot] == 1
                      and not any(name in claim for claim in claims)
                      and owner is not None and amount is not None and amount == owner[1])
            catalog.append(_Row(name, lot, (owner[0],) if owner else (), "",
                                row[totals[0]], header[totals[0]], proved))
    return catalog


def proven_item_total_indexes(entities: list[ExtractedEntity], *, source_text: str) -> set[int]:
    """Identity must be unique before comparing the existing model amount."""
    catalog = _catalog(source_text) + _service_catalog(source_text)
    proved = set()
    for index, row in enumerate(entities):
        if not row.total_price or not _usable(row.product_name):
            continue
        matches = [item for item in catalog if item.name == _key(row.product_name)]
        # Never use price to choose between repeated items or packages.
        if len(matches) != 1:
            continue
        item = matches[0]
        if item.package.endswith("标段") and (not _usable(row.brand) or _usable(row.spec_model)):
            continue  # This new layout proves neither a missing owner nor a model-only specification.
        model_packages = _package_ids(row.product_name)
        if (not item.witnessed or (model_packages and model_packages != {item.package})
                or (_usable(row.brand) and _key(row.brand) not in item.brands)
                or (_usable(row.spec_model) and _usable(item.spec) and _key(row.spec_model) != item.spec)):
            continue
        source_amount = _money(item.value, item.header)
        # Currency must come from the source, never from a guessed model unit.
        model_amount = _money(row.total_price) or _money(row.total_price, item.header)
        if source_amount is not None and model_amount == source_amount:
            proved.add(index)
    return proved


def restore_proven_item_totals(cleaned: list[ExtractedEntity], originals: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    """Final price-only pass: no changed identity, new row, dedup or reordering."""
    counts = Counter(_key(row.product_name) for row in originals)
    final_counts = Counter(_key(row.product_name) for row in cleaned)
    proved = proven_item_total_indexes(originals, source_text=source_text)
    eligible = {_key(originals[i].product_name): originals[i] for i in proved
                if counts[_key(originals[i].product_name)] == final_counts[_key(originals[i].product_name)] == 1}
    result = []
    for row in cleaned:
        original = eligible.get(_key(row.product_name))
        copy = row.model_copy(deep=True)
        if (original is not None and _key(row.brand) == _key(original.brand)
                and (not _usable(row.spec_model) or _key(row.spec_model) == _key(original.spec_model))):
            copy.total_price = original.total_price
        result.append(copy)
    return result
