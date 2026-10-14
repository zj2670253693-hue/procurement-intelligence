"""Whole numeric-field citations only; never infer prices or quantities."""

import re
import unicodedata

from models.schemas import DetailStatus, ExtractedEntity


NUMERIC_REFERENCE_RULE_VERSION = "numeric-reference-v1"
_NUMBER = r"[0-9一二三四五六七八九十百]+"
_REFERENCE = re.compile(
    rf"(?:详见|见|参见|具体详见)(?:本)?(?:"
    rf"附件(?:{_NUMBER}|[A-Z])?|"
    rf"(?:招标|采购|磋商|谈判|询价|竞争性磋商)文件|"
    rf"(?:包{_NUMBER})?报价明细表)"
    rf"(?:第{_NUMBER}[章节条页]|采购需求|技术要求|采购要求|相关章节|内容|要求|说明)*"
)
_PROMISE_VALUES = {"按招标要求提供", "按采购要求提供", "按采购文件要求"}


def sanitize_numeric_reference_entities(
    entities: list[ExtractedEntity],
    *,
    detail_status: DetailStatus | None = None,
) -> list[ExtractedEntity]:
    """Copy rows in order, changing only entire unit-price/quantity placeholders.

    Run after existing deduplication: rejected values must not cause a second
    merge. Readable but partial attachments and unknown status are not missing.
    """
    cleaned = []
    for original in entities:
        entity = original.model_copy(deep=True)
        for field in ("unit_price", "quantity"):
            key = re.sub(
                r"\s+", "", unicodedata.normalize("NFKC", getattr(entity, field) or ""),
            ).rstrip("。；;，,")
            if not _REFERENCE.fullmatch(key) and key not in _PROMISE_VALUES:
                continue
            replacement = (
                "附件缺失"
                if "附件" in key and detail_status in {"missing", "unreadable"}
                else ""
            )
            setattr(entity, field, replacement)
        cleaned.append(entity)
    return cleaned
