import importlib
from task2_relationship_analysis.source_index import index_html
from task2_relationship_analysis.contracts import Fact
from task2_tests.test_identities import BASE, table, candidates
from task2_tests.test_rosters import roster

def amounts(body, cs=None):
    cs = cs or candidates('甲公司')
    return importlib.import_module('task2_relationship_analysis.amounts').verify_amounts(cs, index_html('t20260101_1', body.encode(), 'd'), roster(body, cs))

def test_fee_rate_budget_and_unit_price_are_not_totals():
    body = BASE + table('甲公司').replace('100元','6.5%') + '<p>六、代理服务收费标准及金额</p><p>代理服务收费金额：710155元</p><p>预算金额：60000元</p><p>单价：100元</p>'
    b = amounts(body)
    af = [f for f in b.facts if f.kind == 'amount']
    assert {f.fields['amount_kind'] for f in af} >= {'agency_fee','rate','budget','unit_price'}
    assert next(f for f in af if f.fields['amount_kind']=='rate').fields['decimal_value'] == '6.5'
    assert not [f for f in af if f.fields['amount_kind']=='award_total']

def test_decimal_units_precision_and_currency():
    for value, decimal, currency in [('0.10万元','1000','CNY'),('0.123456789012元','0.123456789012','CNY'),('100美元','100','USD')]:
        b = amounts(BASE + table('甲公司').replace('100元',value))
        f = next(f for f in b.facts if f.kind=='amount')
        assert f.fields['decimal_value']==decimal and f.fields['currency']==currency
        if currency!='CNY': assert f.aggregation_eligibility != 'eligible'
    b = amounts(BASE + table('甲公司').replace('100元','0.1234567890123元'))
    assert 'decimal_out_of_range' in {i.reason_code for i in b.issues}

def test_multiple_winners_and_consortium_ownership():
    b = amounts(BASE + table('甲公司（联合体成员：乙公司）'), candidates('甲公司'))
    f = next(f for f in b.facts if f.kind=='amount')
    s = next(f for f in b.facts if f.kind=='bid_subject')
    assert f.fields['owner_subject_id'] == s.record_id
    b = amounts(BASE + table('甲公司') + table('乙公司') + '<p>总中标成交金额：200元</p>', candidates('甲公司；乙公司'))
    total = next(f for f in b.facts if f.kind=='amount' and f.fields['amount_kind']=='project_total')
    assert total.fields['owner_subject_id'] is None

def test_conflicting_values_same_natural_key():
    b = amounts(BASE + table('甲公司') + table('甲公司').replace('100元','200元'))
    af = [f for f in b.facts if f.kind=='amount']
    assert len(af)==1 and af[0].validation_status=='conflict'
    assert set(af[0].fields['candidate_values'])=={'100元','200元'}

def test_numbered_fee_label_and_quoted_rate():
    b=amounts(BASE+table('甲公司').replace('100元','报价：6.5（%）')+'<p>2.代理服务收费金额（元）：710155</p>')
    af=[f for f in b.facts if f.kind=='amount']
    assert any(f.fields['amount_kind']=='agency_fee' and f.fields['decimal_value']=='710155' for f in af)
    assert any(f.fields['amount_kind']=='rate' and f.fields['decimal_value']=='6.5' for f in af)

def amount(rid, covers=(), coverage='isolated', currency='CNY'):
    fields = dict(scope_id='e',scope_level='event',owner_subject_id='s',product_candidate_id=None,amount_kind='award_total',raw_value='100元',candidate_values=['100元'],decimal_value='100',currency=currency,original_unit='元',coverage_status=coverage,covers_amount_ids=list(covers),overlaps_amount_ids=[])
    return Fact('d',rid,'amount',(rid,),fields,'verified','resolved','eligible')

def test_nonoverlap_parent_priority_unknown_and_currency():
    mod = importlib.import_module('task2_relationship_analysis.amounts')
    selected, issues = mod.select_nonoverlapping_amounts([amount('parent',['child'],'explicit_parent'),amount('child')])
    assert selected==['parent'] and not issues
    selected, issues = mod.select_nonoverlapping_amounts([amount('a',coverage='unknown_overlap')])
    assert not selected and issues
    selected, issues = mod.select_nonoverlapping_amounts([amount('a'),amount('b',currency='USD')])
    assert not selected and issues
