"""Persistent SQL-boundary tests; named-lock competition needs real MySQL."""
import json
import sqlite3
from dataclasses import replace
import pytest


class JobCursor:
    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.db.cursor()
        self.special = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, sql, params=()):
        self.connection.statements.append((sql, params))
        self.special = None
        if "GET_LOCK" in sql:
            self.connection.named_lock = True
            self.special = {"acquired": 1}
            return
        if "RELEASE_LOCK" in sql:
            self.connection.named_lock = False
            self.special = {"released": 1}
            return
        self.cursor.execute(sql.replace("%s", "?").replace(" FOR UPDATE", ""), params)

    def fetchone(self):
        if self.special is not None:
            return self.special
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]

    @property
    def rowcount(self):
        return self.cursor.rowcount


class JobConnection:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.statements = []
        self.named_lock = False
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS t3_runtime_state(singleton_id INTEGER PRIMARY KEY,
            schema_version INTEGER, reserved_bytes INTEGER DEFAULT 0, active_task_id TEXT);
          INSERT OR IGNORE INTO t3_runtime_state(singleton_id,schema_version) VALUES(1,1);
          CREATE TABLE IF NOT EXISTS t3_jobs(task_id TEXT PRIMARY KEY, upload_dataset_id TEXT UNIQUE,
            owner_id TEXT, mode TEXT, status TEXT, phase TEXT, request_key TEXT,
            request_fingerprint TEXT, input_fingerprint TEXT, processing_fingerprint TEXT,
            spec_json TEXT, manifest_json TEXT, diagnostics_json TEXT, epoch INTEGER DEFAULT 0,
            worker_token TEXT, lease_until TEXT, cancel_requested INTEGER DEFAULT 0,
            created_at TEXT, accepted_at TEXT, started_at TEXT, finished_at TEXT,
            received_bytes INTEGER DEFAULT 0, expanded_bytes INTEGER DEFAULT 0,
            reserved_bytes INTEGER DEFAULT 0, used_calls INTEGER DEFAULT 0,
            reserved_input_tokens INTEGER DEFAULT 0, reserved_output_tokens INTEGER DEFAULT 0,
            guest_source_hash TEXT, UNIQUE(owner_id,request_key));
          CREATE TABLE IF NOT EXISTS t3_datasets(upload_dataset_id TEXT PRIMARY KEY, task_id TEXT UNIQUE,
            owner_id TEXT, availability TEXT, task2_dataset_id TEXT, manifest_json TEXT, created_at TEXT);
          CREATE TABLE IF NOT EXISTS t3_item_checkpoints(task_id TEXT, announcement_id TEXT,
            input_fingerprint TEXT, processing_fingerprint TEXT, result_sha256 TEXT, db_state TEXT,
            epoch INTEGER, updated_at TEXT, PRIMARY KEY(task_id,announcement_id));
          CREATE TABLE IF NOT EXISTS t3_inference_attempts(attempt_id TEXT PRIMARY KEY,
            task_id TEXT, state TEXT, epoch INTEGER, error_code TEXT);
        """)

    def cursor(self):
        return JobCursor(self)

    def begin(self):
        self.db.execute("BEGIN")

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()


@pytest.fixture
def jobs_connection(tmp_path):
    connection = JobConnection(tmp_path / "jobs.sqlite")
    yield connection
    connection.db.close()


def spec(c, fingerprint="b" * 64, mode="parse_only"):
    return c.UploadSpec((c.FileSpec("t20990101_1.html", 100, ("a" * 64,)),), mode, fingerprint)


def manifest(sha="a" * 64):
    return {"files": [{"name": "t20990101_1.html", "bytes": 100, "sha256": sha}],
            "inputs": [{"announcement_id": "t20990101_1", "html_sha": sha, "zip_sha": None}]}


def test_single_claim_and_fenced_recovery(platform_module, jobs_connection, actor_a, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    jobs = [store.create_receiving(actor_a, spec(c), str(index)) for index in range(3)]
    for index, job in enumerate(jobs):
        store.finalize(job.task_id, manifest(str(index) * 64))
    claims = [store.claim(worker) for worker in ("worker-a", "worker-b", "worker-c")]
    assert sum(lease is not None for lease in claims) == 1
    lease = next(lease for lease in claims if lease is not None)
    assert jobs_connection.named_lock is True
    clock.advance(10)
    store.heartbeat(lease)
    row = jobs_connection.db.execute("SELECT lease_until FROM t3_jobs WHERE task_id=?", (lease.task_id,)).fetchone()
    assert row["lease_until"] == "2099-01-01 00:01:10.000000"
    clock.advance(61)
    recovered = store.recover_expired()
    assert recovered[0].status == "queued" and recovered[0].epoch == lease.epoch + 1
    with pytest.raises(c.PlatformError) as fenced:
        store.checkpoint(lease, "t20990101_1", "c" * 64, "confirmed")
    assert fenced.value.http_status == 409
    next_lease = store.claim("worker-new")
    assert next_lease is not None and next_lease.epoch > lease.epoch


def test_restart_reuses_confirmed_items(platform_module, jobs_connection, actor_a, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    job = store.create_receiving(actor_a, spec(c), "restart")
    store.finalize(job.task_id, manifest())
    lease = store.claim("worker")
    store.checkpoint(lease, "t20990101_1", "d" * 64, "confirmed")
    store2 = platform_module("job_store").JobStore(jobs_connection, clock)
    assert store2.get(actor_a, job.task_id).status == "running"
    records = store2.checkpoints(job.task_id)
    assert records["t20990101_1"].result_sha256 == "d" * 64
    assert records["t20990101_1"].input_fingerprint
    assert records["t20990101_1"].processing_fingerprint == "b" * 64


def test_idempotency_uses_owner_actual_bytes_and_processing(platform_module, jobs_connection, actor_a, actor_b, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    original = store.create_receiving(actor_a, spec(c), "idempotent")
    assert store.create_receiving(actor_a, spec(c), "idempotent").task_id == original.task_id
    with pytest.raises(c.PlatformError) as conflict:
        store.create_receiving(actor_a, spec(c, "c" * 64), "idempotent")
    assert conflict.value.http_status == 409
    changed_bytes = c.UploadSpec((c.FileSpec("t20990101_1.html", 101, ("a" * 64,)),), "parse_only", "b" * 64)
    with pytest.raises(c.PlatformError):
        store.create_receiving(actor_a, changed_bytes, "idempotent")
    store.finalize(original.task_id, manifest())
    duplicate = store.create_receiving(actor_a, spec(c), "duplicate")
    assert store.finalize(duplicate.task_id, manifest()).task_id == original.task_id
    other = store.create_receiving(actor_b, spec(c), "idempotent")
    assert store.finalize(other.task_id, manifest()).task_id != original.task_id
    changed = store.create_receiving(actor_a, spec(c, "c" * 64), "new-prompt")
    assert store.finalize(changed.task_id, manifest()).task_id != original.task_id


def test_expired_sent_attempt_never_requeues(platform_module, jobs_connection, actor_a, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    job = store.create_receiving(actor_a, spec(c), "sent")
    store.finalize(job.task_id, manifest())
    lease = store.claim("worker")
    jobs_connection.db.execute("INSERT INTO t3_inference_attempts(attempt_id,task_id,state,epoch) VALUES(?,?,?,?)",
                               ("e" * 32, job.task_id, "sent", lease.epoch))
    jobs_connection.commit()
    clock.advance(61)
    recovered = store.recover_expired()
    assert recovered[0].status == "needs_confirmation"
    assert store.claim("replacement") is None
    row = jobs_connection.db.execute("SELECT state FROM t3_inference_attempts").fetchone()
    assert row["state"] == "uncertain"


def test_cancel_stops_new_units_and_invalid_transition(platform_module, jobs_connection, actor_a, actor_b, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    job = store.create_receiving(actor_a, spec(c), "cancel")
    store.finalize(job.task_id, manifest())
    lease = store.claim("worker")
    with pytest.raises(c.PlatformError):
        store.request_cancel(actor_b, job.task_id)
    with pytest.raises(c.PlatformError):
        store.transition(lease, "completed", "export")
    store.request_cancel(actor_a, job.task_id)
    with pytest.raises(c.PlatformError):
        store.checkpoint(lease, "t20990101_1", "d" * 64, "confirmed")
    store.transition(lease, "cancelled", "parsing")
    assert jobs_connection.named_lock is False
    assert store.claim("worker2") is None


def test_queue_capacity_and_parameterized_sql(platform_module, jobs_connection, actor_a, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    for index in range(10):
        job = store.create_receiving(actor_a, spec(c), "queue-" + str(index))
        store.finalize(job.task_id, manifest(str(index) * 64))
    extra = store.create_receiving(actor_a, spec(c), "overflow'; DROP TABLE t3_jobs; --")
    with pytest.raises(c.PlatformError) as full:
        store.finalize(extra.task_id, manifest("f" * 64))
    assert full.value.http_status == 413
    assert store.get(actor_a, extra.task_id).status == "receiving"
    assert all("DROP TABLE" not in sql for sql, _ in jobs_connection.statements)


def test_guest_rate_is_per_source_across_sessions(platform_module, jobs_connection, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    source = "f" * 64
    for index in range(5):
        guest = c.Actor(str(index) * 32, "guest")
        job = store.create_receiving(guest, spec(c), str(index), guest_source_hash=source, public_demo=True)
        store.request_cancel(guest, job.task_id)
    with pytest.raises(c.PlatformError):
        store.create_receiving(c.Actor("e" * 32, "guest"), spec(c), "sixth", guest_source_hash=source, public_demo=True)


def test_failed_checkpoint_rolls_back_and_does_not_publish(platform_module, jobs_connection, actor_a, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    job = store.create_receiving(actor_a, spec(c), "rollback")
    store.finalize(job.task_id, manifest())
    lease = store.claim("worker")
    jobs_connection.db.execute("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON t3_item_checkpoints BEGIN SELECT RAISE(FAIL,'synthetic'); END;")
    jobs_connection.commit()
    with pytest.raises(sqlite3.DatabaseError):
        store.checkpoint(lease, "t20990101_1", "d" * 64, "confirmed")
    assert store.checkpoints(job.task_id) == {}
    assert store.get(actor_a, job.task_id).status == "running"


def test_repeat_claim_keeps_existing_worker_session_lock(platform_module, jobs_connection, actor_a, clock):
    c = platform_module("contracts")
    store = platform_module("job_store").JobStore(jobs_connection, clock)
    job = store.create_receiving(actor_a, spec(c), "repeat-claim")
    store.finalize(job.task_id, manifest())
    lease = store.claim("worker")
    assert store.claim("worker") is None
    assert jobs_connection.named_lock is True
    store.heartbeat(lease)


def test_disk_reservations_survive_cancel_and_do_not_overcommit(platform_module, jobs_connection, actor_a, clock):
    from dataclasses import replace
    c = platform_module("contracts")
    limits = replace(platform_module("limits").Limits(), chunk_bytes=16, request_bytes=64,
                     file_bytes=400, received_bytes=800, html_bytes=16, member_bytes=128,
                     expanded_bytes=800, job_disk_bytes=1200, global_disk_bytes=1600, reserve_disk_bytes=400)
    store = platform_module("job_store").JobStore(jobs_connection, clock, limits=limits)
    for index in range(16):
        job = store.create_receiving(actor_a, spec(c), "disk-" + str(index))
        store.request_cancel(actor_a, job.task_id)
    with pytest.raises(c.PlatformError) as full:
        store.create_receiving(actor_a, spec(c), "disk-full")
    assert full.value.code == "global_disk_quota"
    assert jobs_connection.db.execute("SELECT reserved_bytes FROM t3_runtime_state").fetchone()[0] == 1600
