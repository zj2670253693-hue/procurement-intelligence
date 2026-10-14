"""Only explicit server configuration may adjust resource limits."""
from dataclasses import dataclass, fields
from .contracts import PlatformError

MiB = 1024**2
GiB = 1024**3


@dataclass(frozen=True)
class Limits:
    request_bytes: int = 64 * MiB
    chunk_bytes: int = 16 * MiB
    file_bytes: int = 4 * GiB
    received_bytes: int = 8 * GiB
    html_bytes: int = 16 * MiB
    announcements: int = 2000
    outer_entries: int = 10000
    attachment_entries: int = 2000
    member_bytes: int = 128 * MiB
    expanded_bytes: int = 8 * GiB
    compression_ratio: int = 200
    job_disk_bytes: int = 12 * GiB
    global_disk_bytes: int = 16 * GiB
    reserve_disk_bytes: int = 4 * GiB
    queue_size: int = 10
    active_jobs: int = 1
    parse_workers: int = 1
    model_inflight: int = 1
    job_seconds: int = 6 * 3600
    parse_seconds: int = 180
    model_seconds: int = 120
    heartbeat_seconds: int = 10
    lease_seconds: int = 60

    def __post_init__(self):
        if any(type(getattr(self, field.name)) is not int or getattr(self, field.name) <= 0
               for field in fields(self)):
            raise PlatformError("invalid_limits")
        if (self.chunk_bytes > self.request_bytes or self.chunk_bytes > self.file_bytes
                or self.file_bytes > self.received_bytes
                or self.html_bytes > self.member_bytes
                or self.received_bytes > self.job_disk_bytes
                or self.expanded_bytes > self.job_disk_bytes
                or self.job_disk_bytes > self.global_disk_bytes
                or self.heartbeat_seconds >= self.lease_seconds
                or (self.active_jobs, self.parse_workers, self.model_inflight) != (1, 1, 1)):
            raise PlatformError("inconsistent_limits")

    @classmethod
    def from_env(cls, mapping):
        values = {}
        for field in fields(cls):
            key = "TASK3_" + field.name.upper()
            if key in mapping:
                try:
                    raw = mapping[key]
                    if isinstance(raw, bool) or (not isinstance(raw, int) and
                            (not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal())):
                        raise ValueError
                    values[field.name] = int(raw)
                except (ValueError, TypeError) as exc:
                    raise PlatformError("invalid_limits") from exc
        return cls(**values)
