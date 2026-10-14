from collections import Counter, defaultdict
from .contracts import Fact, FIELDS, ValidationReport, BundleValidationError
from .serialization import canonical_bytes, make_record_id
from .inputs import load_candidates, safe_path
from .source_index import index_html
from .evidence import verify_locator, locate_candidate
from .identities import scope_level

REFERENCES = {
 'organization': {'alias_evidence_ids':'evidence'},
 'bid_subject': {'member_ids':'organization','leader_ids':'organization'},
 'project': {'buyer_organization_id':'organization'},
 'event': {'project_id':'project'},
 'roster': {'scope_id':('event','project'),'validated_subject_ids':'bid_subject','completeness_evidence_ids':'evidence'},
 'participation': {'scope_id':('event','project'),'subject_id':'bid_subject','roster_id':'roster'},
 'award': {'scope_id':('event','project'),'subject_id':'bid_subject','amount_ids':'amount'},
 'supply': {'scope_id':('event','project'),'product_candidate_id':'candidate','provider_organization_id':'organization'},
 'amount': {'scope_id':('event','project'),'owner_subject_id':'bid_subject','product_candidate_id':'candidate','covers_amount_ids':'amount','overlaps_amount_ids':'amount'},
}

def validate_bundle(bundle, snapshot):
    errors=[]
    ds=snapshot.dataset_id
    if bundle.dataset_id!=ds: errors.append('bundle_dataset_mismatch')
    groups=[(bundle.sources,'source_id'),(bundle.candidates,'candidate_id'),(bundle.facts,'record_id'),(bundle.evidence,'evidence_id'),(bundle.issues,'issue_id')]
    all_ids=[]
    for records,key in groups:
        ids=[getattr(x,key) for x in records];all_ids+=ids
        if len(ids)!=len(set(ids)): errors.append('duplicate_'+key)
        if any(x.dataset_id!=ds for x in records): errors.append('cross_dataset_'+key)
    if len(all_ids)!=len(set(all_ids)): errors.append('global_duplicate_id')
    sources={s.source_id:s for s in bundle.sources}
    facts={f.record_id:f for f in bundle.facts}
    evidence={e.evidence_id:e for e in bundle.evidence}
    candidates={c.candidate_id:c for c in bundle.candidates}
    expected_candidates={c.candidate_id:c for c in load_candidates(snapshot)[0]}
    candidate_ids=set(candidates);fact_ids=set(facts);evidence_ids=set(evidence)
    if set(candidates)!=set(expected_candidates): errors.append('candidate_catalog_mismatch')
    for cid,c in candidates.items():
        if cid not in expected_candidates or canonical_bytes(c)!=canonical_bytes(expected_candidates[cid]): errors.append('candidate_path_or_value:'+cid)
    if Counter(s.announcement_id for s in bundle.sources)!=Counter(snapshot.html_entries.keys()): errors.append('source_catalog_mismatch')
    grouped=defaultdict(list)
    for e in bundle.evidence:
        if e.source_id not in sources: errors.append('evidence_source_missing');continue
        grouped[e.source_id].append(e)
        if not set(e.candidate_ids)<=candidate_ids or not set(e.fact_ids)<=fact_ids: errors.append('evidence_reference_missing')
        for fid in e.fact_ids:
            if fid in facts and e.evidence_id not in facts[fid].evidence_ids: errors.append('evidence_reverse_missing')
    matches=defaultdict(list)
    semantic_kinds={'project_name':{'project'},'procurement_unit':{'organization'},'winning_supplier':{'award'},'bidder':{'participation'},'project_amount':{'amount'},'product_reference':{'supply'}}
    for f in bundle.facts:
        for cid in f.candidate_ids:
            if f.kind in semantic_kinds.get(candidates[cid].field_kind,set()) if cid in candidates else False:
                matches[cid].append(f)
    # Relation proofs can be candidate-free but identity source proofs are linked.
    for f in bundle.facts:
        if f.kind not in ('award','participation'): continue
        subject=facts.get(f.fields['subject_id'])
        if subject:
            for cid in subject.candidate_ids:
                if cid in candidates and f.kind in semantic_kinds.get(candidates[cid].field_kind,set()): matches[cid].append(f)
    reasons=defaultdict(set)
    for problem in bundle.issues:
        for cid in problem.candidate_ids: reasons[cid].add(problem.reason_code)
    by_aid=defaultdict(list)
    for c in bundle.candidates: by_aid[c.announcement_id].append(c)
    for source in bundle.sources:
        path=snapshot.html_entries.get(source.announcement_id)
        expected=snapshot.input_manifest['html'].get(source.announcement_id)
        if path is None:
            if source.html_sha256 is not None or grouped[source.source_id]: errors.append('missing_source_has_evidence')
            continue
        try:
            index=index_html(source.announcement_id,safe_path(path).read_bytes(),ds)
            if source.source_id!=index.source_id or source.html_sha256!=index.html_sha256 or source.encoding!=index.encoding or expected['sha256']!=index.html_sha256: errors.append('source_hash_changed')
            for e in grouped[source.source_id]:
                if not verify_locator(e,index): errors.append('evidence_quote_or_locator:'+e.evidence_id)
                if any(candidates[cid].announcement_id!=source.announcement_id for cid in e.candidate_ids if cid in candidates): errors.append('candidate_evidence_cross_announcement')
            for c in by_aid[source.announcement_id]:
                if c.field_kind in semantic_kinds and not matches[c.candidate_id]:
                    located,_=locate_candidate(c,index)
                    reasons[c.candidate_id].add('role_unresolved' if located else 'value_not_located')
        except (OSError,ValueError): errors.append('source_unreadable')
    id_kind={**{k:v.kind for k,v in facts.items()},**{k:'candidate' for k in candidates},**{k:'evidence' for k in evidence}}
    for f in bundle.facts:
        try:
            Fact(**vars(f))
        except (ValueError,TypeError): errors.append('fact_contract:'+f.record_id);continue
        if f.record_id!=make_record_id(ds,f.kind,f.natural_key): errors.append('fact_natural_key:'+f.record_id)
        if f.validation_status=='verified' and not f.evidence_ids: errors.append('verified_without_evidence')
        if not set(f.evidence_ids)<=evidence_ids or not set(f.candidate_ids)<=candidate_ids: errors.append('fact_reference_missing')
        for eid in f.evidence_ids:
            if eid in evidence and f.record_id not in evidence[eid].fact_ids: errors.append('fact_reverse_missing')
        for field,kind in REFERENCES[f.kind].items():
            value=f.fields[field]
            refs=value if isinstance(value,list) else [value] if value is not None else []
            kinds=(kind,) if isinstance(kind,str) else kind
            if any(id_kind.get(ref) not in kinds for ref in refs): errors.append('foreign_key:'+f.kind+'.'+field)
        if f.kind=='bid_subject' and not set(f.fields['leader_ids'])<=set(f.fields['member_ids']): errors.append('leader_not_member')
        scope=facts.get(f.fields.get('scope_id'))
        if scope and f.fields.get('scope_level')!=scope_level(scope): errors.append('scope_level_mismatch')
        if f.aggregation_eligibility=='eligible':
            if scope and (scope.identity_status!='resolved' or scope.validation_status!='verified' or scope.kind=='event' and scope.fields['scope_status']!='resolved'): errors.append('eligible_scope_unresolved')
            if f.kind=='event' and facts.get(f.fields['project_id']) and facts[f.fields['project_id']].identity_status!='resolved': errors.append('eligible_project_unresolved')
        if f.kind=='award':
            for mid in f.fields['amount_ids']:
                amount=facts.get(mid)
                if amount and (amount.fields['scope_id']!=f.fields['scope_id'] or amount.fields['owner_subject_id']!=f.fields['subject_id']): errors.append('award_amount_ownership')
        if f.kind=='amount' and f.aggregation_eligibility=='eligible':
            if not f.fields['owner_subject_id'] or f.fields['currency']!='CNY' or f.fields['decimal_value'] is None or f.fields['amount_kind'] not in ('award_total','product_line_total') or f.fields['coverage_status'] not in ('isolated','explicit_parent','explicit_child') or f.fields['overlaps_amount_ids']: errors.append('eligible_amount_invalid')
        if f.kind=='roster' and f.fields['disclosure_status']=='disclosed_complete':
            if not f.fields['completeness_evidence_ids'] or not f.fields['validated_subject_ids'] or not scope or scope.fields.get('scope_status')!='resolved': errors.append('roster_complete_unproved')
    graph={f.record_id:f.fields['covers_amount_ids'] for f in bundle.facts if f.kind=='amount'}
    visited,active=set(),set()
    def visit(n):
        if n in active: return True
        if n in visited: return False
        active.add(n)
        cycle=any(visit(x) for x in graph.get(n,[]))
        active.remove(n);visited.add(n)
        return cycle
    if any(visit(n) for n in graph): errors.append('coverage_cycle')
    per_kind={kind:dict(Counter(f.validation_status for f in bundle.facts if f.kind==kind)) for kind in FIELDS}
    outcomes={}
    for c in bundle.candidates:
        fs=matches[c.candidate_id]
        state='conflict' if any(f.validation_status=='conflict' for f in fs) else 'verified' if fs and all(f.validation_status=='verified' for f in fs) else 'rejected' if fs and all(f.validation_status=='rejected' for f in fs) else 'unresolved'
        outcomes[c.candidate_id]={'announcement_id':c.announcement_id,'field_kind':c.field_kind,'status':state,'reason_codes':sorted(reasons[c.candidate_id] or ({'candidate_container_retained'} if c.field_kind in ('extra_info','bidders') else {'role_unresolved'} if state=='unresolved' else set())),'fact_ids':sorted(set(f.record_id for f in fs)),'scope_ids':sorted(set(f.fields['scope_id'] for f in fs if f.fields.get('scope_id')))}
    per_aid={aid:{'read_status':next((s.read_status for s in bundle.sources if s.announcement_id==aid),'missing_status'),'issue_count':sum(i.announcement_id==aid for i in bundle.issues),'field_status_counts':{}} for aid in sorted(snapshot.html_entries)}
    for aid in per_aid:
        for field in ('project_name','procurement_unit','winning_supplier','project_amount','bidder','product_reference'):
            values=[outcomes[c.candidate_id] for c in by_aid[aid] if c.field_kind==field]
            per_aid[aid]['field_status_counts'][field]=dict(Counter(v['status'] for v in values)) if values else {'unresolved':1}
    scope_facts=defaultdict(list)
    for f in bundle.facts:
        if f.fields.get('scope_id'): scope_facts[f.fields['scope_id']].append(f)
    scopes={f.record_id:{'scope_level':scope_level(f),'validation_status':f.validation_status,'identity_status':f.identity_status,'aggregation_eligibility':f.aggregation_eligibility,'fact_status_counts':{k:dict(Counter(x.validation_status for x in scope_facts[f.record_id] if x.kind==k)) for k in ('roster','participation','award','supply','amount')}} for f in bundle.facts if f.kind in ('project','event')}
    coverage={'announcement_status_count':len(per_aid),'announcement_status':per_aid,'fact_status_counts':per_kind,'identity_status_counts':dict(Counter(f.identity_status for f in bundle.facts)),'aggregation_eligibility_counts':dict(Counter(f.aggregation_eligibility for f in bundle.facts)),'roster_disclosure_counts':dict(Counter(f.fields['disclosure_status'] for f in bundle.facts if f.kind=='roster')),'amount_kind_counts':dict(Counter(f.fields['amount_kind'] for f in bundle.facts if f.kind=='amount')),'known_amount_count':sum(f.kind=='amount' and f.fields['decimal_value'] is not None and f.validation_status=='verified' for f in bundle.facts),'issue_reason_counts':dict(Counter(i.reason_code for i in bundle.issues)),'metric_note':'Evidence coverage, not extraction accuracy; unknown is not zero.'}
    coverage['candidate_status']=outcomes
    coverage['candidate_status_counts']=dict(Counter(v['status'] for v in outcomes.values()))
    coverage['scope_status']=scopes
    return ValidationReport(not errors,sorted(set(errors)),coverage)

def require_valid(report):
    if not report.valid: raise BundleValidationError(';'.join(report.errors[:20]))
