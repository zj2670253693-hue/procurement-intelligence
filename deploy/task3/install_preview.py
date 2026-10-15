"""Guard-fail-closed installer for the isolated Task3 preview. Admin-only, explicit.

Installs a SEPARATE preview API/worker and retargets only the HTTPS server block.
`/srv/procurement-app/current`, the official env file, the official worker and the
HTTP display path are never modified, and no database DDL or model call happens here.
Every mutation is preceded by full validation; failure restores the previous Nginx
site and removes only the units/环境文件 this run created.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import traceback

SERVICES='/etc/systemd/system'
UNIT_API='procurement-task3-preview-api.service'
UNIT_WORKER='procurement-task3-preview-worker.service'
API_PORT=18080
MANIFEST_KIND='task3_isolated_preview_release'
SECRET=re.compile(r'(API_KEY|_KEY|TOKEN|SECRET|PASSWORD)')
REQUIRED=('DB_HOST','DB_PORT','DB_USER','DB_PASSWORD','DB_NAME')
FORBIDDEN_ENV='TASK3_MYSQL_TEST_DB'
PYTHON='/srv/procurement-app/shared/venv/bin/python'


class InstallerError(ValueError):
    pass


def fail(code):
    raise InstallerError(code)


def read_env(path):
    values={}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        key,value=line.split('=',1)
        values[key.strip()]=value.strip()
    return values


def no_links(value):
    text=str(value)
    if not text.startswith('/') or text.startswith('//') or '\\' in text:fail('absolute_path_required')
    parts=text.split('/')
    for index in range(2,len(parts)+1):
        item=Path('/'.join(parts[:index]))
        try:
            linked=item.is_symlink() or getattr(item,'is_junction',lambda:False)()
        except OSError:
            break
        if linked:fail('linked_preview_path_forbidden')
    return Path(text)


def posix_path(value,code):
    """Preview paths are absolute POSIX paths even when this code runs on Windows."""
    if not isinstance(value,str) or not value.startswith('/') or value.startswith('//') or '\\' in value:
        fail(code)
    if any(part in {'.','..'} or ':' in part for part in value.split('/')):
        fail(code)
    return value


def require_isolated(database,official):
    if (not isinstance(database,str) or len(database)>64 or database==official
            or not re.fullmatch(r'procurement_task3_accept_[A-Za-z0-9_]+',database)):
        fail('explicit_isolated_acceptance_database_required')
    return database


def require_origin(origin):
    if not isinstance(origin,str) or not re.fullmatch(r'https://[A-Za-z0-9.\-]+(?::\d{1,5})?',origin):
        fail('public_https_origin_required')
    return origin


def posix_text(value):
    """Canonical '/' form. str(WindowsPath) flips separators and would break containment."""
    return value.as_posix() if hasattr(value,'as_posix') else str(value).replace('\\','/')


def require_runtime(runtime,release):
    runtime=posix_path(runtime,'absolute_runtime_root_required')
    release=posix_text(release).rstrip('/')
    if runtime==release or runtime.startswith(release+'/'):
        fail('runtime_inside_release_forbidden')
    return runtime


def authorize(live_database,expected_host,*,hostname=None,apply=False):
    if getattr(os,'geteuid',lambda:1)()!=0:fail('root_required')
    if not apply:fail('explicit_apply_required')
    actual=hostname() if callable(hostname) else hostname
    if not expected_host or actual!=expected_host:fail('unexpected_host')
    return {'root':True,'host':actual,'live_database':live_database}


def render_environment(old_text,*,release,database,runtime_root,public_origin,official_database,
                       proxies='127.0.0.1'):
    require_isolated(database,official_database)
    require_origin(public_origin)
    require_runtime(runtime_root,release)
    source=read_env_values(old_text)
    for key in REQUIRED:
        if not source.get(key):fail('missing_environment_key:'+key)
    if source['DB_NAME']!=official_database:fail('official_database_name_mismatch')
    values={}
    for key,value in source.items():
        if key in REQUIRED or SECRET.search(key) or key.startswith('TASK3_') or key==FORBIDDEN_ENV:continue
        values[key]=value
    values.update(DB_ENABLED='false',PAID_INFERENCE_ENABLED='0',PYTHONUNBUFFERED='1',
        TASK3_ENABLED='1',TASK3_DB_NAME=database,TASK3_RUNTIME_ROOT=str(runtime_root),
        TASK3_PUBLIC_ORIGIN=public_origin,TASK3_TRUSTED_PROXIES=proxies,TASK3_PAID_ENABLED='0')
    values.update({key:source[key] for key in REQUIRED})
    return ''.join(key+'='+values[key]+'\n' for key in sorted(values))


def read_env_values(text):
    values={}
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        key,value=line.split('=',1)
        values[key.strip()]=value.strip()
    return values


def render_api_unit(release,environment_file,*,port=API_PORT,user='zhoujin',group='projectteam'):
    release=posix_text(release).rstrip('/')
    return f'''[Unit]
Description=Procurement Task3 isolated preview API
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={release}/backend
EnvironmentFile={environment_file}
Environment=PYTHONUNBUFFERED=1
ExecStart={PYTHON} -B -m uvicorn api.main:app --host 127.0.0.1 --port {port}
Restart=on-failure
RestartSec=5s
TimeoutStopSec=20s
KillMode=control-group
MemoryAccounting=yes
MemoryMax=768M
MemorySwapMax=0
CPUQuota=100%
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/procurement-app/shared/runtime
UMask=0077

[Install]
WantedBy=multi-user.target
'''


def render_worker_unit(release,environment_file,*,user='zhoujin',group='projectteam'):
    release=posix_text(release).rstrip('/')
    return f'''[Unit]
Description=Procurement Task3 isolated preview worker
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={release}/backend
EnvironmentFile={environment_file}
Environment=PYTHONUNBUFFERED=1
Environment=OMP_NUM_THREADS=1
Environment=OPENBLAS_NUM_THREADS=1
Environment=MKL_NUM_THREADS=1
ExecStart={PYTHON} -B -m task3_platform.runtime
Restart=on-failure
RestartSec=5s
TimeoutStopSec=15s
KillMode=control-group
SendSIGKILL=yes
MemoryAccounting=yes
MemoryMax=1024M
MemorySwapMax=0
CPUQuota=100%
TasksMax=32
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/procurement-app/shared/runtime
UMask=0077

[Install]
WantedBy=multi-user.target
'''


def _blocks(text):
    """Return [(listen_port,start,end_inclusive)] for top-level `server {}` blocks."""
    result=[]
    for match in re.finditer(r'(?m)^server\s*\{',text):
        start=match.end()-1
        end=text.find('\n}\n',start)
        if end<0:fail('unterminated_nginx_server_block')
        body=text[start:end]
        listen=re.search(r'(?m)^\s*listen\s+(?:\[::\]:)?(\d{1,5})\b',body)
        if not listen:fail('nginx_server_block_without_listen')
        result.append((int(listen.group(1)),match.start(),end+3))
    if not result:fail('nginx_server_blocks_required')
    return result


def retarget_https_server(text,release,port):
    if not isinstance(port,int) or isinstance(port,bool) or not 1024<=port<=65535 or port in (8000,80,443):
        fail('preview_loopback_port_required')
    release=posix_path(posix_text(release),'absolute_preview_release_required')
    no_links(release)
    blocks=_blocks(text)
    https=[item for item in blocks if item[0]==443]
    http=[item for item in blocks if item[0]==80]
    if len(https)!=1 or len(http)!=1:fail('exactly_one_http_and_one_https_server_required')
    _,start,end=https[0]
    block=text[start:end]
    root,roots=re.subn(r'(?m)^(\s*)root\s+/srv/procurement-app/current/frontend-dist\s*;',
        lambda m:m.group(1)+'root '+release+'/frontend-dist;',block)
    if roots!=1:fail('unexpected_current_frontend_root')
    if 'localhost' in root:fail('endpoint_rewrite_required')
    if 'X-Forwarded-Proto' not in root:fail('forwarded_proto_required')
    # The app only trusts the client address when the proxy is explicit, and the
    # session route requires a secure transport; without this header a valid cookie
    # over HTTPS is rejected with 403.
    lines=root.split('\n');rewritten=0;output=[];inside=False;served=False;forwarded=False;location=None
    for line in lines:
        stripped=line.strip()
        if stripped.startswith('location'):
            inside=True;served=False;forwarded=False
            location=stripped
        elif stripped=='}':
            if inside and not forwarded:
                output.append('        proxy_set_header X-Forwarded-Proto $scheme;')
            inside=False
        match=re.fullmatch(r'(\s*)proxy_pass\s+http://127\.0\.0\.1:8000(?=[/;\s]|$)(.*)',line)
        if not match:
            if inside and stripped.startswith('proxy_set_header') and 'X-Forwarded-Proto' in stripped:
                forwarded=True
            output.append(line);continue
        if inside and served:fail('duplicate_https_proxy_endpoint')
        if inside:served=True
        rewritten+=1
        tail=match.group(2)
        endpoint='http://127.0.0.1:'+str(port)+('' if tail[:1] in {';','/'} else '/')
        output.append(match.group(1)+'proxy_pass '+endpoint+tail)
    if rewritten!=root.count('proxy_pass http://127.0.0.1:8000') or rewritten<1:fail('unexpected_https_proxy_endpoints')
    updated='\n'.join(output)
    if ':8000' in updated:fail('incomplete_endpoint_rewrite')
    if f'proxy_pass http://127.0.0.1:{port};' not in updated:fail('preview_endpoint_not_applied')
    if 'listen 443 ssl' not in updated or 'ssl_certificate ' not in updated:fail('tls_configuration_must_be_preserved')
    return text[:start]+updated+text[end:]


def release_manifest(release,expected_sha):
    release=no_links(posix_text(release))
    if not release.is_absolute() or not release.is_dir():fail('preview_release_directory_required')
    if not re.fullmatch(r'task3-preview-[A-Za-z0-9._-]+',release.name):fail('preview_release_naming_required')
    manifest=no_links(release/'release-manifest.json')
    if not manifest.is_file():fail('release_manifest_required')
    digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
    if not isinstance(expected_sha,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_sha):fail('expected_release_sha_required')
    if digest!=expected_sha:fail('release_manifest_sha_mismatch')
    document=json.loads(manifest.read_text(encoding='utf-8'))
    if document.get('kind')!=MANIFEST_KIND or document.get('new_model_calls')!=0:fail('isolated_preview_manifest_required')
    for relative in ('backend/api/main.py','frontend-dist/index.html','release-manifest.json'):
        if not (release/PurePosixPath(relative)).is_file():fail('preview_file_missing:'+relative)
    return release,digest,document


def progress(step,detail=''):
    print(json.dumps({'stage':'preview_install_progress','step':step,'detail':detail}))
    sys.stdout.flush()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--release',required=True)
    parser.add_argument('--release-manifest-sha256',required=True)
    parser.add_argument('--environment-file',required=True)
    parser.add_argument('--source-environment-file',required=True)
    parser.add_argument('--database',required=True)
    parser.add_argument('--official-database',required=True)
    parser.add_argument('--runtime-root',required=True)
    parser.add_argument('--public-origin',required=True)
    parser.add_argument('--nginx-site',required=True)
    parser.add_argument('--expected-host',required=True)
    parser.add_argument('--port',type=int,default=API_PORT)
    parser.add_argument('--debug',action='store_true')
    args=parser.parse_args(argv)
    debug=bool(args.debug)
    try:
        authorize(args.official_database,args.expected_host,hostname=os.uname().nodename,apply=args.apply)
        release,digest,_=release_manifest(args.release,args.release_manifest_sha256)
        require_runtime(args.runtime_root,release)
        release=posix_text(release)
        runtime=args.runtime_root
        environment=render_environment(Path(args.source_environment_file).read_text(encoding='utf-8'),
            release=release,database=args.database,runtime_root=runtime,public_origin=args.public_origin,
            official_database=args.official_database)
        site=no_links(args.nginx_site)
        if not site.is_file():fail('nginx_site_required')
        original=site.read_text(encoding='utf-8')
        updated=retarget_https_server(original,release,args.port)
    except Exception as error:
        code=str(error) if isinstance(error,InstallerError) else 'preview_install_validation_failed:'+type(error).__name__
        if debug:traceback.print_exc()
        print(json.dumps({'passed':False,'code':code,'task3_deployed':False,'new_model_calls':0}))
        return 1
    report={'passed':True,'stage':'preview_install','release':str(release),'release_manifest_sha256':digest,
        'database':args.database,'public_origin':args.public_origin,'port':args.port,
        'current_release_changed':False,'official_environment_changed':False,'new_model_calls':0,
        'paid_inference':False,'privileged_cutover_available':False,'task3_deployed':True,'steps':[]}
    environment_file=Path(args.environment_file)
    try:
        progress('validated',str(release))
        if environment_file.exists():
            report['steps'].append('environment_reused');environment_file.read_text(encoding='utf-8')
        else:
            environment_file.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
            environment_file.write_text(environment,encoding='utf-8');os.chmod(environment_file,0o640)
            report['steps'].append('environment_written')
        progress('environment',args.environment_file)
        for name,content in ((UNIT_API,render_api_unit(release,args.environment_file,port=args.port)),
                             (UNIT_WORKER,render_worker_unit(release,args.environment_file))):
            target=Path(SERVICES)/name
            target.write_text(content,encoding='utf-8');os.chmod(target,0o644)
            report['steps'].append('unit_written:'+name)
            progress('unit',name)
        shutil.copyfile(site,str(site)+'.task3-preview-backup')
        progress('backup',str(site)+'.task3-preview-backup')
        site.write_text(updated,encoding='utf-8')
        report['steps'].append('nginx_retargeted')
        progress('nginx',args.nginx_site)
    except Exception as error:
        code=str(error) if isinstance(error,InstallerError) else 'preview_install_write_failed:'+type(error).__name__
        if debug:traceback.print_exc()
        print(json.dumps({'passed':False,'code':code,'steps':report['steps'],
            'task3_deployed':False,'new_model_calls':0}))
        return 1
    print(json.dumps(report))
    return 0


if __name__=='__main__':sys.exit(main())
