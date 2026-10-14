import importlib
from task2_tests.test_identities import BASE, table, candidates, run
from task2_relationship_analysis.source_index import index_html
from task2_relationship_analysis.contracts import Candidate

def relations(body, cs):
    index = index_html('t20260101_1', body.encode(), 'd')
    identities = importlib.import_module('task2_relationship_analysis.identities').resolve_identities(cs, index, 'd')
    b = importlib.import_module('task2_relationship_analysis.relations').verify_relations(cs, index, identities)
    assert not any(f.kind == 'award' for f in identities.facts)
    return b

def test_award_only_not_bid_participation():
    b = relations(BASE + table('甲公司'), candidates('甲公司'))
    assert len([f for f in b.facts if f.kind == 'award']) == 1
    p = next(f for f in b.facts if f.kind == 'participation')
    assert p.fields['participation_kind'] == 'award_only'

def test_score_does_not_prove_award():
    body = BASE + '<p>评审信息 合同包1</p><table><tr><th>供应商</th><th>综合得分</th></tr><tr><td>乙公司</td><td>99</td></tr></table>'
    b = relations(body, candidates('', bidders=['乙公司']))
    assert not [f for f in b.facts if f.kind == 'award']
    assert next(f for f in b.facts if f.kind == 'participation').fields['result'] == 'unknown'

def test_buyer_not_agent_and_brand_not_company():
    body = BASE + '<p>四、主要标的信息 合同包1</p><table><tr><th>货物名称</th><th>货物品牌</th></tr><tr><td>设备甲</td><td>品牌甲</td></tr></table>'
    cs = candidates('') + [Candidate('d', 'brand', 't20260101_1', 's', '$[0].entities[0]', 'product_reference', {'brand':'品牌甲','product_service_name':'设备甲'})]
    b = relations(body, cs)
    assert '代理乙' not in [f.fields['raw_name'] for f in b.facts if f.kind == 'organization']
    supply = next(f for f in b.facts if f.kind == 'supply')
    assert supply.fields['provider_kind'] == 'brand'
    assert supply.fields['provider_organization_id'] is None

def test_frozen_v11_product_name_key():
    body=BASE+'<p>四、主要标的信息 合同包1</p><table><tr><th>货物名称</th><th>货物品牌</th></tr><tr><td>设备甲</td><td>品牌甲</td></tr></table>'
    cs=candidates('')+[Candidate('d','v11brand','t20260101_1','s','$[0].entities[0]','product_reference',{'brand':'品牌甲','product_name':'设备甲'})]
    b=relations(body,cs)
    assert next(f for f in b.facts if f.kind=='supply').fields['brand_raw']=='品牌甲'
