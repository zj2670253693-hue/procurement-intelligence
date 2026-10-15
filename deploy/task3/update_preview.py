"""Explicit root-only update of an EXISTING isolated HTTPS preview, with rollback.

Does not touch official current/env/DB, provision users, call a model, or change
paid authorization. Requires hash-pinned units, environment, site and releases.
The only environment addition is the physical read-only official source root.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import sys
import time
import urllib.request

ENV=Path('/etc/procurement-app/procurement-task3-preview.env')
SITE=Path('/etc/nginx/sites-available/procurement-app')
UNITS=tuple(Path('/etc/systemd/system')/(name+'.service') for name in
    ('procurement-task3-preview-api','procurement-task3-preview-worker'))
TABLES={'announcements':'id','extracted_entities':'id','extracted_entity_details':'id',
    'project_relations':'id','bidders':'id','t2_datasets':'dataset_id','t2_active':'slot',
    't2_facts':'dataset_id,record_id','t2_evidence':'dataset_id,evidence_id'}


def fail(code):raise ValueError(code)


def plain(path):
    path=Path(path)
    for item in (path,*path.parents):
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)():fail('linked_update_path')
    return path


def digest(path):
    result=hashlib.sha256()
    with plain(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):result.update(block)
    return result.hexdigest()


def release_paths(old,new):
    for value in (old,new):
        if not isinstance(value,str) or not re.fullmatch(r'/srv/procurement-app/releases/task3-preview-[A-Za-z0-9._-]+',value):fail('explicit_preview_releases_required')
        plain(value)
    if old==new:fail('different_preview_release_required')
    return Path(old),Path(new)


def verify_release(root,expected,*,audited_old_overrides=None):
    root=plain(root)
    if not re.fullmatch(r'[a-f0-9]{64}',expected or '') or digest(root/'release-manifest.json')!=expected:fail('release_manifest_changed')
    value=json.loads((root/'release-manifest.json').read_bytes())
    if value.get('kind')!='task3_isolated_preview_release' or not isinstance(value.get('files'),dict):fail('preview_manifest_required')
    # One observed pre-existing operator draft, independently matched to its
    # retained source file. Only the OLD release may use a plan-pinned exception;
    # never reseal its original manifest or accept arbitrary drift/new-code changes.
    overrides=audited_old_overrides if audited_old_overrides is not None else {}
    if (not isinstance(overrides,dict) or set(overrides)-{'backend/task3_platform/ledger.py'}
            or not set(overrides).issubset(value['files'])
            or any(not isinstance(sha,str) or not re.fullmatch(r'[a-f0-9]{64}',sha) for sha in overrides.values())):
        fail('unapproved_old_release_override')
    for name,sha in value['files'].items():
        path=PurePosixPath(name)
        if path.is_absolute() or '\\' in name or '..' in path.parts or path.as_posix()!=name:fail('unsafe_release_member')
        if digest(root/path)!=overrides.get(name,sha):fail('release_file_changed')
    return value


def update_unit(text,old,new):
    expected='WorkingDirectory='+old+'/backend'
    if text.splitlines().count(expected)!=1:fail('unexpected_unit_working_directory')
    return text.replace(expected+'\n','WorkingDirectory='+new+'/backend\n')


def update_environment(text,root):
    if not isinstance(root,str) or not re.fullmatch(r'/srv/procurement-app/shared/data/[A-Za-z0-9._-]+',root):fail('explicit_physical_sources_required')
    if not text.endswith('\n'):fail('canonical_environment_required')
    key='TASK3_OFFICIAL_SOURCE_ROOT='
    lines=[line for line in text.splitlines() if line.startswith(key)]
    if lines:
        if lines!=[key+root]:fail('unexpected_existing_source_root')
        return text
    return text+key+root+'\n'


def retarget(text,old,new):
    blocks=list(re.finditer(r'(?ms)^server\s*\{.*?^\}\n?',text))
    https=[m for m in blocks if re.search(r'\blisten\s+443\b',m[0])]
    http=[m for m in blocks if re.search(r'\blisten\s+80\b',m[0])]
    if len(https)!=1 or len(http)!=1 or len(blocks)!=2:fail('expected_http_https_blocks_required')
    match=https[0];block=match[0]
    expected='root '+old+'/frontend-dist;'
    if (block.count(expected)!=1 or 'ssl_certificate ' not in block
            or 'proxy_pass http://127.0.0.1:18080;' not in block):fail('unexpected_https_preview_root')
    return text[:match.start()]+block.replace(expected,'root '+new+'/frontend-dist;')+text[match.end():]


def restore(snapshots,writer,runner):
    restored=True
    for path,data in snapshots.items():
        try:writer(path,data)
        except Exception:restored=False
    for command in (['systemctl','daemon-reload'],['nginx','-t'],
        ['systemctl','restart','procurement-task3-preview-api'],
        ['systemctl','restart','procurement-task3-preview-worker'],['systemctl','reload','nginx'],
        ['systemctl','is-active','--quiet','procurement-task3-preview-api','procurement-task3-preview-worker','nginx']):
        try:runner(command)
        except Exception:restored=False
    return restored


def execute(command):
    return subprocess.run(command,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45).stdout


def atomic(path,data):
    path=plain(path);metadata=path.stat();temporary=path.with_name(path.name+'.update-'+secrets.token_hex(6))
    with temporary.open('xb') as stream:
        os.fchmod(stream.fileno(),metadata.st_mode&0o777)
        os.fchown(stream.fileno(),metadata.st_uid,metadata.st_gid)
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def connect(values,database,*,streaming=False):
    import pymysql
    from pymysql.cursors import DictCursor,SSDictCursor
    if values.get('DB_HOST')!='127.0.0.1':fail('loopback_database_required')
    return pymysql.connect(host='127.0.0.1',port=int(values.get('DB_PORT','3306')),
        user=values['DB_USER'],password=values['DB_PASSWORD'],database=database,
        cursorclass=SSDictCursor if streaming else DictCursor,charset='utf8mb4',connect_timeout=10,read_timeout=30)


def fingerprints(values):
    connection=connect(values,values['DB_NAME'],streaming=True);result={}
    try:
        connection.begin()
        for table,order in TABLES.items():
            sha=hashlib.sha256();count=0
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM '+table+' ORDER BY '+order)
                while True:
                    rows=cursor.fetchmany(512)
                    if not rows:break
                    for row in rows:
                        sha.update(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()+b'\n');count+=1
            result[table]={'rows':count,'sha256':sha.hexdigest()}
        connection.rollback()
    finally:connection.close()
    return result


def idle(values):
    connection=connect(values,values['TASK3_DB_NAME'])
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM t3_jobs WHERE status IN ('queued','running','validating')")
            if cursor.fetchone()['n']:fail('active_work_refuses_update')
            cursor.execute("SELECT COUNT(*) AS n FROM t3_inference_attempts WHERE state IN ('sent','uncertain')")
            if cursor.fetchone()['n']:fail('uncertain_request_refuses_update')
    finally:connection.close()


def apply(plan):
    from dotenv import dotenv_values
    if getattr(os,'geteuid',lambda:1)()!=0:fail('root_required')
    if execute(['hostname']).decode().strip()!=plan['expected_host']:fail('unexpected_host')
    old,new=release_paths(plan['old_release'],plan['new_release'])
    manifest=verify_release(new,plan['new_manifest_sha256'])
    verify_release(old,plan['old_manifest_sha256'],audited_old_overrides=plan.get('old_file_overrides'))
    expected_current=Path('/srv/procurement-app/current').resolve(strict=True)
    if str(expected_current)!=plan['official_release']:fail('official_current_changed')
    snapshots={path:plain(path).read_bytes() for path in (*UNITS,ENV,SITE)}
    for path,data in snapshots.items():
        if hashlib.sha256(data).hexdigest()!=plan['expected_files'].get(str(path)):fail('live_configuration_changed')
    values=dotenv_values(ENV)
    if not re.fullmatch(r'procurement_task3_accept_[A-Za-z0-9_]+',values.get('TASK3_DB_NAME','')) or values['TASK3_DB_NAME']==values['DB_NAME']:fail('isolated_database_required')
    if values.get('TASK3_PUBLIC_ORIGIN')!=plan['public_origin'] or not re.fullmatch(r'https://[A-Za-z0-9.-]+',plan['public_origin']):fail('expected_https_origin_required')
    root=plain(plan['source_root'])
    updated={path:update_unit(snapshots[path].decode(),str(old),str(new)).encode() for path in UNITS}
    updated[ENV]=update_environment(snapshots[ENV].decode(),str(root)).encode()
    updated[SITE]=retarget(snapshots[SITE].decode(),str(old),str(new)).encode()
    if not root.is_dir():fail('physical_source_root_missing')
    for relative,sha in manifest['files'].items():
        if relative.startswith('backend/release_assets/task2-sources/') and relative.endswith('.html'):
            if digest(root/Path(relative).name)!=sha:fail('shared_original_html_changed')
    idle(values)
    before=fingerprints(values)
    backup=Path('/var/lib/procurement-task3-updates')/('attempt-'+secrets.token_hex(12))
    backup.mkdir(parents=True,mode=0o700)
    os.chmod(backup,0o700)
    for index,(path,data) in enumerate(snapshots.items()):
        target=backup/(str(index)+'-'+path.name)
        with target.open('xb') as stream:os.fchmod(stream.fileno(),0o600);stream.write(data)
    (backup/'official-before.json').write_text(json.dumps(before))
    try:
        execute(['systemctl','stop','procurement-task3-preview-worker'])
        idle(values)
        execute(['systemctl','stop','procurement-task3-preview-api'])
        for path,data in updated.items():atomic(path,data)
        execute(['systemctl','daemon-reload']);execute(['nginx','-t'])
        execute(['systemctl','start','procurement-task3-preview-api'])
        health=None
        for _ in range(20):
            try:
                with urllib.request.urlopen('http://127.0.0.1:18080/api/health',timeout=3) as response:health=json.load(response)
                break
            except Exception:time.sleep(.5)
        expected=manifest['expected_official_counts']
        if not health or health.get('status')!='ok' or any(health.get(key)!=expected[key] for key in ('announcements','entities','detail_entities')):fail('new_api_health_failed')
        execute(['systemctl','reload','nginx'])
        with urllib.request.urlopen(plan['public_origin']+'/api/health',timeout=10) as response:
            if json.load(response)!=health:fail('public_trusted_tls_health_failed')
        after=fingerprints(values)
        if before!=after or Path('/srv/procurement-app/current').resolve()!=expected_current:fail('official_baseline_changed')
        execute(['systemctl','start','procurement-task3-preview-worker'])
        execute(['systemctl','is-active','--quiet','procurement-task3-preview-api','procurement-task3-preview-worker','nginx','mysql'])
        report={'passed':True,'rollback':False,'kind':'isolated_preview_update','release':str(new),
            'official_fingerprints_unchanged':True,'official_current_unchanged':True,'paid_policy_unchanged':True,
            'audited_old_file_hashes':plan.get('old_file_overrides',{}),
            'public_trusted_tls_health':health,'backup_directory':str(backup),'model_calls_by_updater':0}
    except Exception as error:
        rollback=restore(snapshots,atomic,execute)
        report={'passed':False,'rollback':rollback,'code':str(error) if isinstance(error,ValueError) else type(error).__name__,
            'backup_directory':str(backup),'model_calls_by_updater':0}
    (backup/'result.json').write_text(json.dumps(report))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--plan',required=True);parser.add_argument('--plan-sha256',required=True)
    args=parser.parse_args()
    try:
        if not args.apply:fail('explicit_apply_required')
        if digest(args.plan)!=args.plan_sha256:fail('update_plan_changed')
        plan=json.loads(plain(args.plan).read_bytes())
        report=apply(plan)
    except Exception as error:
        report={'passed':False,'code':str(error) if isinstance(error,ValueError) else type(error).__name__,'applied':False,'model_calls_by_updater':0}
    print(json.dumps(report));return 0 if report.get('passed') else 1


if __name__=='__main__':sys.exit(main())
