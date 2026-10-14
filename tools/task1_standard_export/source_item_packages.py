"""Recover discarded numeric package captions for already-proven item rows.

This module never creates items or repairs prices/names/brands. Frozen row
proof, a unique physical body witness and a registered awarded package are
all prerequisites. Ambiguous captions clear context instead of inheriting it.
"""
import re

from source_awards import package as parse_caption

_PACKAGE_LOOKING = re.compile(
    r"^(?:[一二三四五六七八九十0-9]+[、.])?"
    r"(?:合同包|采购包|包号|包|标包|标段|[A-Za-z]包|"
    r"第[0-9一二三四五六七八九十]+(?:包|标段)|[0-9一二三四五六七八九十]+(?:包|标段)|"
    r"未分包|不分包|不分标段|不划分标段)"
)


def recover_item_contexts(body, tables, catalog, registry, awards):
    from extractor.awarded_rate_tiers import _package_ids
    from extractor.item_total_evidence import _aligned, _item_regions
    from extractor.official_qa_money import _matrix_parts
    from extractor.supplier_rules import _key, _usable
    from extractor.total_award_rules import _label

    matrices = [part for table in tables for part in _matrix_parts(table)]
    regions = _item_regions(body)
    known_awards = {}
    for award in awards:
        key = award.get("package_key", "")
        supplier = award.get("supplier", "")
        if key in registry and _usable(supplier):
            known_awards.setdefault(key, set()).add(_key(supplier))

    timelines = []
    for lines in regions:
        timeline, caption = [], None
        for position, line in enumerate(lines):
            normalized = _key(line)
            if "|" not in normalized and _PACKAGE_LOOKING.match(normalized):
                parsed = parse_caption(line)
                caption = ({**parsed, "source_caption": line, "caption_position": position}
                           if parsed and _package_ids(line) == {parsed["key"]} else None)
            timeline.append(caption)
        timelines.append(timeline)

    recovered = {}
    for row in catalog:
        if not row.proved or row.package_object or row.packages:
            continue  # Existing literal package proof is not replaced.
        table = matrices[row.table_index]
        header = tuple(_key(cell) for cell in table[0])
        if any(re.search(r"包号|合同包|采购包|标包|标段", label) for label in header):
            continue  # Unknown literal package columns cannot borrow a caption.
        cells = tuple(_key(cell) for cell in table[row.row_index])
        witnesses = [(region_index, position, context)
                     for region_index, lines in enumerate(regions)
                     for position, context, values in _aligned(header, lines)
                     if values == cells]
        if len(witnesses) != 1:
            continue
        region_index, position, (context_key, context_owner) = witnesses[0]
        caption = timelines[region_index][position]
        if not caption or caption["key"] != context_key or context_key not in known_awards:
            continue
        owners = [owner for owner in (context_owner, _label(row.owner)[1]) if owner]
        if any(_key(owner) not in known_awards[context_key] for owner in owners):
            continue  # Supplier evidence cannot be contradicted or paired by order.
        recovered[(row.table_index, row.row_index)] = {
            "product_name": row.name, "package_key": context_key,
            "source_table": row.table_index, "source_row": row.row_index,
            "source_region": region_index, "body_row_position": position,
            "source_caption": caption["source_caption"], "caption_position": caption["caption_position"],
            "source_context_supplier": context_owner,
            "method": "unique_proved_item_awarded_package_caption",
        }
    return recovered
