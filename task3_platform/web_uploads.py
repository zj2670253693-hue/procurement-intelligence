from dataclasses import asdict
import hashlib
import io
import secrets
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from .contracts import FileSpec, UploadSpec, ModelBudget, PlatformError, strict_json_loads


async def bounded_body(request, limit):
    result = bytearray()
    async for chunk in request.stream():
        if len(result) + len(chunk) > limit:
            raise PlatformError("request_byte_limit", 413)
        result.extend(chunk)
    return bytes(result)


async def json_body(request):
    value = strict_json_loads(await bounded_body(request, 64*1024))
    if not isinstance(value, dict):
        raise PlatformError("invalid_request_object")
    return value


def upload_dto(session):
    return {"job": asdict(session.job), "files": [asdict(file) for file in session.files]}


def request_spec(body, services):
    if set(body) - {"files", "mode", "budget", "request_key"} or not isinstance(body.get("files"), list):
        raise PlatformError("invalid_upload_spec")
    specs = []
    for file in body["files"]:
        if not isinstance(file, dict) or set(file) != {"name", "bytes", "part_hashes"} or not isinstance(file["name"], str) or not isinstance(file["part_hashes"], list):
            raise PlatformError("invalid_upload_spec")
        specs.append(FileSpec(file["name"], file["bytes"], tuple(file["part_hashes"])))
    budget = body.get("budget")
    if budget is not None and (not isinstance(budget,dict) or set(budget)!={"max_calls","max_input_tokens","max_output_tokens"}):
        raise PlatformError("invalid_model_budget")
    return UploadSpec(tuple(specs), body.get("mode", "parse_only"), services.processing_fingerprint, ModelBudget(**budget) if budget is not None else None)


def router(provider):
    api = APIRouter(prefix="/api/platform")
    @api.post("/uploads")
    async def begin(request: Request, services=Depends(provider)):
        actor = services.actor(request, mutation=True, guest=True)
        body = await json_body(request)
        key = body.get("request_key")
        if not isinstance(key,str): raise PlatformError("invalid_request_key")
        return upload_dto(services.uploads.begin(actor, request_spec(body,services), key))
    @api.get("/uploads/{task_id}")
    def resume(task_id: str, request: Request, services=Depends(provider)):
        actor=services.actor(request,guest=True)
        services.jobs.get(actor,task_id)
        with services.connection.cursor() as cursor:
            row=services.jobs._row(cursor,task_id)
        body=strict_json_loads(row["spec_json"])
        body["request_key"]=row["request_key"]
        # Idempotent server manifest; begin returns existing verified part numbers.
        return upload_dto(services.uploads.begin(actor,request_spec({key:body[key] for key in ("files","mode","budget") if key in body},services),row["request_key"]))
    @api.put("/uploads/{task_id}/files/{file_id}/parts/{part_no}")
    async def part(task_id: str,file_id: str,part_no: int,request: Request,services=Depends(provider)):
        actor=services.actor(request,mutation=True,guest=True)
        services.jobs.get(actor,task_id)
        data=await bounded_body(request,services.jobs.limits.chunk_bytes)
        return asdict(services.uploads.put_part(actor,task_id,file_id,part_no,io.BytesIO(data)))
    @api.post("/uploads/{task_id}/complete")
    def complete(task_id: str,request: Request,services=Depends(provider)):
        actor=services.actor(request,mutation=True,guest=True)
        job=services.uploads.complete(actor,task_id)
        return services.task_dto(actor,job.task_id)
    @api.get("/tasks")
    def tasks(request: Request,services=Depends(provider)):
        return services.tasks(services.actor(request,guest=True))
    @api.get("/tasks/{task_id}")
    def task(task_id: str,request: Request,services=Depends(provider)):
        return services.task_dto(services.actor(request,guest=True),task_id)
    @api.post("/tasks/{task_id}/cancel")
    def cancel(task_id: str,request: Request,services=Depends(provider)):
        actor=services.actor(request,mutation=True,guest=True)
        services.jobs.request_cancel(actor,task_id)
        return services.task_dto(actor,task_id)
    @api.post("/tasks/{task_id}/retry")
    def retry(task_id: str,request: Request,services=Depends(provider)):
        actor=services.actor(request,mutation=True)
        services.jobs.retry(actor,task_id)
        return services.task_dto(actor,task_id)
    return api


def legacy_router(provider):
    api=APIRouter(prefix="/api")
    @api.post("/upload")
    async def upload(request: Request,files:list[UploadFile]=File(...),run_extraction:bool=Form(False),services=Depends(provider)):
        actor=services.actor(request,mutation=True,guest=True)
        # Compatibility accepts only <=64MiB requests; large uploads use /platform/uploads parts.
        payloads,total=[],0
        for file in files:
            data=await file.read(services.jobs.limits.request_bytes+1-total)
            total+=len(data)
            if total>services.jobs.limits.request_bytes: raise PlatformError("request_byte_limit",413)
            payloads.append((file.filename,data))
        specs=[FileSpec(name,len(data),tuple(hashlib.sha256(data[pos:pos+services.jobs.limits.chunk_bytes]).hexdigest() for pos in range(0,len(data),services.jobs.limits.chunk_bytes))) for name,data in payloads]
        session=services.uploads.begin(actor,UploadSpec(tuple(specs),"extract" if run_extraction else "parse_only",services.processing_fingerprint),request.headers.get("x-request-key") or secrets.token_hex(16))
        for slot,(_,data) in zip(session.files,payloads):
            for part_no,pos in enumerate(range(0,len(data),services.jobs.limits.chunk_bytes)):
                services.uploads.put_part(actor,session.job.task_id,slot.file_id,part_no,io.BytesIO(data[pos:pos+services.jobs.limits.chunk_bytes]))
        job=services.uploads.complete(actor,session.job.task_id)
        return services.task_dto(actor,job.task_id)
    @api.get("/tasks")
    def tasks(request: Request,services=Depends(provider)):
        return services.tasks(services.actor(request,guest=True))
    @api.get("/tasks/{task_id}")
    def task(task_id: str,request: Request,services=Depends(provider)):
        return services.task_dto(services.actor(request,guest=True),task_id)
    return api
