"""Persist reservation → sent → received/uncertain before accepting extraction."""
from dataclasses import asdict, dataclass
import secrets

from models.inference import InferenceResponse, InferenceHalt
from .auth import _sql_time
from .contracts import (canonical_bytes, content_sha256, strict_json_loads,
                        PlatformError, validate_announcement_id)
from .job_store import transaction


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    request_hash: str
    state: str


class LedgerStore:
    def __init__(self, jobs, lease):
        self.jobs = jobs
        self.connection = jobs.connection
        self.lease = lease

    def _authorized_user(self, cursor, job):
        cursor.execute("SELECT role,enabled FROM t3_users WHERE user_id=%s", (job["owner_id"],))
        row = cursor.fetchone()
        if not row or not row["enabled"] or row["role"] not in {"admin", "reviewer"}:
            raise InferenceHalt("inference_authorization_revoked")

    def reserve(self, lease, request, input_bound, output_bound):
        if request.task_id != lease.task_id or lease != self.lease:
            raise InferenceHalt("inference_scope_mismatch")
        validate_announcement_id(request.announcement_id)
        if any(type(value) is not int or value <= 0 for value in (input_bound, output_bound)):
            raise InferenceHalt("unverified_token_bound")
        payload = asdict(request)
        request_hash = content_sha256(payload)
        with transaction(self.connection) as cursor:
            self.jobs._runtime(cursor)
            job = self.jobs._fenced(cursor, lease)
            self._authorized_user(cursor, job)
            if job["mode"] != "extract":
                raise InferenceHalt("inference_mode_forbidden")
            manifest = strict_json_loads(job["manifest_json"])
            if request.announcement_id not in {item["announcement_id"] for item in manifest["inputs"]}:
                raise InferenceHalt("inference_input_not_registered")
            cursor.execute("SELECT * FROM t3_inference_attempts WHERE task_id=%s AND request_hash=%s", (lease.task_id, request_hash))
            old = cursor.fetchone()
            if old:
                if old["state"] in {"sent", "uncertain"}:
                    raise InferenceHalt("inference_needs_confirmation")
                return Attempt(old["attempt_id"], request_hash, old["state"])
            cursor.execute("SELECT COUNT(*) AS n FROM t3_inference_attempts WHERE task_id=%s AND state IN ('sent','uncertain')", (lease.task_id,))
            if cursor.fetchone()["n"]:
                raise InferenceHalt("inference_needs_confirmation")
            budget = strict_json_loads(job["spec_json"]).get("budget") or {}
            needed_calls = job["used_calls"] + 1
            needed_input = job["reserved_input_tokens"] + input_bound
            needed_output = job["reserved_output_tokens"] + output_bound
            if (needed_calls > budget.get("max_calls", 0)
                    or needed_input > budget.get("max_input_tokens", 0)
                    or needed_output > budget.get("max_output_tokens", 0)):
                # Keep a stable code and separate conservative reservations from measured
                # provider usage. Missing usage is unknown, not free. No request is sent.
                usage = self.usage_summary()
                raise InferenceHalt("inference_budget_exhausted", details={
                    "budget": {key: budget.get(key, 0) for key in
                               ("max_calls", "max_input_tokens", "max_output_tokens")},
                    "required_total": {"calls": needed_calls, "input_tokens": needed_input,
                                       "output_tokens": needed_output},
                    "next_request_bound": {"input_tokens": input_bound, "output_tokens": output_bound},
                    "observed_usage": {"sent_calls": usage["sent_calls"],
                                       "input_tokens": usage["actual_input_tokens"],
                                       "output_tokens": usage["actual_output_tokens"],
                                       "usage_unknown": usage["usage_unknown"]}})
            attempt_id = secrets.token_hex(16)
            cursor.execute("INSERT INTO t3_inference_attempts(attempt_id,task_id,request_hash,announcement_id,epoch,state,request_json,"
                           "reserved_input_tokens,reserved_output_tokens,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                           (attempt_id, lease.task_id, request_hash, request.announcement_id, lease.epoch, "reserved",
                            canonical_bytes(payload).decode("utf-8"), input_bound, output_bound, _sql_time(self.jobs.clock())))
            cursor.execute("UPDATE t3_jobs SET used_calls=used_calls+1,reserved_input_tokens=reserved_input_tokens+%s,"
                           "reserved_output_tokens=reserved_output_tokens+%s WHERE task_id=%s", (input_bound, output_bound, lease.task_id))
            return Attempt(attempt_id, request_hash, "reserved")

    def _attempt(self, cursor, attempt_id):
        cursor.execute("SELECT * FROM t3_inference_attempts WHERE attempt_id=%s AND task_id=%s FOR UPDATE", (attempt_id, self.lease.task_id))
        row = cursor.fetchone()
        if not row:
            raise InferenceHalt("inference_attempt_not_found")
        return row

    def mark_sent(self, attempt_id):
        with transaction(self.connection) as cursor:
            self.jobs._runtime(cursor)
            job = self.jobs._fenced(cursor, self.lease)
            self._authorized_user(cursor, job)
            row = self._attempt(cursor, attempt_id)
            if row["state"] != "reserved":
                raise InferenceHalt("inference_needs_confirmation")
            cursor.execute("UPDATE t3_inference_attempts SET state='sent',sent_at=%s,epoch=%s WHERE attempt_id=%s",
                           (_sql_time(self.jobs.clock()), self.lease.epoch, attempt_id))

    def record_response(self, attempt_id, response):
        if not isinstance(response, InferenceResponse) or not isinstance(response.content, str):
            raise InferenceHalt("invalid_inference_transport_response")
        with transaction(self.connection) as cursor:
            self.jobs._runtime(cursor)
            self.jobs._fenced(cursor, self.lease, allow_cancel=True)
            row = self._attempt(cursor, attempt_id)
            if row["state"] != "sent":
                raise InferenceHalt("inference_needs_confirmation")
            # Numeric API usage is integral; unknown remains null, never synthesized as zero.
            cursor.execute("UPDATE t3_inference_attempts SET state='received',response_json=%s,responded_at=%s WHERE attempt_id=%s",
                           (canonical_bytes(asdict(response)).decode("utf-8"), _sql_time(self.jobs.clock()), attempt_id))

    def response(self, attempt_id):
        with self.connection.cursor() as cursor:
            row = self._attempt(cursor, attempt_id)
        if row["state"] != "received" or not row["response_json"]:
            raise InferenceHalt("inference_needs_confirmation")
        return InferenceResponse(**strict_json_loads(row["response_json"]))

    def mark_uncertain(self, attempt_id, code):
        with transaction(self.connection) as cursor:
            self.jobs._runtime(cursor)
            row = self._attempt(cursor, attempt_id)
            if row["state"] == "received":
                return
            cursor.execute("UPDATE t3_inference_attempts SET state='uncertain',error_code=%s WHERE attempt_id=%s", (code, attempt_id))

    def require_clear(self, task_id):
        if task_id != self.lease.task_id:
            raise InferenceHalt("inference_scope_mismatch")
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM t3_inference_attempts WHERE task_id=%s AND state IN ('sent','uncertain')", (task_id,))
            if cursor.fetchone()["n"]:
                raise InferenceHalt("inference_needs_confirmation")

    def job_row(self):
        with self.connection.cursor() as cursor:
            return self.jobs._row(cursor, self.lease.task_id)

    def usage_summary(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM t3_inference_attempts WHERE task_id=%s", (self.lease.task_id,))
            rows = cursor.fetchall()
        sent = [row for row in rows if row["sent_at"]]
        responses = [strict_json_loads(row["response_json"]) if row["response_json"] else None for row in sent]
        def total(key):
            values = [(response.get("usage") or {}).get(key) if response else None for response in responses]
            if any(type(value) is not int or value < 0 for value in values):
                return None
            return sum(values)
        input_tokens, output_tokens = total("input_tokens"), total("output_tokens")
        return {"reserved_calls": len(rows), "sent_calls": len(sent),
                "reserved_input_tokens": sum(row["reserved_input_tokens"] for row in rows),
                "reserved_output_tokens": sum(row["reserved_output_tokens"] for row in rows),
                "actual_input_tokens": input_tokens, "actual_output_tokens": output_tokens,
                "usage_unknown": input_tokens is None or output_tokens is None,
                "uncertain_calls": sum(row["state"] in {"sent", "uncertain"} for row in rows)}
