from copy import deepcopy
from .identities import table_records, name_cell, result_record, bidding_record, event_for_record, subject_for_cell, add_fact, negative_result_record, scope_level
from .evidence import normalized, region_evidence
from .inputs import issue

def verify_relations(candidates, index, identities):
    b = deepcopy(identities)
    rows = list(table_records(index))
    bids = set()
    for table, record in rows:
        cell = name_cell(record)
        subject = subject_for_cell(b, cell) if cell else None
        event = event_for_record(b, table, record)
        if subject and event and bidding_record(table, record):
            bids.add((event.record_id, subject.record_id))
    for table, record in rows:
        cell = name_cell(record)
        subject = subject_for_cell(b, cell) if cell else None
        event = event_for_record(b, table, record)
        if not subject or not event:
            continue
        winning, bidding = result_record(table, record), bidding_record(table, record)
        if not (winning or bidding):
            continue
        proof = region_evidence(None, index, cell)
        scope = {"scope_id": event.record_id, "scope_level": scope_level(event)}
        eligible = ('eligible' if event.aggregation_eligibility==subject.aggregation_eligibility=='eligible'
                    else 'identity_unresolved' if event.fields['scope_status']=='resolved' else 'scope_unresolved')
        if winning:
            add_fact(b, "award", (event.record_id, subject.record_id), {**scope, "subject_id": subject.record_id, "amount_ids": []}, [proof], eligibility='eligible' if event.aggregation_eligibility==subject.aggregation_eligibility=='eligible' else eligible)
        participation_kind = "bid_participation" if (event.record_id, subject.record_id) in bids else "award_only"
        score = next((c.text.strip() for h, c in record.items() if h == "综合得分"), None)
        qualification = next((c.text.strip() for h, c in record.items() if h == "资格性审查"), None)
        key = (event.record_id, subject.record_id, participation_kind)
        old = next((f for f in b.facts if f.kind == "participation" and f.natural_key == key), None)
        result = "won" if winning or old and old.fields["result"] == "won" else "unknown"
        values = {**scope, "subject_id": subject.record_id, "participation_kind": participation_kind, "result": result, "qualification_raw": qualification or (old.fields["qualification_raw"] if old else None), "score_raw": score or (old.fields["score_raw"] if old else None), "roster_id": None}
        if old:
            old.fields.update(values)
        add_fact(b, "participation", key, values, [proof], eligibility=eligible)
    # Brands are text, not inferred legal organizations or manufacturers.
    for candidate in candidates:
        if candidate.field_kind != "product_reference" or not isinstance(candidate.raw_value, dict):
            continue
        brand = candidate.raw_value.get("brand")
        product = candidate.raw_value.get("product_name", candidate.raw_value.get("product_service_name"))
        if not isinstance(brand, str) or not brand.strip():
            continue
        matches = []
        for table, record in rows:
            brand_cells = [c for h, c in record.items() if h in ("货物品牌", "品牌") and normalized(c.text) == normalized(brand)]
            product_cells = [c for h, c in record.items() if h in ("货物名称", "标的名称", "服务名称") and isinstance(product, str) and normalized(c.text) == normalized(product)]
            if len(brand_cells) == len(product_cells) == 1:
                matches.append((table, brand_cells[0], product_cells[0]))
        if len(matches) == 1:
            table, cell, product_cell = matches[0]
            event = event_for_record(b, table)
            add_fact(b, "supply", (candidate.candidate_id, "brand"), {"scope_id": event.record_id if event else None, "scope_level": scope_level(event) if event else 'unresolved', "product_candidate_id": candidate.candidate_id, "provider_kind": "brand", "provider_organization_id": None, "brand_raw": brand}, [region_evidence(candidate, index, cell), region_evidence(candidate, index, product_cell)], [candidate], eligibility="not_applicable")
        else:
            b.issues.append(issue(b.dataset_id, index.announcement_id, "product_brand_unresolved", [candidate.candidate_id]))
    # Explicit negative results must invalidate affirmative facts at the same scope.
    for table,record in rows:
        if not negative_result_record(record): continue
        event=event_for_record(b,table,record);subject=subject_for_cell(b,name_cell(record))
        if not event or not subject: continue
        contradictions=[f for f in b.facts if f.kind in ('award','participation') and f.fields['scope_id']==event.record_id and f.fields['subject_id']==subject.record_id]
        has_affirmative_award=any(f.kind=='award' for f in contradictions)
        for fact in contradictions:
            explicit_loss=(fact.kind=='participation' and not has_affirmative_award
                           and fact.fields['participation_kind']=='bid_participation'
                           and fact.fields['result']!='won')
            if explicit_loss:
                fact.fields['result']='lost'
            else:
                fact.validation_status='conflict';fact.aggregation_eligibility='not_applicable'
                if fact.kind=='participation': fact.fields['result']='unknown'
            proofs=[region_evidence(None,index,c) for h,c in record.items() if h in ('是否中标','是否成交','中标结果','成交结果')]+[region_evidence(None,index,name_cell(record))]
            add_fact(b,fact.kind,fact.natural_key,fact.fields,proofs)
            if not explicit_loss:b.issues.append(issue(b.dataset_id,index.announcement_id,'conflicting_sources',fact_ids=[fact.record_id]))
    from .item_profiles import add_explicit_manufacturers
    return add_explicit_manufacturers(candidates, index, b)
