"""Guards for the privileged isolated preview installer.

Nothing here starts a service, writes a unit, or touches nginx. Every assertion is
about what the installer refuses, plus the exact text it would install.
"""
from pathlib import Path

import pytest

from task3_tests.test_deployment import load

RELEASE='/srv/procurement-app/releases/task3-preview-20261015-33c505c'
ENV='/etc/procurement-app/procurement-task3-preview.env'
OFFICIAL='procurement_kg_v14_20261014'
TARGET='procurement_task3_accept_20261015'
NGINX='/tmp/nginx-preview-synthetic.conf'

HTTPS_BLOCK='''server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name _;

    ssl_certificate /etc/letsencrypt-procurement/live/procurement-ip/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt-procurement/live/procurement-ip/privkey.pem;

    root /srv/procurement-app/current/frontend-dist;
    index index.html;
    client_max_body_size 512m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /docs {
        proxy_pass http://127.0.0.1:8000/docs;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
'''
HTTP_BLOCK='''server {
    listen 80 default_server;
    server_name _;
    root /srv/procurement-app/current/frontend-dist;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
    }
}
'''


def render_nginx(release=RELEASE,port=18080,text=None):
    from pathlib import Path as _Path
    module=load('install_preview')
    assert module.retarget_https_server(text or HTTPS_BLOCK+HTTP_BLOCK,_Path(release),port)==\
        module.retarget_https_server(text or HTTPS_BLOCK+HTTP_BLOCK,release,port)
    return module.retarget_https_server(text or HTTPS_BLOCK+HTTP_BLOCK,release,port)


def test_installer_refuses_unauthorized_non_root_invocation(monkeypatch):
    module=load('install_preview')
    monkeypatch.setattr(module.os,'geteuid',lambda:1000,raising=False)
    with pytest.raises(ValueError):module.authorize(live_database=OFFICIAL,expected_host='synthetic-host',hostname=lambda:'synthetic-host')
    monkeypatch.setattr(module.os,'geteuid',lambda:0,raising=False)
    with pytest.raises(ValueError):module.authorize(live_database=OFFICIAL,expected_host='other-host',hostname=lambda:'synthetic-host')
    with pytest.raises(ValueError):module.authorize(live_database=OFFICIAL,expected_host='synthetic-host',hostname=lambda:'synthetic-host',apply=False)
    assert module.authorize(live_database=OFFICIAL,expected_host='synthetic-host',hostname=lambda:'synthetic-host',apply=True)['root'] is True


def test_installer_refuses_official_database_as_task3_target():
    module=load('install_preview')
    assert module.require_isolated(TARGET,OFFICIAL)==TARGET
    for value in (OFFICIAL,'procurement_task3_live','',None,'procurement_task3_accept_'+'x'*60):
        with pytest.raises(ValueError):module.require_isolated(value,OFFICIAL)


def test_preview_environment_keeps_credentials_strips_model_keys_and_forces_isolated_paths():
    module=load('install_preview')
    old='\n'.join(('PYTHONUNBUFFERED=1','PROCESS_MAX_WORKERS=2','DB_HOST=127.0.0.1','DB_PORT=3306',
        'DB_USER=procurement_app','DB_PASSWORD=synthetic-private-password','DB_NAME='+OFFICIAL,
        'TASK2_DATASET_ID=t2_synthetic','LLM_PROVIDER=deepseek','DEEPSEEK_API_KEY=synthetic-private-key',
        'PAID_INFERENCE_ENABLED=1','TASK3_ENABLED=0','TASK3_DB_NAME='+OFFICIAL,'TASK3_MYSQL_TEST_DB=other'))+'\n'
    text=module.render_environment(old,release=RELEASE,database='procurement_task3_accept_20261015',
        runtime_root='/srv/procurement-app/shared/runtime/task3-preview',public_origin='https://47.237.97.246',
        official_database=OFFICIAL)
    values=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
    assert 'synthetic-private-key' not in text and not any('API_KEY' in key for key in values)
    assert values['DB_PASSWORD']=='synthetic-private-password' and values['DB_USER']=='procurement_app'
    assert values['DB_NAME']==OFFICIAL and values['TASK2_DATASET_ID']=='t2_synthetic'
    assert values['TASK3_ENABLED']=='1' and values['TASK3_DB_NAME']=='procurement_task3_accept_20261015'
    assert values['TASK3_RUNTIME_ROOT']=='/srv/procurement-app/shared/runtime/task3-preview'
    assert values['TASK3_PUBLIC_ORIGIN']=='https://47.237.97.246' and values['TASK3_TRUSTED_PROXIES']=='127.0.0.1'
    assert values['TASK3_PAID_ENABLED']=='0' and values['PAID_INFERENCE_ENABLED']=='0' and values['DB_ENABLED']=='false'
    assert not any(key.startswith('TASK3_MYSQL_TEST_') for key in values)
    for required in ('DB_USER','DB_PASSWORD','DB_NAME'):
        with pytest.raises(ValueError):module.render_environment(old.replace(required+'=','MISSING_'+required+'='),
            release=RELEASE,database=TARGET,runtime_root='/srv/procurement-app/shared/runtime/task3-preview',
            public_origin='https://47.237.97.246',official_database=OFFICIAL)
    with pytest.raises(ValueError):module.render_environment(old,release=RELEASE,database=OFFICIAL,
        runtime_root='/srv/procurement-app/shared/runtime/task3-preview',public_origin='https://47.237.97.246',official_database=OFFICIAL)
    with pytest.raises(ValueError):module.render_environment(old,release=RELEASE,database=TARGET,
        runtime_root=RELEASE+'/runtime',public_origin='https://47.237.97.246',official_database=OFFICIAL)
    with pytest.raises(ValueError):module.render_environment(old,release=RELEASE,database=TARGET,
        runtime_root='/srv/procurement-app/shared/runtime/task3-preview',public_origin='http://47.237.97.246',official_database=OFFICIAL)


def test_prepared_release_is_traversable_by_the_web_server():
    """A 750 release root made nginx return 500 on static assets while the API worked."""
    module=load('prepare_preview')
    source=(Path(__file__).resolve().parents[1]/'deploy/task3/prepare_preview.py').read_text(encoding='utf-8')
    assert 'target.mkdir(mode=0o755)' in source
    assert 'os.walk(target)' in source and 'os.chmod(directory,0o755)' in source
    assert '0o750' not in source.replace('0o750)','')
    assert hasattr(module,'prepare_preview')


def test_preview_units_never_point_at_production_paths_and_stay_bounded():
    module=load('install_preview')
    from pathlib import Path as _Path
    api=module.render_api_unit(_Path(RELEASE),ENV);worker=module.render_worker_unit(_Path(RELEASE),ENV)
    for text in (api,worker):
        assert RELEASE in text and ENV in text
        assert '/srv/procurement-app/current' not in text
        assert '/etc/procurement-app/procurement.env' not in text
        assert 'migrate' not in text and 'initialize' not in text
    assert 'MemoryMax=768M' in api and '--port 18080' in api
    assert 'task3_platform.runtime' in worker and 'MemoryMax=1024M' in worker
    assert 'KillMode=control-group' in worker and 'MemorySwapMax=0' in worker
    assert 'Restart=on-failure' in worker


def test_nginx_retarget_moves_only_the_https_server_to_preview_and_keeps_http_untouched():
    module=load('install_preview')
    result=module.retarget_https_server(HTTPS_BLOCK+HTTP_BLOCK,RELEASE,18080)
    https,http=result.split('\n}\n',1)
    assert 'root '+RELEASE+'/frontend-dist;' in https
    assert 'proxy_pass http://127.0.0.1:18080;' in https
    assert ':8000' not in https
    assert 'localhost' not in https
    assert 'ssl_certificate ' in https and 'listen 443 ssl;' in https
    assert http==HTTP_BLOCK
    assert 'proxy_pass http://127.0.0.1:8000;' in http
    assert 'root /srv/procurement-app/current/frontend-dist;' in http


def test_runtime_root_accepts_real_path_and_rejects_release_interior():
    """The server passes release as a pathlib Path; every branch must still work."""
    module=load('install_preview')
    from pathlib import Path as _Path
    release=_Path(RELEASE)
    assert module.require_runtime('/srv/procurement-app/shared/runtime/task3-preview',release)=='/srv/procurement-app/shared/runtime/task3-preview'
    assert module.require_runtime('/srv/procurement-app/other/runtime',_Path('/srv/procurement-app/releases'))=='/srv/procurement-app/other/runtime'
    for bad in (RELEASE,RELEASE+'/runtime',RELEASE+'/',RELEASE+'/nested/deep',str(release),RELEASE+'/../runtime',RELEASE+'/./runtime'):
        with pytest.raises(ValueError):module.require_runtime(bad,release)
        with pytest.raises(ValueError):module.require_runtime(bad,str(release))


def test_nginx_retarget_preserves_upstream_paths_and_separators():
    """The live site routes /docs and /openapi.json too; separators must survive."""
    module=load('install_preview')
    marker='        proxy_pass http://127.0.0.1:8000/docs;'
    assert marker in HTTPS_BLOCK
    live=HTTPS_BLOCK.replace(marker,marker+'\n    }\n\n    location = /openapi.json {\n        proxy_pass http://127.0.0.1:8000/openapi.json;')
    assert live.split('listen 80 default_server;')[0].count(':8000')==3, live
    assert live.count(':8000')==3
    result=module.retarget_https_server(live+HTTP_BLOCK,RELEASE,18080)
    assert 'proxy_pass http://127.0.0.1:18080;' in result
    assert 'proxy_pass http://127.0.0.1:18080/docs;' in result
    assert 'proxy_pass http://127.0.0.1:18080/openapi.json;' in result
    head=result[:result.index('listen 80 default_server;')]
    for broken in (';;',';/',':18080;docs','18080//','8000;'):
        assert broken not in head, broken
    assert result.count(':18080')==3
    assert ':8000' not in result[result.index('listen 443 ssl;'):result.index('listen 80 default_server;')]


def test_nginx_retarget_fails_closed_on_unexpected_topology():
    module=load('install_preview')
    with pytest.raises(ValueError):module.retarget_https_server(HTTP_BLOCK,RELEASE,18080)
    with pytest.raises(ValueError):module.retarget_https_server(HTTPS_BLOCK.replace('listen 443 ssl;','listen 8443 ssl;')+HTTP_BLOCK,RELEASE,18080)
    with pytest.raises(ValueError):module.retarget_https_server(HTTPS_BLOCK+HTTP_BLOCK,RELEASE,8000)
    with pytest.raises(ValueError):module.retarget_https_server(HTTPS_BLOCK+HTTP_BLOCK,RELEASE,80)
    with pytest.raises(ValueError):module.retarget_https_server(
        HTTP_BLOCK.replace('listen 80 default_server;','listen 443 ssl;')+HTTP_BLOCK,RELEASE,18080)
    doubled=HTTPS_BLOCK.split('        proxy_pass http://127.0.0.1:8000;\n',1)
    with pytest.raises(ValueError):module.retarget_https_server(
        doubled[0]+'        proxy_pass http://127.0.0.1:8000;\n        proxy_pass http://127.0.0.1:8000;\n'+doubled[1]+HTTP_BLOCK,RELEASE,18080)
