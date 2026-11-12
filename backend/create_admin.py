"""
CLI recovery/utility tool for creating a user account or resetting a
password directly in the database, bypassing the web UI entirely.

Two independent modes:

1. CREATE (default) -- creates a new account. Typically used for:
   - RECOVERY: the app was started before INITIAL_ADMIN_USERNAME/
     INITIAL_ADMIN_PASSWORD were set, so main.py's startup hard-failed
     with "no way to log in" -- this creates that first admin account
     directly, without needing to edit .env and restart the server.
   - CONVENIENCE: creating a regular user from a script, without going
     through the Users management page in the web UI.

2. RESET PASSWORD (--reset-password) -- resets an EXISTING user's
   password directly. Primarily for: the sole admin account is locked
   out (forgot their password, or was accidentally disabled) and there
   is no other admin account to fix it through the web UI, since only
   one admin account is ever allowed to exist at a time. This mode can
   also re-enable a disabled account in the same step (--reactivate),
   which is what actually restores login access if the account was
   disabled, not just given a new password.

Both modes enforce the same password policy as the web routes (8-32
characters; letters, digits, and ! @ # $ ^ _ - only).

USAGE
-----
Run from the project root (so `backend` resolves as a package):

    # Create a new account (interactive prompts for anything not passed):
    python -m backend.create_admin
    python -m backend.create_admin --username alice --password "..." --role admin
    python -m backend.create_admin --username bob --password "..." --role user

    # Reset an existing account's password (and optionally re-enable it):
    python -m backend.create_admin --reset-password --username alice --password "..."
    python -m backend.create_admin --reset-password --username alice --password "..." --reactivate

Exits with a non-zero status and a message on stderr on any failure
(username already taken / not found, password fails the policy check,
username/password left empty, etc).
"""
from __future__ import annotations

import argparse
import getpass
import sys
import uuid

from .config import get_settings
from .db import LibraryDB
from .models import User
from .security import hash_password, validate_password_policy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a user account or reset an existing user's password directly in the database."
    )
    parser.add_argument(
        "--reset-password", action="store_true",
        help="Reset an EXISTING user's password instead of creating a new account.",
    )
    parser.add_argument("--username", help="Username (prompted if omitted)")
    parser.add_argument("--password", help="Password (prompted securely if omitted)")
    parser.add_argument(
        "--role", default="admin", choices=["admin", "user"],
        help="Role for a NEW account (default: admin). Ignored with --reset-password.",
    )
    parser.add_argument(
        "--reactivate", action="store_true",
        help="With --reset-password: also re-enable the account if it was disabled. "
             "Without this, a disabled account stays disabled even after its password is reset.",
    )
    args = parser.parse_args()

    username = (args.username or input("Username: ")).strip()
    if not username:
        print("Username is required.", file=sys.stderr)
        sys.exit(1)

    password = args.password or getpass.getpass("Password: ")
    if not password:
        print("Password is required.", file=sys.stderr)
        sys.exit(1)

    policy_error = validate_password_policy(password)
    if policy_error:
        print(policy_error, file=sys.stderr)
        sys.exit(1)

    settings = get_settings()
    db = LibraryDB(settings.library_db_file)

    if args.reset_password:
        _reset_password(db, username, password, reactivate=args.reactivate)
    else:
        _create_user(db, username, password, role=args.role)


def _create_user(db: LibraryDB, username: str, password: str, role: str) -> None:
    if db.get_user_by_username(username):
        print(f"A user named '{username}' already exists. Use --reset-password to change their password instead.", file=sys.stderr)
        sys.exit(1)
    if db.get_role(role) is None:
        print(f"Role '{role}' does not exist in the database.", file=sys.stderr)
        sys.exit(1)
    if role == "admin" and db.count_users_by_role("admin") > 0:
        print("An admin account already exists -- only one is allowed at a time. Use --reset-password on the existing admin instead.", file=sys.stderr)
        sys.exit(1)

    user = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    db.create_user(user)
    print(f"Created {role} user '{username}'.")


def _reset_password(db: LibraryDB, username: str, password: str, reactivate: bool) -> None:
    user = db.get_user_by_username(username)
    if not user:
        print(f"No user named '{username}' exists.", file=sys.stderr)
        sys.exit(1)

    user.password_hash = hash_password(password)
    if reactivate and not user.is_active:
        user.is_active = True
    user.touch()
    db.update_user(user)
    # Deliberately not attempting to reach into a currently-running
    # server's in-memory token_version cache -- this script runs
    # standalone, outside that process. bump_token_version() below still
    # updates the DATABASE value directly, which is what the server falls
    # back to reading on a cache miss (e.g. after its next restart, or for
    # a user_id it hasn't seen yet this run) -- but an already-warm cache
    # entry in a currently-running server process won't notice until that
    # process restarts. Fine for the main use case (the account was
    # already logged out or disabled), but if the affected account has an
    # active session in a currently-running server, that old session may
    # remain valid until the server is restarted.
    db.bump_token_version(user.id)
    status_note = " and re-enabled" if reactivate and user.is_active else ""
    print(f"Password reset for '{username}'{status_note}.")


if __name__ == "__main__":
    main()
