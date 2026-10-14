"""Independent MySQL tables; imports publish atomically, never replace Task1."""
import hashlib
import json
import re
import threading
from collections import OrderedDict
from .artifacts import load_dataset
from pathlib import Path
from task2_relationship_analysis.inputs import safe_path

DDL=(
    """CREATE TABLE IF NOT EXISTS t2_datasets (
      dataset_id VARCHAR(80) CHARACTER SET ascii PRIMARY KEY,
      status VARCHAR(16) NOT NULL, manifest_sha CHAR(64) NOT NULL,
      manifest_json LONGTEXT NOT NULL, coverage_json LONGTEXT NOT NULL,
      published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS t2_active (
      slot TINYINT PRIMARY KEY, dataset_id VARCHAR(80) CHARACTER SET ascii NOT NULL,
      FOREIGN KEY (dataset_id) REFERENCES t2_datasets(dataset_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS t2_facts (
      dataset_id VARCHAR(80) CHARACTER SET ascii NOT NULL,
      record_id VARCHAR(100) CHARACTER SET ascii NOT NULL,
      kind VARCHAR(32) NOT NULL, validation_status VARCHAR(16) NOT NULL,
      eligibility VARCHAR(32) NOT NULL, payload LONGTEXT NOT NULL, payload_sha CHAR(64) NOT NULL,
      PRIMARY KEY (dataset_id,record_id), INDEX t2_fact_kind (dataset_id,kind,validation_status),
      FOREIGN KEY (dataset_id) REFERENCES t2_datasets(dataset_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS t2_evidence (
      dataset_id VARCHAR(80) CHARACTER SET ascii NOT NULL,
      evidence_id VARCHAR(100) CHARACTER SET ascii NOT NULL,
      source_id VARCHAR(100) CHARACTER SET ascii NOT NULL,
      payload LONGTEXT NOT NULL, payload_sha CHAR(64) NOT NULL,
      PRIMARY KEY (dataset_id,evidence_id), INDEX t2_evidence_source (dataset_id,source_id),
      FOREIGN KEY (dataset_id) REFERENCES t2_datasets(dataset_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def digest(value): return hashlib.sha256(value.encode('utf-8')).hexdigest()


def verify_records(records,manifest,filename,key):
    ids=[r[key] for r in records]
    if len(records)!=manifest['counts'][filename] or len(ids)!=len(set(ids)):
        raise ValueError('stored record count/duplicate mismatch')
    if any(r['dataset_id']!=manifest['dataset_id'] for r in records): raise ValueError('stored cross-version record')
    raw=''.join(encoded(r)+'\n' for r in sorted(records,key=lambda r:r[key]))
    if digest(raw)!=manifest['output_hashes'][filename]: raise ValueError('stored canonical hash mismatch: '+filename)


def summary(coverage):
    return {k:v for k,v in coverage.items() if k.endswith('_counts') or k in ('announcement_status_count','new_model_calls')}


def initialize(connection):
    with connection.cursor() as cur:
        for sql in DDL: cur.execute(sql)
    connection.commit()


def import_dataset(connection, directory):
    data=load_dataset(directory)
    version=data['dataset_id'];manifest=encoded(data['manifest']);msha=digest(manifest)
    initialize(connection)
    connection.begin()
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT status,manifest_sha FROM t2_datasets WHERE dataset_id=%s FOR UPDATE',(version,))
            old=cur.fetchone()
            if old:
                if old['status']!='completed' or old['manifest_sha']!=msha:
                    raise ValueError('existing immutable version differs; no overwrite allowed')
                MySQLRepository(connection,artifact_root=Path(directory).parent).load(version)
                connection.commit()
                return dict(dataset_id=version,status='reused',task1_written=False)
            cur.execute('INSERT INTO t2_datasets (dataset_id,status,manifest_sha,manifest_json,coverage_json) VALUES (%s,%s,%s,%s,%s)',
                        (version,'pending',msha,manifest,data['coverage_text']))
            for offset in range(0,len(data['facts']),200):
                rows=[]
                for f in data['facts'][offset:offset+200]:
                    payload=encoded(f)
                    rows.append((version,f['record_id'],f['kind'],f['validation_status'],f['aggregation_eligibility'],payload,digest(payload)))
                cur.executemany('INSERT INTO t2_facts (dataset_id,record_id,kind,validation_status,eligibility,payload,payload_sha) VALUES (%s,%s,%s,%s,%s,%s,%s)',rows)
            for offset in range(0,len(data['evidence']),200):
                rows=[]
                for e in data['evidence'][offset:offset+200]:
                    payload=encoded(e);rows.append((version,e['evidence_id'],e['source_id'],payload,digest(payload)))
                cur.executemany('INSERT INTO t2_evidence (dataset_id,evidence_id,source_id,payload,payload_sha) VALUES (%s,%s,%s,%s,%s)',rows)
            for table,expected in (('t2_facts',len(data['facts'])),('t2_evidence',len(data['evidence']))):
                cur.execute('SELECT COUNT(*) AS n FROM '+table+' WHERE dataset_id=%s',(version,))
                if cur.fetchone()['n']!=expected: raise ValueError('imported count mismatch')
            cur.execute('UPDATE t2_datasets SET status=%s WHERE dataset_id=%s',('completed',version))
        connection.commit()
    except BaseException:
        connection.rollback();raise
    return dict(dataset_id=version,status='completed',facts=len(data['facts']),evidence=len(data['evidence']),
                task1_written=False,new_model_calls=0)


def activate(connection,version,expected_active=None):
    """Explicit compare-and-swap, not an automatic importer side effect."""
    MySQLRepository(connection).load(version)  # Content/coverage integrity before publication.
    connection.begin()
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT status FROM t2_datasets WHERE dataset_id=%s FOR UPDATE',(version,))
            row=cur.fetchone()
            if not row or row['status']!='completed': raise ValueError('only completed version may activate')
            cur.execute('SELECT dataset_id FROM t2_active WHERE slot=1 FOR UPDATE')
            old=cur.fetchone();current=old['dataset_id'] if old else None
            if current!=expected_active: raise ValueError('active version changed; refusing overwrite')
            if old: cur.execute('UPDATE t2_active SET dataset_id=%s WHERE slot=1',(version,))
            else: cur.execute('INSERT INTO t2_active (slot,dataset_id) VALUES (1,%s)',(version,))
        connection.commit()
    except BaseException:
        connection.rollback();raise


_cache=OrderedDict();_cache_lock=threading.Lock()


class MySQLRepository:
    def __init__(self,connection,cache_namespace=None,artifact_root=None):
        self.connection=connection;self.cache_namespace=cache_namespace
        self.artifact_root=Path(artifact_root) if artifact_root else None

    def resolve_version(self,requested=None):
        if requested is not None and not re.fullmatch(r'ds_[A-Za-z0-9_]{1,76}',requested):
            raise ValueError('invalid dataset version')
        with self.connection.cursor() as cur:
            if requested:
                cur.execute('SELECT dataset_id,status FROM t2_datasets WHERE dataset_id=%s',(requested,))
            else:
                cur.execute('SELECT d.dataset_id,d.status FROM t2_active a JOIN t2_datasets d ON d.dataset_id=a.dataset_id WHERE a.slot=1')
            row=cur.fetchone()
        if not row or row['status']!='completed': raise KeyError('completed version not found')
        return row['dataset_id']

    def metadata(self,version):
        with self.connection.cursor() as cur:
            cur.execute('SELECT status,manifest_sha,manifest_json,coverage_json FROM t2_datasets WHERE dataset_id=%s',(version,))
            row=cur.fetchone()
        if not row or row['status']!='completed': raise KeyError('version not found')
        if digest(row['manifest_json'])!=row['manifest_sha']: raise ValueError('stored manifest hash mismatch')
        manifest=json.loads(row['manifest_json'])
        if self.artifact_root:
            expected=safe_path(self.artifact_root/version/'manifest.json')
            trusted=json.loads(expected.read_text(encoding='utf-8'))
            if encoded(trusted)!=row['manifest_json']: raise ValueError('stored manifest differs from protected artifact')
        if digest(row['coverage_json'])!=manifest['output_hashes']['coverage.json']:
            raise ValueError('stored coverage hash mismatch')
        return manifest,json.loads(row['coverage_json'])

    def status(self,version):
        manifest,coverage=self.metadata(version)
        return dict(dataset_id=version,status='completed',source_count=len(manifest['sources']),
                    counts=manifest['counts'],coverage=coverage,new_model_calls=0,
                    quality_note='原文记录数不等于已去重交易次数；未知身份/金额不计作0。')

    @staticmethod
    def payload(row):
        if digest(row['payload'])!=row['payload_sha']: raise ValueError('stored record hash mismatch')
        return json.loads(row['payload'])

    def load(self,version):
        key=(self.cache_namespace,version)
        with _cache_lock:
            if self.cache_namespace and key in _cache: return _cache[key]
            manifest,coverage=self.metadata(version)
            values={}
            with self.connection.cursor() as cur:
                for table,file in (('t2_facts','facts.jsonl'),('t2_evidence','evidence.jsonl')):
                    cur.execute('SELECT payload,payload_sha FROM '+table+' WHERE dataset_id=%s',(version,))
                    values[table]=[self.payload(r) for r in cur.fetchall()]
                    verify_records(values[table],manifest,file,'record_id' if table=='t2_facts' else 'evidence_id')
            fact_ids={f['record_id'] for f in values['t2_facts']}
            evidence_ids={e['evidence_id'] for e in values['t2_evidence']}
            source_ids={s['source_id'] for s in manifest['sources']}
            if any(not set(f['evidence_ids'])<=evidence_ids for f in values['t2_facts']): raise ValueError('stored fact evidence missing')
            if any(e['source_id'] not in source_ids or not set(e['fact_ids'])<=fact_ids for e in values['t2_evidence']):
                raise ValueError('stored evidence references missing')
            data=dict(dataset_id=version,sources=manifest['sources'],facts=values['t2_facts'],
                      evidence=values['t2_evidence'],coverage=coverage,manifest=manifest)
            if self.cache_namespace:
                _cache.clear();_cache[key]=data  # One immutable version only, bounded for 4GB host.
            return data

    def evidence(self,version,eid):
        if not re.fullmatch(r'evidence_[A-Za-z0-9_]{1,80}',eid): raise ValueError('invalid evidence identifier')
        data=self.load(version);manifest=data['manifest']
        evidence=next((e for e in data['evidence'] if e['evidence_id']==eid),None)
        if not evidence: raise KeyError('evidence not found')
        source=next((s for s in manifest['sources'] if s['source_id']==evidence['source_id']),None)
        if not source or evidence['dataset_id']!=version: raise ValueError('evidence source/version mismatch')
        return {**evidence,'announcement_id':source['announcement_id'],'html_sha256':source['html_sha256'],
                'encoding':source['encoding'],'source_read_status':source['read_status']}
