"""Compatibility only: no unsafe in-memory worker or paid fallback."""
from fastapi import Request
from task3_platform.contracts import PlatformError
from task3_platform.web_uploads import legacy_router

def provider(request: Request):
    selected=getattr(request.app.state,"task3_provider",None)
    if selected is None: raise PlatformError("platform_not_ready",503)
    yield from selected(request)

router=legacy_router(provider)
