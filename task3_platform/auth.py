"""Platform identities, hashed sessions, same-origin CSRF, and upload policy."""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import ipaddress
import secrets
import unicodedata
from urllib.parse import urlsplit

from .contracts import Actor, PlatformError

_N, _R, _P = 32768, 8, 1
_MAXMEM = 64 * 1024**2


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sql_time(value):
    return _utc(value).strftime("%Y-%m-%d %H:%M:%S.%f")


def normalize_username(username):
    if not isinstance(username, str):
        raise PlatformError("invalid_username")
    value = unicodedata.normalize("NFC", username.strip())
    if not 3 <= len(value) <= 64 or any(unicodedata.category(char).startswith("C") for char in value):
        raise PlatformError("invalid_username")
    return value


def _password_bytes(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise PlatformError("invalid_password")
    result = password.encode("utf-8")
    if len(result) > 1024:
        raise PlatformError("invalid_password")
    return result


def _derive(password, salt):
    return hashlib.scrypt(password, salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32)


def _hash_password(password):
    salt = secrets.token_bytes(16)
    return "scrypt$32768$8$1$" + salt.hex() + "$" + _derive(_password_bytes(password), salt).hex()


def _verify_password(password, stored):
    try:
        supplied = _password_bytes(password)
        kind, n, r, p, salt_hex, expected_hex = stored.split("$")
        if (kind, n, r, p) != ("scrypt", "32768", "8", "1"):
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(expected_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        return hmac.compare_digest(_derive(supplied, salt), expected)
    except (PlatformError, ValueError, TypeError):
        return False


def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _origin(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError
        parsed.port  # Reject malformed ports, not merely an apparently valid netloc.
    except (ValueError, TypeError, AttributeError) as exc:
        raise PlatformError("invalid_public_origin") from exc
    return value


@dataclass(frozen=True)
class Session:
    token: str = field(repr=False)
    csrf: str = field(repr=False)
    actor: Actor
    expires: datetime
    origin: str


def require_owner(actor: Actor, owner_id: str):
    if actor.role != "admin" and actor.user_id != owner_id:
        raise PlatformError("owner_forbidden", 403)


def secure_transport(scheme, client_host, headers, trusted_proxies, *, controlled_channel=False):
    if scheme == "https":
        return True
    try:
        loopback = ipaddress.ip_address(client_host).is_loopback
    except (ValueError, TypeError):
        loopback = False
    if loopback and client_host in trusted_proxies:
        return headers.get("x-forwarded-proto") == "https"
    return bool(loopback and controlled_channel)


def authorize_upload(actor, spec, secure, *, public_demo=False, active_jobs=0, hourly_count=0):
    """Counters must be server-read under the persistent admission lock."""
    if actor.role == "guest":
        if (not public_demo or spec.mode != "parse_only" or len(spec.files) != 1
                or not spec.files[0].name.lower().endswith((".html", ".htm"))
                or spec.files[0].bytes > 1024**2 or spec.files[0].bytes <= 0
                or active_jobs != 0 or hourly_count >= 5):
            raise PlatformError("guest_demo_limit", 403)
    elif not secure:
        raise PlatformError("secure_transport_required", 403)


class AuthService:
    def __init__(self, connection, clock=_now, *, origin="https://localhost"):
        self.connection = connection
        self.clock = clock
        self.origin = _origin(origin)

    def provision(self, username, password, role):
        username = normalize_username(username)
        if role not in {"reviewer", "admin"}:
            raise PlatformError("invalid_role")
        hashed = _hash_password(password)
        actor = Actor(secrets.token_hex(16), role)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT user_id FROM t3_users WHERE username=%s", (username,))
                if cursor.fetchone():
                    raise PlatformError("username_conflict", 409)
                cursor.execute("INSERT INTO t3_users(user_id,username,password_hash,role,created_at) VALUES(%s,%s,%s,%s,%s)",
                               (actor.user_id, username, hashed, role, _sql_time(self.clock())))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return actor

    def login(self, username, password, secure_transport):
        if not secure_transport:
            raise PlatformError("secure_transport_required", 403)
        try:
            username = normalize_username(username)
        except PlatformError:
            raise PlatformError("invalid_credentials", 401) from None
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT user_id,password_hash,role,enabled FROM t3_users WHERE username=%s", (username,))
            row = cursor.fetchone()
        if not row:
            # Comparable expensive work for absent users, without a plaintext default account.
            _verify_password(password, "scrypt$32768$8$1$" + "00" * 16 + "$" + "00" * 32)
            raise PlatformError("invalid_credentials", 401)
        valid = _verify_password(password, row["password_hash"])
        if not valid or not row["enabled"] or row["role"] == "guest":
            raise PlatformError("invalid_credentials", 401)
        session = Session(secrets.token_hex(32), secrets.token_hex(32),
                          Actor(row["user_id"], row["role"]),
                          _utc(self.clock()) + timedelta(hours=8), self.origin)
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO t3_sessions(token_hash,user_id,csrf_hash,origin,expires_at) VALUES(%s,%s,%s,%s,%s)",
                           (_digest(session.token), session.actor.user_id, _digest(session.csrf), self.origin, _sql_time(session.expires)))
        self.connection.commit()
        return session

    def _session_row(self, token):
        if not isinstance(token, str) or len(token) != 64:
            raise PlatformError("session_required", 401)
        try:
            if len(bytes.fromhex(token)) != 32:
                raise ValueError
        except ValueError:
            raise PlatformError("session_required", 401) from None
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT s.user_id,s.csrf_hash,s.origin,s.expires_at,s.revoked,u.role,u.enabled "
                           "FROM t3_sessions s JOIN t3_users u ON u.user_id=s.user_id WHERE s.token_hash=%s", (_digest(token),))
            row = cursor.fetchone()
        if (not row or row["revoked"] or not row["enabled"] or _utc(row["expires_at"]) <= _utc(self.clock())):
            raise PlatformError("session_required", 401)
        return row

    def require_actor(self, session_token):
        row = self._session_row(session_token)
        return Actor(row["user_id"], row["role"])

    def require_csrf(self, session, header, origin):
        token = session.token if isinstance(session, Session) else session
        row = self._session_row(token)
        if (not isinstance(header, str) or len(header) != 64
                or not hmac.compare_digest(row["csrf_hash"], _digest(header))
                or origin != self.origin or origin != row["origin"]):
            raise PlatformError("csrf_forbidden", 403)

    def logout(self, token):
        self._session_row(token)
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE t3_sessions SET revoked=TRUE WHERE token_hash=%s", (_digest(token),))
        self.connection.commit()
