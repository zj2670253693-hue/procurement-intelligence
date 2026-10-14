"""Only t3_ writes; entity+source+checkpoint form one fenced transaction."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import zipfile

from models.result_io import serialize_extraction_result
from database.detail_import import deduplicate_import_details
from .auth import require_owner, _sql_time
from .contracts import (Checkpoint, PlatformError, canonical_bytes, content_sha256,
                        strict_json_loads, validate_dataset_id, validate_announcement_id)
from .filters import FIELDS, numeric_projection, page_window
from .job_store import JobStore, transaction
from .paths import reject_link_ancestors
from .uploads import _hash_file


@dataclass(frozen=True)
class Page:
    items: list
    total: int
    page: int
    page_size: int
    upload_dataset_id: str
    availability: str


class DatasetRepository:
    def __init__(self, connection, *, jobs=None, paths=None):
        self.connection = connection
        self.jobs = jobs or JobStore(connection)
        self.paths = paths

    def dataset(self, actor, dataset_id, *, queryable=True):
        validate_dataset_id(dataset_id)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT d.*,j.status AS job_status,j.mode FROM t3_datasets d JOIN t3_jobs j ON j.task_id=d.task_id WHERE d.upload_dataset_id=%s", (dataset_id,))
            row = cursor.fetchone()
        if row is None:
            raise PlatformError("dataset_not_found", 404)
        require_owner(actor, row["owner_id"])
        if queryable and (row["availability"] not in {"completed", "partial"} or row["job_status"] not in {"completed", "partial"} or row["mode"] != "extract"):
            raise PlatformError("dataset_not_published", 409)
        return row

    def save_announcement(self, lease, prepared, result, *, result_sha256=None):
        validate_announcement_id(prepared.announcement_id)
        if result.announcement_id != prepared.announcement_id or self.paths is None:
            raise PlatformError("entity_input_scope_mismatch", 409)
        task_paths = self.paths.task(lease.task_id)
        expected = task_paths.inputs / (prepared.announcement_id + ".html")
        if prepared.html_path != expected or _hash_file(reject_link_ancestors(expected)) != prepared.html_sha:
            raise PlatformError("entity_input_integrity", 409)
        if prepared.zip_path and (prepared.zip_path != task_paths.inputs / (prepared.announcement_id + ".zip") or _hash_file(reject_link_ancestors(prepared.zip_path)) != prepared.zip_sha):
            raise PlatformError("entity_input_integrity", 409)
        payload = serialize_extraction_result(result, require_resolved=True)
        encoded = canonical_bytes(payload)
        result_hash = result_sha256 or hashlib.sha256(encoded).hexdigest()
        if result_sha256:
            receipt = reject_link_ancestors(task_paths.extraction / "items" / str(lease.epoch) / (prepared.announcement_id + ".json"))
            if not receipt.is_file() or _hash_file(receipt) != result_hash:
                raise PlatformError("entity_receipt_integrity", 409)
            saved = strict_json_loads(receipt.read_bytes())
            if canonical_bytes({key: saved[key] for key in payload}) != encoded:
                raise PlatformError("entity_receipt_integrity", 409)
        with transaction(self.connection) as cursor:
            self.jobs._runtime(cursor)
            job = self.jobs._fenced(cursor, lease)
            if job["mode"] != "extract":
                raise PlatformError("parse_only_entity_write_forbidden", 403)
            registered = strict_json_loads(job["manifest_json"])["inputs"]
            if prepared.to_dto() not in registered:
                raise PlatformError("entity_input_not_registered", 409)
            dataset_id, aid = job["upload_dataset_id"], prepared.announcement_id
            input_fp = content_sha256(prepared.to_dto())
            cursor.execute("SELECT * FROM t3_item_checkpoints WHERE task_id=%s AND announcement_id=%s", (lease.task_id, aid))
            old = cursor.fetchone()
            if old:
                if (old["input_fingerprint"], old["processing_fingerprint"], old["result_sha256"]) != (input_fp, job["processing_fingerprint"], result_hash):
                    raise PlatformError("checkpoint_conflict", 409)
                return Checkpoint(aid, input_fp, job["processing_fingerprint"], result_hash, old["db_state"], old["epoch"])
            for table in ("t3_entities", "t3_detail_entities", "t3_announcements"):
                cursor.execute(f"DELETE FROM {table} WHERE upload_dataset_id=%s AND announcement_id=%s", (dataset_id, aid))
            cursor.execute("INSERT INTO t3_announcements(upload_dataset_id,announcement_id,html_sha256,zip_sha256,html_relative_path,zip_relative_path,payload_json,diagnostics_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (dataset_id, aid, prepared.html_sha, prepared.zip_sha, prepared.html_path.relative_to(self.paths.root).as_posix(),
                 prepared.zip_path.relative_to(self.paths.root).as_posix() if prepared.zip_path else None, encoded.decode("utf-8"), "[]"))
            for index, entity in enumerate(result.entities, 1):
                values = entity.model_dump()
                projections = [numeric_projection(values[name], name) for name in ("unit_price", "quantity", "total_price")]
                cursor.execute("INSERT INTO t3_entities(upload_dataset_id,announcement_id,row_no," + ",".join(FIELDS) +
                    ",unit_price_numeric,quantity_numeric,total_price_numeric,unit_price_unit,quantity_unit,total_price_unit,payload_json) VALUES(" + ",".join(["%s"] * 17) + ")",
                    (dataset_id, aid, index, *(values[name] or None for name in FIELDS), *(p[0] for p in projections),
                     *(p[1] for p in projections), canonical_bytes(values).decode("utf-8")))
            for index, detail in enumerate(deduplicate_import_details(result.detail_entities), 1):
                value = detail.model_dump()
                cursor.execute("INSERT INTO t3_detail_entities(upload_dataset_id,announcement_id,row_no,payload_json,source_fingerprint) VALUES(%s,%s,%s,%s,%s)",
                               (dataset_id, aid, index, canonical_bytes(value).decode("utf-8"), content_sha256(value)))
            cursor.execute("INSERT INTO t3_item_checkpoints(task_id,announcement_id,input_fingerprint,processing_fingerprint,result_sha256,db_state,epoch,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (lease.task_id, aid, input_fp, job["processing_fingerprint"], result_hash, "confirmed", lease.epoch, _sql_time(self.jobs.clock())))
        return Checkpoint(aid, input_fp, job["processing_fingerprint"], result_hash, "confirmed", lease.epoch)

    def search(self, actor, dataset_id, filters, page=1, page_size=20):
        offset = page_window(page, page_size)
        dataset = self.dataset(actor, dataset_id)
        clauses, params = filters.sql()
        where = "upload_dataset_id=%s" + (" AND " + " AND ".join(clauses) if clauses else "")
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM t3_entities WHERE " + where, (dataset_id, *params))
            total = cursor.fetchone()["total"]
            cursor.execute("SELECT * FROM t3_entities WHERE " + where + " ORDER BY announcement_id,row_no LIMIT %s OFFSET %s", (dataset_id, *params, page_size, offset))
            rows = cursor.fetchall()
        items = [{**strict_json_loads(row["payload_json"]), "id": row["announcement_id"] + ":" + str(row["row_no"]),
                  "announcement_id": row["announcement_id"], "upload_dataset_id": dataset_id} for row in rows]
        return Page(items, total, page, page_size, dataset_id, dataset["availability"])

    def announcement(self, actor, dataset_id, aid):
        dataset = self.dataset(actor, dataset_id)
        validate_announcement_id(aid)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM t3_announcements WHERE upload_dataset_id=%s AND announcement_id=%s", (dataset_id, aid))
            row = cursor.fetchone()
        if not row:
            raise PlatformError("announcement_not_found", 404)
        path = reject_link_ancestors(self.paths.task(dataset["task_id"]).inputs / (aid + ".html"))
        if path.relative_to(self.paths.root).as_posix() != row["html_relative_path"] or _hash_file(path) != row["html_sha256"]:
            raise PlatformError("source_integrity", 409)
        files = []
        if row["zip_sha256"]:
            archive_path = reject_link_ancestors(path.with_suffix(".zip"))
            if _hash_file(archive_path) != row["zip_sha256"]:
                raise PlatformError("source_integrity", 409)
            with zipfile.ZipFile(archive_path) as archive:
                files = [item.filename for item in archive.infolist() if not item.is_dir()]
        return {"announcement_id": aid, "upload_dataset_id": dataset_id, "source_sha256": row["html_sha256"],
                "html_text": path.read_bytes().decode("utf-8-sig"), "attachments": files,
                "result": strict_json_loads(row["payload_json"]), "availability": dataset["availability"]}

    def publish(self, actor, dataset_id, state, manifest, *, lease=None):
        dataset = self.dataset(actor, dataset_id, queryable=False)
        if lease is None or lease.task_id != dataset["task_id"] or state not in {"completed", "partial"}:
            raise PlatformError("publication_lease_required", 403)
        with transaction(self.connection) as cursor:
            self.jobs._runtime(cursor)
            job = self.jobs._fenced(cursor, lease)
            if job["status"] != "validating" or job["mode"] != "extract":
                raise PlatformError("publication_stage_mismatch", 409)
            cursor.execute("SELECT COUNT(*) AS n FROM t3_artifacts WHERE upload_dataset_id=%s AND kind='standard_workbook'", (dataset_id,))
            if cursor.fetchone()["n"] != 1 or not dataset["task2_dataset_id"]:
                raise PlatformError("publication_artifacts_missing", 409)
            cursor.execute("SELECT COUNT(*) AS n FROM t3_item_checkpoints WHERE task_id=%s AND db_state='confirmed'", (lease.task_id,))
            count = cursor.fetchone()["n"]
            expected = len(strict_json_loads(job["manifest_json"])["inputs"])
            if (state == "completed" and count != expected) or not count:
                raise PlatformError("publication_coverage_mismatch", 409)
            cursor.execute("UPDATE t3_datasets SET availability=%s,manifest_json=%s WHERE upload_dataset_id=%s", (state, canonical_bytes(manifest).decode("utf-8"), dataset_id))
