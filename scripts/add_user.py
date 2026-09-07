"""Add an account to the sign-in store (docs/13 §1).

    uv run python scripts/add_user.py alice@example.com officer --name "Alice"

Prompts for the password twice and never takes it as an argument: a password on
a command line is in the shell history, in `ps` output while it runs, and in
whatever collects either.

The store is `docker/users.yaml`, which git ignores. Adding somebody takes
effect when the gateway restarts, which is deliberate friction on the file that
decides who gets in.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cio_common.auth import ROLES  # noqa: E402
from cio_common.users import UserStoreError, hash_password  # noqa: E402

STORE = ROOT / "docker" / "users.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("role", choices=sorted(ROLES))
    parser.add_argument("--name", default="", help="Shown in the workbench header.")
    parser.add_argument("--branch", default=None)
    parser.add_argument(
        "--member-id",
        default=None,
        help="Required for a member account: every member-facing tool reads it from the token.",
    )
    parser.add_argument("--store", default=str(STORE))
    parser.add_argument("--replace", action="store_true", help="Change an existing account's password.")
    args = parser.parse_args(argv)

    email = args.email.strip().lower()
    if args.role == "member" and not args.member_id:
        print("  a member account needs --member-id", file=sys.stderr)
        return 2

    store = Path(args.store)
    body = yaml.safe_load(store.read_text()) if store.is_file() else {}
    accounts = list((body or {}).get("accounts") or [])

    existing = next((a for a in accounts if str(a.get("email", "")).lower() == email), None)
    if existing and not args.replace:
        print(f"  {email} already has an account; pass --replace to change its password", file=sys.stderr)
        return 2

    password = getpass.getpass("  password: ")
    if password != getpass.getpass("  again: "):
        print("  they do not match", file=sys.stderr)
        return 2

    try:
        digest = hash_password(password)
    except UserStoreError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    entry = {
        "email": email,
        "role": args.role,
        "password_hash": digest,
        **({"name": args.name} if args.name else {}),
        **({"branch": args.branch} if args.branch else {}),
        **({"member_id": args.member_id} if args.member_id else {}),
    }
    accounts = [a for a in accounts if str(a.get("email", "")).lower() != email]
    accounts.append(entry)

    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        "# Who may sign in (docs/13 §1). Git-ignored: this file decides access.\n"
        "# Managed by scripts/add_user.py. Restart the gateway after a change.\n"
        + yaml.safe_dump({"accounts": accounts}, sort_keys=False)
    )
    store.chmod(0o600)

    print(f"\n  {'changed' if existing else 'added'} {email} as {args.role} in {store}")
    print("  restart the gateway for it to take effect: make up\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
