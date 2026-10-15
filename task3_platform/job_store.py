"""MySQL persistent admission/queue with session lock and epoch-fenced writes.

The caller owns a dedicated raw DictCursor connection, never a shared API connection.
File preservation means disk reservations are not released at job completion.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
import re
import secrets

from .auth import authorize_upload, require_owner, _now, _utc, _sql_time
from .contracts import (Job, Lease, Checkpoint, PlatformError, canonical_bytes,
                        content_sha256, strict_json_loads, validate_task_id,
                        validate_announcement_id, PHASES)
from .limits import Limits

_TERMINAL = {"validated_only", "completed", "partial", "failed", "cancelled", "needs_confirmation"}
_EDGES = {
    "running": {"validating", "validated_only", "partial", "failed", "cancelled", "needs_confirmation"},
    "validating": {"completed", "partial", "failed", "cancelled", "needs_confirmation"},
}


@contextmanager
def transaction(connection):
    connection.begin()
    try:
        with connection.cursor() as cursor:
            yield cursor
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def job_from_row(row):
    return Job(*(row[key] for key in ("task_id", "upload_dataset_id", "owner_id", "mode", "status", "phase",
                                     "input_fingerprint", "processing_fingerprint", "epoch")))


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise PlatformError("invalid_fingerprint")
    return value


class JobStore:
    def __init__(self, connection, clock=_now, *, limits=None):
        self.connection = connection
        self.clock = clock
        self.limits = limits or Limits()
        self._lock_token = None
        # One processing worker for this server, including separate acceptance DBs.
        self._lock_name = "procurement.task3.processing_worker"

    def _runtime(self, cursor):
        cursor.execute("SELECT * FROM t3_runtime_state WHERE singleton_id=1 FOR UPDATE")
        row = cursor.fetchone()
        if not row or row["schema_version"] != 1:
            raise PlatformError("platform_migration_required", 503)
        return row

    def _row(self, cursor, task_id, *, lock=False):
        validate_task_id(task_id)
        cursor.execute("SELECT * FROM t3_jobs WHERE task_id=%s" + (" FOR UPDATE" if lock else ""), (task_id,))
        row = cursor.fetchone()
        if row is None:
            raise PlatformError("task_not_found", 404)
        return row

    def get(self, actor, task_id):
        with self.connection.cursor() as cursor:
            row = self._row(cursor, task_id)
        require_owner(actor, row["owner_id"])
        return job_from_row(row)

    def _fenced(self, cursor, lease, *, allow_cancel=False):
        row = self._row(cursor, lease.task_id, lock=True)
        if (row["epoch"] != lease.epoch or row["worker_token"] != lease.worker_token
                or row["status"] not in {"running", "validating"}
                or not row["lease_until"] or _utc(row["lease_until"]) <= _utc(self.clock())):
            raise PlatformError("stale_worker_lease", 409)
        if row["cancel_requested"] and not allow_cancel:
            raise PlatformError("task_cancel_requested", 409)
        return row

    def create_receiving(self, actor, spec, request_key, *, guest_source_hash=None, public_demo=False):
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
            raise PlatformError("invalid_idempotency_key")
        payload = asdict(spec)
        request_fp = content_sha256(payload)
        total = sum(file.bytes for file in spec.files)
        if (not spec.files or any(type(file.bytes) is not int or file.bytes <= 0
                                  or file.bytes > self.limits.file_bytes for file in spec.files)
                or total > self.limits.received_bytes):
            raise PlatformError("upload_received_limit", 413)
        with transaction(self.connection) as cursor:
            runtime = self._runtime(cursor)
            cursor.execute("SELECT * FROM t3_jobs WHERE owner_id=%s AND request_key=%s", (actor.user_id, request_key))
            existing = cursor.fetchone()
            if existing:
                if existing["request_fingerprint"] != request_fp:
                    raise PlatformError("idempotency_conflict", 409)
                return job_from_row(existing)
            if actor.role == "guest":
                _sha(guest_source_hash)
                since = _sql_time(_utc(self.clock()) - timedelta(hours=1))
                cursor.execute("SELECT COUNT(*) AS n FROM t3_jobs WHERE guest_source_hash=%s AND created_at>=%s", (guest_source_hash, since))
                count = cursor.fetchone()["n"]
                cursor.execute("SELECT COUNT(*) AS n FROM t3_jobs WHERE guest_source_hash=%s "
                               "AND status IN ('receiving','queued','running','validating')", (guest_source_hash,))
                active = cursor.fetchone()["n"]
                authorize_upload(actor, spec, False, public_demo=public_demo, active_jobs=active, hourly_count=count)
            if runtime["reserved_bytes"] + total > self.limits.global_disk_bytes:
                raise PlatformError("global_disk_quota", 413)
            task_id = secrets.token_hex(16)
            dataset_id = "up_" + secrets.token_hex(16)
            cursor.execute("INSERT INTO t3_jobs(task_id,upload_dataset_id,owner_id,mode,status,phase,request_key,"
                           "request_fingerprint,processing_fingerprint,spec_json,created_at,reserved_bytes,guest_source_hash) "
                           "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                           (task_id, dataset_id, actor.user_id, spec.mode, "receiving", "preflight", request_key,
                            request_fp, spec.processing_fingerprint, canonical_bytes(payload).decode("utf-8"),
                            _sql_time(self.clock()), total, guest_source_hash if actor.role == "guest" else None))
            cursor.execute("UPDATE t3_runtime_state SET reserved_bytes=reserved_bytes+%s WHERE singleton_id=1", (total,))
            cursor.execute("INSERT INTO t3_datasets(upload_dataset_id,task_id,owner_id,availability,created_at) VALUES(%s,%s,%s,%s,%s)",
                           (dataset_id, task_id, actor.user_id, "staging", _sql_time(self.clock())))
            return job_from_row(self._row(cursor, task_id))

    def finalize(self, task_id, input_manifest):
        inputs = input_manifest.get("inputs") if isinstance(input_manifest, dict) else None
        if not isinstance(inputs, list) or not inputs or len(inputs) > self.limits.announcements:
            raise PlatformError("invalid_input_manifest")
        aids = set()
        for item in inputs:
            aid = validate_announcement_id(item["announcement_id"])
            if aid in aids:
                raise PlatformError("duplicate_announcement")
            aids.add(aid)
            _sha(item["html_sha"])
            if item.get("zip_sha") is not None:
                _sha(item["zip_sha"])
        fp = content_sha256(input_manifest)
        with transaction(self.connection) as cursor:
            self._runtime(cursor)
            row = self._row(cursor, task_id, lock=True)
            if row["status"] != "receiving":
                if row["input_fingerprint"] != fp:
                    raise PlatformError("input_manifest_conflict", 409)
                return job_from_row(row)
            cursor.execute("SELECT * FROM t3_jobs WHERE owner_id=%s AND input_fingerprint=%s "
                           "AND processing_fingerprint=%s AND mode=%s ORDER BY created_at,task_id LIMIT 1",
                           (row["owner_id"], fp, row["processing_fingerprint"], row["mode"]))
            duplicate = cursor.fetchone()
            if duplicate:
                cursor.execute("UPDATE t3_jobs SET status='cancelled',cancel_requested=TRUE,finished_at=%s WHERE task_id=%s",
                               (_sql_time(self.clock()), task_id))
                return job_from_row(duplicate)
            cursor.execute("SELECT COUNT(*) AS n FROM t3_jobs WHERE status='queued'")
            if cursor.fetchone()["n"] >= self.limits.queue_size:
                raise PlatformError("queue_capacity", 413)
            cursor.execute("UPDATE t3_jobs SET status='queued',input_fingerprint=%s,manifest_json=%s,accepted_at=%s WHERE task_id=%s",
                           (fp, canonical_bytes(input_manifest).decode("utf-8"), _sql_time(self.clock()), task_id))
            return job_from_row(self._row(cursor, task_id))

    def _acquire_worker_lock(self, worker_token):
        if self._lock_token is not None:
            return self._lock_token == worker_token
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s,0) AS acquired", (self._lock_name,))
            row = cursor.fetchone()
        if not row or row["acquired"] != 1:
            return False
        self._lock_token = worker_token
        return True

    def _release_worker_lock(self):
        if self._lock_token is not None:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s) AS released", (self._lock_name,))
            self._lock_token = None

    def claim(self, worker_token):
        if not isinstance(worker_token, str) or not 1 <= len(worker_token) <= 128:
            raise PlatformError("invalid_worker_token")
        had_lock = self._lock_token is not None
        if not self._acquire_worker_lock(worker_token):
            return None
        lease = None
        try:
            with transaction(self.connection) as cursor:
                runtime = self._runtime(cursor)
                active = runtime["active_task_id"]
                if active:
                    # A lock naming a task that no longer exists (or already finished)
                    # would otherwise block every queued job forever.
                    cursor.execute("SELECT status FROM t3_jobs WHERE task_id=%s", (active,))
                    holder = cursor.fetchone()
                    if holder is not None and holder["status"] in ("running", "validating"):
                        return None
                    cursor.execute("UPDATE t3_runtime_state SET active_task_id=NULL WHERE singleton_id=1 AND active_task_id=%s", (active,))
                    runtime = self._runtime(cursor)
                cursor.execute("SELECT COUNT(*) AS n FROM t3_inference_attempts WHERE state IN ('sent','uncertain')")
                uncertainty_gate = " AND mode='parse_only'" if cursor.fetchone()["n"] else ""
                cursor.execute("SELECT * FROM t3_jobs WHERE status='queued' AND cancel_requested=FALSE" + uncertainty_gate + " ORDER BY accepted_at,task_id LIMIT 1 FOR UPDATE")
                row = cursor.fetchone()
                if row:
                    lease = Lease(row["task_id"], row["epoch"] + 1, worker_token)
                    cursor.execute("UPDATE t3_jobs SET status='running',epoch=%s,worker_token=%s,lease_until=%s,"
                                   "started_at=COALESCE(started_at,%s) WHERE task_id=%s",
                                   (lease.epoch, worker_token, _sql_time(_utc(self.clock()) + timedelta(seconds=self.limits.lease_seconds)),
                                    _sql_time(self.clock()), row["task_id"]))
                    cursor.execute("UPDATE t3_runtime_state SET active_task_id=%s WHERE singleton_id=1", (row["task_id"],))
        finally:
            if lease is None and not had_lock:
                self._release_worker_lock()
        return lease

    def heartbeat(self, lease):
        with transaction(self.connection) as cursor:
            self._fenced(cursor, lease, allow_cancel=True)
            cursor.execute("UPDATE t3_jobs SET lease_until=%s WHERE task_id=%s", (_sql_time(_utc(self.clock()) + timedelta(seconds=self.limits.lease_seconds)), lease.task_id))

    def checkpoint(self, lease, aid, result_hash, db_state):
        """Parse-only checkpoint; entity adapter uses the same SQL inside its save transaction."""
        validate_announcement_id(aid)
        _sha(result_hash)
        if db_state not in {"confirmed", "validated_only"}:
            raise PlatformError("invalid_checkpoint_state")
        with transaction(self.connection) as cursor:
            row = self._fenced(cursor, lease)
            manifest = strict_json_loads(row["manifest_json"])
            items = [item for item in manifest["inputs"] if item["announcement_id"] == aid]
            if len(items) != 1:
                raise PlatformError("checkpoint_input_not_registered", 409)
            input_fp = content_sha256(items[0])
            cursor.execute("SELECT * FROM t3_item_checkpoints WHERE task_id=%s AND announcement_id=%s", (lease.task_id, aid))
            old = cursor.fetchone()
            if old:
                if (old["input_fingerprint"], old["processing_fingerprint"], old["result_sha256"], old["db_state"]) != (
                        input_fp, row["processing_fingerprint"], result_hash, db_state):
                    raise PlatformError("checkpoint_conflict", 409)
                return
            cursor.execute("INSERT INTO t3_item_checkpoints(task_id,announcement_id,input_fingerprint,processing_fingerprint,result_sha256,db_state,epoch,updated_at) "
                           "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                           (lease.task_id, aid, input_fp, row["processing_fingerprint"], result_hash, db_state, lease.epoch, _sql_time(self.clock())))

    def checkpoints(self, task_id):
        validate_task_id(task_id)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM t3_item_checkpoints WHERE task_id=%s", (task_id,))
            rows = cursor.fetchall()
        return {row["announcement_id"]: Checkpoint(*(row[key] for key in (
            "announcement_id", "input_fingerprint", "processing_fingerprint", "result_sha256", "db_state", "epoch"))) for row in rows}

    def request_cancel(self, actor, task_id):
        with transaction(self.connection) as cursor:
            self._runtime(cursor)
            row = self._row(cursor, task_id, lock=True)
            require_owner(actor, row["owner_id"])
            if row["status"] in _TERMINAL:
                return job_from_row(row)
            cursor.execute("UPDATE t3_jobs SET cancel_requested=TRUE WHERE task_id=%s", (task_id,))
            if row["status"] in {"receiving", "queued"}:
                cursor.execute("UPDATE t3_jobs SET status='cancelled',finished_at=%s WHERE task_id=%s", (_sql_time(self.clock()), task_id))
            return job_from_row(self._row(cursor, task_id))

    def retry(self, actor, task_id):
        """Explicit retry reuses confirmed checkpoints and never resets model budgets."""
        with transaction(self.connection) as cursor:
            self._runtime(cursor)
            row=self._row(cursor,task_id,lock=True)
            require_owner(actor,row["owner_id"])
            cursor.execute("SELECT COUNT(*) AS n FROM t3_inference_attempts WHERE task_id=%s AND state IN ('sent','uncertain')",(task_id,))
            if cursor.fetchone()["n"] or row["status"]=="needs_confirmation":
                raise PlatformError("inference_needs_confirmation",409)
            if row["status"] not in {"failed","cancelled"} or not row["manifest_json"]:
                raise PlatformError("task_not_retryable",409)
            cursor.execute("SELECT COUNT(*) AS n FROM t3_jobs WHERE status='queued'",())
            if cursor.fetchone()["n"]>=self.limits.queue_size: raise PlatformError("queue_full",503)
            cursor.execute("UPDATE t3_jobs SET status='queued',cancel_requested=FALSE,epoch=epoch+1,worker_token=NULL,lease_until=NULL,started_at=NULL,finished_at=NULL WHERE task_id=%s",(task_id,))
            return job_from_row(self._row(cursor,task_id))

    def transition(self, lease, status, phase):
        if phase not in PHASES:
            raise PlatformError("invalid_job_phase")
        terminal = status in _TERMINAL
        with transaction(self.connection) as cursor:
            self._runtime(cursor)
            row = self._fenced(cursor, lease, allow_cancel=status in {"cancelled", "needs_confirmation", "failed"})
            if status != row["status"] and status not in _EDGES.get(row["status"], set()):
                raise PlatformError("invalid_job_transition", 409)
            if status == "validated_only" and row["mode"] != "parse_only":
                raise PlatformError("invalid_job_transition", 409)
            if status == "completed" and row["mode"] != "extract":
                raise PlatformError("invalid_job_transition", 409)
            cursor.execute("UPDATE t3_jobs SET status=%s,phase=%s,finished_at=%s WHERE task_id=%s",
                           (status, phase, _sql_time(self.clock()) if terminal else None, lease.task_id))
            if terminal:
                cursor.execute("UPDATE t3_runtime_state SET active_task_id=NULL WHERE singleton_id=1 AND active_task_id=%s", (lease.task_id,))
        if terminal:
            self._release_worker_lock()

    def recover_expired(self):
        recovered = []
        with transaction(self.connection) as cursor:
            runtime = self._runtime(cursor)
            active = runtime["active_task_id"]
            if active:
                cursor.execute("SELECT status FROM t3_jobs WHERE task_id=%s", (active,))
                holder = cursor.fetchone()
                if holder is None or holder["status"] not in ("running", "validating"):
                    cursor.execute("UPDATE t3_runtime_state SET active_task_id=NULL WHERE singleton_id=1 AND active_task_id=%s", (active,))
            cursor.execute("SELECT * FROM t3_jobs WHERE status IN ('running','validating') AND lease_until<=%s FOR UPDATE", (_sql_time(self.clock()),))
            for row in cursor.fetchall():
                cursor.execute("SELECT COUNT(*) AS n FROM t3_inference_attempts WHERE task_id=%s AND state IN ('sent','uncertain')", (row["task_id"],))
                uncertain = cursor.fetchone()["n"] > 0
                expired_wall = row["started_at"] and (_utc(self.clock()) - _utc(row["started_at"])).total_seconds() > self.limits.job_seconds
                status = "needs_confirmation" if uncertain else ("cancelled" if row["cancel_requested"] else ("failed" if expired_wall else "queued"))
                if uncertain:
                    cursor.execute("UPDATE t3_inference_attempts SET state='uncertain',error_code='lease_expired' WHERE task_id=%s AND state='sent'", (row["task_id"],))
                cursor.execute("UPDATE t3_jobs SET status=%s,epoch=epoch+1,worker_token=NULL,lease_until=NULL,finished_at=%s WHERE task_id=%s",
                               (status, _sql_time(self.clock()) if status in _TERMINAL else None, row["task_id"]))
                cursor.execute("UPDATE t3_runtime_state SET active_task_id=NULL WHERE singleton_id=1 AND active_task_id=%s", (row["task_id"],))
                recovered.append(job_from_row(self._row(cursor, row["task_id"])))
        if recovered:
            self._release_worker_lock()
        return recovered
