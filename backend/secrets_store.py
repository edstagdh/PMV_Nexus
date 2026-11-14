"""Encrypted credential store -- every password, API key and account setting
the app needs, managed from Admin -> Credentials instead of a .env file.

Storage: data/secrets.db (SQLite), one row per credential, each value
encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using the key in
data/secret.key. The key is generated on first start.

What this protects against: a copy of secrets.db (or a backup made without
"include secrets") is useless without secret.key. Anyone who can read the
whole data/ folder can still decrypt it -- the same exposure a .env file has,
so keep that folder private.

Kept in its own file (not library.db) on purpose: config.get_settings() reads
it before the library database is opened, and restoring a backup without
secrets leaves the current credentials untouched.

Upgrading from .env: on the first start, any credential still found in the
environment / .env is imported once; after that .env is never read again.
"""
from __future__ import annotations

import os
import secrets as pysecrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
KEY_FILE = DATA_DIR / "secret.key"
DB_FILE = DATA_DIR / "secrets.db"
_ENV_IMPORTED = "__env_imported"

# name -> definition. `kind`: "secret" (write-only in the UI, shown masked),
# "text" / "number" (shown in clear -- identifiers, not passwords),
# "toggle" ("1" / "0"; a toggle that's on hides the rest of its group).
# `revealable`: secret the admin may view/copy (needed to paste elsewhere).
FIELDS: dict[str, dict] = {
    "pmvhaven_api_key": {"group": "pmvhaven_api", "label": "API key", "kind": "secret", "env": "PMVHAVEN_API_KEY"},
    "app_api_key": {"group": "external_api", "label": "API key", "kind": "secret", "env": "APP_API_KEY",
                    "revealable": True, "generate": True},
    "tpdb_api_key": {"group": "tpdb", "label": "API key", "kind": "secret"},
    "virustotal_api_key": {"group": "virustotal", "label": "API key", "kind": "secret"},
    "gemini_api_key": {"group": "gemini", "label": "API key", "kind": "secret", "env": "GEMINI_API_KEY"},
    "proxy_url": {"group": "proxy", "label": "Address", "kind": "text"},
    "proxy_username": {"group": "proxy", "label": "Username", "kind": "text"},
    "proxy_password": {"group": "proxy", "label": "Password", "kind": "secret"},
    # Internal: signs login cookies. Generated automatically, never shown.
    "app_secret_key": {"group": "internal", "label": "Session signing key", "kind": "secret",
                       "env": "APP_SECRET_KEY", "internal": True},
    # Protects new backups (2.25.0). Set on the Backups page, not under Credentials.
    "backup_password": {"group": "internal", "label": "Backup password", "kind": "secret", "internal": True},
}

GROUPS = [
    ("pmvhaven_api", "PMVHaven API",
     "API key from https://pmvhaven.com/api-keys (format pmvh_...). Used to sync videos with the site; "
     "the same key works on porntubeai.com and hmvhaven.com."),
    ("external_api", "External API (userscript)",
     "Key the companion userscript sends in the X-API-Key header. Leave empty to disable the external API."),
    ("virustotal", "VirusTotal",
     "Free API key from https://www.virustotal.com (sign in → API key). Used to look up each uploaded file's fingerprint "
     "(SHA-256) — only the fingerprint is sent, never the file. Server Settings → Uploads → Fingerprint check."),
    ("gemini", "Google Gemini (optional AI)",
     "Optional, free-tier API key from https://aistudio.google.com/apikey. Only used by features you turn on: "
     "Management → Songs → “Ask Gemini (AI)” sends 20-second sound clips of the spots Shazam didn't recognise. "
     "Free-tier requests may be used by Google to improve its products."),
    ("tpdb", "ThePornDB",
     "API key from https://theporndb.net/user/api-tokens. Used for performer profiles (photo, bio, aliases)."),
    ("proxy", "Proxy",
     "e.g. socks5://192.168.1.10:1080 or http://proxy.example:8080; username/password only if it needs them. "
     "Where it's used, and a speed test: Server Settings → Proxy."),
]


def _fernet() -> Fernet:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        KEY_FILE.write_bytes(Fernet.generate_key())
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
        logger.info("Generated a new credential encryption key at {}", KEY_FILE)
    return Fernet(KEY_FILE.read_bytes().strip())


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL, updated_at REAL NOT NULL)")
    return conn


def read_all() -> dict[str, dict]:
    """name -> {"value": str, "updated_at": float}. Values that cannot be
    decrypted (e.g. secrets.db restored without its key) are skipped with a
    warning, i.e. treated as not set."""
    f = _fernet()
    out: dict[str, dict] = {}
    with _connect() as conn:
        for name, blob, updated_at in conn.execute("SELECT name, value, updated_at FROM secrets"):
            try:
                out[name] = {"value": f.decrypt(blob).decode("utf-8"), "updated_at": updated_at}
            except InvalidToken:
                logger.warning("Credential '{}' cannot be decrypted with the current key -- ignoring it", name)
    return out


def values() -> dict[str, Optional[str]]:
    stored = read_all()
    return {name: (stored[name]["value"] if name in stored else d.get("default")) for name, d in FIELDS.items()}


def write(updates: dict[str, Optional[str]]) -> None:
    """Empty string or None deletes the credential."""
    unknown = set(updates) - set(FIELDS) - {_ENV_IMPORTED}
    if unknown:
        raise ValueError(f"Unknown credential(s): {', '.join(sorted(unknown))}")
    f = _fernet()
    now = time.time()
    with _connect() as conn:
        for name, value in updates.items():
            value = (value or "").strip() if name != _ENV_IMPORTED else value
            if not value:
                conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
            else:
                conn.execute(
                    "INSERT INTO secrets (name, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (name, f.encrypt(value.encode("utf-8")), now),
                )


def generate_token() -> str:
    return pysecrets.token_hex(32)


def ensure_initialized() -> None:
    """Runs on every settings load (cheap after the first time): imports any
    legacy .env values once, and makes sure the cookie-signing key exists."""
    stored = read_all()
    updates: dict[str, str] = {}
    if _ENV_IMPORTED not in stored:
        imported = []
        for name, d in FIELDS.items():
            env_value = os.getenv(d["env"]) if d.get("env") else None
            if env_value and name not in stored:
                updates[name] = env_value
                imported.append(d["env"])
        updates[_ENV_IMPORTED] = str(time.time())
        if imported:
            logger.info(
                "Imported {} credential(s) from .env into the encrypted credential store ({}). "
                ".env is no longer read -- manage credentials in Server Settings → Credentials.",
                len(imported), ", ".join(imported),
            )
    if "app_secret_key" not in stored and "app_secret_key" not in updates:
        updates["app_secret_key"] = generate_token()
    if updates:
        write(updates)
