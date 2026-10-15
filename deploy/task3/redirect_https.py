"""Root-only HTTP redirect update after a separately completed HTTPS cutover.

Only the one Nginx site changes. Release, env, units, databases, model policy and
the ACME validation location are preserved. Verify fresh requests with retries:
systemctl reload returning does not prove new Nginx workers are serving yet.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import time
import urllib.request

SITE=Path('/etc/nginx/sites-available/procurement-app')
ENV=Path('/etc/procurement-app/procurement-task3-preview.env')
UNITS=tuple(Path('/etc/systemd/system')/(name+'.service') for name in
    ('procurement-task3-preview-api','procurement-task3-preview-worker'))


def fail(code):raise ValueError(code)


def protected_hashes(paths,digest):
    """JSON plan keys are strings; Path keys are never comparison-compatible."""
    return {str(path):digest(path) for path in paths}


def redirect_http(text,origin):
    if not isinstance(origin,str) or not re.fullmatch(r'https://[A-Za-z0-9.-]+',origin):
        fail('explicit_https_origin_required')
    blocks=list(re.finditer(r'(?ms)^server\s*\{.*?^\}\n?',text))
    http=[m for m in blocks if re.search(r'\blisten\s+80\b',m[0])]
    https=[m for m in blocks if re.search(r'\blisten\s+443\b',m[0])]
    if len(blocks)!=2 or len(http)!=1 or len(https)!=1:fail('expected_http_https_blocks_required')
    match=http[0];old=match[0]
    acme=re.findall(r'(?ms)^\s*location\s+(?:\^~\s+)?/\.well-known/acme-challenge/\s*\{[^{}]*\}',old)
    names=re.findall(r'(?m)^\s*server_name\s+[^;\n]+;',old)
    listens=re.findall(r'(?m)^\s*listen\s+(?:80\b|\[::\]:80\b)[^;]*;',old)
    if len(acme)!=1 or len(names)!=1 or not listens:fail('simple_acme_http_layout_required')
    new=('server {\n'+ '\n'.join('    '+line.strip() for line in listens+names)
         +'\n'+acme[0]+'\n    location / { return 308 '+origin+'$request_uri; }\n}\n')
    return text[:match.start()]+new+text[match.end():]


class RedirectVerificationError(ValueError):
    def __init__(self,diagnostics):
        super().__init__('redirect_verification_exhausted');self.diagnostics=diagnostics


def wait_for_redirect(probe,*,attempts=20,delay=.5,sleep=time.sleep):
    if type(attempts) is not int or not 2<=attempts<=30 or not 0<=delay<=1:
        fail('invalid_redirect_probe_bound')
    diagnostics=[];consecutive=0
    for attempt in range(1,attempts+1):
        try:
            result=probe();consecutive+=1
            diagnostics.append({'attempt':attempt,'passed':True})
            if consecutive==2:return {'attempts':attempt,'consecutive_successes':2,'last_probe':result,'diagnostics':diagnostics}
        except (OSError,ValueError) as error:
            consecutive=0
            # No arbitrary exception message or response body enters a report.
            code=getattr(error,'probe_code',type(error).__name__)
            diagnostics.append({'attempt':attempt,'passed':False,'code':code})
        if attempt<attempts:sleep(delay)
    raise RedirectVerificationError(diagnostics)


def change_and_verify(path,original,candidate,*,writer,runner,probe,unchanged,waiter=wait_for_redirect):
    try:
        writer(path,candidate)
        runner(['nginx','-t']);runner(['systemctl','reload','nginx'])
        checked=waiter(probe)
        unchanged()
        return {'passed':True,'http_configuration_restored':False,'verification':checked}
    except Exception as error:
        restored=True
        for action in (lambda:writer(path,original),lambda:runner(['nginx','-t']),
                       lambda:runner(['systemctl','reload','nginx'])):
            try:action()
            except Exception:restored=False
        try:restored &= path.read_bytes()==original
        except Exception:restored=False
        return {'passed':False,'code':str(error) if isinstance(error,RedirectVerificationError) else type(error).__name__,
            'http_configuration_restored':bool(restored),
            'verification_diagnostics':getattr(error,'diagnostics',[])}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _probe_error(code):
    error=ValueError('redirect_probe_failed');error.probe_code=code;return error


def probe_redirect(origin,version,expected_health):
    plain=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    secure=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    values=[]
    for endpoint in ('/api/health','/api/task2/summary'):
        url='http://'+origin.removeprefix('https://')+endpoint
        try:
            with plain.open(urllib.request.Request(url,headers={'Connection':'close'}),timeout=5) as response:
                status,location=response.status,response.headers.get('Location')
        except urllib.error.HTTPError as error:status,location=error.code,error.headers.get('Location');error.close()
        if status!=308:raise _probe_error('http_status_'+str(status))
        if location!=origin+endpoint:raise _probe_error('redirect_location_mismatch')
        with secure.open(urllib.request.Request(location,headers={'Connection':'close'}),timeout=10) as response:
            if response.url!=location:raise _probe_error('unexpected_https_redirect')
            data=json.loads(response.read(2*1024**2))
            if response.status!=200:raise _probe_error('https_status_'+str(response.status))
        if endpoint.endswith('summary'):
            if data.get('dataset_id')!=version:raise _probe_error('https_dataset_mismatch')
        elif data!=expected_health:raise _probe_error('https_health_mismatch')
        values.append({'endpoint':endpoint,'http_status':308,'https_status':200})
    return values


def preflight(plan,updater):
    from dotenv import dotenv_values
    if socket.gethostname()!=plan['expected_host']:fail('authorized_host_required')
    if updater.digest(SITE)!=plan['site_sha256']:fail('live_site_changed')
    original=SITE.read_bytes();candidate=redirect_http(original.decode(),plan['public_origin']).encode()
    protected=protected_hashes((ENV,*UNITS),updater.digest)
    if protected!=plan['protected_files']:fail('protected_configuration_changed')
    current=Path('/srv/procurement-app/current').resolve(strict=True)
    if str(current)!=plan['official_release']:fail('official_release_changed')
    release=Path(plan['preview_release'])
    updater.release_paths(str(release),str(release)+'-redirect-validation')
    updater.verify_release(release,plan['preview_manifest_sha256'])
    for path in UNITS:
        if path.read_text().splitlines().count('WorkingDirectory='+str(release)+'/backend')!=1:fail('preview_unit_release_mismatch')
    values=dotenv_values(ENV);updater.idle(values)
    policy=Path(values['TASK3_INFERENCE_PROFILE']);policy_sha=updater.digest(policy)
    if policy_sha!=plan['policy_sha256']:fail('paid_policy_changed')
    before=updater.fingerprints(values)
    with urllib.request.urlopen(plan['public_origin']+'/api/health',timeout=10) as response:health=json.load(response)
    if health!=plan['expected_health']:fail('initial_health_changed')
    return {'original':original,'candidate':candidate,'protected':protected,'current':current,
        'release':release,'values':values,'policy':policy,'policy_sha':policy_sha,'before':before,'health':health}


def apply(plan,updater):
    if os.geteuid()!=0:fail('authorized_admin_required')
    checked=preflight(plan,updater)
    original,candidate=checked['original'],checked['candidate']
    protected,current,release=checked['protected'],checked['current'],checked['release']
    values,policy,policy_sha=checked['values'],checked['policy'],checked['policy_sha']
    before,health=checked['before'],checked['health']
    def unchanged():
        if before!=updater.fingerprints(values):fail('official_database_changed')
        if protected_hashes((ENV,*UNITS),updater.digest)!=protected:fail('protected_configuration_changed')
        if updater.digest(policy)!=policy_sha:fail('paid_policy_changed')
        if Path('/srv/procurement-app/current').resolve()!=current:fail('official_release_changed')
    backup=Path('/var/lib/procurement-http-redirects')/('attempt-'+secrets.token_hex(12))
    backup.mkdir(parents=True,mode=0o700);os.chmod(backup,0o700)
    with (backup/'nginx.before').open('xb') as stream:os.fchmod(stream.fileno(),0o600);stream.write(original)
    report=change_and_verify(SITE,original,candidate,writer=updater.atomic,runner=updater.execute,
        probe=lambda:probe_redirect(plan['public_origin'],plan['task2_dataset_id'],health),unchanged=unchanged)
    report.update(kind='http_https_redirect_only',backup_directory=str(backup),preview_release=str(release),
        model_calls_by_updater=0,release_or_unit_switched=False)
    if report['passed']:report.update(official_fingerprints_unchanged=True,protected_configuration_unchanged=True,paid_policy_unchanged=True)
    with (backup/'result.json').open('x') as stream:json.dump(report,stream,sort_keys=True,indent=2)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--check',action='store_true')
    parser.add_argument('--plan',required=True);parser.add_argument('--plan-sha256',required=True)
    args=parser.parse_args()
    try:
        if args.apply==args.check:fail('choose_check_or_apply')
        path=Path(args.plan)
        spec=importlib.util.spec_from_file_location('updater',Path(__file__).with_name('update_preview.py'))
        updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)
        if updater.digest(path)!=args.plan_sha256:fail('redirect_plan_changed')
        plan=json.loads(path.read_bytes())
        if args.apply:report=apply(plan,updater)
        else:
            checked=preflight(plan,updater)
            report={'passed':True,'kind':'redirect_readonly_preflight','protected_configuration_matches':True,
                'site_sha256':updater.digest(SITE),'candidate_sha256':hashlib.sha256(checked['candidate']).hexdigest(),
                'preview_release':str(checked['release']),'applied':False,'model_calls_by_updater':0}
    except Exception as error:
        report={'passed':False,'code':str(error) if type(error) is ValueError else type(error).__name__,'applied':False,'model_calls_by_updater':0}
    print(json.dumps(report));return 0 if report['passed'] else 1


if __name__=='__main__':sys.exit(main())
