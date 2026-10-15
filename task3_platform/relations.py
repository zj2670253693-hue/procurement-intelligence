"""Uploaded relationship versions: offline child, immutable import, owner binding."""
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from task2_query_service.artifacts import load_dataset
from task2_query_service.storage import MySQLRepository, import_dataset
from .contracts import PlatformError, canonical_bytes, strict_json_loads
from .datasets import DatasetRepository
from .job_store import JobStore, transaction
from .paths import reject_link_ancestors
from .uploads import _hash_file

ROOT = Path(__file__).resolve().parents[1]


def run_relation_child(html_root, raw_path, output_root, profile, *, pulse=lambda: None, timeout=600, max_output_bytes=512 * 1024**2):
    html_root, raw_path, output_root = [reject_link_ancestors(Path(path)) for path in (html_root, raw_path, output_root)]
    for source in (html_root, raw_path.parent):
        if source == output_root or source.is_relative_to(output_root) or output_root.is_relative_to(source):
            raise PlatformError("relation_output_overlap", 409)
    if not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
        raise PlatformError("relation_output_limit", 413)
    request = raw_path.parent / "relations.request.json"
    response = raw_path.parent / "relations.response.json"
    request.write_bytes(canonical_bytes({"html_root": str(html_root), "raw_path": str(raw_path), "output_root": str(output_root),
        "profile": profile, "response": str(response), "max_output_bytes": max_output_bytes}))
    environment = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
    environment.update(PYTHONPATH=str(ROOT), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1")
    process = subprocess.Popen([sys.executable, "-B", "-m", "task3_platform.relation_child", str(request)], cwd=ROOT,
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic()
    try:
        while process.poll() is None:
            pulse()
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise PlatformError("relation_timeout", 408)
            try:
                process.wait(timeout=min(10, remaining))
            except subprocess.TimeoutExpired:
                pass
        if process.returncode:
            raise PlatformError("relation_child_failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    receipt = strict_json_loads(reject_link_ancestors(response).read_bytes())
    version = receipt.get("dataset_id", "")
    if not re.fullmatch(r"ds_[0-9a-f]{64}", version):
        raise PlatformError("relation_receipt_invalid", 409)
    directory = reject_link_ancestors(output_root / version)
    data = load_dataset(directory)
    if data["manifest"].get("input_profile") != profile or _hash_file(directory / "manifest.json") != receipt["manifest_sha256"] or data["manifest"]["new_model_calls"] != 0:
        raise PlatformError("relation_receipt_invalid", 409)
    from task2_relationship_analysis.inputs import snapshot_inputs
    snapshot = snapshot_inputs(html_root, raw_path, raw_path, data["manifest"]["implementation"], input_profile=profile)
    if snapshot.input_manifest != data["manifest"]["input_manifest"] or snapshot.dataset_id != version:
        raise PlatformError("relation_input_drift", 409)
    return receipt


@dataclass(frozen=True)
class RelationBinding:
    upload_dataset_id: str
    task2_dataset_id: str
    manifest_sha256: str
    new_model_calls: int = 0  # Offline relation stage ONLY; upstream ledger is separate.


class ScopedRepository:
    def __init__(self, repository, version,upload_coverage=None):
        self.repository, self.version = repository, version
        self.upload_coverage=upload_coverage

    def resolve_version(self, requested=None):
        if requested is not None and requested != self.version:
            raise PlatformError("relation_version_not_owned", 403)
        return self.version

    def __getattr__(self, name):
        if name not in {"metadata", "status", "load", "evidence"}:
            raise AttributeError(name)
        def guarded(version, *args):
            self.resolve_version(version)
            return getattr(self.repository, name)(version, *args)
        return guarded


class RelationService:
    def __init__(self, connection, paths, *, jobs=None,import_connection_factory=None):
        self.connection, self.paths = connection, paths
        self.jobs = jobs or JobStore(connection)
        self.datasets = DatasetRepository(connection, jobs=self.jobs, paths=paths)
        self.import_connection_factory=import_connection_factory or self._import_connection

    @contextmanager
    def _import_connection(self):
        # Import transaction and durable lease use different connections on the
        # SAME owning worker thread, never a concurrently shared DB connection.
        database=getattr(self.connection,'db',None)
        if isinstance(database,bytes): database=database.decode('ascii')
        if not isinstance(database,str) or not re.fullmatch(r'procurement_task3_[A-Za-z0-9_]+',database):
            raise PlatformError('isolated_import_connection_required',503)
        from database.connection import DatabaseConfig
        import pymysql
        config=DatabaseConfig(database=database).to_dict()
        config.update(connect_timeout=10,read_timeout=15,write_timeout=15)
        connection=pymysql.connect(**config)
        try: yield connection
        finally: connection.close()

    def build_and_bind(self, lease, html_root, raw_path, provenance, *, pulse=lambda: None, max_output_bytes=512 * 1024**2):
        task = self.paths.task(lease.task_id)
        raw_path = reject_link_ancestors(Path(raw_path))
        if Path(html_root) != task.inputs or not raw_path.is_relative_to(task.extraction):
            raise PlatformError("relation_input_scope", 403)
        with transaction(self.connection) as cursor:
            self.jobs._runtime(cursor)
            job = self.jobs._fenced(cursor, lease)
            if job["mode"] != "extract" or job["status"] != "validating":
                raise PlatformError("relation_stage_mismatch", 409)
        profile = {"kind": "uploaded", "producer": provenance["producer"], "extraction_receipt_sha256": _hash_file(raw_path)}
        from .relation_disk import staging_root,promote
        staging=staging_root(self.paths,lease.task_id)/'artifacts'
        root = self.paths.root / "task2/artifacts"
        receipt = run_relation_child(html_root, raw_path, staging, profile, pulse=pulse, max_output_bytes=max_output_bytes)
        promote(self.paths,lease.task_id,receipt)
        pulse()
        # No activate(): uploaded versions never replace the official default.
        with self.import_connection_factory() as import_connection:
            imported = import_dataset(import_connection, root / receipt["dataset_id"],initialize_schema=False,pulse=pulse)
        pulse()
        if imported["dataset_id"] != receipt["dataset_id"]:
            raise PlatformError("relation_import_mismatch", 409)
        with transaction(self.connection) as cursor:
            self.jobs._runtime(cursor)
            job = self.jobs._fenced(cursor, lease)
            cursor.execute("SELECT task2_dataset_id FROM t3_datasets WHERE upload_dataset_id=%s FOR UPDATE", (job["upload_dataset_id"],))
            old = cursor.fetchone()["task2_dataset_id"]
            if old and old != receipt["dataset_id"]:
                raise PlatformError("relation_binding_conflict", 409)
            cursor.execute("UPDATE t3_datasets SET task2_dataset_id=%s WHERE upload_dataset_id=%s", (receipt["dataset_id"], job["upload_dataset_id"]))
        return RelationBinding(job["upload_dataset_id"], receipt["dataset_id"], receipt["manifest_sha256"])

    def repository_for(self, actor, upload_dataset_id):
        dataset = self.datasets.dataset(actor, upload_dataset_id)
        version = dataset["task2_dataset_id"]
        if not version:
            raise PlatformError("relation_not_ready", 409)
        root = self.paths.root / "task2/artifacts"
        repository = MySQLRepository(self.connection, cache_namespace=str(root), artifact_root=root)
        return ScopedRepository(repository, version,self.datasets.coverage(actor,upload_dataset_id))
