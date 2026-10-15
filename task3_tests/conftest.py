"""Synthetic-only Task3 tests; SQL doubles do not prove MySQL locking."""
import importlib
import importlib.util
import socket
from datetime import datetime, timezone, timedelta

import pytest


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original_create, original_connect = socket.create_connection, socket.socket.connect
    def local_create(address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_create(address, *args, **kwargs)
        raise AssertionError("Task3 synthetic test attempted external network")
    def local_connect(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        raise AssertionError("Task3 synthetic test attempted external network")
    monkeypatch.setattr(socket, "create_connection", local_create)
    monkeypatch.setattr(socket.socket, "connect", local_connect)


@pytest.fixture
def platform_module():
    def load(name):
        try:
            found = importlib.util.find_spec("task3_platform." + name)
        except ModuleNotFoundError:
            found = None
        assert found is not None, "Task3 interface not implemented: " + name
        return importlib.import_module("task3_platform." + name)
    return load


@pytest.fixture
def actor_a(platform_module):
    return platform_module("contracts").Actor("a" * 32, "reviewer")


@pytest.fixture
def actor_b(platform_module):
    return platform_module("contracts").Actor("b" * 32, "reviewer")


@pytest.fixture
def synthetic_html():
    return b'<html><title>synthetic</title><body>test</body></html>'


class Clock:
    def __init__(self):
        self.value = datetime(2099, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


class MigrationCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=()):
        self.connection.statements.append((sql, params))
        if sql == "SELECT DATABASE() AS database_name":
            self.rows = [{"database_name": self.connection.database}]
        elif "information_schema.tables" in sql:
            self.rows = [{"table_name": name} for name in self.connection.tables]
        elif sql.lstrip().startswith("CREATE TABLE"):
            import re
            self.connection.tables.add(re.search(r"EXISTS\s+(t[23]_\w+)", sql).group(1))
            self.rows = []
        elif sql.startswith("SELECT schema_version"):
            self.rows = [{"schema_version": self.connection.version}] if self.connection.version else []
        elif sql.startswith("INSERT INTO t3_runtime_state"):
            self.connection.version = params[0]
            self.rows = []
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class MigrationConnection:
    def __init__(self):
        self.database = "synthetic_task3"
        self.tables = set()
        self.version = None
        self.statements = []

    def cursor(self):
        return MigrationCursor(self)

    def commit(self):
        pass


@pytest.fixture
def fake_connection():
    return MigrationConnection()
