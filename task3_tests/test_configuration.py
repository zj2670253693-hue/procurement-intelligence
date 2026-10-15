import hashlib
import json
import pytest
from task3_tests.test_inference_ledger import ledger_setup
from task3_tests.test_web import web_case
from task3_tests.test_datasets import dataset_setup


def test_processing_identity_changes_with_effective_model_and_parser_settings(platform_module,monkeypatch):
    module=platform_module('services')
    monkeypatch.setenv('DEEPSEEK_MODEL','synthetic-model-A');a=module.processing_fingerprint()
    monkeypatch.setenv('DEEPSEEK_MODEL','synthetic-model-B');b=module.processing_fingerprint()
    assert a!=b,'Model configuration missing from processing identity'
    monkeypatch.setenv('OCR_MAX_PAGES_PER_PDF','3');c=module.processing_fingerprint()
    assert b!=c
    monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-secret-rotation')
    assert module.processing_fingerprint()==c,'Secret is not algorithm identity'


def test_parser_child_receives_only_effective_nonsecret_settings(platform_module,monkeypatch,tmp_path):
    module=platform_module('processing_profile')
    monkeypatch.setenv('OCR_MAX_PAGES_PER_PDF','3')
    monkeypatch.setenv('DB_PASSWORD','synthetic-private')
    values=module.parser_settings()
    assert values['OCR_MAX_PAGES_PER_PDF']=='3'
    assert 'DB_PASSWORD' not in values and 'API_KEY' not in str(values)


def test_processing_code_identity_covers_flat_deployed_backend(platform_module,monkeypatch,tmp_path):
    module=platform_module('services')
    root=tmp_path/'backend';(root/'task3_platform').mkdir(parents=True);(root/'models').mkdir()
    stub=root/'task3_platform/services.py';stub.write_text('# synthetic service')
    model=root/'models/schemas.py';model.write_text('# synthetic model A')
    monkeypatch.setattr(module,'__file__',str(stub));module._code_fingerprint.cache_clear()
    try:
        before=module._code_fingerprint();model.write_text('# synthetic model B');module._code_fingerprint.cache_clear()
        assert module._code_fingerprint()!=before,'Flat deployment core code missing from identity'
    finally:module._code_fingerprint.cache_clear()


def profile_env(tmp_path,owner_id):
    counter=tmp_path/'counters/approved.py';counter.parent.mkdir()
    counter.write_text("class Counter:\n verified=True\n profile_sha256='"+'f'*64+"'\n def supports(self,p,m): return (p,m)==('deepseek','synthetic-A')\n def upper_bound(self,r): return 8\ndef build_counter(): return Counter()\n",encoding='utf-8')
    profile={'version':1,'provider':'deepseek','model':'synthetic-A','base_url':'https://example.invalid/v1',
        'authorized_owner_ids':[owner_id],'maximum_budget':{'max_calls':8,'max_input_tokens':1024,'max_output_tokens':100000},
        'generation':{'temperature':'0.1','max_tokens':64},
        'counter':{'path':str(counter),'source_sha256':hashlib.sha256(counter.read_bytes()).hexdigest(),'profile_sha256':'f'*64},
        'verification':{'approved':True,'record_sha256':'e'*64}}
    path=tmp_path/'approved-profile.json';path.write_text(json.dumps(profile),encoding='utf-8')
    return {'TASK3_PAID_ENABLED':'1','TASK3_INFERENCE_PROFILE':str(path),'DEEPSEEK_API_KEY':'synthetic-key'},path,profile


def test_production_configuration_wires_finite_authorization_and_ledger_without_network(platform_module,ledger_setup,tmp_path,actor_a):
    module=platform_module('inference_configuration')
    jobs,ledger,_,backend,request,_=ledger_setup()
    env,path,profile=profile_env(tmp_path,actor_a.user_id)
    made=[]
    def factory(**kwargs): made.append(1);return backend
    config=module.configured_inference(env,backend_factory=factory)
    assert made==[]
    from task3_tests.test_jobs import spec
    from dataclasses import replace
    c=platform_module('contracts')
    config.authorize_upload(actor_a,replace(spec(c,mode='extract'),budget=c.ModelBudget(8,1024,100000)))
    with pytest.raises(c.PlatformError):
        config.authorize_upload(c.Actor('d'*32,'reviewer'),replace(spec(c,mode='extract'),budget=c.ModelBudget(8,1024,100000)))
    transport=config.transport_factory(jobs,ledger.lease,None)
    assert transport.provider=='deepseek' and transport.model=='synthetic-A'
    response=transport(request)
    assert response.content=='{"entities":[]}' and backend.sent==1
    env['TASK3_PAID_ENABLED']='0'
    with pytest.raises(__import__('models.inference',fromlist=['InferenceHalt']).InferenceHalt): transport(request)
    assert backend.sent==1


def test_configuration_stays_off_or_rejects_unverified_counter(platform_module,tmp_path,actor_a):
    module=platform_module('inference_configuration')
    assert module.configured_inference({'PAID_INFERENCE_ENABLED':'1'}) is None
    env,path,profile=profile_env(tmp_path,actor_a.user_id)
    profile['verification']['approved']=False;path.write_text(json.dumps(profile),encoding='utf-8')
    with pytest.raises(platform_module('contracts').PlatformError): module.configured_inference(env)


def test_missing_worker_credentials_cannot_advertise_paid_configuration(platform_module,tmp_path,actor_a):
    module=platform_module('inference_configuration')
    env,*_=profile_env(tmp_path,actor_a.user_id);env.pop('DEEPSEEK_API_KEY')
    with pytest.raises(platform_module('contracts').PlatformError): module.configured_inference(env)


def test_unverified_loaded_counter_cannot_create_backend(platform_module,ledger_setup,tmp_path,actor_a):
    module=platform_module('inference_configuration')
    jobs,ledger,*_=ledger_setup()
    env,path,profile=profile_env(tmp_path,actor_a.user_id)
    counter=__import__('pathlib').Path(profile['counter']['path'])
    counter.write_text(counter.read_text().replace('verified=True','verified=False'))
    profile['counter']['source_sha256']=hashlib.sha256(counter.read_bytes()).hexdigest();path.write_text(json.dumps(profile))
    made=[];configuration=module.configured_inference(env,backend_factory=lambda **kwargs:made.append(1))
    with pytest.raises(__import__('models.inference',fromlist=['InferenceHalt']).InferenceHalt):
        configuration.transport_factory(jobs,ledger.lease,None)
    assert made==[]


def test_actual_api_and_worker_compositions_install_only_configured_factory(platform_module,web_case,monkeypatch,tmp_path):
    from types import SimpleNamespace
    import database.connection
    _,connection,*_=web_case
    marker=lambda *args:None
    configuration=SimpleNamespace(authorize_upload=marker,transport_factory=marker)
    monkeypatch.setattr(platform_module('inference_configuration'),'safe_configuration',lambda:configuration)
    class Manager:
        def __init__(self,*args):self._conn=connection
        def connect(self,**kwargs):assert kwargs['create_db'] is False;return self
        def close(self,**kwargs):pass
    monkeypatch.setattr(database.connection,'DatabaseManager',Manager)
    for key,value in {'TASK3_ENABLED':'1','TASK3_DB_NAME':'procurement_task3_accept_synthetic',
                      'TASK3_PUBLIC_ORIGIN':'https://testserver','TASK3_RUNTIME_ROOT':str(tmp_path/'runtime')}.items():monkeypatch.setenv(key,value)
    request=SimpleNamespace(url=SimpleNamespace(scheme='https'),client=SimpleNamespace(host='127.0.0.1'),headers={})
    with platform_module('services').configured_services(request,tmp_path) as services:
        assert services.uploads.extract_authorizer is marker
        steps=platform_module('runtime').configured_runtime_steps(services.jobs,services.paths)
        assert steps.transport_factory is marker
