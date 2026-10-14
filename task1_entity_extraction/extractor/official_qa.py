"""Partition evidence roles in raw notes. Never delete original files or infer lost bids."""
from collections import Counter
from hashlib import sha256
import re

from extractor.awarded_rate_tiers import _package_ids
from extractor.official_qa_money import (OFFICIAL_QA_RULE_VERSION, NAMES, PRICE, QUANTITY, TOTAL,
                                        award_records, base_money_evidence, multiply_item, normalize_missing, revise_base_entities)
from extractor.supplier_rules import _key, _scope, _usable
from extractor.total_award_rules import _literal_amount
from models.schemas import AnnouncementContent, ExtractedEntity, ExtractionResult

PLAN_HEADING = re.compile(r"采购需求|招标需求|采购预算|预算清单|预算(?:单价|金额|价)|最高限价|投标邀请|投标文件格式|报价(?:模板|范本)")
ACTUAL_HEADING = re.compile(r"(?:中标|成交)(?:明细表?|一览表?|清单|标的)|实际中标|合同价格清单")


def _role_heading(normalized):
    if ACTUAL_HEADING.search(normalized) and re.search(r"模板|范本|格式|(?:并非|不是|不属于|非实际)", normalized):
        return "planned_requirement"
    if PLAN_HEADING.search(normalized):
        return "planned_requirement"
    if ACTUAL_HEADING.search(normalized):
        return "awarded_item"
    return None


def _attachment_role(detail, text):
    """Every occurrence participates: a plan/award conflict stays unknown."""
    role, observations = "unknown", []
    name = _key(detail.product_name)
    if not name:
        return "unknown", []
    for line in (text or "").splitlines():
        normalized = _key(line)
        heading = _role_heading(normalized)
        if heading:
            role = heading
        if name in normalized:
            observations.append((role, line))
    roles = {r for r, _ in observations}
    if len(roles) != 1 or not roles or "unknown" in roles:
        return "unknown", observations
    return next(iter(roles)), observations


def _detail_source(detail, attachment_sources):
    source = attachment_sources.get(detail.source_file, "")
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        # A bounded verified PDF page must not classify other pages in that file.
        return source.get("pages", {}).get(detail.source_page.strip(), "")
    return ""


def _detail_money(detail, text):
    name = _key(detail.product_name)
    headers, role, found = None, "unknown", []
    for line in text.splitlines():
        normalized = _key(line)
        heading = _role_heading(normalized)
        if heading:
            role, headers = ("award" if heading == "awarded_item" else "plan"), None
        cells = [c.strip() for c in line.split("|")]
        if role != "award" or len(cells) < 2:
            continue
        if any(_key(c) in NAMES for c in cells):
            headers = [_key(c) for c in cells]
            continue
        if not headers or len(cells) != len(headers):
            continue
        names = [i for i, h in enumerate(headers) if h in NAMES]
        if len(names) == 1 and _key(cells[names[0]]) == name:
            found.append((headers, cells))
    if len(found) != 1:
        return detail
    headers, cells = found[0]
    if len(set(headers)) != len(headers):
        return detail
    for field, labels in (("brand", {"品牌", "货物品牌", "产品品牌"}),
                          ("spec_model", {"规格型号", "货物型号", "产品型号", "型号"})):
        indexes = [i for i, h in enumerate(headers) if h in labels]
        model_value = getattr(detail, field)
        if len(indexes) > 1 or (len(indexes) == 1 and _usable(model_value) and _usable(cells[indexes[0]])
                               and _key(model_value) != _key(cells[indexes[0]])):
            return detail
    packages = [i for i, h in enumerate(headers) if h in {"包号", "采购包", "合同包", "标包"}]
    if len(packages) > 1:
        return detail
    if packages and _usable(detail.package_name):
        value = cells[packages[0]]
        source_ids = _package_ids(value) or _package_ids("采购包" + value)
        model_ids = _package_ids(detail.package_name) or _package_ids("采购包" + detail.package_name)
        if not source_ids or source_ids != model_ids:
            return detail
    totals = [i for i, h in enumerate(headers) if TOTAL.fullmatch(h)]
    prices = [i for i, h in enumerate(headers) if PRICE.fullmatch(h)]
    quantities = [i for i, h in enumerate(headers) if QUANTITY.fullmatch(h)]
    result = detail.model_copy(deep=True)
    if len(totals) > 1:
        return detail
    if len(totals) == 1:
        i = totals[0]
        literal = _literal_amount(cells[i], TOTAL.fullmatch(headers[i])[1] or "")
        if literal:
            result.total_price = literal
            return result
        if _usable(cells[i]):
            return detail  # An unsupported explicit total vetoes arithmetic.
    if len(prices) == len(quantities) == 1:
        amount = multiply_item(cells[prices[0]], cells[quantities[0]], headers[prices[0]], headers[quantities[0]])
        if amount:
            result.total_price = amount
    return result


def _nonaward_packages(content):
    found = set()
    for line in content.html_text.splitlines():
        normalized = _key(line)
        if re.fullmatch(r"(?:第\d+包|采购包\d+|合同包\d+)(?:[:：])?(?:废标|流标|未成交|未中标|终止采购)(?:。)?", normalized):
            found |= _package_ids(normalized)
    return found


def _published_package_scope(title):
    text = _key(title)
    if not text.endswith(("中标公告", "成交公告")):
        return set()
    matches = list(re.finditer(r"(?:第)?(\d{1,3})包", text))
    if len(matches) < 2:
        return set()
    between = text[matches[0].start():matches[-1].end()]
    if re.sub(r"(?:第)?\d{1,3}包|[,、;]", "", between):
        return set()  # No inferred closed scope from ranges or qualifiers.
    return {"包:" + (m[1].lstrip("0") or "0") for m in matches}


def apply_official_qa(result: ExtractionResult, content: AnnouncementContent, *, attachment_sources=None) -> ExtractionResult:
    from extractor.summary_rules import build_summary_source  # avoid import cycle

    revised = result.model_copy(deep=True)
    source = build_summary_source(content.html_text, content.html_tables)
    original_names = revised.extra_info.get("_v12_original_base_names")
    originals = ([ExtractedEntity(product_name=name) for name in original_names]
                 if isinstance(original_names, list) and all(isinstance(n, str) for n in original_names) else None)
    revised.entities = revise_base_entities(revised.entities, source_text=source, originals=originals)
    if not revised.entities and not revised.detail_entities and "official_qa" not in revised.extra_info:
        return revised
    prior = revised.extra_info.get("official_qa", {})
    notes = {"rule_version": OFFICIAL_QA_RULE_VERSION,
             "excluded_base": list(prior.get("excluded_base", [])),
             "excluded_details": list(prior.get("excluded_details", [])),
             "unverified_detail_count": 0, "money_evidence": [],
             "note_visibility": "raw_json_only_not_existing_website_api"}
    body, _, valid = _scope(source)
    notes["disclosed_awards"] = award_records(body) if valid else []
    notes["announcement_source_sha256"] = sha256(source.encode()).hexdigest()
    nonaward = _nonaward_packages(content)
    published = _published_package_scope(content.title)
    kept_base = []
    for entity in revised.entities:
        ids = _package_ids(entity.product_name)
        if ids and ids <= nonaward:
            notes["excluded_base"].append({"classification": "nonaward_package", "entity": entity.model_dump()})
        else:
            kept_base.append(entity)
    revised.entities = kept_base
    kept = []
    for original in revised.detail_entities:
        detail = normalize_missing(original)
        ids = _package_ids(detail.package_name)
        if not ids and re.fullmatch(r"\d{1,3}", detail.package_name):
            ids = {"包:" + (detail.package_name.lstrip("0") or "0")}
        text = _detail_source(detail, attachment_sources or {})
        role, _ = _attachment_role(detail, text)
        classification = ("nonaward_package" if ids and ids <= nonaward else
                          "outside_announcement_award_scope" if published and ids and not ids.intersection(published) else
                          "planned_requirement" if role == "planned_requirement" else "")
        if classification:
            notes["excluded_details"].append({"classification": classification, "entity": detail.model_dump(),
                                              "source_text_sha256": sha256(text.encode()).hexdigest() if text else None})
            continue
        if role == "awarded_item":
            updated = _detail_money(detail, text)
            if updated.total_price != detail.total_price:
                notes["money_evidence"].append({"method": "awarded_row_literal_or_unit_times_quantity",
                                               "source_file": detail.source_file, "declared_source_page": detail.source_page,
                                               "product_name": detail.product_name, "before": detail.total_price, "after": updated.total_price,
                                               "source_text_sha256": sha256(text.encode()).hexdigest()})
            kept.append(updated)
        else:
            notes["unverified_detail_count"] += 1
            kept.append(detail)  # unread/ambiguous source is NOT declared awarded
    revised.detail_entities = kept
    if result.entities != revised.entities:
        notes["base_before_revision"] = [e.model_dump() for e in result.entities]
        notes["base_after_revision"] = [e.model_dump() for e in revised.entities]
    elif "base_before_revision" in prior:
        notes["base_before_revision"] = prior["base_before_revision"]
        notes["base_after_revision"] = prior["base_after_revision"]
    # Preserve previous derived-money provenance on idempotent reapplication.
    new_base_proofs = base_money_evidence(revised.entities, source)
    for proof in new_base_proofs:
        proof["source_text_sha256"] = notes["announcement_source_sha256"]
    proofs = list(prior.get("money_evidence", []))
    for proof in [*new_base_proofs, *notes["money_evidence"]]:
        if proof not in proofs:
            proofs.append(proof)
    notes["money_evidence"] = proofs
    revised.extra_info["official_qa"] = notes
    return revised
