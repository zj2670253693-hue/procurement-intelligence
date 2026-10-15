"""Read-only environment probe. Configuration is never proof of acceptance."""
import os
from pathlib import Path
import platform
import re
import shutil
from urllib.parse import urlsplit


def require_isolated_database(database, official_database=None):
    if (not isinstance(database, str) or len(database)>64 or
            not re.fullmatch(r"procurement_task3_accept_[A-Za-z0-9_]+", database) or
            database == official_database):
        raise ValueError("explicit_isolated_acceptance_database_required")
    return database


def preflight(config):
    database = require_isolated_database(config.get("database"), config.get("official_database"))
    origin = urlsplit(config.get("public_origin", ""))
    if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password or
            origin.query or origin.fragment or origin.path not in ("", "/")):
        raise ValueError("public_https_origin_required")
    supplied = Path(config.get("runtime_root", ""))
    if not supplied.is_absolute():
        raise ValueError("absolute_runtime_root_required")
    # Do not create the runtime, follow an existing symlink, or read secret config.
    for path in (supplied, *supplied.parents):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("linked_runtime_root_forbidden")
    release = config.get("release_root")
    if release and supplied.resolve().is_relative_to(Path(release).resolve()):
        raise ValueError("runtime_inside_release_forbidden")
    ancestor = supplied
    while not ancestor.exists():
        ancestor = ancestor.parent
    system = platform.system()
    memory = None
    ubuntu24 = False
    if system == "Linux":
        values = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
        ubuntu24 = values.get("ID", "").strip('"') == "ubuntu" and values.get("VERSION_ID", "").strip('"') == "24.04"
        match = re.search(r"(?m)^MemTotal:\s+(\d+)\s+kB$", Path("/proc/meminfo").read_text())
        memory = int(match.group(1))*1024 if match else None
    cpus = os.cpu_count()
    # A local Windows machine or a larger host cannot certify the official limit.
    verified = ubuntu24 and cpus == 2 and memory is not None and 3*1024**3 <= memory <= 4*1024**3
    return {"kind": "read_only_environment_probe", "target_database": database,
            "https_configured": True, "public_tls_verified": False,
            "system": system, "ubuntu_24_04": ubuntu24, "vcpu": cpus,
            "memory_bytes": memory, "disk_free_bytes": shutil.disk_usage(ancestor).free,
            "real_environment_verified": verified,
            "database_connected": False, "worker_cgroup_verified": False,
            "ready_to_cutover": False}
