import hashlib
import json
from pathlib import Path
import pytest
from task3_tests.test_deployment import load
from task3_tests.test_preview_install import HTTP_BLOCK, HTTPS_BLOCK

OLD='/srv/procurement-app/releases/task3-preview-synthetic-old'
NEW='/srv/procurement-app/releases/task3-preview-synthetic-new'
SOURCES='/srv/procurement-app/shared/data/synthetic-originals'


def test_update_changes_only_https_root_and_unit_working_directory():
    m=load('update_preview')
    # The official HTTP block remains current; only the already-preview HTTPS block changes.
    original=HTTP_BLOCK+HTTPS_BLOCK.replace('/srv/procurement-app/current/frontend-dist;',OLD+'/frontend-dist;').replace(':8000',':18080')
    updated=m.retarget(original,OLD,NEW)
    assert updated.startswith(HTTP_BLOCK)
    assert NEW+'/frontend-dist;' in updated
    assert m.update_unit('WorkingDirectory='+OLD+'/backend\nEnvironmentFile=/etc/private.env\n',OLD,NEW)==\
        'WorkingDirectory='+NEW+'/backend\nEnvironmentFile=/etc/private.env\n'
    with pytest.raises(ValueError):m.retarget(HTTP_BLOCK+HTTPS_BLOCK,OLD,NEW)
    with pytest.raises(ValueError):m.update_unit('WorkingDirectory=/unexpected\n',OLD,NEW)


def test_source_root_is_the_only_environment_change_never_enables_or_raises_paid_budget():
    m=load('update_preview')
    original='# preserve comment\nTASK3_PAID_ENABLED=0\nDEEPSEEK_API_KEY=synthetic-test-placeholder\nDB_PASSWORD=synthetic-placeholder\n'
    updated=m.update_environment(original,SOURCES)
    assert updated==original+'TASK3_OFFICIAL_SOURCE_ROOT='+SOURCES+'\n'
    assert m.update_environment(updated,SOURCES)==updated
    with pytest.raises(ValueError):m.update_environment(updated,SOURCES+'-other')
    with pytest.raises(ValueError):m.update_environment(original,'/srv/procurement-app/shared/data/../escape')


@pytest.mark.parametrize('old,new',[(OLD,OLD),('/srv/procurement-app/current',NEW),(OLD,'/tmp/other'),(OLD,NEW+'/../escape')])
def test_update_refuses_broad_linked_or_identical_release_names(old,new):
    with pytest.raises(ValueError):load('update_preview').release_paths(old,new)


def test_update_verifies_every_declared_byte_before_any_mutation(tmp_path):
    m=load('update_preview')
    release=tmp_path/'new';release.mkdir()
    file=release/'backend/entry.py';file.parent.mkdir();file.write_bytes(b'public synthetic code')
    document={'kind':'task3_isolated_preview_release','files':{'backend/entry.py':hashlib.sha256(file.read_bytes()).hexdigest()}}
    manifest=release/'release-manifest.json';manifest.write_text(json.dumps(document))
    expected=hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert m.verify_release(release,expected)==document
    file.write_bytes(b'tampered')
    with pytest.raises(ValueError):m.verify_release(release,expected)


def test_update_failure_restores_every_snapshot_and_checks_rollback():
    m=load('update_preview');writes=[];commands=[]
    snapshots={Path('/synthetic/site.conf'):b'old-site',Path('/synthetic/preview.env'):b'old-env'}
    def runner(command):
        commands.append(command)
        if command==['systemctl','restart','procurement-task3-preview-api']:raise RuntimeError('synthetic rollback failure')
    restored=m.restore(snapshots,lambda path,data:writes.append((path,data)),runner)
    assert writes==list(snapshots.items())
    assert restored is False
    assert ['nginx','-t'] in commands and ['systemctl','reload','nginx'] in commands


def test_official_fingerprint_connection_is_actually_streaming(monkeypatch):
    import pymysql
    from pymysql.cursors import SSDictCursor
    m=load('update_preview');calls=[]
    monkeypatch.setattr(pymysql,'connect',lambda **kwargs:calls.append(kwargs))
    m.connect({'DB_HOST':'127.0.0.1','DB_USER':'synthetic','DB_PASSWORD':'synthetic-placeholder'},
        'synthetic_official',streaming=True)
    assert calls[0]['cursorclass'] is SSDictCursor


def test_only_explicitly_audited_old_ledger_drift_is_accepted_not_new_release_tampering(tmp_path):
    m=load('update_preview');root=tmp_path/'old';file=root/'backend/task3_platform/ledger.py'
    file.parent.mkdir(parents=True);file.write_bytes(b'original synthetic ledger')
    manifest=root/'release-manifest.json'
    manifest.write_text(json.dumps({'kind':'task3_isolated_preview_release','files':{
        'backend/task3_platform/ledger.py':hashlib.sha256(file.read_bytes()).hexdigest()}}))
    expected=hashlib.sha256(manifest.read_bytes()).hexdigest()
    file.write_bytes(b'prior operator synthetic diagnostic draft')
    actual=hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises(ValueError):m.verify_release(root,expected)
    assert m.verify_release(root,expected,audited_old_overrides={'backend/task3_platform/ledger.py':actual})
    with pytest.raises(ValueError):m.verify_release(root,expected,audited_old_overrides={'backend/api/main.py':actual})
    with pytest.raises(ValueError):m.verify_release(root,expected,audited_old_overrides={'backend/task3_platform/ledger.py':'0'*64})
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()==expected
