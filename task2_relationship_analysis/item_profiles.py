"""Explicit actual-item roles and line totals; never promote budget or brands."""
import re
from .evidence import normalized, region_evidence
from .identities import (table_records, actual_item_record, ITEM_HEADERS, event_for_record,
                         add_fact, scope_level, candidate_for_name)

MISSING = {'', '/', '-', '无', '未公示', '详见附件', '见附件', '国产', '进口'}
FACTORY_HEADERS = {'生产厂家','制造商','制造商名称','生产厂商','产品供应商','产品供应商名称'}


def unique_product_rows(candidates, index, bundle):
    rows = [(t,r) for t,r in table_records(index) if actual_item_record(t,r)]
    for candidate in candidates:
        if candidate.field_kind != 'product_reference' or not isinstance(candidate.raw_value, dict):
            continue
        name = candidate.raw_value.get('product_name', candidate.raw_value.get('product_service_name'))
        if not isinstance(name, str) or not normalized(name):
            continue
        matches = [(t,r,[c for h,c in r.items() if h in ITEM_HEADERS and normalized(c.text)==normalized(name)])
                   for t,r in rows]
        matches = [(t,r,cells[0]) for t,r,cells in matches if len(cells)==1]
        if len(matches)==1:
            table, record, product = matches[0]
            yield candidate, table, record, product, event_for_record(bundle, table, record)


def unique_product_groups(candidates, index, bundle):
    groups = {}
    for row in unique_product_rows(candidates,index,bundle):
        groups.setdefault((row[1].table,row[3].row),[]).append(row)
    for key in sorted(groups):
        members = sorted(groups[key],key=lambda r:r[0].candidate_id)
        yield members[0], [r[0] for r in members]


def add_explicit_manufacturers(candidates, index, bundle):
    matches = { (row[1].table,row[3].row): (row,refs) for row,refs in unique_product_groups(candidates,index,bundle) }
    # An explicit structured source role is retained even when the model omitted
    # the product candidate. It remains source-only, not a new inferred candidate.
    for table,record in table_records(index):
        if not actual_item_record(table,record):
            continue
        cells=[c for h,c in record.items() if h in {'标的名称','采购标的','货物名称','服务名称','产品名称'}]
        if len(cells)!=1 or not cells[0].text.strip():
            continue
        product=cells[0]
        key=(table.table,product.row)
        matches.setdefault(key,((None,table,record,product,event_for_record(bundle,table,record)),[]))
    for key in sorted(matches):
        (candidate, table, record, product, event), refs = matches[key]
        disclosures = []
        for header, cell in record.items():
            if header in FACTORY_HEADERS:
                name = cell.text.strip()
                if name not in MISSING and not re.search(r'[;；|\n]', name):
                    disclosures.append((name,cell,None,None))
            elif header in {'规格型号','型号','货物型号'}:
                match = re.match(r'\s*此项产品无品牌[，,]\s*制造商名称[:：]\s*([^。；;|\n]+)。', cell.text)
                if (match and match[1].strip() not in MISSING
                        and len(re.findall(r'(?:制造商名称|生产厂家名称)[:：]',cell.text))==1):
                    # The remaining specification is not an organization name.
                    disclosures.append((match[1].strip(),cell,
                                        cell.positions[match.start()][0],cell.positions[match.end()-1][1]))
        if len(disclosures)!=1 or event is None:
            continue
        name, cell, start, end = disclosures[0]
        proof = region_evidence(candidate,index,cell,start,end)
        key = (index.announcement_id,'exact_name',name)
        existing = next((f for f in bundle.facts if f.kind=='organization' and f.natural_key==key), None)
        values = dict(existing.fields) if existing else dict(raw_name=name,exact_key=name,
            identity_basis={'kind':'announcement_exact_name','value':name},alias_evidence_ids=[])
        values['alias_evidence_ids'] = sorted(set(values['alias_evidence_ids']+[proof.evidence_id]))
        if existing:
            existing.fields.update(values)
        organization = add_fact(bundle,'organization',key,values,[proof],refs,
                                identity='identity_unresolved',eligibility='identity_unresolved')
        add_fact(bundle,'supply',(index.source_id,str(table.table),str(product.row),'product_supplier',event.record_id,organization.record_id),
                 dict(scope_id=event.record_id,scope_level=scope_level(event),product_candidate_id=candidate.candidate_id if candidate else None,
                      provider_kind='product_supplier',provider_organization_id=organization.record_id,
                      brand_raw=candidate.raw_value.get('brand') if candidate else None), [proof,region_evidence(candidate,index,product)],
                 refs,eligibility='identity_unresolved' if event.fields['scope_status']=='resolved' else 'scope_unresolved')
    return bundle


def disclosed_goods_owner(index, table, event, bundle, candidates):
    if event is None or event.fields['scope_status']!='resolved':
        return None, None
    before = [r for r in index.sections if r.end <= table.start]
    if not before:
        return None, None
    caption = before[-1]
    match = re.fullmatch(r'货物类[（(]([^（）()]+)[）)]', caption.text.strip())
    if not match or not candidate_for_name(candidates, match[1]):
        return None, None
    known = []
    for award in bundle.facts:
        if award.kind!='award' or award.validation_status!='verified' or award.fields['scope_id']!=event.record_id:
            continue
        subject = next(f for f in bundle.facts if f.record_id==award.fields['subject_id'])
        members = [f for f in bundle.facts if f.record_id in subject.fields['member_ids']]
        if subject.fields['subject_kind']=='individual' and len(members)==1 and members[0].fields['raw_name']==match[1].strip():
            known.append(subject)
    return (known[0],region_evidence(None,index,caption)) if len(known)==1 else (None,None)
