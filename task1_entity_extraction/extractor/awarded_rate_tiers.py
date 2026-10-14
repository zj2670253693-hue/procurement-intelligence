"""Protect source-proven awarded tiers, never expand or infer missing rows.

Supported evidence is an explicit awarded rate field in a uniquely labelled
package block. Unknown formats fail closed to the existing package collapse.
"""
from collections import Counter
from dataclasses import dataclass
import re

from extractor.supplier_rules import _SECTION, _fields, _key, _scope, _supplier
from models.schemas import ExtractedEntity


AWARDED_RATE_TIER_RULE_VERSION = "unique-awarded-rate-tier-v1"
_NUMBER = r"[0-9一二三四五六七八九十]+"
_PACKAGE_TOKEN = re.compile(rf"(?:第({_NUMBER})包|(?:采购包|合同包|包)({_NUMBER})|标包:?([A-Za-z0-9]+))")
_PACKAGE_LINE = re.compile(rf"(?:第{_NUMBER}包|(?:采购包|合同包|包){_NUMBER}|标包:?[A-Za-z0-9]+):?$")
_HEADING = re.compile(r"^[一二三四五六七八九十0-9]+[、.]")
_NEGATIVE = re.compile(r"预算|最高限价|招标需求|采购需求|模板|范本|未中标|未成交|候选人|投标报价|评审报价|报价排名")
_RATE_FIELDS = r"(?:中标|成交)(?:分档报价|报价|费率)"
_RATE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:万元|元)(?:/[^\d\s/;；|:()]+)+$")
_OWNER_DECLARATION = re.compile(r"供应商|中标人|成交人|竞得人|中标单位|成交单位")
_BLOCK_KEYS = {"项目名称", "采购项目名称", "供应商名称", "供应商地址", "中标金额", "成交金额",
               "中标(成交)金额", "中标价", "成交价", "服务名称", "标的名称", "产品服务名称"}
_METADATA_KEYS = {"项目名称", "采购项目名称", "项目编号", "品目", "行政区域", "公告时间", "公告标题",
                  "采购单位", "代理机构", "招标公告日期", "中标公告日期"}


@dataclass(frozen=True)
class _Tier:
    package: str
    supplier: str
    tier: str
    rate: str


def _package_ids(value: str) -> set[str]:
    ids = set()
    for match in _PACKAGE_TOKEN.finditer(_key(value)):
        number = match[1] or match[2]
        if number:
            chinese = {c: str(i) for i, c in enumerate("一二三四五六七八九十", 1)}
            ids.add("包:" + chinese.get(number, number.lstrip("0") or "0"))
        else:
            ids.add("标包:" + match[3].upper())
    return ids


def _split_rates(value: str) -> list[tuple[str, str]]:
    if _NEGATIVE.search(value):
        return []
    tiers = []
    for segment in re.split(r"[;；]", _key(value).rstrip("。.;；")):
        match = _RATE.search(segment)
        if not match:
            return []
        tier = segment[:match.start()].rstrip(":")
        if not tier or any(char in tier for char in "|=;；"):
            return []
        tiers.append((tier, match.group()))
    # Repeated/conflicting tier declarations remain ambiguity, not a set.
    if len(tiers) < 2 or len({tier for tier, _ in tiers}) != len(tiers):
        return []
    return tiers


def _supported_key(value: str) -> bool:
    return value in _BLOCK_KEYS or bool(re.fullmatch(_RATE_FIELDS, value))


def _supported_block(lines: list[str]) -> bool:
    for line in lines:
        value = _key(line)
        if _PACKAGE_LINE.fullmatch(value):
            continue
        parts = value.split(":", 1)
        if len(parts) != 2 or not parts[1] or not _supported_key(parts[0]):
            return False
    return True


def _tables_consistent(body: str, tables: list[list[list[str]]]) -> bool:
    body_lines = {_key(line) for line in body.splitlines() if line.strip()}
    for table in tables:
        if table and table[0] and _key(table[0][0]).rstrip(":") == "公告信息":
            if any(_key(cell) for cell in table[0][1:]):
                return False
            rows = []
            for row in table[1:]:
                if len(row) != 2:
                    return False
                # Known non-award metadata has its own role, but declarations
                # hidden in a value/unknown notes column cannot be discarded.
                if (_key(row[0]).rstrip(":") in _METADATA_KEYS
                        and not _OWNER_DECLARATION.search(_key(row[1]))):
                    continue
                rows.append(row)
        else:
            rows = table
        # Only complete repeated key/value witnesses are supported here.
        # Unknown award matrices cannot silently conceal competing owners.
        if any(len(row) != 2 or not all(row) or not _supported_key(_key(row[0]).rstrip(":"))
               or _key(row[0]).rstrip(":") + ":" + _key(row[1]) not in body_lines for row in rows):
            return False
    return True


def _award_sections(body: str) -> list[list[str]]:
    """Keep the complete award region, including disqualifying tail notes.

    The supplier-fill parser intentionally stops at bid/candidate text. That
    behaviour cannot prove awarded rates: discarding a qualifier would leave
    an apparently positive prefix. Only supported award/item headings change
    a region; unknown numbered headings stay as potentially negative context.
    """
    sections, collecting, lines = [], False, []
    for raw in body.splitlines():
        normalized = _key(raw)
        heading = _SECTION.fullmatch(normalized)
        title = heading[1].rstrip(":") if heading else ""
        award_heading = bool(re.fullmatch(r"(?:中标(?:\(成交\))?|成交)信息", title))
        if award_heading or title == "主要标的信息":
            if collecting:
                sections.append(lines)
            collecting = award_heading
            lines = []
        elif collecting and normalized:
            lines.append(raw.strip())
    if collecting:
        sections.append(lines)
    return sections


def _evidence(source: str) -> list[_Tier]:
    body, tables, valid = _scope(source)
    # Serialized HTML requires the existing project/scope consistency proof.
    # It is not safe to borrow a rate that appears only in a table/attachment.
    if not valid and (tables or re.search(r"(?m)^[ \t]*---[^\n]*---[ \t]*$", source)):
        return []
    if not _tables_consistent(body, tables):
        return []
    sections = _award_sections(body)
    if not sections:
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        # Minimal unsectioned API inputs are accepted only with their own
        # explicit awarded-tier label, project and unique package/supplier.
        if (any(_HEADING.match(_key(line)) for line in lines) or _NEGATIVE.search(body)
                or not any(re.match(r"(?:采购)?项目名称[:：]", line) for line in lines)
                or not _fields(lines, r"(?:中标|成交)分档报价")):
            return []
        sections = [lines]
    blocks = []
    for lines in sections:
        current, package = [], ""
        labelled = any(_PACKAGE_LINE.fullmatch(_key(line)) for line in lines)
        if not labelled:
            ids = _package_ids("\n".join(lines)) or _package_ids(body)
            if len(ids) == 1:
                blocks.append((next(iter(ids)), lines))
            continue
        prefix_end = next(i for i, line in enumerate(lines) if _PACKAGE_LINE.fullmatch(_key(line)))
        prefix = lines[:prefix_end]
        # The pre-package context must not disappear. A template/ceiling or
        # unowned supplier/rate there cannot become evidence for every block.
        if any(_key(line).strip(":|()") for line in prefix):
            continue
        for line in lines:
            if _PACKAGE_LINE.fullmatch(_key(line)):
                if package:
                    blocks.append((package, current))
                package = next(iter(_package_ids(line)))
                current = [line]
            elif package:
                current.append(line)
        if package:
            blocks.append((package, current))
    counts = Counter(package for package, _ in blocks)
    evidence = []
    for package, lines in blocks:
        block_text = "\n".join(lines)
        if (counts[package] != 1 or _package_ids(block_text) - {package}
                or _NEGATIVE.search(block_text) or not _supported_block(lines)):
            continue
        suppliers = _fields(lines, "供应商名称")
        values = _fields(lines, _RATE_FIELDS)
        if len(suppliers) != 1 or len(values) != 1 or not _supplier(suppliers[0]):
            continue
        tiers = _split_rates(values[0])
        evidence.extend(_Tier(package, _key(suppliers[0]), tier, rate) for tier, rate in tiers)
    return evidence


def keep_proven_rate_tiers(
    entities: list[ExtractedEntity], *, source_text: str,
    collapsed_packages: list[ExtractedEntity],
) -> list[ExtractedEntity]:
    """Replace only uniquely matched package collapses with original tier rows.

    No row/field is synthesized; unknown/missing/conflicting identity keeps
    the previous collapse. Proven rate fields do not prove per-tier totals;
    clear those rather than repeat a whole package award on each rate row.
    Ordinary packages follow their unchanged path.
    """
    evidence = _evidence(source_text)
    protected = []
    for index, row in enumerate(entities):
        packages = _package_ids(row.product_name)
        matches = [item for item in evidence if packages == {item.package}
                   and _key(row.brand) == item.supplier
                   and _key(row.spec_model) == item.tier
                   and item.tier in _key(row.product_name)
                   and _key(row.unit_price) == item.rate]
        if len(matches) == 1:
            protected.append((index, matches[0].package, matches[0].supplier))
    owners = {(package, supplier) for _, package, supplier in protected}
    collapsed_owners = [
        (next(iter(ids)), _key(row.brand)) if len(ids := _package_ids(row.product_name)) == 1 else None
        for row in collapsed_packages
    ]
    # Do not rely on the old positional collapse as identity evidence: it
    # must contain exactly one matching package+supplier to be replaced.
    owners = {owner for owner in owners if collapsed_owners.count(owner) == 1}
    if not owners:
        return collapsed_packages
    kept = [entities[index].model_copy(deep=True, update={"total_price": ""})
            for index, package, supplier in protected if (package, supplier) in owners]
    return kept + [row for row, owner in zip(collapsed_packages, collapsed_owners) if owner not in owners]
