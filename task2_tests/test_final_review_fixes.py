from dataclasses import asdict
from task2_relationship_analysis.source_index import index_html
from task2_relationship_analysis.identities import resolve_identities
from task2_relationship_analysis.relations import verify_relations
from task2_relationship_analysis.amounts import verify_amounts
from task2_query_service.queries import RelationshipQueries
from task2_tests.test_identities import BASE,table,candidates
from task2_relationship_analysis.contracts import Candidate


def resolved_body(lot='合同包1'):
    base=BASE.replace('<p>名称：采购甲</p>','<p>名称：采购甲</p><p>统一社会信用代码：913456789012345678</p>')
    return '<p>轮次：第一轮</p>'+base+table('甲公司',lot,credit='913456789012345679')


def facts(body):
    cs=candidates('甲公司',bidders=['甲公司'])
    ix=index_html('t20260101_1',body.encode(),'d')
    return verify_amounts(cs,ix,verify_relations(cs,ix,resolve_identities(cs,ix,'d')))


def test_physical_source_to_exact_bidding_count_when_identity_is_explicit():
    body=resolved_body()+('<p>评审信息 合同包1</p><table><tr><th>投标人名称</th><th>综合得分</th><th>统一社会信用代码</th></tr>'
                          '<tr><td>甲公司</td><td>90</td><td>913456789012345679</td></tr></table>')
    b=facts(body)
    participation=next(f for f in b.facts if f.kind=='participation' and f.fields['participation_kind']=='bid_participation')
    assert participation.aggregation_eligibility=='eligible'
    q=RelationshipQueries(dict(dataset_id='d',facts=[asdict(f) for f in b.facts],
                               evidence=[asdict(e) for e in b.evidence],sources=[asdict(s) for s in b.sources]))
    assert q.buyer_bidders('采购甲')['top5'][0]['transaction_count']==1


def test_explicit_undivided_event_inherits_resolved_project_identity():
    b=facts('<p>分包情况：未分包</p>'+resolved_body(''))
    event=next(f for f in b.facts if f.kind=='event' and f.fields['lot_key']=='no_lot')
    assert event.identity_status=='resolved' and event.aggregation_eligibility=='eligible'


def test_missing_round_still_blocks_exact_participation():
    b=facts(resolved_body().replace('<p>轮次：第一轮</p>',''))
    assert all(f.aggregation_eligibility!='eligible' for f in b.facts if f.kind in ('event','participation','award'))


def test_isolated_explicit_line_with_resolved_owner_can_be_eligible():
    cs=candidates('甲公司')+[Candidate('d','product','t20260101_1','s','$[0].entities[0]',
                                      'product_reference',{'product_name':'设备甲','brand':'品牌甲'})]
    body=resolved_body().replace('<td>100元</td>','<td></td>')+(
        '<p>四、主要标的信息 合同包1</p><p>货物类（甲公司）</p><table>'
        '<tr><th>标的名称</th><th>品牌</th><th>总价(元)</th></tr>'
        '<tr><td>设备甲</td><td>品牌甲</td><td>20</td></tr></table>')
    ix=index_html('t20260101_1',body.encode(),'d')
    b=verify_amounts(cs,ix,verify_relations(cs,ix,resolve_identities(cs,ix,'d')))
    line=next(f for f in b.facts if f.kind=='amount' and f.fields['amount_kind']=='product_line_total')
    assert line.fields['decimal_value']=='20' and line.aggregation_eligibility=='eligible'


def test_explicit_nonwinner_with_bidding_proof_is_not_discarded_as_a_conflict():
    body=BASE+table('甲公司')+('<p>评审信息 合同包1</p><table><tr><th>投标人名称</th><th>综合得分</th><th>是否中标</th></tr>'
                              '<tr><td>甲公司</td><td>90</td><td>是</td></tr><tr><td>乙公司</td><td>80</td><td>否</td></tr></table>')
    cs=candidates('甲公司',bidders=['甲公司','乙公司']);ix=index_html('t20260101_1',body.encode(),'d')
    b=verify_relations(cs,ix,resolve_identities(cs,ix,'d'))
    q=RelationshipQueries(dict(dataset_id='d',facts=[asdict(f) for f in b.facts],evidence=[asdict(e) for e in b.evidence],sources=[asdict(s) for s in b.sources]))
    assert [r['name'] for r in q.supplier_peers('甲公司')['nonwinning_participants']]==['乙公司']
