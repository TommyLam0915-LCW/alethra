"""Local-only passwordless access for the disposable candidate stack.

This is a trial-entry convenience, not an authentication replacement. It issues
a normal short-lived JWT for one of the synthetic accounts the candidate profile
lists, so ownership, scopes, consent, quota, kill switch and broker containment
all still apply to that identity. Passwords and hashes are left untouched.

Three guards keep the bypass local and allowlist-only:
  * Settings refuses to boot with the mode on outside a local development
    environment (see ``Settings._validate_candidate_passwordless``);
  * only a loopback TCP peer is accepted -- client-supplied Host and
    X-Forwarded-* headers are never consulted;
  * the role must exist in the server-side allowlist, so a client can never
    pick an arbitrary user id or email.
"""
import re
from urllib.parse import urlsplit

ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

ROLE_LABELS_ZH = {
    "owner": "預設測試帳戶（已覆核決策）",
    "thin": "資料不足測試帳戶",
    "empty": "空白測試帳戶",
    "trained": "已訓練測試帳戶",
}

LOOPBACK_HOSTS = {"127.0.0.1", "::1"}

# The database URL is operator configuration, not a request header, so the
# conventional "localhost" spelling is accepted there as well. The TCP peer
# check keeps LOOPBACK_HOSTS strict (a socket peer is always an IP literal).
LOCAL_DATABASE_HOSTS = LOOPBACK_HOSTS | {"localhost"}


class CandidateAccessConfigError(ValueError):
    pass


def parse_account_allowlist(raw: str) -> dict[str, str]:
    """Parse ``role=email,role=email`` into an ordered role -> email map."""
    accounts: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        role, separator, email = item.partition("=")
        role = role.strip()
        email = email.strip().lower()
        if not separator or not role or not email:
            raise CandidateAccessConfigError(
                "CANDIDATE_PASSWORDLESS_ACCOUNTS entries must look like role=email"
            )
        if not ROLE_PATTERN.match(role):
            raise CandidateAccessConfigError(f"invalid candidate role {role!r}")
        if "@" not in email:
            raise CandidateAccessConfigError(f"invalid candidate email for role {role!r}")
        if role in accounts:
            raise CandidateAccessConfigError(f"duplicate candidate role {role!r}")
        accounts[role] = email
    if not accounts:
        raise CandidateAccessConfigError(
            "CANDIDATE_PASSWORDLESS_MODE requires CANDIDATE_PASSWORDLESS_ACCOUNTS"
        )
    return accounts


def role_label(role: str) -> str:
    return ROLE_LABELS_ZH.get(role, role)


def is_loopback_host(host: str | None) -> bool:
    return (host or "") in LOOPBACK_HOSTS


def is_local_database_url(url: str) -> bool:
    """A sqlite file, a loopback PostgreSQL URL, or a Unix socket connection is local."""
    raw = (url or "").strip()
    if not raw:
        return False
    if raw.startswith("sqlite"):
        return True
    cleaned = re.sub(r"\+\w+", "", raw, count=1)
    parsed = urlsplit(cleaned)
    if parsed.scheme not in ("postgresql", "mysql"):
        return False
    # Unix socket: hostname is None/empty and host query param is a path
    if not parsed.hostname:
        from urllib.parse import parse_qs
        query_params = parse_qs(parsed.query)
        socket_host = query_params.get("host", [None])[0]
        # Unix socket paths start with / or are empty (default socket)
        if socket_host is None or socket_host.startswith("/"):
            return True
    return (parsed.hostname or "") in LOCAL_DATABASE_HOSTS
