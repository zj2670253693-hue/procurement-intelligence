from fastapi import FastAPI
from fastapi.testclient import TestClient
from task2_tests.test_queries import fixture


class Repository:
    def resolve_version(self, requested=None):
        if requested and requested!='d': raise KeyError('version not found')
        return 'd'
    def load(self, version): return fixture()
    def status(self, version): return dict(dataset_id=version,coverage={},source_count=2)
    def evidence(self, version, eid):
        if eid!='s1e': raise KeyError('evidence not found')
        return dict(dataset_id=version,evidence_id=eid,raw_quote='<script>not executable</script>',announcement_id='t20260101_1')


def client():
    from task2_query_service.api import create_router
    app=FastAPI();app.include_router(create_router(lambda: Repository()))
    return TestClient(app)


def test_five_queries_pin_dataset_paginate_and_return_evidenced_graph():
    c=client()
    for kind in ('buyer_suppliers','buyer_bidders','supplier_peers','common_buyers','common_projects'):
        payload=dict(query_type=kind,dataset_id='d',selector='采购甲' if kind.startswith('buyer') else '甲公司',
                     selectors=['甲公司','乙公司'],page=1,page_size=1)
        response=c.post('/api/task2/query',json=payload)
        assert response.status_code==200,response.text
        value=response.json()
        assert value['dataset_id']==value['graph']['dataset_id']=='d'
        assert len(value.get('items',[]))<=1 and value['page_size']==1


def test_api_rejects_wrong_version_invalid_selection_and_unbounded_pages():
    c=client()
    assert c.post('/api/task2/query',json=dict(query_type='common_buyers',selectors=['甲公司'])).status_code==400
    assert c.post('/api/task2/query',json=dict(query_type='buyer_suppliers',selector='采购甲',dataset_id='other')).status_code==404
    assert c.post('/api/task2/query',json=dict(query_type='buyer_suppliers',selector='采购甲',page_size=999)).status_code==422


def test_source_evidence_and_choices_are_read_only_version_pinned():
    c=client()
    assert c.get('/api/task2/status').json()['dataset_id']=='d'
    assert c.get('/api/task2/organizations',params=dict(role='buyer',dataset_id='d')).json()['items'][0]['label']=='采购甲'
    value=c.get('/api/task2/evidence/s1e',params=dict(dataset_id='d')).json()
    assert value['raw_quote']=='<script>not executable</script>'
    assert c.get('/api/task2/evidence/missing',params=dict(dataset_id='d')).status_code==404


def test_page_201_is_reachable_under_10000_result_offset_cap():
    response=client().post('/api/task2/query',json=dict(query_type='buyer_suppliers',selector='采购甲',page=201,page_size=30))
    assert response.status_code==200
    assert client().post('/api/task2/query',json=dict(query_type='buyer_suppliers',selector='采购甲',page=501,page_size=30)).status_code==400


def test_secondary_collections_have_separate_pages_and_explicit_totals():
    response=client().post('/api/task2/query',json=dict(query_type='buyer_bidders',selector='采购甲',secondary_page=2,secondary_page_size=1))
    value=response.json()
    assert value['combinations_total']==2 and len(value['combinations'])==1
    assert value['secondary_page']==2 and value['secondary_page_size']==1
