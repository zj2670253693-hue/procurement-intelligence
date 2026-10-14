"""Restore only literal awarded consortium suffixes for exact existing leads.

Same-package, complete BODY/HTML item and supplier rows must corroborate.
No company aliases, price/order ownership, field fill or attachment evidence.
"""
from collections import Counter, defaultdict
import re

from extractor.item_total_evidence import _aligned, _OWNER, _PACKAGE_LINE, _TABLE_REFERENCE
from extractor.supplier_rules import _key, _scope, _SECTION, _usable
from models.schemas import ENTITY_FIELDS, ExtractedEntity


CONSORTIUM_SUPPLIER_RULE_VERSION = "source-proven-consortium-suffix-v1"
_NAMES = {"名称", "货物名称", "服务名称", "工程名称", "产品名称", "采购标的", "标的名称", "产品服务名称"}
_SUPPLIERS = {"供应商名称", "中标供应商", "成交供应商"}
_CATEGORY = {"品目名称"}
_ITEM_COLUMNS = _NAMES | _CATEGORY | {"品目号", "品目编号", "序号", "服务范围", "服务要求", "服务时间", "服务标准", "金额(元)"}
_AWARD_AMOUNT = re.compile(r"(?:中标\(成交\)金额|中标/成交金额|中标金额|成交金额)(?:\((?:亿元|万元|元)\))?$")
_AWARD_COLUMNS = _SUPPLIERS | {"供应商地址", "评审总得分", "社会信用代码"}
_NEGATIVE = re.compile(r"候选人|未中标|未成交|投标报价|评审报价|模板|范本|采购需求")
_MEMBER = "联合体成员:"
_PACKAGE_CLAIM = re.compile(r"所属包|采购包|合同包|包号|标包|标段")
_CANDIDATE_ANNOUNCEMENT = re.compile(r"候选人(?:公示|公告|名单)")


def _balanced(value):
    depth = 0
    for character in value:
        depth += (character == "(") - (character == ")")
        if depth < 0:
            return False
    return depth == 0


def _parts(value):
    value = _key(value)
    marker = "(" + _MEMBER
    if value.count(marker) != 1 or not value.endswith(")"):
        return None
    lead, members = value.rsplit(marker, 1)
    members = members[:-1]
    if (not _usable(lead) or not _usable(members) or not _balanced(lead)
            or not _balanced(members) or any(c in lead + members for c in ":|;")
            or _NEGATIVE.search(lead + members)):
        return None
    return lead, members


def _owner_parts(value):
    value = _key(value)
    if parts := _parts(value):
        return parts
    if value.count("," + _MEMBER) != 1:
        return None
    lead, members = value.rsplit("," + _MEMBER, 1)
    return _parts(lead + "(" + _MEMBER + members + ")")


def _regions(body):
    """Only explicit award/item sections; every numbered section ends a role."""
    result, role, lines = defaultdict(list), "", []
    for raw in body.splitlines():
        value = _key(raw)
        if heading := _SECTION.fullmatch(value):
            if role and lines:
                result[role].append(lines)
            title = heading[1].rstrip(":")
            role = "award" if re.fullmatch(r"(?:中标(?:\(成交\))?|成交)(?:信息|结果)|采购结果", title) else (
                "item" if title == "主要标的信息" else "")
            lines = []
        elif role and value:
            lines.append(raw.strip())
    if role and lines:
        result[role].append(lines)
    return result


def _announcement_allowed(body, tables):
    prefix = []
    for line in body.splitlines():
        value = _key(line)
        heading = _SECTION.fullmatch(value)
        if heading and re.fullmatch(r"(?:中标(?:\(成交\))?|成交)(?:信息|结果)|采购结果", heading[1].rstrip(":")):
            break
        prefix.append(value)
    titles = [cell for table in tables if table and _key(table[0][0]).rstrip(":") == "公告信息"
              for row in table[1:] if len(row) == 2 and _key(row[0]).rstrip(":") == "公告标题" for cell in row[1:]]
    if any(_CANDIDATE_ANNOUNCEMENT.search(_key(value)) for value in prefix + titles):
        return False
    return not any(_TABLE_REFERENCE.search(_key(line)) and _NEGATIVE.search(_key(line))
                   for line in body.splitlines())


def _source_rows(tables, regions, role):
    """Literal HTML row plus exact body alignment and explicit scope position."""
    result = {}
    for table in tables:
        if not table:
            continue
        header = tuple(_key(cell) for cell in table[0])
        if len(set(header)) != len(header):
            continue
        if role == "award":
            suppliers = [i for i, cell in enumerate(header) if cell in _SUPPLIERS]
            if (len(suppliers) != 1 or sum(bool(_AWARD_AMOUNT.fullmatch(cell)) for cell in header) != 1
                    or any(cell not in _AWARD_COLUMNS and not _AWARD_AMOUNT.fullmatch(cell) for cell in header)):
                continue
        else:
            if sum(cell in _NAMES for cell in header) != 1 or any(cell not in _ITEM_COLUMNS for cell in header):
                continue
        for raw in table[1:]:
            cells = tuple(_key(cell) for cell in raw)
            if len(cells) != len(header) or not all(cells) or cells == header:
                continue
            for ri, lines in enumerate(regions[role]):
                if _NEGATIVE.search("\n".join(_key(line) for line in lines)):
                    continue
                kinds, kind = [], ""
                for line in lines:
                    value = _key(line)
                    if _PACKAGE_LINE.fullmatch(value):
                        kind = ""
                    elif _OWNER.fullmatch(value):
                        kind = "service" if value.startswith(("服务类(", "工程类(")) else "goods"
                    kinds.append(kind)
                for position, (package, owner), values in _aligned(header, lines):
                    if values == cells and package:
                        identity = (ri, position, header, cells, package, owner)
                        # Duplicate serialization does not create a second row.
                        result[(identity, tuple(raw))] = (header, tuple(raw), package, owner, kinds[position], ri, position)
    return list(result.values())


def consortium_supplier_decisions(entities: list[ExtractedEntity], *, source_text: str):
    """Per-row original full supplier or empty; never derive fields from gold."""
    body, tables, valid = _scope(source_text)
    valid = valid and _announcement_allowed(body, tables)
    regions = _regions(body) if valid else defaultdict(list)
    awards = _source_rows(tables, regions, "award") if regions["award"] else []
    items = _source_rows(tables, regions, "item") if regions["item"] else []
    model_counts = Counter((_key(row.product_name), _key(row.category), _key(row.brand)) for row in entities)
    decisions = []
    for entity in entities:
        lead, name = _key(entity.brand), _key(entity.product_name)
        if not valid or not _usable(lead) or _parts(lead) or ":" in lead:
            decisions.append(("", "not_exact_existing_lead"))
            continue
        if model_counts[name, _key(entity.category), lead] != 1:
            decisions.append(("", "ambiguous_current_item_lead"))
            continue
        matching_awards = []
        for header, raw, package, _, _, _, _ in awards:
            supplier = raw[next(i for i, cell in enumerate(header) if cell in _SUPPLIERS)].strip()
            parts = _parts(supplier)
            if parts and parts[0] == lead:
                matching_awards.append((package, supplier, parts[1]))
            elif _key(supplier) == lead:
                matching_awards.append((package, "", ""))
        if len(matching_awards) != 1 or not matching_awards[0][1]:
            decisions.append(("", "not_unique_awarded_consortium"))
            continue
        package, supplier, members = matching_awards[0]
        awarded_literals = {(header, tuple(_key(cell) for cell in raw)) for header, raw, *_ in awards}
        unmatched = False
        for table in tables:
            if not table:
                continue
            header = tuple(_key(cell) for cell in table[0])
            si = [i for i, cell in enumerate(header) if cell in _SUPPLIERS]
            if len(si) != 1:
                continue
            for raw in table[1:]:
                value = _key(raw[si[0]]) if len(raw) > si[0] else ""
                if (value == lead or value.startswith(lead + "(联合体成员:")) and (
                        header, tuple(_key(cell) for cell in raw)) not in awarded_literals:
                    unmatched = True
        award_consumed = defaultdict(set)
        for header, _, _, _, _, ari, pos in awards:
            award_consumed[ari].update({pos} if "|" in regions["award"][ari][pos] else range(pos, pos + len(header)))
        if unmatched or any(lead in _key(line) and ("联合体成员" in _key(line)
                or "供应商" in _key(line) or _key(line) == lead) and index not in award_consumed[ari]
                for ari, lines in enumerate(regions["award"]) for index, line in enumerate(lines)):
            decisions.append(("", "unparsed_or_unmatched_awarded_supplier"))
            continue
        matching_items = []
        for header, raw, item_package, owner, kind, ri, position in items:
            ni = next(i for i, cell in enumerate(header) if cell in _NAMES)
            ci = [i for i, cell in enumerate(header) if cell in _CATEGORY]
            if (_key(raw[ni]) != name or kind != "service" or _owner_parts(owner) != (lead, members)
                    or (entity.category and (len(ci) != 1 or _key(raw[ci[0]]) != _key(entity.category)))):
                continue
            matching_items.append((item_package, ri, position, len(header)))
        if len(matching_items) != 1 or matching_items[0][0] != package:
            decisions.append(("", "not_unique_same_package_item_owner"))
            continue
        _, ri, position, size = matching_items[0]
        item_literals = {(header, tuple(_key(cell) for cell in raw)) for header, raw, *_ in items}
        unmatched_items = False
        for table in tables:
            if not table:
                continue
            header = tuple(_key(cell) for cell in table[0])
            ni = [i for i, cell in enumerate(header) if cell in _NAMES]
            if not ni:
                continue
            for raw in table[1:]:
                if any(len(raw) > index and _key(raw[index]) == name for index in ni) and (
                        header, tuple(_key(cell) for cell in raw)) not in item_literals:
                    unmatched_items = True
        item_consumed = defaultdict(set)
        for header, _, _, _, _, iri, pos in items:
            item_consumed[iri].update({pos} if "|" in regions["item"][iri][pos] else range(pos, pos + len(header)))
        if any(_PACKAGE_CLAIM.search(_key(line)) and index not in consumed_by_role[cri]
                and not _PACKAGE_LINE.fullmatch(_key(line)) and not _OWNER.fullmatch(_key(line))
                for role, consumed_by_role in (("award", award_consumed), ("item", item_consumed))
                for cri, lines in enumerate(regions[role]) for index, line in enumerate(lines)):
            decisions.append(("", "unparsed_package_ownership"))
            continue
        if unmatched_items or any(name in _key(line) and index not in item_consumed[iri]
                and not _PACKAGE_LINE.fullmatch(_key(line)) and not _OWNER.fullmatch(_key(line))
                for iri, lines in enumerate(regions["item"]) for index, line in enumerate(lines)):
            decisions.append(("", "unparsed_or_unmatched_same_item"))
            continue
        if any((owner := _OWNER.fullmatch(_key(line))) and lead in owner[1]
               and _owner_parts(owner[1]) != (lead, members)
               for lines in regions["item"] for line in lines):
            decisions.append(("", "conflicting_declared_item_owner"))
            continue
        consumed = set(range(position, position + size)) if "|" not in regions["item"][ri][position] else {position}
        if any((_MEMBER in _key(line) and not _OWNER.fullmatch(_key(line)))
               and index not in consumed for index, line in enumerate(regions["item"][ri])):
            decisions.append(("", "unparsed_member_claim"))
            continue
        decisions.append((supplier, "unique_literal_same_package_consortium"))
    # A brand-only repair must not introduce a collision at the unchanged DAO
    # seven-field dedup boundary. Preserve originals rather than delete rows.
    proposed = [tuple(_key(supplier if field == "brand" and supplier else getattr(row, field))
                      for field in ENTITY_FIELDS) for row, (supplier, _) in zip(entities, decisions)]
    proposed_counts = Counter(proposed)
    return [("", "would_create_db_dedup_collision") if supplier and proposed_counts[key] > 1 else (supplier, reason)
            for key, (supplier, reason) in zip(proposed, decisions)]


def restore_consortium_suppliers(entities: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    """Final brand-only copy pass; all other fields and row order remain fixed."""
    result = []
    for original, (supplier, _) in zip(entities, consortium_supplier_decisions(entities, source_text=source_text)):
        row = original.model_copy(deep=True)
        if supplier:
            row.brand = supplier
        result.append(row)
    return result
