#!/usr/bin/env python3
"""Secret landing-point scan over the files this delivery ships.

Reports the file, the line, the rule that fired and the value LENGTH. It never
prints a value, so its own output cannot become the leak it looks for.

Rules:

  R1  ``KEY=value`` where KEY names a credential and the value is a literal.
  R2  a connection URL whose inline ``:password@`` component is a literal.
  R3  a credential harvested by R2 quoted somewhere else — the way a credential
      ends up pasted into a delivery report, where it is prose and no key name
      is nearby to trigger R1.
  R4  a JWT literal.
  R5  a provider key with a recognisable live/test prefix (Stripe, GitHub, AWS).
  R6  a credential-named key whose literal is one of the product's own
      ``_INSECURE_JWT_DEFAULTS``. That set is read from backend/app/config.py
      instead of copied here, so the rule follows the list the product already
      refuses rather than a snapshot of this file's prose. Two extraction forms
      are needed: ``KEY=value`` (already R1's shape) and a typed field default
      ``jwt_secret_key: str = "..."``, where R1's capture lands on the type
      annotation rather than the value. R6 never promotes to HIGH: the value is
      public by construction — it is the list the product itself refuses — so
      the answer is a product change, not a rotation, and the ``runtime ∩ test``
      promotion that R1 gets would call a documented default a deployed
      credential. What it does share with R1 is the lane split: runtime path →
      WEAK_LITERAL, test path → FIXTURE, so a test that pins the refused default
      does not inflate the runtime-debt count.
  R7  a *database* credential judged by identity rather than by entropy: the
      userinfo password of a connection URL that is a vendor default
      (``INSECURE_DB_DEFAULTS``) or is simply the account name spelled again,
      plus the container-init key form (``POSTGRES_PASSWORD=…``). R1 and R2
      cannot see this class at all — ``postgres`` is 8 characters, one
      character class, and a readable phrase, so every entropy and shape test
      in this file passes it. That is the gap the landing-point review was
      asked to close rather than re-confirm: a default that is readable is not
      a default that is inert, because it is exactly what a fresh install
      authenticates with. Tiers: a client-side URL in deployment material
      (compose file, ``deploy/``, workflow, ``*.sh``) → HIGH, because that is
      the credential a started service connects with; the container-init form →
      WEAK_LITERAL, because that value only sets a volume's own password; a
      test path → FIXTURE. A ``${VAR}`` userinfo segment is an indirection and
      is not reported — that is the shape the fix takes, and the positive
      controls below keep both halves honest.

Each R1/R2/R3 hit is then sorted into one of two tiers by the *shape* of the
value, because the two require different answers:

  HIGH         base64/hex/random-shaped, or carries a provider prefix. This is
               what a real generated credential looks like. Always exit 1, and
               no marker can opt a HIGH value out of being reported.
  WEAK_LITERAL long and mixed-class, but built from readable words with
               separators — e.g. a fixed signing key written as
               ``acceptance-test-secret-minimum-32-chars-long``. That is not a
               published secret, it is a hardening debt: it still signs tokens,
               so switching it to environment indirection invalidates them and
               is the owner's call, not a line the scanner may delete.

So the gate is red for the shape that means "rotate it now", and states the
other shape as counted debt instead of either hiding it or making the build
permanently red until someone switches the gate off. The per-tier counts for
this tree are not restated here because they rot on the next commit; they are
pinned as measured sets in backend/tests/test_secret_scan_gate.py, which fails
when the tree and the recorded sets disagree. ``--strict`` promotes
WEAK_LITERAL to exit 1 for release smoke, where the debt is meant to be zero.

The word "hex" above promised more than the first version delivered: a key
built from letters and digits has exactly two character classes, and the class
count used to be a floor of three, so every hex-shaped secret was passed over
in silence. Two classes are accepted now when the whole run is alphanumeric and
carries a digit, which is what separates a generated key from a prose default;
``KEY_ASSIGN_RE`` already limits R1 to credential-named keys, so a checksum or
image-digest field cannot reach this branch. A hardcoded default whose literal
has no digit at all (lowercase words around separators) stays invisible to that
entropy test by design, so it is not judged on entropy: R6 reports it when the
product itself already names the value as a default it refuses, and R7 reports
the database half of the same class from its own registry. What still is not
detected is a prose default in *neither* registry — closing that means growing
a refuse-list, not loosening a rule here.

Keys that name an ``http``-scheme endpoint (``*_URL``) are exempt from R1 — the
value is a public URL, not a credential. An inline password in such a value is
still caught by R2, so the exemption costs no coverage.
A line may opt out with a trailing ``# secret-scan: fixture`` comment; the
comment is the disclosure, and ``--list-optouts`` reports every line that uses
it so an opt-out cannot become invisible. Opt-outs are counted and printed, and
never reduce the HIGH tier silently — an opted-out line still appears in the
opt-out list.

Exit 0 = no HIGH (WEAK_LITERAL may be listed), 1 = HIGH present, or WEAK_LITERAL
present under ``--strict``, 2 = usage error, 3 = the tree could not be read.
"""
import argparse
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KEY_ASSIGN_RE = re.compile(
    r"""(?ix)
    ^\s*(?:export\s+)?
    ([A-Za-z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|API_KEY|ACCESS_KEY|
                    PRIVATE_KEY|CREDENTIAL|SALT|DSN)[A-Za-z0-9_]*)
    \s*[:=]\s*
    (.+)$"""
)

# keys that count things rather than hold credentials: prompt_tokens = 128
METRIC_KEY_RE = re.compile(r"(?i)(?:_tokens?$|^tokens$|tokens_used|count|length|size|ms$|seconds$)")

# keys that name a public endpoint: LSEG_OAUTH2_TOKEN_URL = "https://…"
# An inline password inside such a value is still caught by R2.
ENDPOINT_KEY_RE = re.compile(r"(?i)_url$")
HTTP_SCHEME_RE = re.compile(r"(?i)^https?://")

URL_RE = re.compile(
    r"(?i)\b(?:postgres(?:ql)?(?:\+\w+)?|mysql|redis|amqp|mongodb(?:\+srv)?|mssql|https?)://"
    r"[^\s:@/\"']+:[^\s@\"'/]+@"
)

JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")

# R7's registries. R1/R2 judge a literal by length and character class, so the
# one credential class that is always short and always readable — the vendor
# default database password — cannot be seen by them: `postgres` is 8 chars and
# a password that is simply the account name has one character class. Being
# readable is not evidence of being inert, so R7 judges identity, the way R6
# judges the product's own JWT refuse-list. A `${VAR}` userinfo segment is an
# indirection and stays out (is_placeholder), which is what distinguishes the
# fixed shape from the shipped one.
INSECURE_DB_DEFAULTS = frozenset({
    "postgres", "postgrespw", "password123", "123456", "oracle", "mysql",
    "admin", "root", "sa", "tiger", "db2inst1", "sybase", "informix",
})
# Keys that initialise a database password rather than connect with one.
DB_PW_KEY_RE = re.compile(r"(?i)(?:POSTGRES|MYSQL|MARIADB|MSSQL|ORACLE|DB|"
                          r"DATABASE|REDIS)_?(?:PASSWORD|PASSWD)$")

PROVIDER_RE = re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"
                         r"|\bwhsec_[A-Za-z0-9]{16,}\b"
                         r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
                         r"|\bAKIA[0-9A-Z]{16}\b")

OPTOUT_RE = re.compile(r"#\s*secret-scan:\s*fixture\s*$")

# R6's second extraction form. ``jwt_secret_key: str = "…"`` is a credential key
# followed by a type annotation, so KEY_ASSIGN_RE stops at the first colon and
# captures "str = \"…\"" — prose to every later test, and the site invisible.
TYPED_ASSIGN_RE = re.compile(
    r"""(?ix)
    ^\s*
    ([A-Za-z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|API_KEY|ACCESS_KEY|
                    PRIVATE_KEY|CREDENTIAL|SALT|DSN)[A-Za-z0-9_]*)
    \s*:\s*[A-Za-z_][\w.\[\]]*\s*=\s*(.+?)\s*,?$"""
)

# The product's own refuse-list, read from source so this file never carries a
# copy of it that can rot.
INSECURE_SET_RE = re.compile(r"_INSECURE_JWT_DEFAULTS\s*=\s*\{(.*?)\n\}", re.S)
QUOTED_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')
CONFIG_REL = os.path.join("backend", "app", "config.py")


def load_insecure_defaults(root=None):
    """(values, loaded) — ``loaded`` is False when the set could not be read.

    R6 stays off rather than reporting "no known defaults": an empty set read
    from an unreadable file would look exactly like a clean tree.
    """
    path = os.path.join(root or ROOT, CONFIG_REL)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return set(), False
    match = INSECURE_SET_RE.search(text)
    if not match:
        return set(), False
    values = {a or b for a, b in QUOTED_RE.findall(match.group(1))}
    return {v for v in values if v}, True


def typed_assign_value(line):
    m = TYPED_ASSIGN_RE.match(line.rstrip("\n"))
    if not m:
        return None, None
    value = m.group(2).strip().rstrip(",;")
    if value[:1] in "\"'" and value[:1] == value[-1:]:
        value = value[1:-1]
    return m.group(1), value

CHARSET_RE = re.compile(r"[A-Za-z0-9+/=_.:\-]{20,}")
ALNUM_RE = re.compile(r"[A-Za-z0-9]{20,}")
CLASS_RES = (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"[0-9]"),
             re.compile(r"[^A-Za-z0-9]"))

EXACT_PLACEHOLDERS = {"pass", "passwd", "password", "changeme", "secret", "xxx",
                      "yourpassword", "redacted", "masked", "placeholder", ""}

TEXT_EXT = (".py", ".md", ".sh", ".yml", ".yaml", ".toml", ".ini", ".cfg",
            ".txt", ".json", ".sql", ".ts", ".tsx", ".js", ".mjs")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", ".next", ".dev-runs",
             "__pycache__", "dist", "build", ".pytest_cache", "playwright-report",
             "coverage", ".turbo"}


def is_scannable(rel):
    base = os.path.basename(rel)
    if base.startswith(".env") and not base.endswith((".example", ".template", ".sample")):
        return True
    return rel.endswith(TEXT_EXT)


def is_template(rel):
    """A committed example/template documents names, not values."""
    return rel.endswith((".example", ".template", ".sample"))


def is_placeholder(value):
    """True when the value is scaffolding rather than a credential.

    Anchored on purpose: an unanchored "starts like a placeholder" test matches
    every string and silently turns the whole scanner into a no-op.
    """
    if value in EXACT_PLACEHOLDERS:
        return True
    if re.fullmatch(r"\s*", value):
        return True
    if re.fullmatch(r"\$\{?[A-Za-z0-9_]*\}?", value):
        return True                                   # ${ENV_REFERENCE}
    if re.fullmatch(r"(?:your|example|sample|changeme|change|placeholder|dummy|"
                    r"redacted|masked|todo|tbd|replace|insert|fill)[\w\-<>{}$./ ]{0,40}",
                    value, re.I):
        return True
    if re.fullmatch(r"<[^>]*>|\{\{.*?\}\}|%s", value):
        return True
    return False


def default_db_credential_reason(user, password):
    """Why this literal is a vendor default, or None when it is not one.

    The username only changes the *reason*, never the verdict: a password equal
    to the account name is the worst shape in the registry, because the login a
    fresh install documents is then guessable from the account list alone. Not
    extended to "any user, as long as the password repeats it" — measured on
    this tree, that would have promoted six ``test:test`` e2e fixtures to HIGH,
    which is the fires-too-much failure this gate already records.
    """
    if not password or is_placeholder(password):
        return None
    if password.lower() not in INSECURE_DB_DEFAULTS:
        return None
    if user and password == user:
        return "database account name reused as its own password"
    return "vendor default database password"


def is_secretish(value):
    """A generated-looking secret: long, credential charset, mixed classes."""
    if not value or is_placeholder(value):
        return False
    if not CHARSET_RE.fullmatch(value):
        return False
    if len(value) < 20:
        return False
    classes = sum(bool(r.search(value)) for r in CLASS_RES)
    if classes >= 3:
        return True
    # A hex or base36 key is letters+digits, so a rule that insists on three
    # classes skips every one of them. Requiring a digit keeps prose defaults
    # (lowercase words around separators) out, and KEY_ASSIGN_RE only ever
    # matches credential-named keys, so a checksum or image-digest field cannot
    # reach this branch. Both halves are positive controls in
    # backend/tests/test_secret_scan_gate.py.
    return classes == 2 and bool(ALNUM_RE.fullmatch(value)) and bool(re.search(r"\d", value))


BASE64_RE = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")
HEX_RE = re.compile(r"[0-9a-fA-F]{40,}")


def shape(value):
    """'opaque' when the literal is indistinguishable from a generated key.

    Generated credentials arrive as one unbroken base64 or hex run. A readable
    literal is built from words joined by separators, so it has several short
    runs. The distinction is what separates "rotate this now" from "harden
    later", and it is checked by a positive control in
    backend/tests/test_secret_scan_gate.py — without that control a typo in
    either pattern would silently downgrade every real secret.
    """
    if BASE64_RE.search(value) or HEX_RE.search(value):
        return "opaque"
    runs = [r for r in re.split(r"[^A-Za-z0-9]+", value) if r]
    if len(runs) >= 3:
        return "word-literal"
    return "opaque"


def lane(rel):
    """Where the value lives: a test fixture, or a path that configures a service.

    Deliberately narrow. ``verify_data_persistence.sh`` exports a key and then
    starts a stack, so it is a runtime path, not scaffolding — only files under
    a ``tests`` directory or named ``test_*.py`` get fixture benefit of the
    doubt, plus Playwright specs, which run against a stack someone else started
    and cannot be the thing a deployed service reads. A credential that really
    is deployed still shows up as HIGH, because the same value appears in a
    runtime file too and ``runtime ∩ test`` promotes it.
    """
    parts = rel.split("/")
    base = parts[-1].lower()
    if "tests" in parts or "test" in parts or "e2e" in parts:
        return "test"
    if base.startswith("test_") and base.endswith(".py"):
        return "test"
    if base.endswith((".spec.ts", ".spec.js", ".test.ts", ".test.tsx", ".test.js")):
        return "test"
    return "runtime"


def is_deployment_material(rel):
    """A file whose contents *are* the configuration a stack starts with.

    A test path gets fixture benefit of the doubt; a compose file, a deploy
    script or a workflow does not, because nothing rewrites what it ships.
    """
    base = os.path.basename(rel)
    return (rel.startswith("docker-compose") or rel.startswith("deploy/")
            or rel.startswith(".github/workflows") or base.startswith("Dockerfile")
            or base.endswith(".sh"))


def tracked_files(root=None):
    root = root or ROOT
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True,
                             text=True, check=True).stdout
        names = out.splitlines()
    except subprocess.CalledProcessError:
        names = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            names.extend(os.path.relpath(os.path.join(dirpath, f), root) for f in filenames)
    result = []
    for rel in names:
        path = os.path.join(root, rel)
        if not is_scannable(rel) or os.path.islink(path) or not os.path.isfile(path):
            continue
        result.append(rel)
    return sorted(result)


def read(rel, root=None):
    try:
        with open(os.path.join(root or ROOT, rel), encoding="utf-8", errors="strict") as fh:
            return fh.readlines()
    except (OSError, UnicodeDecodeError):
        return []


def inline_credentials(line):
    """``(user, password)`` for every URL in the line that carries userinfo.

    Both halves are needed: the shape this rule exists for is the account name
    reused as its own password, and a password-only view cannot see it.
    """
    out = []
    for m in URL_RE.finditer(line):
        try:
            parsed = urlparse(m.group(0))
        except ValueError:
            continue
        if parsed.password:
            out.append((parsed.username or "", parsed.password))
    return out


def inline_passwords(line):
    return [pw for _user, pw in inline_credentials(line)]


def _assign_value(raw):
    """The value part of a KEY=value line, quotes stripped.

    A TypeScript / YAML flow-map entry ends with a separator
    (``jwt_secret_key: 'ab..cd',``), and that trailing comma sits *outside* the
    quotes, so unquoting never runs and the value fails the charset test — the
    literal is then invisible to R1. The separator is dropped before the quote
    check for that reason.
    """
    m = KEY_ASSIGN_RE.match(raw.rstrip("\n"))
    if not m:
        return None, None
    value = m.group(2).strip().rstrip(",;")
    if value[:1] in "\"'" and value[:1] == value[-1:]:
        value = value[1:-1]
    return m.group(1), value


def scan(root=None, collect_optouts=False):
    findings = []
    optouts = []
    harvested = {}
    files = tracked_files(root)
    known_defaults = load_insecure_defaults(root)[0]
    # rule hits that carry a literal value are tiered after the walk, because
    # the tier depends on every lane a value appears in, not on one line.
    literal_hits = []

    for rel in files:
        template = is_template(rel)
        for lineno, raw in enumerate(read(rel, root), 1):
            line = raw.rstrip("\n")
            if OPTOUT_RE.search(line):
                optouts.append(f"{rel}:{lineno}")
                continue
            if JWT_RE.search(line):
                findings.append(("R4", rel, lineno, "JWT-shaped literal", 0, "HIGH"))
            if PROVIDER_RE.search(line):
                findings.append(("R5", rel, lineno, "provider key with a live/test prefix",
                                 0, "HIGH"))
            if template:
                continue

            key, value = _assign_value(line)
            if known_defaults:
                # Both assignment shapes, because the typed form is the one that
                # hides the product's own default from every other rule here.
                for k6, v6 in ((key, value), typed_assign_value(line)):
                    if (k6 and v6 and v6 in known_defaults
                            and not METRIC_KEY_RE.search(k6)
                            and not is_placeholder(v6)):
                        # Tiered by lane like R1, because "runtime debt" has to
                        # keep meaning "a site that configures a service": a test
                        # that pins the refused default is scaffolding, not debt.
                        tier = "FIXTURE" if lane(rel) == "test" else "WEAK_LITERAL"
                        findings.append(("R6", rel, lineno, k6, len(v6), tier))
            if key and value and not METRIC_KEY_RE.search(key):
                bare = value.split("#", 1)[0].strip()
                excluded = (ENDPOINT_KEY_RE.search(key) and HTTP_SCHEME_RE.match(bare))
                if is_secretish(bare) and not excluded:
                    literal_hits.append(("R1", rel, lineno, key, bare))

            for pw in inline_passwords(line):
                if is_secretish(pw):
                    literal_hits.append(("R2", rel, lineno, "inline password in a "
                                         "connection URL", pw))
                    harvested.setdefault(pw, f"{rel}:{lineno}")

            # R7: judged by identity, not by entropy, so it cannot be tiered by
            # the shape walk below — `postgres` reads as "opaque" there and would
            # be promoted in a test file and demoted nowhere.
            for user, pw in inline_credentials(line):
                reason = default_db_credential_reason(user, pw)
                if not reason:
                    continue
                if lane(rel) == "test":
                    tier = "FIXTURE"
                elif is_deployment_material(rel):
                    tier = "HIGH"
                else:
                    tier = "WEAK_LITERAL"
                findings.append(("R7", rel, lineno, reason, len(pw), tier))

            if key and DB_PW_KEY_RE.search(key) and not METRIC_KEY_RE.search(key):
                # Container-init side of the same word: this value *creates* a
                # fresh volume's password rather than authenticating to something
                # that already exists, so it is debt to point at, not an alarm.
                reason = default_db_credential_reason(None, value)
                if reason:
                    tier = "FIXTURE" if lane(rel) == "test" else "WEAK_LITERAL"
                    findings.append(("R7", rel, lineno,
                                     reason + " on the container-init side",
                                     len(value), tier))

    for pw, origin in harvested.items():
        for rel in files:
            if is_template(rel):
                continue
            for lineno, raw in enumerate(read(rel, root), 1):
                if OPTOUT_RE.search(raw):
                    continue
                if pw in raw and f"{rel}:{lineno}" != origin:
                    literal_hits.append(("R3", rel, lineno, f"same literal as {origin}", pw))

    lanes = {}
    for _rule, rel, _lineno, _key, value in literal_hits:
        lanes.setdefault(value, set()).add(lane(rel))

    for rule, rel, lineno, key, value in literal_hits:
        used = lanes[value]
        if shape(value) == "opaque":
            severity = "HIGH"
        elif "runtime" in used and "test" in used:
            # A value that a test asserts on *and* a service reads is a deployed
            # credential wearing a fixture's clothes.
            severity = "HIGH"
        elif "runtime" in used:
            severity = "WEAK_LITERAL"
        else:
            severity = "FIXTURE"
        findings.append((rule, rel, lineno, key, len(value), severity))

    if collect_optouts:
        return findings, optouts
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="secret landing-point scan")
    ap.add_argument("--root", default=None, help="tree to scan (default: this repository)")
    ap.add_argument("--list-optouts", action="store_true",
                    help="print every line that opted out, so an opt-out stays visible")
    ap.add_argument("--strict", action="store_true",
                    help="also fail on WEAK_LITERAL (release smoke default)")
    ap.add_argument("--all", action="store_true",
                    help="print every finding, not the first 40")
    args = ap.parse_args(argv)

    findings, optouts = scan(args.root, collect_optouts=True)
    tiers = {}
    for hit in findings:
        tiers[hit[5]] = tiers.get(hit[5], 0) + 1
    r6 = sum(1 for hit in findings if hit[0] == "R6")
    print(f"secret scan: {len(findings)} finding(s) — "
          f"HIGH {tiers.get('HIGH', 0)}, WEAK_LITERAL {tiers.get('WEAK_LITERAL', 0)}, "
          f"FIXTURE {tiers.get('FIXTURE', 0)}; "
          f"{len(set(optouts))} line(s) opted out as fixtures")
    if load_insecure_defaults(args.root)[1]:
        print(f"  R6 known-default landing points: {r6}")
    else:
        # Stated, not silent: an empty refuse-list and a clean tree look the
        # same in the finding count, which is the failure mode R6 exists to stop.
        print(f"  R6 OFF: no _INSECURE_JWT_DEFAULTS read from {CONFIG_REL} — "
              "known defaults were not checked in this scan")
    # R7's own line, because "HIGH 0" is what a round reported while two compose
    # files were handing out the vendor default DB password. A count the operator
    # has to go looking for is a count that gets read as zero.
    print(f"  R7 vendor-default database landing points: "
          f"{sum(1 for hit in findings if hit[0] == 'R7')}")
    if args.list_optouts:
        for where in sorted(set(optouts)):
            print(f"  OPTOUT {where}")
    shown = findings if args.all else findings[:40]
    for rule, rel, lineno, key, length, severity in sorted(shown):
        if severity == "FIXTURE" and not args.all:
            continue
        shown_len = f"(value length {length})" if length else "(pattern match)"
        print(f"  {severity}  {rule}  {rel}:{lineno}  {key}  {shown_len}")
    if len(findings) > len(shown):
        print(f"  ... {len(findings) - len(shown)} more (use --all)")
    if tiers.get("HIGH"):
        print("  Mask the literal, read it from the environment instead, and rotate the "
              "credential — editing the file does not un-publish it.")
        return 1
    if tiers.get("WEAK_LITERAL"):
        print(f"  WEAK_LITERAL: {tiers['WEAK_LITERAL']} readable credential literal(s) on a "
              "runtime path. Not a published secret, but they do sign tokens / open sessions, "
              "so replacing them with indirection invalidates live credentials — owner "
              "decision, tracked here rather than silenced.")
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
