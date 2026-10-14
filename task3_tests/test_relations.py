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


def test_light_summary_and_coverage_page():
    from task2_query_service.api import create_router
    class Repo:
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
