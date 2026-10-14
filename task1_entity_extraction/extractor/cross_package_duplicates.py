"""Keep existing raw multiplicity only, never assign rows to packages.

Complete awarded body rows and serialized HTML must agree. Unsupported
identities, unparsed competing claims and model/source count conflicts defer
to the old seven-field dedup. Nothing here fills or constructs entity fields.
"""
from collections import Counter, defaultdict
import re

from extractor.item_total_evidence import _item_regions, _OWNER, _PACKAGE_LINE
from extractor.supplier_rules import _key, _scope, _usable
from models.schemas import ENTITY_FIELDS, ExtractedEntity


CROSS_PACKAGE_DUPLICATE_RULE_VERSION = "source-proven-existing-copies-and-master-awards-v12"
_NAMES = {"名称", "标的名称", "采购标的", "货物名称", "服务名称", "工程名称", "产品服务名称"}
_PACKAGES = {"标项名称", "采购包", "包号", "合同包", "标包", "采购包名称", "标段"}
_SERIAL = {"序号", "品目号"}
_FIELDS = {"品目名称": "category", "品牌": "brand", "货物品牌": "brand",
           "供应商名称": "brand", "中标供应商": "brand", "成交供应商": "brand",
           "规格型号": "spec_model", "货物型号": "spec_model", "型号": "spec_model",
           "数量": "quantity", "数量(单位)": "quantity",
           "单价": "unit_price", "单价(元)": "unit_price",
           "总价": "total_price", "总价(元)": "total_price", "金额(元)": "total_price"}
_NUMBER = r"(?:[0-9]+|[一二三四五六七八九十])"
_TOKEN = re.compile(rf"第({_NUMBER})包|(?:采购包|合同包|包)({_NUMBER})|第?({_NUMBER})标段|标包:?([A-Za-z0-9]+)")
_UNAWARDED = re.compile(r"候选报价|招标需求|采购需求|模板|范本")
_SCOPE_CLAIM = re.compile(r"采购包|合同包|标包|包号|标段|供应商")
_SAME_PACKAGE = re.compile(r"(?:同一|同一个|相同)(?:采购包|合同包|标包|包)")


def _entity_key(row: ExtractedEntity) -> tuple[str, ...]:
    return tuple(_key(getattr(row, field)) for field in ENTITY_FIELDS)


def _package_identity(label: str, header: str, name: str) -> str:
    # A bare number is allowed only under an explicit package-ID header,
    # never under sequence/serial columns. Unsupported Chinese numerals veto.
    label = _key(label)
    if header in {"采购包", "包号", "合同包", "标段"} and re.fullmatch(_NUMBER, label):
        label = ("第" + label + "标段") if header == "标段" else "采购包" + label
    matches = list(_TOKEN.finditer(label))
    if len(matches) != 1:
        return ""
    match = matches[0]
    remainder = label[:match.start()] + label[match.end():]
    if remainder.strip("():") not in {"", name}:
        return ""
    number = match[1] or match[2] or match[3]
    if not number:
        if not match[4].isdigit():
            return "标包:" + match[4].upper()
        number = match[4]
    number = {c: str(i) for i, c in enumerate("一二三四五六七八九十", 1)}.get(number, number)
    # 包 and 标段 aliases are conservatively treated as the same number.
    return "包:" + str(int(number))


def _complete_body_blocks(header, blocks, regions):
    """Match whole serialized tables at body line boundaries, not fixed widths.

    HTML text can split one cell at inline tags. Joining its text fragments
    is allowed only when the entire table still matches the independent cell
    matrix, including every intervening row and both block boundaries.
    """
    occurrences, seen = [], set()
    for ri, lines in enumerate(regions):
        if any(_PACKAGE_LINE.fullmatch(_key(line)) or _OWNER.fullmatch(_key(line)) for line in lines):
            continue  # External owner/package scopes are not resolved here.
        values = ["".join(_key(line).split("|")) for line in lines]
        offsets = [0]
        for value in values:
            offsets.append(offsets[-1] + len(value))
        boundaries = {offset: index for index, offset in enumerate(offsets)}
        flat = "".join(values)
        for rows in blocks:
            template = "".join(header) + "".join("".join(row) for row in rows)
            start = flat.find(template)
            while start >= 0:
                end = start + len(template)
                if start in boundaries and end in boundaries and (ri, start, end) not in seen:
                    indexes = range(boundaries[start], boundaries[end])
                    # A pipe row has explicit boundaries; do not flatten away
                    # a conflicting cell split to create apparent agreement.
                    if all("|" not in lines[i] or tuple(_key(lines[i]).split("|")) in {header, *rows}
                           for i in indexes):
                        seen.add((ri, start, end))
                        row_start = start + len("".join(header))
                        for cells in rows:
                            row_end = row_start + len("".join(cells))
                            if row_start in boundaries and row_end in boundaries:
                                consumed = {(ri, i) for i in range(boundaries[row_start], boundaries[row_end])}
                                block_consumed = {(ri, i) for i in indexes}
                                occurrences.append((cells, consumed, block_consumed))
                            row_start = row_end
                start = flat.find(template, start + 1)
    return occurrences


def proven_cross_package_duplicate_keys(
    entities: list[ExtractedEntity], *, source_text: str,
) -> set[tuple[str, ...]]:
    """Prove interchangeable source-group count, not each model row's owner."""
    groups = defaultdict(list)
    for row in entities:
        groups[_entity_key(row)].append(row)
    by_name = Counter(key[0] for key in groups)
    candidates = {key: rows for key, rows in groups.items()
                  if len(rows) > 1 and by_name[key[0]] == 1 and _usable(key[0])
                  and len({tuple(getattr(row, field) for field in ENTITY_FIELDS) for row in rows}) == 1}
    if not candidates:
        return set()
    body, tables, valid = _scope(source_text)
    regions = _item_regions(body) if valid else []
    if (not regions or _SAME_PACKAGE.search(_key(body))
            or any(_UNAWARDED.search(_key(line)) for lines in regions for line in lines)):
        return set()
    proved = set()
    for key, models in candidates.items():
        name = key[0]
        witnesses, headers, blocks, bad = set(), set(), set(), False
        for table in tables:
            if not table or not any(name in _key(cell) for row in table[1:] for cell in row):
                continue
            if table[0] and _key(table[0][0]).rstrip(":") == "公告信息":
                # _scope has already validated the project against the body.
                # Its literal title may legitimately contain an item name;
                # unknown metadata declarations still veto that item group.
                if any(len(row) != 2 or _key(row[0]).rstrip(":") not in {"采购项目名称", "公告标题"}
                       for row in table[1:] if any(name in _key(cell) for cell in row)):
                    bad = True
                    break
                continue
            header = tuple(_key(cell) for cell in table[0])
            names = [i for i, cell in enumerate(header) if cell in _NAMES]
            packages = [i for i, cell in enumerate(header) if cell in _PACKAGES]
            if (len(names) != 1 or len(packages) != 1 or len(set(header)) != len(header)
                    or any(cell not in _NAMES | _PACKAGES | _SERIAL | set(_FIELDS) for cell in header)
                    or not any(cell in _FIELDS for cell in header)):
                bad = True
                break
            headers.add(header)
            block = tuple(tuple(_key(cell) for cell in raw) for raw in table[1:])
            if any(len(cells) != len(header) for cells in block):
                bad = True
                break
            blocks.add(block)
            for raw in table[1:]:
                cells = tuple(_key(cell) for cell in raw)
                if name not in "".join(cells):
                    continue
                if len(cells) != len(header) or not all(cells) or cells[names[0]] != name:
                    bad = True
                    break
                witnesses.add((header, cells))
        if bad or not witnesses or len(headers) != 1:
            continue
        header = next(iter(headers))
        ni = next(i for i, cell in enumerate(header) if cell in _NAMES)
        pi = next(i for i, cell in enumerate(header) if cell in _PACKAGES)
        body_rows, consumed, block_consumed = [], set(), set()
        for cells, indexes, block_indexes in _complete_body_blocks(header, blocks, regions):
            block_consumed.update(block_indexes)
            if cells[ni] != name:
                continue
            consumed.update(indexes)
            if (header, cells) not in witnesses or not all(cells):
                bad = True
            package = _package_identity(cells[pi], header[pi], name)
            if not package:
                bad = True
            attributes = tuple(cells[i] for i, cell in enumerate(header) if cell not in _SERIAL | _PACKAGES)
            body_rows.append((package, attributes, cells))
        if (bad or len(body_rows) != len(models)
                or len({package for package, _, _ in body_rows}) != len(models)
                or len({attributes for _, attributes, _ in body_rows}) != 1
                or {(header, cells) for _, _, cells in body_rows} != witnesses):
            continue
        # A malformed/unparsed same-name declaration cannot disappear simply
        # because two other rows happen to match the model's count.
        if any(name in _key(line) and (ri, index) not in consumed
               for ri, lines in enumerate(regions) for index, line in enumerate(lines)):
            continue
        # Scope statements outside the complete parsed table cannot silently
        # supply or contradict ownership, even when they omit this item name.
        if any((_TOKEN.search(_key(line)) or _SCOPE_CLAIM.search(_key(line))
                or _OWNER.fullmatch(_key(line))) and (ri, index) not in block_consumed
               for ri, lines in enumerate(regions) for index, line in enumerate(lines)):
            continue
        source_cells = body_rows[0][2]
        if any(getattr(models[0], field) and _key(getattr(models[0], field)) != source_cells[i]
               for i, cell in enumerate(header) if (field := _FIELDS.get(cell))):
            continue
        # A populated model field with no corresponding source column is not
        # proof of interchangeable owners/attributes.
        if any(getattr(models[0], field) and field not in _fields_for_header(header)
               for field in ENTITY_FIELDS[1:]):
            continue
        proved.add(key)
    return proved


def _fields_for_header(header):
    return {_FIELDS[cell] for cell in header if cell in _FIELDS}


def preserve_proven_duplicate_copies(
    cleaned: list[ExtractedEntity], originals: list[ExtractedEntity], *, source_text: str,
) -> list[ExtractedEntity]:
    """Restore discarded raw copies only when the final representative is unchanged."""
    proved = proven_cross_package_duplicate_keys(originals, source_text=source_text)
    original_groups = defaultdict(list)
    for row in originals:
        original_groups[_entity_key(row)].append(row)
    counts = Counter(_entity_key(row) for row in cleaned)
    eligible = {key for key in proved if counts[key] == 1
                and next(row for row in cleaned if _entity_key(row) == key).model_dump()
                == original_groups[key][0].model_dump()}
    if not eligible:
        return cleaned
    # Restore interleaved copies only if every surviving old row is an exact,
    # uniquely located anchor in the original sequence. Never resurrect other
    # deleted rows, or infer an anchor after an old cleanup changed its fields.
    first_positions = {}
    for index, row in enumerate(originals):
        first_positions.setdefault(_entity_key(row), index)
    anchors = [first_positions.get(_entity_key(row), -1) for row in cleaned]
    if (all(counts[_entity_key(row)] == 1 and position >= 0
            and row.model_dump() == originals[position].model_dump()
            for row, position in zip(cleaned, anchors))
            and anchors == sorted(set(anchors))):
        survivors = {_entity_key(row): row for row in cleaned}
        result, emitted = [], set()
        for row in originals:
            key = _entity_key(row)
            if key not in survivors:
                continue
            if key not in emitted:
                result.append(survivors[key])
            elif key in eligible:
                result.append(row.model_copy(deep=True))
            emitted.add(key)
        return result
    # With unlocatable anchors, adjacent original copies are still safe;
    # interleaved groups defer to the unchanged old-cleanup result.
    positions = defaultdict(list)
    for index, row in enumerate(originals):
        positions[_entity_key(row)].append(index)
    eligible = {key for key in eligible if positions[key][-1] - positions[key][0] + 1 == len(positions[key])}
    result = []
    for row in cleaned:
        result.append(row)
        key = _entity_key(row)
        originals_for_key = original_groups.get(key, [])
        if key in eligible:
            result.extend(copy.model_copy(deep=True) for copy in originals_for_key[1:])
    return result


def deduplicate_with_source_evidence(
    entities: list[ExtractedEntity], *, source_text: str = "",
) -> list[ExtractedEntity]:
    """DB revalidates proof; missing/invalid source keeps stable old dedup."""
    from extractor.official_qa_money import master_award_rows
    master = master_award_rows(source_text)
    if master is not None and Counter(_entity_key(r) for r in entities) == Counter(_entity_key(r) for r in master):
        return list(entities)  # both independent source projections proved these occurrences
    proved = proven_cross_package_duplicate_keys(entities, source_text=source_text)
    result, seen = [], set()
    for row in entities:
        key = _entity_key(row)
        if key not in seen or key in proved:
            result.append(row)
        seen.add(key)
    return result
