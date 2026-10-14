from contextlib import contextmanager

import pytest

from database.connection import DatabaseManager
from database.dao import DataRepository, DetailEntityDAO
from database.schema import ALL_TABLES, CREATE_TABLE_ENTITY_DETAILS
from models.schemas import DetailedEntity, ExtractedEntity, ExtractionResult


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self):
        pass

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.connection.statements.append((normalized, params))
        self.rows = []
        if normalized.startswith("SHOW COLUMNS FROM announcements LIKE"):
            column = params[0]
            self.rows = [{"Field": column}] if column in self.connection.columns else []
        elif normalized.startswith("ALTER TABLE announcements ADD COLUMN"):
            self.connection.columns.add(normalized.split()[5])
        elif normalized.startswith("SHOW COLUMNS FROM extracted_entity_details LIKE"):
            column = params[0]
            self.rows = [{"Field": column, "Type": {"product_name": "varchar(1024)", "spec_model": "varchar(8192)"}[column]}]

    def executemany(self, sql, rows):
        normalized = " ".join(sql.split())
        rows = list(rows)
        self.connection.statements.append((normalized, rows))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class RecordingConnection:
    def __init__(self):
        self.statements = []
        self.columns = set()
        self.begins = 0
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return RecordingCursor(self)

    def begin(self):
        self.begins += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def make_database():
    manager = DatabaseManager()
    manager._conn = RecordingConnection()
    return manager


def test_schema_contains_detail_table_and_idempotent_announcement_columns():
    assert CREATE_TABLE_ENTITY_DETAILS in ALL_TABLES
    assert "extracted_entity_details" in CREATE_TABLE_ENTITY_DETAILS
    assert "package_name" in CREATE_TABLE_ENTITY_DETAILS
    assert "source_file" in CREATE_TABLE_ENTITY_DETAILS

    database = make_database()
    database.init_tables()
    database.init_tables()

    alters = [sql for sql, _ in database._conn.statements if sql.startswith("ALTER TABLE")]
    assert sum("detail_status" in sql for sql in alters) == 1
    assert sum("detail_error_message" in sql for sql in alters) == 1


def test_detail_dao_replaces_and_deduplicates_package_scoped_rows():
    database = make_database()
    dao = DetailEntityDAO(database)
    items = [
        DetailedEntity(package_name="包一", product_name="设备甲", source_file="A.pdf"),
        DetailedEntity(package_name="包一", product_name="设备甲", source_file="B.pdf"),
        DetailedEntity(package_name="包二", product_name="设备甲", source_file="B.pdf"),
    ]

    dao.save_details("fictional-001", items)

    delete_calls = [
        item for item in database._conn.statements
        if item[0].startswith("DELETE FROM extracted_entity_details")
    ]
    insert_calls = [
        item for item in database._conn.statements
        if item[0].startswith("INSERT INTO extracted_entity_details")
    ]
    assert len(delete_calls) == 1
    assert len(insert_calls) == 1
    assert len(insert_calls[0][1]) == 2


def test_repository_rolls_back_every_layer_when_detail_write_fails(monkeypatch):
    database = make_database()
    repository = DataRepository(database)
    result = ExtractionResult(
        announcement_id="fictional-002",
        entities=[ExtractedEntity(product_name="采购包一")],
        detail_entities=[DetailedEntity(product_name="设备甲")],
        detail_status="available",
    )

    def fail_details(*_args, **_kwargs):
        raise RuntimeError("fictional insert failure")

    monkeypatch.setattr(repository.details, "save_details", fail_details)

    with pytest.raises(RuntimeError, match="fictional insert failure"):
        repository.save_extraction_result(result)

    assert database._conn.begins == 1
    assert database._conn.commits == 0
    assert database._conn.rollbacks == 1


def test_repository_rejects_unresolved_detail_status():
    database = make_database()
    repository = DataRepository(database)
    result = ExtractionResult(
        announcement_id="fictional-003",
        detail_status=None,
    )

    with pytest.raises(ValueError, match="detail_status"):
        repository.save_extraction_result(result)

