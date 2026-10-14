"""The SQL transport is simulated locally; real MySQL is also checked at deploy."""
import re

import pytest

from database.connection import DatabaseManager
from database.dao import DetailEntityDAO
from database.schema import CREATE_TABLE_ENTITY_DETAILS
from models.schemas import DetailedEntity


class CapacityCursor:
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
        sql = " ".join(sql.split())
        self.rows = []
        if sql.startswith("CREATE TABLE IF NOT EXISTS extracted_entity_details"):
            if self.connection.columns is None:
                self.connection.columns = {
                    name: f"varchar({size})" for name, size in
                    re.findall(r"(product_name|spec_model) VARCHAR\((\d+)\)", sql)
                }
                match = re.search(r"idx_detail_product \(product_name(?:\((\d+)\))?\)", sql)
                self.connection.prefix = int(match[1]) if match[1] else None
        elif sql.startswith("SHOW COLUMNS FROM announcements LIKE"):
            self.rows = [{"Field": params[0]}]
        elif sql.startswith("SHOW COLUMNS FROM extracted_entity_details LIKE"):
            name = params[0]
            if name in self.connection.columns:
                self.rows = [{"Field": name, "Type": self.connection.columns[name]}]
        elif sql.startswith("SHOW INDEX FROM extracted_entity_details"):
            if self.connection.index_rows is not None:
                self.rows = self.connection.index_rows
            elif self.connection.has_index:
                self.rows = [{"Key_name": "idx_detail_product", "Column_name": "product_name",
                              "Seq_in_index": 1, "Non_unique": 1,
                              "Sub_part": self.connection.prefix}]
        elif sql.startswith("ALTER TABLE extracted_entity_details"):
            if "MODIFY COLUMN spec_model" in sql and self.connection.fail_spec_once:
                self.connection.fail_spec_once = False
                raise RuntimeError("fictional second ALTER failure")
            self.connection.alters.append(sql)
            for name, size in re.findall(r"MODIFY COLUMN (product_name|spec_model) VARCHAR\((\d+)\)", sql):
                self.connection.columns[name] = f"varchar({size})"
            if "ADD INDEX idx_detail_product" in sql:
                self.connection.prefix = int(re.search(r"product_name\((\d+)\)", sql)[1])
                self.connection.has_index = True

    def executemany(self, sql, rows):
        names = re.search(r"INSERT INTO extracted_entity_details\s*\(([^)]+)\)", sql)[1]
        names = [name.strip() for name in names.split(",")]
        for values in rows:
            row = dict(zip(names, values))
            for name, column_type in self.connection.columns.items():
                match = re.fullmatch(r"varchar\((\d+)\)", column_type)
                if match and len(row[name]) > int(match[1]):
                    raise ValueError(f"storage capacity exceeded: {name}")
            self.connection.saved.append(row)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class CapacityConnection:
    def __init__(self, columns=None, prefix=None, has_index=True):
        self.columns = columns
        self.prefix = prefix
        self.has_index = has_index
        self.alters = []
        self.saved = []
        self.index_rows = None
        self.fail_spec_once = False

    def cursor(self):
        return CapacityCursor(self)

    def commit(self):
        pass


def database(columns=None, prefix=None, has_index=True):
    manager = DatabaseManager()
    manager._conn = CapacityConnection(columns, prefix, has_index)
    return manager


def save_long_record(manager):
    # Synthetic strings reproduce the independently observed character lengths.
    name, specification = "名" * 645, "规" * 4438
    DetailEntityDAO(manager).save_details("fictional-capacity", [
        DetailedEntity(product_name=name, spec_model=specification),
    ])
    assert manager._conn.saved[0]["product_name"] == name
    assert manager._conn.saved[0]["spec_model"] == specification


def test_new_detail_table_preserves_observed_long_strings():
    manager = database()
    manager.init_tables()
    save_long_record(manager)


def test_existing_detail_table_is_expanded_before_write():
    manager = database({"product_name": "varchar(512)", "spec_model": "varchar(2048)"})
    manager.init_tables()
    save_long_record(manager)
    assert manager._conn.prefix == 512
    assert manager._conn.columns == {"product_name": "varchar(1024)", "spec_model": "varchar(8192)"}


def test_capacity_migration_does_not_repeat_alters():
    manager = database({"product_name": "varchar(512)", "spec_model": "varchar(2048)"})
    manager.init_tables()
    first_alters = list(manager._conn.alters)
    assert first_alters
    manager.init_tables()
    assert manager._conn.alters == first_alters


@pytest.mark.parametrize("columns", [
    {"product_name": "varchar(2048)", "spec_model": "varchar(12000)"},
    {"product_name": "text", "spec_model": "mediumtext"},
])
def test_existing_larger_columns_are_not_shrunk(columns):
    manager = database(dict(columns), prefix=512)
    manager.init_tables()
    assert manager._conn.columns == columns
    assert manager._conn.alters == []


def test_migration_adds_safe_index_if_old_index_is_missing():
    manager = database({"product_name": "varchar(512)", "spec_model": "varchar(2048)"}, has_index=False)
    manager.init_tables()
    assert manager._conn.has_index and manager._conn.prefix == 512


def test_migration_preserves_already_safe_shorter_index():
    manager = database({"product_name": "varchar(512)", "spec_model": "varchar(2048)"}, prefix=191)
    manager.init_tables()
    save_long_record(manager)
    assert manager._conn.prefix == 191


def test_unknown_column_type_is_rejected_instead_of_rewritten():
    manager = database({"product_name": "int", "spec_model": "varchar(2048)"})
    with pytest.raises(RuntimeError, match="product_name"):
        manager.init_tables()
    assert not manager._conn.alters


def test_retry_recovers_after_second_column_alter_fails():
    manager = database({"product_name": "varchar(512)", "spec_model": "varchar(2048)"})
    manager._conn.fail_spec_once = True
    with pytest.raises(RuntimeError, match="fictional second ALTER failure"):
        manager.init_tables()
    assert manager._conn.columns == {"product_name": "varchar(1024)", "spec_model": "varchar(2048)"}
    assert len(manager._conn.alters) == 1
    manager.init_tables()
    save_long_record(manager)
    assert len(manager._conn.alters) == 2


@pytest.mark.parametrize("changed", [
    {"Column_name": "brand"}, {"Non_unique": 0}, {"Seq_in_index": 2},
])
def test_unexpected_named_index_is_rejected_without_altering(changed):
    manager = database({"product_name": "varchar(512)", "spec_model": "varchar(2048)"})
    row = {"Key_name": "idx_detail_product", "Column_name": "product_name",
           "Seq_in_index": 1, "Non_unique": 1, "Sub_part": None}
    row.update(changed)
    manager._conn.index_rows = [row]
    with pytest.raises(RuntimeError, match="索引结构异常"):
        manager.init_tables()
    assert not manager._conn.alters
