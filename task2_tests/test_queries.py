"""Independent five-scenario expectations; no model/SQL required for semantics."""
from copy import deepcopy
from task2_relationship_analysis.contracts import FIELDS


def fixture():
    facts=[]
    def add(kind,rid,source='s1',eligible=True,**values):
        fields={name:None for name in FIELDS[kind].split()}
        fields.update(values)
        facts.append(dict(dataset_id='d',record_id=rid,kind=kind,natural_key=[rid],fields=fields,
                          validation_status='verified',identity_status='resolved',
                          aggregation_eligibility='eligible' if eligible else 'identity_unresolved',
                          evidence_ids=[source+'e'],candidate_ids=[]))
    for rid,name in [('buyer','采购甲'),('a','甲公司'),('b','乙公司'),('c','丙公司'),('f','丁公司'),('manufacturer','戊厂家')]:
        add('organization',rid,raw_name=name,exact_key=name,identity_basis={'kind':'credit_code','value':rid},alias_evidence_ids=[])
    for rid in 'abcf':
        add('bid_subject','bs'+rid,subject_kind='individual',member_ids=[rid],leader_ids=[])
    for n,winner,names,amount in [(1,'a','abc','100.10'),(2,'b','abf','200.20')]:
        src='s'+str(n)
        add('project','p'+str(n),src,raw_name='项目'+str(n),project_code='CODE'+str(n),round_raw='第一轮',buyer_organization_id='buyer')
        add('event','ev'+str(n),src,project_id='p'+str(n),lot_key='合同包1',lot_raw='合同包1',round_raw='第一轮',scope_status='resolved')
        add('award','a'+str(n),src,scope_id='ev'+str(n),scope_level='event',subject_id='bs'+winner,amount_ids=['money'+str(n)])
        add('amount','money'+str(n),src,scope_id='ev'+str(n),scope_level='event',owner_subject_id='bs'+winner,
            product_candidate_id=None,amount_kind='award_total',raw_value=amount+'元',candidate_values=[amount+'元'],
            decimal_value=amount,currency='CNY',original_unit='元',coverage_status='isolated',covers_amount_ids=[],overlaps_amount_ids=[])
        for name in names:
            add('participation','part'+str(n)+name,src,scope_id='ev'+str(n),scope_level='event',subject_id='bs'+name,
                participation_kind='bid_participation',result='won' if name==winner else 'lost',qualification_raw='通过',score_raw=None,roster_id=None)
    add('supply','supply1',scope_id='ev1',scope_level='event',product_candidate_id=None,provider_kind='product_supplier',provider_organization_id='manufacturer',brand_raw=None)
    return dict(dataset_id='d',facts=facts,
                evidence=[dict(evidence_id='s1e',source_id='s1'),dict(evidence_id='s2e',source_id='s2')],
                sources=[dict(source_id='s1',announcement_id='t20260101_1'),dict(source_id='s2',announcement_id='t20260102_2')])


def query(data=None):
    from task2_query_service.queries import RelationshipQueries
    return RelationshipQueries(data or fixture())


def test_buyer_supplier_counts_and_attributed_exact_money():
    result=query().buyer_suppliers('采购甲')
    suppliers={r['name']:r for r in result['items'] if r['role']=='award_supplier'}
    assert suppliers['甲公司']['transaction_count']==1 and suppliers['甲公司']['amount_cny']=='100.10'
    assert suppliers['乙公司']['amount_cny']=='200.20'
    manufacturer=next(r for r in result['items'] if r['role']=='product_supplier')
    assert manufacturer['name']=='戊厂家' and manufacturer['amount_cny'] is None


def test_buyer_top5_bid_subjects_include_winners_and_preserve_known_combinations():
    result=query().buyer_bidders('采购甲')
    ranks={r['name']:r['announcement_count'] for r in result['top5']}
    assert ranks=={'甲公司':2,'乙公司':2,'丙公司':1,'丁公司':1}
    assert {tuple(r['names']) for r in result['combinations']}=={('丙公司','乙公司','甲公司'),('丁公司','乙公司','甲公司')}
    assert all(r['roster_complete'] is False for r in result['combinations'])


def test_supplier_peers_and_explicit_nonwinner_set_use_all_its_bid_projects():
    result=query().supplier_peers('甲公司')
    assert result['top5'][0]['name']=='乙公司' and result['top5'][0]['announcement_count']==2
    assert {r['name'] for r in result['participants']}=={'甲公司','乙公司','丙公司','丁公司'}
    assert {r['name'] for r in result['nonwinning_participants']}=={'甲公司','乙公司','丙公司','丁公司'}


def test_common_buyers_does_not_require_suppliers_to_win_the_same_project():
    result=query().common_buyers(['甲公司','乙公司'])
    assert [r['name'] for r in result['items']]==['采购甲']
    assert set(result['items'][0]['by_supplier'])=={'甲公司','乙公司'}


def test_common_projects_requires_joint_bid_evidence_not_joint_wins():
    result=query().common_projects(['甲公司','乙公司'])
    assert {r['project_id'] for r in result['items']}=={'p1','p2'}
    assert result['project_count']==2
    assert {r['results']['甲公司'] for r in result['items']}=={'won','lost'}


def test_award_only_does_not_enter_common_bidding():
    data=fixture()
    for f in data['facts']:
        if f['kind']=='participation' and f['fields']['subject_id']=='bsa':
            f['fields']['participation_kind']='award_only'
    assert query(data).common_projects(['甲公司','乙公司'])['items']==[]


def test_unknown_identity_keeps_source_occurrence_but_not_exact_transactions_or_money():
    data=fixture()
    for f in data['facts']:
        f['aggregation_eligibility']='identity_unresolved'
        f['identity_status']='identity_unresolved'
    result=query(data).buyer_suppliers('采购甲')
    row=next(r for r in result['items'] if r['name']=='甲公司')
    assert row['announcement_count']==1 and row['transaction_count'] is None and row['amount_cny'] is None
    assert result['metric_basis']=='verified_source_occurrences_with_separate_eligible_metrics'


def test_conflicts_cannot_be_counted_and_queries_do_not_mutate_facts():
    data=fixture(); before=deepcopy(data)
    next(f for f in data['facts'] if f['record_id']=='a1')['validation_status']='conflict'
    changed=deepcopy(data)
    assert not any(r['name']=='甲公司' and r['role']=='award_supplier' for r in query(data).buyer_suppliers('采购甲')['items'])
    assert data==changed and before!=data


def test_repeated_same_fact_is_rejected_instead_of_inflating_statistics():
    import pytest
    data=fixture();data['facts'].append(deepcopy(data['facts'][0]))
    with pytest.raises(ValueError,match='duplicate'):
        query(data)


def test_project_vs_package_counts_are_explicit_not_summed_implicitly():
    data=fixture()
    clone=deepcopy(next(f for f in data['facts'] if f['record_id']=='ev1'));clone['record_id']='ev3';clone['fields']['lot_key']='合同包2';clone['fields']['lot_raw']='合同包2';data['facts'].append(clone)
    award=deepcopy(next(f for f in data['facts'] if f['record_id']=='a1'));award['record_id']='a3';award['fields']['scope_id']='ev3';award['fields']['amount_ids']=[];data['facts'].append(award)
    assert next(r for r in query(data).buyer_suppliers('采购甲')['items'] if r['name']=='甲公司')['transaction_count']==1
    assert next(r for r in query(data).buyer_suppliers('采购甲',scope_level='event')['items'] if r['name']=='甲公司')['transaction_count']==2


def test_brand_only_does_not_appear_as_product_supplier_organization():
    data=fixture();s=next(f for f in data['facts'] if f['kind']=='supply');s['fields']['provider_kind']='brand';s['fields']['provider_organization_id']=None;s['fields']['brand_raw']='戊厂家'
    assert not any(r['role']=='product_supplier' for r in query(data).buyer_suppliers('采购甲')['items'])


def test_complete_roster_uses_contract_fields_and_exact_scope():
    data=fixture()
    for n in (1,2):
        fields={key:None for key in FIELDS['roster'].split()}
        fields.update(scope_id='p'+str(n),scope_level='project',roster_kind='all_bidders',
                      declared_names=[],validated_subject_ids=['bsa','bsb','bsc' if n==1 else 'bsf'],
                      disclosure_status='disclosed_complete',completeness_evidence_ids=['s'+str(n)+'e'])
        data['facts'].append(dict(dataset_id='d',record_id='roster'+str(n),kind='roster',fields=fields,
                                 natural_key=[],validation_status='verified',identity_status='resolved',
                                 aggregation_eligibility='eligible',evidence_ids=['s'+str(n)+'e'],candidate_ids=[]))
    assert all(r['roster_complete'] for r in query(data).buyer_bidders('采购甲')['combinations'])


def test_supplier_peer_amounts_are_its_own_awards_not_target_awards():
    result=query().supplier_peers('甲公司')
    assert result['top5'][0]['name']=='乙公司' and result['top5'][0]['amount_cny']=='200.20'
    assert next(r for r in result['participants'] if r['name']=='丙公司')['amount_cny'] is None


def test_unknown_result_is_not_nonwinner():
    data=fixture()
    next(f for f in data['facts'] if f['record_id']=='part1c')['fields']['result']='unknown'
    result=query(data).supplier_peers('甲公司')
    assert not any(r['name']=='丙公司' for r in result['nonwinning_participants'])
    assert result['unknown_result_count']==1


def test_consortium_is_one_bid_subject_no_member_amount_allocation():
    data=fixture()
    a=next(f for f in data['facts'] if f['record_id']=='bsa')
    a['fields'].update(subject_kind='consortium',member_ids=['a','c'],leader_ids=['a'])
    bidder_names={r['name'] for r in query(data).buyer_bidders('采购甲')['items']}
    assert '丙公司 + 甲公司' in bidder_names and '甲公司' not in bidder_names
    result=query(data).common_buyers(['甲公司','乙公司'])
    assert result['items'][0]['by_supplier']['甲公司']['amount_cny'] is None


def test_cross_package_joint_project_is_not_same_package_competition():
    data=fixture()
    clone=deepcopy(next(f for f in data['facts'] if f['record_id']=='ev1'))
    clone['record_id']='ev3';clone['fields']['lot_key']='合同包2';data['facts'].append(clone)
    next(f for f in data['facts'] if f['record_id']=='part1b')['fields']['scope_id']='ev3'
    assert query(data).common_projects(['甲公司','乙公司'])['project_count']==2
    result=query(data).common_projects(['甲公司','乙公司'],scope_level='event')
    assert result['project_count']==1 and result['items'][0]['project_id']=='p2'


def test_parent_child_coverage_amount_not_double_counted_and_cycles_rejected():
    q=query();parent=deepcopy(q.facts['money1']);child=deepcopy(parent)
    child['record_id']='child';child['fields']['decimal_value']='40.10'
    parent['fields']['coverage_status']='explicit_parent';parent['fields']['covers_amount_ids']=['child']
    child['fields']['coverage_status']='explicit_child'
    assert q.money([parent,child])=='100.10'
    child['fields']['covers_amount_ids']=['money1']
    assert q.money([parent,child]) is None


def test_invalid_scope_and_overlapping_selectors_are_rejected():
    import pytest
    with pytest.raises(ValueError): query().buyer_bidders('采购甲',scope_level='unresolved')
    with pytest.raises(ValueError): query().common_buyers(['甲公司','a'])
