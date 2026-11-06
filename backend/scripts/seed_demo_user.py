"""
scripts/seed_demo_user.py — Create (or reset) the pre-verified demo account.

    cd backend
    DEMO_ACCOUNT_PASSWORD='the password the README will publish' python scripts/seed_demo_user.py

Why this exists: the deployed demo has no access code any more, so a reviewer needs an account. Asking one
to give an email address and wait for a mail they cannot read — from a demo whose inbox nobody checks — is
a worse gate than the code it replaced. This creates one shared account, already confirmed, whose password
the README publishes: the same "publish the credential so a stranger can look at the product" deal the
access code made, but with a real identity behind it so the namespace, the rate limit, and the documents
belong to somebody.

The password is *never* a default in this file. It comes from `DEMO_ACCOUNT_PASSWORD`, and running without
it is an error rather than a fallback — a hard-coded password in a public repo is the secret that gets
brute-forced the week after it is written.

Idempotent by design, because it gets run once per redeploy: an existing address keeps its `user_id` (so
its documents survive) and gets a new hash and a confirmed flag.

Reads whatever `MONGODB_URI` points at, like the app itself — so seeding production means running it with
the production URI, and `DRY_RUN=1` says which cluster you are about to write to first.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# No credential defaults here, unlike scripts/serve_offline.py: this script's whole job is to write one real
# row into the real database, so silently substituting a placeholder URI would seed the wrong cluster.
# `Settings` reads `backend/.env` relative to the working directory, which is why the usage line says
# `cd backend` first.

from app.core.password import PasswordPolicyError, create_hash  # noqa: E402
from app.services import user_store  # noqa: E402

DEFAULT_EMAIL = "demo@documind.app"


def seed(email: str, password: str) -> dict:
    """Create or reset one confirmed account. Returns what happened, for the caller to print."""
    hashed = create_hash(password, email=email)
    existing = user_store.find_by_email(email)

    if existing:
        user_id = existing["user_id"]
        # The hash is rewritten even when it matches, which costs one Argon2 run and keeps "I forgot the
        # demo password" fixable by re-running the same command instead of reading the schema.
        user_store.store_password_hash(user_id, hashed)
        user_store.mark_verified(user_id)
        return {"action": "reset", "user_id": user_id, "email": existing["email"]}

    record = user_store.create_account(email=email, password_hash=hashed)
    user_store.mark_verified(record["user_id"])
    return {"action": "created", "user_id": record["user_id"], "email": record["email"]}


def main() -> int:
    password = os.environ.get("DEMO_ACCOUNT_PASSWORD", "").strip()
    if not password:
        print(
            "DEMO_ACCOUNT_PASSWORD is not set. Choose the password the README will publish, export it, and "
            "run this again — it is deliberately not a default in this file.",
            file=sys.stderr,
        )
        return 2

    email = os.environ.get("DEMO_ACCOUNT_EMAIL", DEFAULT_EMAIL).strip()
    if os.environ.get("DRY_RUN") == "1":
        # Which cluster, and whether the address is already taken. The URI is never printed: it carries
        # Atlas's password in the userinfo part of the authority.
        taken = user_store.find_by_email(email)
        print(f"would write {email} to the cluster in MONGODB_URI — {'reset' if taken else 'create'}")
        return 0

    try:
        result = seed(email, password)
    except PasswordPolicyError as exc:
        # The reason is safe to print; the password is not, and it never was in this code path.
        print(f"That password will not be accepted: {exc}", file=sys.stderr)
        return 2

    print(f"{result['action']} {result['email']} (user_id={result['user_id']}) — confirmed, ready to sign in")
    print(f"accounts in this database now: {user_store.count_accounts()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
