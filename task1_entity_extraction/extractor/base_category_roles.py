"""Base-only corrections for proven service roles and field-boundary false hits."""

from dataclasses import dataclass
import re

from extractor.announcement_category import announcement_category_evidence
from extractor.category_rules import _normalized, _usable_category_source
from extractor.quantity_units import _body_tables, _column
from extractor.supplier_rules import _aligned_rows, _body_sections, _key, _name, _scope
from models.schemas import ExtractedEntity


BASE_CATEGORY_ROLE_RULE_VERSION = "base-category-role-boundaries-v1"
_BREAK = "\x00"
_SERVICE_FIELDS = {"服务名称", "服务范围"}
_NAME_FIELDS = {"名称", "产品名称", "货物名称", "服务名称", "工程名称", "采购标的", "标的名称"}
_CATEGORY_FIELDS = {"品目", "品目编号", "品目编码", "品目号", "品目名称", "类型", "类别", "所属行业"}
_FIELDS = {"名称", "产品名称", "货物名称", "服务名称", "工程名称", "服务范围", "服务要求", "服务时间", "服务标准",
           "品牌", "货物品牌", "规格型号", "货物型号", "数量", "货物数量", "单价", "总价", "供应商名称", "供应商地址",
           "项目名称", "采购项目名称", "品目", "品目编号", "品目编码", "品目号", "品目名称", "类型", "类别", "所属行业"}
_LABEL = re.compile(r"^(?:[一二三四五六七八九十0-9]+[、.])?(?:"
                    + "|".join(sorted(_FIELDS, key=len, reverse=True)) + r"):")
_MARKER = re.compile(r"(^---[^\n]*---$)", re.MULTILINE)
_QUOTE = re.compile(r"^(.+?服务)单台设备报价:[￥¥]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:元|万元)?$")


@dataclass(frozen=True)
class _Owner:
    name: str
    values: tuple[str, ...]
    identity: tuple


def _canonical(source: str) -> str:
    return "\n".join(_key(line) for line in source.splitlines() if line.strip())


def _declared_header(cells) -> bool:
    return (_column(cells, _NAME_FIELDS) is not None
            or sum(c in _FIELDS for c in cells) >= 2)


def _field_aware(source: str) -> str:
    """Keep field/cell boundaries, permitting only declared code+name pairing."""
    output, combination, width = [], None, None
    for raw in source.splitlines():
        line = _key(raw)
        if "|" in line:
            cells = line.split("|")
            if _declared_header(cells):
                width = len(cells)
            elif width != len(cells):
                width = None
            ci = _column(cells, {"品目编号", "品目编码", "品目号"})
            ni = _column(cells, {"品目名称"})
            if ci is not None and ni is not None:
                combination = (len(cells), ci, ni)
            elif any(cell in _FIELDS for cell in cells):
                combination = None
            combined = ""
            if combination and ci is None and ni is None and len(cells) == combination[0]:
                _, code_index, name_index = combination
                combined = _BREAK + cells[code_index] + cells[name_index] + _BREAK
            if width or cells[0] in _CATEGORY_FIELDS:
                line = _BREAK + _BREAK.join(cells) + combined + _BREAK
        else:
            combination, width = None, None
        if line in _FIELDS or line.startswith("---"):
            line = _BREAK + line
        output.append(_LABEL.sub(lambda m: _BREAK + m[0], line))
    return "\n".join(output)


def _declared_values(source: str) -> set[str]:
    """An actual category cell/value takes priority over lexical boundary rules."""
    values, header, pending, continuations = set(), None, False, {}
    for raw in _usable_category_source(source).splitlines():
        line = _key(raw)
        label, separator, value = line.partition(":")
        if pending:
            # An explicit empty label may place its actual value on the next
            # line; do not reinterpret field-like words inside that value.
            if "|" not in line and not line.startswith("---"):
                values.add(_normalized(line))
            pending = False
        cells = line.split("|")
        if continuations and len(cells) > 1:
            continued = {}
            for (width, index), prefixes in continuations.items():
                if (width == len(cells) and cells[index]
                        and not any(c for j, c in enumerate(cells) if j != index)):
                    continued[(width, index)] = {p + _normalized(cells[index]) for p in prefixes}
            if continued:
                # Empty other columns cannot prove a new record. Retain only
                # continuations at the explicitly declared category index.
                continuations = continued
                values.update(v for prefixes in continued.values() for v in prefixes)
                continue
        if (continuations and "|" not in line and not line.startswith("---")
                and line not in _FIELDS and not _LABEL.match(line)):
            # An unstructured softwrap cannot prove a different row. This
            # guard preserves possible category values without emitting them.
            continuations = {position: {p + _normalized(line) for p in prefixes}
                             for position, prefixes in continuations.items()}
            values.update(v for prefixes in continuations.values() for v in prefixes)
            header = None
            continue
        continuations = {}
        if separator and label in _CATEGORY_FIELDS:
            values.add(_normalized(value))
            pending = not value
            continuations = {(None, None): {_normalized(value)}} if value else {}
        elif line in _CATEGORY_FIELDS:
            pending = True
        if len(cells) == 2 and cells[0] in _CATEGORY_FIELDS and cells[1] not in _FIELDS:
            values.add(_normalized(cells[1]))
            continuations = {(len(cells), 1): {_normalized(cells[1])}}
            header = None
        elif len(cells) > 1 and any(c in _CATEGORY_FIELDS for c in cells) and any(c in _FIELDS for c in cells):
            header = cells
        elif header and len(cells) == len(header):
            for i, h in enumerate(header):
                if h in _CATEGORY_FIELDS:
                    values.add(_normalized(cells[i]))
                    continuations[(len(cells), i)] = {_normalized(cells[i])}
            ci = _column(header, {"品目编号", "品目编码", "品目号"})
            ni = _column(header, {"品目名称"})
            if ci is not None and ni is not None:
                combined = _normalized(cells[ci] + cells[ni])
                values.add(combined)
                continuations.setdefault((len(cells), ni), set()).add(combined)
        else:
            header = None
    return values - {""}


def _rewrite_row(segment: str, cells: tuple[str, ...], replacement: tuple[str, ...]) -> str:
    """Replace only complete row lines/sequences inside their proven segment."""
    for separator in ("|", "\n"):
        pattern = re.compile(r"(^|\n)" + re.escape(separator.join(cells)) + r"(?=\n|$)")
        segment = pattern.sub(lambda m: m[1] + _BREAK + _BREAK.join(replacement) + _BREAK, segment)
    return segment


def _role_sources(source: str, body: str, tables: list) -> tuple[str, str, set[_Owner]]:
    """Materialize proven body rows; attachments/unknown mentions stay opaque."""
    full, body = _canonical(source), _canonical(body)
    bounded_body, residual_body = body, body
    headers = {tuple(_key(c) for c in row) for table in tables for row in table if len(row) > 1
               and _declared_header([_key(c) for c in row])}
    owners, certified = set(), set()
    for role, lines in _body_sections(body):
        if role != "item":
            continue
        for header, original_segment in _body_tables(lines, headers):
            if _column(header, {"服务名称"}) is None:
                continue
            raw_segment = "\n".join(original_segment)
            bounded_segment, residual_segment = raw_segment, raw_segment
            ni = _column(header, {"服务名称"})
            fields = [i for i, h in enumerate(header) if h in _SERVICE_FIELDS]
            for package, cells in _aligned_rows(header, original_segment):
                if ni is None or cells[ni] in _FIELDS or not _name(cells[ni]):
                    continue
                values = tuple(cells[i] for i in fields)
                owners.add(_Owner(_name(cells[ni]), values, (package, tuple(header), cells)))
                certified.add((tuple(header), cells))
                bounded_segment = _rewrite_row(bounded_segment, cells, cells)
                residual_segment = _rewrite_row(residual_segment, cells,
                                                tuple(_BREAK if i in fields else v for i, v in enumerate(cells)))
            bounded_body = bounded_body.replace(raw_segment, bounded_segment, 1)
            residual_body = residual_body.replace(raw_segment, residual_segment, 1)
    winning = False
    for line in body.splitlines():
        if line.rstrip(":") == "成交单位":
            winning = True
            continue
        match = _QUOTE.fullmatch(line) if winning else None
        if match:
            owners.add(_Owner(_name(match[1]), (match[1],), ("winning_price_label", line)))
            residual_body = residual_body.replace(line, _BREAK + line[len(match[1]):], 1)
        else:
            winning = False
    # The source scope function returns the exact body prefix before markers.
    suffix = full[len(body):]
    pieces = _MARKER.split(suffix)
    residual_pieces = list(pieces)
    attached = False
    for i, piece in enumerate(pieces):
        if not piece.startswith("---"):
            continue
        if "附件" in piece or "全页原生文本" in piece:
            attached = True
        if attached or not re.fullmatch(r"---HTML表格[1-9][0-9]*---", piece) or i + 1 >= len(pieces):
            continue
        output, residual_output, active = [], [], None
        for line in pieces[i + 1].splitlines():
            cells = tuple(line.split("|"))
            if cells in headers:
                active = cells
            if active and (active, cells) in certified:
                fields = [j for j, h in enumerate(active) if h in _SERVICE_FIELDS]
                output.append(_BREAK + _BREAK.join(cells) + _BREAK)
                residual_output.append(_BREAK + _BREAK.join(_BREAK if j in fields else c for j, c in enumerate(cells)) + _BREAK)
            else:
                output.append(line)
                residual_output.append(line)
        ending = "\n" if pieces[i + 1].endswith("\n") else ""
        pieces[i + 1] = "\n".join(output) + ending
        residual_pieces[i + 1] = "\n".join(residual_output) + ending
    return bounded_body + "".join(pieces), residual_body + "".join(residual_pieces), owners


def base_category_role_decisions(entities: list[ExtractedEntity], *, source_text: str) -> list[tuple[str | None, str]]:
    body, tables, valid = _scope(source_text)
    valid = valid and _BREAK not in source_text
    if valid:
        bounded, residual, owners = _role_sources(source_text, body, tables)
        old_evidence = _normalized(_usable_category_source(source_text))
        bounded_evidence = _normalized(_field_aware(_usable_category_source(bounded)))
        residual_evidence = _normalized(_field_aware(_usable_category_source(residual)))
        announcement, reason = announcement_category_evidence(source_text)
        declared = _declared_values(source_text)
    else:
        old_evidence = bounded_evidence = residual_evidence = ""
        owners, announcement, reason = set(), "", "unproven_scope"
        declared = set()
    decisions = []
    for entity in entities:
        value = _normalized(entity.category)
        if not value:
            decisions.append((None, "preserve_empty_category"))
        elif not valid:
            decisions.append((None, "unproven_announcement_scope"))
        elif any(value in field for field in declared):
            decisions.append((None, "preserve_declared_category_value"))
        elif value in old_evidence and value not in bounded_evidence:
            decisions.append(("", "cross_field_false_occurrence"))
        elif (announcement and reason == "declared_announcement_category"
              and value not in _normalized(announcement) and value in bounded_evidence and value not in residual_evidence):
            matches = {o for o in owners if o.name == _name(entity.product_name)
                       and any(value in v for v in o.values)}
            if len(matches) == 1:
                decisions.append((announcement, "proven_service_role_to_announcement_category"))
            else:
                decisions.append((None, "no_unique_service_owner"))
        else:
            decisions.append((None, "preserve_true_unknown_or_disputed_category"))
    return decisions


def correct_base_category_roles(entities: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    """After existing cleanup/dedup; change category only, never detail entrypoint."""
    result = []
    for original, (value, _) in zip(entities, base_category_role_decisions(entities, source_text=source_text)):
        copied = original.model_copy(deep=True)
        if value is not None:
            copied.category = value
        result.append(copied)
    return result
