"""Conservative, local identities. A source match alone is never a role proof."""
import re
from .contracts import Bundle, Fact, Source, FIELDS
from .evidence import normalized, region_evidence
from .inputs import issue
from .serialization import make_record_id
from .source_index import LOT

NAME_HEADERS = {"供应商名称", "供应商", "投标人名称", "投标人", "竞标人", "中标供应商名称", "中标供应商", "成交供应商"}
LOT_HEADERS={'包号','采购包','采购包号','合同包','合同包号','包名称','标段','标段号'}
ITEM_HEADERS={'货物名称','标的名称','服务名称','采购标的','产品名称','产品服务名称','标项名称'}


def actual_item_record(table, record):
    heading = normalized(table.heading)
    return ('主要标的信息' in heading and not any(x in heading for x in ('预算','采购需求','招标需求'))
            and any(h in ITEM_HEADERS for h in record))

def result_flag(record):
    flags=[c.text.strip() for h,c in record.items() if h in ('是否中标','是否成交','中标结果','成交结果')]
    if not flags: return None
    if any(x in ('否','未中标','未成交','未中标（成交）','未中标(成交)') for x in flags): return False
    return True if all(x in ('是','中标','成交','已中标','已成交') for x in flags) else None

def negative_result_record(record):
    return name_cell(record) is not None and result_flag(record) is False

def table_records(index):
    """Rectangles or only explicit package-rowspan; keep physical cell objects."""
    for table in index.tables:
        if table.malformed or len(table.rows) < 2:
            continue
        headers = [normalized(c.text) for c in table.rows[0]]
        if len(set(headers)) != len(headers) or any(not h for h in headers):
            continue
        if any(c.rowspan != 1 or c.colspan != 1 for c in table.rows[0]):
            continue
        if any(c.rowspan != 1 or c.colspan != 1 for row in table.rows[1:] for c in row):
            # No colspan, money/subject carry, nested table or incomplete mapping.
            if any(c.colspan != 1 for row in table.rows[1:] for c in row):
                continue
            active, logical_rows, valid = {}, [], True
            for ri, physical in enumerate(table.rows[1:], 1):
                logical = {col: cell for col, (cell, end) in active.items() if ri < end}
                col = 0
                for cell in physical:
                    while col in logical:
                        col += 1
                    if col >= len(headers) or (cell.rowspan > 1 and
                            (headers[col] not in LOT_HEADERS or ri + cell.rowspan > len(table.rows))):
                        valid = False
                        break
                    logical[col] = cell
                    if cell.rowspan > 1:
                        active[col] = (cell, ri + cell.rowspan)
                    col += 1
                if not valid or set(logical) != set(range(len(headers))):
                    valid = False
                    break
                logical_rows.append({headers[col]: logical[col] for col in range(len(headers))})
            if valid:
                for record in logical_rows:
                    if any(c.text.strip() for c in record.values()):
                        yield table, record
            continue
        for row in table.rows[1:]:
            if len(row) == len(headers) and any(c.text.strip() for c in row):
                yield table, dict(zip(headers, row))

def name_cell(record):
    values = [cell for header, cell in record.items() if header in NAME_HEADERS]
    return values[0] if len(values) == 1 else None

def result_record(table, record):
    context = normalized(table.heading)
    if any(x in h for h in record for x in ("资格", "符合性", "推荐排名", "得分排名", "未中标")):
        return False
    if any(x in context for x in ("候选", "评审", "评分", "审查")):
        return False
    if result_flag(record) is False: return False
    affirmative_name=any(h in ('中标供应商名称','中标供应商','成交供应商') for h in record)
    monetary=any(re.fullmatch(r'(?:中标|成交|中标\(成交\))(?:总)?(?:金额|总价)(?:\((?:元|万元)\))?',h) for h in record)
    return name_cell(record) is not None and (affirmative_name or result_flag(record) is True or monetary and ('中标' in context or '成交' in context or '采购结果' in context) and '主要标的' not in context)

def bidding_record(table, record):
    text = normalized(table.heading) + "|" + "|".join(record)
    return name_cell(record) is not None and any(x in text for x in ("投标人", "资格性审查", "符合性审查", "综合得分", "得分排名", "投标名单", "全部投标", "候选人"))

def candidate_for_name(candidates, raw, kinds=("winning_supplier", "bidder")):
    full = raw.strip()
    main = re.split(r"[（(]联合体", full)[0].strip()
    for c in candidates:
        if c.field_kind not in kinds or not isinstance(c.raw_value, str):
            continue
        # Delimiters bound whole source names; a company prefix is not an identity.
        for target in (full, main) if "联合体" in full else (full,):
            # Formatting of consortium brackets is not a company alias rule.
            value=c.raw_value.replace('（','(').replace('）',')')
            target=target.replace('（','(').replace('）',')')
            if value.strip() == target or re.search(r"(?:^|[；;、\n])\s*" + re.escape(target) + r"\s*(?=$|[；;、\n])", value):
                return c
    return None

def add_fact(bundle, kind, key, values, proofs=(), candidates=(), state="verified", identity="not_applicable", eligibility="not_applicable"):
    key = tuple(str(x) for x in key)
    rid = make_record_id(bundle.dataset_id, kind, key)
    fields = {k: None for k in FIELDS[kind].split()}
    fields.update(values)
    existing = next((f for f in bundle.facts if f.record_id == rid), None)
    if existing is None:
        existing = Fact(bundle.dataset_id, rid, kind, key, fields, state, identity, eligibility)
        bundle.facts.append(existing)
    elif existing.fields != fields:
        existing.validation_status = "conflict"
        existing.aggregation_eligibility = "not_applicable"
    for proof in proofs:
        saved = next((e for e in bundle.evidence if e.evidence_id == proof.evidence_id), None)
        if saved is None:
            saved = proof
            bundle.evidence.append(saved)
        saved.fact_ids = sorted(set(saved.fact_ids + [rid]))
        existing.evidence_ids = sorted(set(existing.evidence_ids + [saved.evidence_id]))
    existing.candidate_ids = sorted(set(existing.candidate_ids + [c.candidate_id for c in candidates]))
    return existing

def label_values(index, labels, predicate=lambda s: True):
    found = []
    prefix=r"\s*(?:(?:[一二三四五六七八九十]+|\d+)[、.．]\s*)?"
    label=r"(?:"+"|".join(re.escape(x) for x in labels)+r")"
    for n,region in enumerate(index.sections):
        if not predicate(region):
            continue
        match = re.fullmatch(prefix+label+r"\s*[:：]\s*(.+?)\s*", region.text, re.S)
        if match:
            found.append((match[1].strip(), region))
        elif re.fullmatch(prefix+label+r"\s*[:：]?\s*",region.text) and n+1<len(index.sections):
            value=index.sections[n+1]
            if value.title==region.title and value.text.strip() and not re.search(r"[:：]",value.text) and not any(region.end<t.start<value.start for t in index.tables):
                found.append((value.text.strip(),value))
    for table in index.tables:
        if table.malformed: continue
        for row in table.rows:
            if len(row)==2 and all(c.rowspan==c.colspan==1 for c in row) and row[0].text.strip() in labels and row[1].text.strip() and predicate(row[0]):
                found.append((row[1].text.strip(),row[1]))
    return found

def unique_value(pairs):
    values = {v for v, _ in pairs}
    return next(iter(values)) if len(values) == 1 else None

def record_lot(table, record=None):
    lots = set(m.group(0).strip() for m in LOT.finditer(table.heading))
    raw=next(iter(lots)) if len(lots)==1 else None
    cells=[c for h,c in (record or {}).items() if h in LOT_HEADERS and c.text.strip()]
    if cells:
        values={c.text.strip() for c in cells}
        if len(values)!=1: return None,True
        row_raw=next(iter(values))
        found=list(LOT.finditer(row_raw))
        if len(found)!=1 or found[0].group(0).strip()!=row_raw: return None,True
        if raw and raw!=row_raw: return None,True
        raw=row_raw
    tag_cells = [c for h,c in (record or {}).items() if h == '标项名称' and c.text.strip()]
    if tag_cells:
        tags = list(LOT.finditer(tag_cells[0].text.strip()))
        if len(tags) > 1:
            return None, True
        if len(tags) == 1 and tags[0].start() == 0:
            tag_lot = tags[0].group(0).strip()
            if raw and raw != tag_lot:
                return None, True
            raw = tag_lot
    return raw,len(lots)>1

def event_for_record(bundle, table, record=None):
    raw,conflict=record_lot(table,record)
    if conflict: return None
    return next((f for f in bundle.facts if f.kind == "event" and f.fields["lot_raw"] == raw), None)

def scope_level(scope):
    return 'project' if scope.kind=='project' else 'event' if scope.fields['scope_status']=='resolved' else 'unresolved'

def subject_for_cell(bundle, cell):
    ids = {e.evidence_id for e in bundle.evidence if (e.locator.table, e.locator.row, e.locator.cell) == (cell.table, cell.row, cell.cell)}
    matches = [f for f in bundle.facts if f.kind == "bid_subject" and ids.intersection(f.evidence_ids)]
    return matches[0] if len(matches) == 1 else None

def resolve_identities(candidates, index, dataset_id):
    if dataset_id != index.dataset_id:
        raise ValueError("cross dataset source")
    b = Bundle(dataset_id, [Source(dataset_id, index.source_id, index.announcement_id, index.html_sha256, index.encoding, "decoded" if index.encoding else "decode_failed")], list(candidates), issues=list(index.diagnostics))
    aid = index.announcement_id
    organizations = {}
    def organization(raw, proof, code=None, candidate=None):
        raw = raw.strip()
        key = ("credit_code", code) if code else (aid, "exact_name", raw)
        if key in organizations:
            f = organizations[key]
            # The alias is explicit in a same-row credit-code cell, not fuzzy matching.
            f.fields["alias_evidence_ids"] = sorted(set(f.fields["alias_evidence_ids"] + [proof.evidence_id]))
            f.fields["raw_name"] = min(f.fields["raw_name"], raw)
            values = dict(f.fields)
        else:
            values = {"raw_name": raw, "exact_key": code or raw, "identity_basis": {"kind": "credit_code" if code else "announcement_exact_name", "value": code or raw}, "alias_evidence_ids": [proof.evidence_id]}
        f = add_fact(b, "organization", key, values, [proof], [candidate] if candidate else [], identity="resolved" if code else "identity_unresolved", eligibility="eligible" if code else "identity_unresolved")
        organizations[key] = f
        return f
    buyer_candidates = [c for c in candidates if c.field_kind == "procurement_unit" and isinstance(c.raw_value, str)]
    buyers = label_values(index, ("名称", "采购人名称", "采购单位"), lambda s: "采购人信息" in s.title and "代理" not in s.title)
    buyer_raw = unique_value(buyers)
    buyer = None
    if buyer_raw:
        matching = next((c for c in buyer_candidates if c.raw_value.strip() == buyer_raw), None)
        if matching:
            buyer_codes=label_values(index,('统一社会信用代码',),lambda s:'采购人信息' in s.title and '代理' not in s.title)
            buyer_code=unique_value(buyer_codes)
            if not buyer_code or not re.fullmatch(r'[0-9A-Z]{18}',buyer_code): buyer_code=None
            buyer = organization(buyer_raw, region_evidence(matching, index, next(s for v, s in buyers if v == buyer_raw)), buyer_code, matching)
            if buyer_code:
                organization(buyer_raw,region_evidence(matching,index,next(s for v,s in buyer_codes if v==buyer_code)),buyer_code,matching)
    projects = label_values(index, ("项目名称", "采购项目名称"))
    project_raw = unique_value(projects)
    project_candidate = next((c for c in candidates if c.field_kind == "project_name" and isinstance(c.raw_value, str) and c.raw_value.strip() == project_raw), None)
    codes = label_values(index, ("项目编号", "采购项目编号"))
    rounds = label_values(index, ("采购轮次", "轮次", "采购批次"))
    code, round_raw = unique_value(codes), unique_value(rounds)
    resolved = bool(code and round_raw and buyer and buyer.identity_status == "resolved")
    project_proofs = [region_evidence(project_candidate, index, s) for v, s in projects if v == project_raw] if project_candidate else []
    project_proofs += [region_evidence(None, index, s) for v, s in codes + rounds if v in (code, round_raw)]
    key = (code, buyer.record_id, round_raw) if resolved else (aid, "project")
    p = add_fact(b, "project", key, {"raw_name": project_raw if project_candidate else None, "project_code": code, "round_raw": round_raw, "buyer_organization_id": buyer.record_id if buyer else None}, project_proofs, [project_candidate] if project_candidate else [], state="verified" if project_candidate else "unresolved", identity="resolved" if resolved else "identity_unresolved", eligibility="eligible" if resolved and project_candidate else "identity_unresolved")
    if not resolved:
        b.issues.append(issue(dataset_id, aid, "identity_unresolved", fact_ids=[p.record_id]))
    # Explicit markers are retained literally. Missing package is not 'package 1'.
    lot_regions = {}
    for region in index.sections:
        matches = list(LOT.finditer(region.text))
        if len(matches) == 1 and len(region.text) < 140:
            lot_regions.setdefault(matches[0].group(0).strip(), []).append(region)
    for table, record in table_records(index):
        if actual_item_record(table, record):
            item_lot, conflict = record_lot(table, record)
            if item_lot and not conflict:
                cells = [c for h,c in record.items() if h in LOT_HEADERS or h == '标项名称']
                if cells:
                    lot_regions.setdefault(item_lot, []).extend(cells)
            continue  # A product row cannot manufacture a bidder/award subject.
        if not (result_record(table, record) or bidding_record(table, record) or negative_result_record(record)):
            continue
        row_lot,conflict=record_lot(table,record)
        if conflict:
            b.issues.append(issue(dataset_id,aid,'scope_unresolved',locator=region_evidence(None,index,name_cell(record)).locator))
        elif row_lot and row_lot not in lot_regions:
            row_proofs=[c for h,c in record.items() if h in LOT_HEADERS and c.text.strip()==row_lot]
            if row_proofs: lot_regions[row_lot]=row_proofs
        cell = name_cell(record)
        c = candidate_for_name(candidates, cell.text)
        if not c:
            b.issues.append(issue(dataset_id, aid, "role_candidate_uncovered", locator=region_evidence(None, index, cell).locator))
            continue
        proof = region_evidence(c, index, cell)
        raw = cell.text.strip()
        group = re.fullmatch(r"(.+?)[（(]联合体成员\s*[:：]\s*(.+?)[）)]", raw)
        members = [group[1].strip()] + [x.strip() for x in re.split(r"[、；;]", group[2]) if x.strip()] if group else [raw]
        credit_cell = record.get("统一社会信用代码")
        credit = credit_cell.text.strip() if credit_cell and re.fullmatch(r"[0-9A-Z]{18}", credit_cell.text.strip()) and not group else None
        orgs = [organization(n, proof, credit, c) for n in members]
        if credit:
            organization(raw, region_evidence(c, index, credit_cell), credit, c)
        leader_pairs=label_values(index,('牵头人','牵头方','联合体牵头人'),lambda s: bool(row_lot and row_lot in s.title)) if group else []
        leader=unique_value(leader_pairs)
        leaders=[o.record_id for o in orgs if o.fields['raw_name']==leader]
        leader_proofs=[region_evidence(None,index,s) for v,s in leader_pairs if v==leader] if leaders else []
        add_fact(b, "bid_subject", ("consortium" if group else "individual", *sorted(o.record_id for o in orgs)), {"subject_kind": "consortium" if group else "individual", "member_ids": sorted(o.record_id for o in orgs), "leader_ids": leaders}, [proof]+leader_proofs, [c], identity="resolved" if all(o.identity_status == "resolved" for o in orgs) else "identity_unresolved", eligibility="identity_unresolved" if any(o.identity_status != "resolved" for o in orgs) else "eligible")
    for lot, regions in sorted(lot_regions.items()):
        add_fact(b, "event", (p.record_id, lot), {"project_id": p.record_id, "lot_key": lot, "lot_raw": lot, "round_raw": round_raw, "scope_status": "resolved"}, [region_evidence(None, index, s) for s in regions], identity=p.identity_status, eligibility="eligible" if resolved else "identity_unresolved")
    no_lot = [(v, s) for v, s in label_values(index, ("分包情况",)) if v in ("未分包", "不分包")]
    fallback_eligible=('eligible' if no_lot and not lot_regions and p.aggregation_eligibility=='eligible'
                       else 'identity_unresolved' if no_lot and not lot_regions else 'scope_unresolved')
    fallback = add_fact(b, "event", (p.record_id, "no_lot" if no_lot else "scope_unknown"), {"project_id": p.record_id, "lot_key": "no_lot" if no_lot else None, "lot_raw": None, "round_raw": round_raw, "scope_status": "resolved" if no_lot and not lot_regions else "scope_unresolved"}, [region_evidence(None, index, s) for _, s in no_lot], state="verified" if no_lot and not lot_regions else "unresolved", identity=p.identity_status, eligibility=fallback_eligible)
    if fallback.validation_status == "unresolved":
        b.issues.append(issue(dataset_id, aid, "scope_unresolved", fact_ids=[fallback.record_id]))
    return b
