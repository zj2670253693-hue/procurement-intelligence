"""Legacy upload delegates isolated service, never authorizes old model token."""
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from task3_platform.contracts import Actor, Job, PlatformError
from task3_platform.limits import Limits
from task3_platform.web import install

def client(tmp,monkeypatch):
    actor=Actor("a"*32,"reviewer")
    job=Job("b"*32,"up_"+"c"*32,actor.user_id,"parse_only","queued","preflight",None,"d"*64,0)
    class Uploads:
        def begin(self,who,spec,key):
            if spec.mode=="extract": raise PlatformError("paid_inference_disabled",403)
            return SimpleNamespace(job=job,files=[SimpleNamespace(file_id="e"*32)])
        def put_part(self,*args): pass
        def complete(self,*args): return job
    services=SimpleNamespace(jobs=SimpleNamespace(limits=Limits()),uploads=Uploads(),processing_fingerprint="d"*64,
        actor=lambda *args,**kwargs:actor,task_dto=lambda *args:{"task_id":job.task_id,"mode":job.mode,"status":job.status,"run_extraction":False})
    app=FastAPI();install(app,tmp,services_provider=lambda request:services)
    return TestClient(app)

def test_omitted_option_defaults_to_parse_only(tmp_path,monkeypatch):
    monkeypatch.delenv("PAID_INFERENCE_ENABLED",raising=False)
    response=client(tmp_path,monkeypatch).post("/api/upload",files={"files":("t20990101_1.html",b"<p>synthetic</p>","text/html")})
    assert response.status_code==200 and response.json()["run_extraction"] is False

def test_forged_or_unapproved_inference_never_schedules_pipeline(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    monkeypatch.delenv("PAID_INFERENCE_ENABLED",raising=False)
    assert c.post("/api/upload",data={"run_extraction":"true"},files={"files":("t20990101_1.html",b"x","text/html")}).status_code==403
    monkeypatch.setenv("PAID_INFERENCE_ENABLED","1");monkeypatch.setenv("MODEL_RUN_TOKEN","synthetic-only-token")
    assert c.post("/api/upload",data={"run_extraction":"true"},files={"files":("t20990101_1.html",b"x","text/html")},headers={"Authorization":"Bearer synthetic-only-token"}).status_code==403
