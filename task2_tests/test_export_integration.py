from pathlib import Path
import json
import hashlib
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_standard_export_hash_gate_and_missing_file(local_tmp):
    from task2_query_service.integration import install
    app=FastAPI();install(app,local_tmp);c=TestClient(app)
    assert c.get('/api/task1/export/standard').status_code==503
    assets=local_tmp/'release_assets';assets.mkdir()
    data=b'not-an-actual-xlsx-synthetic-export-response'
    (assets/'task1-standard.xlsx').write_bytes(data)
    (assets/'task1-standard-manifest.json').write_text(json.dumps({'sha256':hashlib.sha256(data).hexdigest()}))
    response=c.get('/api/task1/export/standard')
    assert response.status_code==200 and response.content==data
    assert 'filename' in response.headers['content-disposition']
    (assets/'task1-standard.xlsx').write_bytes(b'tampered')
    assert c.get('/api/task1/export/standard').status_code==503


def test_standard_item_display_is_separate_from_raw_database_and_scope_safe(local_tmp):
    from task2_query_service.integration import install
    assets=local_tmp/'release_assets';assets.mkdir()
    table=dict(headers={'标的表':['原公告ID','名称']},tables={'标的表':[
        {'原公告ID':'t20260101_1','名称':'实际中标物品'},
        {'原公告ID':'t20260101_2','名称':'另一公告'}]})
    (assets/'task1-tables.json').write_text(json.dumps(table),encoding='utf-8')
    app=FastAPI();install(app,local_tmp);c=TestClient(app)
    value=c.get('/api/task1/standard-items/t20260101_1').json()
    assert len(value['items'])==1 and value['database_written'] is False
    assert value['items'][0]['名称']=='实际中标物品'
    assert c.get('/api/task1/standard-items/not-a-source').status_code==400
