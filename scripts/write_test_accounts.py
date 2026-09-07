"""Create the demo accounts and the file the drills sign in with.

    uv run python scripts/write_test_accounts.py

Writes two files, both git-ignored:

  docker/users.yaml          the store the gateway reads: hashes, no passwords
  docker/test-accounts.json  role -> {email, password}, for the drills

The second holds passwords in plain text, which is the point and the risk. It
exists so `scripts/dev_token.sh` keeps working once `/api/auth/dev-token` is
refused, and with it every drill, harness run and browser test. It sits next to
the store it was generated from, at the same trust level, on a machine that
already holds both.

Do not put it on a deployment nobody drills against, and do not reuse these
accounts for people: they are for the scripts. Real accounts are
`scripts/add_user.py`, which never writes a password anywhere.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cio_common.users import hash_password  # noqa: E402

#: One account per role the drills and the run-book use.
PEOPLE: tuple[tuple[str, str, str], ...] = (
    ("officer@cio.demo", "officer", "Credit Officer"),
    ("senior@cio.demo", "senior_officer", "Senior Officer"),
    ("collections@cio.demo", "collections", "Collections"),
    ("manager@cio.demo", "manager", "Manager"),
    ("compliance@cio.demo", "compliance", "Compliance"),
    ("committee@cio.demo", "committee", "Credit Committee"),
    ("head.credit@cio.demo", "head_of_credit", "Head of Credit"),
    ("head.risk@cio.demo", "head_of_risk", "Head of Risk"),
    ("system@cio.demo", "system", "System"),
    ("member@cio.demo", "member", "Member"),
)

#: Ordinary words, four of them and a number. Long enough to be strong and
#: short enough to read down a phone line, which is how one of these will
#: actually be given to somebody.
WORDS = [
    "amber",
    "birch",
    "cobalt",
    "dune",
    "ember",
    "fern",
    "gale",
    "harbour",
    "ivory",
    "juniper",
    "kelp",
    "larch",
    "marble",
    "nectar",
    "onyx",
    "pewter",
    "quarry",
    "rowan",
    "slate",
    "thistle",
    "umber",
    "verge",
    "willow",
    "yarrow",
]


def phrase() -> str:
    return "-".join(secrets.choice(WORDS) for _ in range(4)) + "-" + str(secrets.randbelow(90) + 10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--member-id",
        default=None,
        help="Which member the member account is. Found from the core when not given.",
    )
    parser.add_argument("--store", default=str(ROOT / "docker" / "users.yaml"))
    parser.add_argument("--accounts", default=str(ROOT / "docker" / "test-accounts.json"))
    parser.add_argument("--print", action="store_true", help="Print the passwords.")
    parser.add_argument(
        "--shared-password",
        action="store_true",
        help=(
            "One password for every demo account, so a reader can pick a role, "
            "type it once and switch freely. A deliberate demo trade-off: it "
            "means one leaked password is all of them."
        ),
    )
    args = parser.parse_args(argv)

    member_id = args.member_id or _a_member_with_history()
    if not member_id:
        print("  could not find a member with an application and an account", file=sys.stderr)
        print("  pass --member-id, or run 'make seed' first", file=sys.stderr)
        return 1

    accounts: list[dict[str, str]] = []
    plain: dict[str, dict[str, str]] = {}
    shared = phrase() if args.shared_password else None
    for email, role, name in PEOPLE:
        password = shared or phrase()
        entry = {"email": email, "role": role, "name": name, "password_hash": hash_password(password)}
        if role == "member":
            entry["member_id"] = member_id
        accounts.append(entry)
        plain[role] = {"email": email, "password": password}

    store = Path(args.store)
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        "# Who may sign in (docs/13 §1). Git-ignored: this file decides access.\n"
        "# Written by scripts/write_test_accounts.py. Restart the gateway after a change.\n"
        + yaml.safe_dump({"accounts": accounts}, sort_keys=False)
    )
    store.chmod(0o600)

    accounts_file = Path(args.accounts)
    accounts_file.write_text(json.dumps(plain, indent=2, sort_keys=True) + "\n")
    accounts_file.chmod(0o600)

    print(f"\n  {len(accounts)} accounts in {store}")
    print(f"  passwords for the drills in {accounts_file}")
    print(f"  the member account is {member_id}")
    if shared:
        print(f"  one password for every role: {shared}")
    print()
    if args.print:
        for role, who in sorted(plain.items()):
            print(f"    {role:<16} {who['email']:<24} {who['password']}")
        print()
    print("  restart the gateway for it to take effect: make up\n")
    return 0


def _a_member_with_history() -> str | None:
    """A member who has both an application and a facility, so the assistant
    has something to answer about."""
    import os
    import subprocess

    query = (
        "SELECT a.member_id FROM core.application_ext a "
        "JOIN core.account ac ON ac.member_id = a.member_id LIMIT 1;"
    )
    argv = [
        "docker",
        "compose",
        "--env-file",
        "docker/.env",
        "-f",
        "docker/compose.yaml",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        os.environ.get("POSTGRES_USER", "cio"),
        "-d",
        os.environ.get("POSTGRES_DB", "cio"),
        "-tAc",
        query,
    ]
    try:
        out = subprocess.run(  # noqa: S603 - a fixed argv; the statement is this file's own
            argv, check=False, capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


if __name__ == "__main__":
    raise SystemExit(main())
