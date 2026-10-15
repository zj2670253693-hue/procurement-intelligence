import hashlib
import json
from dataclasses import replace
from decimal import Decimal
import pytest
from task3_tests.test_jobs import JobConnection, spec
from task3_tests.test_inference_ledger import ledger_setup, Backend


class Steps:
    """Boundary doubles; real orchestration/checkpoints/files are under test."""
    def __init__(self, module, jobs, paths, prepared, clock):
        self.module, self.jobs, self.paths = module, jobs, paths
        self.prepared, self.clock = prepared, clock
        self.parsed = []
        self.saved_entities = 0
        self.interrupt_aid = None
        self.fail_aid = None
        self.cancel_aid = None
        self.finalize_ok = True

    def prepare(self, lease, job):
        return self.prepared

    def parse(self, prepared, pulse):
        pulse()
        self.parsed.append(prepared.announcement_id)
        if self.interrupt_aid == prepared.announcement_id:
            raise KeyboardInterrupt()
        if self.fail_aid == prepared.announcement_id:
            raise self.module.PlatformError("parse_timeout", 408)
        from models.schemas import AnnouncementContent
        from data_loader.attachment_parser import AttachmentParseOutcome
        return self.module.ParsedAnnouncement(AnnouncementContent(announcement_id=prepared.announcement_id, html_text="synthetic"),
            AttachmentParseOutcome({}, {}, [], [], [], "no_attachment"), self.module.content_sha256(prepared.to_dto()))

    def entity(self, lease, prepared, parsed, pulse):
        pulse()
        from models.schemas import ExtractionResult, ExtractedEntity
        return self.module.EntityStepResult(ExtractionResult(announcement_id=prepared.announcement_id,
            entities=[ExtractedEntity(product_name="synthetic")], detail_status="no_attachment"),
            {"schema_valid": True, "extra_valid": True, "coverage_gap": False}, "complete")

    def save(self, lease, prepared, result, result_hash):
        self.saved_entities += len(result.entities)
        self.jobs.checkpoint(lease, prepared.announcement_id, result_hash, "confirmed")

    def finish(self, lease, job, items, diagnostics, pulse):
        pulse()
        return {"relations_verified": self.finalize_ok, "exports_verified": self.finalize_ok,
                "dataset_published": self.finalize_ok}

    def finish_parse(self, lease, job, items, diagnostics, pulse):
        pulse()
        return {"exports_verified": self.finalize_ok}


@pytest.fixture
def worker_setup(platform_module, tmp_path, clock, actor_a):
    connection = JobConnection(tmp_path / "worker.sqlite")
    jobs = platform_module("job_store").JobStore(connection, clock)
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime")
    c = platform_module("contracts")
    def setup(mode="parse_only", count=1):
        module = platform_module("worker")
        job = jobs.create_receiving(actor_a, spec(c, mode=mode), "worker")
        prepared = []
        for index in range(1, count + 1):
            aid = "t20990101_" + str(index)
            path = paths.task(job.task_id).inputs / (aid + ".html")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"<p>synthetic</p>")
            prepared.append(c.PreparedInput(aid, path, None, hashlib.sha256(path.read_bytes()).hexdigest(), None))
        jobs.finalize(job.task_id, {"files": [], "inputs": [p.to_dto() for p in prepared]})
        steps = Steps(module, jobs, paths, prepared, clock)
        return module, jobs, job, steps, paths
    yield setup
    connection.db.close()


def test_worker_parse_only_and_complete_are_distinct(worker_setup, clock):
    module, jobs, job, steps, paths = worker_setup()
    result = module.ProcessingWorker(jobs, steps, clock).run_once()
    assert result.status == "validated_only" and steps.saved_entities == 0
    assert jobs.checkpoints(job.task_id)["t20990101_1"].db_state == "validated_only"
    assert json.loads((paths.task(job.task_id).extraction / "items/1/t20990101_1.json").read_bytes())["validated_only"] is True


def test_resumed_parse_keeps_previous_partial_attachment_state(worker_setup,clock):
    module,jobs,job,steps,paths=worker_setup(count=2)
    original=steps.parse
    def partial_first(prepared,pulse):
        parsed=original(prepared,pulse)
        if prepared.announcement_id.endswith("_1"):
            parsed.attachments.failed_files.append("synthetic.pdf")
        return parsed
    steps.parse=partial_first
    steps.interrupt_aid="t20990101_2"
    with pytest.raises(KeyboardInterrupt): module.ProcessingWorker(jobs,steps,clock).run_once()
    steps.interrupt_aid=None
    clock.advance(61)
    resumed=module.ProcessingWorker(jobs,steps,clock).run_once()
    assert resumed.status=="partial"


def test_worker_receipt_allocation_cannot_bypass_disk_quota(worker_setup, clock):
    module, jobs, job, steps, paths = worker_setup()
    jobs.limits = replace(jobs.limits, request_bytes=32, chunk_bytes=16, file_bytes=32,
                         received_bytes=64, expanded_bytes=64, html_bytes=16, member_bytes=128,
                         job_disk_bytes=128, global_disk_bytes=256, reserve_disk_bytes=1)
    result = module.ProcessingWorker(jobs, steps, clock).run_once()
    assert result.status == "failed"
    assert jobs.checkpoints(job.task_id) == {}
    assert not list(paths.task(job.task_id).extraction.rglob("*.json"))


def test_extract_is_not_completed_before_finalization(worker_setup, clock):
    module, jobs, job, steps, _ = worker_setup("extract")
    steps.finalize_ok = False
    result = module.ProcessingWorker(jobs, steps, clock).run_once()
    assert result.status == "failed"
    assert steps.saved_entities == 1


def test_worker_persists_budget_details_without_claiming_completion(worker_setup, clock):
    from models.inference import InferenceHalt
    module, jobs, job, steps, _ = worker_setup("extract")
    details = {"budget": {"max_calls": 1}, "observed_usage": {"input_tokens": None}}
    def exhausted(*args):
        raise InferenceHalt("inference_budget_exhausted", details=details)
    steps.entity = exhausted
    result = module.ProcessingWorker(jobs, steps, clock).run_once()
    assert result.status == "failed" and steps.saved_entities == 0
    with jobs.connection.cursor() as cursor:
        row = jobs._row(cursor, job.task_id)
    assert json.loads(row["diagnostics_json"]) == [{"code": "inference_budget_exhausted", "details": details}]


def test_resume_no_shared_outputs_or_extra_call(worker_setup, clock, monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "official-output")
    module, jobs, job, steps, _ = worker_setup("extract", 2)
    steps.interrupt_aid = "t20990101_2"
    with pytest.raises(KeyboardInterrupt):
        module.ProcessingWorker(jobs, steps, clock).run_once()
    assert steps.saved_entities == 1
    clock.advance(61)
    jobs.recover_expired()
    steps.interrupt_aid = None
    assert module.ProcessingWorker(jobs, steps, clock).run_once().status == "completed"
    assert steps.parsed.count("t20990101_1") == 1
    assert steps.saved_entities == 2
    assert not (tmp_path / "official-output").exists()


def test_timeout_cancel_partial_zero(worker_setup, clock):
    module, jobs, job, steps, _ = worker_setup("extract", 2)
    steps.fail_aid = "t20990101_2"
    result = module.ProcessingWorker(jobs, steps, clock).run_once()
    assert result.status == "partial" and steps.saved_entities == 1


def test_job_wall_timeout_cannot_complete(worker_setup, clock):
    module, jobs, job, steps, _ = worker_setup()
    original_parse = steps.parse
    def parse(prepared, pulse):
        parsed = original_parse(prepared, pulse)
        clock.advance(6 * 3600 + 1)
        pulse()
        return parsed
    steps.parse = parse
    result = module.ProcessingWorker(jobs, steps, clock).run_once()
    assert result.status == "failed"


def test_entity_adapter_keeps_zero_schema_separate_from_hallucination_gap(platform_module, tmp_path, monkeypatch):
    module = platform_module("entity_step")
    c = platform_module("contracts")
    from models.schemas import AnnouncementContent
    from models.inference import InferenceResponse
    from data_loader.attachment_parser import AttachmentParseOutcome
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime").task("a" * 32)
    paths.inputs.mkdir(parents=True)
    html = paths.inputs / "t20990101_1.html"
    html.write_bytes(b"<p>synthetic</p>")
    prepared = c.PreparedInput("t20990101_1", html, None, hashlib.sha256(html.read_bytes()).hexdigest(), None)
    class EmptyTransport:
        task_id, provider, model = "a" * 32, "deepseek", "synthetic"
        def __call__(self, request):
            return InferenceResponse('{"entities":[]}' if request.layer != "extra" else '{}', None, None, None)
    attachments = AttachmentParseOutcome({}, {}, [], [], [], "no_attachment")
    content = AnnouncementContent(announcement_id=prepared.announcement_id, html_text="本项目废标，无实际中标标的")
    parsed = module.ParsedAnnouncement(content, attachments, c.content_sha256(prepared.to_dto()))
    outcome = module.run_entity_step(c.Lease("a" * 32, 1, "worker"), prepared, parsed, EmptyTransport(), paths)
    assert outcome.stage_status == "complete" and outcome.result.entities == []
    assert outcome.diagnostics["schema_valid"] is True
    content.html_text = "主要标的信息"
    content.html_tables = [[["货物名称", "数量"], ["实际设备", "1"]]]
    gap = module.run_entity_step(c.Lease("a" * 32, 1, "worker"), prepared, parsed, EmptyTransport(), paths)
    assert gap.stage_status == "failed" and gap.diagnostics["coverage_gap"] is True


def test_injected_pipeline_uses_preparsed_content_and_private_writer(platform_module, tmp_path, monkeypatch):
    module = platform_module("entity_step")
    import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "official")
    from models.schemas import AnnouncementContent
    from models.inference import InferenceResponse
    from data_loader.attachment_parser import AttachmentParseOutcome
    c = platform_module("contracts")
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime").task("a" * 32)
    paths.inputs.mkdir(parents=True)
    html = paths.inputs / "t20990101_1.html"
    html.write_bytes(b"<p>source</p>")
    prepared = c.PreparedInput("t20990101_1", html, None, hashlib.sha256(html.read_bytes()).hexdigest(), None)
    content = AnnouncementContent(announcement_id=prepared.announcement_id, html_text="合成设备")
    parsed = module.ParsedAnnouncement(content, AttachmentParseOutcome({}, {}, [], [], [], "no_attachment"), c.content_sha256(prepared.to_dto()))
    class Transport:
        task_id, provider, model = "a" * 32, "deepseek", "synthetic"
        def __call__(self, request):
            payload = '{"entities":[{"product_name":"合成设备"}]}' if request.layer == "summary" else '{}'
            return InferenceResponse(payload, None, None, None)
    from data_loader.html_parser import HTMLAnnouncementParser
    monkeypatch.setattr(HTMLAnnouncementParser, "parse_file", lambda *args: pytest.fail("parsed HTML twice"))
    outcome = module.run_entity_step(c.Lease("a" * 32, 1, "worker"), prepared, parsed, Transport(), paths)
    assert outcome.result.entities[0].product_name == "合成设备"
    assert not (tmp_path / "official").exists()


def test_real_parse_child_reads_text_without_network_or_model(platform_module, tmp_path):
    module = platform_module("parse_child")
    c = platform_module("contracts")
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime").task("a" * 32)
    paths.inputs.mkdir(parents=True)
    html = paths.inputs / "t20990101_1.html"
    html.write_bytes(b"<html><h1>synthetic</h1><p>source</p></html>")
    prepared = c.PreparedInput("t20990101_1", html, None, hashlib.sha256(html.read_bytes()).hexdigest(), None)
    outcome = module.run_parse_child(prepared, platform_module("limits").Limits(), pulse=lambda: None)
    assert outcome.content.title == "synthetic" and "source" in outcome.content.html_text
    assert outcome.attachments.status == "no_attachment"


def test_zip_hash_is_streamed_not_read_as_one_big_buffer(platform_module, tmp_path, monkeypatch):
    import io, zipfile
    from pathlib import Path
    module = platform_module("parse_child")
    c = platform_module("contracts")
    html = tmp_path / "t20990101_1.html"
    html.write_bytes(b"<p>synthetic</p>")
    attached = tmp_path / "t20990101_1.zip"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("spec.txt", "synthetic detail")
    attached.write_bytes(out.getvalue())
    prepared = c.PreparedInput(html.stem, html, attached, hashlib.sha256(html.read_bytes()).hexdigest(), hashlib.sha256(out.getvalue()).hexdigest())
    original = Path.read_bytes
    def bounded(path):
        assert path.suffix != ".zip", "Whole archive allocation is not bounded"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", bounded)
    result = module._parse(prepared, platform_module("limits").Limits())
    assert result["attachments"]["texts"]["spec.txt"] == "synthetic detail"


def test_global_uncertain_blocks_other_extraction_jobs_but_allows_parse(platform_module, tmp_path, actor_a, clock):
    c = platform_module("contracts")
    from task3_tests.test_jobs import manifest
    connection = JobConnection(tmp_path / "uncertain.sqlite")
    try:
        jobs = platform_module("job_store").JobStore(connection, clock)
        first = jobs.create_receiving(actor_a, spec(c, mode="extract"), "first")
        jobs.finalize(first.task_id, manifest())
        lease = jobs.claim("worker")
        connection.db.execute("INSERT INTO t3_inference_attempts(attempt_id,task_id,state,epoch) VALUES(?,?,?,?)",
                              ("e" * 32, first.task_id, "sent", lease.epoch))
        connection.commit()
        clock.advance(61)
        jobs.recover_expired()
        second = jobs.create_receiving(actor_a, spec(c, "c" * 64, "extract"), "second")
        jobs.finalize(second.task_id, manifest("b" * 64))
        assert jobs.claim("other") is None
        parse = jobs.create_receiving(actor_a, spec(c, "d" * 64), "parse")
        jobs.finalize(parse.task_id, manifest("c" * 64))
        assert jobs.claim("parse-worker").task_id == parse.task_id
    finally:
        connection.db.close()


def test_entity_adapter_does_not_complete_when_extra_schema_fails(platform_module, tmp_path):
    module = platform_module("entity_step")
    c = platform_module("contracts")
    from models.schemas import AnnouncementContent
    from models.inference import InferenceResponse
    from data_loader.attachment_parser import AttachmentParseOutcome
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime").task("a" * 32)
    paths.inputs.mkdir(parents=True)
    html = paths.inputs / "t20990101_1.html"
    html.write_bytes(b"<p>synthetic</p>")
    prepared = c.PreparedInput(html.stem, html, None, hashlib.sha256(html.read_bytes()).hexdigest(), None)
    parsed = module.ParsedAnnouncement(AnnouncementContent(announcement_id=html.stem, html_text="合成设备"),
                                      AttachmentParseOutcome({}, {}, [], [], [], "no_attachment"), c.content_sha256(prepared.to_dto()))
    class Transport:
        task_id, provider, model = "a" * 32, "deepseek", "synthetic"
        def __call__(self, request):
            return InferenceResponse('malformed' if request.layer == "extra" else '{"entities":[{"product_name":"合成设备"}]}', None, None, None)
    result = module.run_entity_step(c.Lease("a" * 32, 1, "worker"), prepared, parsed, Transport(), paths)
    assert result.stage_status == "partial" and result.result.success is True
    assert result.diagnostics["extra_valid"] is False


def test_intention_only_empty_is_not_a_published_target_gap(platform_module):
    from models.schemas import AnnouncementContent
    module=platform_module('entity_step')
    content=AnnouncementContent(announcement_id='t20990101_1',title='框架协议第一阶段入围公告',
        html_text='仅征集，尚无实际成交。主要标的信息：打印机',html_tables=[[['货物名称'],['打印机']]])
    assert module.source_has_targets(content) is False
    actual=content.model_copy(update={'title':'框架协议第二阶段订单成交公告','html_text':'主要标的信息：打印机已实际成交'})
    assert module.source_has_targets(actual) is True


def test_long_model_wait_pulses_on_sql_owner_thread(ledger_setup, clock):
    import threading, time
    from types import SimpleNamespace
    from models.inference import InferenceResponse
    class WaitingBackend(Backend):
        def send(self, request, *, timeout):
            self.sent += 1
            clock.advance(50)
            time.sleep(0.04)
            clock.advance(50)
            return InferenceResponse('{}', {"input_tokens": 1, "output_tokens": 1}, None, None)
    jobs, _, transport, backend, request, _ = ledger_setup(backend=WaitingBackend())
    jobs.limits = SimpleNamespace(**{**vars(jobs.limits), "heartbeat_seconds": 0.01})
    pulses = []
    owner_thread = threading.get_ident()
    def pulse():
        assert threading.get_ident() == owner_thread
        pulses.append(1)
        jobs.heartbeat(transport.store.lease)
    transport.pulse = pulse
    response = transport(request)
    assert response.content == '{}' and backend.sent == 1 and len(pulses) >= 1
