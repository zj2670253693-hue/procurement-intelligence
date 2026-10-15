"""Private registered-file downloads: owner, subtree, bytes and SHA are required."""
from dataclasses import dataclass
from pathlib import Path
from .contracts import PlatformError, validate_dataset_id
from .archive_safety import safe_member_name
from .auth import require_owner
from .job_store import transaction
from .paths import reject_link_ancestors
from .uploads import _hash_file


# Historical exports are immutable. An explicitly installed, source-verified
# revision may satisfy the existing download URL without destroying its original
# file or SHA receipt. Future jobs already produce corrected primary exports.
SUMMARY_REVISIONS = {
    "standard.xlsx": "standard.summary-v1.xlsx",
    "standard_projection.json": "standard_projection.summary-v1.json",
    "export_report.json": "export_report.summary-v1.json",
    "总表.csv": "总表.summary-v1.csv",
}


@dataclass(frozen=True)
class VerifiedFile:
    path: Path
    name: str
    sha256: str
    bytes: int
    kind: str


class ArtifactService:
    def __init__(self, store, paths):
        self.store, self.paths = store, paths

    def _verify(self, task_id, artifact):
        name = safe_member_name(artifact.name)
        if "/" in name or len(name) > 128:
            raise PlatformError("invalid_artifact_name")
        path = reject_link_ancestors(self.paths.task(task_id).exports / name)
        if path.relative_to(self.paths.root).as_posix() != artifact.relative_path or not path.is_file():
            raise PlatformError("artifact_not_found", 404)
        if path.stat().st_size != artifact.bytes or _hash_file(path) != artifact.sha256:
            raise PlatformError("artifact_integrity", 409)
        return VerifiedFile(path, name, artifact.sha256, artifact.bytes, artifact.kind)

    def register(self, lease, artifacts):
        verified = [self._verify(lease.task_id, artifact) for artifact in artifacts]
        with transaction(self.store.connection) as cursor:
            self.store._runtime(cursor)
            job = self.store._fenced(cursor, lease)
            for artifact in verified:
                cursor.execute("SELECT * FROM t3_artifacts WHERE upload_dataset_id=%s AND name=%s", (job["upload_dataset_id"], artifact.name))
                old = cursor.fetchone()
                if old:
                    if old["sha256"] != artifact.sha256:
                        raise PlatformError("artifact_registration_conflict", 409)
                    continue
                cursor.execute("INSERT INTO t3_artifacts(upload_dataset_id,name,relative_path,sha256,bytes,kind) VALUES(%s,%s,%s,%s,%s,%s)",
                               (job["upload_dataset_id"], artifact.name, artifact.path.relative_to(self.paths.root).as_posix(), artifact.sha256, artifact.bytes, artifact.kind))

    def resolve(self, actor, dataset_id, name):
        from .contracts import Artifact
        validate_dataset_id(dataset_id)
        safe = safe_member_name(name)
        if "/" in safe or len(safe) > 128:
            raise PlatformError("invalid_artifact_name")
        with self.store.connection.cursor() as cursor:
            cursor.execute("SELECT task_id,owner_id FROM t3_datasets WHERE upload_dataset_id=%s", (dataset_id,))
            dataset = cursor.fetchone()
            if not dataset:
                raise PlatformError("dataset_not_found", 404)
            require_owner(actor, dataset["owner_id"])
            row = None
            if name in SUMMARY_REVISIONS:
                cursor.execute("SELECT * FROM t3_artifacts WHERE upload_dataset_id=%s AND name=%s",
                               (dataset_id, SUMMARY_REVISIONS[name]))
                row = cursor.fetchone()
            if row is None:
                cursor.execute("SELECT * FROM t3_artifacts WHERE upload_dataset_id=%s AND name=%s", (dataset_id, name))
                row = cursor.fetchone()
        if not row:
            raise PlatformError("artifact_not_found", 404)
        return self._verify(dataset["task_id"], Artifact(row["name"], row["relative_path"], row["sha256"], row["bytes"], row["kind"]))
