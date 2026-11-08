"""Create (or reset the password of) an account from the command line - the password is typed hidden, never put on the command line.

    cd backend
    ..\\.venv\\Scripts\\python.exe tools\\create_user.py designer@adinn.com                 # role designer
    ..\\.venv\\Scripts\\python.exe tools\\create_user.py boss@adinn.com --role admin --name "Boss"
    ..\\.venv\\Scripts\\python.exe tools\\create_user.py designer@adinn.com --reset          # new password for an existing account

Uses the same database as the server (SIGNAGE_DATA, default backend/data/signage.db).
"""
from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("email")
    ap.add_argument("--role", choices=auth.ROLES, default="designer")
    ap.add_argument("--name", default="")
    ap.add_argument("--reset", action="store_true", help="set a new password for an existing account")
    args = ap.parse_args()

    email = args.email.strip().lower()
    if not auth._EMAIL_RE.match(email):
        print("That is not a valid email address.")
        return 1
    db.init_db()
    password = getpass.getpass(f"Password for {email} (at least {auth.MIN_PASSWORD} characters, hidden): ")
    if len(password) < auth.MIN_PASSWORD:
        print(f"Too short - at least {auth.MIN_PASSWORD} characters. Nothing was changed.")
        return 1
    if password != getpass.getpass("Type it again: "):
        print("The two entries differ. Nothing was changed.")
        return 1

    existing = db.get_user_with_hash(email)
    if args.reset:
        if not existing:
            print("No such account. Nothing was changed.")
            return 1
        db.update_user(existing["id"], {"password_hash": auth.hash_password(password)})
        db.delete_user_sessions(existing["id"])
        print(f"Password changed for {email}; they are signed out everywhere.")
        return 0
    try:
        user = db.create_user(email, args.name.strip(), auth.hash_password(password), args.role)
    except sqlite3.IntegrityError:
        print("That email already has an account (use --reset to change its password).")
        return 1
    print(f"Created {user['role']} account {email}.")
    if not db.count_active_admins():
        print("NOTE: there is no administrator yet, and the first-run setup form is now closed. Create one with:\n"
              "  python tools\\create_user.py <admin email> --role admin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
