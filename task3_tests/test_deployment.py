import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

def load(name):
    path=ROOT/'deploy/task3'/f'{name}.py'
    assert path.is_file(),'Deployment interface not implemented: '+name
    spec=importlib.util.spec_from_file_location('task3_'+name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def evidence():
    return {"kind":"real_isolated_acceptance","target_database":"procurement_task3_accept_synthetic_guard",
        "release_sha256":"a"*64,"old_dataset_fingerprint":"b"*64,"old_dataset_fingerprint_unchanged":True,
        "gates":{"mysql":True,"tls":True,"browser":True,"three_users_processing":True,"linux_cgroup":True,
            "memory_no_oom":True,"model_counter_verified":True,"finite_model_authorization":True,"real_model_sample":True,"whole_branch_review":True},
        "query_samples":[{"query_type":name,"dataset_id":"up_"+"c"*32,"elapsed_ms":20} for name in ('buyer_suppliers','buyer_bidders','supplier_peers','common_buyers','common_projects')],
        "resource":{"vcpu":2,"memory_bytes":4*1024**3,"worker_peak_bytes":200*1024**2,"api_peak_bytes":100*1024**2,"available_min_bytes":512*1024**2,"oom_events":0},
        "model_sample":{"announcements":3,"sent_calls":8,"input_tokens":1000,"output_tokens":1000,"usage_unknown":False}}

def test_deploy_refuses_live_target_missing_tls_or_browser_gate():
    module=load('finalize_release')
    report=evidence()
    with pytest.raises(Exception): module.finalize_release({**report,'target_database':'procurement_kg_v14_20261014'},'b'*64)
    for gate in report['gates']:
        changed={**report,'gates':{**report['gates'],gate:False}}
        with pytest.raises(Exception): module.finalize_release(changed,'b'*64)
    with pytest.raises(Exception): module.finalize_release(report,'d'*64)
    checked=module.finalize_release(report,'b'*64)
    assert checked['old_dataset_fingerprint_unchanged'] is True
    assert checked['applied'] is False
    with pytest.raises(Exception): module.finalize_release({**report,'kind':'local_synthetic_browser'},'b'*64)

def test_worker_unit_and_no_startup_ddl():
    unit=ROOT/'deploy/task3/procurement-worker.service'
    assert unit.is_file()
    text=unit.read_text(encoding='utf-8')
    assert 'MemoryMax=1024M' in text and 'KillMode=control-group' in text
    assert 'task3_platform.runtime' in text and 'Restart=on-failure' in text
    assert 'migrate' not in text and 'initialize' not in text
    api=(ROOT/'task1_entity_extraction/api/main.py').read_text(encoding='utf-8')
    assert 'init_tables(' not in api and 'create_db=True' not in api

def test_preflight_redacts_secrets_and_refuses_nonisolated_target():
    module=load('preflight')
    config={'database':'procurement_task3_accept_synthetic','official_database':'procurement_kg_v14_20261014',
        'public_origin':'https://example.invalid','runtime_root':str(ROOT/'test-artifacts/runtime'),'DB_PASSWORD':'synthetic-private-value','DEEPSEEK_API_KEY':'synthetic-private-key'}
    report=module.preflight(config)
    assert 'synthetic-private' not in str(report)
    assert report['real_environment_verified'] is False
    with pytest.raises(Exception): module.preflight({**config,'database':config['official_database']})

def test_performance_or_resource_failure_cannot_finalize():
    module=load('finalize_release');report=evidence()
    bad={**report,'query_samples':[dict(sample,elapsed_ms=6000) for sample in report['query_samples']]}
    with pytest.raises(Exception): module.finalize_release(bad,'b'*64)
    with pytest.raises(Exception): module.finalize_release({**report,'resource':{**report['resource'],'oom_events':1}},'b'*64)
    with pytest.raises(Exception): module.finalize_release({**report,'model_sample':{**report['model_sample'],'sent_calls':9}},'b'*64)


def test_exact_512mib_available_minimum_required():
    module=load('finalize_release');report=evidence()
    with pytest.raises(ValueError):
        module.finalize_release({**report,'resource':{**report['resource'],'available_min_bytes':511*1024**2}},'b'*64)


def test_preparation_can_never_manufacture_a_cutover_pass(tmp_path):
    release=tmp_path/'release';release.mkdir()
    (release/'release-manifest.json').write_text('{"synthetic":true}',encoding='utf-8')
    report=load('prepare_acceptance').prepare_acceptance(release,{
        'database':'procurement_task3_accept_synthetic','official_database':'procurement_kg_v14_20261014',
        'public_origin':'https://example.invalid','runtime_root':str(tmp_path/'runtime')})
    assert report['passed'] is False and report['ready_to_cutover'] is False
    assert report['new_model_calls']==0 and 'real_mysql' in report['missing_gates']
    assert report['environment']['database_connected'] is False


def test_mysql_probe_only_hashes_whitelisted_tables_and_uses_isolated_environment(tmp_path):
    module=load('run_mysql_acceptance')
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql):assert sql.startswith('SELECT * FROM ')
        def __iter__(self):return iter([{'id':1,'value':'synthetic-private-source'}])
    class Connection:
        def begin(self):pass
        def rollback(self):pass
        def cursor(self):return Cursor()
    values=module.fingerprints(Connection())
    assert len(values)==9 and all(value['rows']==1 for value in values.values())
    assert 'synthetic-private-source' not in str(values)
    config={'DB_HOST':'127.0.0.1','DB_PORT':'3306','DB_USER':'synthetic','DB_PASSWORD':'synthetic-secret','DB_NAME':'procurement_kg_v14_20261014'}
    env=module.acceptance_environment(config,'procurement_task3_accept_synthetic',tmp_path,tmp_path/'test-deps')
    assert env['TASK3_MYSQL_TEST_DB']==env['DB_NAME']=='procurement_task3_accept_synthetic'
    assert env['TASK3_PAID_ENABLED']=='0' and 'DEEPSEEK_API_KEY' not in env
    with pytest.raises(ValueError):module.acceptance_environment(config,config['DB_NAME'],tmp_path,tmp_path/'test-deps')


def test_mysql_workflow_receipts_are_explicit_private_paths_not_model_configuration(tmp_path):
    module=load('run_mysql_acceptance')
    config={'DB_HOST':'127.0.0.1','DB_USER':'synthetic','DB_PASSWORD':'synthetic-secret','DB_NAME':'procurement_kg_v14_20261014'}
    env=module.acceptance_environment(config,'procurement_task3_accept_synthetic',tmp_path,tmp_path/'deps',
        workflow_runtime=tmp_path/'workflow-runtime',workflow_receipts=tmp_path/'private-evidence')
    assert env['TASK3_MYSQL_WORKFLOW_RUNTIME']==str(tmp_path/'workflow-runtime')
    assert env['TASK3_MYSQL_WORKFLOW_RECEIPTS']==str(tmp_path/'private-evidence')
    assert env['TASK3_PAID_ENABLED']=='0' and not any('API_KEY' in key for key in env)
