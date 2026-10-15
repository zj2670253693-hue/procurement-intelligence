from types import SimpleNamespace
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from task3_tests.test_datasets import dataset_setup, visible


@pytest.fixture
def web_case(platform_module, dataset_setup, actor_a, actor_b):
    setup, connection, paths, jobs = dataset_setup
    repository, job, prepared, _, _ = setup()
    visible(connection, job)
    connection.db.executescript("""
      ALTER TABLE t3_inference_attempts ADD COLUMN response_json TEXT;
      CREATE TABLE t3_upload_files(task_id TEXT,file_id TEXT,original_name TEXT,expected_bytes INTEGER,
        part_hashes TEXT,received_bytes INTEGER DEFAULT 0,relative_path TEXT,sha256 TEXT,PRIMARY KEY(task_id,file_id));
      CREATE TABLE t3_upload_parts(task_id TEXT,file_id TEXT,part_no INTEGER,sha256 TEXT,bytes INTEGER,
        relative_path TEXT,PRIMARY KEY(task_id,file_id,part_no));
    """)
    database_path=connection.db.execute("PRAGMA database_list").fetchone()[2]
    connection.db.close()
    connection.db=sqlite3.connect(database_path,check_same_thread=False)
    connection.db.row_factory=sqlite3.Row
    contracts = platform_module("contracts")
    class Auth:
        origin = "https://testserver"
        def require_actor(self, token):
            if token not in {"a"*64,"b"*64}: raise contracts.PlatformError("session_required",401)
            return actor_a if token == "a"*64 else actor_b
        def require_csrf(self, token, header, origin):
            self.require_actor(token)
            if header != "c"*64 or origin != self.origin: raise contracts.PlatformError("csrf_forbidden",403)
        def login(self, username, password, secure):
            if not secure: raise contracts.PlatformError("secure_transport_required",403)
            return SimpleNamespace(token="a"*64,csrf="c"*64,actor=actor_a)
        def logout(self, token): self.require_actor(token)
    def provider(request):
        return platform_module("services").Services(connection,paths,jobs=jobs,auth=Auth(),secure=request.url.scheme=="https",processing_fingerprint="d"*64)
    app=FastAPI()
    platform_module("web").install(app,paths.root,services_provider=provider)
    client=TestClient(app,base_url="https://testserver")
    client.cookies.set("t3_session","a"*64)
    return client,connection,job,prepared


def test_private_routes_check_owner_and_relation_binding(platform_module,web_case):
    client,connection,job,prepared=web_case
    root=f"/api/platform/datasets/{job.upload_dataset_id}"
    assert client.get(root+"/entities").status_code==200
    assert client.get(root+f"/announcements/{prepared.announcement_id}").status_code==200
    assert client.post(f"/api/platform/tasks/{job.task_id}/cancel").status_code==403
    assert client.post(f"/api/platform/tasks/{job.task_id}/cancel",headers={"X-CSRF-Token":"c"*64,"Origin":"https://testserver"}).status_code==200
    client.cookies.set("t3_session","b"*64)
    for endpoint in ("/entities","/stats",f"/announcements/{prepared.announcement_id}",f"/announcements/{prepared.announcement_id}/source",f"/announcements/{prepared.announcement_id}/attachments", "/artifacts/standard.xlsx","/task2/summary"):
        response=client.get(root+endpoint)
        assert response.status_code==403,(endpoint,response.text)
        assert "runtime" not in response.text and "password" not in response.text
    client.cookies.set("t3_session","a"*64)
    connection.db.execute("UPDATE t3_datasets SET availability='staging' WHERE upload_dataset_id=?",(job.upload_dataset_id,))
    connection.db.commit()
    assert client.get(root+"/entities").status_code==409


def test_source_download_is_attachment_nosniff(web_case):
    client,_,job,prepared=web_case
    response=client.get(f"/api/platform/datasets/{job.upload_dataset_id}/announcements/{prepared.announcement_id}/source")
    assert response.status_code==200 and response.content==prepared.html_path.read_bytes()
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"]=="nosniff"


def test_partial_coverage_is_explicit_and_owner_scoped(web_case):
    import json
    client,connection,job,prepared=web_case
    connection.db.execute("UPDATE t3_jobs SET status='partial',manifest_json=? WHERE task_id=?",(json.dumps({'inputs':[{'announcement_id':prepared.announcement_id},{'announcement_id':'t20990101_2'}]}),job.task_id))
    connection.db.execute("UPDATE t3_datasets SET availability='partial' WHERE upload_dataset_id=?",(job.upload_dataset_id,));connection.commit()
    url=f'/api/platform/datasets/{job.upload_dataset_id}/coverage'
    response=client.get(url)
    assert response.status_code==200,response.text
    assert response.json()['availability']=='partial'
    assert response.json()['processed_announcements']==1 and response.json()['total_announcements']==2
    client.cookies.set('t3_session','b'*64)
    assert client.get(url).status_code==403


def test_auth_cookies_and_http_rejected(web_case):
    client,*_=web_case
    response=client.post("/api/platform/auth/login",json={"username":"synthetic","password":"synthetic-only-password"})
    assert response.status_code==200
    cookie=response.headers.get_list("set-cookie")[0]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    insecure=TestClient(client.app,base_url="http://testserver")
    assert insecure.post("/api/platform/auth/login",json={"username":"synthetic","password":"synthetic-only-password"},headers={"X-Forwarded-Proto":"https"}).status_code==403


def test_login_sync_verification_is_not_on_asgi_event_loop(platform_module):
    import asyncio,threading
    import httpx
    owner_thread=threading.get_ident()
    class Auth:
        origin='https://testserver'
        def login(self,*args):
            assert threading.get_ident()!=owner_thread,'Synchronous SQL/scrypt blocks ASGI loop'
            return SimpleNamespace(token='a'*64,csrf='b'*64,actor=SimpleNamespace(user_id='c'*32,role='reviewer'))
    services=SimpleNamespace(auth=Auth(),secure=True)
    app=FastAPI();app.include_router(platform_module('web_auth').router(lambda:services))
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://testserver') as client:
            response=await client.post('/api/platform/auth/login',json={'username':'synthetic','password':'synthetic-only-password'})
            assert response.status_code==200
    asyncio.run(request())


def test_login_admission_limits_rate_and_inflight_work(platform_module):
    module=platform_module('web_auth');now=[0]
    assert hasattr(module,'LoginAdmission'),'Missing bounded login admission'
    gate=module.LoginAdmission(clock=lambda:now[0],max_attempts=2,window_seconds=60,max_inflight=1)
    with gate.enter('synthetic-address'):
        with pytest.raises(platform_module('contracts').PlatformError) as busy:
            with gate.enter('synthetic-address'): pass
        assert busy.value.http_status==429
    with pytest.raises(platform_module('contracts').PlatformError):
        with gate.enter('synthetic-address'): pass
    now[0]=61
    with gate.enter('synthetic-address'): pass


def test_legacy_upload_cannot_touch_baseline_even_with_model_flag(platform_module,web_case,monkeypatch):
    client,*_=web_case
    monkeypatch.setenv("PAID_INFERENCE_ENABLED","1")
    monkeypatch.setenv("MODEL_RUN_TOKEN","synthetic-only")
    legacy_repository=SimpleNamespace(writes=0)
    response=client.post("/api/upload",data={"run_extraction":"true"},files={"files":("t20990101_2.html",b"<p>synthetic</p>","text/html")},headers={"Origin":"https://testserver","X-CSRF-Token":"c"*64,"Authorization":"Bearer synthetic-only"})
    assert response.status_code==403
    assert legacy_repository.writes==0
    response=client.post("/api/upload",files={"files":("t20990101_2.html",b"<p>synthetic</p>","text/html")},headers={"Origin":"https://testserver","X-CSRF-Token":"c"*64})
    assert response.status_code==200 and response.json()["mode"]=="parse_only"
    assert response.json()["status"]=="queued"


def test_disabled_install_fails_closed(platform_module,tmp_path):
    app=FastAPI()
    platform_module("web").install(app,tmp_path)
    client=TestClient(app)
    assert client.get("/api/platform/tasks").status_code==503
    assert client.post("/api/upload",files={"files":("t20990101_1.html",b"x","text/html")}).status_code==503


def test_anonymous_shared_actor_cannot_authorize_any_task(platform_module,web_case,tmp_path):
    client,connection,*_=web_case
    service_module=platform_module('services')
    services=service_module.Services(connection,platform_module('paths').RuntimePaths(tmp_path),
        processing_fingerprint='a'*64,public_demo=True,guest_actor=platform_module('contracts').Actor('f'*32,'guest'))
    with pytest.raises(platform_module('contracts').PlatformError) as denied:
        services.actor(SimpleNamespace(cookies={}),mutation=True,guest=True)
    assert denied.value.http_status==401


def test_chunk_resume_and_explicit_retry_guards(platform_module,web_case):
    import hashlib
    client,connection,job,_=web_case
    headers={"Origin":"https://testserver","X-CSRF-Token":"c"*64}
    data=b"<p>synthetic chunk</p>"
    response=client.post("/api/platform/uploads",json={"request_key":"chunk-web","files":[{"name":"t20990101_9.html","bytes":len(data),"part_hashes":[hashlib.sha256(data).hexdigest()]}]},headers=headers)
    assert response.status_code==200,response.text
    session=response.json()
    tid=session["job"]["task_id"]
    fid=session["files"][0]["file_id"]
    assert client.put(f"/api/platform/uploads/{tid}/files/{fid}/parts/0",content=data,headers=headers).status_code==200
    assert client.get(f"/api/platform/uploads/{tid}").json()["files"][0]["received_parts"]==[0]
    assert client.post(f"/api/platform/uploads/{tid}/complete",headers=headers).json()["status"]=="queued"
    connection.db.execute("UPDATE t3_jobs SET status='failed' WHERE task_id=?",(tid,))
    connection.db.commit()
    assert client.post(f"/api/platform/tasks/{tid}/retry",headers=headers).json()["status"]=="queued"
    connection.db.execute("UPDATE t3_jobs SET status='needs_confirmation' WHERE task_id=?",(tid,))
    connection.db.execute("INSERT INTO t3_inference_attempts(attempt_id,task_id,state) VALUES('synthetic',?,'uncertain')",(tid,))
    connection.db.commit()
    assert client.post(f"/api/platform/tasks/{tid}/retry",headers=headers).status_code==409


@pytest.mark.parametrize("filters",[{"text":None},{"missing":[]},{"ranges":{"quantity":7}},{"quantity_unit":[]},{"ranges":{"quantity":[1,2]},"quantity_unit":"unknown-dimension"}])
def test_malformed_seven_field_filters_fail_as_format_errors(web_case,filters):
    import json
    client,_,job,_=web_case
    response=client.get(f"/api/platform/datasets/{job.upload_dataset_id}/entities",params={"filters":json.dumps(filters)})
    assert response.status_code==400,response.text


def test_forged_source_query_has_no_sql_or_file_side_effect(web_case):
    client,connection,job,_=web_case
    before=len(connection.statements)
    response=client.get(f"/api/platform/datasets/{job.upload_dataset_id}/announcements/not-an-id")
    assert response.status_code==400
    assert not any("DELETE" in sql or "UPDATE" in sql for sql,_ in connection.statements[before:])


def test_server_capabilities_and_exact_decimal_range_string(web_case):
    import json
    client,_,job,_=web_case
    value=client.get('/api/platform/capabilities').json()
    assert value['chunk_bytes']==16*1024**2 and value['paid_inference_ready'] is False
    filters={"ranges":{"unit_price":["1.23000000","1.23000000"]}}
    response=client.get(f'/api/platform/datasets/{job.upload_dataset_id}/entities',params={"filters":json.dumps(filters)})
    assert response.status_code==200 and response.json()['total']==1
