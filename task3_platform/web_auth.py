from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool
from contextlib import contextmanager
from collections import deque
import threading
import time
from .contracts import PlatformError
from .web_uploads import json_body


class LoginAdmission:
    """One API process: finite hash work, bounded per-source attempts and memory."""
    def __init__(self,*,clock=time.monotonic,max_attempts=12,window_seconds=60,max_inflight=1):
        self.clock,self.max_attempts,self.window_seconds=clock,max_attempts,window_seconds
        self.max_inflight,self.inflight=max_inflight,0
        self.recent={};self.lock=threading.Lock()

    @contextmanager
    def enter(self,source):
        with self.lock:
            now=self.clock()
            for key in list(self.recent):
                entries=self.recent[key]
                while entries and entries[0]<=now-self.window_seconds: entries.popleft()
                if not entries: del self.recent[key]
            if source not in self.recent and len(self.recent)>=1024:
                raise PlatformError('login_rate_limited',429)
            attempts=self.recent.setdefault(source,deque())
            if len(attempts)>=self.max_attempts: raise PlatformError('login_rate_limited',429)
            attempts.append(now)
            if self.inflight>=self.max_inflight: raise PlatformError('login_busy',429)
            self.inflight+=1
        try: yield
        finally:
            with self.lock: self.inflight-=1


def router(provider):
    api = APIRouter(prefix="/api/platform/auth")
    admission=LoginAdmission()
    @api.post("/login")
    async def login(request: Request, response: Response, services=Depends(provider)):
        body = await json_body(request)
        if set(body) != {"username", "password"} or not all(isinstance(body[key], str) for key in body):
            raise PlatformError("invalid_credentials", 401)
        if request.headers.get("origin") not in (None, services.auth.origin):
            raise PlatformError("csrf_forbidden", 403)
        # Do not trust forwarded headers as a rate identity. Bounded rejection is
        # explicit; expensive SQL/scrypt never blocks the ASGI event-loop thread.
        with admission.enter(request.client.host if request.client else 'unknown'):
            session = await run_in_threadpool(services.auth.login,body["username"],body["password"],services.secure)
        response.set_cookie("t3_session", session.token, max_age=8*3600, secure=True, httponly=True, samesite="strict", path="/")
        response.set_cookie("t3_csrf", session.csrf, max_age=8*3600, secure=True, httponly=False, samesite="strict", path="/")
        return {"user_id": session.actor.user_id, "role": session.actor.role, "csrf": session.csrf}
    @api.get("/session")
    def session(request: Request, services=Depends(provider)):
        actor = services.actor(request)
        csrf = request.cookies.get("t3_csrf")
        services.auth.require_csrf(request.cookies.get("t3_session"), csrf, services.auth.origin)
        return {"user_id": actor.user_id, "role": actor.role, "csrf": csrf}
    @api.post("/logout")
    def logout(request: Request, response: Response, services=Depends(provider)):
        services.actor(request, mutation=True)
        services.auth.logout(request.cookies.get("t3_session"))
        response.delete_cookie("t3_session", path="/", secure=True, httponly=True, samesite="strict")
        response.delete_cookie("t3_csrf", path="/", secure=True, samesite="strict")
        return {"logged_out": True}
    return api
