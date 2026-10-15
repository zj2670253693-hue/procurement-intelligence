"""Opt-in REAL MySQL tests, never a fallback to DB_NAME or a production database.

Run only in a dedicated acceptance database, with the processing worker stopped.
Synthetic test rows are retained for inspection; this module drops no tables/data.
"""
import hashlib
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.mysql_integration


@pytest.fixture
def real_mysql():
    database = os.getenv("TASK3_MYSQL_TEST_DB", "")
    if not database:
        pytest.skip("REAL MySQL not configured: no SQL acceptance claim")
    if (len(database)>64 or not re.fullmatch(r"procurement_task3_accept_[A-Za-z0-9_]+", database)
            or os.getenv("TASK3_MYSQL_TEST_WRITE_AUTHORIZED") != "1"):
        pytest.fail("Explicit isolated database and test-write authorization required; no connection made")
    import pymysql
    from pymysql.cursors import DictCursor
    config = dict(host="127.0.0.1", port=int(os.getenv("TASK3_MYSQL_TEST_PORT", "3306")),
                  user=os.environ["TASK3_MYSQL_TEST_USER"], password=os.environ["TASK3_MYSQL_TEST_PASSWORD"],
                  database=database, cursorclass=DictCursor, charset="utf8mb4", autocommit=False)
    connections = [pymysql.connect(**config) for _ in range(2)]
    try:
        from task3_platform.migrate import migrate
        migrate(connections[0], database)
        with connections[0].cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM t3_jobs WHERE status IN ('queued','running','validating')")
            assert cursor.fetchone()["n"] == 0, "Acceptance database has active work; refusing interference"
        yield connections
    finally:
        for connection in connections:
            connection.close()


def test_real_mysql_owner_decimal_competition_restart_rollback(real_mysql, tmp_path):
    from task3_platform.contracts import Actor, UploadSpec, FileSpec, PreparedInput, PlatformError
    from task3_platform.job_store import JobStore
    from task3_platform.datasets import DatasetRepository
    from task3_platform.paths import RuntimePaths
    from task3_platform.filters import EntityFilters
    from models.schemas import ExtractionResult, ExtractedEntity
    connection, competitor = real_mysql
    actors = [Actor(uuid.uuid4().hex, "reviewer") for _ in range(2)]
    with connection.cursor() as cursor:
        for actor in actors:
            cursor.execute("INSERT INTO t3_users(user_id,username,password_hash,role,enabled,created_at) VALUES(%s,%s,%s,%s,TRUE,%s)",
                           (actor.user_id, "synthetic_"+actor.user_id, "not-a-login-hash", actor.role, datetime.now(timezone.utc).replace(tzinfo=None)))
    connection.commit()
    now = [datetime.now(timezone.utc)]
    jobs = JobStore(connection, lambda: now[0])
    paths = RuntimePaths(tmp_path/"runtime")
    repo = DatasetRepository(connection, jobs=jobs, paths=paths)
    own = []
    for index, actor in enumerate(actors):
        body = ("<p>synthetic source "+str(index)+"</p>").encode()
        sha = hashlib.sha256(body).hexdigest()
        job = jobs.create_receiving(actor, UploadSpec((FileSpec("t20990101_1.html",len(body),(sha,)),), "extract", "a"*64), uuid.uuid4().hex)
        html = paths.task(job.task_id).inputs/"t20990101_1.html"
        html.parent.mkdir(parents=True)
        html.write_bytes(body)
        prepared = PreparedInput(html.stem, html, None, sha, None)
        jobs.finalize(job.task_id, {"files": [], "inputs": [prepared.to_dto()]})
        lease = jobs.claim("synthetic-worker-"+str(index))
        assert lease is not None
        assert JobStore(competitor).claim("synthetic-competitor") is None
        result = ExtractionResult(announcement_id=html.stem, entities=[ExtractedEntity(product_name="synthetic", unit_price="1.230000000000000000000000000001万元")], detail_status="no_attachment")
        # Force the actual transaction to fail at its final checkpoint, not at an
        # earlier validation check. All preceding announcement/entity writes roll back.
        writes = []
        class FailingCursor:
            def __init__(self, cursor): self.cursor=cursor
            def __enter__(self): self.cursor.__enter__(); return self
            def __exit__(self,*args): return self.cursor.__exit__(*args)
            def __getattr__(self,name): return getattr(self.cursor,name)
            def execute(self,sql,params=()):
                if sql.startswith("INSERT INTO t3_item_checkpoints"):
                    raise RuntimeError("synthetic_final_checkpoint_failure")
                if sql.startswith("INSERT INTO t3_entities"):
                    writes.append("entity")
                return self.cursor.execute(sql,params)
        class FailingConnection:
            def __getattr__(self,name): return getattr(connection,name)
            def cursor(self): return FailingCursor(connection.cursor())
        repo.connection=FailingConnection()
        with pytest.raises(RuntimeError,match="synthetic_final_checkpoint_failure"):
            repo.save_announcement(lease, prepared, result)
        repo.connection=connection
        assert writes == ["entity"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM t3_announcements WHERE upload_dataset_id=%s", (job.upload_dataset_id,))
            assert cursor.fetchone()["n"] == 0
        repo.save_announcement(lease, prepared, result)
        assert JobStore(connection).checkpoints(job.task_id)[html.stem].db_state == "confirmed"
        # Simulate expiry without a real blocking sleep; old epoch must be fenced.
        now[0] += timedelta(seconds=61)
        jobs.recover_expired()
        with pytest.raises(PlatformError):
            jobs.heartbeat(lease)
        lease = jobs.claim("synthetic-restarted-"+str(index))
        assert lease is not None
        repo.save_announcement(lease, prepared, result)
        jobs.transition(lease,"failed","export")
        # Explicit fixture publication isolates SQL query tests from export/relation acceptance.
        with connection.cursor() as cursor:
            cursor.execute("UPDATE t3_jobs SET status='completed' WHERE task_id=%s", (job.task_id,))
            cursor.execute("UPDATE t3_datasets SET availability='completed' WHERE upload_dataset_id=%s", (job.upload_dataset_id,))
            cursor.execute("SELECT unit_price_numeric FROM t3_entities WHERE upload_dataset_id=%s", (job.upload_dataset_id,))
            assert cursor.fetchone()["unit_price_numeric"] == Decimal("1.230000000000000000000000000001")
        connection.commit()
        own.append(job)
    for index, job in enumerate(own):
        assert repo.search(actors[index], job.upload_dataset_id, EntityFilters()).total == 1
        assert str(index) in repo.announcement(actors[index], job.upload_dataset_id, "t20990101_1")["html_text"]
        with pytest.raises(PlatformError):
            repo.announcement(actors[1-index], job.upload_dataset_id, "t20990101_1")
