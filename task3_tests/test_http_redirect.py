from pathlib import Path
import hashlib
import json
import pytest
from task3_tests.test_deployment import load

HTTP='''server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/synthetic-acme;
        try_files $uri =404;
    }
    location /api/ { proxy_pass http://127.0.0.1:8000; }
    location / { try_files $uri /index.html; }
}
'''
HTTPS='''server {
    listen 443 ssl;
    server_name example.invalid;
    location /api/ { proxy_pass http://127.0.0.1:18080; }
}
'''

def test_protected_config_keys_survive_the_actual_json_plan_round_trip(tmp_path):
    m=load('redirect_https');files=[tmp_path/'api.service',tmp_path/'worker.service',tmp_path/'private.env']
    for index,path in enumerate(files):path.write_bytes(str(index).encode())
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    plan=json.loads(json.dumps({'protected_files':{str(p):digest(p) for p in files}}))
    assert m.protected_hashes(files,digest)==plan['protected_files']
    files[0].write_bytes(b'actually changed')
    assert m.protected_hashes(files,digest)!=plan['protected_files']

def test_redirect_preserves_https_acme_names_and_both_listeners():
    m=load('redirect_https');value=m.redirect_http(HTTP+HTTPS,'https://example.invalid')
    assert value.endswith(HTTPS)
    assert 'listen [::]:80 default_server;' in value and 'server_name _;' in value
    acme=HTTP[HTTP.index('    location ^~'):HTTP.index('    location /api/')].rstrip()
    assert acme in value
    assert 'return 308 https://example.invalid$request_uri;' in value
    assert ':8000' not in value and value.count(':18080')==1

@pytest.mark.parametrize('origin',['http://example.invalid','https://x/other','https://x;bad','https://u:p@x','https://x\n'])
def test_redirect_rejects_unsafe_origin(origin):
    with pytest.raises(ValueError):load('redirect_https').redirect_http(HTTP+HTTPS,origin)

@pytest.mark.parametrize('text',[HTTP,HTTP+HTTP+HTTPS,HTTP.replace('/.well-known/acme-challenge/','/other/')+HTTPS])
def test_redirect_rejects_unknown_layout(text):
    with pytest.raises(ValueError):load('redirect_https').redirect_http(text,'https://example.invalid')

def test_wait_accepts_only_two_successes_after_old_workers_stop_serving():
    m=load('redirect_https');calls=[];sleeps=[]
    def probe():
        calls.append(1)
        if len(calls)<3:raise m._probe_error('http_status_200')
        return [{'http_status':308}]
    result=m.wait_for_redirect(probe,attempts=5,delay=.1,sleep=sleeps.append)
    assert result['attempts']==4 and result['consecutive_successes']==2
    assert [d.get('code') for d in result['diagnostics'][:2]]==['http_status_200']*2
    assert len(sleeps)==3

def test_wait_resets_consecutive_success_after_intermittent_failure():
    m=load('redirect_https');calls=[]
    def probe():
        calls.append(1)
        if len(calls)==2:raise OSError('synthetic sensitive server details')
        return 'checked'
    result=m.wait_for_redirect(probe,attempts=5,delay=0,sleep=lambda _:None)
    assert result['attempts']==4 and 'sensitive' not in str(result)

def test_wait_exhaustion_retains_safe_cause_not_response_body():
    m=load('redirect_https')
    def probe():raise m._probe_error('https_dataset_mismatch')
    with pytest.raises(m.RedirectVerificationError) as caught:m.wait_for_redirect(probe,attempts=2,delay=0,sleep=lambda _:None)
    assert caught.value.diagnostics[-1]['code']=='https_dataset_mismatch'

def test_real_temporary_file_is_restored_after_verification_failure(tmp_path):
    m=load('redirect_https');path=tmp_path/'site.conf';path.write_bytes(b'old');calls=[]
    def writer(path,data):path.write_bytes(data)
    def broken_probe():raise m.RedirectVerificationError([{'code':'http_status_200'}])
    result=m.change_and_verify(path,b'old',b'new',writer=writer,runner=calls.append,
        probe=broken_probe,unchanged=lambda:None,waiter=lambda probe:probe())
    assert path.read_bytes()==b'old' and not result['passed'] and result['http_configuration_restored']
    assert result['verification_diagnostics']==[{'code':'http_status_200'}]
    assert calls==[['nginx','-t'],['systemctl','reload','nginx']]*2

def test_database_or_protected_file_change_restores_site(tmp_path):
    m=load('redirect_https');path=tmp_path/'site.conf';path.write_bytes(b'old')
    def changed():raise ValueError('synthetic original row')
    result=m.change_and_verify(path,b'old',b'new',writer=lambda p,d:p.write_bytes(d),runner=lambda _:None,
        probe=lambda:True,unchanged=changed,waiter=lambda probe:probe())
    assert not result['passed'] and path.read_bytes()==b'old' and 'original row' not in str(result)

def test_success_changes_only_explicit_site_file(tmp_path):
    m=load('redirect_https');site=tmp_path/'site.conf';site.write_bytes(b'old')
    protected=tmp_path/'unit.env';protected.write_bytes(b'protected');verified=[]
    result=m.change_and_verify(site,b'old',b'new',writer=lambda p,d:p.write_bytes(d),runner=lambda _:None,
        probe=lambda:'two endpoints',unchanged=lambda:verified.append(1),waiter=lambda probe:probe())
    assert result['passed'] and site.read_bytes()==b'new' and protected.read_bytes()==b'protected' and verified==[1]
