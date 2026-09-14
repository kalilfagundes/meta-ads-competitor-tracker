"""Login / logout. The first-run wizard lives in `setup`."""

from __future__ import annotations

import math

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db
from ...auth import SESSION_COOKIE_NAME, verify_password
from .. import throttle
from ..deps import _needs_setup, _sanitize_next, _set_session_cookie, templates

router = APIRouter()


def _too_many_attempts(request: Request, nxt: str, wait_seconds: int):
    return templates.TemplateResponse(
        request, "login.html",
        {"show_error": False, "next": nxt, "retry_minutes": math.ceil(wait_seconds / 60)},
        status_code=429, headers={"retry-after": str(wait_seconds)},
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if _needs_setup():
        return RedirectResponse("/setup", status_code=302)
    nxt = _sanitize_next(request.query_params.get("next"))
    return templates.TemplateResponse(
        request, "login.html", {"show_error": False, "next": nxt}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
):
    nxt = _sanitize_next(next)
    address = request.client.host if request.client else ""
    user = db.get_user_by_username(username)
    account = str(user["id"]) if user else None

    def retry_after() -> int:
        return max(throttle.login_throttle.retry_after(address),
                   throttle.account_throttle.retry_after(account) if account else 0)

    # Checked before the password, so a blocked address or account learns nothing by guessing.
    if wait := retry_after():
        return _too_many_attempts(request, nxt, wait)
    if not user or not verify_password(password, user["password_hash"]):
        throttle.login_throttle.record_failure(address)
        if account:
            throttle.account_throttle.record_failure(account)
        if wait := retry_after():
            return _too_many_attempts(request, nxt, wait)
        return templates.TemplateResponse(
            request, "login.html", {"show_error": True, "next": nxt},
            status_code=401,
        )
    throttle.login_throttle.reset(address)
    throttle.account_throttle.reset(account)
    resp = RedirectResponse(nxt or "/", status_code=302)
    _set_session_cookie(resp, request)
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp

