import hashlib
import json
from decimal import Decimal
from task3_tests.test_deployment import load


def test_synthetic_html_is_verified_by_real_builder_for_all_five_positive_queries(tmp_path):
    m=load('acceptance_fixtures')
    samples=m.samples();root=tmp_path/'inputs';root.mkdir()
    raw=[]
    for item in samples:
        (root/(item['announcement_id']+'.html')).write_bytes(item['html'].encode('utf-8'))
        raw.append(item['raw'])
    raw_path=tmp_path/'extraction/raw.json';raw_path.parent.mkdir()
    raw_path.write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
    from task3_platform.relations import run_relation_child
    profile={'kind':'uploaded','producer':'task3_uploaded_pipeline_v1','extraction_receipt_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest()}
    report=run_relation_child(root,raw_path,tmp_path/'out',profile)
    from task2_query_service.artifacts import load_dataset
    from task2_query_service.queries import RelationshipQueries
    q=RelationshipQueries(load_dataset(tmp_path/'out'/report['dataset_id']))
    suppliers=q.buyer_suppliers('合成采购甲')
    awards={row['name']:row for row in suppliers['items'] if row['role']=='award_supplier'}
    assert set(awards)=={'合成甲公司','合成乙公司'}
    # Source discloses both award total and product-line total, without an
    # explicit containment declaration. The unchanged overlap guard must keep
    # aggregated money unknown, not add the same amount twice.
    assert all(row['amount_cny'] is None for row in awards.values())
    amounts=[f for f in q.facts.values() if f['kind']=='amount' and f['fields']['amount_kind']=='award_total']
    assert sorted(Decimal(f['fields']['decimal_value']) for f in amounts)==[Decimal('100.10'),Decimal('200.20')]
    assert all(f['fields']['coverage_status']=='unknown_overlap' for f in amounts)
    assert all(row['transaction_count']==1 for row in awards.values())
    bidders=q.buyer_bidders('合成采购甲')
    assert {row['name'] for row in bidders['items']}=={'合成甲公司','合成乙公司','合成丙公司','合成丁公司'}
    assert '合成乙公司' in {row['name'] for row in q.supplier_peers('合成甲公司')['top5']}
    assert [row['name'] for row in q.common_buyers(['合成甲公司','合成乙公司'])['items']]==['合成采购甲']
    assert q.common_projects(['合成甲公司','合成乙公司'])['total']==2
    assert report['new_model_calls']==0


def test_synthetic_transport_exercises_real_parser_and_entity_pipeline(tmp_path):
    m=load('acceptance_fixtures')
    from task3_platform.contracts import Lease,PreparedInput
    from task3_platform.paths import RuntimePaths
    from task3_platform.parse_child import run_parse_child
    from task3_platform.limits import Limits
    from task3_platform.entity_step import run_entity_step
    lease=Lease('a'*32,1,'synthetic-worker')
    paths=RuntimePaths(tmp_path/'runtime').task(lease.task_id);paths.inputs.mkdir(parents=True)
    transport=m.synthetic_transport(lease.task_id)
    for item in m.samples():
        html=paths.inputs/(item['announcement_id']+'.html');body=item['html'].encode('utf-8');html.write_bytes(body)
        prepared=PreparedInput(item['announcement_id'],html,None,hashlib.sha256(body).hexdigest(),None)
        parsed=run_parse_child(prepared,Limits(),pulse=lambda:None)
        outcome=run_entity_step(lease,prepared,parsed,transport,paths)
        assert outcome.stage_status=='complete',outcome.diagnostics
        assert len(outcome.result.entities)==1
        assert outcome.result.extra_info['procurement_unit']=='合成采购甲'
        assert set(outcome.result.extra_info['bidders'])==set(item['raw']['extra_info']['bidders'])
    assert len(transport.calls)==4
