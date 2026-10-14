import importlib
from copy import deepcopy
from task2_relationship_analysis.inputs import snapshot_inputs, load_candidates
from task2_relationship_analysis.source_index import index_html
from task2_tests.test_identities import BASE, table

def valid_fixture(input_case):
    root, legacy, v11 = input_case()
    (root/'t20260101_1.html').write_bytes((BASE+table('甲公司')).encode())
    legacy.write_text('[{"announcement_id":"t20260101_1","extra_info":{"project_name":"测试项目","procurement_unit":"采购甲","winning_supplier":"甲公司","project_amount":"100元","bidders":[]}}]',encoding='utf8')
    s=snapshot_inputs(root,legacy,v11,{'test':'fixed'})
    cs, problems=load_candidates(s)
    ix=index_html('t20260101_1',(root/'t20260101_1.html').read_bytes(),s.dataset_id)
    b=importlib.import_module('task2_relationship_analysis.identities').resolve_identities(cs,ix,s.dataset_id)
    for module,function in [('relations','verify_relations'),('rosters','verify_rosters'),('amounts','verify_amounts')]:
        b=getattr(importlib.import_module('task2_relationship_analysis.'+module),function)(cs,ix,b)
    b.issues+=problems
    return b,s

def test_invalid_structure_blocks_publication(input_case):
    b,s=valid_fixture(input_case)
    mod=importlib.import_module('task2_relationship_analysis.validation')
    assert mod.validate_bundle(b,s).valid
    for mutate in [lambda x:x.facts.append(deepcopy(x.facts[0])),lambda x:setattr(x.facts[0],'dataset_id','wrong'),lambda x:x.facts[0].evidence_ids.clear(),lambda x:x.evidence.__setitem__(0,deepcopy(x.evidence[0]))]:
        bad=deepcopy(b);mutate(bad)
        # Last case is separately corrupted below.
        if bad==b: bad.evidence[0].raw_quote='not original'
        assert not mod.validate_bundle(bad,s).valid
    bad=deepcopy(b);next(f for f in bad.facts if f.kind=='event').fields['project_id']='missing'
    assert not mod.validate_bundle(bad,s).valid

def test_amount_ownership_and_cycle_block(input_case):
    b,s=valid_fixture(input_case)
    mod=importlib.import_module('task2_relationship_analysis.validation')
    f=next(f for f in b.facts if f.kind=='amount')
    f.fields['covers_amount_ids']=[f.record_id]
    assert not mod.validate_bundle(b,s).valid
    f.fields['covers_amount_ids']=[];f.fields['owner_subject_id']='no-subject'
    assert not mod.validate_bundle(b,s).valid

def test_partial_fields_and_coverage_not_accuracy(input_case):
    b,s=valid_fixture(input_case)
    r=importlib.import_module('task2_relationship_analysis.validation').validate_bundle(b,s)
    assert r.valid and r.coverage['announcement_status_count']==1
    assert 'accuracy' not in r.coverage
    assert r.coverage['aggregation_eligibility_counts']['identity_unresolved']>0
    assert r.coverage['roster_disclosure_counts']['not_disclosed']>0
