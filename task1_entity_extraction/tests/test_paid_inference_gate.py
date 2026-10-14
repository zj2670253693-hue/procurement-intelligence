import os
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def client(tmp,monkeypatch):
    from api.routers import task3
    monkeypatch.setattr(task3,'UPLOAD_ROOT',tmp)
    # Never import a pipeline or model client during a deliberately unsafe RED.
    calls=[]
    monkeypatch.setattr(task3,'_run_task',lambda task_id,work_dir,run_extraction:calls.append(run_extraction))
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED','1')
    monkeypatch.setenv('DEEPSEEK_API_KEY','')
    monkeypatch.setenv('QWEN_API_KEY','')
    app=FastAPI();app.include_router(task3.router)
    return TestClient(app)


def test_omitted_option_defaults_to_parse_only(tmp_path,monkeypatch):
    monkeypatch.delenv('PAID_INFERENCE_ENABLED',raising=False)
    response=client(tmp_path,monkeypatch).post('/api/upload',files={'files':('synthetic.html',b'<p>synthetic</p>','text/html')})
    assert response.status_code==200 and response.json()['run_extraction'] is False


def test_forged_or_unapproved_inference_never_schedules_pipeline(tmp_path,monkeypatch):
    monkeypatch.delenv('PAID_INFERENCE_ENABLED',raising=False)
    c=client(tmp_path,monkeypatch)
    assert c.post('/api/upload',data={'run_extraction':'true'},files={'files':('synthetic.html',b'<p>synthetic</p>','text/html')}).status_code==403
    monkeypatch.setenv('PAID_INFERENCE_ENABLED','1');monkeypatch.setenv('MODEL_RUN_TOKEN','synthetic-only-token')
    assert c.post('/api/upload',data={'run_extraction':'true'},files={'files':('synthetic.html',b'<p>synthetic</p>','text/html')}).status_code==403
