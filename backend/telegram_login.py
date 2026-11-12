"""
Signing the app in to Telegram from the interface (Server settings ->
Connections -> Telegram), replacing Tools/generate_telegram_session.py.

Telegram's login is a conversation -- phone number, then the code Telegram
sends, then the account's cloud password if two-step verification is on --
so it's kept here between requests (one login at a time). The new login is
made in its own session file and only replaces the saved one once it
succeeds; the old file is kept as "<name>.session.bak". An expired login
(AUTH_KEY_UNREGISTERED when sharing) is fixed by simply signing in again.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from loguru import logger

from .config import get_settings
from .share_backends import is_expired_login, telegram_client

_LOGIN_TTL = 15 * 60  # a started login is dropped after this long
_TEMP_SUFFIX = "_login"


class TelegramLoginError(ValueError):
    pass


_state: dict = {}
_lock = asyncio.Lock()


def _session_dir() -> Path:
    return get_settings().session_dir / "telegram"


def _session_name() -> str:
    return get_settings().share.telegram_session_name


def _who(user) -> str:
    name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x) or "Telegram user"
    return f"{name} (@{user.username})" if getattr(user, "username", None) else f"{name} ({user.id})"


def _require_app_keys() -> None:
    cfg = get_settings().share
    if not cfg.telegram_api_id or not cfg.telegram_api_hash:
        raise TelegramLoginError("Save the Telegram API ID and hash in Server settings → Credentials first "
                                 "(from https://my.telegram.org)")


async def _drop_login() -> None:
    client = _state.pop("client", None)
    _state.clear()
    if client is not None:
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    temp = _session_dir() / f"{_session_name()}{_TEMP_SUFFIX}.session"
    temp.unlink(missing_ok=True)


async def status(check: bool = False) -> dict:
    """{configured, signed_in (session file exists), login_step, account?, ok?, error?}.
    check=True connects to Telegram to see whether the saved login still works."""
    cfg = get_settings().share
    session = _session_dir() / f"{_session_name()}.session"
    out = {
        "configured": bool(cfg.telegram_api_id and cfg.telegram_api_hash),
        "signed_in": session.exists(),
        "login_step": _state.get("step"),
        "phone": _state.get("phone_masked"),
    }
    if check and out["configured"] and out["signed_in"] and not _state:
        app = telegram_client(get_settings())
        try:
            await app.start()
            try:
                out["account"] = _who(await app.get_me())
                out["ok"] = True
            finally:
                await app.stop()
        except Exception as exc:  # noqa: BLE001
            out["ok"] = False
            out["expired"] = is_expired_login(exc)
            out["error"] = "The saved login has expired — sign in again." if out["expired"] else str(exc)[:300]
    return out


async def start(phone: str) -> dict:
    """Step 1: Telegram sends a login code to the account's Telegram apps (or by SMS)."""
    _require_app_keys()
    phone = "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")
    if len(phone.lstrip("+")) < 7:
        raise TelegramLoginError("Enter the phone number with its country code, e.g. +44 7700 900123")
    async with _lock:
        await _drop_login()
        client = telegram_client(get_settings(), name=f"{_session_name()}{_TEMP_SUFFIX}")
        try:
            await client.connect()
            sent = await client.send_code(phone)
        except Exception as exc:  # noqa: BLE001
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            (_session_dir() / f"{_session_name()}{_TEMP_SUFFIX}.session").unlink(missing_ok=True)
            raise TelegramLoginError(f"Telegram didn't send a code: {exc}") from exc
        _state.update(client=client, phone=phone, phone_masked=phone[:3] + "…" + phone[-2:], started=time.time())
        return _code_sent(sent)


# Where Telegram says it sent the code (SentCode.type), in words.
_DELIVERY = {
    "APP": "as a message in your Telegram app — in the chat named “Telegram” (blue tick), on the phone or "
           "desktop where you're already signed in. It often arrives without a notification, so open that chat.",
    "SMS": "by SMS.", "SMS_WORD": "by SMS (a word, not digits).", "SMS_PHRASE": "by SMS (a phrase, not digits).",
    "FIREBASE_SMS": "by SMS.", "FRAGMENT_SMS": "to your anonymous number on fragment.com (open Fragment to read it).",
    "CALL": "by a phone call that reads the code out.", "FLASH_CALL": "by a short phone call.",
    "MISSED_CALL": "by a missed call — the code is the last digits of the calling number.",
    "EMAIL_CODE": "by email, to the login email set on your Telegram account.",
}
_NEXT = {"SMS": "by SMS", "CALL": "by a phone call", "FLASH_CALL": "by a short phone call",
         "MISSED_CALL": "by a missed call", "FRAGMENT_SMS": "on fragment.com"}


def _name(value) -> str:
    return str(value or "").rsplit(".", 1)[-1].upper()


def _code_sent(sent) -> dict:
    kind = _name(getattr(sent, "type", None))
    if kind == "SETUP_EMAIL_REQUIRED":
        # Telegram sends no code until the account has a login email: set one up
        # here (email -> code sent to it -> then the login code goes to that email).
        _state.update(code_hash=sent.phone_code_hash, step="email", code_type=kind, next_type=None)
        logger.info("Telegram sign-in: Telegram asks for a login email first")
        return {"step": "email"}
    nxt = _name(getattr(sent, "next_type", None))
    _state.update(code_hash=sent.phone_code_hash, step="code", code_type=kind, next_type=nxt or None,
                  resend_after=time.time() + (getattr(sent, "timeout", None) or 0))
    logger.info("Telegram sign-in: login code sent ({}), other way available: {}", kind or "unknown", nxt or "none")
    return {"step": "code", "sent": _DELIVERY.get(kind, f"by Telegram ({kind.lower() or 'unknown way'})."),
            "type": kind, "next": _NEXT.get(nxt), "resend_in": int(getattr(sent, "timeout", None) or 0)}


async def set_login_email(email: str) -> dict:
    """Telegram asked for a login email (SETUP_EMAIL_REQUIRED): it sends a code to this email."""
    from pyrogram import raw
    email = (email or "").strip()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise TelegramLoginError("Enter a valid email address")
    async with _lock:
        st = _active()
        if st.get("step") != "email":
            raise TelegramLoginError("Telegram isn't asking for an email")
        try:
            await st["client"].invoke(raw.functions.account.SendVerifyEmailCode(
                purpose=raw.types.EmailVerifyPurposeLoginSetup(phone_number=st["phone"], phone_code_hash=st["code_hash"]),
                email=email))
        except Exception as exc:  # noqa: BLE001
            raise TelegramLoginError(f"Telegram didn't accept that email: {exc}") from exc
        st.update(step="email_code", email=email)
        return {"step": "email_code", "sent": f"to {email}"}


async def verify_login_email(code: str) -> dict:
    """The code Telegram emailed: confirms the login email; then the login code
    itself goes to that email (step "code")."""
    from pyrogram import raw
    async with _lock:
        st = _active()
        if st.get("step") != "email_code":
            raise TelegramLoginError("Enter the email address first")
        try:
            r = await st["client"].invoke(raw.functions.account.VerifyEmail(
                purpose=raw.types.EmailVerifyPurposeLoginSetup(phone_number=st["phone"], phone_code_hash=st["code_hash"]),
                verification=raw.types.EmailVerificationCode(code="".join(ch for ch in code if ch.isalnum()))))
        except Exception as exc:  # noqa: BLE001
            raise TelegramLoginError(f"That email code didn't work: {exc}") from exc
        sent = getattr(r, "sent_code", None)
        if sent is not None and type(sent).__name__ == "SentCodePaymentRequired":
            raise TelegramLoginError(
                f"Telegram wants a payment ({getattr(sent, 'amount', '')} {getattr(sent, 'currency', '')}) or Telegram "
                "Premium before this account can sign in to a new app; that can't be done from here.")
        new_hash = getattr(sent, "phone_code_hash", None)
        if new_hash:
            st["code_hash"] = new_hash
        st.update(step="code", code_type="EMAIL_CODE", next_type=None)
        logger.info("Telegram sign-in: login email confirmed; login code sent by email")
        return {"step": "code", "sent": f"by email, to {st.get('email')}.", "type": "EMAIL_CODE", "next": None, "resend_in": 0}


async def resend() -> dict:
    """"Didn't get it?": asks Telegram to send the code the next way it offers (SMS, call...)."""
    async with _lock:
        st = _active()
        if st.get("step") != "code":
            raise TelegramLoginError("Telegram is waiting for the password, not a code")
        if not st.get("next_type"):
            raise TelegramLoginError("Telegram offers no other way to send this code — wait a few minutes and start again")
        try:
            sent = await st["client"].resend_code(st["phone"], st["code_hash"])
        except Exception as exc:  # noqa: BLE001
            raise TelegramLoginError(f"Telegram didn't send it another way: {exc}") from exc
        return _code_sent(sent)


def _active() -> dict:
    if not _state or time.time() - _state.get("started", 0) > _LOGIN_TTL:
        raise TelegramLoginError("No sign-in in progress (or it timed out) — start again")
    return _state


async def code(value: str) -> dict:
    """Step 2: the code. Returns step "password" when two-step verification is on."""
    from pyrogram.errors import PhoneCodeExpired, PhoneCodeInvalid, SessionPasswordNeeded
    async with _lock:
        st = _active()
        if st["step"] != "code":
            raise TelegramLoginError("Telegram is waiting for the password, not a code")
        client = st["client"]
        try:
            user = await client.sign_in(st["phone"], st["code_hash"], "".join(ch for ch in value if ch.isdigit()))
        except SessionPasswordNeeded:
            st["step"] = "password"
            hint = None
            try:
                hint = await client.get_password_hint()
            except Exception:  # noqa: BLE001
                pass
            return {"step": "password", "hint": hint}
        except PhoneCodeInvalid as exc:
            raise TelegramLoginError("That code isn't right — check it and try again") from exc
        except PhoneCodeExpired as exc:
            await _drop_login()
            raise TelegramLoginError("That code has expired — start again for a new one") from exc
        if not hasattr(user, "id"):  # TermsOfService / False: no account with this number
            await _drop_login()
            raise TelegramLoginError("There's no Telegram account with this phone number")
        return await _finish(user)


async def password(value: str) -> dict:
    """Step 3 (two-step verification on): the account's cloud password."""
    from pyrogram.errors import PasswordHashInvalid
    async with _lock:
        st = _active()
        if st["step"] != "password":
            raise TelegramLoginError("Enter the code first")
        try:
            user = await st["client"].check_password(value)
        except PasswordHashInvalid as exc:
            raise TelegramLoginError("Wrong password") from exc
        return await _finish(user)


async def _finish(user) -> dict:
    """Saves the new login over the old one (kept as .bak)."""
    client = _state["client"]
    await client.disconnect()  # writes the session file
    folder = _session_dir()
    new = folder / f"{_session_name()}{_TEMP_SUFFIX}.session"
    current = folder / f"{_session_name()}.session"
    if current.exists():
        shutil.copy2(current, current.with_name(current.name + ".bak"))
    new.replace(current)
    _state.clear()
    who = _who(user)
    logger.info("Signed in to Telegram as {}", who)
    return {"step": "done", "account": who}


async def cancel() -> None:
    async with _lock:
        await _drop_login()


async def sign_out() -> None:
    """Forgets the saved login (the file is kept as .bak)."""
    async with _lock:
        await _drop_login()
        current = _session_dir() / f"{_session_name()}.session"
        if current.exists():
            current.replace(current.with_name(current.name + ".bak"))
