import hashlib
import sqlite3
from dataclasses import replace
from decimal import Decimal
import pytest
from task3_tests.test_jobs import JobConnection, JobCursor, spec


class DatasetCursor(JobCursor):
    def execute(self, sql, params=()):
        params = tuple(str(value) if isinstance(value, Decimal) else value for value in params)
        return super().execute(sql, params)


class DatasetConnection(JobConnection):
    def __init__(self, path):
        super().__init__(path)
        self.db.executescript("""
          CREATE TABLE t3_announcements(upload_dataset_id TEXT,announcement_id TEXT,html_sha256 TEXT,
            zip_sha256 TEXT,html_relative_path TEXT,zip_relative_path TEXT,payload_json TEXT,diagnostics_json TEXT,
            PRIMARY KEY(upload_dataset_id,announcement_id));
          CREATE TABLE t3_entities(upload_dataset_id TEXT,announcement_id TEXT,row_no INTEGER,
            product_name TEXT,category TEXT,brand TEXT,spec_model TEXT,unit_price TEXT,quantity TEXT,total_price TEXT,
            unit_price_numeric NUMERIC,quantity_numeric NUMERIC,total_price_numeric NUMERIC,
            unit_price_unit TEXT,quantity_unit TEXT,total_price_unit TEXT,payload_json TEXT,
            PRIMARY KEY(upload_dataset_id,announcement_id,row_no));
          CREATE TABLE t3_detail_entities(upload_dataset_id TEXT,announcement_id TEXT,row_no INTEGER,
            payload_json TEXT,source_fingerprint TEXT,PRIMARY KEY(upload_dataset_id,announcement_id,row_no));
          CREATE TABLE t3_artifacts(upload_dataset_id TEXT,name TEXT,relative_path TEXT,sha256 TEXT,bytes INTEGER,kind TEXT,
            PRIMARY KEY(upload_dataset_id,name));
        """)
    def cursor(self):
        return DatasetCursor(self)


@pytest.fixture
def dataset_setup(platform_module, tmp_path, actor_a, clock):
    connection = DatasetConnection(tmp_path / "datasets.sqlite")
    c = platform_module("contracts")
    jobs = platform_module("job_store").JobStore(connection, clock)
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime")
    def setup(actor=actor_a, key="a", body=b"<p>synthetic A</p>", entities=None, details=None):
        repo = platform_module("datasets").DatasetRepository(connection, jobs=jobs, paths=paths)
        from models.schemas import ExtractionResult, ExtractedEntity
        job = jobs.create_receiving(actor, spec(c, mode="extract"), key)
        path = paths.task(job.task_id).inputs / "t20990101_1.html"
        path.parent.mkdir(parents=True)
        path.write_bytes(body)
        prepared = c.PreparedInput(path.stem, path, None, hashlib.sha256(body).hexdigest(), None)
        jobs.finalize(job.task_id, {"files": [], "inputs": [prepared.to_dto()]})
        lease = jobs.claim("worker-" + key)
        result = ExtractionResult(announcement_id=path.stem,
            entities=entities if entities is not None else [ExtractedEntity(product_name="合成 A", brand="甲牌", spec_model="A-1", category="设备", unit_price="1.23000000万元", quantity="1套", total_price="1.23000000万元")],
            detail_entities=details or [], detail_status="no_attachment")
        checkpoint = repo.save_announcement(lease, prepared, result)
        # Isolate query tests from Task8/9 publishing: staged rows alone must remain hidden.
        jobs.transition(lease, "failed", "export")
        return repo, job, prepared, result, checkpoint
    yield setup, connection, paths, jobs
    connection.db.close()


def visible(connection, job, status="completed"):
    connection.db.execute("UPDATE t3_datasets SET availability=? WHERE upload_dataset_id=?", (status, job.upload_dataset_id))
    connection.db.execute("UPDATE t3_jobs SET status=? WHERE task_id=?", (status, job.task_id))
    connection.commit()


def test_same_aid_two_dataset_views(platform_module, dataset_setup, actor_a, actor_b):
    setup, connection, _, _ = dataset_setup
    filters = platform_module("filters").EntityFilters()
    repo, first, _, _, _ = setup(actor_a, "first")
    _, second, _, _, _ = setup(actor_b, "second", b"<p>synthetic B</p>")
    visible(connection, first)
    visible(connection, second)
    assert repo.search(actor_a, first.upload_dataset_id, filters, 1, 20).total == 1
    view_a = repo.announcement(actor_a, first.upload_dataset_id, "t20990101_1")
    view_b = repo.announcement(actor_b, second.upload_dataset_id, "t20990101_1")
    assert view_a["source_sha256"] != view_b["source_sha256"]
    assert "synthetic A" in view_a["html_text"] and "synthetic B" in view_b["html_text"]
    assert "html_relative_path" not in view_a and "D:" not in str(view_a)
    with pytest.raises(platform_module("contracts").PlatformError) as denied:
        repo.announcement(actor_b, first.upload_dataset_id, "t20990101_1")
    assert denied.value.http_status == 403


def test_all_seven_filters_keep_precision(platform_module, dataset_setup, actor_a):
    setup, connection, _, _ = dataset_setup
    repo, job, _, _, _ = setup()
    visible(connection, job)
    fields = {"product_name": "合成", "category": "设备", "brand": "甲牌", "spec_model": "A-1",
              "unit_price": "1.23000000", "quantity": "1套", "total_price": "1.23000000"}
    for name, text in fields.items():
        page = repo.search(actor_a, job.upload_dataset_id, platform_module("filters").EntityFilters(text={name: text}), 1, 20)
        assert page.total == 1
        assert page.items[0]["unit_price"] == "1.23000000万元"
        assert page.items[0]["quantity"] == "1套"
    page = repo.search(actor_a, job.upload_dataset_id,
                      platform_module("filters").EntityFilters(ranges={"unit_price": (Decimal("1.22"), Decimal("1.24"))}), 1, 20)
    assert page.total == 1


def test_missing_staging_pagination_and_literal_wildcards(platform_module, dataset_setup, actor_a):
    setup, connection, _, _ = dataset_setup
    from models.schemas import ExtractedEntity
    repo, job, _, _, _ = setup(entities=[ExtractedEntity(product_name="设备%型号_1", spec_model="未公示")])
    c = platform_module("contracts")
    f = platform_module("filters").EntityFilters
    with pytest.raises(c.PlatformError) as staging:
        repo.search(actor_a, job.upload_dataset_id, f(), 1, 20)
    assert staging.value.http_status == 409
    visible(connection, job, "partial")
    assert repo.search(actor_a, job.upload_dataset_id, f(text={"product_name": "%型号_"}), 1, 20).total == 1
    assert repo.search(actor_a, job.upload_dataset_id, f(text={"product_name": "%; DROP TABLE t3_users"}), 1, 20).total == 0
    assert repo.search(actor_a, job.upload_dataset_id, f(missing={"spec_model": True}), 1, 20).availability == "partial"
    for page, size in ((51, 200), (1, 201), (0, 20)):
        with pytest.raises(c.PlatformError):
            repo.search(actor_a, job.upload_dataset_id, f(), page, size)


def test_numeric_units_rates_and_over_precision(platform_module):
    f = platform_module("filters")
    assert f.numeric_projection("12300元", "unit_price") == (Decimal("1.23"), "万元")
    assert f.numeric_projection("1.230000000000000000000000000001万元", "unit_price")[0] == Decimal("1.230000000000000000000000000001")
    for raw in ("78%", "预算6万元", "12300", "1." + "1" * 31 + "万元", "1" * 36 + "万元"):
        assert f.numeric_projection(raw, "total_price")[0] is None
    assert f.numeric_projection("1批", "quantity") == (Decimal("1"), "批")
    assert f.numeric_projection("1", "quantity") == (None, None)


def test_save_transaction_failure_rolls_back_entities_and_checkpoint(platform_module, dataset_setup, actor_a):
    setup, connection, paths, jobs = dataset_setup
    c = platform_module("contracts")
    from models.schemas import ExtractionResult, ExtractedEntity
    repo = platform_module("datasets").DatasetRepository(connection, jobs=jobs, paths=paths)
    job = jobs.create_receiving(actor_a, spec(c, mode="extract"), "rollback")
    path = paths.task(job.task_id).inputs / "t20990101_1.html"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<p>source</p>")
    prepared = c.PreparedInput(path.stem, path, None, hashlib.sha256(path.read_bytes()).hexdigest(), None)
    jobs.finalize(job.task_id, {"files": [], "inputs": [prepared.to_dto()]})
    lease = jobs.claim("worker")
    connection.db.execute("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON t3_item_checkpoints BEGIN SELECT RAISE(FAIL,'synthetic'); END;")
    connection.commit()
    with pytest.raises(sqlite3.DatabaseError):
        repo.save_announcement(lease, prepared, ExtractionResult(announcement_id=path.stem, entities=[ExtractedEntity(product_name="synthetic")], detail_status="no_attachment"))
    assert connection.db.execute("SELECT COUNT(*) FROM t3_entities").fetchone()[0] == 0
    assert connection.db.execute("SELECT COUNT(*) FROM t3_announcements").fetchone()[0] == 0
    assert jobs.checkpoints(job.task_id) == {}


def test_distinct_complete_pages_preserve_provenance(platform_module, dataset_setup):
    setup, connection, _, _ = dataset_setup
    from models.schemas import DetailedEntity
    details = [DetailedEntity(product_name="设备", source_file="spec.pdf", source_page=page) for page in ("1", "2", "2")]
    setup(details=details)
    assert connection.db.execute("SELECT COUNT(*) FROM t3_detail_entities").fetchone()[0] == 2


def test_baseline_adapter_never_updates_existing_tables(platform_module, actor_a):
    module = platform_module("baseline")
    from task3_tests.test_auth import AuthConnection
    connection = AuthConnection()
    connection.db.executescript("CREATE TABLE extracted_entities(id INTEGER, announcement_id TEXT, product_name TEXT,category TEXT,brand TEXT,spec_model TEXT,unit_price TEXT,quantity TEXT,total_price TEXT);")
    connection.db.execute("INSERT INTO extracted_entities VALUES(1,'t20990101_1','baseline','','','','','','')")
    connection.commit()
    before = connection.db.total_changes
    adapter = module.BaselineAdapter(connection, {})
    page = adapter.search(actor_a, "official", platform_module("filters").EntityFilters(text={"product_name": "base"}), 1, 20)
    assert page.total == 1 and page.items[0]["product_name"] == "baseline"
    assert connection.db.total_changes == before
    connection.db.close()
