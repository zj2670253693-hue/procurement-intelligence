"""Explicit installer entry point. Requires a connected DictCursor MySQL DB."""
import re
from .contracts import PlatformError
from .schema import DDL, TABLES, SCHEMA_VERSION


def migrate(connection, expected_database: str) -> dict:
    if not isinstance(expected_database, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", expected_database):
        raise PlatformError("invalid_migration_database")
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS database_name")
        row = cursor.fetchone()
        if not row or row["database_name"] != expected_database:
            raise PlatformError("migration_database_mismatch", 409)
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (expected_database,))
        existing = {row["table_name"] for row in cursor.fetchall()}
        if "t3_runtime_state" in existing:
            cursor.execute("SELECT schema_version FROM t3_runtime_state WHERE singleton_id=1")
            row = cursor.fetchone()
            if not row or row["schema_version"] != SCHEMA_VERSION:
                raise PlatformError("migration_schema_mismatch", 409)
        for statement in DDL:
            cursor.execute(statement)
        cursor.execute("INSERT INTO t3_runtime_state(singleton_id,schema_version) VALUES(1,%s) "
                       "ON DUPLICATE KEY UPDATE singleton_id=singleton_id", (SCHEMA_VERSION,))
    connection.commit()
    return {"schema_version": SCHEMA_VERSION, "created_tables": sorted(set(TABLES) - existing),
            "reused": set(TABLES).issubset(existing)}
