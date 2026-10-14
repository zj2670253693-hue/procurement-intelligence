"""Isolated HTTP surface; no DDL, model client or legacy paid fallback."""
from fastapi import Request
from fastapi.responses import JSONResponse
from .contracts import PlatformError
from .limits import Limits
from .services import configured_services
from . import web_auth, web_uploads, web_datasets

class RequestLimit:
    def __init__(self,app,limit): self.app,self.limit=app,limit
    async def __call__(self,scope,receive,send):
        if scope["type"]!="http" or not (scope.get("path","").startswith("/api/platform") or scope.get("path")=="/api/upload"):
            return await self.app(scope,receive,send)
        raw=dict(scope.get("headers",[])).get(b"content-length")
        if raw is not None and (not raw.isdigit() or int(raw)>self.limit):
            return await JSONResponse({"code":"request_byte_limit"},status_code=413)(scope,receive,send)
        size=0
        async def bounded():
            nonlocal size
            message=await receive()
            if message["type"]=="http.request":
                size+=len(message.get("body",b""))
                if size>self.limit: raise PlatformError("request_byte_limit",413)
            return message
        await self.app(scope,bounded,send)

def install(app,backend_root,*,services_provider=None):
    def provider(request: Request):
        result=services_provider(request) if services_provider else configured_services(request,backend_root)
        if hasattr(result,"__enter__"):
            with result as value: yield value
        else: yield result
    app.state.task3_provider=provider
    @app.exception_handler(PlatformError)
    async def platform_error(request,error):
        return JSONResponse({"code":error.code},status_code=error.http_status,headers={"Cache-Control":"no-store"})
    app.add_middleware(RequestLimit,limit=Limits().request_bytes)
    app.include_router(web_auth.router(provider))
    app.include_router(web_uploads.router(provider))
    app.include_router(web_uploads.legacy_router(provider))
    app.include_router(web_datasets.router(provider))
    app.include_router(web_datasets.relationship_router(provider))
