from dataclasses import replace
import hashlib
from types import SimpleNamespace
from task3_tests.test_datasets import DatasetConnection
from task3_tests.test_jobs import spec

def setup(platform_module,tmp_path,actor,clock,mode):
    connection=DatasetConnection(tmp_path/"runtime.sqlite")
    connection.db.executescript("CREATE TABLE t3_users(user_id TEXT PRIMARY KEY,role TEXT,enabled INTEGER);")
    connection.db.execute("INSERT INTO t3_users VALUES(?,?,1)",(actor.user_id,actor.role))
    connection.db.commit()
    limits=replace(platform_module("limits").Limits(),reserve_disk_bytes=1)
    jobs=platform_module("job_store").JobStore(connection,clock,limits=limits)
    job=jobs.create_receiving(actor,spec(platform_module("contracts"),mode=mode),"runtime")
    paths=platform_module("paths").RuntimePaths(tmp_path/"runtime")
    html=paths.task(job.task_id).inputs/"t20990101_1.html"
    html.parent.mkdir(parents=True)
    html.write_text("<p>合成项目名称：测试</p>",encoding="utf-8")
    prepared=platform_module("contracts").PreparedInput(html.stem,html,None,hashlib.sha256(html.read_bytes()).hexdigest(),None)
    jobs.finalize(job.task_id,{"files":[],"inputs":[prepared.to_dto()]})
    return connection,jobs,job,paths

def test_real_parse_export_workflow_has_no_entities(platform_module,tmp_path,actor_a,clock):
    connection,jobs,job,paths=setup(platform_module,tmp_path,actor_a,clock,"parse_only")
    steps=platform_module("runtime").RuntimeSteps(jobs,paths,processing_fingerprint=job.processing_fingerprint)
    result=platform_module("worker").ProcessingWorker(jobs,steps,clock).run_once()
    assert result.status=="validated_only"
    assert (paths.task(job.task_id).exports/"standard.xlsx").is_file()
    assert connection.db.execute("SELECT COUNT(*) FROM t3_entities").fetchone()[0]==0
    assert connection.db.execute("SELECT COUNT(*) FROM t3_artifacts").fetchone()[0]==9
    connection.db.close()

def test_extraction_composition_imports_counts_and_publishes_only_own_dataset(platform_module,tmp_path,actor_a,clock):
    connection,jobs,job,paths=setup(platform_module,tmp_path,actor_a,clock,"extract")
    from models.schemas import ExtractionResult,ExtractedEntity,DetailedEntity
    result=ExtractionResult(announcement_id="t20990101_1",entities=[ExtractedEntity(product_name="采购包一")],
        detail_entities=[DetailedEntity(product_name="设备甲"),DetailedEntity(product_name="设备乙")],detail_status="available")
    def entity(*args): return platform_module("entity_step").EntityStepResult(result,{},"complete")
    class Relations:
        def build_and_bind(self,lease,*args,**kwargs):
            connection.db.execute("UPDATE t3_datasets SET task2_dataset_id=? WHERE upload_dataset_id=?",("ds_"+"f"*64,job.upload_dataset_id))
            connection.db.commit()
            return SimpleNamespace(task2_dataset_id="ds_"+"f"*64,manifest_sha256="f"*64)
    steps=platform_module("runtime").RuntimeSteps(jobs,paths,processing_fingerprint=job.processing_fingerprint,
        transport_factory=lambda *args:None,entity_runner=entity,relations=Relations())
    completed=platform_module("worker").ProcessingWorker(jobs,steps,clock).run_once()
    assert completed.status=="completed",connection.db.execute("SELECT diagnostics_json FROM t3_jobs").fetchone()[0]
    assert connection.db.execute("SELECT COUNT(*) FROM t3_entities").fetchone()[0]==1
    assert connection.db.execute("SELECT COUNT(*) FROM t3_detail_entities").fetchone()[0]==2
    assert connection.db.execute("SELECT availability FROM t3_datasets").fetchone()[0]=="completed"
    assert not any("UPDATE extracted_entities" in sql for sql,_ in connection.statements)
    connection.db.close()


def test_relation_failed_staging_receipts_and_published_bytes_survive_restart_accounting(platform_module,tmp_path,actor_a,clock):
    import json
    connection,jobs,job,paths=setup(platform_module,tmp_path,actor_a,clock,'extract')
    lease=jobs.claim('quota-test')
    steps=platform_module('runtime').RuntimeSteps(jobs,paths,processing_fingerprint=job.processing_fingerprint)
    before=steps._actual(lease)
    staged=paths.root/'task2/staging'/job.task_id/'artifacts/.pending-synthetic'
    staged.mkdir(parents=True);(staged/'partial').write_bytes(b'x'*100)
    receipt=staged.parent.parent/'run_receipts';receipt.mkdir();(receipt/'synthetic.inputs.json').write_bytes(b'y'*50)
    version='ds_'+'a'*64
    published=paths.root/'task2/artifacts'/version;published.mkdir(parents=True);(published/'manifest.json').write_bytes(b'z'*75)
    index=paths.task(job.task_id).extraction/'relation-ownership.json';index.parent.mkdir(parents=True,exist_ok=True)
    index.write_text(json.dumps({'versions':[version]}))
    restarted=platform_module('runtime').RuntimeSteps(jobs,paths,processing_fingerprint=job.processing_fingerprint)
    assert restarted._actual(lease)>=before+225+index.stat().st_size
    restarted._reserve(lease,0,settle=True)
    stored=connection.db.execute('SELECT reserved_bytes FROM t3_jobs WHERE task_id=?',(job.task_id,)).fetchone()[0]
    assert stored==restarted._actual(lease)
    connection.db.close()
