"""Conservative base-only fill from the serialized HTML announcement table."""

import re
import unicodedata

from models.schemas import ExtractedEntity


ANNOUNCEMENT_CATEGORY_RULE_VERSION = "announcement-category-fill-v1"
_SECTION = re.compile(r"^---[^\n]*---[ \t]*$", re.MULTILINE)
_HTML_TABLE = re.compile(r"---\s*HTML表格[1-9][0-9]*\s*---")
_ROW_FIELD = re.compile(r"(?:^|[;；])(?:品目(?:号|名称|编号|编码)?|类型|类别)(?:[:：]|$)")
_PLACEHOLDERS = {"", "未公示", "无", "公告中品目未公示", "-", "/",
                 "不适用", "未公布", "暂无", "未提供", "n/a", "null"}


def _key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def _is_placeholder(value: str) -> bool:
    normalized = _key(value)
    return normalized.lower() in _PLACEHOLDERS or bool(
        re.match(r"(?:详见|见|参见|以附件为准)", normalized)
    )


def _has_row_category_label(cells: list[str]) -> bool:
    return any(_ROW_FIELD.search(_key(cell)) for cell in cells)


def announcement_category_evidence(source_text: str) -> tuple[str, str]:
    """Return a literal declared value, or a source-only reason for deferral.

    Require one announcement identity and explicit HTML metadata framing.
    Any row-category evidence conservatively blocks fallback for all rows;
    this is not a row resolver, and incomplete sources are not absence proof.
    """
    source = (source_text or "").replace("\r\n", "\n")
    sections = list(_SECTION.finditer(source))
    values, projects, metadata_spans = [], set(), []
    for index, marker in enumerate(sections):
        heading = marker.group().strip()
        if "附件" in heading or "全页原生文本" in heading:
            break  # An attached/copied announcement is never the HTML metadata.
        if not _HTML_TABLE.fullmatch(heading):
            continue
        end = sections[index + 1].start() if index + 1 < len(sections) else len(source)
        lines = [line.strip() for line in source[marker.end():end].splitlines() if line.strip()]
        if not lines or _key(lines[0]).rstrip(":") != "公告信息":
            continue
        cells = [[cell.strip() for cell in line.split("|")] for line in lines[1:]]
        if any(len(row) == 1 and _key(row[0]).rstrip(":") not in {
            "联系人及联系方式", "附件",
        } for row in cells):
            return "", "ambiguous_metadata_structure"
        if any(_has_row_category_label(row)
               for row in cells if not (len(row) == 2 and _key(row[0]) == "品目")):
            return "", "row_category_evidence_needs_resolution"
        project = [row[1] for row in cells if len(row) == 2 and _key(row[0]) == "采购项目名称"]
        category = [row[1] for row in cells if len(row) == 2 and _key(row[0]) == "品目"]
        if len(project) != 1 or _is_placeholder(project[0]) or len(category) != 1:
            return "", "ambiguous_metadata_structure"
        projects.add(_key(project[0]))
        values.append(category[0])
        metadata_spans.append((marker.start(), end))
    if not values:
        return "", "no_scoped_announcement_table"
    if len(projects) != 1 or len({_key(value) for value in values}) != 1:
        return "", "conflicting_announcement_metadata"
    value = values[0]
    if _is_placeholder(value):
        return "", "placeholder_announcement_category"
    # Remove only the proven metadata blocks. Scan the rest, including parsed
    # attachment text, for explicit row labels rather than guessing ownership.
    parts, start = [], 0
    for left, right in metadata_spans:
        parts.append(source[start:left])
        start = right
    parts.append(source[start:])
    for line in "\n".join(parts).splitlines():
        if _has_row_category_label(line.split("|")) or re.fullmatch(
            r"(?:货物|服务|工程)类(?:主要标的信息)?[:：]?", _key(line),
        ):
            return "", "row_category_evidence_needs_resolution"
    return value, "declared_announcement_category"


def fill_announcement_category_entities(
    entities: list[ExtractedEntity], *, source_text: str,
) -> list[ExtractedEntity]:
    """Copy empty base categories only; call after existing dedup, never details."""
    category, _ = announcement_category_evidence(source_text)
    cleaned = []
    for original in entities:
        entity = original.model_copy(deep=True)
        if not entity.category.strip() and category:
            entity.category = category
        cleaned.append(entity)
    return cleaned
