import hashlib
import io
import stat
import zipfile
from dataclasses import replace
import pytest
from task3_tests.test_jobs import JobConnection


class UploadConnection(JobConnection):
    def __init__(self, path):
        super().__init__(path)
        self.db.executescript("""
          CREATE TABLE t3_upload_files(task_id TEXT,file_id TEXT,original_name TEXT,expected_bytes INTEGER,
            part_hashes TEXT,received_bytes INTEGER DEFAULT 0,relative_path TEXT,sha256 TEXT,
            PRIMARY KEY(task_id,file_id));
          CREATE TABLE t3_upload_parts(task_id TEXT,file_id TEXT,part_no INTEGER,sha256 TEXT,bytes INTEGER,
            relative_path TEXT,PRIMARY KEY(task_id,file_id,part_no));
        """)


@pytest.fixture
def upload_setup(platform_module, tmp_path, clock):
    limits = replace(platform_module("limits").Limits(), request_bytes=64, chunk_bytes=16,
                     file_bytes=4096, received_bytes=8192, html_bytes=1024,
                     member_bytes=2048, expanded_bytes=8192, job_disk_bytes=12288,
                     global_disk_bytes=16384, reserve_disk_bytes=1)
    connection = UploadConnection(tmp_path / "uploads.sqlite")
    store = platform_module("job_store").JobStore(connection, clock, limits=limits)
    paths = platform_module("paths").RuntimePaths(tmp_path / "runtime")
    def setup():
        service = platform_module("uploads").UploadService(store, paths, limits, secure_transport=True)
        return service, connection, paths, limits
    yield setup
    connection.db.close()


def upload_spec(c, files, limits):
    specs = []
    for name, data in files:
        hashes = tuple(hashlib.sha256(data[pos:pos + limits.chunk_bytes]).hexdigest()
                       for pos in range(0, len(data), limits.chunk_bytes))
        specs.append(c.FileSpec(name, len(data), hashes))
    return c.UploadSpec(tuple(specs), "parse_only", "b" * 64)


def send_all(service, actor, session, files, limits):
    for slot, (_, data) in zip(session.files, files):
        for part_no, pos in enumerate(range(0, len(data), limits.chunk_bytes)):
            service.put_part(actor, session.job.task_id, slot.file_id, part_no,
                             io.BytesIO(data[pos:pos + limits.chunk_bytes]))


def make_zip(entries, compression=zipfile.ZIP_STORED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return out.getvalue()


def test_chunk_replay_and_conflict(platform_module, upload_setup, actor_a, actor_b):
    service, connection, paths, limits = upload_setup()
    c = platform_module("contracts")
    files = [("t20990101_1.html", b"x" * 32)]
    session = service.begin(actor_a, upload_spec(c, files, limits), "chunks")
    slot = session.files[0]
    receipt = service.put_part(actor_a, session.job.task_id, slot.file_id, 0, io.BytesIO(b"x" * 16))
    assert receipt.reused is False and receipt.bytes == 16
    before = sum(p.stat().st_size for p in paths.task(session.job.task_id).received.rglob("*") if p.is_file())
    replay = service.put_part(actor_a, session.job.task_id, slot.file_id, 0, io.BytesIO(b"x" * 16))
    assert replay.reused is True
    assert sum(p.stat().st_size for p in paths.task(session.job.task_id).received.rglob("*") if p.is_file()) == before
    with pytest.raises(c.PlatformError) as conflict:
        service.put_part(actor_a, session.job.task_id, slot.file_id, 0, io.BytesIO(b"y" * 16))
    assert conflict.value.http_status == 409
    with pytest.raises(c.PlatformError) as too_large:
        service.put_part(actor_a, session.job.task_id, slot.file_id, 1, io.BytesIO(b"x" * 17))
    assert too_large.value.http_status == 413
    with pytest.raises(c.PlatformError):
        service.put_part(actor_b, session.job.task_id, slot.file_id, 1, io.BytesIO(b"x" * 16))
    with pytest.raises(c.PlatformError) as incomplete:
        service.complete(actor_a, session.job.task_id)
    assert incomplete.value.code == "upload_parts_missing"
    resumed = service.begin(actor_a, upload_spec(c, files, limits), "chunks")
    assert resumed.files[0].received_parts == (0,)


def test_canonical_htm_and_same_name_attachment(platform_module, upload_setup, actor_a):
    service, _, paths, limits = upload_setup()
    c = platform_module("contracts")
    html = b"<html><body>synthetic source</body></html>"
    attached = make_zip([("folder/spec.txt", b"synthetic attachment")])
    files = [("t20990101_1.HTM", html), ("t20990101_1.ZIP", attached)]
    session = service.begin(actor_a, upload_spec(c, files, limits), "canonical")
    send_all(service, actor_a, session, files, limits)
    job = service.complete(actor_a, session.job.task_id)
    assert job.status == "queued"
    prepared, diagnostics = service.prepare_inputs(job)
    assert len(prepared) == 1 and diagnostics == []
    assert prepared[0].announcement_id == "t20990101_1"
    assert prepared[0].html_path.name == "t20990101_1.html"
    assert prepared[0].html_path.read_bytes() == html
    assert prepared[0].zip_path.read_bytes() == attached
    assert prepared[0].html_sha == hashlib.sha256(html).hexdigest()
    assert service.complete(actor_a, session.job.task_id).task_id == job.task_id


def test_missing_attachment_is_visible_not_fake_failure(platform_module, upload_setup, actor_a):
    service, _, _, limits = upload_setup()
    c = platform_module("contracts")
    files = [("t20990101_1.html", b"<p>synthetic</p>")]
    session = service.begin(actor_a, upload_spec(c, files, limits), "missing")
    send_all(service, actor_a, session, files, limits)
    job = service.complete(actor_a, session.job.task_id)
    prepared, diagnostics = service.prepare_inputs(job)
    assert prepared[0].zip_path is None
    assert diagnostics[0]["code"] == "attachment_not_provided"
    assert job.status == "queued"


@pytest.mark.parametrize("files", [
    [("t20990101_1.html", b"ok"), ("t20990101_1.HTM", b"other")],
    [("../t20990101_1.html", b"ok")],
    [("C:\\t20990101_1.html", b"ok")],
])
def test_colliding_or_unsafe_upload_names_rejected(platform_module, upload_setup, actor_a, files):
    service, _, _, limits = upload_setup()
    c = platform_module("contracts")
    with pytest.raises(c.PlatformError):
        service.begin(actor_a, upload_spec(c, files, limits), "unsafe")


def test_non_utf8_and_nonstandard_id_never_queue(platform_module, upload_setup, actor_a):
    service, _, _, limits = upload_setup()
    c = platform_module("contracts")
    files = [("t20990101_1.html", b"<p>bad\xff</p>")]
    session = service.begin(actor_a, upload_spec(c, files, limits), "encoding")
    send_all(service, actor_a, session, files, limits)
    with pytest.raises(c.PlatformError) as invalid:
        service.complete(actor_a, session.job.task_id)
    assert invalid.value.code == "html_not_utf8"
    assert service.store.get(actor_a, session.job.task_id).status == "receiving"
    with pytest.raises(c.PlatformError):
        service.begin(actor_a, upload_spec(c, [("unknown.html", b"ok")], limits), "unknown")


@pytest.mark.parametrize("member", ["../escape.txt", "/absolute.txt", "C:/drive.txt", "\\\\host\\share.txt", "a/../../x.txt"])
def test_zip_and_pair_limits(platform_module, tmp_path, member):
    c = platform_module("contracts")
    archive = tmp_path / "bad.zip"
    archive.write_bytes(make_zip([(member, b"evil")]))
    with pytest.raises(c.PlatformError):
        platform_module("archive_safety").inspect_archive(archive, platform_module("limits").Limits())
    assert not (tmp_path / "escape.txt").exists()


def test_zip_link_duplicate_corrupt_encrypted_and_ratio(platform_module, tmp_path):
    c = platform_module("contracts")
    safety = platform_module("archive_safety")
    limits = platform_module("limits").Limits()
    link = zipfile.ZipInfo("symlink.txt")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    data = make_zip([(link, b"../../private")])
    duplicate = make_zip([("a/file.txt", b"a"), ("b/FILE.txt", b"b")])
    bomb = make_zip([("large.txt", b"0" * 100000)], zipfile.ZIP_DEFLATED)
    encrypted = bytearray(make_zip([("a.txt", b"a")]))
    central = encrypted.index(b"PK\x01\x02")
    encrypted[central + 8] |= 1
    corrupt = bytearray(make_zip([("a.txt", b"synthetic")]))
    central2 = corrupt.index(b"PK\x01\x02")
    corrupt[central2 + 16] ^= 0xFF
    for index, payload in enumerate((data, duplicate, bomb, encrypted, corrupt, b"not a zip")):
        path = tmp_path / (str(index) + ".zip")
        path.write_bytes(payload)
        with pytest.raises(c.PlatformError):
            safety.inspect_archive(path, limits)


def test_inner_member_limit_and_actual_stream_limit(platform_module, tmp_path):
    c = platform_module("contracts")
    safety = platform_module("archive_safety")
    limits = replace(platform_module("limits").Limits(), html_bytes=1, member_bytes=8)
    path = tmp_path / "inner.zip"
    path.write_bytes(make_zip([("spec.txt", b"123456789")]))
    with pytest.raises(c.PlatformError):
        safety.inspect_archive(path, limits, kind="attachment")
    output = io.BytesIO()
    with pytest.raises(c.PlatformError) as too_large:
        safety.copy_bounded(io.BytesIO(b"123456789"), output, 8)
    assert too_large.value.http_status == 413
    assert len(output.getvalue()) <= 8


def test_outer_zip_pairs_and_aggregate_limits(platform_module, upload_setup, actor_a):
    service, _, _, limits = upload_setup()
    c = platform_module("contracts")
    attached = make_zip([("spec.txt", b"synthetic")])
    data = make_zip([("html/t20990101_1.HTM", b"<p>source</p>"), ("zips/t20990101_1.zip", attached)])
    files = [("batch.zip", data)]
    session = service.begin(actor_a, upload_spec(c, files, limits), "outer")
    send_all(service, actor_a, session, files, limits)
    job = service.complete(actor_a, session.job.task_id)
    prepared, _ = service.prepare_inputs(job)
    assert len(prepared) == 1 and prepared[0].zip_path is not None
    constrained = replace(limits, expanded_bytes=8)
    service.limits = constrained
    # Inspection counts actual decompressed bytes, not just received compressed bytes.
    with pytest.raises(c.PlatformError):
        platform_module("archive_safety").inspect_archive(prepared[0].zip_path, constrained, kind="attachment")


def test_disk_reserve_refuses_before_write(platform_module, upload_setup, actor_a, monkeypatch):
    service, _, paths, limits = upload_setup()
    c = platform_module("contracts")
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(platform_module("uploads").shutil, "disk_usage", lambda path: usage(100, 100, 0))
    with pytest.raises(c.PlatformError) as full:
        service.begin(actor_a, upload_spec(c, [("t20990101_1.html", b"ok")], limits), "disk")
    assert full.value.code == "disk_reserve_required"
    assert not paths.root.exists() or not any(paths.root.rglob("*.html"))


def test_assembly_restart_keeps_confirmed_file_before_later_incomplete_file(platform_module, upload_setup, actor_a):
    service, connection, paths, limits = upload_setup()
    c = platform_module("contracts")
    files = [("t20990101_1.html", b"<p>one</p>"), ("t20990101_2.html", b"<p>two</p>")]
    session = service.begin(actor_a, upload_spec(c, files, limits), "assembly-restart")
    first = sorted(session.files, key=lambda slot: slot.file_id)[0]
    body = dict(files)[first.name]
    service.put_part(actor_a, session.job.task_id, first.file_id, 0, io.BytesIO(body))
    with pytest.raises(c.PlatformError):
        service.complete(actor_a, session.job.task_id)
    row = dict(connection.db.execute("SELECT * FROM t3_upload_files WHERE file_id=?", (first.file_id,)).fetchone())
    assert row["sha256"] == hashlib.sha256(body).hexdigest()
    assert (paths.root / row["relative_path"]).read_bytes() == body
    assert not service._part_path(session.job.task_id, first.file_id, 0).exists()
    other = next(slot for slot in session.files if slot != first)
    service.put_part(actor_a, session.job.task_id, other.file_id, 0, io.BytesIO(dict(files)[other.name]))
    assert service.complete(actor_a, session.job.task_id).status == "queued"


def test_eight_received_plus_eight_expanded_cannot_bypass_twelve_total(platform_module, upload_setup, actor_a):
    service, connection, paths, limits = upload_setup()
    c = platform_module("contracts")
    files = [("t20990101_1.html", b"<p>ok</p>")]
    session = service.begin(actor_a, upload_spec(c, files, limits), "combined-disk")
    root = paths.task(session.job.task_id).received
    (root / "synthetic-existing").write_bytes(b"x" * 8192)
    from task3_platform.job_store import transaction
    with pytest.raises(c.PlatformError) as too_large:
        with transaction(connection) as cursor:
            service.store._runtime(cursor)
            row = service.store._row(cursor, session.job.task_id, lock=True)
            service._reserve(cursor, row, 8192)
    assert too_large.value.code == "job_disk_quota"


def test_outer_expansion_already_on_disk_is_not_reserved_twice(platform_module, upload_setup, actor_a):
    service, connection, paths, limits = upload_setup()
    c = platform_module("contracts")
    service.limits = replace(limits, file_bytes=2000, received_bytes=2000,
                            expanded_bytes=2500, job_disk_bytes=3000, global_disk_bytes=4000)
    service.store.limits = service.limits
    attached = make_zip([("spec.txt", b"s" * 1000)])
    outer = make_zip([("t20990101_1.html", b"<p>ok</p>"), ("t20990101_1.zip", attached)], zipfile.ZIP_DEFLATED)
    files = [("batch.zip", outer)]
    session = service.begin(actor_a, upload_spec(c, files, service.limits), "accurate-disk")
    send_all(service, actor_a, session, files, service.limits)
    assert service.complete(actor_a, session.job.task_id).status == "queued"
    assert service._disk_bytes(session.job.task_id) + 1000 <= 3000


def test_zip_nul_filename_is_rejected_before_zipfile_truncation(platform_module, tmp_path):
    c = platform_module("contracts")
    payload = bytearray(make_zip([("safer.txt", b"synthetic")]))
    payload[30 + 4] = 0
    central = payload.index(b"PK\x01\x02")
    payload[central + 46 + 4] = 0
    path = tmp_path / "nul-name.zip"
    path.write_bytes(payload)
    with pytest.raises(c.PlatformError) as denied:
        platform_module("archive_safety").inspect_archive(path, platform_module("limits").Limits())
    assert denied.value.code == "unsafe_archive_name"
