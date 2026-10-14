"""Frozen-result import policy; does not alter extraction-time deduplication."""
import re
import unicodedata

from models.schemas import DetailedEntity, ENTITY_FIELDS


def deduplicate_import_details(items: list[DetailedEntity]) -> list[DetailedEntity]:
    """Retain distinct pages only in an unambiguous same-attachment group.

    Content identity follows the existing package/seven-field normalization.
    Every member must name the same nonempty attachment and a complete positive
    ASCII page number. Otherwise the whole group keeps the old first-row policy.
    A retained row is a provenance observation, not another inferred purchase.
    """
    keyed = []
    sources = {}
    for item in items:
        key = tuple(re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))
                    for value in (item.package_name, *(getattr(item, f) for f in ENTITY_FIELDS)))
        filename, page = item.source_file.strip(), item.source_page.strip()
        keyed.append((item, key, page))
        sources.setdefault(key, []).append((filename, page))

    page_aware = {
        key for key, group in sources.items()
        if group[0][0]
        and all(filename == group[0][0] and re.fullmatch(r"[1-9][0-9]*", page)
                for filename, page in group)
    }
    unique, seen = [], set()
    for item, key, page in keyed:
        identity = (key, page if key in page_aware else None)
        if identity not in seen:
            seen.add(identity)
            unique.append(item)
    return unique
