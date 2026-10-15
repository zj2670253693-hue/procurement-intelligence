"""Admin TLS orchestration: real temporary files, external programs at boundary."""
from pathlib import Path
import hashlib
import pytest
from task3_tests.test_deployment import load

ORIGINAL=b'''server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    root /srv/procurement-app/current/frontend-dist;
    location /api/ { proxy_pass http://127.0.0.1:8000; }
    location / { try_files $uri $uri/ /index.html; }
}
'''


def fixture_paths(tmp_path):
    m=load('configure_ip_tls')
    m.IP='203.0.113.10'
    config=tmp_path/'nginx'/'procurement-app';config.parent.mkdir()
    config.write_bytes(ORIGINAL)
    paths=m.Paths(config=config,webroot=tmp_path/'webroot',state=tmp_path/'state',
        certbot=tmp_path/'venv'/'bin'/'certbot',cert_config=tmp_path/'cert-config',
        units=tmp_path/'units')
    paths.units.mkdir()
    return m,paths


class Programs:
    def __init__(self,fail=None):self.calls=[];self.fail=fail
    def __call__(self,args):
        self.calls.append(args)
        if self.fail and self.fail(args):raise RuntimeError('synthetic_program_failure')


def test_tls_authorization_and_wrong_config_fail_before_any_write(tmp_path):
    m,p=fixture_paths(tmp_path);run=Programs()
    for authorized,digest in ((False,hashlib.sha256(ORIGINAL).hexdigest()),(True,'0'*64)):
        with pytest.raises(ValueError):m.install(p,authorized=authorized,expected_sha256=digest,run=run,probe=lambda tls:{'status':'ok'})
    assert not run.calls and not p.state.exists() and p.config.read_bytes()==ORIGINAL


def test_tls_issue_and_renewal_keep_old_routes_and_check_trusted_tls(tmp_path):
    m,p=fixture_paths(tmp_path);run=Programs();probes=[]
    def probe(tls):probes.append(tls);return {'status':'ok','announcements':1038,'entities':6784,'detail_entities':14110}
    report=m.install(p,authorized=True,expected_sha256=hashlib.sha256(ORIGINAL).hexdigest(),run=run,probe=probe)
    assert report['passed'] and report['renewal_dry_run_passed'] and not report['task3_deployed']
    assert report['new_model_calls']==0 and p.config.read_bytes().startswith(ORIGINAL.replace(b'    server_name _;\n',b'    server_name _;\n'+m.challenge_location(p.webroot).encode()))
    rendered=p.config.read_text()
    assert rendered.count('proxy_pass http://127.0.0.1:8000;')==2
    assert 'listen 443 ssl;' in rendered and 'server_name 203.0.113.10;' in rendered
    assert probes==[False,False,True,True]
    # Inspect the actual argv handed to subprocess, not mocked return values.
    requests=[call for call in run.calls if 'certonly' in call]
    assert len(requests)==2 and '--dry-run' in requests[0] and '--dry-run' not in requests[1]
    assert all('--ip-address' in call and '203.0.113.10' in call and '--webroot' in call for call in requests)
    assert all('--register-unsafely-without-email' in call for call in requests)
    assert any('renew' in call and '--dry-run' in call and '--run-deploy-hooks' in call for call in run.calls)
    assert any(call==['/usr/bin/systemctl','enable','--now','procurement-ip-renew.timer'] for call in run.calls)
    assert (p.units/'procurement-ip-renew.timer').is_file()
    assert (Path(report['backup_directory'])/'nginx.before').read_bytes()==ORIGINAL


@pytest.mark.parametrize('failure', ['issuance','nginx_reload','renewal','timer'])
def test_tls_external_failure_restores_original_site_and_preserves_artifacts(tmp_path,failure):
    m,p=fixture_paths(tmp_path);failed=[False]
    def fail(args):
        hit={'issuance':'certonly' in args and '--dry-run' not in args,
            'nginx_reload':args==['/usr/bin/systemctl','reload','nginx'],
            'renewal':'renew' in args,
            'timer':'enable' in args}[failure]
        if hit and not failed[0]:failed[0]=True;return True
        return False
    run=Programs(fail)
    with pytest.raises(RuntimeError):m.install(p,authorized=True,expected_sha256=hashlib.sha256(ORIGINAL).hexdigest(),run=run,probe=lambda tls:{'status':'ok'})
    assert failed[0] and p.config.read_bytes()==ORIGINAL
    backups=list(p.state.glob('attempt-*/nginx.before'))
    assert len(backups)==1 and backups[0].read_bytes()==ORIGINAL
    assert not (p.units/'procurement-ip-renew.timer').exists()
    assert run.calls[-2:]==[['/usr/sbin/nginx','-t'],['/usr/bin/systemctl','reload','nginx']]


def test_tls_health_change_or_untrusted_certificate_cannot_pass(tmp_path):
    m,p=fixture_paths(tmp_path)
    def probe(tls):
        if tls:raise RuntimeError('certificate_ip_or_chain_invalid')
        return {'status':'ok'}
    with pytest.raises(RuntimeError):m.install(p,authorized=True,expected_sha256=hashlib.sha256(ORIGINAL).hexdigest(),run=Programs(),probe=probe)
    assert p.config.read_bytes()==ORIGINAL


def test_tls_rejects_existing_units_or_linked_paths_without_overwrite(tmp_path):
    m,p=fixture_paths(tmp_path);existing=p.units/'procurement-ip-renew.timer';existing.write_text('unrelated')
    with pytest.raises(ValueError):m.install(p,authorized=True,expected_sha256=hashlib.sha256(ORIGINAL).hexdigest(),run=Programs(),probe=lambda tls:{})
    assert existing.read_text()=='unrelated' and not p.state.exists()


def test_timer_cleanup_failure_does_not_prevent_nginx_recovery(tmp_path):
    m,p=fixture_paths(tmp_path)
    def fail(args):return 'enable' in args or 'disable' in args
    with pytest.raises(RuntimeError,match='recovery'):
        m.install(p,authorized=True,expected_sha256=hashlib.sha256(ORIGINAL).hexdigest(),run=Programs(fail),probe=lambda tls:{'status':'ok'})
    assert p.config.read_bytes()==ORIGINAL


@pytest.mark.parametrize('field,bad', [('euid',1000),('hostname','another-host'),('system','Windows'),('distribution',{'ID':'ubuntu','VERSION_ID':'22.04'})])
def test_wrong_admin_host_cannot_start_install(field,bad):
    m=load('configure_ip_tls')
    m.HOST='synthetic-authorized-host'
    fields={'euid':0,'hostname':'synthetic-authorized-host','system':'Linux','distribution':{'ID':'ubuntu','VERSION_ID':'24.04'}}
    fields[field]=bad
    with pytest.raises(ValueError):m.validate_host(**fields)


def test_changed_old_health_rolls_back_before_certificate_request(tmp_path):
    m,p=fixture_paths(tmp_path);run=Programs();calls=[0]
    def probe(tls):calls[0]+=1;return {'status':'ok','entities':6784 if calls[0]==1 else 6783}
    with pytest.raises(RuntimeError,match='old_health_changed'):
        m.install(p,authorized=True,expected_sha256=hashlib.sha256(ORIGINAL).hexdigest(),run=run,probe=probe)
    assert p.config.read_bytes()==ORIGINAL and not any('certonly' in call for call in run.calls)
