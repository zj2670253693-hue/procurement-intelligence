from pathlib import Path
import importlib.util
import pytest


def module(name):
    import sys
    here=Path(__file__).resolve().parent;sys.path.insert(0,str(here))
    spec=importlib.util.spec_from_file_location(name,here/(name+'.py'));obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
    return obj


def test_cannot_import_into_online_or_unexpected_database():
    m=module('prepare_isolated')
    with pytest.raises(ValueError):m.reject_live_target(m.TARGET,m.TARGET)
    with pytest.raises(ValueError):m.reject_live_target('old','procurement_kg')
    m.reject_live_target('old',m.TARGET)


def test_identifiers_are_checked_not_string_injected():
    m=module('prepare_isolated')
    assert m.checked_identifier('announcements')=='`announcements`'
    for name in ('table; DROP TABLE x','name`','a.b',''):
        with pytest.raises(ValueError):m.checked_identifier(name)


def test_env_cutover_only_changes_database_and_preserves_secrets_verbatim():
    m=module('admin_cutover')
    raw=b'DB_USER=synthetic\nDB_PASSWORD="synthetic-only"\nDB_NAME=old\nOTHER=value\n'
    assert m.replace_database(raw,'new')==raw.replace(b'DB_NAME=old',b'DB_NAME=new')
    with pytest.raises(ValueError):m.replace_database(raw+b'DB_NAME=duplicate\n','new')
    with pytest.raises(ValueError):m.replace_database(b'OTHER=one\n','new')


class Cursor:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql):pass
    def fetchall(self):return [{'Tables_in_target':'already_exists'}]


def test_clone_refuses_nonempty_target_before_reading_source_or_deleting():
    m=module('prepare_isolated')
    class Target:
        def cursor(self):return Cursor()
    with pytest.raises(ValueError,match='fresh and empty'):m.clone(None,Target())


def test_package_rejects_unlisted_env_or_code(tmp_path):
    import json,hashlib
    m=module('prepare_isolated');root=tmp_path/'release';(root/'backend').mkdir(parents=True);(root/'frontend-dist').mkdir()
    (root/'backend/safe.py').write_bytes(b'# synthetic\n')
    manifest={'release':m.RELEASE,'new_model_calls':0,'files':{'backend/safe.py':hashlib.sha256(b'# synthetic\n').hexdigest()}}
    (root/'release-manifest.json').write_text(json.dumps(manifest))
    m.verify_package(root)
    (root/'backend/.env').write_bytes(b'SYNTHETIC=only')
    with pytest.raises(ValueError,match='catalog'):m.verify_package(root)


def test_cutover_rejects_missing_browser_gate_or_manifest_change():
    m=module('admin_cutover')
    accepted={'passed':True,'browser_interactive_qa_done':False,'release_manifest_sha256':'original'}
    with pytest.raises(ValueError):m.require_readiness(accepted,'original')
    accepted['browser_interactive_qa_done']=True
    with pytest.raises(ValueError):m.require_readiness(accepted,'changed')
    m.require_readiness(accepted,'original')


def test_runtime_directory_is_narrow_and_code_not_writable(tmp_path):
    m=module('admin_cutover');root=tmp_path/'release';(root/'backend/output').mkdir(parents=True)
    runtime=tmp_path/'shared'/'runtime-test'
    m.configure_runtime(root,runtime,owner=None)
    assert (runtime/'uploads').is_dir()
    assert not (root/'backend/output/uploads').exists()


def test_failure_recovery_restores_env_link_service_and_checks_old_health():
    m=module('admin_cutover');calls=[]
    m.recover(lambda:calls.append('env'),lambda:calls.append('link'),lambda:calls.append('restart'),lambda:calls.append('old-health'))
    assert calls==['env','link','restart','old-health']
