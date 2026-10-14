from fastapi import APIRouter, Depends, Request, Response
from .contracts import PlatformError
from .web_uploads import json_body


def router(provider):
    api = APIRouter(prefix="/api/platform/auth")
    @api.post("/login")
    async def login(request: Request, response: Response, services=Depends(provider)):
        body = await json_body(request)
        if set(body) != {"username", "password"} or not all(isinstance(body[key], str) for key in body):
            raise PlatformError("invalid_credentials", 401)
        if request.headers.get("origin") not in (None, services.auth.origin):
            raise PlatformError("csrf_forbidden", 403)
        session = services.auth.login(body["username"], body["password"], services.secure)
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
