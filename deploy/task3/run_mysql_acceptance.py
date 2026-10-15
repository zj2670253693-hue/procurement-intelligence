"""Explicit isolated SQL probe with streaming official before/after fingerprints.

No model keys enter the test subprocess. This is NOT a full release acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

TABLES={'announcements':'id','extracted_entities':'id','extracted_entity_details':'id',
        'project_relations':'id','bidders':'id','t2_datasets':'dataset_id','t2_active':'slot',
        't2_facts':'dataset_id,record_id','t2_evidence':'dataset_id,evidence_id'}


def isolated(target,official=None):
    if not isinstance(target,str) or len(target)>64 or not re.fullmatch(r'procurement_task3_accept_[A-Za-z0-9_]+',target) or target==official:
        raise ValueError('explicit_isolated_database_required')
    return target


def fingerprints(connection):
    result={};connection.begin()
    try:
        for table,order in TABLES.items():
            digest=hashlib.sha256();count=0
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM '+table+' ORDER BY '+order)
                for row in cursor:
                    digest.update(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode('utf-8')+b'\n')
                    count+=1
            result[table]={'rows':count,'sha256':digest.hexdigest()}
    finally:connection.rollback()  # Official connection never commits writes.
    return result


def acceptance_environment(config,target,root,dependencies):
    isolated(target,config['DB_NAME'])
    values={key:value for key,value in os.environ.items() if key.upper() in
            {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','TMPDIR','LANG','LC_ALL'}}
    values.update(PYTHONPATH=os.pathsep.join((str(dependencies),str(root),str(root/'task1_entity_extraction'))),
        PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHON_DOTENV_DISABLED='1',
        TASK3_MYSQL_TEST_DB=target,TASK3_MYSQL_TEST_WRITE_AUTHORIZED='1',
        TASK3_MYSQL_TEST_USER=config['DB_USER'],TASK3_MYSQL_TEST_PASSWORD=config['DB_PASSWORD'],
        TASK3_MYSQL_TEST_PORT=str(config.get('DB_PORT','3306')),TASK3_PAID_ENABLED='0',PAID_INFERENCE_ENABLED='0',
        DB_HOST='127.0.0.1',DB_PORT=str(config.get('DB_PORT','3306')),DB_USER=config['DB_USER'],
        DB_PASSWORD=config['DB_PASSWORD'],DB_NAME=target,DB_ENABLED='false',
        OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    return values


def safe_path(path):
    path=Path(path)
    if not path.is_absolute() or path==Path(path.anchor):raise ValueError('absolute_probe_path_required')
    for item in (path,*path.parents):
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)():raise ValueError('linked_probe_path_forbidden')
    return path


def run(args):
    if not args.allow_test_writes:raise ValueError('explicit_test_write_authorization_required')
    from dotenv import dotenv_values
    import pymysql
    from pymysql.cursors import SSDictCursor
    config=dotenv_values(safe_path(args.environment_file))
    isolated(args.database,config['DB_NAME'])
    if config.get('DB_HOST')!='127.0.0.1':raise ValueError('loopback_database_required')
    output=safe_path(args.output);output.mkdir(parents=True,exist_ok=False,mode=0o700)
    root=Path(__file__).resolve().parents[2]
    env=acceptance_environment(config,args.database,root,safe_path(args.test_dependencies))
    official=pymysql.connect(host='127.0.0.1',port=int(config.get('DB_PORT',3306)),user=config['DB_USER'],
        password=config['DB_PASSWORD'],database=config['DB_NAME'],cursorclass=SSDictCursor,
        charset='utf8mb4',connect_timeout=10,read_timeout=30)
    try:
        before=fingerprints(official)
        (output/'official-before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
        with (output/'pytest.log').open('w',encoding='utf-8') as log:
            completed=subprocess.run([sys.executable,'-B','-m','pytest','task3_tests/test_mysql_integration.py',
                '-m','mysql_integration','-q','--tb=short','--basetemp',str(output/'pytest-temp'),
                '--junitxml',str(output/'mysql.xml')],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
        after=fingerprints(official)
    finally:official.close()
    suites=list(ET.parse(output/'mysql.xml').getroot().iter('testsuite'))
    counts={key:sum(int(suite.get(key,'0')) for suite in suites) for key in ('tests','failures','errors','skipped')}
    passed=completed.returncode==0 and counts['tests']>=1 and all(counts[key]==0 for key in ('failures','errors','skipped')) and before==after
    report={'kind':'real_mysql_boundary_probe','passed':passed,'target_database':args.database,
        'old_dataset_fingerprint_unchanged':before==after,'official_fingerprints':after,'mysql_cases':counts,
        'new_model_calls':0,'full_release_accepted':False,'tls_verified':False,'cgroup_verified':False}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if passed else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--allow-test-writes',action='store_true')
    parser.add_argument('--environment-file',default='/etc/procurement-app/procurement.env')
    parser.add_argument('--test-dependencies',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:return run(args)
    except Exception:
        print(json.dumps({'passed':False,'code':'mysql_probe_failed','new_model_calls':0,'full_release_accepted':False}))
        return 1


if __name__=='__main__':sys.exit(main())
