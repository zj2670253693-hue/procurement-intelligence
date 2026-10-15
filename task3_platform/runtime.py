"""Executable serial workflow. Production uses ledger transport; no paid default."""
from pathlib import Path
import os
import shutil
import time
from models.inference import InferenceHalt
from .artifacts import ArtifactService
from .auth import Actor, _now
from .contracts import PlatformError, PreparedInput, canonical_bytes, strict_json_loads
from .datasets import DatasetRepository
from .entity_step import run_entity_step
from .exports import build_exports
from .job_store import transaction
from .parse_child import run_parse_child
from .paths import reject_link_ancestors
from .relations import RelationService
from .services import processing_fingerprint
from .uploads import _hash_file
from .worker import atomic_json, ProcessingWorker

class RuntimeSteps:
    def __init__(self,jobs,paths,*,processing_fingerprint=None,transport_factory=None,
                 entity_runner=run_entity_step,relations=None):
        self.jobs,self.paths=jobs,paths
        self.processing_fingerprint=processing_fingerprint or globals()["processing_fingerprint"]()
        self.transport_factory,self.entity_runner=transport_factory,entity_runner
        self.datasets=DatasetRepository(jobs.connection,jobs=jobs,paths=paths)
        self.artifacts=ArtifactService(jobs,paths)
        self.relations=relations or RelationService(jobs.connection,paths,jobs=jobs)
        self._lease=None

    def _actual(self,lease):
        root=self.paths.task(lease.task_id).inputs.parent
        from .relation_disk import counted_roots
        total=0
        for owned_root in (root,*counted_roots(self.paths,lease.task_id)):
            reject_link_ancestors(owned_root)
            if owned_root.is_file():
                total+=owned_root.stat().st_size
                continue
            for path in owned_root.rglob("*"):
                reject_link_ancestors(path)
                if path.is_file(): total+=path.stat().st_size
        return total

    def _reserve(self,lease,additional,*,settle=False):
        actual=self._actual(lease)
        root=self.paths.task(lease.task_id).inputs.parent
        with transaction(self.jobs.connection) as cursor:
            runtime=self.jobs._runtime(cursor)
            row=self.jobs._fenced(cursor,lease)
            wanted=actual if settle else max(row["reserved_bytes"],actual+additional)
            delta=wanted-row["reserved_bytes"]
            if wanted>self.jobs.limits.job_disk_bytes or runtime["reserved_bytes"]+delta>self.jobs.limits.global_disk_bytes:
                raise PlatformError("runtime_disk_quota",413)
            if delta>0 and shutil.disk_usage(root).free<delta+self.jobs.limits.reserve_disk_bytes:
                raise PlatformError("disk_reserve_required",503)
            cursor.execute("UPDATE t3_jobs SET reserved_bytes=%s WHERE task_id=%s",(wanted,lease.task_id))
            cursor.execute("UPDATE t3_runtime_state SET reserved_bytes=reserved_bytes+%s WHERE singleton_id=1",(delta,))

    def prepare(self,lease,job):
        if job.processing_fingerprint!=self.processing_fingerprint:
            raise PlatformError("processing_version_changed",409)
        self._lease=lease
        with self.jobs.connection.cursor() as cursor:
            row=self.jobs._row(cursor,job.task_id)
        paths=self.paths.task(job.task_id)
        result=[]
        for item in strict_json_loads(row["manifest_json"])["inputs"]:
            aid=item["announcement_id"]
            from .contracts import validate_announcement_id
            validate_announcement_id(aid)
            html=reject_link_ancestors(paths.inputs/(aid+".html"))
            archive=reject_link_ancestors(paths.inputs/(aid+".zip")) if item["zip_sha"] else None
            if _hash_file(html)!=item["html_sha"] or archive is not None and _hash_file(archive)!=item["zip_sha"]:
                raise PlatformError("prepared_input_integrity",409)
            result.append(PreparedInput(aid,html,archive,item["html_sha"],item["zip_sha"]))
        paths.extraction.mkdir(parents=True,exist_ok=True,mode=0o700)
        return result

    def parse(self,prepared,pulse):
        self._reserve(self._lease,2*self.jobs.limits.member_bytes+self.jobs.limits.html_bytes)
        parsed=run_parse_child(prepared,self.jobs.limits,pulse=pulse)
        self._reserve(self._lease,0,settle=True)
        return parsed

    def entity(self,lease,prepared,parsed,pulse):
        if self.transport_factory is None:
            raise InferenceHalt("paid_inference_disabled")
        transport=self.transport_factory(self.jobs,lease,pulse)
        return self.entity_runner(lease,prepared,parsed,transport,self.paths.task(lease.task_id))

    def save(self,lease,prepared,result,result_sha):
        return self.datasets.save_announcement(lease,prepared,result,result_sha256=result_sha)

    def _raw(self,lease,items,diagnostics):
        by_id={item["announcement_id"]:dict(item) for item in items}
        for diagnostic in diagnostics:
            aid=diagnostic.get("announcement_id")
            if not aid: continue
            receipt=reject_link_ancestors(self.paths.task(lease.task_id).extraction/"items"/str(lease.epoch)/(aid+".json"))
            if aid not in by_id:
                by_id[aid]=strict_json_loads(receipt.read_bytes()) if receipt.is_file() else {"announcement_id":aid,"success":False,"entities":[],"detail_entities":[],"extra_info":{}}
            item=by_id[aid]
            existing=item.get("diagnostics")
            if isinstance(existing,dict): item["processing_diagnostics"]=existing
            item["diagnostics"]=(existing if isinstance(existing,list) else [])+[diagnostic]
        values=[by_id[key] for key in sorted(by_id)]
        path=self.paths.task(lease.task_id).extraction/"raw_results.json"
        self._reserve(lease,len(canonical_bytes(values)))
        atomic_json(path,values)
        return path

    def _exports(self,lease,job,raw,pulse):
        inputs=self.prepare(lease,job)
        allowance=min(512*1024**2,self.jobs.limits.job_disk_bytes-self._actual(lease))
        self._reserve(lease,allowance)
        artifacts,report=build_exports(job,inputs,raw,self.paths.task(job.task_id).exports,pulse=pulse,max_output_bytes=allowance)
        self.artifacts.register(lease,artifacts)
        self._reserve(lease,0,settle=True)
        return report

    def finish_parse(self,lease,job,items,diagnostics,pulse):
        raw=self._raw(lease,items,diagnostics)
        report=self._exports(lease,job,raw,pulse)
        return {"exports_verified":report["saved_workbook_verified"]}

    def finish(self,lease,job,items,diagnostics,pulse):
        raw=self._raw(lease,items,diagnostics)
        allowance=min(512*1024**2,self.jobs.limits.job_disk_bytes-self._actual(lease)-64*1024)
        self._reserve(lease,allowance+64*1024)
        binding=self.relations.build_and_bind(lease,self.paths.task(job.task_id).inputs,raw,
            {"producer":"task3_uploaded_pipeline_v1"},pulse=pulse,max_output_bytes=allowance)
        self._reserve(lease,0,settle=True)
        report=self._exports(lease,job,raw,pulse)
        state="partial" if diagnostics or any(item.get("stage_status")=="partial" for item in items) or report["incomplete"] else "completed"
        with self.jobs.connection.cursor() as cursor:
            cursor.execute("SELECT role FROM t3_users WHERE user_id=%s",(job.owner_id,))
            role=cursor.fetchone()["role"]
        self.datasets.publish(Actor(job.owner_id,role),job.upload_dataset_id,state,
            {"task2_dataset_id":binding.task2_dataset_id,"relation_manifest_sha256":binding.manifest_sha256,
             "export_report":report,"processing_fingerprint":job.processing_fingerprint},lease=lease)
        return {"relations_verified":True,"exports_verified":True,"dataset_published":True}


def main():
    """Standalone systemd worker. DB migration is a separate explicit admin step."""
    from database.connection import DatabaseConfig,DatabaseManager
    from .job_store import JobStore
    from .limits import Limits
    from .paths import RuntimePaths
    import re
    database=os.getenv("TASK3_DB_NAME","")
    if not re.fullmatch(r"procurement_task3_[A-Za-z0-9_]+",database) or os.getenv("TASK3_ENABLED","0")!="1":
        raise PlatformError("platform_configuration_required",503)
    manager=DatabaseManager(DatabaseConfig(database=database)).connect(create_db=False,verbose=False)
    try:
        jobs=JobStore(manager._conn,limits=Limits.from_env(os.environ))
        paths=RuntimePaths(Path(os.environ["TASK3_RUNTIME_ROOT"]))
        worker=ProcessingWorker(jobs,RuntimeSteps(jobs,paths),_now)
        while True:
            worker.run_once()
            time.sleep(2)
    finally: manager.close(verbose=False)

if __name__=="__main__": main()
