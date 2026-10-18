#!/usr/bin/env python3
"""Generate setup text locally; never put passwords or tokens on the command line.

Run from the project folder: python scripts/generate_secrets.py
Copy the generated TOML into Streamlit's app Secrets settings. This helper
prints the generated encryption key and password hash only when you run it.
It does not write a secrets file, contact a service, or ask for a GitHub token.
"""

from __future__ import annotations

import getpass
import json
import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from cryptography.fernet import Fernet  # noqa: E402
from ccc.security import ROLES, hash_password, normalize_username  # noqa: E402


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_template(username: str, display_name: str, role: str, password_hash: str, key: str | None) -> str:
    """Build text from already generated credentials; no password is accepted."""
    account = "\n".join((f"[users.{_quote(username)}]", f"display_name = {_quote(display_name)}",
                           f"role = {_quote(role)}", f"password_hash = {_quote(password_hash)}", "enabled = true"))
    if key is None:
        return account + "\n"
    return f'''# Paste into Streamlit app Settings > Secrets. Never commit this text.
# Back up the encryption key in your organization's password manager.

[app]
session_timeout_minutes = 30

[github]
owner = "YOUR_GITHUB_OWNER"
repo = "YOUR_PRIVATE_DATA_REPOSITORY"
branch = "main"
path = "encrypted/ccc.enc"
token = "PASTE_FINE_GRAINED_GITHUB_TOKEN_HERE"

[encryption]
key = {_quote(key)}
previous_keys = []

{account}
'''


def main() -> int:
    if len(sys.argv) != 1:
        print("Run this helper without arguments; credentials are entered in hidden prompts.", file=sys.stderr)
        return 2
    print("Med-Peds CCC setup\nPasswords are entered privately; no GitHub token is requested.", file=sys.stderr)
    try:
        mode = input("Create first setup or add a user? [setup/user]: ").strip().lower()
        if mode not in ("setup", "user"):
            raise ValueError("Enter setup or user.")
        username = normalize_username(input("Account username (simple letters, numbers, dots, dashes): "))
        if not username:
            raise ValueError("Use a username of 2–64 simple characters.")
        display_name = input("Display name: ").strip()
        if not display_name or len(display_name) > 120 or any(ord(c) < 32 for c in display_name):
            raise ValueError("Use a short display name.")
        role = input("Role [admin/reviewer/viewer]: ").strip().lower()
        if role not in ROLES or (mode == "setup" and role != "admin"):
            raise ValueError("The first setup needs an admin; added users may have any listed role.")
        # Fail closed if a terminal cannot hide password input. getpass otherwise
        # warns and falls back to visible standard input, which is inappropriate.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("New passphrase (at least 14 characters): ")
            confirmation = getpass.getpass("Repeat passphrase: ")
        if password != confirmation:
            raise ValueError("The passphrases did not match. Run the helper again.")
        password_hash = hash_password(password)
        del password, confirmation
        key = Fernet.generate_key().decode("ascii") if mode == "setup" else None
        print("\nCopy the following into app Secrets. Keep the encryption key safe.\n", file=sys.stderr)
        print(build_template(username, display_name, role, password_hash, key))
        return 0
    except (EOFError, KeyboardInterrupt):
        print("Setup cancelled. No file was written.", file=sys.stderr)
        return 1
    except (ValueError, getpass.GetPassWarning) as exc:
        if isinstance(exc, getpass.GetPassWarning):
            message = "This terminal cannot hide password input. Run the helper in a normal local terminal."
        else:
            message = str(exc)
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
