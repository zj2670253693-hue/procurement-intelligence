"""Base-only, source-proven numbered software function prose cleanup."""

import re
from dataclasses import replace
from dataclasses import dataclass

from extractor.spec_rules import (
    _ATTRIBUTE_CLUE, _ATTRIBUTE_LABELS, _NAME_LABELS, _REFERENCE, _PROMISE,
    _compact, _declared_model, _service_text_key,
)
from extractor.quantity_units import _body_tables, _column
from extractor.supplier_rules import _key, _package, _scope
from models.schemas import ExtractedEntity


BASE_FUNCTION_SPEC_RULE_VERSION = "base-numbered-function-spec-v1"
_NAMES = _NAME_LABELS | {"产品名称", "货物名称", "工程名称"}
_TITLE = re.compile(r"^([1-9]\d*[.、][^:]+):(.+)$")
_FUNCTION_TITLE = re.compile(r"管理|功能|分析|预警|采集|巡查|自查|督导|复核|检索|查询|对接|共享")
_ACTION = re.compile(r"采集|编辑|删除|查询|筛选|检索|导出|记录|反馈|上传|推送|比对|分析|预警|巡查|管理|展示")
_UNCERTAIN = re.compile(r"\d\s*[×xX*]\s*\d|[≥≤<>]\s*\d|\d+(?:\.\d+)?\s*(?:%|秒|毫秒|米|厘米|毫米|伏|瓦)|性能|在线率|正常率")
_CHINESE_ATTRIBUTE = re.compile(
    r"准确率|正确率|可用率|成功率|百分之|存储|储存|容纳|承载|"
    r"每(?:毫秒|秒|分钟|小时|天|日|周|月|年)|"
    r"[0-9零〇一二两三四五六七八九十百千万亿点半]+(?:毫秒|秒|分钟|小时|天|日|周|月|年|"
    r"次|笔|条|个|名|件|毫米|厘米|米|字节|兆|吉|伏|瓦|千克|公斤|克|毫升|升|赫兹|英寸)"
)
_SPEC_HEADER = re.compile(r"规格|型号|版本|容量|性能参数")
_NUMERIC_UNCERTAINTY = re.compile(r"[\d零〇一二两三四五六七八九十百千万亿半壹贰叁肆伍陆柒捌玖拾佰仟萬億]")
_NOTE_FIELDS = {"名称", "内容", "范围", "类型", "隐患类型", "发现时间", "位置", "责任人", "排查内容",
                "复核情况", "整改完成情况"}
_ATTACHMENT = re.compile(r"^---[^\n]*(?:附件|全页原生文本)[^\n]*---[ \t]*$", re.MULTILINE)
_ITEM = re.compile(r"^[一二三四五六七八九十0-9]+[、.]主要标的信息:?$")
_OUTER = re.compile(r"^[一二三四五六七八九十]+[、.]|^[0-9]+[、.](?=\D)")
_KNOWN_END = re.compile(
    r"^[一二三四五六七八九十0-9]+[、.](?:评审专家(?:\(单一来源采购人员\))?(?:名单)?|"
    r"代理服务(?:收费标准及金额)?|公告期限|其[他它]补充(?:事宜)?|"
    r"凡对本次公告内容提出询问[,，]请按以下方式联系)[.:。]?$"
)


def _attributes(value: str, *, numbered_title: bool = False) -> bool:
    key = _key(value)
    if numbered_title:
        key = re.sub(r"^[1-9]\d*[.、]", "", key, count=1)
    return bool(_ATTRIBUTE_CLUE.search(key) or _UNCERTAIN.search(key)
                or _CHINESE_ATTRIBUTE.search(key) or _NUMERIC_UNCERTAINTY.search(key))


def _header_label(value: str) -> str:
    """Only declared header annotations, never annotations in row values."""
    return re.sub(r"\([^()]*\)$", "", _key(value))


def _reduced_title(title: str) -> str:
    match = re.fullmatch(r"(.+)\(([^()]*)\)", title)
    if not match or _attributes(match[2]) or not match[2].endswith("等"):
        return title
    labels = re.split(r"[/、,]", match[2][:-1])
    if labels and all(re.sub(r"^(?:明确|记录)", "", label) in _NOTE_FIELDS for label in labels):
        return match[1]
    return title


@dataclass(frozen=True)
class _Row:
    names: frozenset[str]
    functions: frozenset[str]
    body: str
    protected: bool
    header: tuple[str, ...]
    cells: tuple[str, ...]
    trusted: bool = True


def _known_header(cells) -> bool:
    return bool({_header_label(c) for c in cells} & _NAMES)


def _item_regions(body: str, serialized: list[_Row]):
    """Exempt numbered cells only inside an entire, actively aligned row."""
    raw_lines = [line.strip() for line in body.splitlines() if _key(line)]
    headers = {r.header for r in serialized}
    owner_labels = {_service_text_key(r.cells[ni]) for r in serialized
                    if (ni := _column(tuple(_header_label(c) for c in r.header), _NAMES)) is not None}
    lines, active, active_header, i, uncertain = [], False, None, 0, False
    while i < len(raw_lines):
        raw, key = raw_lines[i], _key(raw_lines[i])
        if active:
            pipe = tuple(_key(c) for c in raw.split("|"))
            header = next((h for h in headers if pipe == h or
                           tuple(_key(c) for c in raw_lines[i:i + len(h)]) == h), None)
            if header:
                width = 1 if pipe == header else len(header)
                lines.extend(raw_lines[i:i + width])
                active_header, i = header, i + width
                continue
            if active_header:
                whole = next((r for r in serialized if r.header == active_header and
                              (pipe == r.cells or tuple(_key(c) for c in raw_lines[i:i + len(r.cells)]) == r.cells)), None)
                if whole:
                    width = 1 if pipe == whole.cells else len(whole.cells)
                    lines.extend(raw_lines[i:i + width])
                    i += width
                    continue
            if "|" in raw and _known_header(pipe):
                active_header = None
        if _ITEM.fullmatch(key) or _OUTER.match(key):
            if active:
                # An unmatched numbered owner/function must not disappear as
                # a chapter. Unknown endings make the whole region uncertain.
                if active_header and ("|" in raw or not _KNOWN_END.fullmatch(key)
                                      or _service_text_key(raw) in owner_labels):
                    uncertain = True
                yield lines, uncertain
            lines, active, active_header = [], bool(_ITEM.fullmatch(key)), None
            uncertain = False
        elif active and key:
            lines.append(raw)
        i += 1
    if active:
        yield lines, uncertain


def _rows(tables: list) -> list[_Row]:
    result = []
    for table in tables:
        header, start = None, len(result)
        for raw in table:
            cells = tuple(_key(c) for c in raw)
            if _known_header(cells):
                header, start = cells, len(result)
                continue
            if not header:
                continue
            if len(cells) != len(header):
                result[start:] = [replace(row, trusted=False) for row in result[start:]]
                header = None
                continue
            labels = tuple(_header_label(c) for c in header)
            ni = _column(labels, _NAMES)
            if ni is None or not cells[ni]:
                continue
            base = _service_text_key(cells[ni])
            names, functions, body, protected = {base}, set(), "", False
            si = _column(labels, {"服务范围"})
            title = _TITLE.fullmatch(cells[si]) if si is not None else None
            if title:
                functions = {base + _service_text_key(title[1]), base + _service_text_key(_reduced_title(title[1]))}
                names.update(functions)
                body = title[2] if _FUNCTION_TITLE.search(title[1]) else ""
                protected = _attributes(title[1], numbered_title=True) or _attributes(title[2])
            for i, label in enumerate(labels):
                if label in _ATTRIBUTE_LABELS or _SPEC_HEADER.search(label):
                    value = cells[i].rstrip("。;，,")
                    if value and value not in {"无", "未公示"} and not _REFERENCE.fullmatch(value) and not _PROMISE.fullmatch(value):
                        protected = True
            result.append(_Row(frozenset(names), frozenset(functions), body, protected, header, cells))
    return result


def _body_evidence(body: str, headers: set[tuple[str, ...]], serialized: list[_Row]):
    """Keep every body row, including duplicates; uncertain layouts block clear."""
    rows, uncertain = [], False
    for lines, region_uncertain in _item_regions(body, serialized):
        uncertain |= region_uncertain
        # A body-only header cannot be silently absorbed as data or skipped.
        covered = set()
        for i in range(len(lines)):
            for header in headers:
                if tuple(_key(line) for line in lines[i:i + len(header)]) == header:
                    covered.update(range(i, i + len(header)))
        for i, line in enumerate(lines):
            cells = tuple(_key(c) for c in line.split("|"))
            if "|" in line and _known_header(cells) and cells not in headers:
                uncertain = True
            elif "|" not in line and _header_label(line) in _NAMES and i not in covered:
                uncertain = True
        for header, segment in _body_tables(lines, headers):
            width, i, pending, values = len(header), 0, [], []
            # _body_tables may prepend a package label before the header.
            while i < len(segment):
                if tuple(_key(c) for c in segment[i].split("|")) == tuple(header):
                    i += 1
                    break
                if tuple(_key(c) for c in segment[i:i + width]) == tuple(header):
                    i += width
                    break
                i += 1
            for line in segment[i:]:
                if _package(line) and not pending:
                    continue
                if "|" in line:
                    cells = tuple(_key(c) for c in line.split("|"))
                    if pending or len(cells) != width:
                        uncertain = True
                    else:
                        values.append(cells)
                else:
                    pending.append(_key(line))
                    if len(pending) == width:
                        values.append(tuple(pending))
                        pending = []
            uncertain |= bool(pending)
            rows.extend(_rows([[list(header), *values]]))
    return rows, uncertain


def base_function_spec_decisions(entities: list[ExtractedEntity], *, source_text: str) -> list[tuple[bool, str]]:
    """Clear only complete pure prose in one body-certified function row."""
    body, tables, valid = _scope(source_text)
    rows = _rows(tables) if valid and "\x00" not in source_text else []
    headers = {tuple(_key(c) for c in r) for table in tables for r in table if len(r) > 1 and _known_header([_key(c) for c in r])}
    body_rows, uncertain_body = _body_evidence(body, headers, rows)
    marker = _ATTACHMENT.search(source_text)
    attachment = source_text[marker.start():] if marker else ""
    competing = _rows([[line.split("|") for line in attachment.splitlines()]])
    decisions = []
    for entity in entities:
        value, name = _service_text_key(entity.spec_model), _service_text_key(entity.product_name)
        if not value:
            decisions.append((False, "preserve_empty_spec"))
        elif not valid or "\x00" in source_text:
            decisions.append((False, "unproven_announcement_scope"))
        elif _attributes(entity.spec_model):
            decisions.append((False, "preserve_attribute_or_uncertain_spec"))
        elif _declared_model(_compact(entity.spec_model), source_text):
            decisions.append((False, "preserve_declared_spec_value"))
        elif (value in _service_text_key(attachment) or name in _service_text_key(attachment)
              or any(name in r.names for r in competing)):
            decisions.append((False, "preserve_competing_or_unknown_attachment"))
        else:
            matches = [r for r in rows if name in r.names]
            if len(matches) != 1:
                decisions.append((False, "no_unique_function_owner"))
                continue
            row = matches[0]
            body_matches = [r for r in body_rows if name in r.names]
            proven = (not uncertain_body and len(body_matches) == 1
                      and body_matches[0] == row and row.trusted
                      and name in row.functions and not row.protected
                      and value == _service_text_key(row.body)
                      and bool(_ACTION.search(row.body)))
            decisions.append((proven, "unique_whole_numbered_function_prose" if proven else "preserve_unproven_function_copy"))
    return decisions


def clear_base_function_specs(entities: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    """Copy after existing base cleanup/dedup; never a detail entrypoint."""
    cleaned = []
    for original, (clear, _) in zip(entities, base_function_spec_decisions(entities, source_text=source_text)):
        copied = original.model_copy(deep=True)
        if clear:
            copied.spec_model = ""
        cleaned.append(copied)
    return cleaned
