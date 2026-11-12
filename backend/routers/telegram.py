"""
Server settings -> Connections -> Telegram: sign the app in to Telegram
(phone -> code -> password if two-step verification is on), check the saved
login, sign out. See backend/telegram_login.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import telegram_login
from ..security import AuthContext, require_permission

router = APIRouter()
_admin = require_permission("settings.manage")


class Phone(BaseModel):
    phone: str


class Code(BaseModel):
    code: str


class Password(BaseModel):
    password: str


async def _run(coro):
    try:
        return await coro
    except telegram_login.TelegramLoginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/telegram/status")
async def telegram_status(check: bool = False, user: AuthContext = Depends(_admin)):
    """check=true connects to Telegram to see whether the saved login still works."""
    return await telegram_login.status(check)


@router.post("/api/telegram/login/start")
async def telegram_login_start(body: Phone, user: AuthContext = Depends(_admin)):
    return await _run(telegram_login.start(body.phone))


class Email(BaseModel):
    email: str


@router.post("/api/telegram/login/email")
async def telegram_login_email(body: Email, user: AuthContext = Depends(_admin)):
    """Telegram asked for a login email first: it sends a code to this address."""
    return await _run(telegram_login.set_login_email(body.email))


@router.post("/api/telegram/login/email-code")
async def telegram_login_email_code(body: Code, user: AuthContext = Depends(_admin)):
    return await _run(telegram_login.verify_login_email(body.code))


@router.post("/api/telegram/login/resend")
async def telegram_login_resend(user: AuthContext = Depends(_admin)):
    """Send the code another way (SMS, call...) when Telegram offers one."""
    return await _run(telegram_login.resend())


@router.post("/api/telegram/login/code")
async def telegram_login_code(body: Code, user: AuthContext = Depends(_admin)):
    return await _run(telegram_login.code(body.code))


@router.post("/api/telegram/login/password")
async def telegram_login_password(body: Password, user: AuthContext = Depends(_admin)):
    return await _run(telegram_login.password(body.password))


@router.post("/api/telegram/login/cancel")
async def telegram_login_cancel(user: AuthContext = Depends(_admin)):
    await telegram_login.cancel()
    return {"ok": True}


@router.post("/api/telegram/logout")
async def telegram_logout(user: AuthContext = Depends(_admin)):
    await telegram_login.sign_out()
    return {"ok": True}
