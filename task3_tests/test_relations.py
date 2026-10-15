import hashlib
import json
from pathlib import Path
import socket
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PROFILE = {"kind": "uploaded", "producer": "task3_uploaded_pipeline_v1", "extraction_receipt_sha256": "a" * 64}


def inputs(tmp_path, count=1, suffix=".html"):
    root = tmp_path / "inputs"
    root.mkdir()
    records = []
    for number in range(count):
        aid = "t20990101_" + str(number + 1)
        (root / (aid + suffix)).write_text("<p>项目名称：合成项目</p>", encoding="utf-8")
        records.append({"announcement_id": aid, "success": True, "entities": [], "detail_entities": [],
                        "extra_info": {"project_name": "合成项目", "procurement_unit": "合成单位", "winning_supplier": "合成公司", "project_amount": "", "bidders": []}})
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return root, raw


def test_runtime_1038_does_not_require_v11_hash(tmp_path):
    from task2_relationship_analysis.builder import build_dataset
    root, raw = inputs(tmp_path, 1038)
    result = build_dataset(root, raw, raw, tmp_path.parent / (tmp_path.name + "-out"), 1038, input_profile=PROFILE)
    assert result.manifest["legacy_producer"] == PROFILE["producer"]
    assert result.manifest["new_model_calls"] == 0
    assert result.manifest["input_manifest"]["input_profile"] == PROFILE
    from task2_relationship_analysis.contracts import InputContractError
    with pytest.raises(InputContractError, match="1038 acceptance"):
        build_dataset(root, raw, raw, tmp_path.parent / (tmp_path.name + "-default"), 1038)


def test_htm_profile_and_producer_not_legacy(tmp_path):
    from task2_relationship_analysis.builder import build_dataset
    from task2_relationship_analysis.publication import verify_artifacts
    root, raw = inputs(tmp_path, suffix=".htm")
    output = tmp_path.parent / (tmp_path.name + "-out")
    result = build_dataset(root, raw, raw, output, input_profile=PROFILE)
    assert result.manifest["sources"][0]["read_status"] == "decoded"
    candidates = [json.loads(line) for line in (output / result.dataset_id / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(candidate["producer"] == PROFILE["producer"] for candidate in candidates)
    assert verify_artifacts(output / result.dataset_id)["valid"]
    (root / "t20990101_1.htm").write_text("changed", encoding="utf-8")
    with pytest.raises(Exception):
        verify_artifacts(output / result.dataset_id)


def test_child_boundary_cannot_block_api(platform_module, tmp_path):
    module = platform_module("relations")
    root, raw = inputs(tmp_path)
    original = socket.create_connection
    result = module.run_relation_child(root, raw, tmp_path.parent / (tmp_path.name + "-out"), PROFILE)
    assert result["new_model_calls"] == 0
    assert socket.create_connection is original
    again = module.run_relation_child(root, raw, tmp_path.parent / (tmp_path.name + "-out"), PROFILE)
    assert again["dataset_id"] == result["dataset_id"]
    with pytest.raises(platform_module("contracts").PlatformError):
        module.run_relation_child(root, raw, root / "overlap", PROFILE)


def test_promoted_relation_keeps_verifiable_receipt_and_durable_owner(platform_module,tmp_path):
    from task2_relationship_analysis.publication import verify_artifacts
    disk=platform_module('relation_disk')
    paths=platform_module('paths').RuntimePaths(tmp_path/'runtime')
    task_id='d'*32;task=paths.task(task_id)
    task.inputs.mkdir(parents=True);task.extraction.mkdir()
    (task.inputs/'t20990101_1.html').write_text('<p>项目名称：合成项目</p>',encoding='utf-8')
    raw=task.extraction/'raw.json';raw.write_text(json.dumps([{'announcement_id':'t20990101_1','success':True,'entities':[],'detail_entities':[],'extra_info':{}}]))
    profile={**PROFILE,'extraction_receipt_sha256':hashlib.sha256(raw.read_bytes()).hexdigest()}
    receipt=platform_module('relations').run_relation_child(task.inputs,raw,disk.staging_root(paths,task_id)/'artifacts',profile)
    target=disk.promote(paths,task_id,receipt)
    assert receipt['dataset_id'] in disk.owned_versions(paths,task_id)
    assert verify_artifacts(target)['valid'] is True


def test_light_summary_and_coverage_page():
    from task2_query_service.api import create_router
    class Repo:
        upload_coverage={'availability':'partial','processed_announcements':1,'total_announcements':2}
        def resolve_version(self, version=None): return "synthetic"
        def metadata(self, version):
            return ({"sources": [], "counts": {}, "new_model_calls": 0},
                    {"announcement_status_count": 250, "fact_status_counts": {}, "announcement_status": {str(i): {"state": "complete"} for i in range(250)}})
    app = FastAPI()
    app.include_router(create_router(lambda: Repo(), prefix="/scoped"))
    client = TestClient(app)
    summary = client.get("/scoped/summary").json()
    assert "announcement_status" not in summary["coverage"]
    assert summary["source_count"] == 0
    assert summary['status']=='completed' and summary['upload_coverage']['availability']=='partial'
    page = client.get("/scoped/coverage", params={"kind": "announcement_status", "page": 2, "page_size": 200}).json()
    assert page["total"] == 250 and len(page["items"]) == 50
    assert client.get("/scoped/coverage", params={"page_size": 201}).status_code == 422


def test_binding_checks_owner_before_repository(platform_module, tmp_path, actor_a, actor_b, clock):
    from task3_tests.test_datasets import DatasetConnection
    from task3_tests.test_jobs import spec
    connection = DatasetConnection(tmp_path / "binding.sqlite")
    jobs = platform_module("job_store").JobStore(connection, clock)
    job = jobs.create_receiving(actor_a, spec(platform_module("contracts"), mode="extract"), "bind")
    connection.db.execute("UPDATE t3_jobs SET status='completed' WHERE task_id=?", (job.task_id,))
    connection.db.execute("UPDATE t3_datasets SET availability='completed',task2_dataset_id=? WHERE upload_dataset_id=?", ("ds_" + "b"*64, job.upload_dataset_id))
    connection.db.commit()
    service = platform_module("relations").RelationService(connection, platform_module("paths").RuntimePaths(tmp_path / "runtime"), jobs=jobs)
    with pytest.raises(platform_module("contracts").PlatformError) as error:
        service.repository_for(actor_b, job.upload_dataset_id)
    assert error.value.http_status == 403
    repository = service.repository_for(actor_a, job.upload_dataset_id)
    assert repository.resolve_version() == "ds_" + "b"*64
    with pytest.raises(platform_module("contracts").PlatformError):
        repository.resolve_version("ds_" + "c"*64)
    connection.db.close()


@pytest.mark.parametrize('cancel',[False,True])
def test_sql_import_uses_separate_connection_and_owner_thread_heartbeat(platform_module,tmp_path,actor_a,clock,monkeypatch,cancel):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from task3_tests.test_runtime import setup
    connection,jobs,job,paths=setup(platform_module,tmp_path,actor_a,clock,'extract')
    lease=jobs.claim('import-test');jobs.transition(lease,'validating','relation_import')
    task=paths.task(job.task_id);task.extraction.mkdir();raw=task.extraction/'raw.json';raw.write_text('[]')
    version='ds_'+'b'*64
    module=platform_module('relations')
    monkeypatch.setattr(module,'run_relation_child',lambda *args,**kwargs:{'dataset_id':version,'manifest_sha256':'a'*64})
    monkeypatch.setattr(platform_module('relation_disk'),'promote',lambda *args:None)
    separate=object()
    @contextmanager
    def factory(): yield separate
    def importer(import_connection,directory,*,initialize_schema,pulse):
        assert import_connection is separate and initialize_schema is False
        for index in range(4):
            clock.advance(20)
            if cancel and index==1: jobs.request_cancel(actor_a,job.task_id)
            pulse()
        return {'dataset_id':version}
    monkeypatch.setattr(module,'import_dataset',importer)
    service=module.RelationService(connection,paths,jobs=jobs,import_connection_factory=factory)
    worker=platform_module('worker').ProcessingWorker(jobs,SimpleNamespace(),clock)
    if cancel:
        with pytest.raises(platform_module('contracts').PlatformError):
            service.build_and_bind(lease,task.inputs,raw,{'producer':'task3_uploaded_pipeline_v1'},pulse=lambda:worker._pulse(lease))
        assert connection.db.execute('SELECT task2_dataset_id FROM t3_datasets').fetchone()[0] is None
    else:
        binding=service.build_and_bind(lease,task.inputs,raw,{'producer':'task3_uploaded_pipeline_v1'},pulse=lambda:worker._pulse(lease))
        assert binding.task2_dataset_id==version
    connection.db.close()


def test_128_character_ids_profile_fingerprint_and_duplicate_stem(tmp_path):
    from task2_relationship_analysis.inputs import snapshot_inputs
    from task2_relationship_analysis.contracts import InputContractError
    root, raw = inputs(tmp_path)
    aid = "t20990101_" + "1" * 118
    (root / "t20990101_1.html").rename(root / (aid + ".html"))
    data = json.loads(raw.read_bytes())
    data[0]["announcement_id"] = aid
    raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    one = snapshot_inputs(root, raw, raw, {"synthetic": "v1"}, input_profile=PROFILE)
    two = snapshot_inputs(root, raw, raw, {"synthetic": "v1"}, input_profile={**PROFILE, "producer": "task3_uploaded_pipeline_v2"})
    assert len(aid) == 128 and one.dataset_id != two.dataset_id
    (root / (aid + ".htm")).write_bytes((root / (aid + ".html")).read_bytes())
    with pytest.raises(InputContractError):
        snapshot_inputs(root, raw, raw, {}, input_profile=PROFILE)
