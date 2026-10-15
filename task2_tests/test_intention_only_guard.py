"""Actual query regressions: first-stage disclosures are not transactions."""
from dataclasses import asdict

import pytest

from task2_relationship_analysis.amounts import verify_amounts
from task2_relationship_analysis.builder import build_dataset
from task2_relationship_analysis.identities import resolve_identities
from task2_relationship_analysis.relations import verify_relations
from task2_relationship_analysis.rosters import verify_rosters
from task2_relationship_analysis.source_index import index_html
from task2_query_service.artifacts import load_dataset
from task2_query_service.queries import RelationshipQueries
from task2_tests.test_final_review_fixes import resolved_body
from task2_tests.test_identities import candidates


def source(title, notice):
    # Some first-stage notices still use the generic winning-supplier/amount
    # headings. A positive table heading cannot override the disclosure role.
    return ('<html><head><title>' + title + '</title></head><body><h1>' + title + '</h1>'
            '<p>' + notice + '</p>' + resolved_body().replace('供应商名称', '中标供应商名称') +
            '<p>评审信息 合同包1 全部投标人名单</p><table>'
            '<tr><th>投标人名称</th><th>综合得分</th><th>统一社会信用代码</th></tr>'
            '<tr><td>甲公司</td><td>90</td><td>913456789012345679</td></tr></table></body></html>')


def run(body):
    cs = candidates('甲公司', bidders=['甲公司'])
    index = index_html('t20260101_1', body.encode(), 'd')
    bundle = resolve_identities(cs, index, 'd')
    for stage in (verify_relations, verify_rosters, verify_amounts):
        bundle = stage(cs, index, bundle)
    query = RelationshipQueries(dict(dataset_id='d', facts=[asdict(f) for f in bundle.facts],
        evidence=[asdict(e) for e in bundle.evidence], sources=[asdict(s) for s in bundle.sources]))
    return bundle, query


@pytest.mark.parametrize(('title', 'notice'), [
    ('框架协议入围结果公示', '仅采购意向，尚未发生实际采购。'),
    ('框架协议征集结果公告', '第一阶段入围供应商；入围单价不代表实际成交。'),
    ('框架协议中标公告', '仅征集供应商，尚无实际成交。'),
    ('服务入围结果公告', '以下为入围供应商及入围单价。'),
    ('年度采购意向公告', '预计采购金额100元，未发生实际采购。'),
])
def test_intention_disclosure_does_not_create_awards_bids_or_transaction_amounts(title, notice):
    bundle, query = run(source(title, notice))
    assert not [f for f in bundle.facts if f.kind in {'award', 'participation', 'roster', 'supply', 'amount'}]
    assert query.buyer_suppliers('采购甲')['total'] == 0
    assert query.buyer_bidders('采购甲')['total'] == 0
    assert any(i.reason_code == 'intention_only_excluded' for i in bundle.issues)
    assert any(f.kind == 'project' for f in bundle.facts), 'Retain source identities for provenance'


@pytest.mark.parametrize(('title', 'notice'), [
    ('设备采购成交公告', '本项目已实际成交。'),
    ('框架协议第二阶段订单成交公告', '按第一阶段入围结果下单，现公示本次实际成交。'),
    ('框架协议订单成交公告', '历史入围供应商名单仅作背景；本次订单成交金额100元。'),
    ('系统采购成交公告', '本次交付框架协议管理软件，并非入围征集。'),
    ('采购意向管理系统采购成交公告', '本次已实际成交；采购意向是软件名称的一部分。'),
    ('框架协议第二阶段订单成交公告', '本公告并非仅采购意向，现公示实际订单成交金额。'),
    ('框架协议第二阶段订单成交公告', '第一阶段尚未发生实际采购；本次订单已实际成交。'),
])
def test_real_award_and_second_stage_orders_keep_original_query_results(title, notice):
    bundle, query = run(source(title, notice))
    assert [f for f in bundle.facts if f.kind == 'award' and f.validation_status == 'verified']
    row = query.buyer_suppliers('采购甲')['items'][0]
    assert row['transaction_count'] == 1 and row['amount_cny'] == '100'
    assert query.buyer_bidders('采购甲')['top5'][0]['name'] == '甲公司'
    assert not any(i.reason_code == 'intention_only_excluded' for i in bundle.issues)


def test_page_heading_is_used_when_browser_title_is_generic():
    body = source('框架协议入围结果公示', '仅采购意向，尚未发生实际采购。')
    body = body.replace('<title>框架协议入围结果公示</title>', '<title>政府采购网</title>')
    bundle, query = run(body)
    assert not [f for f in bundle.facts if f.kind == 'award']
    assert query.buyer_suppliers('采购甲')['total'] == 0


def test_second_stage_title_with_explicit_current_no_purchase_is_still_excluded():
    bundle, query = run(source('框架协议第二阶段订单成交公告', '尚未发生实际采购，仅为采购意向公示。'))
    assert not [f for f in bundle.facts if f.kind == 'award']
    assert query.buyer_suppliers('采购甲')['total'] == 0


def test_published_intention_dataset_retains_candidates_and_exclusion_reason(input_case, local_tmp):
    body = source('框架协议入围结果公示', '仅采购意向，尚未发生实际采购。')
    html, legacy, products = input_case(extra={'project_name': '测试项目', 'procurement_unit': '采购甲',
        'winning_supplier': '甲公司', 'project_amount': '100元', 'bidders': ['甲公司']}, body=body)
    built = build_dataset(html, legacy, products, local_tmp / 'published')
    data = load_dataset(built.output_dir)
    assert data['candidates'], 'Original model candidates remain available for audit'
    assert not [f for f in data['facts'] if f['kind'] in {'award', 'participation', 'amount'}]
    assert data['coverage']['issue_reason_counts']['intention_only_excluded'] == 1
    assert data['manifest']['new_model_calls'] == 0
