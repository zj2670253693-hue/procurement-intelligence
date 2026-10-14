"""Serial workflow, durable per-input receipt, explicit partial/completion gates."""
from datetime import timedelta
from pathlib import Path
import hashlib
import os
import secrets
import shutil

from models.inference import InferenceHalt
from models.result_io import serialize_extraction_result
from .contracts import PlatformError, content_sha256, canonical_bytes, strict_json_loads
from .entity_step import ParsedAnnouncement, EntityStepResult
from .job_store import job_from_row, transaction
from .auth import _utc
from .paths import reject_link_ancestors


def atomic_json(path, payload):
    path = reject_link_ancestors(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = canonical_bytes(payload)
    temporary = path.with_suffix(path.suffix + ".tmp")
    reject_link_ancestors(temporary)
    with open(temporary, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    return hashlib.sha256(data).hexdigest()


class ProcessingWorker:
    def __init__(self, jobs, steps, clock):
        self.jobs, self.steps, self.clock = jobs, steps, clock
        self.worker_token = secrets.token_hex(16)

    def _row(self, task_id):
        with self.jobs.connection.cursor() as cursor:
            return self.jobs._row(cursor, task_id)

    def _pulse(self, lease):
        row = self._row(lease.task_id)
        if row["started_at"] and (_utc(self.clock()) - _utc(row["started_at"])).total_seconds() > self.jobs.limits.job_seconds:
            raise PlatformError("job_timeout", 408)
        if row["cancel_requested"]:
            raise PlatformError("task_cancel_requested", 409)
        self.jobs.heartbeat(lease)

    def _diagnostics(self, lease, diagnostics):
        with transaction(self.jobs.connection) as cursor:
            self.jobs._fenced(cursor, lease, allow_cancel=True)
            cursor.execute("UPDATE t3_jobs SET diagnostics_json=%s WHERE task_id=%s", (canonical_bytes(diagnostics).decode("utf-8"), lease.task_id))

    def _write_receipt(self, lease, path, item):
        size = len(canonical_bytes(item))
        root = self.steps.paths.task(lease.task_id).inputs.parent
        actual = 0
        for file in root.rglob("*"):
            reject_link_ancestors(file)
            if file.is_file():
                actual += file.stat().st_size
        with transaction(self.jobs.connection) as cursor:
            runtime = self.jobs._runtime(cursor)
            row = self.jobs._fenced(cursor, lease)
            wanted = max(row["reserved_bytes"], actual + size)
            increase = wanted - row["reserved_bytes"]
            if wanted > self.jobs.limits.job_disk_bytes or runtime["reserved_bytes"] + increase > self.jobs.limits.global_disk_bytes:
                raise PlatformError("runtime_disk_quota", 413)
            if shutil.disk_usage(root).free < size + self.jobs.limits.reserve_disk_bytes:
                raise PlatformError("disk_reserve_required", 503)
            cursor.execute("UPDATE t3_jobs SET reserved_bytes=%s WHERE task_id=%s", (wanted, lease.task_id))
            cursor.execute("UPDATE t3_runtime_state SET reserved_bytes=reserved_bytes+%s WHERE singleton_id=1", (increase,))
        return atomic_json(path, item)

    def run_once(self):
        self.jobs.recover_expired()
        lease = self.jobs.claim(self.worker_token)
        if lease is None:
            return None
        job = job_from_row(self._row(lease.task_id))
        pulse = lambda: self._pulse(lease)
        diagnostics, items, good, partial = [], [], 0, False
        try:
            inputs = self.steps.prepare(lease, job)
            checkpoints = self.jobs.checkpoints(job.task_id)
            for prepared in inputs:
                pulse()
                old = checkpoints.get(prepared.announcement_id)
                receipt_epoch = old.epoch if old else lease.epoch
                path = self.steps.paths.task(job.task_id).extraction / "items" / str(receipt_epoch) / (prepared.announcement_id + ".json")
                if old:
                    if (old.input_fingerprint != content_sha256(prepared.to_dto()) or old.processing_fingerprint != job.processing_fingerprint
                            or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != old.result_sha256):
                        raise PlatformError("checkpoint_integrity", 409)
                    item = strict_json_loads(path.read_bytes())
                    items.append(item)
                    good += 1
                    partial = partial or item.get("stage_status") == "partial"
                    continue
                try:
                    self.jobs.transition(lease, "running", "parsing")
                    parsed = self.steps.parse(prepared, pulse)
                    pulse()
                    if job.mode == "parse_only":
                        item = {"announcement_id": prepared.announcement_id, "validated_only": True,
                                "input_fingerprint": parsed.input_fingerprint, "stage_status": "complete",
                                "failed_attachments": parsed.attachments.failed_files}
                        sha = self._write_receipt(lease, path, item)
                        self.jobs.checkpoint(lease, prepared.announcement_id, sha, "validated_only")
                        partial = partial or bool(parsed.attachments.failed_files)
                    else:
                        self.jobs.transition(lease, "running", "extraction")
                        outcome = self.steps.entity(lease, prepared, parsed, pulse)
                        pulse()
                        item = serialize_extraction_result(outcome.result, require_resolved=True)
                        item.update(stage_status=outcome.stage_status, diagnostics=outcome.diagnostics)
                        sha = self._write_receipt(lease, path, item)
                        if outcome.stage_status == "failed":
                            raise PlatformError("entity_extraction_failed")
                        self.jobs.transition(lease, "running", "entity_import")
                        self.steps.save(lease, prepared, outcome.result, sha)
                        partial = partial or outcome.stage_status == "partial"
                    good += 1
                    items.append(item)
                except InferenceHalt:
                    raise
                except PlatformError as error:
                    if error.code in {"task_cancel_requested", "job_timeout", "stale_worker_lease"}:
                        raise
                    diagnostics.append({"announcement_id": prepared.announcement_id, "code": error.code})
                    partial = True
            self._diagnostics(lease, diagnostics)
            if good == 0:
                raise PlatformError("no_successful_announcements")
            if job.mode == "parse_only":
                report = self.steps.finish_parse(lease, job, items, diagnostics, pulse)
                if not report.get("exports_verified"):
                    raise PlatformError("finalization_failed")
                status = "partial" if partial else "validated_only"
            else:
                self.jobs.transition(lease, "validating", "relation_build")
                report = self.steps.finish(lease, job, items, diagnostics, pulse)
                if not all(report.get(key) for key in ("relations_verified", "exports_verified", "dataset_published")):
                    raise PlatformError("finalization_failed")
                status = "partial" if partial else "completed"
            self.jobs.transition(lease, status, "export")
        except (PlatformError, InferenceHalt) as error:
            status = "needs_confirmation" if isinstance(error, InferenceHalt) and error.code == "inference_needs_confirmation" else (
                     "cancelled" if error.code == "task_cancel_requested" else "failed")
            try:
                self._diagnostics(lease, diagnostics + [{"code": error.code}])
                self.jobs.transition(lease, status, self._row(job.task_id)["phase"])
            except PlatformError:
                self.jobs.recover_expired()
        except Exception:
            self._diagnostics(lease, diagnostics + [{"code": "processing_stage_failed"}])
            self.jobs.transition(lease, "failed", self._row(job.task_id)["phase"])
        return job_from_row(self._row(job.task_id))
