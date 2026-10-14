"""Real hashing/authorization with a lightweight SQL boundary, not MySQL proof."""
import importlib.util
import sqlite3
from pathlib import Path
import pytest


class Cursor:
    def __init__(self, connection):
        self.cursor = connection.db.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, sql, params=()):
        return self.cursor.execute(sql.replace("%s", "?"), params)

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class AuthConnection:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE t3_users(user_id TEXT PRIMARY KEY, username TEXT UNIQUE,
            password_hash TEXT, role TEXT, enabled INTEGER DEFAULT 1, created_at TEXT);
          CREATE TABLE t3_sessions(token_hash TEXT PRIMARY KEY, user_id TEXT,
            csrf_hash TEXT, origin TEXT, expires_at TEXT, revoked INTEGER DEFAULT 0);
        """)

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def close(self):
        self.closed = True


@pytest.fixture
def auth_connection():
    connection = AuthConnection()
    yield connection
    connection.db.close()


def test_private_owner_and_secure_login(platform_module, auth_connection, clock):
    auth = platform_module("auth")
    error = platform_module("contracts").PlatformError
    service = auth.AuthService(auth_connection, clock, origin="https://example.test")
    actor = service.provision("reviewer-a", "synthetic-test-password", "reviewer")
    other = service.provision("reviewer-b", "synthetic-test-password", "reviewer")
    with pytest.raises(error) as denied:
        auth.require_owner(other, actor.user_id)
    assert denied.value.http_status == 403
    with pytest.raises(error) as insecure:
        service.login("reviewer-a", "synthetic-test-password", False)
    assert insecure.value.http_status == 403
    session = service.login("reviewer-a", "synthetic-test-password", True)
    assert service.require_actor(session.token) == actor
    service.require_csrf(session, session.csrf, "https://example.test")
    for header, origin in ((session.csrf, "https://evil.test"), ("wrong", "https://example.test"),
                           (session.csrf, None)):
        with pytest.raises(error) as denied:
            service.require_csrf(session, header, origin)
        assert denied.value.http_status == 403
    clock.advance(8 * 3600)
    with pytest.raises(error) as expired:
        service.require_actor(session.token)
    assert expired.value.http_status == 401
    clock.advance(-8 * 3600)
    service.logout(session.token)
    with pytest.raises(error) as revoked:
        service.require_actor(session.token)
    assert revoked.value.http_status == 401


def test_password_not_plaintext(platform_module, auth_connection, clock):
    service = platform_module("auth").AuthService(auth_connection, clock)
    supplied = "synthetic-test-password"
    service.provision("reviewer", supplied, "reviewer")
    service.provision("reviewer2", supplied, "reviewer")
    rows = [dict(row) for row in auth_connection.db.execute("SELECT * FROM t3_users")]
    assert rows[0]["password_hash"] != supplied
    assert rows[0]["password_hash"] != rows[1]["password_hash"]
    session = service.login("reviewer", supplied, True)
    stored = dict(auth_connection.db.execute("SELECT * FROM t3_sessions").fetchone())
    assert stored["token_hash"] != session.token
    assert stored["csrf_hash"] != session.csrf
    assert supplied not in str(rows) and session.token not in str(stored)
    assert len(bytes.fromhex(session.token)) == 32
    assert stored["expires_at"] == "2099-01-01 08:00:00.000000"


def test_normalized_username_duplicate_and_credential_errors_are_safe(platform_module, auth_connection, clock):
    auth = platform_module("auth")
    error = platform_module("contracts").PlatformError
    service = auth.AuthService(auth_connection, clock)
    service.provision("  cafe\u0301  ", "synthetic-test-password", "reviewer")
    session = service.login("caf\u00e9", "synthetic-test-password", True)
    assert service.require_actor(session.token).role == "reviewer"
    with pytest.raises(error) as duplicate:
        service.provision("caf\u00e9", "synthetic-test-password", "reviewer")
    assert duplicate.value.http_status == 409
    for username in ("ab", "x" * 65, "abc\x00", "abc\u202e"):
        with pytest.raises(error):
            service.provision(username, "synthetic-test-password", "reviewer")
    for username, password in (("absent", "synthetic-secret-wrong"), ("caf\u00e9", "synthetic-secret-wrong")):
        with pytest.raises(error) as denied:
            service.login(username, password, True)
        assert denied.value.code == "invalid_credentials"
        assert "synthetic-secret" not in str(denied.value)
    auth_connection.db.execute("UPDATE t3_users SET enabled=0")
    auth_connection.commit()
    with pytest.raises(error) as disabled:
        service.require_actor(session.token)
    assert disabled.value.http_status == 401


def test_guest_cannot_extract_or_submit_private_large_batches(platform_module):
    auth = platform_module("auth")
    c = platform_module("contracts")
    guest = c.Actor("a" * 32, "guest")
    file = c.FileSpec("t20990101_1.html", 100, ("a" * 64,))
    parse = c.UploadSpec((file,), "parse_only", "b" * 64)
    auth.authorize_upload(guest, parse, False, public_demo=True, active_jobs=0, hourly_count=4)
    for spec, options in (
        (c.UploadSpec((file,), "extract", "b" * 64), {"public_demo": True}),
        (parse, {"public_demo": False}),
        (parse, {"public_demo": True, "active_jobs": 1}),
        (parse, {"public_demo": True, "hourly_count": 5}),
        (c.UploadSpec((c.FileSpec("x.zip", 100, ("a" * 64,)),), "parse_only", "b" * 64), {"public_demo": True}),
        (c.UploadSpec((c.FileSpec("x.html", 1024**2 + 1, ("a" * 64,)),), "parse_only", "b" * 64), {"public_demo": True}),
    ):
        with pytest.raises(c.PlatformError):
            auth.authorize_upload(guest, spec, False, **options)
    reviewer = c.Actor("b" * 32, "reviewer")
    with pytest.raises(c.PlatformError):
        auth.authorize_upload(reviewer, c.UploadSpec((file,), "extract", "b" * 64), False)


def test_forwarded_protocol_only_from_named_loopback_proxy(platform_module):
    secure = platform_module("auth").secure_transport
    assert secure("https", "203.0.113.2", {}, ()) is True
    assert secure("http", "203.0.113.2", {"x-forwarded-proto": "https"}, ("127.0.0.1",)) is False
    assert secure("http", "127.0.0.1", {"x-forwarded-proto": "https"}, ("127.0.0.1",)) is True
    assert secure("http", "127.0.0.1", {"x-forwarded-proto": "https,http"}, ("127.0.0.1",)) is False
    assert secure("http", "127.0.0.1", {"x-forwarded-proto": "https"}, ()) is False
    assert secure("http", "203.0.113.2", {}, (), controlled_channel=True) is False
    assert secure("http", "127.0.0.1", {}, (), controlled_channel=True) is True


def test_provision_cli_reads_hidden_password_and_has_no_password_option(platform_module, monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "deploy/task3/provision_user.py"
    assert path.is_file(), "Task3 account provisioning CLI not implemented"
    spec = importlib.util.spec_from_file_location("task3_provision_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connection = AuthConnection()
    monkeypatch.setattr(module, "connect", lambda: connection)
    prompts = []
    def hidden(prompt):
        prompts.append(prompt)
        return "synthetic-test-password"
    monkeypatch.setattr(module.getpass, "getpass", hidden)
    assert module.main(["--username", "reviewer", "--role", "reviewer"]) == 0
    stored = dict(connection.db.execute("SELECT * FROM t3_users").fetchone())
    assert stored["username"] == "reviewer" and stored["password_hash"] != "synthetic-test-password"
    assert len(prompts) == 2
    assert connection.closed is True
    assert "synthetic-test-password" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        module.main(["--username", "reviewer", "--password", "dangerous"])
    connection.db.close()
