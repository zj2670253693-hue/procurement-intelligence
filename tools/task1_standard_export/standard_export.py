"""Offline three-view projection. Never overwrite extraction or infer identities."""
from collections import Counter
from copy import deepcopy
import csv
from decimal import Decimal, ROUND_HALF_UP, localcontext
import io
import hashlib
from pathlib import Path
import re
import sys
import json
import socket
import os
import unicodedata

FIELDS = ("product_name", "category", "brand", "spec_model", "quantity", "unit_price", "total_price")
LABELS = ("产品服务名称", "品目", "品牌", "规格型号", "数量", "单价", "总价")
HEADERS = {
    "总表": ["序号", "URL", "UUID", "标段/合同包", "项目名称", "采购单位", "中标供应商", "中标金额", "备注", "原公告ID"],
    "标的表": ["序号", "UUID", "标段/合同包", *LABELS, "类别", "证据来源", "备注", "原公告ID"],
    "投标人表": ["序号", "UUID", "标段/合同包", "名称", "类别", "通过审查", "报价（万元）", "得分", "未中标原因", "备注", "原公告ID"],
}
OPTIONAL = {"总表": {"序号", "URL", "备注", "原公告ID"},
            "标的表": {"序号", "类别", "证据来源", "备注", "原公告ID"},
            "投标人表": {"序号", "得分", "未中标原因", "备注", "原公告ID"}}
MISSING = {"", "/", "-", "无", "未公示", "未公布", "未提供", "详见附件", "见附件", "null", "n/a"}


def require(value, message):
    if not value:
        raise ValueError(message)


def literal(value):
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in MISSING else text


def money_wan(value, *, round_two=False, declared_unit=""):
    text = unicodedata.normalize("NFKC", literal(value))
    text = re.sub(r"\((亿元|万元|元)\)", r"\1", text)
    match = re.fullmatch(r"(?:人民币)?[￥¥]?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(亿元|万元|元)?(?:\s*\(人民币\))?", text)
    if not match or len(text) > 96:
        return ""
    unit = match[2] or declared_unit
    if unit not in {"元", "万元", "亿元"}:
        return ""
    with localcontext() as ctx:
        ctx.prec = 100
        amount = Decimal(match[1].replace(",", "")).scaleb({"元": -4, "万元": 0, "亿元": 4}[unit])
        if round_two:
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format(amount, "f")


def classify(title, text, *, concrete_transaction=False):
    """Require title AND body intention evidence; generic discount is no veto."""
    if re.search(r"入围|框架协议", title) and re.search(r"尚无实际成交|仅征集", text):
        return "intention_only"
    if re.search(r"第二阶段|订单成交|实际成交|合同成交", title):
        return "award_candidate"
    if "入围" in title and re.search(r"入围信息|入围供应商|入围单价|第一阶段", text):
        return "intention_only"
    if "框架协议" in title and re.search(r"尚无实际成交|仅征集|第一阶段|入围供应商", text):
        return "intention_only"
    return "award_candidate"


def project(records, sources):
    ids = [r.get("announcement_id", "") for r in records]
    require(all(re.fullmatch(r"[A-Za-z0-9_-]+", a) for a in ids), "invalid announcement ID")
    require(len(ids) == len(set(ids)), "duplicate announcement ID")
    require(set(ids) <= set(sources), "missing source")
    tables = {name: [] for name in HEADERS}
    mappings, audits, advanced, name_metadata_joins = [], [], [], []
    for raw in records:
        aid = raw["announcement_id"]; s = sources[aid]
        package_rows = sorted(s.get("packages", []), key=lambda p: p["key"])
        require(len({p["key"] for p in package_rows}) == len(package_rows), "duplicate package identity")
        mapping = {p["key"]: {"UUID": f"{aid}_{i:02d}", "标段/合同包": p["label"]}
                   for i, p in enumerate(package_rows, 1)}
        for p in package_rows:
            mappings.append({"announcement_id": aid, "project_name": s["project_name"],
                             "package_key": p["key"], **mapping[p["key"]]})

        def identity(package_key=""):
            if package_key:
                require(package_key in mapping, "unregistered package mapping")
                return {**mapping[package_key], "备注": "", "原公告ID": aid}
            return {"UUID": aid, "标段/合同包": "未分包" if s.get("divided") is False else "",
                    "备注": "" if s.get("divided") is False else "采购包对应关系未知", "原公告ID": aid}

        classification = s["classification"]
        advanced.append({"announcement_id": aid, "classification": classification,
                         "raw_result": deepcopy(raw), "source_projection": deepcopy(s)})
        audit = {"announcement_id": aid, "classification": classification, "source_sha256": s["source_sha256"],
                 "excluded_base_rows": len(raw.get("entities", [])) if classification == "intention_only" else 0,
                 "unverified_detail_rows_not_promoted": len(raw.get("detail_entities", [])),
                 "warnings": list(s.get("warnings", [])), "invalid_money_fields": [], "unbound_base_rows": 0}
        audits.append(audit)
        if classification == "intention_only":
            continue
        def audit_money(value, label, scope, *, declared_unit=""):
            converted = money_wan(value, round_two=True, declared_unit=declared_unit)
            if literal(value) and not converted:
                audit["invalid_money_fields"].append({"field": label, "scope": scope, "source_value": value})
            return converted
        awards = s.get("awards", []) or [{"supplier": "", "amount": ""}]
        for award in awards:
            tables["总表"].append({**identity(award.get("package_key", "")), "URL": literal(s.get("url")),
                                   "项目名称": literal(s["project_name"]), "采购单位": literal(s["procurement_unit"]),
                                   "中标供应商": literal(award.get("supplier")),
                                   "中标金额": audit_money(award.get("amount"), "中标金额", "award",
                                                         declared_unit=award.get("amount_unit", ""))})
        bindings = s.get("item_bindings", [])
        names = Counter(e.get("product_name", "") for e in raw.get("entities", []))
        from source_name_join import cosmetic_name_key, select_name_metadata
        cosmetic_names = Counter(cosmetic_name_key(e.get("product_name")) for e in raw.get("entities", []))
        awarded_packages = {a.get("package_key", "") for a in s.get("awards", [])
                            if literal(a.get("supplier")) and a.get("package_key") in mapping}
        for entity_index, e in enumerate(raw.get("entities", [])):
            matches = [b for b in bindings if b["product_name"] == e.get("product_name")]
            binding = matches[0] if len(matches) == 1 and names[e.get("product_name")] == 1 else {}
            metadata_binding = binding
            if not binding:
                metadata_binding = select_name_metadata(
                    {field: literal(e.get(field)) for field in ("product_name", "brand", "spec_model")},
                    bindings, cosmetic_names, awarded_packages,
                )
                if metadata_binding:
                    name_metadata_joins.append({"announcement_id": aid, "entity_index": entity_index,
                                                "raw_product_name": e.get("product_name"),
                                                "source_product_name": metadata_binding["product_name"],
                                                "package_key": metadata_binding["package_key"],
                                                "source_identity": deepcopy(metadata_binding["source_identity"]),
                                                "source_evidence": metadata_binding["evidence"],
                                                "method": "unique_whitespace_round_parentheses_metadata_only",
                                                "new_money_binding_used": False})
            item = {**identity(metadata_binding.get("package_key", "")),
                    **{label: literal(e.get(field)) for field, label in zip(FIELDS, LABELS)},
                    "类别": "中标公示候选", "证据来源": metadata_binding.get("evidence", "V12基础结果；未逐项核实")
                    + ("；仅空白/圆括号名称对齐，未据此改写七字段" if metadata_binding and not binding else "")}
            if not binding:
                audit["unbound_base_rows"] += 1
            for field, label in (("unit_price", "单价"), ("total_price", "总价")):
                item[label] = money_wan(e.get(field), declared_unit=binding.get(field + "_unit", ""))
                if literal(e.get(field)) and not item[label]:
                    audit["invalid_money_fields"].append({"product_name": e.get("product_name"), "field": field,
                                                          "source_value": e.get(field)})
                    item["备注"] += ("；" if item["备注"] else "") + label + "非明确金额或单位不明；原文保留在审计JSON"
            tables["标的表"].append(item)
        for b in s.get("bidders", []):
            tables["投标人表"].append({**identity(b.get("package_key", "")), "名称": literal(b.get("name")),
                                       "类别": literal(b.get("category")), "通过审查": literal(b.get("review")),
                                       "报价（万元）": audit_money(b.get("quote"), "报价（万元）", "bidder",
                                                               declared_unit=b.get("quote_unit", "")),
                                       "得分": literal(b.get("score")), "未中标原因": literal(b.get("reason"))})
    for rows in tables.values():
        for i, row in enumerate(rows, 1): row["序号"] = i
    return {"tables": tables, "headers": HEADERS, "optional_headers": {k: sorted(v) for k, v in OPTIONAL.items()},
            "uuid_map": mappings, "audit": audits, "advanced": advanced,
            "name_metadata_joins": name_metadata_joins,
            "new_model_calls": 0, "database_accessed": False, "candidate_only": True}


def csv_bytes(sheet, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HEADERS[sheet])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "'" + v if isinstance(v, str) and re.match(r"[=+@-]", v) else v for k, v in row.items()})
    return stream.getvalue().encode("utf-8-sig")


BACKEND = Path(os.environ.get('TASK1_STANDARD_BACKEND',str(Path(__file__).resolve().parents[2]/'task1_entity_extraction')))


def package_token(value):
    text = re.sub(r"\s+", "", str(value)).rstrip(":：")
    numeric = r"[0-9一二三四五六七八九十]+"
    match = re.fullmatch(rf"(?:第)?({numeric})(包|标段)|(?:采购包|合同包|包号|包)({numeric})|([A-Za-z])包", text)
    if not match:
        return None
    number = match[1] or match[3]
    if number:
        digits = {c: str(i) for i, c in enumerate("一二三四五六七八九十", 1)}
        number = digits.get(number, number.lstrip("0") or "0")
        key = ("标段:" if match[2] == "标段" else "包:") + number
    else:
        key = "标包:" + match[4].upper()
    return {"key": key, "label": str(value).strip().rstrip(":：")}


def parse_source(html, announcement_id):
    """Conservative projection of already parsed public HTML, not a model call."""
    sys.path.insert(0, str(BACKEND))
    from data_loader.html_parser import HTMLAnnouncementParser
    from extractor.summary_rules import build_summary_source
    from extractor.official_qa_money import award_records, item_catalog, _matrix_parts, NAMES
    from extractor.supplier_rules import _scope, _key, _name
    require(Path(sys.modules[HTMLAnnouncementParser.__module__].__file__).resolve().is_relative_to(BACKEND.resolve()),
            "wrong frozen HTML parser")
    content = HTMLAnnouncementParser().parse_html(html.decode("utf-8-sig"), announcement_id)
    source = build_summary_source(content.html_text, content.html_tables)
    body, source_tables, valid = _scope(source)
    warnings = []
    def field(names):
        values = {literal(row[i + 1]) for table in content.html_tables for row in table
                  for i, cell in enumerate(row[:-1]) if _key(cell) in names and literal(row[i + 1])}
        if len(values) == 1:
            return next(iter(values))
        warnings.append("缺失或冲突字段：" + "/".join(sorted(names)))
        return ""
    project_name = field({"采购项目名称"})
    procurement_unit = field({"采购单位"})
    registry = {}
    for table in content.html_tables:
        for row in table:
            for cell in row:
                package = package_token(cell)
                if package:
                    prior = registry.get(package["key"])
                    if prior is None or package["label"] < prior["label"]:
                        registry[package["key"]] = package
    for line in body.splitlines():
        package = package_token(line)
        if package and package["key"] not in registry:
            registry[package["key"]] = package
    catalog = item_catalog(source) if valid else []
    owner_packages = []
    for row in catalog:
        parts = re.split(r"[:：]", row.owner, maxsplit=1)
        if len(parts) != 2 or not literal(parts[1]):
            continue
        prefix = re.match(r"^(?:第[0-9一二三四五六七八九十]+(?:包|标段)|(?:采购包|合同包|包)[0-9一二三四五六七八九十]+|[A-Za-z]包)", parts[0].strip())
        package = package_token(prefix[0]) if prefix else None
        if not package:
            continue
        # This is an explicit package+supplier declaration in the actual item
        # region, not an ordering inference. Preserve its full source caption.
        package["label"] = parts[0].strip()
        registry[package["key"]] = package
        owner_packages.append((package["key"], literal(parts[1])))
    awards = award_records(body) if valid else []
    matrix_rejections = []
    if valid:
        from source_awards import parse_award_tables
        matrix = parse_award_tables(html)
        # Do not concatenate text mirrors with table rows. New matrices are a
        # fallback only when the original complete labelled-source gate found none.
        if not awards and matrix["awards"]:
            awards = matrix["awards"]
            for p in matrix["packages"]: registry[p["key"]] = p
        if matrix["rejected"]:
            matrix_rejections = matrix["rejected"]
            warnings.append("成交表部分行未满足完整金额/结构条件；审计保留拒绝原因")
    # A canonical package without an exact source label remains unresolved.
    for award in awards:
        key = award.pop("package", award.get("package_key", ""))
        exact = {p for p, supplier in owner_packages if _key(supplier) == _key(award["supplier"])}
        if not key and len(exact) == 1:
            key = next(iter(exact))
        elif key and exact and exact != {key}:
            key = ""  # A source disagreement is not resolved by preference.
        award["package_key"] = key if key in registry else ""
    from source_item_packages import recover_item_contexts
    recovered_contexts = recover_item_contexts(body, source_tables, catalog, registry, awards) if valid else {}
    # Preserve the full literal cell separately: row.name already went through
    # frozen NFKC/citation cleanup, which is NOT allowed for the new fallback.
    catalog_tables = [part for table in source_tables for part in _matrix_parts(table)]
    bindings = []
    for row in catalog:
        if not row.proved or row.package_object:
            continue
        packages = [p for p in row.packages if p in registry]
        recovery = recovered_contexts.get((row.table_index, row.row_index), {})
        table = catalog_tables[row.table_index]
        name_columns = [i for i, h in enumerate(table[0]) if _key(h) in NAMES]
        complete_name = table[row.row_index][name_columns[0]] if len(name_columns) == 1 else ""
        bindings.append({"product_name": row.name, "package_key": packages[0] if len(packages) == 1 else recovery.get("package_key", ""),
                         "source_identity": {"proved": True, "source_table": row.table_index, "source_row": row.row_index,
                                             "complete_name": complete_name,
                                             "brand": literal(row.brand), "spec_model": literal(row.spec)},
                         "unit_price_unit": re.search(r"亿元|万元|元", row.price_header)[0]
                         if re.search(r"亿元|万元|元", row.price_header) else "",
                         "total_price_unit": re.search(r"亿元|万元|元", row.total_header)[0]
                         if re.search(r"亿元|万元|元", row.total_header) else "",
                         "evidence": f"公告主要标的信息表{row.table_index}行{row.row_index}"
                         + ("；唯一成交包标题上下文" if recovery else ""),
                         **({"package_context_evidence": recovery} if recovery else {})})
    membership = {}
    for row in catalog:
        parts = re.split(r"[:：]", row.owner, maxsplit=1)
        if len(parts) != 2:
            continue
        matches = {key for key, company in owner_packages if _key(company) == _key(parts[1])
                   and registry[key]["label"] == parts[0].strip()}
        if len(matches) != 1:
            continue
        for name in re.split(r"[;；]", row.name):
            if literal(name): membership.setdefault(name, set()).update(matches)
    already_bound = {b["product_name"] for b in bindings}
    for name, keys in membership.items():
        if name not in already_bound and len(keys) == 1:
            bindings.append({"product_name": name, "package_key": next(iter(keys)),
                             "evidence": "公告明示包与组合标的名称；仅证明归属，不按组合位置配对价格"})
    concrete = any(r.proved and not r.package_object and literal(r.quantity)
                   and (money_wan(r.price, declared_unit=(re.search(r"亿元|万元|元", r.price_header)[0]
                                                        if re.search(r"亿元|万元|元", r.price_header) else ""))
                        or money_wan(r.total, declared_unit=(re.search(r"亿元|万元|元", r.total_header)[0]
                                                            if re.search(r"亿元|万元|元", r.total_header) else "")))
                   for r in catalog)
    classification = classify(content.title, body if valid else content.html_text, concrete_transaction=concrete)
    bidders = []
    winners = {(a["supplier"], a["package_key"]) for a in awards}
    for ti, table in enumerate(content.html_tables):
        if len(table) < 2:
            continue
        header = [_key(c) for c in table[0]]
        names = [i for i, h in enumerate(header) if h in {"供应商", "供应商名称", "投标人名称", "投标单位名称", "响应供应商名称", "投标单位"}]
        quote = [i for i, h in enumerate(header) if re.fullmatch(r"(?:投标|最终|最后|响应)?报价(?:\((?:元|万元|亿元)\))?", h)]
        review = [i for i, h in enumerate(header) if h in {"资格审查", "资格性审查", "符合性审查", "通过审查", "审查结果", "评审结果"}]
        score = [i for i, h in enumerate(header) if h in {"综合得分", "总得分", "得分", "评审总得分"}]
        packages = [i for i, h in enumerate(header) if h in {"包号", "标段", "标段/合同包", "采购包", "合同包"}]
        if (len(names) != 1 or not (quote or review or score) or max(len(quote), len(score), len(packages)) > 1
                or len(review) > 2 or len(set(header)) != len(header)):
            continue
        for ri, row in enumerate(table[1:], 1):
            if len(row) != len(header):
                warnings.append(f"投标表{ti}行{ri}为异构/合并单元格，未按位置补齐")
                continue
            name = literal(row[names[0]])
            if not name:
                continue
            review_values = [literal(row[i]) for i in review]
            review_value = (review_values[0] if len(review_values) == 1 else
                            "通过" if review_values and all(_key(v) == "通过" for v in review_values) else
                            "未通过：" + "；".join(header[i] + "=" + literal(row[i]) for i in review
                                               if _key(literal(row[i])).startswith("未通过"))
                            if any(_key(v).startswith("未通过") for v in review_values) else "")
            review_key = _key(review_value)
            if review_value and not (review_key.startswith("未通过") or review_key == "通过"):
                warnings.append(f"投标表{ti}行{ri}审查值无法规范化，保留原文")
            package = package_token(row[packages[0]]) if packages else None
            key = package["key"] if package and package["key"] in registry else ""
            winner_proved = (name, key) in winners and (bool(key) or not registry and len(awards) == 1)
            category = ("其他" if review_key.startswith("未通过") else "中标人" if winner_proved
                        else "" if not key and registry and any(a["supplier"] == name for a in awards) else "参与人")
            quote_header = header[quote[0]] if quote else ""
            unit = re.search(r"亿元|万元|元", quote_header)
            bidders.append({"name": name, "category": category, "review": review_value,
                            "quote": literal(row[quote[0]]) if quote else "", "quote_unit": unit[0] if unit else "",
                            "score": literal(row[score[0]]) if score else "", "reason": "", "package_key": key,
                            "review_source": {header[i]: literal(row[i]) for i in review},
                            "evidence": f"公告投标表{ti}行{ri}"})
    known_names = {(b["name"], b["package_key"]) for b in bidders}
    for a in awards:
        if (a["supplier"], a["package_key"]) not in known_names:
            bidders.append({"name": a["supplier"], "category": "中标人", "package_key": a["package_key"],
                            "review": "", "quote": "", "score": "", "reason": "",
                            "evidence": "公告明确成交供应商；审查与报价未据此推断"})
    divided = False if re.search(r"未分包|不分包|不划分标段|不分标段", body) and not registry else True if registry else None
    return {"project_name": project_name, "procurement_unit": procurement_unit, "divided": divided,
            "packages": list(registry.values()), "classification": classification, "awards": awards,
            "bidders": bidders, "item_bindings": bindings, "source_sha256": hashlib.sha256(html).hexdigest(),
            "warnings": warnings, "rejected_award_rows": matrix_rejections, "url": ""}


HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get('TASK1_PRIVATE_WORKSPACE',str(HERE.parents[1])))
RAW_PATH = WORK / "task1-official-qa-candidate-v12-20261012/verification/replay-run6/cached_v12_1038.json"
RAW_SHA = "0dbef260171b60338a8a502972180d38957d31b1bcd97228f68cd6e6a5abf49f"
SOURCE_ROOT = Path(os.environ.get('TASK1_STANDARD_HTML_ROOT',str(WORK / 'task1-release-validation-20261012-v12/stage-final-r2/backend/sample_data')))


def sha_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def block_network():
    def blocked(*args, **kwargs): raise RuntimeError("v13_offline_forbids_network")
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = blocked
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["OPENAI_API_KEY"] = os.environ["DEEPSEEK_API_KEY"] = ""


def run_candidate(destination, *, raw_path=RAW_PATH):
    require(sha_file(raw_path) == RAW_SHA, "frozen raw hash mismatch")
    destination = Path(destination)
    require(not destination.exists() and not destination.is_symlink(), "output already exists")
    require(not any(p.is_symlink() for p in destination.parents), "symlink output ancestor")
    block_network()
    protected = {str(p.resolve()): sha_file(p) for p in (Path(raw_path), Path(__file__))}
    protected.update({str(p.resolve()): sha_file(p) for p in HERE.glob("*.py")})
    protected.update({str(p.resolve()): sha_file(p) for p in BACKEND.rglob("*.py")})
    records = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    require(len(records) == 1038, "wrong frozen record count")
    prior_audit_path = WORK / "task1-official-qa-candidate-v12-20261012/verification/replay-run6/audit.json"
    protected[str(prior_audit_path.resolve())] = sha_file(prior_audit_path)
    expected_sources = json.loads(prior_audit_path.read_text(encoding="utf-8"))["source_html_sha256"]
    sources = {}
    for i, r in enumerate(records, 1):
        aid = r["announcement_id"]
        require(re.fullmatch(r"[A-Za-z0-9_-]+", aid), "unsafe source ID")
        file = SOURCE_ROOT / (aid + ".html")
        digest = sha_file(file)
        require(digest == expected_sources[aid], "source hash mismatch " + aid)
        protected[str(file.resolve())] = digest
        sources[aid] = parse_source(file.read_bytes(), aid)
        if i % 200 == 0: print(f"source projection {i}/1038", flush=True)
    bundle = project(records, sources)
    repeated = project(records, sources)
    require(bundle == repeated, "projection not repeatable")
    for aid, want in {"t20260508_26523610": "intention_only", "t20260702_26855564": "award_candidate",
                      "t20260401_26345150": "award_candidate", "t20260206_26158480": "award_candidate"}.items():
        require(sources[aid]["classification"] == want, "real classification guard failed")
    require(all(sha_file(p) == h for p, h in protected.items()), "frozen input drift")
    destination.mkdir()
    artifacts = {
        "tables.json": {k: bundle[k] for k in ("tables", "headers", "optional_headers", "candidate_only")},
        "uuid-map.json": bundle["uuid_map"], "source-coverage-audit.json": bundle["audit"],
        "advanced-and-original-results.json": bundle["advanced"],
        "name-metadata-joins.json": bundle["name_metadata_joins"],
    }
    for name, data in artifacts.items():
        with (destination / name).open("xb") as stream: stream.write(encoded(data))
    for name, rows in bundle["tables"].items():
        with (destination / (name + ".csv")).open("xb") as stream: stream.write(csv_bytes(name, rows))
    report = {"passed": True, "candidate_only": True, "announcements": len(records), "raw_sha256": RAW_SHA,
              "new_model_calls": 0, "database_accessed": False, "online_cutover_done": False,
              "table_rows": {k: len(v) for k, v in bundle["tables"].items()},
              "intention_only_ids": [a["announcement_id"] for a in bundle["audit"] if a["classification"] == "intention_only"],
              "excluded_intention_base_rows": sum(a["excluded_base_rows"] for a in bundle["audit"]),
              "unverified_detail_rows_not_promoted": sum(a["unverified_detail_rows_not_promoted"] for a in bundle["audit"]),
              "unbound_base_rows": sum(a["unbound_base_rows"] for a in bundle["audit"]),
              "cosmetic_name_metadata_rows": len(bundle["name_metadata_joins"]),
              "invalid_money_fields": sum(len(a["invalid_money_fields"]) for a in bundle["audit"]),
              "column_population": {name: {h: sum(literal(r.get(h)) != "" for r in rows) for h in HEADERS[name]}
                                    for name, rows in bundle["tables"].items()},
              "input_fingerprints": protected, "projection_repeatable": True,
              "quality_score_measured": False, "artifacts_sha256": {p.name: sha_file(p) for p in destination.iterdir()}}
    require(all(sha_file(p) == h for p, h in protected.items()), "frozen input drift after output")
    with (destination / "verification.json").open("xb") as stream: stream.write(encoded(report))
    return report


if __name__ == "__main__":
    require(sys.dont_write_bytecode and not sys.flags.optimize, "use Python -B without -O")
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=HERE / "candidate-run1")
    args = parser.parse_args()
    require(args.output_dir.resolve().is_relative_to(HERE.resolve()), "output outside V13 directory")
    report = run_candidate(args.output_dir)
    print(json.dumps({k: v for k, v in report.items() if k not in {"input_fingerprints", "column_population"}}, ensure_ascii=False))
