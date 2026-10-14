from dataclasses import asdict
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import FileResponse
from task2_query_service.api import create_router
from .contracts import PlatformError, strict_json_loads, content_sha256
from .filters import EntityFilters, FIELDS, UNKNOWN, page_window
from .paths import reject_link_ancestors
from .uploads import _hash_file

def download(path,name):
    return FileResponse(path,filename=name,media_type="application/octet-stream",headers={"X-Content-Type-Options":"nosniff","Cache-Control":"no-store"})

def router(provider):
    api=APIRouter(prefix="/api/platform")
    @api.get("/datasets")
    def datasets(request: Request,services=Depends(provider)):
        actor=services.actor(request,guest=True)
        with services.connection.cursor() as cursor:
            cursor.execute("SELECT d.upload_dataset_id,d.availability,d.task2_dataset_id,j.status,j.mode,d.task_id FROM t3_datasets d JOIN t3_jobs j ON j.task_id=d.task_id WHERE d.owner_id=%s ORDER BY d.created_at DESC LIMIT 100",(actor.user_id,))
            rows=cursor.fetchall()
        return {"items":[{"upload_dataset_id":"official","availability":"completed","label":"官方冻结数据（只读）"},*rows]}
    @api.get("/datasets/{dataset_id}/entities")
    def entities(dataset_id:str,request:Request,filters:str=Query(default="{}",max_length=8192),page:int=1,page_size:int=20,services=Depends(provider)):
        actor=services.actor(request,guest=True)
        value=strict_json_loads(filters)
        if not isinstance(value,dict) or set(value)-{"text","missing","ranges","quantity_unit"}: raise PlatformError("invalid_entity_filter")
        repository=services.baseline if dataset_id=="official" else services.datasets
        return asdict(repository.search(actor,dataset_id,EntityFilters(**value),page,page_size))
    @api.get("/datasets/{dataset_id}/stats")
    def stats(dataset_id:str,request:Request,services=Depends(provider)):
        actor=services.actor(request,guest=True)
        if dataset_id=="official": return services.baseline.stats()
        dataset=services.datasets.dataset(actor,dataset_id)
        with services.connection.cursor() as cursor:
            totals=[]
            for table in ("t3_announcements","t3_entities","t3_detail_entities"):
                cursor.execute("SELECT COUNT(*) AS n FROM "+table+" WHERE upload_dataset_id=%s",(dataset_id,))
                totals.append(cursor.fetchone()["n"])
            cursor.execute("SELECT payload_json FROM t3_entities WHERE upload_dataset_id=%s",(dataset_id,))
            fills={name:0 for name in FIELDS}
            for row in cursor.fetchall():
                item=strict_json_loads(row["payload_json"])
                for name in FIELDS: fills[name]+=str(item.get(name) or "").strip() not in UNKNOWN
        return {"upload_dataset_id":dataset_id,"availability":dataset["availability"],"announcements":totals[0],"entities":totals[1],"detail_entities":totals[2],"field_fill":{name:{"count":count,"rate":round(count/totals[1]*100,1) if totals[1] else 0} for name,count in fills.items()},"note":"填充率不是准确率；未公示值不强填。"}
    def source(services,actor,dataset_id,aid):
        return services.baseline.announcement(actor,dataset_id,aid) if dataset_id=="official" else services.datasets.announcement(actor,dataset_id,aid)
    @api.get("/datasets/{dataset_id}/announcements/{aid}")
    def announcement(dataset_id:str,aid:str,request:Request,services=Depends(provider)):
        return source(services,services.actor(request,guest=True),dataset_id,aid)
    @api.get("/datasets/{dataset_id}/announcements/{aid}/source")
    def source_download(dataset_id:str,aid:str,request:Request,services=Depends(provider)):
        actor=services.actor(request,guest=True)
        source(services,actor,dataset_id,aid)
        if dataset_id=="official": path=services.baseline.source_path(aid)
        else:
            dataset=services.datasets.dataset(actor,dataset_id)
            path=services.paths.task(dataset["task_id"]).inputs/(aid+".html")
        return download(path,aid+".html")
    @api.get("/datasets/{dataset_id}/announcements/{aid}/details")
    def details(dataset_id:str,aid:str,request:Request,page:int=1,page_size:int=200,services=Depends(provider)):
        actor=services.actor(request,guest=True)
        offset=page_window(page,page_size)
        info=source(services,actor,dataset_id,aid)
        if dataset_id=="official": return services.baseline.details(aid,page,page_size)
        with services.connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM t3_detail_entities WHERE upload_dataset_id=%s AND announcement_id=%s",(dataset_id,aid))
            total=cursor.fetchone()["n"]
            cursor.execute("SELECT payload_json FROM t3_detail_entities WHERE upload_dataset_id=%s AND announcement_id=%s ORDER BY row_no LIMIT %s OFFSET %s",(dataset_id,aid,page_size,offset))
            items=[strict_json_loads(row["payload_json"]) for row in cursor.fetchall()]
        return {"upload_dataset_id":dataset_id,"announcement_id":aid,"total":total,"items":items,"page":page,"page_size":page_size,"detail_status":info["result"].get("detail_status"),"detail_error_message":info["result"].get("detail_error_message","")}
    @api.get("/datasets/{dataset_id}/announcements/{aid}/attachments")
    def attachments(dataset_id:str,aid:str,request:Request,services=Depends(provider)):
        actor=services.actor(request,guest=True)
        info=source(services,actor,dataset_id,aid)
        if dataset_id=="official": return services.baseline.attachments(aid)
        dataset=services.datasets.dataset(actor,dataset_id)
        paths=services.paths.task(dataset["task_id"])
        parsed=reject_link_ancestors(paths.extraction/"parsed"/(aid+".json"))
        if not parsed.is_file(): raise PlatformError("parsed_preview_not_available",409)
        payload=strict_json_loads(parsed.read_bytes())
        archive=paths.inputs/(aid+".zip")
        expected=content_sha256({"announcement_id":aid,"html_sha":info["source_sha256"],"zip_sha":_hash_file(archive) if archive.is_file() else None})
        if payload.get("input_fingerprint")!=expected: raise PlatformError("parsed_preview_integrity",409)
        texts=payload["attachments"]["texts"]
        return {"upload_dataset_id":dataset_id,"announcement_id":aid,"total":len(texts),"items":[{"name":name,"text":text,"length":len(text)} for name,text in texts.items()],"failed_files":payload["attachments"]["failed_files"]}
    @api.get("/datasets/{dataset_id}/announcements/{aid}/attachments/download")
    def archive_download(dataset_id:str,aid:str,request:Request,services=Depends(provider)):
        actor=services.actor(request,guest=True)
        info=source(services,actor,dataset_id,aid)
        if not info.get("attachments"): raise PlatformError("attachment_not_available",404)
        if dataset_id=="official": path=services.baseline.source_path(aid).with_suffix(".zip")
        else:
            dataset=services.datasets.dataset(actor,dataset_id)
            path=services.paths.task(dataset["task_id"]).inputs/(aid+".zip")
        return download(path,aid+".zip")
    @api.get("/datasets/{dataset_id}/artifacts/{name}")
    def artifact(dataset_id:str,name:str,request:Request,services=Depends(provider)):
        actor=services.actor(request,guest=True)
        if dataset_id=="official": return download(services.baseline.workbook(),"任务一-标准三表.xlsx")
        verified=services.artifacts.resolve(actor,dataset_id,name)
        return download(verified.path,verified.name)
    return api

def relationship_router(provider):
    def repository(request:Request,services=Depends(provider)):
        actor=services.actor(request,mutation=request.method=="POST",guest=True)
        dataset_id=request.path_params["upload_dataset_id"]
        if dataset_id=="official": return services.baseline.relations()
        return services.relations.repository_for(actor,dataset_id)
    return create_router(repository,prefix="/api/platform/datasets/{upload_dataset_id}/task2")
