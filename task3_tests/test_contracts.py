from decimal import Decimal
import json
import re

import pytest


def test_limits_and_scoped_paths(platform_module, tmp_path):
    limits = platform_module("limits").Limits.from_env({})
    assert limits.chunk_bytes == 16 * 1024**2
    assert limits.job_disk_bytes == 12 * 1024**3
    assert limits.queue_size == 10
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime")
    task = paths.task("a" * 32)
    assert task.inputs == tmp_path / "runtime/uploads" / ("a" * 32) / "inputs"
    assert len({task.received, task.inputs, task.extraction, task.exports}) == 4
    assert paths.task("b" * 32).inputs != task.inputs
    error = platform_module("contracts").PlatformError
    for value in ("../data", "official", "A" * 32, "a" * 31):
        with pytest.raises(error):
            paths.task(value)
    with pytest.raises(error):
        platform_module("limits").Limits.from_env({"TASK3_CHUNK_BYTES": "-1"})


@pytest.mark.parametrize("overrides", [
    {"TASK3_CHUNK_BYTES": "0"}, {"TASK3_QUEUE_SIZE": "x"},
    {"TASK3_CHUNK_BYTES": "67108865"}, {"TASK3_ACTIVE_JOBS": "2"},
    {"TASK3_HEARTBEAT_SECONDS": "60", "TASK3_LEASE_SECONDS": "60"},
])
def test_limits_reject_unenforceable_configuration(platform_module, overrides):
    with pytest.raises(platform_module("contracts").PlatformError):
        platform_module("limits").Limits.from_env(overrides)


def test_scoped_paths_reject_symlink_ancestors(platform_module, tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Windows lacks symlink privilege")
    with pytest.raises(platform_module("contracts").PlatformError):
        platform_module("paths").RuntimePaths(link / "runtime").task("a" * 32)


def test_migration_only_t3_tables(platform_module, fake_connection):
    migrate = platform_module("migrate").migrate
    report = migrate(fake_connection, expected_database="synthetic_task3")
    expected = {"t3_users", "t3_sessions", "t3_jobs", "t3_datasets",
                "t3_upload_files", "t3_upload_parts", "t3_announcements",
                "t3_entities", "t3_detail_entities", "t3_item_checkpoints",
                "t3_inference_attempts", "t3_artifacts", "t3_runtime_state"}
    assert set(report["created_tables"]) == expected
    statements = [sql for sql, _ in fake_connection.statements]
    ddls = [sql for sql in statements if sql.startswith("CREATE TABLE")]
    assert {re.search(r"EXISTS\s+(t3_\w+)", sql).group(1) for sql in ddls} == expected
    assert all("CREATE DATABASE" not in sql and "ALTER TABLE" not in sql for sql in statements)
    assert migrate(fake_connection, "synthetic_task3")["reused"] is True


def test_migration_wrong_database_or_schema_never_writes(platform_module, fake_connection):
    error = platform_module("contracts").PlatformError
    migrate = platform_module("migrate").migrate
    with pytest.raises(error):
        migrate(fake_connection, "other_database")
    assert not any(sql.startswith(("INSERT", "CREATE")) for sql, _ in fake_connection.statements)
    fake_connection.tables.add("t3_runtime_state")
    fake_connection.version = 99
    fake_connection.statements.clear()
    with pytest.raises(error):
        migrate(fake_connection, "synthetic_task3")
    assert not any(sql.startswith(("INSERT", "CREATE")) for sql, _ in fake_connection.statements)


def test_admin_platform_migration_installs_relation_dependency_explicitly(platform_module,fake_connection):
    module=platform_module('migrate')
    assert hasattr(module,'migrate_platform'),'Full explicit platform migration missing'
    fake_connection.database='procurement_task3_accept_synthetic'
    module.migrate_platform(fake_connection,fake_connection.database)
    ddls=[sql for sql,_ in fake_connection.statements if sql.startswith('CREATE TABLE')]
    assert any('t2_datasets' in sql for sql in ddls) and any('t2_evidence' in sql for sql in ddls)


def test_canonical_business_bytes_keep_decimal_and_reject_floats(platform_module):
    contracts = platform_module("contracts")
    encoded = contracts.canonical_bytes({"money": Decimal("1.23000000"), "unknown": None})
    assert json.loads(encoded) == {"money": "1.23000000", "unknown": None}
    assert contracts.canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    for value in (1.2, float("nan"), Decimal("Infinity")):
        with pytest.raises(contracts.PlatformError):
            contracts.canonical_bytes({"value": value})
    with pytest.raises(contracts.PlatformError):
        contracts.strict_json_loads('{"a":1,"a":2}')


def test_id_boundary_and_dtos_never_reveal_local_paths(platform_module, tmp_path):
    contracts = platform_module("contracts")
    assert contracts.validate_announcement_id("t20990101_1") == "t20990101_1"
    assert contracts.validate_announcement_id("t20990101_" + "1" * 118)
    for aid in ("t20990101_" + "1" * 119, "x1", "t20990101_1/secret"):
        with pytest.raises(contracts.PlatformError):
            contracts.validate_announcement_id(aid)
    prepared = contracts.PreparedInput("t20990101_1", tmp_path / "x.html", None, "a" * 64, None)
    dto = prepared.to_dto()
    assert dto == {"announcement_id": "t20990101_1", "html_sha": "a" * 64, "zip_sha": None}
