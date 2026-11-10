#!/usr/bin/env python3
"""Rotate the isolated Alethra test database credential.

The replacement is written only to the gitignored, 0600 `backend/.env.local`.
Nothing secret is printed: each value is identified by a short SHA-256
fingerprint prefix so a run can be reconciled against history without
re-publishing the credential.

    set -a && . backend/.env.local && set +a
    python3 scripts/rotate_test_credential.py            # rotate in place
    python3 scripts/rotate_test_credential.py --verify   # check current value only

Retiring a leaked value does not remove it from git history. If a published
credential ever reached a shared or public remote, treat history cleanup as a
separate, explicitly approved operation — never a force push from here.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import psycopg
from psycopg import sql

ENV_PATH = Path(__file__).resolve().parent.parent / "backend" / ".env.local"

# Kept narrow on purpose: ALTER ROLE composes the value as a client-side
# literal (utility statements take no bind parameter), so the alphabet below is
# what guarantees no quoting hazard can reach the statement text.
TOKEN_ALPHABET = re.compile(r"[A-Za-z0-9_-]+\Z")


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def scrub(text: str, *secrets_to_hide: str) -> str:
    """Remove credential material from text destined for the terminal or a log.

    psycopg parses a conninfo URL by echoing it back in its error message, so a
    driver that rejects the SQLAlchemy-only ``postgresql+psycopg://`` scheme
    would otherwise print the password. Everything printed by this script goes
    through here.
    """
    for value in secrets_to_hide:
        if value:
            text = text.replace(value, "<REDACTED>")
    return text


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"FAIL: {path} not found. Create it with the keys listed in --help.")
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def write_env(path: Path, host: str, port: str, name: str, user: str, password: str) -> None:
    url = f"postgresql+psycopg://{user}:{quote(password, '')}@{host}:{port}/{name}"
    body = (
        "# Local-only secrets for the isolated Alethra test database.\n"
        "# Gitignored (.gitignore: .env.local). Mode 0600. Do not commit.\n"
        "# Source it with:  set -a && . backend/.env.local && set +a\n"
        "# Rotate with:     python3 scripts/rotate_test_credential.py\n"
        f"DATABASE_URL='{url}'\n"
        f"ALETHRA_TEST_DB_HOST='{host}'\n"
        f"ALETHRA_TEST_DB_PORT='{port}'\n"
        f"ALETHRA_TEST_DB_NAME='{name}'\n"
        f"ALETHRA_TEST_DB_USER='{user}'\n"
        f"ALETHRA_TEST_DB_PASSWORD='{password}'\n"
    )
    # 0600 from creation time, not a later chmod, so the value is never
    # momentarily group- or world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as handle:
        handle.write(body)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def connect(host: str, port: str, name: str, user: str, password: str):
    return psycopg.connect(host=host, port=int(port), dbname=name, user=user, password=password)


def verify_current(env: dict[str, str]) -> int:
    password = env["ALETHRA_TEST_DB_PASSWORD"]
    try:
        with connect(
            env["ALETHRA_TEST_DB_HOST"], env["ALETHRA_TEST_DB_PORT"],
            env["ALETHRA_TEST_DB_NAME"], env["ALETHRA_TEST_DB_USER"], password,
        ) as conn:
            user, dbname = conn.execute("SELECT current_user, current_database()").fetchone()
    except Exception as exc:
        print(f"verify=FAIL error={type(exc).__name__}")
        return 1
    print(f"verify=OK fp={fingerprint(password)} user={user} db={dbname}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="only test the stored credential")
    parser.add_argument("--env", default=str(ENV_PATH), help="path to the local env file")
    args = parser.parse_args()

    path = Path(args.env)
    env = load_env(path)

    if args.verify:
        return verify_current(env)

    old_password = env["ALETHRA_TEST_DB_PASSWORD"]
    new_password = secrets.token_urlsafe(32)
    if not TOKEN_ALPHABET.match(new_password):
        print("FAIL: generated token left the safe character class; aborting")
        return 1

    try:
        with connect(
            env["ALETHRA_TEST_DB_HOST"], env["ALETHRA_TEST_DB_PORT"],
            env["ALETHRA_TEST_DB_NAME"], env["ALETHRA_TEST_DB_USER"], old_password,
        ) as conn:
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(env["ALETHRA_TEST_DB_USER"]), sql.Literal(new_password)
                )
            )
            conn.commit()
    except Exception as exc:
        # An error here usually means the stored value is already retired, which
        # is the outcome rotation wants; say so instead of implying a new write.
        print(f"FAIL: rotate command raised {type(exc).__name__}; no env file rewritten")
        return 1

    print(f"rotate=OK old_fp={fingerprint(old_password)} new_fp={fingerprint(new_password)}")

    write_env(
        path, env["ALETHRA_TEST_DB_HOST"], env["ALETHRA_TEST_DB_PORT"],
        env["ALETHRA_TEST_DB_NAME"], env["ALETHRA_TEST_DB_USER"], new_password,
    )
    print(f"env_written={path} mode={oct(stat.S_IMODE(path.stat().st_mode))}")

    try:
        with connect(
            env["ALETHRA_TEST_DB_HOST"], env["ALETHRA_TEST_DB_PORT"],
            env["ALETHRA_TEST_DB_NAME"], env["ALETHRA_TEST_DB_USER"], new_password,
        ) as conn:
            user, dbname = conn.execute("SELECT current_user, current_database()").fetchone()
        print(f"new_auth=OK user={user} db={dbname}")
    except Exception as exc:
        print(f"new_auth=FAIL error={type(exc).__name__} — stored a value that cannot connect")
        return 1

    try:
        with connect(
            env["ALETHRA_TEST_DB_HOST"], env["ALETHRA_TEST_DB_PORT"],
            env["ALETHRA_TEST_DB_NAME"], env["ALETHRA_TEST_DB_USER"], old_password,
        ):
            print("old_auth=STILL_ACCEPTED — rotation did not take effect")
            return 1
    except psycopg.errors.InvalidPassword:
        print("old_auth=rejected(invalid_password)")
    except Exception as exc:
        raw = str(exc).strip().splitlines()
        detail = scrub(raw[-1].strip() if raw else "", old_password, new_password)
        print(f"old_auth=rejected({type(exc).__name__}) detail={detail}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
