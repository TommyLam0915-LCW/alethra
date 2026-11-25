"""One answer to "does this tree keep a credential in plain text?".

The trigger was ``backend/.env.test``. It is tracked by git, and its first
commit (``5af8521``) wrote a database URL with the password inline. The next
commit (``77e84ca``) replaced that password with
``${ALETHRA_TEST_RUNTIME_PASSWORD}``, so the current file is clean — but the
old line is still reachable from history, and nothing in the repository
noticed either way. ``.gitignore`` covers ``.env``, ``.env.local`` and
``.env.*.local`` and says nothing about ``.env.test``, which is why the file
could be committed at all.

A reminder in a document cannot catch the next one, so this module is the
check. Three rules, each with a scope chosen so that it cannot be noise:

* ``credential_literal`` — a credential-shaped key assigned a literal value
  **in a file that configures the running application**: ``.env*`` files, shell
  entry points and compose/yaml environment blocks. Python source is excluded
  on purpose: ``password_hash=get_password_hash(...)`` is a function argument,
  and a gate that reports 159 of those is a gate nobody reads.
* ``literal_url_password`` — ``scheme://user:pass@host`` with an inline
  password in the same scope, which is how the original defect was written.
* ``provider_token`` — a live-format provider key (Stripe/OpenAI/GitHub/AWS…)
  or a signed JWT, searched in **every** tracked text file. The shape alone
  makes the call, so this rule has no scope problem: a real key is a real key
  wherever it appears, including inside a template that was published
  deliberately.
* ``credential_default`` — a credential-shaped name with a literal standing
  behind it as the *default*: ``process.env.X_PASSWORD || "<literal>"`` and
  ``os.environ.get("X_PASSWORD", "<literal>")``. Searched in every tracked text
  file for the same reason as the rule above, and it is a different defect from
  ``credential_literal``: an assignment states a value, a default states that
  the program **still runs** when the operator forgets to inject one. The
  frontend acceptance specs are exactly that shape, which is why they were
  invisible while the assignment rule was limited to configuration files.

Accepted instead of reported: an empty value, a ``${VAR}`` / ``${VAR:-…}`` /
``${VAR:?…}`` / ``$VAR`` injection, a ``$(…)`` value generated at run time, and
a word that says it is a placeholder (``your-secret-key``, ``change-me…``,
``xxx``, ``<token>``). The line the vocabulary draws is *instruction versus
credential*: a value that tells you to substitute something is an instruction,
while a value you could log in with is a credential even if everybody knows it.
So ``postgres``, ``password``, ``secret``, ``admin`` and ``alethra`` are not on
the list — they are the strings a reader might actually be told to use, and each
one works as a password.

Findings carry path, line, key name, a kind and a content digest. The value is
never read into a finding's text, so running this tool -- or pasting its output
into a log, an issue or a test failure -- cannot leak what it looks for. The
length used to be printed and is not: for a four-character default password a
character count is the first thing an attacker already knows.

What the scanner supports, and what it cannot see:

* assignment syntax on one line, in ``.env*`` and compose/yaml environment
  blocks. A credential assembled across lines, built by string concatenation,
  or read from a file at run time is outside its grammar and is not reported.
* the inline ``scheme://user:pass@host`` form, and provider-key and JWT shapes
  in any tracked text file. A key that has been base64'd, encrypted or split
  into variables is not recognised by shape.
* a default that is a *literal* at the ``getenv(… , X)`` / ``X || "…"`` position.
  A fallback computed by a call, built across lines, or moved behind a name
  (``const P = "…"; env.X || P``) has no literal at the operator and is not
  reported. That limit is also why the contract tests assemble their refused
  lines from constants instead of writing them out: this repository is what the
  scanner reads, so a fixture left in the open becomes a real occurrence of the
  debt the ledger counts.
* tracked files (``git ls-files``) by default, and with ``--pre-commit`` the
  files a commit would actually carry: the index **plus** everything untracked
  and not ignored (``git ls-files --others --exclude-standard``). The second
  scope exists because the first has a specific hole -- a new
  ``backend/migrations/versions/x.py`` or ``backend/app/api/y.py`` that has not
  been ``git add``-ed yet is invisible to ``ls-files`` and is exactly the file
  that gets a secret into the first commit. An *ignored* untracked file (a real
  ``.env`` on a laptop, a build artifact, anything in ``node_modules``/venv) is
  still not scanned, and neither is a file force-added past an ignore rule:
  those are only visible once they enter the index, at which point the default
  scope sees them. Binary files and files over 2 MB are skipped in both scopes.
* **not git history.** This answers "what does the tree say now". A password
  removed from a later commit is still reachable from the commit that wrote it
  (``backend/.env.test`` at ``5af8521``, whose value class is UNKNOWN here),
  and only history rewriting addresses that. It is a separate, owner-approved
  operation, not a finding this tool can withdraw or downgrade.

Exit codes: 0 when the scan equals ``TRACKED_DEBT`` exactly, 1 when git could
not answer (an unreadable tree is never a pass), 2 when the tree and the ledger
disagree — a new literal, a ledgered position now holding different text, a
ledger entry that has been fixed but not deleted, or an occurrence count that
moved.

NOT RUN against PostgreSQL or any network: this reads the working tree only.
It starts no service, opens no socket and writes nothing.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote

SECRET_KEY_SHAPE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|API_?KEY|ACCESS_KEY|PRIVATE_KEY|"
    r"AUTH_TOKEN|BEARER_TOKEN|CREDENTIAL|SIGNING_KEY)"
)
# The fallback-default rule accepts a bare ``TOKEN`` too. The assignment rule
# above does not, because ``TOKEN_COUNT=12`` and ``X_TOKENS=[…]`` are
# configuration; a *literal standing behind* ``||`` has nowhere harmless to be.
CREDENTIAL_NAME_SHAPE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|API_?KEY|ACCESS_KEY|PRIVATE_KEY|TOKEN|CREDENTIAL|SIGNING_KEY)"
)
ENV_GET_DEFAULT = re.compile(
    r"""(?:environ\.get|getenv)\(\s*["']([A-Za-z0-9_]+)["']\s*,\s*["']([^"']+)["']"""
)
OR_DEFAULT = re.compile(
    r"""([A-Za-z0-9_]+)\s*\|\|\s*["']([^"']+)["']"""
)
NUMERIC_VALUE = re.compile(r"^[\d.]+$")
# ``${VAR}`` and every shell default/error form — ``${VAR:-d}``, ``${VAR-d}``,
# ``${VAR:?e}``, ``${VAR?e}``, ``${VAR:+a}`` — the value is injected by the
# caller, so the file carries no secret. ``:?`` additionally refuses to source,
# which is why the template uses it and backend/.env.test.example is checked
# for that form specifically.
TEMPLATE_REFERENCE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?::?[-+?=#][^}]*)?\}$")
ENV_REFERENCE = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$")
RUNTIME_GENERATED = re.compile(r"^(\$\(|`)")
REFERENCE_VALUE = re.compile(r"^\$\{?[A-Za-z_]")

# Strings that mean "substitute me", never a credential. A default password is
# not on this list on purpose: ``postgres``, ``password``, ``admin``, ``secret``
# and ``alethra`` are all values a reader might be told to use, and each one is
# a credential. Neither is ``change-this-to-…``: a compose file applies its
# value whether or not a human meant to change it, so those lines stay findings.
PLACEHOLDER_VALUE = re.compile(
    r"^(?:(?:your|my|sample|placeholder|example|dummy|fake)[_-][A-Za-z0-9_-]+|"
    r"(?:change|replace)[ _-]?(?:me|this|that)[A-Za-z0-9 _-]*|"
    r"(?:put|set|insert)[_-][A-Za-z0-9_-]+|"
    r"todo|tbd|t\.b\.d|none|null|<[^>]*>|\[[^\]]*\]|\{\{[^}]*\}\}|\*{3,}|x{3,}|0{3,})$",
    re.IGNORECASE,
)

PROVIDER_TOKEN_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("stripe", re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("stripe_publishable", re.compile(r"\bpk_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("stripe_webhook", re.compile(r"\bwhsec_[A-Za-z0-9]{16,}\b")),
    ("openai", re.compile(r"\bsk-[A-Za-z0-9_-]{28,}\b")),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("signed_jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)

# Extensions read at all, for the provider-token rule.
TEXT_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".zsh", ".ts", ".tsx", ".js", ".jsx", ".mjs",
     ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json", ".md",
     ".txt", ".example", ".tpl", ".template", ".service", ".unified", ".env"}
)
# Extensions whose *assignments* are application configuration. Shell scripts
# are deliberately absent: a script line is as likely to be a command, a
# heredoc or a fixture password handed to curl as it is to be environment
# configuration, and a rule that guesses wrong in either direction is worse
# than no rule. The script-side literals are inventoried instead in
# EXECUTION_RECORD_20261109.md §去敏紀錄 7 (acceptance JWT, 8 tracked files /
# 10 occurrences) and the same section's second class (test:test@…:5434).
SETTINGS_SUFFIXES = frozenset({".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env"})
SKIP_NAME_PARTS = ("node_modules", ".venv", "venv", "__pycache__", ".next")
MAX_FILE_BYTES = 2_000_000

ENV_STYLE_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
YAML_ASSIGNMENT = re.compile(r"^-?\s*([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")
YAML_ITEM_ASSIGNMENT = re.compile(r"^\s*-\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def is_injected(value: str) -> bool:
    """True when something else supplies the value at run time."""
    return bool(TEMPLATE_REFERENCE.match(value) or ENV_REFERENCE.match(value)
                or RUNTIME_GENERATED.match(value))


def digest(value: str) -> str:
    """A content binding for the ledger, not a disclosure of the value.

    The owner's objection to the old ledger was structural: an entry named only
    ``(path, key, kind)``, so any text written at that position was blessed --
    change ``POSTGRES_PASSWORD=postgres`` to a real password and the gate still
    said "matches the ledger". Binding the content closes that, and the binding
    is a hash because the alternative (a skeleton, or a length) is what the
    finding used to print and is exactly the guessable fingerprint that was
    withdrawn.

    What the hash reveals is bounded by what the repository already contains:
    every ledger entry is a literal sitting in a tracked file, so a dictionary
    that recovers the value from its digest is an attack on data the reader has
    already been handed. Nothing here is claimed for a value that is NOT in the
    tree -- which is why the ledger may only ever bind what ``scan`` returned.
    """
    return hashlib.sha256(b"alethra-env-hygiene-v1\x00" + value.encode("utf-8", "replace")).hexdigest()[:12]


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    key: str
    kind: str
    value_digest: str

    def __str__(self) -> str:
        # No length, no digest, no fragment: a scan is run to be pasted into
        # logs and issue threads, and the previous form printed the character
        # count of every credential it found.
        return f"{self.path}:{self.line}: {self.key} -> {self.kind}"


@dataclass(frozen=True)
class Analysis:
    files_scanned: int
    settings_files: int
    findings: Tuple[Finding, ...]
    scope: str = "tracked"

    @property
    def summary(self) -> str:
        counts: dict = {}
        for finding in self.findings:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
        detail = "none" if not counts else ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        return (
            f"{len(self.findings)} finding(s) across {self.files_scanned} {self.scope} text files "
            f"({self.settings_files} configuration files in assignment scope): {detail}"
        )


def tracked_files(repo_root: Path) -> List[str]:
    """Every path git tracks, or ``[]`` when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [p for p in result.stdout.split("\0") if p]


def _git_ls_files_z(repo_root: Path, *args: str) -> Optional[List[str]]:
    """One ``git ls-files -z`` call; ``None`` when git could not answer at all.

    ``None`` and ``[]`` are different answers and must not be merged: an empty
    list means git looked and found nothing, which is a fact about the tree,
    while no answer means the scan has no evidence and must not print a pass.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", *args],
            cwd=str(repo_root), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [p for p in result.stdout.split("\0") if p]


def untracked_files(repo_root: Path) -> Optional[List[str]]:
    """Files present but not in the index and not ignored -- ``None`` if git is silent."""
    return _git_ls_files_z(repo_root, "--others", "--exclude-standard")


def commit_candidate_files(repo_root: Path) -> Optional[List[str]]:
    """What a commit would carry now: the index plus untracked-not-ignored paths.

    Returns ``None`` when git could not answer either half, because a scan that
    silently lost one of the two sets reports "clean" over a tree it never read.
    """
    index = _git_ls_files_z(repo_root)
    others = untracked_files(repo_root)
    if index is None or others is None:
        return None
    return sorted(set(index) | set(others))


def _suffix(path: str) -> str:
    name = Path(path).name
    if name.startswith(".env"):
        return ".env"
    return Path(name).suffix.lower()


def is_text_file(path: str) -> bool:
    if any(part in SKIP_NAME_PARTS for part in Path(path).parts):
        return False
    return _suffix(path) in TEXT_SUFFIXES


def is_settings_file(path: str) -> bool:
    """A file whose assignments configure the running application."""
    return is_text_file(path) and _suffix(path) in SETTINGS_SUFFIXES


def effective_value(raw: str) -> str:
    """Drop the inline comment, surrounding quotes and padding around a value."""
    value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def assignments(line: str) -> List[Tuple[str, str]]:
    """Credential-shaped ``(key, raw value)`` pairs this line assigns."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return []
    out: List[Tuple[str, str]] = []
    for pattern in (ENV_STYLE_ASSIGNMENT, YAML_ASSIGNMENT, YAML_ITEM_ASSIGNMENT):
        match = pattern.match(stripped)
        if match:
            out.append((match.group(1), match.group(2)))
    return out


def _authority(value: str) -> str:
    """``user:pass@host:port`` of a URL, with ``${…}`` bodies kept whole.

    A plain ``split(\"/\")`` would cut inside a message such as
    ``${VAR:?set this before /deploy}`` and leave an unterminated reference
    behind, which the rule would then report as a literal password.
    """
    rest = value.split("://", 1)[1]
    depth = 0
    for index, char in enumerate(rest):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "/" and depth == 0:
            return rest[:index]
    return rest


def _url_password(value: str) -> Optional[str]:
    """The password text of ``scheme://user:pass@host``, or ``None``.

    Parsed by hand: ``urlsplit`` reads the ``?`` inside
    ``${VAR:?message}`` as the query delimiter and hands back half a
    reference, which would be reported as a literal password in a file that
    has no password at all.
    """
    if "://" not in value:
        return None
    authority = _authority(value)
    if "@" not in authority:
        return None
    credentials = authority.rsplit("@", 1)[0]
    if ":" not in credentials:
        return None
    # The first colon separates user from password. Splitting on the last one
    # would read ``user:${VAR:?message}`` as a password called ``?message``.
    password = unquote(credentials.split(":", 1)[1])
    if password == "" or TEMPLATE_REFERENCE.match(password) or ENV_REFERENCE.match(password) \
            or PLACEHOLDER_VALUE.match(password):
        return None
    return password


def fallback_defaults(line: str) -> List[Tuple[str, str]]:
    """Credential-shaped ``(name, literal)`` pairs this line uses as a default.

    An empty default (``getenv("X", "")``) is not a credential and the patterns
    require at least one character for that reason. What this cannot see is a
    default built at run time -- ``getenv("X", build_password())`` is a call, not
    a literal, and stays outside its grammar like every other multi-line form.
    """
    found: List[Tuple[str, str]] = []
    for pattern in (ENV_GET_DEFAULT, OR_DEFAULT):
        for match in pattern.finditer(line):
            name, value = match.group(1), match.group(2)
            if not CREDENTIAL_NAME_SHAPE.search(name.upper()):
                continue
            if is_injected(value) or PLACEHOLDER_VALUE.match(value) or NUMERIC_VALUE.match(value):
                continue
            if (name, value) not in found:
                found.append((name, value))
    return found


def classify(key: str, raw_value: str) -> List[Tuple[str, int]]:
    """Findings for one settings-file assignment; empty means the value is safe.

    The URL rule runs whatever the key is called: the defect that started this
    was a password inside ``DATABASE_URL``, and ``URL`` is not a
    credential-shaped word.
    """
    value = effective_value(raw_value)
    kinds: List[Tuple[str, str]] = []
    if _url_password(value) is not None:
        kinds.append(("literal_url_password", digest(value)))
    if SECRET_KEY_SHAPE.search(key.upper()) and value \
            and not is_injected(value) and not NUMERIC_VALUE.match(value) \
            and not PLACEHOLDER_VALUE.match(value):
        kinds.append(("credential_literal", digest(value)))
    return kinds


def scan_file(path: str, text: str) -> List[Finding]:
    findings: List[Finding] = []
    settings = is_settings_file(path)
    for number, line in enumerate(text.splitlines(), 1):
        for name, pattern in PROVIDER_TOKEN_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(Finding(path, number, "<inline>",
                                        f"provider_token:{name}", digest(match.group(0))))
        for name, value in fallback_defaults(line):
            findings.append(Finding(path, number, name, "credential_default", digest(value)))
        if not settings:
            continue
        for key, raw_value in assignments(line):
            for kind, value_digest in classify(key, raw_value):
                findings.append(Finding(path, number, key, kind, value_digest))
    return findings


def scan(repo_root: Path, paths: Optional[Sequence[str]] = None,
         scope: str = "tracked") -> Analysis:
    """Scan a file set, where ``scope`` names the set only ``paths`` does not give.

    The scope is part of the result rather than a detail of the call, because
    "no findings" over the index and "no findings" over the index plus the
    untracked files a commit would carry are two different claims, and the
    second is the one a pre-commit gate is allowed to print.
    """
    if paths is not None:
        candidates = list(paths)
    elif scope == "commit":
        enumerated = commit_candidate_files(repo_root)
        if enumerated is None:
            # No enumeration is not a clean tree: report zero files so the exit
            # code is the "git could not answer" one, never the "matches the
            # ledger" one.
            return Analysis(files_scanned=0, settings_files=0, findings=(), scope=scope)
        candidates = [p for p in enumerated if is_text_file(p)]
    else:
        candidates = [p for p in tracked_files(repo_root) if is_text_file(p)]
    findings: List[Finding] = []
    scanned = 0
    settings = 0
    for relative in candidates:
        target = repo_root / relative
        try:
            if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
                continue
            text = target.read_text(errors="replace")
        except OSError:
            continue
        scanned += 1
        settings += 1 if is_settings_file(relative) else 0
        findings.extend(scan_file(relative, text))
    return Analysis(files_scanned=scanned, settings_files=settings,
                    findings=tuple(findings), scope=scope)


# Credential literals git still tracks, measured 2026-11-17. Each one is an
# owner decision that is still open (EXECUTION_RECORD_20261109.md §去敏紀錄 7
# and §5 of the earlier round: rotate the role password, decide the acceptance
# JWT literal). They are listed instead of hidden so that (a) anything not on
# this list fails the gate, (b) an entry that has been fixed fails the gate
# until the line is deleted, and (c) a count that moves fails the gate. Fixing
# one of these is not this tool's job and needs its own approval: rewriting a
# compose file changes how the acceptance stack starts.
#   - ``postgres`` (the published default superuser password) written twice,
#     once as POSTGRES_PASSWORD and once inside the matching DATABASE_URL, in
#     each compose file — 4 entries.
#   - the acceptance lane's HS256 signing key literal in
#     ``docker-compose.acceptance.yml:30``, one of the 8 tracked files / 10
#     occurrences counted in §去敏紀錄 7.
#   - four ``credential_default`` entries added by this round's fourth rule: the
#     same two acceptance passwords standing as the fallback literal of
#     ``process.env.ACCEPTANCE_PASSWORD_[AB] || …`` in three e2e specs. Two
#     distinct values, four positions, so a fifth copy of either is reported
#     against the position it sits at and a rotation anywhere breaks every entry
#     that binds the same digest. Their consequence differs from the compose
#     files: each of those four lines is read straight into the password field
#     the spec submits to ``/login``, so a browser run that forgets to inject the
#     variable still authenticates -- with text from the source tree.
# Not listed, because the rule cannot call it a credential:
#   - ``docker-compose.yml:28`` JWT_SECRET_KEY whose value says ``change-me…``.
#     It is a placeholder phrase, not a leaked secret — but compose applies it
#     whenever the variable is not injected, so it is a fail-open default and is
#     recorded for the deployment section instead of hidden here.
#   - ``os.environ.get("…", "")`` defaults (four of them, measured the same
#     way). An empty default is the honest "unset means unset", which is why the
#     pattern requires a non-empty literal.
TRACKED_DEBT: Tuple[Tuple[str, str, str, int, str], ...] = (
    # (path, key, kind, occurrences, content digest) -- the last field is what
    # makes each entry a statement about *this text* rather than a standing
    # exemption for this path and key name. Generate them with
    # ``python3 backend/scripts/check_env_hygiene.py --emit-ledger``; never type
    # a digest, and never widen an entry to accommodate a changed value.
    ('docker-compose.acceptance.yml', 'DATABASE_URL', 'literal_url_password', 1, '52565aee4105'),
    ('docker-compose.acceptance.yml', 'JWT_SECRET_KEY', 'credential_literal', 1, '5f377d5d7bfb'),
    ('docker-compose.acceptance.yml', 'POSTGRES_PASSWORD', 'credential_literal', 1, 'efa1e5ece6f7'),
    ('docker-compose.yml', 'DATABASE_URL', 'literal_url_password', 1, '2044b89d40d1'),
    ('docker-compose.yml', 'POSTGRES_PASSWORD', 'credential_literal', 1, 'efa1e5ece6f7'),
    ('frontend/e2e/method-review.spec.ts', 'ACCEPTANCE_PASSWORD_A', 'credential_default', 1, 'd260f025af64'),
    ('frontend/e2e/research-drafts.spec.ts', 'ACCEPTANCE_PASSWORD_A', 'credential_default', 1, 'd260f025af64'),
    ('frontend/e2e/research-drafts.spec.ts', 'ACCEPTANCE_PASSWORD_B', 'credential_default', 1, '15124160c6f1'),
    ('frontend/e2e/strategy-preflight-feedback.spec.ts', 'ACCEPTANCE_PASSWORD_A', 'credential_default', 1, 'd260f025af64'),
)


def emit_ledger(analysis: Analysis) -> str:
    """The ledger literal this scan implies, as text a maintainer pastes once."""
    counts: dict = {}
    for finding in analysis.findings:
        key = (finding.path, finding.key, finding.kind, finding.value_digest)
        counts[key] = counts.get(key, 0) + 1
    lines = ["TRACKED_DEBT: Tuple[Tuple[str, str, str, int, str], ...] = ("]
    for (path, key, kind, value_digest), count in sorted(counts.items()):
        lines.append(f"    ({path!r}, {key!r}, {kind!r}, {count}, {value_digest!r}),")
    lines.append(")")
    return "\n".join(lines)


def reconcile(analysis: Analysis,
              debt: Tuple[Tuple[str, str, str, int, str], ...] = TRACKED_DEBT) -> dict:
    """Compare a scan with the debt ledger. Four ways to fail, none of them silent."""
    seen: dict = {}
    for finding in analysis.findings:
        key = (finding.path, finding.key, finding.kind, finding.value_digest)
        seen[key] = seen.get(key, 0) + 1
    expected = {(path, key, kind, value_digest): count for path, key, kind, count, value_digest in debt}
    ledgered_positions = {(path, key, kind) for path, key, kind, _d in expected}
    new = sorted(set(seen) - set(expected))
    resolved = sorted(set(expected) - set(seen))
    counts = sorted(k for k in set(seen) & set(expected) if seen[k] != expected[k])
    # A position the ledger already names, holding text the ledger does not:
    # the old four-field ledger had no way to say this, so it said "clean".
    changed = [k for k in new if k[:3] in ledgered_positions]
    new = [k for k in new if k not in changed]
    return {
        "clean": not new and not resolved and not counts and not changed,
        "new": new,
        "changed": changed,
        "resolved": resolved,
        "count_mismatch": [(k, seen[k], expected[k]) for k in counts],
        "ledger_total": sum(expected.values()),
        "found_total": sum(seen.values()),
        # Carried through so the sentence below can say *which* file set was
        # found clean; a pre-commit gate that prints "tracked" while it scanned
        # the index plus the untracked half would name the wrong evidence.
        "scope": analysis.scope,
    }


def verdict_text(verdict: dict) -> str:
    scope = verdict.get("scope", "tracked")
    if verdict["clean"]:
        return (
            f"matches the ledger: {verdict['found_total']} {scope} literal(s), "
            f"{verdict['ledger_total']} expected, no new credential"
        )
    parts = []
    if verdict["new"]:
        parts.append("NEW literals not in the ledger: "
                     + ", ".join(f"{p}:{k}({t})={d}" for p, k, t, d in verdict["new"]))
    if verdict["changed"]:
        parts.append("ledgered position now holds different text (the exemption "
                     "covers that exact value, not that key name): "
                     + ", ".join(f"{p}:{k}({t})={d}" for p, k, t, d in verdict["changed"]))
    if verdict["resolved"]:
        parts.append("ledger entries that no longer exist (delete them here): "
                     + ", ".join(f"{p}:{k}({t})={d}" for p, k, t, d in verdict["resolved"]))
    if verdict["count_mismatch"]:
        parts.append("occurrence counts moved: "
                     + ", ".join(f"{p}:{k}({t})={d} found={f} ledger={e}"
                                 for (p, k, t, d), f, e in verdict["count_mismatch"]))
    return "REFUSED — " + "; ".join(parts)


def main(argv: Iterable[str]) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    argv = list(argv)
    show_all = "--all" in argv
    # The default stays "tracked" so that a run without the flag keeps meaning
    # exactly what it meant before; --pre-commit is the scope that answers the
    # question a commit actually asks, and it is what the hook and CI pass.
    scope = "commit" if "--pre-commit" in argv else "tracked"
    analysis = scan(repo_root, scope=scope)
    if "--emit-ledger" in argv:
        # The ledger is generated from the scan so that the content bindings are
        # measurements. It prints the tree's current literals, which is a
        # disclosure act of its own: use it to replace the block, then leave the
        # file alone until an entry is genuinely fixed.
        print(emit_ledger(analysis))
        return 0
    verdict = reconcile(analysis)
    print(analysis.summary)
    print(verdict_text(verdict))
    shown = analysis.findings if show_all else analysis.findings[:40]
    for finding in shown:
        print(f"  {finding}")
    if len(analysis.findings) > len(shown):
        print(f"  ... {len(analysis.findings) - len(shown)} more (pass --all)")
    if not analysis.files_scanned:
        return 1
    return 0 if verdict["clean"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
