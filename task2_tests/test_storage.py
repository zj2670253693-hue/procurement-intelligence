import pytest


def test_schema_only_creates_independent_task2_tables():
    from task2_query_service.storage import DDL
    text=' '.join(DDL).lower()
    assert 't2_datasets' in text and 't2_facts' in text and 't2_evidence' in text
    assert not any(word in text for word in ('drop ', 'delete ', 'alter ', 'extracted_entities','announcements'))


class Cursor:
    def __init__(self): self.calls=[];self.rows=[]
    def execute(self,sql,args=None): self.calls.append((sql,args))
    def fetchone(self): return self.rows.pop(0) if self.rows else None
    def fetchall(self): return self.rows
    def __enter__(self): return self
    def __exit__(self,*args): pass


class Connection:
    def __init__(self): self.cur=Cursor();self.events=[]
    def cursor(self): return self.cur
    def begin(self): self.events.append('begin')
    def commit(self): self.events.append('commit')
    def rollback(self): self.events.append('rollback')


def test_requested_version_cannot_resolve_pending_or_inject_sql():
    from task2_query_service.storage import MySQLRepository
    conn=Connection();repo=MySQLRepository(conn)
    conn.cur.rows=[{'dataset_id':'ds_abc','status':'pending'}]
    with pytest.raises(KeyError): repo.resolve_version('ds_abc')
    assert conn.cur.calls[-1][1]==('ds_abc',)
    with pytest.raises(ValueError): repo.resolve_version("ds_abc' OR 1=1")


def test_completed_version_resolution_uses_parameterized_query():
    from task2_query_service.storage import MySQLRepository
    conn=Connection();conn.cur.rows=[{'dataset_id':'ds_abc','status':'completed'}]
    assert MySQLRepository(conn).resolve_version('ds_abc')=='ds_abc'
    assert conn.cur.calls[-1][1]==('ds_abc',)


def test_record_hashes_are_compared_to_manifest_not_only_adjacent_mutable_hash():
    from task2_query_service.storage import verify_records,encoded,digest
    from hashlib import sha256
    import json
    original={'dataset_id':'ds_abc','record_id':'fact','value':'original'}
    manifest={'dataset_id':'ds_abc','counts':{'facts.jsonl':1},
              'output_hashes':{'facts.jsonl':sha256((encoded(original)+'\n').encode()).hexdigest()}}
    verify_records([original],manifest,'facts.jsonl','record_id')
    changed={**original,'value':'changed'}
    assert digest(encoded(changed))!=digest(encoded(original))
    with pytest.raises(ValueError,match='canonical'): verify_records([changed],manifest,'facts.jsonl','record_id')
    with pytest.raises(ValueError): verify_records([],manifest,'facts.jsonl','record_id')
    with pytest.raises(ValueError): verify_records([original,original],manifest,'facts.jsonl','record_id')


def test_reused_import_loads_and_checks_all_stored_rows(local_tmp,monkeypatch):
    from task2_tests.test_artifact_loader import version
    from task2_query_service import storage
    directory=version(local_tmp);data=storage.load_dataset(directory)
    conn=Connection();conn.cur.rows=[{'status':'completed','manifest_sha':storage.digest(storage.encoded(data['manifest']))}]
    called=[]
    def refuse(*args):called.append(True);raise ValueError('stored missing records')
    monkeypatch.setattr(storage.MySQLRepository,'load',refuse)
    with pytest.raises(ValueError,match='stored missing'):storage.import_dataset(conn,directory)
    assert called and conn.events[-1]=='rollback'


def test_runtime_import_can_explicitly_forbid_ddl(local_tmp,monkeypatch):
    from task2_tests.test_artifact_loader import version
    from task2_query_service import storage
    directory=version(local_tmp);data=storage.load_dataset(directory)
    conn=Connection();conn.cur.rows=[{'status':'completed','manifest_sha':storage.digest(storage.encoded(data['manifest']))}]
    monkeypatch.setattr(storage.MySQLRepository,'load',lambda *args: {})
    result=storage.import_dataset(conn,directory,initialize_schema=False)
    assert result['status']=='reused'
    assert not any(sql.lstrip().startswith(('CREATE','ALTER','DROP')) for sql,_ in conn.cur.calls)


def test_import_pulses_at_load_and_transaction_boundaries(local_tmp,monkeypatch):
    from task2_tests.test_artifact_loader import version
    from task2_query_service import storage
    directory=version(local_tmp);data=storage.load_dataset(directory)
    conn=Connection();conn.cur.rows=[{'status':'completed','manifest_sha':storage.digest(storage.encoded(data['manifest']))}]
    monkeypatch.setattr(storage.MySQLRepository,'load',lambda *args: {})
    pulses=[]
    storage.import_dataset(conn,directory,initialize_schema=False,pulse=lambda:pulses.append(1))
    assert len(pulses)>=3
