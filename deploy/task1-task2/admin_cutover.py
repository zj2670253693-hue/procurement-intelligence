"""Admin-only guarded release switch, restoring old env/link on failure."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from prepare_isolated import RELEASE,TARGET,EXPECTED,verify_package,fingerprints,file_sha
APP=Path('/srv/procurement-app')
ENV=Path('/etc/procurement-app/procurement.env')


def replace_database(raw,target):
    text=raw.decode('utf-8')
    pattern=r'(?m)^DB_NAME=.*$'
    if len(re.findall(pattern,text))!=1: raise ValueError('expected exactly one DB_NAME setting')
    return re.sub(pattern,'DB_NAME='+target,text).encode('utf-8')


def atomic_bytes(path,data,mode=0o640):
    descriptor,name=tempfile.mkstemp(prefix=path.name+'.pending-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(name,mode);os.chown(name,path.stat().st_uid,path.stat().st_gid)
        os.replace(name,path)
    finally:
        if Path(name).exists(): Path(name).unlink()


def switch_link(path,target):
    pending=path.parent/(path.name+'.pending-task1-task2')
    if pending.exists() or pending.is_symlink(): raise ValueError('pending link exists')
    pending.symlink_to(target,target_is_directory=True);os.replace(pending,path)


def require_readiness(accepted,manifest_sha):
    if not accepted.get('passed') or not accepted.get('browser_interactive_qa_done') or accepted.get('release_manifest_sha256')!=manifest_sha:
        raise ValueError('isolated acceptance, interactive browser QA and same manifest required')


def configure_runtime(root,runtime,owner):
    runtime.mkdir(parents=True,mode=0o750)
    (runtime/'uploads').mkdir(mode=0o750)
    if owner:
        for path in (runtime,runtime/'uploads'):os.chown(path,*owner)


def set_runtime_env(raw,runtime):
    text=raw.decode('utf-8')
    for key,value in (('PAID_INFERENCE_ENABLED','0'),('APP_UPLOAD_ROOT',str(runtime/'uploads'))):
        pattern=r'(?m)^'+key+r'=.*$';matches=re.findall(pattern,text)
        if len(matches)>1:raise ValueError('duplicate runtime setting')
        text=re.sub(pattern,key+'='+value,text) if matches else text.rstrip('\n')+'\n'+key+'='+value+'\n'
    return text.encode('utf-8')


def recover(restore_env,restore_link,restart,check_health):
    restore_env();restore_link();restart();check_health()


def health(expected=EXPECTED):
    for attempt in range(40):
        try:
            with urllib.request.urlopen('http://127.0.0.1/api/health',timeout=5) as r: body=json.load(r)
            if tuple(body[k] for k in ('announcements','entities','detail_entities'))==expected: return body
        except (OSError,ValueError,KeyError): pass
        time.sleep(.5)
    raise ValueError('new live health did not pass')


def main():
    if os.geteuid()!=0: raise PermissionError('run this command as an administrator via sudo')
    import fcntl
    lock=Path('/var/lock/procurement-task1-task2-cutover.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    root=HERE.parent/'release'
    acceptance=json.loads((root/'isolated-acceptance.json').read_text(encoding='utf-8'))
    require_readiness(acceptance,file_sha(root/'release-manifest.json'))
    manifest=verify_package(root,acceptance['attachment_links'])
    if not acceptance['passed'] or acceptance['target_database']!=TARGET or not acceptance['source_database_unchanged']:
        raise ValueError('completed isolated acceptance required')
    old=APP/'current'
    if not old.is_symlink(): raise ValueError('current is not a managed symlink')
    old_target=old.resolve()
    if old_target.parent!=(APP/'releases').resolve(): raise ValueError('current outside releases')
    original=ENV.read_bytes()
    from dotenv import dotenv_values
    config=dotenv_values(ENV)
    if config['DB_NAME']!=acceptance['source_database']: raise ValueError('current database changed since acceptance')
    import pymysql
    from pymysql.cursors import DictCursor
    db=pymysql.connect(host=config['DB_HOST'],port=int(config.get('DB_PORT',3306)),user=config['DB_USER'],password=config['DB_PASSWORD'],database=config['DB_NAME'],cursorclass=DictCursor)
    try:
        if fingerprints(db)!=acceptance['source_fingerprints']: raise ValueError('live old data changed since acceptance')
    finally: db.close()
    sys.path.insert(0,str(root/'backend'))
    from task2_query_service.storage import MySQLRepository
    target_db=pymysql.connect(host=config['DB_HOST'],port=int(config.get('DB_PORT',3306)),user=config['DB_USER'],password=config['DB_PASSWORD'],database=TARGET,cursorclass=DictCursor)
    try:
        if fingerprints(target_db,False)!=acceptance['target_semantic_fingerprints']:raise ValueError('isolated target changed since acceptance')
        MySQLRepository(target_db,artifact_root=root/'backend/release_assets/task2').load(manifest['task2_dataset_id'])
    finally:target_db.close()
    destination=APP/'releases'/RELEASE
    if destination.exists(): raise ValueError('new release destination exists; no overwrite')
    backup=ENV.parent/('procurement.env.before-'+RELEASE)
    if backup.exists(): raise ValueError('environment backup already exists; no overwrite')
    backup.write_bytes(original);os.chmod(backup,0o600)
    destination.mkdir(mode=0o755)
    for folder in ('backend','frontend-dist'):
        shutil.copytree(root/folder,destination/folder,symlinks=True)
    shutil.copyfile(root/'release-manifest.json',destination/'release-manifest.json')
    verify_package(destination,acceptance['attachment_links'])
    for path in destination.rglob('*'):
        if not path.is_symlink(): os.chmod(path,0o755 if path.is_dir() else 0o644)
    import pwd,grp
    service_user=subprocess.check_output(['systemctl','show','procurement-api','--property=User','--value'],text=True).strip()
    service_group=subprocess.check_output(['systemctl','show','procurement-api','--property=Group','--value'],text=True).strip()
    if not service_user or service_user=='root':raise ValueError('unexpected privileged service account')
    account=pwd.getpwnam(service_user);gid=grp.getgrnam(service_group).gr_gid if service_group else account.pw_gid
    runtime=APP/'shared/runtime'/RELEASE
    configure_runtime(destination,runtime,(account.pw_uid,gid))
    switched=False
    try:
        atomic_bytes(ENV,set_runtime_env(replace_database(original,TARGET),runtime))
        switch_link(old,destination);switched=True
        subprocess.run(['nginx','-t'],check=True)
        subprocess.run(['systemctl','restart','procurement-api'],check=True)
        new_health=health()
        with urllib.request.urlopen('http://127.0.0.1/api/task2/status',timeout=10) as r: status=json.load(r)
        if status['dataset_id']!=manifest['task2_dataset_id']: raise ValueError('Task2 version mismatch')
        with urllib.request.urlopen('http://127.0.0.1/api/task1/export/standard',timeout=10) as r: saved=r.read()
        expected=json.loads((destination/'backend/release_assets/task1-standard-manifest.json').read_text())['sha256']
        if hashlib.sha256(saved).hexdigest()!=expected: raise ValueError('live workbook differs')
        for service in ('procurement-api','nginx','mysql'): subprocess.run(['systemctl','is-active','--quiet',service],check=True)
        report=dict(passed=True,rollback=False,current_release=str(destination),health=new_health,
                    task2_dataset_id=status['dataset_id'],old_database_overwritten=False,
                    environment_backup=str(backup),new_model_calls=0)
    except BaseException:
        old_counts=tuple(acceptance['source_fingerprints'][t]['rows'] for t in ('announcements','extracted_entities','extracted_entity_details'))
        recover(lambda:atomic_bytes(ENV,original),lambda:switch_link(old,old_target) if switched else None,
                lambda:subprocess.run(['systemctl','restart','procurement-api'],check=True),lambda:health(old_counts))
        raise
    (HERE/'admin-cutover-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__': main()
