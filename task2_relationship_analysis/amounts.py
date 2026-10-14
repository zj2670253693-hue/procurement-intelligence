import re
from copy import deepcopy
from decimal import Decimal, localcontext, InvalidOperation
from .identities import table_records, result_record, event_for_record, name_cell, subject_for_cell, add_fact, scope_level
from .evidence import region_evidence, normalized
from .inputs import issue
from .serialization import decimal_text

KINDS = {'award_total','project_total','product_line_total','budget','ceiling','agency_fee','unit_price','rate','unknown'}

def parse_value(raw, header=''):
    text = normalized(raw)
    text = re.sub(r'^报价[:：]', '', text)
    text = text.replace('(%)','%')
    match = re.fullmatch(r'(?:人民币|￥|¥|USD|CNY)?([0-9]+(?:\.[0-9]+)?)(万元|元|美元|%|％)?', text.replace(',', ''))
    if not match:
        return None, None, None, 'amount_parse_failed'
    value, unit = Decimal(match[1]), match[2]
    if not unit:
        unit = '万元' if '万元' in header else '元' if re.search(r'[（(]元[）)]', header) else None
    currency = 'USD' if unit == '美元' or text.startswith('USD') else 'CNY' if unit in ('元','万元') or text.startswith(('人民币','￥','¥','CNY')) else None
    with localcontext() as ctx:
        ctx.prec = max(60, len(match[1])+10)
        if unit == '万元': value *= Decimal(10000)
    out = decimal_text(value)
    integer, _, fraction = out.partition('.')
    if len(integer.lstrip('-').lstrip('0')) > 26 or len(fraction) > 12:
        return None, currency, unit, 'decimal_out_of_range'
    return out, currency, unit, None

def amount_kind(header, raw, is_result=False):
    h, v = normalized(header), normalized(raw)
    if '代理' in h and ('费' in h or '收费' in h): return 'agency_fee'
    if '预算' in h: return 'budget'
    if '限价' in h or '最高价' in h: return 'ceiling'
    if re.search(r'%[)]?$',v): return 'rate'
    if '单价' in h: return 'unit_price'
    if '总中标成交金额' in h or '项目成交总额' in h: return 'project_total'
    if is_result and ('中标' in h or '成交' in h) and ('金额' in h or '总价' in h): return 'award_total'
    return 'unknown'

def verify_amounts(candidates, index, context):
    b = deepcopy(context)
    project = next(f for f in b.facts if f.kind == 'project')
    def emit(header, raw, region, event=None, owner=None, is_result=False):
        kind = amount_kind(header, raw, is_result)
        if kind == 'unknown':
            return
        value, currency, unit, error = parse_value(raw, header)
        scope = project if kind == 'project_total' or event is None else event
        proof = region_evidence(None, index, region)
        scope_ok = scope.kind == 'event' and scope.fields['scope_status'] == 'resolved'
        owner_id = owner.record_id if owner else None
        key = (scope.record_id, owner_id or 'unassigned', kind, 'no_product')
        coverage = 'isolated' if scope_ok and owner and kind=='award_total' else 'unknown_overlap' if kind in ('award_total','project_total') else 'not_applicable'
        award=next((f for f in b.facts if f.kind=='award' and f.fields['scope_id']==scope.record_id and f.fields['subject_id']==owner_id),None) if kind=='award_total' else None
        ownership_conflict=award is not None and award.validation_status=='conflict'
        values = dict(scope_id=scope.record_id,scope_level=scope_level(scope),owner_subject_id=owner_id,product_candidate_id=None,amount_kind=kind,raw_value=raw,candidate_values=[raw],decimal_value=value,currency=currency,original_unit=unit,coverage_status=coverage,covers_amount_ids=[],overlaps_amount_ids=[])
        existing = next((f for f in b.facts if f.kind=='amount' and f.natural_key == key), None)
        if existing:
            existing.fields['candidate_values'] = sorted(set(existing.fields['candidate_values']+[raw]))
            # Formatting equivalence is safe; different values/units are not.
            if (existing.fields['decimal_value'],existing.fields['currency']) != (value,currency):
                existing.validation_status = 'conflict'
                existing.aggregation_eligibility = 'not_applicable'
                b.issues.append(issue(b.dataset_id,index.announcement_id,'amount_conflict',fact_ids=[existing.record_id]))
            add_fact(b,'amount',key,dict(existing.fields),[proof])
            return
        eligible = not ownership_conflict and kind=='award_total' and value is not None and currency=='CNY' and scope_ok and owner and project.identity_status=='resolved' and owner.identity_status=='resolved'
        eligibility = 'eligible' if eligible else 'identity_unresolved' if kind=='award_total' and scope_ok and owner else 'overlap_unresolved' if kind in ('award_total','project_total') else 'not_applicable'
        proofs=[proof]+[e for e in b.evidence if ownership_conflict and e.evidence_id in award.evidence_ids]
        fact = add_fact(b,'amount',key,values,proofs,state='conflict' if ownership_conflict else 'verified' if value is not None and (kind!='award_total' or scope_ok and owner) else 'unresolved',eligibility='not_applicable' if ownership_conflict else eligibility)
        if ownership_conflict: b.issues.append(issue(b.dataset_id,index.announcement_id,'conflicting_sources',fact_ids=[fact.record_id]))
        if error: b.issues.append(issue(b.dataset_id,index.announcement_id,error,fact_ids=[fact.record_id]))
        if currency not in ('CNY',None) or currency is None and kind not in ('rate',):
            b.issues.append(issue(b.dataset_id,index.announcement_id,'currency_unresolved',fact_ids=[fact.record_id]))
        if kind=='award_total' and owner:
            for award in b.facts:
                if award.kind=='award' and award.fields['scope_id']==scope.record_id and award.fields['subject_id']==owner_id:
                    award.fields['amount_ids'] = sorted(set(award.fields['amount_ids']+[fact.record_id]))
    for table, record in table_records(index):
        result = result_record(table,record)
        event = event_for_record(b,table,record)
        cell = name_cell(record)
        owner = subject_for_cell(b,cell) if cell and result else None
        for header, amount_cell in record.items():
            if not amount_cell.text.strip(): continue
            emit(header,amount_cell.text.strip(),amount_cell,event,owner,result)
    labels = ('代理服务收费金额','代理服务费金额','预算金额','最高限价','单价','总中标成交金额','项目成交总额')
    for region in index.sections:
        m = re.fullmatch(r'\s*(?:\d+[.．、]\s*)?('+'|'.join(labels)+r')(\s*[（(](?:元|万元)[）)])?\s*[:：]\s*(.+?)\s*',region.text,re.S)
        if m: emit(m[1]+(m[2] or ''),m[3],region)
    from .item_profiles import unique_product_groups, disclosed_goods_owner
    for (candidate,table,record,product,event), references in unique_product_groups(candidates,index,b):
        totals = [(h,c) for h,c in record.items() if re.fullmatch(r'(?:总价|总金额|合价|金额)\((?:元|万元)\)',h)]
        if len(totals)!=1 or event is None:
            continue
        header,cell = totals[0]
        value,currency,unit,error = parse_value(cell.text.strip(),header)
        owner,owner_proof = disclosed_goods_owner(index,table,event,b,candidates)
        if error or currency!='CNY' or owner is None:
            continue
        peers = [f for f in b.facts if f.kind=='amount' and f.fields['amount_kind'] in ('award_total','project_total')
                 and (f.fields['scope_id']==event.record_id or f.fields['scope_id']==project.record_id)]
        values = dict(scope_id=event.record_id,scope_level=scope_level(event),owner_subject_id=owner.record_id,
                      product_candidate_id=candidate.candidate_id,amount_kind='product_line_total',raw_value=cell.text.strip(),
                      candidate_values=[cell.text.strip()],decimal_value=value,currency=currency,original_unit=unit,
                      coverage_status='unknown_overlap' if peers else 'isolated',covers_amount_ids=[],
                      overlaps_amount_ids=sorted(f.record_id for f in peers))
        proofs=[region_evidence(reference,index,region) for reference in references for region in (product,cell)]+[owner_proof]
        line=add_fact(b,'amount',(event.record_id,owner.record_id,'product_line_total',index.source_id,str(table.table),str(cell.row),str(cell.cell)),values,proofs,
                      references,eligibility='overlap_unresolved' if peers else 'eligible' if
                      event.aggregation_eligibility==owner.aggregation_eligibility==project.aggregation_eligibility=='eligible'
                      else 'identity_unresolved')
        for peer in peers:
            peer.fields['overlaps_amount_ids']=sorted(set(peer.fields['overlaps_amount_ids']+[line.record_id]))
            peer.fields['coverage_status']='unknown_overlap'
            peer.aggregation_eligibility='overlap_unresolved' if peer.validation_status=='verified' else 'not_applicable'
    # When project and package totals coexist, coverage is not guessed from sums.
    totals = [f for f in b.facts if f.kind=='amount' and f.fields['amount_kind'] in ('award_total','project_total')]
    parent_totals = [f for f in totals if f.fields['amount_kind']=='project_total']
    if parent_totals and len(totals)>len(parent_totals):
        for f in totals:
            f.fields['coverage_status']='unknown_overlap'
            f.fields['overlaps_amount_ids']=sorted(x.record_id for x in totals if x.record_id!=f.record_id)
            f.aggregation_eligibility='overlap_unresolved' if f.validation_status=='verified' else 'not_applicable'
    return b

def select_nonoverlapping_amounts(amounts):
    eligible = [f for f in amounts if f.validation_status=='verified' and f.aggregation_eligibility=='eligible' and f.fields['amount_kind'] in ('award_total','project_total','product_line_total')]
    problems = []
    def problem(reason):
        problems.append(issue(amounts[0].dataset_id if amounts else '', '', reason, fact_ids=sorted(f.record_id for f in amounts)))
    if len({f.fields['currency'] for f in eligible})>1 or any(f.fields['currency'] is None for f in eligible):
        problem('currency_mixed'); return [], problems
    if any(f.fields['coverage_status'] not in ('isolated','explicit_parent','explicit_child') or f.fields['overlaps_amount_ids'] for f in eligible):
        problem('overlap_unresolved'); return [], problems
    covered = {cid for f in eligible for cid in f.fields['covers_amount_ids']}
    ids = {f.record_id for f in eligible}
    if not covered <= ids:
        problem('coverage_reference_missing'); return [], problems
    graph = {f.record_id:f.fields['covers_amount_ids'] for f in eligible}
    def cycle(node, path):
        return node in path or any(cycle(c,path|{node}) for c in graph.get(node,[]))
    if any(cycle(n,set()) for n in graph):
        problem('coverage_cycle'); return [], problems
    return sorted(ids-covered), problems
