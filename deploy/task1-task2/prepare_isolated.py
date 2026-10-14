"""Private isolated acceptance. Refuses current DB and nonempty target.

Never invokes inference/OCR or changes services. Requires an admin-created DB.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TARGET='procurement_kg_v14_20261014'
RELEASE='20261014-task1-v14-task2-v2'
TABLES=('announcements','extracted_entities','extracted_entity_details','project_relations','bidders')
EXPECTED=(1038,6784,14110)


def checked_identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_]+',value): raise ValueError('unsafe SQL identifier')
    return '`'+value+'`'


def reject_live_target(source,target):
    if target!=TARGET or target==source: raise ValueError('refusing live or unexpected target database')


def fingerprints(connection,full=True):
    values={}
    with connection.cursor() as cur:
        for table in TABLES:
            cur.execute('SELECT * FROM '+checked_identifier(table)+' ORDER BY id')
            h=hashlib.sha256();count=0
            for row in cur.fetchall():
                if not full: row={k:v for k,v in row.items() if k not in ('id','created_at','updated_at')}
                h.update(json.dumps(row,ensure_ascii=False,sort_keys=True,default=str,separators=(',',':')).encode('utf-8'));h.update(b'\n');count+=1
            values[table]=dict(rows=count,sha256=h.hexdigest())
    return values


def clone(source,target):
    with target.cursor() as cur:
        cur.execute('SHOW TABLES')
        if cur.fetchall(): raise ValueError('isolated target must be fresh and empty; no overwrite')
    with source.cursor() as src,target.cursor() as dst:
        for table in TABLES:
            src.execute('SHOW CREATE TABLE '+checked_identifier(table));sql=next(v for k,v in src.fetchone().items() if k.startswith('Create'))
            dst.execute(sql)
    target.begin()
    try:
        with source.cursor() as src,target.cursor() as dst:
            for table in TABLES:
                src.execute('SELECT * FROM '+checked_identifier(table)+' ORDER BY id')
                rows=src.fetchall()
                if not rows: continue
                columns=list(rows[0]);sql='INSERT INTO '+checked_identifier(table)+' ('+','.join(checked_identifier(c) for c in columns)+') VALUES ('+','.join(['%s']*len(columns))+')'
                for offset in range(0,len(rows),100): dst.executemany(sql,[tuple(row[c] for c in columns) for row in rows[offset:offset+100]])
        target.commit()
    except BaseException: target.rollback();raise


def file_sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def verify_package(root,attachment_links=None):
    manifest=json.loads((root/'release-manifest.json').read_text(encoding='utf-8'))
    if manifest['release']!=RELEASE or manifest['new_model_calls']!=0: raise ValueError('wrong release')
    attachment_links=attachment_links or {}
    actual=set();allowed_links=set()
    shared=Path('/srv/procurement-app/shared/data/task1-v10-20261009')
    for folder in ('backend','frontend-dist'):
        for path in (root/folder).rglob('*'):
            name=path.relative_to(root).as_posix()
            if path.is_symlink():
                match=re.fullmatch(r'backend/sample_data/(t\d{8}_\d+\.zip)',name)
                if not match or match[1] not in attachment_links or path.resolve()!=(shared/match[1]).resolve() or file_sha(path)!=attachment_links[match[1]]:
                    raise ValueError('unexpected attachment link/catalog')
                allowed_links.add(match[1]);continue
            if path.is_file():actual.add(name)
    if actual!=set(manifest['files']) or allowed_links!=set(attachment_links):
        raise ValueError('release exact file catalog mismatch')
    for name,expected in manifest['files'].items():
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('release file mismatch: '+name)
    return manifest


def import_task1(root,target_config):
    backend=root/'backend';sys.path.insert(0,str(backend))
    from database.connection import DatabaseManager,DatabaseConfig
    from database.dao import DataRepository
    from data_loader.html_parser import HTMLAnnouncementParser
    from extractor.summary_rules import build_summary_source
    from models.result_io import parse_extraction_result
    db=DatabaseManager(DatabaseConfig(**target_config)).connect(create_db=False,verbose=False)
    repo=DataRepository(db);parser=HTMLAnnouncementParser()
    rows=json.loads((backend/'output/raw_results.json').read_text(encoding='utf-8'))
    for row in rows:
        if not re.fullmatch(r't\d{8}_\d+',row['announcement_id']): raise ValueError('unsafe announcement identifier')
    def write_one(raw):
        result=parse_extraction_result(raw)
        result.extra_info={k:v for k,v in result.extra_info.items() if k in ('project_name','procurement_unit','winning_supplier','project_amount','bidders')}
        # If metadata contains notes only, leave existing project/bidders unchanged.
        content=parser.parse_file(str(backend/'sample_data'/(result.announcement_id+'.html')))
        repo.save_extraction_result(result,title=content.title,raw_text=content.html_text,
            file_path='sample_data/'+result.announcement_id+'.html',
            summary_source=build_summary_source(content.html_text,content.html_tables),preserve_detail_source_pages=True)
    start=time.perf_counter()
    try:
        for row in rows: write_one(row)
        first=fingerprints(db._conn,False)
        counts=tuple(first[t]['rows'] for t in TABLES[:3])
        if counts!=EXPECTED: raise ValueError('Task1 imported counts mismatch: '+str(counts))
        for row in rows: write_one(row)
        second=fingerprints(db._conn,False)
        if first!=second: raise ValueError('repeat import changed semantic rows')
        before=fingerprints(db._conn)
        original=repo.details.save_details
        def fail(*args,**kwargs): raise RuntimeError('intentional_isolated_rollback_test')
        repo.details.save_details=fail
        altered=json.loads(json.dumps(rows[0]));altered['entities'][0]['product_name']='ISOLATED_ROLLBACK_TEST_ONLY'
        try: write_one(altered)
        except RuntimeError as e:
            if str(e)!='intentional_isolated_rollback_test': raise
        else: raise ValueError('rollback injection did not execute')
        finally: repo.details.save_details=original
        if fingerprints(db._conn)!=before: raise ValueError('Task1 rollback did not restore actual rows')
        return dict(counts=counts,repeat_identical=True,rollback_test=True,seconds=round(time.perf_counter()-start,3))
    finally: db.close()


def http_accept(root,env):
    log=root/'isolated-api.log'
    with log.open('xb') as stream:
        process=subprocess.Popen([sys.executable,'-B','-m','uvicorn','api.main:app','--host','127.0.0.1','--port','18080','--workers','1','--limit-concurrency','4'],cwd=root/'backend',env=env,stdout=stream,stderr=subprocess.STDOUT)
        try:
            def get(path):
                with urllib.request.urlopen('http://127.0.0.1:18080'+path,timeout=30) as r: return r.read()
            for attempt in range(60):
                if process.poll() is not None: raise ValueError('isolated API exited; inspect private log')
                try: health=json.loads(get('/api/health'));break
                except (OSError,ValueError): time.sleep(.5)
            else: raise ValueError('isolated API did not become healthy')
            if tuple(health[k] for k in ('announcements','entities','detail_entities'))!=EXPECTED: raise ValueError('HTTP Task1 count mismatch')
            status=json.loads(get('/api/task2/status'));version=status['dataset_id']
            selections={'buyer_suppliers':'西安工程大学','buyer_bidders':'济宁学院','supplier_peers':'中国移动通信集团广东有限公司',
                        'common_buyers':['北京万方数据股份有限公司','同方知网数字科技有限公司'],
                        'common_projects':['上海万方数据有限公司','山东世纪读秀信息科技有限公司']}
            def query(kind):
                selected=selections[kind];body=dict(query_type=kind,dataset_id=version,page_size=30)
                body['selectors' if isinstance(selected,list) else 'selector']=selected
                request=urllib.request.Request('http://127.0.0.1:18080/api/task2/query',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                start=time.perf_counter()
                with urllib.request.urlopen(request,timeout=30) as response: data=json.load(response)
                if data['dataset_id']!=version or not data['items'] or data['graph']['dataset_id']!=version: raise ValueError('real five-query positive failed: '+kind)
                ids={n['id'] for n in data['graph']['nodes']}
                if any(e['from'] not in ids or e['to'] not in ids for e in data['graph']['edges']): raise ValueError('dangling graph edge')
                if data['graph']['edges']:
                    eid=data['graph']['edges'][0]['evidence_ids'][0]
                    proof=json.loads(get('/api/task2/evidence/'+eid+'?dataset_id='+version))
                    if not proof['raw_quote'] or proof['dataset_id']!=version: raise ValueError('evidence lookup failed')
                return dict(query_type=kind,rows=len(data['items']),total=data['total'],seconds=round(time.perf_counter()-start,4))
            first=[query(k) for k in selections]
            with ThreadPoolExecutor(max_workers=2) as pool: parallel=list(pool.map(query,list(selections)*2))
            download=get('/api/task1/export/standard')
            expected=json.loads((root/'backend/release_assets/task1-standard-manifest.json').read_text())['sha256']
            if hashlib.sha256(download).hexdigest()!=expected: raise ValueError('download differs from checked workbook')
            body=b'--SyntheticBoundary\r\nContent-Disposition: form-data; name="files"; filename="synthetic.html"\r\nContent-Type: text/html\r\n\r\n<p>synthetic parse only</p>\r\n--SyntheticBoundary--\r\n'
            request=urllib.request.Request('http://127.0.0.1:18080/api/upload',data=body,headers={'Content-Type':'multipart/form-data; boundary=SyntheticBoundary'})
            with urllib.request.urlopen(request,timeout=10) as r:uploaded=json.load(r)
            if uploaded['run_extraction'] is not False: raise ValueError('upload omitted option invoked inference')
            ann=json.loads(get('/api/announcements/t20260206_26154828'))
            if not ann['source_exists'] or not ann['html_text'] or not ann['attachment_files']: raise ValueError('original/attachment listing unavailable')
            memory={}
            for line in Path('/proc/'+str(process.pid)+'/status').read_text().splitlines():
                if line.startswith(('VmRSS:','VmHWM:')): memory[line.split(':')[0]]=line.split(':')[1].strip()
            return dict(health=health,five_queries=first,parallel_queries=parallel,process_memory=memory,
                        download_sha256=expected,original_and_attachment_listing=True,parsing_only_upload=True,storage='real_mysql')
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill();process.wait()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--release-root',type=Path,required=True);args=parser.parse_args()
    root=args.release_root.resolve();manifest=verify_package(root)
    from dotenv import load_dotenv
    load_dotenv('/etc/procurement-app/procurement.env')
    import pymysql
    from pymysql.cursors import DictCursor
    source_name=os.environ['DB_NAME'];reject_live_target(source_name,TARGET)
    if (root/'isolated-acceptance.json').exists(): raise ValueError('acceptance already exists; use readonly verification, no repeat overwrite')
    config=dict(host=os.environ['DB_HOST'],port=int(os.getenv('DB_PORT','3306')),user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD'],charset='utf8mb4',cursorclass=DictCursor,autocommit=True)
    source=pymysql.connect(**config,database=source_name);target=pymysql.connect(**config,database=TARGET)
    try:
        before=fingerprints(source)
        clone(source,target)
        if fingerprints(target)!=before: raise ValueError('cloned source content mismatch')
        # Link only known, already-uploaded originals; never copy/delete shared ZIPs.
        shared=Path('/srv/procurement-app/shared/data/task1-v10-20261009')
        attachment_links={}
        for f in shared.glob('t*.zip'):
            if re.fullmatch(r't\d{8}_\d+\.zip',f.name):
                destination=root/'backend/sample_data'/f.name
                if destination.exists(): raise ValueError('unexpected attachment collision')
                destination.symlink_to(f)
                attachment_links[f.name]=file_sha(f)
        domain_config=dict(host=config['host'],port=config['port'],user=config['user'],password=config['password'],database=TARGET)
        task1=import_task1(root,domain_config)
        sys.path.insert(0,str(root/'backend'))
        from task2_query_service.storage import import_dataset,MySQLRepository
        data_dir=root/'backend/release_assets/task2'/manifest['task2_dataset_id']
        imported=import_dataset(target,data_dir);repeated=import_dataset(target,data_dir)
        repo=MySQLRepository(target,artifact_root=data_dir.parent);version=repo.resolve_version(manifest['task2_dataset_id']);loaded=repo.load(version)
        if len(loaded['facts'])!=22028 or len(loaded['evidence'])!=28221: raise ValueError('Task2 actual imported count mismatch')
        runtime=root.parent/'runtime-preview';runtime.mkdir(mode=0o700)
        env=os.environ.copy();env.update(DB_NAME=TARGET,TASK2_DATASET_ID=version,DEEPSEEK_API_KEY='',QWEN_API_KEY='',PYTHONDONTWRITEBYTECODE='1',
                                        PAID_INFERENCE_ENABLED='0',APP_UPLOAD_ROOT=str(runtime/'uploads'))
        api=http_accept(root,env)
        after=fingerprints(source)
        if before!=after: raise ValueError('live original database changed')
        report=dict(passed=True,release=RELEASE,source_database=source_name,target_database=TARGET,
                    source_fingerprints=before,source_database_unchanged=True,task1=task1,task2=imported,
                    target_semantic_fingerprints=fingerprints(target,False),attachment_links=attachment_links,
                    release_manifest_sha256=file_sha(root/'release-manifest.json'),
                    task2_repeat_status=repeated['status'],api=api,new_model_calls=0,online_cutover_done=False,
                    browser_interactive_qa_done=False,official_accuracy_not_measured=True)
        (root/'isolated-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('source_fingerprints','api')},ensure_ascii=False))
    finally: source.close();target.close()


if __name__=='__main__': main()
