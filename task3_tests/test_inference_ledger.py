import importlib.util
from dataclasses import replace
from decimal import Decimal
import json
import pytest
from task3_tests.test_jobs import JobConnection, manifest


def inference_module():
    assert importlib.util.find_spec("models.inference") is not None, "Inference protocol not implemented"
    import models.inference as module
    return module


class InferenceConnection(JobConnection):
    def __init__(self, path):
        super().__init__(path)
        self.db.executescript("""
          DROP TABLE t3_inference_attempts;
          CREATE TABLE t3_inference_attempts(attempt_id TEXT PRIMARY KEY,task_id TEXT,request_hash TEXT,
            announcement_id TEXT,epoch INTEGER,state TEXT,request_json TEXT,response_json TEXT,
            reserved_input_tokens INTEGER,reserved_output_tokens INTEGER,error_code TEXT,
            created_at TEXT,sent_at TEXT,responded_at TEXT,UNIQUE(task_id,request_hash));
          CREATE TABLE t3_users(user_id TEXT PRIMARY KEY,role TEXT,enabled INTEGER);
        """)


class Backend:
    def __init__(self, response=None, failure=None):
        self.sent = 0
        self.response = response
        self.failure = failure

    def send(self, request, *, timeout):
        assert timeout == 120
        self.sent += 1
        if self.failure:
            raise self.failure
        return self.response


class Counter:
    verified = True
    profile_sha256 = "f" * 64

    def supports(self, provider, model):
        return provider == "deepseek" and model in {"synthetic-A", "synthetic-B"}

    def upper_bound(self, request):
        return 8


@pytest.fixture
def ledger_setup(platform_module, tmp_path, actor_a, clock):
    connection = InferenceConnection(tmp_path / "ledger.sqlite")
    connection.db.execute("INSERT INTO t3_users VALUES(?,?,?)", (actor_a.user_id, "reviewer", 1))
    connection.commit()
    jobs = platform_module("job_store").JobStore(connection, clock)
    def setup(*, budget=None, backend=None, counter=None):
        protocol = inference_module()
        c = platform_module("contracts")
        budget = budget or c.ModelBudget(8, 1024, 100000)
        spec = c.UploadSpec((c.FileSpec("t20990101_1.html", 100, ("a" * 64,)),), "extract", "b" * 64, budget)
        job = jobs.create_receiving(actor_a, spec, "inference")
        jobs.finalize(job.task_id, manifest())
        lease = jobs.claim("worker")
        ledger = platform_module("ledger").LedgerStore(jobs, lease)
        module = platform_module("model_transport")
        policy = module.InferencePolicy(enabled=lambda: True, authorized_owner_ids={actor_a.user_id},
                                       maximum_budget=c.ModelBudget(8, 1024, 100000),
                                       allowed_models={("deepseek", "synthetic-A"), ("deepseek", "synthetic-B")},
                                       counter_profiles={"f" * 64})
        backend = backend or Backend(protocol.InferenceResponse('{"entities":[]}', None, None, None))
        transport = module.LedgerTransport(ledger, policy, backend, counter or Counter())
        request = protocol.InferenceRequest(job.task_id, "t20990101_1", "summary", "", 0,
                                            "deepseek", "synthetic-A", {"temperature": Decimal("0.1"), "max_tokens": 64, "chunk_policy": "synthetic-v1"},
                                            ({"role": "system", "content": "synthetic"}, {"role": "user", "content": "test"}))
        return jobs, ledger, transport, backend, request, connection
    yield setup
    connection.db.close()


def test_timeout_is_one_sent_attempt_and_halts_all_layers(ledger_setup):
    p = inference_module()
    backend = Backend(failure=TimeoutError("synthetic timeout credential must not leak"))
    _, ledger, transport, _, request, connection = ledger_setup(backend=backend)
    for layer in ("summary", "details", "extra", "base"):
        with pytest.raises(p.InferenceHalt) as halted:
            transport(replace(request, layer=layer))
        assert "credential" not in str(halted.value)
    assert backend.sent == 1
    row = dict(connection.db.execute("SELECT * FROM t3_inference_attempts").fetchone())
    assert row["state"] == "uncertain"
    assert ledger.usage_summary()["usage_unknown"] is True


@pytest.mark.parametrize("layer", ["base", "summary", "details", "extra"])
def test_extractor_does_not_swallow_halt_or_create_sdk(layer, monkeypatch):
    p = inference_module()
    import extractor.llm_extractor as module
    sdk = []
    monkeypatch.setattr(module, "OpenAI", lambda **kwargs: sdk.append(kwargs))
    failure = p.InferenceHalt("synthetic_halt")
    class HaltTransport:
        task_id = "a" * 32
        def __call__(self, request):
            raise failure
    extractor = module.LLMExtractor(provider="deepseek", model="synthetic-A", call_transport=HaltTransport())
    assert sdk == [] and extractor.api_key is None
    with pytest.raises(p.InferenceHalt) as propagated:
        if layer == "base":
            extractor.extract("source", announcement_id="t20990101_1")
        elif layer == "summary":
            extractor.extract_summary("source", has_tables=False, detail_status="no_attachment", announcement_id="t20990101_1")
        elif layer == "details":
            extractor.extract_details(texts={"spec.txt": "source"}, tables={}, announcement_id="t20990101_1")
        else:
            extractor.extract_extra_info("source", "t20990101_1")
    assert propagated.value is failure


def test_pipeline_future_preserves_halt(tmp_path, monkeypatch):
    p = inference_module()
    from pipeline import EntityExtractionPipeline
    import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "not-written")
    path = tmp_path / "t20990101_1.html"
    path.write_text("<p>synthetic</p>", encoding="utf-8")
    pipeline = object.__new__(EntityExtractionPipeline)
    pipeline.max_workers = 1
    failure = p.InferenceHalt("uncertain")
    def halted(_path):
        raise failure
    pipeline.process_single_file = halted
    with pytest.raises(p.InferenceHalt) as propagated:
        pipeline.process_directory(str(tmp_path))
    assert propagated.value is failure
    assert not (tmp_path / "not-written").exists()


def test_received_invalid_json_is_not_resent(ledger_setup):
    p = inference_module()
    from extractor.llm_extractor import LLMExtractor
    backend = Backend(p.InferenceResponse("malformed response", None, "request-synthetic", None))
    _, ledger, transport, _, _, connection = ledger_setup(backend=backend)
    extractor = LLMExtractor(provider="deepseek", model="synthetic-A", call_transport=transport)
    for _ in range(2):
        result = extractor.extract_summary("synthetic", has_tables=False, detail_status="no_attachment", announcement_id="t20990101_1")
        assert result.success is False
    assert backend.sent == 1
    stored = dict(connection.db.execute("SELECT * FROM t3_inference_attempts").fetchone())
    assert stored["state"] == "received"
    assert json.loads(stored["response_json"])["content"] == "malformed response"
    summary = ledger.usage_summary()
    assert summary["sent_calls"] == 1 and summary["actual_input_tokens"] is None
    assert summary["actual_output_tokens"] is None and summary["usage_unknown"] is True


def test_injected_json_preserves_precision_and_rejects_duplicate_or_truncated(monkeypatch):
    p = inference_module()
    from extractor.llm_extractor import LLMExtractor
    class Transport:
        task_id = "a" * 32
        def __call__(self, request):
            return p.InferenceResponse("{}", None, None, None)
    extractor = LLMExtractor(provider="deepseek", model="synthetic-A", call_transport=Transport())
    values = extractor._parse_entities('{"entities":[{"product_name":"synthetic","unit_price":1.2300000000000000000000000001}]}')
    assert values[0].unit_price == "1.2300000000000000000000000001"
    for text in ('{"entities":[],"entities":[]}', '{"entities":[{"product_name":"synthetic"}'):
        with pytest.raises(ValueError):
            extractor._parse_json_response(text)


def test_unknown_counter_never_sends(ledger_setup):
    p = inference_module()
    counter = Counter()
    counter.verified = False
    _, _, transport, backend, request, connection = ledger_setup(counter=counter)
    with pytest.raises(p.InferenceHalt):
        transport(request)
    assert backend.sent == 0
    assert connection.db.execute("SELECT COUNT(*) FROM t3_inference_attempts").fetchone()[0] == 0


def test_budget_zero_and_revoked_user_never_send(platform_module, ledger_setup):
    p = inference_module()
    c = platform_module("contracts")
    _, _, transport, backend, request, _ = ledger_setup(budget=c.ModelBudget(0, 0, 0))
    with pytest.raises(p.InferenceHalt):
        transport(request)
    assert backend.sent == 0


def test_revocation_and_cancel_are_checked_before_new_attempt(ledger_setup, actor_a):
    p = inference_module()
    jobs, _, transport, backend, request, connection = ledger_setup()
    connection.db.execute("UPDATE t3_users SET enabled=0")
    connection.commit()
    with pytest.raises(p.InferenceHalt):
        transport(request)
    assert backend.sent == 0
    jobs.request_cancel(actor_a, request.task_id)
    with pytest.raises(p.InferenceHalt):
        transport(request)
    assert backend.sent == 0


def test_cache_keys_change_with_model_source_and_chunk_strategy(ledger_setup):
    _, ledger, transport, backend, request, _ = ledger_setup()
    first = transport(request)
    assert transport(request) == first and backend.sent == 1
    transport(replace(request, model="synthetic-B"))
    transport(replace(request, source_file="spec.txt", chunk_index=1))
    transport(replace(request, parameters={**request.parameters, "chunk_policy": "synthetic-v2"}))
    assert backend.sent == 4
    assert ledger.usage_summary()["reserved_input_tokens"] == 32


def test_provider_backend_disables_hidden_retries_and_keeps_usage(platform_module, monkeypatch):
    p = inference_module()
    from types import SimpleNamespace
    module = platform_module("model_transport")
    constructors, sent = [], []
    def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2),
                               model="synthetic-reported-model", id="synthetic-request")
    def sdk(**kwargs):
        constructors.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(module, "OpenAI", sdk)
    backend = module.OpenAIBackend(api_key="synthetic-not-a-secret", base_url="https://provider.invalid/v1")
    request = p.InferenceRequest("a" * 32, "t20990101_1", "summary", "", 0, "deepseek", "synthetic-A",
                                 {"temperature": Decimal("0.1"), "max_tokens": 64},
                                 ({"role": "user", "content": "test"},))
    response = backend.send(request, timeout=120)
    assert constructors[0]["max_retries"] == 0 and constructors[0]["timeout"] == 120
    assert sent[0]["temperature"] == 0.1 and sent[0]["max_tokens"] == 64
    assert response.usage == {"input_tokens": 7, "output_tokens": 2}
    assert response.reported_model == "synthetic-reported-model"
    assert response.provider_request_id == "synthetic-request"


def test_legacy_retry_wrapper_does_not_retry_explicit_halt(monkeypatch):
    p = inference_module()
    from types import SimpleNamespace
    from extractor.llm_extractor import LLMExtractor
    import config
    monkeypatch.setattr(config, "LLM_RETRY_DELAY", 0)
    calls = []
    failure = p.InferenceHalt("must_not_retry")
    def stop(**kwargs):
        calls.append(kwargs)
        raise failure
    extractor = object.__new__(LLMExtractor)
    extractor.model = "synthetic"
    extractor.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=stop)))
    with pytest.raises(p.InferenceHalt) as propagated:
        extractor._call_llm("synthetic", "test")
    assert propagated.value is failure and len(calls) == 1


def test_response_persistence_failure_does_not_send_again_after_transport_restart(ledger_setup, platform_module):
    p = inference_module()
    _, ledger, transport, backend, request, connection = ledger_setup()
    connection.db.execute("CREATE TRIGGER reject_response BEFORE UPDATE ON t3_inference_attempts WHEN NEW.state='received' BEGIN SELECT RAISE(FAIL,'synthetic response persistence'); END;")
    connection.commit()
    with pytest.raises(p.InferenceHalt):
        transport(request)
    restarted = platform_module("model_transport").LedgerTransport(ledger, transport.policy, backend, Counter())
    with pytest.raises(p.InferenceHalt):
        restarted(request)
    assert backend.sent == 1
    assert connection.db.execute("SELECT state FROM t3_inference_attempts").fetchone()[0] == "uncertain"


def test_budget_rejection_is_before_second_send(ledger_setup, platform_module):
    p = inference_module()
    c = platform_module("contracts")
    _, _, transport, backend, request, _ = ledger_setup(budget=c.ModelBudget(1, 8, 64))
    transport(request)
    with pytest.raises(p.InferenceHalt):
        transport(replace(request, layer="extra"))
    assert backend.sent == 1


@pytest.mark.parametrize("usage,observed", [
    ({"input_tokens": 7, "output_tokens": 2}, {"sent_calls": 1, "input_tokens": 7, "output_tokens": 2, "usage_unknown": False}),
    (None, {"sent_calls": 1, "input_tokens": None, "output_tokens": None, "usage_unknown": True}),
])
def test_budget_refusal_keeps_stable_code_separates_bounds_from_real_usage_and_never_resends(ledger_setup, platform_module, usage, observed):
    p = inference_module()
    c = platform_module("contracts")
    backend = Backend(p.InferenceResponse('{"entities":[]}', usage, "synthetic-request", "synthetic-A"))
    _, ledger, transport, _, request, connection = ledger_setup(budget=c.ModelBudget(1, 8, 64), backend=backend)
    transport(request)
    for _ in range(2):
        with pytest.raises(p.InferenceHalt) as refused:
            transport(replace(request, layer="extra"))
        assert refused.value.code == "inference_budget_exhausted"
        assert refused.value.details == {
            "budget": {"max_calls": 1, "max_input_tokens": 8, "max_output_tokens": 64},
            "required_total": {"calls": 2, "input_tokens": 16, "output_tokens": 128},
            "next_request_bound": {"input_tokens": 8, "output_tokens": 64},
            "observed_usage": observed}
        assert "synthetic-request" not in str(refused.value.details)
    assert backend.sent == 1
    assert ledger.usage_summary()["sent_calls"] == 1
    row = connection.db.execute("SELECT used_calls,reserved_input_tokens,reserved_output_tokens FROM t3_jobs WHERE task_id=?", (request.task_id,)).fetchone()
    assert tuple(row) == (1, 8, 64)
