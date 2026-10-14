"""Conservative spec-only cleanup, shared by the three extraction entrypoints."""

import re
import unicodedata

from models.schemas import DetailStatus, ExtractedEntity


SPEC_RULE_VERSION = "spec-service-source-v2"
_NUMBER = r"[0-9一二三四五六七八九十百]+"
_REFERENCE = re.compile(
    rf"(?:详见|见|参见|具体详见)(?:本)?(?:"
    rf"附件(?:{_NUMBER}|[A-Z])?|"
    rf"(?:招标|采购|磋商|谈判|询价|竞争性磋商)文件|"
    rf"(?:包{_NUMBER})?报价明细表)"
    rf"(?:第{_NUMBER}[章节条页]|采购需求|技术要求|采购要求|相关章节|内容|要求|说明)*"
)
_PROMISE = re.compile(
    r"按(?:照)?(?:招标|采购|磋商)(?:文件)?(?:要求|需求)(?:提供|执行|供货)?"
)
_PACKAGE = re.compile(rf"(?:第{_NUMBER}(?:包|标段)|(?:包|采购包|合同包|标段){_NUMBER}|[A-Z]包)")
_SPEC_LABEL = r"(?:货物)?(?:规格型号|型号|规格)"
_SERVICE_LABELS = {"服务范围", "服务要求", "服务时间", "服务期限", "服务标准"}
_NAME_LABELS = {"采购标的", "服务名称", "标的名称", "名称", "产品服务名称"}
_ATTRIBUTE_LABELS = {"规格", "型号", "规格型号", "货物型号", "货物规格型号", "软件版本", "版本"}
# Positive prose evidence is deliberately narrow. Unknown descriptions stay.
_SERVICE_PROSE = re.compile(
    r"^(?:包括|包含|支持|提供|通过|基于|依托|记录|重点完成|系统对|督导任务)"
    r".*(?:功能|查询|记录|管理|服务|任务|建设|数据|巡查|信息|系统|统计)"
    r"|按照(?:采购人|招标人)要求(?:进行)?服务"
)
# These are attribute clues, not a test for any English letter or number.
# False alarms mean retaining a value for review, never inferring a new spec.
_ATTRIBUTE_CLUE = re.compile(
    r"型号|规格|版本|定制|带宽|内存|容量|尺寸|重量|分辨率|频率|功率|电压|"
    r"材质|材料|全铜|不锈钢|防腐|结构(?!化)|包装|配置|采用|搭载|配备|CPU|GPU|"
    r"并发|吞吐|响应时间|时延|百兆|千兆|万兆|版|"
    r"\d+(?:\.\d+)?\s*(?:Mbps|Gbps|[KMGT]B|[KMGT]?Hz|kg|ml|mm|cm|寸|升)(?![A-Za-z])|"
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?[A-Za-z][A-Za-z0-9_.-]*|"
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z_-]*\d+[A-Za-z0-9_.-]*",
    re.IGNORECASE,
)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def _declared_model(value: str, source_text: str) -> bool:
    """Protect a literal model that happens to look like a procurement ID."""
    source = re.sub(r"[^\S\n]+", "", unicodedata.normalize("NFKC", source_text or ""))
    if re.search(rf"{_SPEC_LABEL}(?:[:|]|\n)\s*{re.escape(value)}(?=$|[\s|。；;，,()])", source):
        return True
    columns, width = [], 0
    for line in source.splitlines():
        cells = line.split("|")
        labels = [i for i, cell in enumerate(cells) if re.fullmatch(_SPEC_LABEL, cell)]
        if labels:
            columns, width = labels, len(cells)
        elif columns and len(cells) == width:
            if any(re.match(rf"{re.escape(value)}(?=$|[。；;，,()])", cells[i]) for i in columns):
                return True
        else:
            columns, width = [], 0
    return False


def _is_package_identifier(value: str, entity: ExtractedEntity, source_text: str) -> bool:
    if not _PACKAGE.fullmatch(value) or _declared_model(value, source_text):
        return False
    # Do not treat 包1/包十 as evidence from 包10/包十一. Global occurrence in
    # another procurement package does not establish this row's package.
    number_chars = "0-9A-Za-z一二三四五六七八九十百千万零〇两"
    same_label = rf"(?<![{number_chars}]){re.escape(value)}(?![{number_chars}])"
    for context in (entity.product_name, getattr(entity, "package_name", "")):
        if re.search(same_label, _compact(context)):
            return True
    return False


def _service_text_key(value: str) -> str:
    """Ignore presentation punctuation, not words or unrecognized prefixes."""
    return re.sub(r"[\s，,。；;：:、.()（）]+", "", unicodedata.normalize("NFKC", value or ""))


def _source_rows(source_text: str) -> list[dict]:
    """Include named spec rows too: another table can make identity ambiguous."""
    rows = []
    headers = None
    table_start = 0
    for line in unicodedata.normalize("NFKC", source_text or "").splitlines():
        if "|" not in line:
            headers = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        possible_headers = [re.sub(r"\([^()]*\)$", "", cell) for cell in cells]
        if _NAME_LABELS.intersection(possible_headers) and (_SERVICE_LABELS | _ATTRIBUTE_LABELS).intersection(possible_headers):
            headers = possible_headers if len(set(possible_headers)) == len(possible_headers) else None
            table_start = len(rows)
            continue
        if not headers or len(cells) != len(headers):
            if headers:
                # A dropped/truncated row cannot establish unique identity.
                for row in rows[table_start:]:
                    row["trusted"] = False
            headers = None
            continue
        mapped = dict(zip(headers, cells))
        names = {_service_text_key(mapped[label]) for label in headers if label in _NAME_LABELS and mapped[label]}
        if not names:
            continue
        service_values = [mapped[label] for label in headers if label in _SERVICE_LABELS and mapped[label]]
        scope = mapped.get("服务范围", "")
        head, separator, body = scope.partition(":")
        # Some service tables split a module name and its numbered function
        # title across two columns. Match their exact combination, not substring.
        if separator and re.match(r"\d+[.、]", head):
            names |= {name + _service_text_key(head) for name in names}
            service_values.append(body)
        rows.append({"names": names, "service_values": service_values,
                     "combined": "".join(mapped[label] for label in headers if label in _SERVICE_LABELS),
                     "declared_specs": [mapped[label] for label in headers if label in _ATTRIBUTE_LABELS],
                     "trusted": True})
    return rows


def _is_service_prose_copy(entity: ExtractedEntity, source_text: str, rows: list[dict]) -> bool:
    """Require unique identity, whole-cell provenance and positive prose clues."""
    spec = unicodedata.normalize("NFKC", entity.spec_model)
    if not spec or _ATTRIBUTE_CLUE.search(spec) or not _SERVICE_PROSE.search(spec):
        return False
    candidates = [row for row in rows if _service_text_key(entity.product_name) in row["names"]]
    if len(candidates) != 1 or _declared_model(_compact(spec), source_text):
        return False
    row = candidates[0]
    if not row["trusted"]:
        return False
    for declared in row["declared_specs"]:
        value = _compact(declared).rstrip("。；;，,")
        if value and value not in {"未公示", "无"} and not _REFERENCE.fullmatch(value) and not _PROMISE.fullmatch(value):
            return False
    key = _service_text_key(spec)
    return key in {_service_text_key(value) for value in [*row["service_values"], row["combined"]]}


def sanitize_spec_entities(
    entities: list[ExtractedEntity], *, source_text: str,
    detail_status: DetailStatus | None = None,
) -> list[ExtractedEntity]:
    """Copy rows, changing placeholders, known IDs and proven service copies.

    No absence check, blanket service deletion, length limit, catalog or gold.
    Run after existing deduplication to avoid merging distinct original rows.
    Missing/unreadable status is never inferred from a citation or partial text.
    """
    service_rows = _source_rows(source_text)
    cleaned = []
    for original in entities:
        entity = original.model_copy(deep=True)
        value = _compact(entity.spec_model).rstrip("。；;，,")
        reference = bool(_REFERENCE.fullmatch(value))
        if reference or _PROMISE.fullmatch(value) or value in {"未公示", "无"}:
            entity.spec_model = (
                "附件缺失" if reference and "附件" in value
                and detail_status in {"missing", "unreadable"} else ""
            )
        elif _is_package_identifier(value, entity, source_text):
            entity.spec_model = ""
        elif _is_service_prose_copy(entity, source_text, service_rows):
            entity.spec_model = ""
        cleaned.append(entity)
    return cleaned
