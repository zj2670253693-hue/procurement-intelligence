"""Independent price-unit projection; frozen inputs are never changed."""
from copy import deepcopy
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
import os

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get('TASK1_STANDARD_BASE',str(HERE)))
sys.path.insert(0, str(BASE))
import standard_export as old
from source_name_join import cosmetic_name_key, select_name_metadata

BARE_NUMBER = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
PRICE_HEADERS = {
    "unit_price": re.compile(r"(?:货物|产品|服务)?单价\((元|万元|亿元)\)"),
    "total_price": re.compile(r"(?:总价|总金额|合价|金额)\((元|万元|亿元)\)"),
}


def explicit_column_price(raw, source, header, field):
    header_key = re.sub(r"\s+", "", unicodedata.normalize("NFKC", header))
    match = PRICE_HEADERS[field].fullmatch(header_key)
    raw_text = old.literal(raw)
    if not match or not BARE_NUMBER.fullmatch(raw_text):
        return ""
    unit = match[1]
    # A conflicting inline scale must not borrow this column's unit.
    inline_units = re.findall(r"亿元|万元|元", str(source))
    if inline_units and (len(inline_units) != 1 or inline_units[0] != unit):
        return ""
    proposed = old.money_wan(raw_text, declared_unit=unit)
    disclosed = old.money_wan(source, declared_unit=unit)
    return proposed if proposed and disclosed and Decimal(proposed) == Decimal(disclosed) else ""


def load_source_prices(aid):
    old.require(re.fullmatch(r"[A-Za-z0-9_-]+", aid), "unsafe announcement ID")
    old.block_network()
    html = (old.SOURCE_ROOT / (aid + ".html")).read_bytes()
    prior=Path(os.environ.get('TASK1_STANDARD_R9_DIR',str(BASE/'candidate-r9-name-final')))
    expected = json.loads((prior / 'verification.json').read_text(encoding="utf-8"))
    digest = hashlib.sha256(html).hexdigest()
    old.require(expected["input_fingerprints"][str((old.SOURCE_ROOT / (aid + ".html")).resolve())] == digest,
                "frozen source drift")
    source = old.parse_source(html, aid)
    from data_loader.html_parser import HTMLAnnouncementParser
    from extractor.summary_rules import build_summary_source
    from extractor.supplier_rules import _scope, _key
    from extractor.official_qa_money import _matrix_parts
    content = HTMLAnnouncementParser().parse_html(html.decode("utf-8-sig"), aid)
    _, tables, valid = _scope(build_summary_source(content.html_text, content.html_tables))
    old.require(valid, "source no longer passes frozen source gates")
    parts = [part for table in tables for part in _matrix_parts(table)]
    prices = {}
    for binding in source["item_bindings"]:
        proof = binding.get("source_identity", {})
        if proof.get("proved") is not True:
            continue
        ti, ri = proof["source_table"], proof["source_row"]
        table, row = parts[ti], parts[ti][ri]
        columns = {}
        for field, pattern in PRICE_HEADERS.items():
            indexes = [i for i, h in enumerate(table[0]) if pattern.fullmatch(_key(h))]
            if len(indexes) == 1:
                i = indexes[0]
                columns[field] = {"header": table[0][i], "raw_value": row[i], "cell": i}
        prices[(ti, ri)] = columns
    return {"source": source, "prices": prices, "source_sha256": digest}


def project_prices(bundle, records, joins, source_loader):
    revised, changes = deepcopy(bundle), []
    raw_by_id = {r["announcement_id"]: r for r in records}
    old.require(len(raw_by_id) == len(records), "duplicate raw announcement")
    positions, occurrence = {}, Counter()
    for row in revised["tables"]["标的表"]:
        aid = row["原公告ID"]
        positions[(aid, occurrence[aid])] = row
        occurrence[aid] += 1
    old.require(len({(j["announcement_id"], j["entity_index"]) for j in joins}) == len(joins), "duplicate join")
    cache = {}
    for join in joins:
        aid, index = join["announcement_id"], join["entity_index"]
        raw = raw_by_id[aid]["entities"][index]
        row = positions[(aid, index)]
        old.require(row["产品服务名称"] == old.literal(raw.get("product_name")), "item order/name drift")
        if aid not in cache:
            cache[aid] = source_loader(aid)
        loaded = cache[aid]
        source = loaded["source"]
        old.require(source["classification"] == "award_candidate", "intention source cannot gain prices")
        counts = Counter(cosmetic_name_key(e.get("product_name")) for e in raw_by_id[aid]["entities"])
        accepted = {a["package_key"] for a in source["awards"] if old.literal(a.get("supplier")) and a["package_key"]}
        match = select_name_metadata({f: old.literal(raw.get(f)) for f in ("product_name", "brand", "spec_model")},
                                     source["item_bindings"], counts, accepted)
        old.require(match and match["source_identity"] == join["source_identity"], "source identity no longer unique/proved")
        proof = match["source_identity"]
        columns = loaded["prices"].get((proof["source_table"], proof["source_row"]), {})
        for field, label in (("unit_price", "单价"), ("total_price", "总价")):
            cell = columns.get(field)
            if row[label] or not cell:
                continue
            value = explicit_column_price(raw.get(field), cell["raw_value"], cell["header"], field)
            if not value:
                continue
            row[label] = value
            warning = label + "非明确金额或单位不明；原文保留在审计JSON"
            row["备注"] = re.sub(r"；{2,}", "；", row["备注"].replace(warning, "")).strip("；")
            suffix = "；价格按本字段原文列单位换算"
            if not row["证据来源"].endswith(suffix):
                row["证据来源"] += suffix
            changes.append({"announcement_id": aid, "entity_index": index, "row": row["序号"],
                            "product_name": raw["product_name"], "field": field, "before": "", "after": value,
                            "raw_model_value": raw.get(field), "source_sha256": loaded["source_sha256"],
                            "source_table": proof["source_table"], "source_row": proof["source_row"], **cell,
                            "method": "explicit_own_column_unit_and_equal_scalar_value", "computed_by_multiplication": False})
    return revised, changes
