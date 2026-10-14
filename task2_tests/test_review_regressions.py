import importlib
from copy import deepcopy
import pytest
from task2_relationship_analysis.contracts import Fact, InputContractError
from task2_relationship_analysis.identities import resolve_identities
from task2_relationship_analysis.source_index import index_html
from task2_relationship_analysis.publication import output_path
from task2_tests.test_identities import BASE, table, candidates, run
from task2_tests.test_rosters import roster
from task2_tests.test_amounts import amounts, amount
from task2_tests.test_validation import valid_fixture

def negative():
    return '<p>合同包1</p><table><tr><th>供应商名称</th><th>是否中标</th></tr><tr><td>甲公司</td><td>否</td></tr></table>'

def test_review_negative_is_not_award_and_conflict_not_verified():
    b=roster(BASE+negative(),candidates('甲公司'))
    assert not [f for f in b.facts if f.kind=='award' and f.validation_status=='verified']
    b=roster(BASE+table('甲公司')+negative(),candidates('甲公司'))
    assert not [f for f in b.facts if f.kind=='award' and f.validation_status=='verified']
    assert 'conflicting_sources' in {i.reason_code for i in b.issues}

def test_review_row_package_conflict_not_verified():
    body=BASE+'<p>合同包1</p><table><tr><th>包号</th><th>供应商名称</th><th>中标金额</th></tr><tr><td>合同包2</td><td>甲公司</td><td>100元</td></tr></table>'
    b=amounts(body)
    assert not [f for f in b.facts if f.kind=='award' and f.validation_status=='verified']
    assert not [f for f in b.facts if f.kind=='amount' and f.fields['amount_kind']=='award_total' and f.validation_status=='verified']

def test_review_explicit_identity_is_reachable_across_announcements():
    body=BASE.replace('<p>名称：采购甲</p>','<p>名称：采购甲</p><p>统一社会信用代码：913456789012345678</p>')+'<p>采购轮次：第一轮</p>'+table('甲公司',credit='923456789012345678')
    bs=[]
    for aid in ('t20260101_1','t20260102_2'):
        cs=candidates('甲公司')
        for c in cs: c.announcement_id=aid
        bs.append(resolve_identities(cs,index_html(aid,body.encode(),'d'),'d'))
    ps=[next(f for f in b.facts if f.kind=='project') for b in bs]
    assert all(p.identity_status=='resolved' and p.aggregation_eligibility=='eligible' for p in ps)
    assert ps[0].record_id==ps[1].record_id

def test_review_consortium_leader_requires_explicit_role():
    raw='甲公司（联合体成员：乙公司）'
    b=run(BASE+table(raw),candidates('甲公司'))
    assert next(f for f in b.facts if f.kind=='bid_subject').fields['leader_ids']==[]
    b=run(BASE+table(raw)+'<p>牵头人：甲公司</p>',candidates('甲公司'))
    assert len(next(f for f in b.facts if f.kind=='bid_subject').fields['leader_ids'])==1

def test_review_malformed_roster_and_categories():
    body=BASE+'<p>全部投标人名单 合同包1</p><table><tr><th>供应商</th><th>综合得分</th></tr><tr><td><table><tr><td>甲公司</td></tr></table></td><td>90</td></tr></table>'
    b=roster(body,candidates('',bidders=['甲公司']))
    assert any(f.kind=='roster' and f.fields['disclosure_status']=='parse_failed' for f in b.facts)
    from task2_tests.test_rosters import review
    kinds=[]
    for title in ('全部投标人名单','仅合格供应商','前三名候选供应商'):
        b=roster(BASE+review(title),candidates('',bidders=['甲公司','乙公司']))
        kinds.append(next(f for f in b.facts if f.kind=='roster' and f.fields['declared_names']).fields['roster_kind'])
    assert len(set(kinds))==3

def test_review_contract_enums_are_enforced():
    b=run(BASE+table('甲公司'),candidates('甲公司'))
    assert next(f for f in b.facts if f.kind=='bid_subject').fields['subject_kind']=='individual'
    b=roster(BASE+table('甲公司'),candidates('甲公司'))
    assert next(f for f in b.facts if f.kind=='award').fields['scope_level']=='event'
    f=next(f for f in b.facts if f.kind=='bid_subject')
    bad=deepcopy(vars(f));bad['fields']['subject_kind']='invented'
    with pytest.raises(ValueError): Fact(**bad)

def test_review_all_write_locations_exclude_input_directories(input_case,local_tmp):
    b,s=valid_fixture(input_case)
    with pytest.raises(InputContractError): output_path(s.legacy_path.parent/'nested-output',s)
    # JSON input directories are separate; HTML equals the computed receipt dir.
    source=local_tmp/'run_receipts';source.mkdir()
    s.html_root=source
    with pytest.raises(InputContractError): output_path(local_tmp/'outputs',s)

def test_review_unmatched_candidate_has_status_and_reason(input_case):
    b,s=valid_fixture(input_case)
    # Original empty bidders replaced in a new independent fixture.
    root,old,v11=input_case(extra={'project_name':'测试项目','winning_supplier':'甲公司','bidders':['不存在公司']},body=BASE+table('甲公司'))
    from task2_relationship_analysis.inputs import snapshot_inputs,load_candidates
    snap=snapshot_inputs(root,old,v11,{'test':'fixed'});cs,issues=load_candidates(snap)
    ix=index_html('t20260101_1',(root/'t20260101_1.html').read_bytes(),snap.dataset_id)
    bundle=resolve_identities(cs,ix,snap.dataset_id)
    for module,function in [('relations','verify_relations'),('rosters','verify_rosters'),('amounts','verify_amounts')]:
        bundle=getattr(importlib.import_module('task2_relationship_analysis.'+module),function)(cs,ix,bundle)
    bundle.issues+=issues
    validation=importlib.import_module('task2_relationship_analysis.validation')
    report=validation.validate_bundle(bundle,snap)
    bidder=next(c for c in cs if c.field_kind=='bidder')
    status=report.coverage['candidate_status'][bidder.candidate_id]
    assert status['status']=='unresolved' and 'value_not_located' in status['reason_codes']
    assert 'field_status_counts' in report.coverage['announcement_status']['t20260101_1']
    assert report.coverage['scope_status']

def test_review_equivalent_units_are_not_value_conflict():
    b=amounts(BASE+table('甲公司')+table('甲公司').replace('100元','0.01万元'))
    f=next(f for f in b.facts if f.kind=='amount')
    assert f.validation_status=='verified' and f.fields['decimal_value']=='100'
    assert set(f.fields['candidate_values'])=={'100元','0.01万元'} and len(f.evidence_ids)>=2

def test_real_format_consortium_brackets_do_not_merge_company_names():
    b=run(BASE+table('甲公司(联合体成员：乙公司)'),candidates('甲公司（联合体成员：乙公司）；丙公司'))
    assert next(f for f in b.facts if f.kind=='bid_subject').fields['subject_kind']=='consortium'

def test_review_negative_result_also_blocks_verified_amount():
    b=amounts(BASE+table('甲公司')+negative())
    assert not [f for f in b.facts if f.kind=='amount' and f.fields['amount_kind']=='award_total' and f.validation_status=='verified']

def test_ambiguous_multi_marker_heading_does_not_create_evidenceless_event(input_case,local_tmp):
    from task2_tests.test_builder import build
    body=BASE+'<p>合同包1、合同包2</p>'+table('甲公司').replace('<p>合同包1</p>','')
    result,paths=build(input_case,local_tmp,body)
    assert result.status=='completed'

def test_end_implementation_guard_includes_validation_time(input_case,local_tmp,monkeypatch):
    module=importlib.import_module('task2_relationship_analysis.builder')
    from task2_relationship_analysis.contracts import InputChangedError
    actual_validate=module.validate_bundle;actual_fingerprint=module.implementation_fingerprint
    state={'changed':False}
    def fingerprint(path):
        return {**actual_fingerprint(path),**({'test_mutation':True} if state['changed'] else {})}
    def validate(*args):
        report=actual_validate(*args);state['changed']=True;return report
    monkeypatch.setattr(module,'implementation_fingerprint',fingerprint)
    monkeypatch.setattr(module,'validate_bundle',validate)
    from task2_tests.test_builder import build
    with pytest.raises(InputChangedError): build(input_case,local_tmp)

def test_real_procurement_result_heading_keeps_explicit_winner():
    body=BASE.replace('三、中标（成交）信息','三、采购结果')+table('甲公司')
    b=amounts(body)
    assert any(f.kind=='award' and f.validation_status=='verified' for f in b.facts)
    assert any(f.kind=='amount' and f.fields['amount_kind']=='award_total' and f.validation_status=='verified' for f in b.facts)
