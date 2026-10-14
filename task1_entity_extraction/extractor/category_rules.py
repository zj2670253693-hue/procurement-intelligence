"""Reject categories with no usable occurrence in the model input.

Occurrence outside known non-category contexts is necessary, not proof that a
value belongs to a particular row. Unknown contexts remain for later review.
"""

import re
import unicodedata

from models.schemas import ExtractedEntity


CATEGORY_RULE_VERSION = "source-context-v2"

# Keep line boundaries until context filtering has finished. A label may have
# its value on the next line, but a field must not consume the following row.
_NON_CATEGORY_FIELD = re.compile(
    r"(?:采购方式|预算(?:说明|金额)?|最高限价|采购需求|需求说明|技术要求)(?:[:|]|\n)[\s|]*"
    r".*?(?=\n|[。；;|]|[一二三四五六七八九十]+、|"
    r"(?:品目(?:编号|编码|号|名称)?|类型|类别|所属行业|中标标的|名称)[:|]|$)"
)
_POLICY_LIST = re.compile(r"(?:节能产品|环境标志产品)政府采购品目\s*清单")
_CATEGORY_LABEL = re.compile(r"(?:品目(?:编号|编码|号|名称)?|类型|类别|所属行业)[:|]")
_CATEGORY_CELL_LABEL = re.compile(r"(?:品目(?:编号|编码|号|名称)?|类型|类别|所属行业)")
_NOTE_CONTEXT = re.compile(r"政府采购品目清单|招标文件范本|参考样本|参考样表")


def _remove_context_notes(value: str) -> str:
    # Balanced parentheses allow policy years and attachment numbers nested
    # inside the note. Leave unclosed/ambiguous notes untouched.
    depth = 0
    start = 0
    retained_from = 0
    retained = []
    for index, char in enumerate(value):
        if char == "(":
            if depth == 0:
                start = index
            depth += 1
        elif char == ")" and depth:
            depth -= 1
            if depth == 0 and _NOTE_CONTEXT.search(value[start:index + 1]):
                retained.append(value[retained_from:start])
                retained_from = index + 1
    retained.append(value[retained_from:])
    return "".join(retained)


def _without_category_notes(source: str) -> str:
    """Preserve a declared category before a parenthetical policy/form note.

    Only explicit key/value fields and clearly headed table columns qualify.
    Removing the note also prevents its words from becoming category evidence.
    """
    rows = []
    columns: list[int] = []
    width = 0
    for line in source.splitlines():
        cells = line.split("|")
        labels = [i for i, cell in enumerate(cells) if _CATEGORY_CELL_LABEL.fullmatch(cell)]
        if labels:
            other_headers = {"名称", "产品名称", "货物名称", "采购标的", "品牌", "备注", "说明", "序号"}
            is_header = len(labels) > 1 or any(cell in other_headers for cell in cells)
            columns, width = (labels, len(cells)) if is_header else ([], 0)
            if not is_header:
                for index in labels:
                    if index + 1 < len(cells):
                        cells[index + 1] = _remove_context_notes(cells[index + 1])
        elif columns and len(cells) == width:
            for index in columns:
                cells[index] = _remove_context_notes(cells[index])
        else:
            columns, width = [], 0
        line = "|".join(cells)
        line = re.sub(
            r"((?:品目(?:编号|编码|号|名称)?|类型|类别|所属行业):)([^|\n]*)",
            lambda match: match[1] + _remove_context_notes(match[2]), line,
        )
        rows.append(line)
    return "\n".join(rows)


def _usable_category_source(source: str) -> str:
    """Exclude only identifiable non-award fields, policies and form examples.

    This is not a whitelist of category names or a row-to-column resolver.
    Ambiguous/unlabelled occurrences are retained, not filled from a catalog.
    """
    source = unicodedata.normalize("NFKC", source or "").replace("\r\n", "\n")
    source = re.sub(r"[^\S\n]+", "", source)
    source = _without_category_notes(source)
    source = _NON_CATEGORY_FIELD.sub("\n", source)
    # A soft PDF line break in '品目清单' is not a new field. Explicit category
    # labels start a new segment, so policy text cannot erase a later real row.
    segments = re.split(
        r"(?<=[。；;|])|(?=\n(?:品目(?:编号|编码|号|名称)?|类型|类别|所属行业)[:|])",
        source,
    )
    policy_filtered = []
    for segment in segments:
        if _POLICY_LIST.search(segment) and not _CATEGORY_LABEL.search(segment):
            # Restrict removal to the sentence containing the policy list;
            # preceding newline-separated rows are not policy evidence.
            lines = segment.splitlines(keepends=True)
            pending = ""
            for line in lines:
                pending += line
                if _POLICY_LIST.search(pending):
                    pending = ""
                elif not re.search(
                    r"(?:(?:节能产品|环境标志产品)政府采购品目|应符合|须符合|必须符合|按照|参照|执行)\s*$",
                    pending,
                ):
                    policy_filtered.append(pending)
                    pending = ""
            policy_filtered.append(pending)
        else:
            policy_filtered.append(segment)
    source = "".join(policy_filtered)
    # Template headings and explanations are not the awarded item's type.
    return "\n".join(
        "|".join(
            cell if not re.search(r"招标文件范本|参考样本|参考样表", cell)
            or _CATEGORY_LABEL.search(cell) else ""
            for cell in line.split("|")
        )
        for line in source.splitlines()
    )


def _normalized(value: str) -> str:
    return re.sub(r"[\s|]+", "", unicodedata.normalize("NFKC", value or ""))


def sanitize_category_entities(
    entities: list[ExtractedEntity], *, source_text: str,
) -> list[ExtractedEntity]:
    """Copy rows, clearing categories absent from usable same-input evidence.

    Supply HTML plus attachments for legacy predictions, not an HTML excerpt.
    Row count, order and other fields stay intact. No gold data is consulted.
    """
    normalized_source = _normalized(_usable_category_source(source_text))
    cleaned = []
    for original in entities:
        entity = original.model_copy(deep=True)
        category = _normalized(entity.category)
        if category in {"未公示", "无"} or (category and category not in normalized_source):
            entity.category = ""
        cleaned.append(entity)
    return cleaned
