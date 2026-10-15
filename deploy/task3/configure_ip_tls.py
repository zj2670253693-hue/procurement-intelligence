"""Admin-only, pinned-host IP TLS preparation. Does not deploy Task3 or touch DBs.

User must authorize the CA Subscriber Agreement and public IP certificate record.
Preserves HTTP routes; certbot uses webroot without stopping nginx. Independent
public HTTPS verification is still required after this host-local check.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.request
import uuid

# Supplied by the administrator, never commit instance-specific metadata.
IP=''
HOST=''
CERT_NAME='procurement-ip'
TIMER='procurement-ip-renew.timer'
SERVICE='procurement-ip-renew.service'


class Paths:
    def __init__(self,*,config,webroot,state,certbot,cert_config,units):
        for key,value in locals().copy().items():
            if key!='self':setattr(self,key,Path(value))


def safe_path(path):
    if not path.is_absolute():raise ValueError('absolute_path_required')
    for item in (path,*path.parents):
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)():
            raise ValueError('linked_tls_path_forbidden')
        if os.name=='posix' and item.exists():
            info=item.stat()
            if info.st_uid!=0 or info.st_mode & 0o022:
                raise ValueError('root_owned_nonwritable_tls_path_required')


def atomic_write(path,data,mode=0o600):
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    with temporary.open('xb') as stream:
        os.chmod(temporary,mode);stream.write(data);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def challenge_location(webroot):
    return ('    location ^~ /.well-known/acme-challenge/ {\n'
        f'        root {webroot.as_posix()};\n'
        '        default_type text/plain;\n        try_files $uri =404;\n    }\n')


def configurations(original,paths):
    text=original.decode('utf-8')
    if text.count('server {')!=1 or text.count('    server_name _;\n')!=1 or text.count('listen 80 default_server;')!=1 or text.count('listen [::]:80 default_server;')!=1:
        raise ValueError('unexpected_nginx_structure')
    http=text.replace('    server_name _;\n','    server_name _;\n'+challenge_location(paths.webroot))
    cert=paths.cert_config/'live'/CERT_NAME
    tls=text.replace('listen 80 default_server;','listen 443 ssl;').replace('listen [::]:80 default_server;','listen [::]:443 ssl;').replace('    server_name _;\n',
        f'    server_name {IP};\n    ssl_certificate {cert.as_posix()}/fullchain.pem;\n'
        f'    ssl_certificate_key {cert.as_posix()}/privkey.pem;\n'
        '    ssl_protocols TLSv1.2 TLSv1.3;\n    ssl_session_cache shared:ProcurementTLS:1m;\n')
    return http.encode(),(http+'\n'+tls).encode()


def certbot_common(paths):
    return [str(paths.certbot),'--config-dir',str(paths.cert_config),
        '--work-dir',str(paths.state/'work'),'--logs-dir',str(paths.state/'logs'),
        '--non-interactive','--strict-permissions','--cert-name',CERT_NAME]


def prepare_certbot_directory(path):
    # Certbot --strict-permissions checks this TOP directory for exactly 0755.
    # It creates accounts/archive/live as 0700 itself; never chmod recursively.
    path.mkdir(parents=True,exist_ok=True,mode=0o755)
    os.chmod(path,0o755)


def install(paths,*,authorized,expected_sha256,run,probe):
    if not authorized:raise ValueError('ca_agreement_and_ip_publication_authorization_required')
    for value in vars(paths).values():safe_path(value)
    original=paths.config.read_bytes()
    if hashlib.sha256(original).hexdigest()!=expected_sha256:raise ValueError('nginx_configuration_changed')
    http,tls=configurations(original,paths)
    for name in (SERVICE,TIMER):
        if (paths.units/name).exists():raise ValueError('existing_tls_unit_not_overwritten')
    before=probe(False)
    if before.get('status')!='ok':raise ValueError('old_api_unhealthy')
    mode=stat.S_IMODE(paths.config.stat().st_mode)
    paths.state.mkdir(parents=True,exist_ok=True,mode=0o700)
    attempt=paths.state/('attempt-'+uuid.uuid4().hex);attempt.mkdir(mode=0o700)
    atomic_write(attempt/'nginx.before',original,mode)
    paths.webroot.mkdir(parents=True,exist_ok=True,mode=0o755)
    prepare_certbot_directory(paths.cert_config)
    hook=paths.cert_config/'reload-nginx.sh'
    if hook.exists():
        safe_path(hook)
        if hook.read_bytes()!=b'#!/bin/sh\nset -eu\n/usr/sbin/nginx -t\n/usr/bin/systemctl reload nginx\n':
            raise ValueError('existing_reload_hook_not_overwritten')
    atomic_write(hook,b'#!/bin/sh\nset -eu\n/usr/sbin/nginx -t\n/usr/bin/systemctl reload nginx\n',0o700)
    created=[];timer_attempted=False;changed=False
    try:
        if not paths.certbot.exists():
            run(['/usr/bin/python3','-m','venv',str(paths.certbot.parents[1])])
            run([str(paths.certbot.with_name('python')),'-m','pip','--isolated','install',
                '--index-url','https://pypi.org/simple','--only-binary=:all:','certbot==5.8.0'])
        run([str(paths.certbot),'--version'])
        run(['/usr/sbin/nginx','-t'])
        atomic_write(paths.config,http,mode);changed=True
        run(['/usr/sbin/nginx','-t']);run(['/usr/bin/systemctl','reload','nginx'])
        if probe(False)!=before:raise RuntimeError('old_health_changed_during_acme_setup')
        issue=certbot_common(paths)+['certonly','--webroot','--webroot-path',str(paths.webroot),
            '--ip-address',IP,'--preferred-profile','shortlived','--agree-tos','--register-unsafely-without-email','--keep-until-expiring',
            '--deploy-hook',str(hook)]
        run(issue+['--dry-run'])  # Staging first; no untrusted certificate installed.
        run(issue)
        atomic_write(paths.config,tls,mode)
        run(['/usr/sbin/nginx','-t']);run(['/usr/bin/systemctl','reload','nginx'])
        if probe(True)!=before:raise RuntimeError('trusted_tls_health_differs_from_old_api')
        renew=certbot_common(paths)+['renew','--deploy-hook',str(hook)]
        run(renew+['--dry-run','--run-deploy-hooks'])
        if probe(True)!=before:raise RuntimeError('health_changed_after_renewal_test')
        unit=('[Unit]\nDescription=Renew procurement IP certificate\nWants=network-online.target\nAfter=network-online.target nginx.service\n'
            '[Service]\nType=oneshot\nUMask=0077\nTimeoutStartSec=15min\nExecStart='+' '.join(renew)+'\n')
        timer=('[Unit]\nDescription=Check procurement short-lived certificate every six hours\n'
            '[Timer]\nOnCalendar=*-*-* 00,06,12,18:00:00\nRandomizedDelaySec=30min\nPersistent=true\n'
            '[Install]\nWantedBy=timers.target\n')
        for name,content in ((SERVICE,unit),(TIMER,timer)):
            atomic_write(paths.units/name,content.encode(),0o644);created.append(name)
        run(['/usr/bin/systemctl','daemon-reload'])
        timer_attempted=True;run(['/usr/bin/systemctl','enable','--now',TIMER])
        run(['/usr/bin/systemctl','is-active','--quiet',TIMER])
        run(['/usr/bin/systemctl','is-enabled','--quiet',TIMER])
        report={'passed':True,'kind':'host_local_ip_tls_preparation','origin':'https://'+IP,
            'trusted_tls_local_verified':True,'public_ingress_verified':False,
            'renewal_dry_run_passed':True,'renewal_timer_active':True,'backup_directory':str(attempt),
            'health':before,'new_model_calls':0,'task3_deployed':False}
        atomic_write(attempt/'report.json',json.dumps(report,indent=2).encode())
        return report
    except Exception as original_error:
        # Try every recovery step, even if stopping the new timer fails.
        errors=[]
        def recover(action):
            try:action()
            except Exception as error:errors.append(type(error).__name__)
        if timer_attempted:recover(lambda:run(['/usr/bin/systemctl','disable','--now',TIMER]))
        for name in created:recover(lambda name=name:os.replace(paths.units/name,attempt/('failed-'+name)))
        if created:recover(lambda:run(['/usr/bin/systemctl','daemon-reload']))
        if changed:
            recover(lambda:atomic_write(attempt/'nginx.failed',paths.config.read_bytes(),mode))
            recover(lambda:atomic_write(paths.config,original,mode))
            recover(lambda:run(['/usr/sbin/nginx','-t']))
            recover(lambda:run(['/usr/bin/systemctl','reload','nginx']))
        if errors:raise RuntimeError('tls_failed_and_recovery_incomplete:'+','.join(errors)) from original_error
        raise


def verify_health(tls):
    if not tls:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1/api/health',timeout=15) as response:
            return json.loads(response.read(65536))
    context=ssl.create_default_context()
    with socket.create_connection(('127.0.0.1',443),timeout=15) as raw:
        # IP SAN + public CA chain validation, never an insecure curl -k probe.
        with context.wrap_socket(raw,server_hostname=IP) as connection:
            cert=connection.getpeercert()
            if ssl.cert_time_to_seconds(cert['notAfter'])-time.time()<48*3600:
                raise ValueError('certificate_validity_too_short')
            import http.client
            connection.sendall(f'GET /api/health HTTP/1.1\r\nHost: {IP}\r\nConnection: close\r\n\r\n'.encode())
            response=http.client.HTTPResponse(connection);response.begin()
            if response.status!=200:raise ValueError('https_health_failed')
            return json.loads(response.read(65536))


def validate_host(euid,hostname,system,distribution):
    if euid!=0 or hostname!=HOST or system!='Linux' or distribution.get('ID')!='ubuntu' or distribution.get('VERSION_ID')!='24.04':
        raise ValueError('admin_on_authorized_ubuntu_host_required')


def main():
    global IP,HOST
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--accept-ca-agreement-and-public-ip',action='store_true')
    parser.add_argument('--ip-address',required=True)
    parser.add_argument('--expected-host',required=True)
    parser.add_argument('--expected-nginx-sha256',required=True)
    args=parser.parse_args()
    if not args.apply or not args.accept_ca_agreement_and_public_ip:parser.error('both explicit authorization flags are required')
    try:
        import ipaddress
        IP=str(ipaddress.IPv4Address(args.ip_address));HOST=args.expected_host
        if not ipaddress.IPv4Address(IP).is_global:raise ValueError('public_ipv4_required')
        if len(args.expected_nginx_sha256)!=64 or any(c not in '0123456789abcdef' for c in args.expected_nginx_sha256):raise ValueError('explicit_nginx_sha256_required')
        validate_host(getattr(os,'geteuid',lambda:-1)(),socket.gethostname(),platform.system(),platform.freedesktop_os_release())
        paths=Paths(config='/etc/nginx/sites-available/procurement-app',webroot='/var/www/procurement-acme',
            state='/var/lib/procurement-ip-tls',certbot='/opt/procurement-certbot58/bin/certbot',
            cert_config='/etc/letsencrypt-procurement',units='/etc/systemd/system')
        # Close inherited Python/pip/proxy configuration; no application env is read.
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8','PYTHONUTF8':'1'}
        def run(argv):subprocess.run(argv,check=True,env=env,timeout=900)
        report=install(paths,authorized=True,expected_sha256=args.expected_nginx_sha256,run=run,probe=verify_health)
        print(json.dumps(report));return 0
    except Exception as error:
        print(json.dumps({'passed':False,'code':type(error).__name__,'detail':str(error),
            'task3_deployed':False,'new_model_calls':0}));return 1


if __name__=='__main__':sys.exit(main())
