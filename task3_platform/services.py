"""Per-request composition; no DDL, model client or processing thread on startup."""
from contextlib import contextmanager
from dataclasses import asdict
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
from .artifacts import ArtifactService
from .auth import AuthService, _now, secure_transport
from .contracts import Actor, PlatformError, canonical_bytes, strict_json_loads
from .datasets import DatasetRepository
from .job_store import JobStore
from .limits import Limits
from .paths import RuntimePaths
from .relations import RelationService
from .uploads import UploadService


@lru_cache(maxsize=1)
def processing_fingerprint():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256(b"task3-isolated-pipeline-v1")
    for directory in (root / "task3_platform", root / "task1_entity_extraction", root / "tools/task1_standard_export"):
        for path in sorted(directory.rglob("*.py")):
            if not any(name in path.parts for name in ("tests", "__pycache__", ".venv")):
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


class Services:
    def __init__(self, connection, paths, *, jobs=None, auth=None, secure=False,
                 processing_fingerprint=None, public_demo=False, guest_source_hash=None,
                 guest_actor=None, extract_authorizer=None,baseline_factory=None):
        self.connection, self.paths = connection, paths
        self.jobs = jobs or JobStore(connection)
        self.auth = auth or AuthService(connection)
        self.secure = secure
        self.processing_fingerprint = processing_fingerprint or globals()["processing_fingerprint"]()
        self.guest_actor = guest_actor
        self._baseline_factory,self._baseline=None if baseline_factory is None else baseline_factory,None
        self.uploads = UploadService(self.jobs, paths, self.jobs.limits, secure_transport=secure,
            public_demo=public_demo, guest_source_hash=guest_source_hash, extract_authorizer=extract_authorizer)
        self.datasets = DatasetRepository(connection, jobs=self.jobs, paths=paths)
        self.artifacts = ArtifactService(self.jobs, paths)
        self.relations = RelationService(connection, paths, jobs=self.jobs)

    @property
    def baseline(self):
        if self._baseline is None:
            if self._baseline_factory is None: raise PlatformError("official_read_adapter_required",503)
            self._baseline=self._baseline_factory()
        return self._baseline

    def actor(self, request, *, mutation=False, guest=False):
        token = request.cookies.get("t3_session")
        if token:
            if not self.secure:
                raise PlatformError("secure_transport_required", 403)
            actor = self.auth.require_actor(token)
            if mutation:
                self.auth.require_csrf(token, request.headers.get("x-csrf-token"), request.headers.get("origin"))
            return actor
        # A configured shared guest owner is NOT a browser-bound identity.
        # Anonymous entry remains disabled until isolated guest sessions exist.
        raise PlatformError("session_required", 401)

    def task_dto(self, actor, task_id):
        job = self.jobs.get(actor, task_id)
        with self.connection.cursor() as cursor:
            row = self.jobs._row(cursor, task_id)
            cursor.execute("SELECT COUNT(*) AS n FROM t3_item_checkpoints WHERE task_id=%s", (task_id,))
            done = cursor.fetchone()["n"]
            counts = []
            for table in ("t3_entities", "t3_detail_entities"):
                cursor.execute("SELECT COUNT(*) AS n FROM " + table + " WHERE upload_dataset_id=%s", (job.upload_dataset_id,))
                counts.append(cursor.fetchone()["n"])
            cursor.execute("SELECT state,response_json FROM t3_inference_attempts WHERE task_id=%s", (task_id,))
            attempts = cursor.fetchall()
        usage, unknown = {"input_tokens": 0, "output_tokens": 0}, 0
        for attempt in attempts:
            response = strict_json_loads(attempt["response_json"]) if attempt["response_json"] else {}
            tokens = response.get("usage")
            if attempt["state"] in {"sent", "uncertain"} or attempt["state"] == "received" and tokens is None:
                unknown += 1
            if tokens:
                for key in usage:
                    usage[key] += tokens.get(key, 0)
        manifest = strict_json_loads(row["manifest_json"]) if row["manifest_json"] else {}
        return {**asdict(job), "run_extraction": job.mode == "extract", "total": len(manifest.get("inputs", [])),
            "done": done, "success": done, "entities": counts[0], "detail_entities": counts[1],
            "diagnostics": strict_json_loads(row["diagnostics_json"]) if row["diagnostics_json"] else [],
            "model_ledger": {"calls_reserved": row["used_calls"], "attempts": len(attempts), "known_usage": usage,
                "unknown_usage_attempts": unknown, "offline_relationship_model_calls": 0}}

    def tasks(self, actor):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT task_id FROM t3_jobs WHERE owner_id=%s ORDER BY created_at DESC LIMIT 100", (actor.user_id,))
            ids = cursor.fetchall()
        return {"items": [self.task_dto(actor, row["task_id"]) for row in ids]}


@contextmanager
def configured_services(request,backend_root):
    if os.getenv("TASK3_ENABLED", "0") != "1":
        raise PlatformError("platform_not_ready", 503)
    database = os.getenv("TASK3_DB_NAME", "")
    origin, runtime = os.getenv("TASK3_PUBLIC_ORIGIN", ""), os.getenv("TASK3_RUNTIME_ROOT", "")
    if not re.fullmatch(r"procurement_task3_[a-zA-Z0-9_]+", database) or not origin or not runtime:
        raise PlatformError("platform_configuration_required", 503)
    from database.connection import DatabaseConfig, DatabaseManager
    manager = DatabaseManager(DatabaseConfig(database=database))
    official_manager=None
    try:
        try:
            manager.connect(create_db=False, verbose=False)
        except Exception:
            raise PlatformError("platform_storage_unavailable", 503) from None
        limits = Limits.from_env(os.environ)
        jobs = JobStore(manager._conn, limits=limits)
        with manager._conn.cursor() as cursor:
            jobs._runtime(cursor)  # SELECT only, never initialize.
        proxies = tuple(value for value in os.getenv("TASK3_TRUSTED_PROXIES", "").split(",") if value)
        secure = secure_transport(request.url.scheme, request.client.host if request.client else None,
            request.headers, proxies, controlled_channel=os.getenv("TASK3_CONTROLLED_LOOPBACK", "0") == "1")
        def baseline():
            nonlocal official_manager
            from database.dao import DataRepository
            from .baseline import BaselineAdapter
            official_manager=DatabaseManager().connect(create_db=False,verbose=False)
            return BaselineAdapter(DataRepository(official_manager),Path(backend_root)/"release_assets")
        yield Services(manager._conn, RuntimePaths(Path(runtime)), jobs=jobs, auth=AuthService(manager._conn, origin=origin),
            secure=secure, public_demo=False, baseline_factory=baseline)
    finally:
        if official_manager is not None: official_manager.close(verbose=False)
        manager.close(verbose=False)
