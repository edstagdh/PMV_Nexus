"""
One-time interactive Telegram login for the "Share To" feature.

Pyrogram needs a phone number + login code (and possibly a 2FA password)
the first time it authenticates a user session -- something the FastAPI
server can't prompt for on its own. Run this script once, locally, with a
real terminal attached; it saves a reusable session file under your
configured session directory, which the backend then picks up
automatically on every share (no further login needed, including across
server restarts).

Usage:
    python generate_telegram_session.py

Requires the Telegram API ID / hash to already be saved in Admin -> Credentials
(get them from https://my.telegram.org). Run from the project root:
    python -m Tools.generate_telegram_session
"""
from __future__ import annotations

from pyrogram import Client

from backend.config import get_settings


def main() -> None:
    settings = get_settings()
    cfg = settings.share

    if not cfg.telegram_api_id or not cfg.telegram_api_hash:
        raise SystemExit(
            "The Telegram API ID / hash are not set -- save them in Admin -> Credentials, "
            "get them from https://my.telegram.org first."
        )

    workdir = settings.session_dir / "telegram"
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"Logging in -- session '{cfg.telegram_session_name}' will be saved under {workdir}")
    app = Client(
        cfg.telegram_session_name,
        api_id=cfg.telegram_api_id,
        api_hash=cfg.telegram_api_hash,
        workdir=str(workdir),
    )
    with app:
        me = app.get_me()
        print(f"Logged in as {me.first_name} (@{me.username or me.id}). Session saved -- you're all set.")


if __name__ == "__main__":
    main()
