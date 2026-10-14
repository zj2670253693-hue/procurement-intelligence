"""Explicit local administrator CLI, not exposed as a web registration route."""
import argparse
import getpass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task3_platform.auth import AuthService
from task3_platform.contracts import PlatformError


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default echoes unrecognized arguments, possibly passwords.
        self.exit(2, "invalid_arguments; use --help\n")


def connect():
    import pymysql
    from pymysql.cursors import DictCursor
    database = os.environ.get("DB_NAME")
    user = os.environ.get("DB_USER")
    if not database or not user:
        raise PlatformError("database_configuration_required", 503)
    return pymysql.connect(host=os.environ.get("DB_HOST", "127.0.0.1"),
                           port=int(os.environ.get("DB_PORT", "3306")),
                           user=user, password=os.environ.get("DB_PASSWORD", ""),
                           database=database, charset="utf8mb4", cursorclass=DictCursor,
                           autocommit=True, connect_timeout=10)


def main(argv=None):
    parser = SafeParser(description="Provision a platform user after explicit Task3 migration.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=("reviewer", "admin"), default="reviewer")
    args = parser.parse_args(argv)
    connection = None
    try:
        password = getpass.getpass("Platform password (12+ characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise PlatformError("password_confirmation_mismatch")
        connection = connect()
        actor = AuthService(connection).provision(args.username, password, args.role)
        print("platform_user_created role=" + actor.role)
        return 0
    except PlatformError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    except Exception:
        print("platform_user_provision_failed", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
