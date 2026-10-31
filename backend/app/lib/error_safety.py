"""Keep internal exception text out of user-facing surfaces.

Two channels expose raw exception text to a person:

* provider failures embedded in an HTTP 503 body, and
* job/run failures persisted as ``error_details`` and rendered verbatim in
  the UI.

A raw ``str(exc)`` can carry absolute filesystem paths, a database URL with a
password, or a bearer token. The full exception still goes to the server log;
what reaches a user is this bounded, path-free summary.
"""

import re

_DSN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s'\"]+")
_ABS_PATH = re.compile(
    r"(?:[A-Za-z]:)?/(?:Users|home|opt|var|private|tmp|root|usr|etc)/[^\s'\":,;)\]]*"
)
# Credential names are routinely prefixed (``api_token``, ``access_token``,
# ``client_secret``, ``apiToken``). A bare ``\btoken\b`` misses those because
# the preceding underscore or letter is a word character, so the name pattern
# allows a prefix and the whole name is kept while only the value is removed.
_SECRET_NAME = (
    r"api[_-]?key|secret|password|passwd|passphrase|token|authorization"
    r"|credential|client[_-]?secret"
)
_SECRET_ASSIGN = re.compile(
    rf"(?i)\b([A-Za-z0-9_.-]*(?:{_SECRET_NAME}))\b\s*[=:]\s*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s'\"]+")


def scrub_text(text: str, max_len: int = 200) -> str:
    """Redact paths, DSNs and credentials from arbitrary text."""
    text = text.strip()
    text = _DSN.sub("<redacted-url>", text)
    text = _ABS_PATH.sub("<path>", text)
    # Bearer first: ``Authorization: Bearer <tok>`` would otherwise be eaten by
    # the assignment rule up to the first space, hiding the scheme from it and
    # leaving the token itself in place.
    text = _BEARER.sub(lambda m: f"{m.group(1)}<redacted>", text)
    text = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def safe_error_message(exc: BaseException, max_len: int = 200) -> str:
    """Return a short, path-free summary of ``exc`` safe to show a user."""
    return scrub_text(str(exc) or type(exc).__name__, max_len=max_len)


def safe_provider_status(status: dict) -> dict:
    """Copy of a provider status dict with its free-text error scrubbed.

    Providers stash the last upstream error (``last_error``) and return it from
    ``get_status``, which is embedded in 503 bodies. That string is raw
    exception text, so it needs the same treatment as the error message.
    """
    clean = dict(status)
    if isinstance(clean.get("last_error"), str):
        clean["last_error"] = scrub_text(clean["last_error"])
    return clean
