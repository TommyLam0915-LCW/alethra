"""The tracked settings surface must not gain a credential literal — decided, not described.

``backend/.env.test`` is tracked by git. Its first commit (``5af8521``) put a
database password inline; the next (``77e84ca``) replaced it with a
``${ALETHRA_TEST_RUNTIME_PASSWORD}`` injection, and that is still the shape in
HEAD today. Nothing noticed either way, because no code reads the file and no
test looked at it. These tests pin the three pieces that make the next
occurrence a failure instead of a commit:

* the settings template, with the same keys as the file it describes,
* the ignore rule that keeps a filled-in copy out of the index,
* and ``scripts/check_env_hygiene.py``, whose rules are exercised here on
  literal lines — including the exact shape of the original defect — so a pass
  means the decision logic refused something, not that the tree happened to be
  quiet.

The debt ledger is asserted from both directions: an entry that has been fixed
fails until it is deleted here, and an occurrence count that moves fails. No
test in this file starts a service or touches a database. Every ``git`` call
against *this* repository is a read command; the two write commands the scope
tests need (``init``, ``add``) run only inside pytest's own ``tmp_path``
directory, which is outside the project and deleted with it.
"""

import re
import subprocess
from pathlib import Path

import pytest

from scripts.check_env_hygiene import (
    ENV_STYLE_ASSIGNMENT,
    TRACKED_DEBT,
    Analysis,
    Finding,
    assignments,
    classify,
    commit_candidate_files,
    digest,
    effective_value,
    is_settings_file,
    is_text_file,
    reconcile,
    scan,
    scan_file,
    tracked_files,
    untracked_files,
    verdict_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

# A marker, never a credential. The refused line below is assembled rather than
# written out, because putting the refused shape into the tree as a literal
# would add the very occurrence this ledger exists to count.
USER = "alethra_test_runtime"
PASSWORD_MARKER = "Th1s-is-a-password-marker"
HOST = "disposable-db.internal"
NAME = "alethra_env_hygiene_contract"

DEFECT_SHAPE = f"postgresql://{USER}:{PASSWORD_MARKER}@{HOST}:5432/{NAME}"
ENV_TEST_KEYS = ("KRAKEN_API_KEY", "KRAKEN_API_SECRET", "KRAKEN_WS_URL")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60
    )


def _env_test_value_at(rev: str, key: str = "DATABASE_URL") -> str:
    show = _git("show", f"{rev}:backend/.env.test")
    assert show.returncode == 0, show.stderr
    line = [ln for ln in show.stdout.splitlines() if ln.startswith(f"{key}=")]
    assert len(line) == 1, f"{key} appears {len(line)} time(s) at {rev}"
    return line[0].split("=", 1)[1]


@pytest.fixture(scope="module")
def tree_scan() -> Analysis:
    return scan(REPO_ROOT)


# -- the decision logic, on literal lines -----------------------------------


def test_the_original_defect_shape_is_refused():
    """``5af8521`` wrote a password inside DATABASE_URL; that line still fails."""
    assert [kind for kind, _ in classify("DATABASE_URL", DEFECT_SHAPE)] == [
        "literal_url_password"
    ]


def test_a_credential_key_with_a_literal_value_is_refused():
    assert classify("POSTGRES_PASSWORD", PASSWORD_MARKER) == [
        ("credential_literal", digest(PASSWORD_MARKER))
    ]
    assert classify("JWT_SECRET_KEY", f'"{PASSWORD_MARKER}"')[0][0] == "credential_literal"


def test_injected_empty_and_numeric_values_are_accepted():
    accepted = [
        "${" + NAME.upper() + "}",
        "${" + NAME.upper() + ":?set it before sourcing}",
        "${" + NAME.upper() + "-" + PASSWORD_MARKER + "}",
        "$" + NAME.upper(),
        "$(" + NAME.lower() + " 32)",
        '""',
        "",
        "   ",
        "30",
    ]
    for value in accepted:
        assert classify("POSTGRES_PASSWORD", value) == [], value
    # A name that merely contains TOKEN is not a secret when the value is a number.
    assert classify("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30") == []


def test_default_passwords_are_not_treated_as_placeholders():
    """The word that tells you which account to use is still a credential."""
    for default in ("postgres", "password", "admin", "secret", "alethra", "test"):
        assert classify("POSTGRES_PASSWORD", default), default
    assert classify("POSTGRES_PASSWORD", "your-secret-key") == []
    assert classify("POSTGRES_PASSWORD", "xxx") == []


def test_urls_that_point_without_a_password_are_not_findings():
    assert classify("DATABASE_URL", f"postgresql://{USER}@{HOST}:5432/{NAME}") == []
    assert classify("DATABASE_URL", f"sqlite:///{NAME}.db") == []
    injected = f"postgresql://{USER}:${{{NAME.upper()}}}@{HOST}:5432/{NAME}"
    assert classify("DATABASE_URL", injected) == []
    # A message inside the braces must not be read as the password itself.
    fail_closed = f"postgresql://{USER}:${{{NAME.upper()}:?set it first}}@{HOST}:5432/{NAME}"
    assert classify("DATABASE_URL", fail_closed) == []
    defaulted = f"postgresql://{USER}:${{{NAME.upper()}-x}}@{HOST}:5432/{NAME}"
    assert classify("DATABASE_URL", defaulted) == []
    slashed = f"postgresql://{USER}:${{{NAME.upper()}:?set it before /deploy}}@{HOST}:5432/{NAME}"
    assert classify("DATABASE_URL", slashed) == []


def test_the_url_parse_follows_the_spec_it_needs_to():
    """Where the authority ends is a rule, not a guess, so state it in both directions.

    RFC 3986 keeps ``/`` out of userinfo (it must be percent-encoded), so the
    first ``/`` outside a ``${…}`` body ends the authority. A literal password
    that ignores that is a malformed URL and is outside what any parser can
    locate here — recorded rather than papered over. Nothing in this tree
    depends on that case: the ledger's two URL findings both use the canonical
    form.
    """
    malformed = f"postgresql://{USER}:{PASSWORD_MARKER}/more@{HOST}:5432/{NAME}"
    assert classify("DATABASE_URL", malformed) == []
    encoded = f"postgresql://{USER}:{PASSWORD_MARKER}%2Fmore@{HOST}:5432/{NAME}"
    assert classify("DATABASE_URL", encoded) == [
        ("literal_url_password", digest(encoded))
    ]


def test_an_instruction_is_a_placeholder_a_usable_password_is_not():
    """The line the vocabulary draws, in both directions."""
    for instruction in ("your-secret-key", "change-me-to-a-random-value",
                        "change-this-to-a-random-value", "replace_me_now",
                        "xxx", "<token>", "{{ jwt_secret }}", "your_api_key_here"):
        assert classify("JWT_SECRET_KEY", instruction) == [], instruction
    for usable in ("postgres", "password", "P@ssw0rd", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
                   PASSWORD_MARKER):
        assert classify("JWT_SECRET_KEY", usable), usable


def test_shell_and_python_lines_are_out_of_assignment_scope():
    """What made the first version useless: 159 of its 168 findings were code."""
    assert not is_settings_file("backend/app/api/auth.py")
    assert not is_settings_file("execute_journey_a_api.sh")
    assert not is_settings_file("backend/tests/conftest.py")
    assert is_settings_file("backend/.env.test")
    assert is_settings_file("docker-compose.yml")
    assert is_settings_file(".env.example")
    # A python keyword argument is not an environment assignment.
    assert scan_file("backend/app/api/auth.py", '    u = User(password_hash="x")') == []
    # Neither is a shell line that only names credential keys.
    assert scan_file("start_candidate_stack.sh", 'CREDENTIAL_KEYS="DATABASE_URL JWT_SECRET_KEY"') == []


def test_provider_token_shape_is_enough_anywhere_it_appears():
    line = "reference key " + "sk_live_" + "A" * 24
    for path in ("docs/notes.md", "backend/app/config.py", "frontend/src/lib/api.ts",
                 "docker-compose.yml", ".env.example"):
        findings = scan_file(path, line)
        assert [f.kind for f in findings] == ["provider_token:stripe"], path
    # Split across a concatenation it is not a key, and nothing is reported.
    assert scan_file("docs/notes.md", 'key = "sk_live_" + "A" * 24') == []


# The refused default, assembled. This file is part of what the scanner reads, so
# writing the shape out here would register a real occurrence of the debt these
# nodes are only meant to demonstrate.
PIPE_PIPE = chr(124) * 2


def _or_default(name: str, value: str) -> str:
    return f'{name} {PIPE_PIPE} "{value}"'


def _env_get(name: str, value: str) -> str:
    return f'os.environ.get("{name}", "{value}")'


def test_a_credential_default_is_reported_in_every_source_file():
    """A literal behind a default is what the program uses when nobody injects one.

    The two compose files were never the whole exposure. Three browser specs
    stand a password behind ``process.env.X || …`` and read it into the login
    form, and the assignment rule could not see them at all: a spec is source
    code, which is deliberately outside the assignment scope.
    """
    for path in ("frontend/e2e/a.spec.ts", "backend/scripts/tool.py",
                 "docker-compose.acceptance.yml", "docs/notes.md", "start_stack.sh"):
        for line in (_or_default("ACCEPTANCE_PASSWORD_A", PASSWORD_MARKER),
                     _env_get("CANDIDATE_DB_PASSWORD", PASSWORD_MARKER)):
            findings = scan_file(path, line)
            assert [f.kind for f in findings] == ["credential_default"], (path, line)
            assert findings[0].value_digest == digest(PASSWORD_MARKER), path


def test_a_default_that_is_not_a_credential_is_not_reported():
    """The accept-list on the new rule, so it cannot become a noise generator.

    An empty default means unset stays unset, a placeholder means substitute me,
    and a name with no credential word in it is ordinary configuration.
    """
    for line in (
        _env_get("CANDIDATE_DB_PASSWORD", ""),
        _or_default("CANDIDATE_DB_PASSWORD", ""),
        _env_get("CANDIDATE_DB_PASSWORD", "your-password-here"),
        _or_default("CANDIDATE_DB_PASSWORD", "change-me-before-running"),
        _or_default("RETRY_COUNT", PASSWORD_MARKER),
        _env_get("DATABASE_HOST", PASSWORD_MARKER),
    ):
        assert scan_file("backend/scripts/tool.py", line) == [], line
    # The same name with a usable literal behind it is reported.
    assert scan_file("backend/scripts/tool.py",
                     _env_get("CANDIDATE_DB_PASSWORD", PASSWORD_MARKER))


def test_a_credential_default_is_bound_to_its_position_as_strongly_as_the_rest():
    """A fifth copy is a finding even though its value is already exempted.

    The ledger exempts (path, key, kind, content). Repeating an exempted value in
    a file nobody looked at has to fail, or the rule that exists to stop the
    literal spreading would stop counting it the moment it spread.
    """
    known = Finding("frontend/e2e/a.spec.ts", 8, "ACCEPTANCE_PASSWORD_A",
                    "credential_default", digest(PASSWORD_MARKER))
    one = (("frontend/e2e/a.spec.ts", "ACCEPTANCE_PASSWORD_A", "credential_default",
            1, digest(PASSWORD_MARKER)),)
    assert reconcile(Analysis(10, 4, (known,)), debt=one)["clean"]
    copied = Analysis(10, 4, (known, Finding("frontend/e2e/b.spec.ts", 8,
                                             "ACCEPTANCE_PASSWORD_A",
                                             "credential_default", digest(PASSWORD_MARKER))))
    verdict = reconcile(copied, debt=one)
    assert not verdict["clean"]
    assert verdict["new"] == [("frontend/e2e/b.spec.ts", "ACCEPTANCE_PASSWORD_A",
                               "credential_default", digest(PASSWORD_MARKER))], verdict


def test_a_finding_never_carries_the_value_it_found():
    """Proof 5a: neither the finding, the classifier, the summary nor a refusal
    prints the value -- and no longer the character count either."""
    findings = [Finding("f.yml", 1, "POSTGRES_PASSWORD", "credential_literal", digest(PASSWORD_MARKER))]
    rendered = " ".join([
        str(findings[0]),
        str(classify("POSTGRES_PASSWORD", PASSWORD_MARKER)),
        Analysis(1, 1, tuple(findings)).summary,
        verdict_text(reconcile(Analysis(1, 1, tuple(findings)))),
    ])
    assert PASSWORD_MARKER not in rendered
    assert PASSWORD_MARKER not in str(tracked_files(REPO_ROOT))
    assert "len=" not in rendered, (
        "a length is the first thing an attacker already knows about a "
        "four-character default password: " + rendered
    )
    assert digest(PASSWORD_MARKER) not in str(findings[0]), str(findings[0])


def test_a_refusal_names_the_digest_without_naming_the_value():
    """Proof 5b: the refusal is auditable (which position, which content hash)
    without becoming a copy of the secret it refuses."""
    one = (("f.yml", "POSTGRES_PASSWORD", "credential_literal", 1, digest("postgres")),)
    verdict = reconcile(Analysis(10, 4, (
        Finding("f.yml", 1, "POSTGRES_PASSWORD", "credential_literal", digest(PASSWORD_MARKER)),
    )), debt=one)
    text = verdict_text(verdict)
    assert not verdict["clean"]
    assert PASSWORD_MARKER not in text, text
    assert digest(PASSWORD_MARKER) in text, text


def test_effective_value_drops_comments_and_quotes():
    assert effective_value(f" # {PASSWORD_MARKER}") == ""
    assert effective_value(PASSWORD_MARKER) == PASSWORD_MARKER
    assert effective_value(f'"{PASSWORD_MARKER}"') == PASSWORD_MARKER
    raw = assignments(f'export POSTGRES_PASSWORD="{PASSWORD_MARKER}" # inline')[0][1]
    assert raw == f'"{PASSWORD_MARKER}" # inline'
    assert classify("POSTGRES_PASSWORD", raw)[0][0] == "credential_literal"


# -- the ledger and this tree ------------------------------------------------


def test_git_answered_and_the_ledger_is_additive(tree_scan):
    assert tree_scan.files_scanned > 0, "git returned nothing; a silent scan cannot PASS"
    assert tree_scan.settings_files > 0, "no configuration file in scope means nothing was decided"
    verdict = reconcile(tree_scan)
    assert verdict["found_total"] == len(tree_scan.findings) == verdict["ledger_total"]
    assert sum(entry[3] for entry in TRACKED_DEBT) == verdict["ledger_total"]


def test_the_ledger_is_the_measurement_rather_than_a_remembered_list(tree_scan):
    """Every entry, content binding included, is what this tree produces.

    This is the only direction that closes the two ways a ledger rots: a typed
    digest that never came from a scan, and an exemption written wide enough to
    cover whatever is at that position next.
    """
    seen: dict = {}
    for finding in tree_scan.findings:
        key = (finding.path, finding.key, finding.kind, finding.value_digest)
        seen[key] = seen.get(key, 0) + 1
    assert seen == {(entry[:3] + (entry[4],)): entry[3] for entry in TRACKED_DEBT}


def test_no_new_credential_literal_in_the_tracked_tree(tree_scan):
    verdict = reconcile(tree_scan)
    assert verdict["new"] == [], verdict_text(verdict)
    assert verdict["changed"] == [], verdict_text(verdict)
    assert verdict["resolved"] == [], verdict_text(verdict)
    assert verdict["count_mismatch"] == [], verdict_text(verdict)
    assert verdict["clean"]


def test_the_ledger_names_every_position_the_tree_produces(tree_scan):
    assert {f.path for f in tree_scan.findings} == {
        "docker-compose.yml", "docker-compose.acceptance.yml",
        "frontend/e2e/method-review.spec.ts",
        "frontend/e2e/research-drafts.spec.ts",
        "frontend/e2e/strategy-preflight-feedback.spec.ts",
    }
    assert {entry[2] for entry in TRACKED_DEBT} == {
        "credential_literal", "literal_url_password", "credential_default"
    }


def test_the_four_default_positions_are_two_values(tree_scan):
    """The new entries bind content, and stay additive against the scan.

    Three specs repeat one password and one repeats the other. Written per
    position with a fresh digest each time, rotating the value would have left
    three exemptions behind that no longer named anything in the tree -- the same
    rot the content binding exists to prevent.
    """
    defaults = [entry for entry in TRACKED_DEBT if entry[2] == "credential_default"]
    found = [f for f in tree_scan.findings if f.kind == "credential_default"]
    assert len(defaults) == len(found) == 4, (defaults, found)
    assert len({entry[4] for entry in defaults}) == 2, [entry[4] for entry in defaults]
    assert sum(entry[3] for entry in defaults) == len(found)


def test_the_ledger_refuses_a_new_literal_and_a_stale_entry():
    """A ledger nobody can fail is decoration: all four directions are exercised."""
    known_digest = digest("postgres")
    one = (("docker-compose.yml", "POSTGRES_PASSWORD", "credential_literal", 1, known_digest),)
    known = Finding("docker-compose.yml", 8, "POSTGRES_PASSWORD", "credential_literal", known_digest)
    assert reconcile(Analysis(10, 4, (known,)), debt=one)["clean"]
    extra = Analysis(10, 4, (known, Finding(".env", 2, "JWT_SECRET_KEY", "credential_literal",
                                            digest("sk-live-something"))))
    assert reconcile(extra, debt=one)["new"] == [
        (".env", "JWT_SECRET_KEY", "credential_literal", digest("sk-live-something"))
    ]
    assert reconcile(Analysis(10, 4, ()), debt=one)["resolved"] == [
        ("docker-compose.yml", "POSTGRES_PASSWORD", "credential_literal", known_digest)
    ]
    assert not reconcile(Analysis(10, 4, ()))["clean"], (
        "an empty tree is not clean while the real ledger still expects "
        f"{sum(entry[3] for entry in TRACKED_DEBT)} literal(s)"
    )
    assert reconcile(Analysis(10, 4, (known, known)), debt=one)["count_mismatch"]


def test_changing_the_text_at_a_ledgered_position_is_not_still_exempted():
    """Proof 2: the exemption is this value, not this key name.

    The four-field ledger could not express this at all. Rotating
    ``POSTGRES_PASSWORD`` in place -- the single most likely future edit to a
    compose file -- produced the same ``(path, key, kind)`` the ledger already
    blessed, so the scan said "matches the ledger" about a credential the
    ledger had never seen.
    """
    old = digest("postgres")
    new = digest("a-rotated-password")
    one = (("docker-compose.yml", "POSTGRES_PASSWORD", "credential_literal", 1, old),)
    verdict = reconcile(Analysis(10, 4, (
        Finding("docker-compose.yml", 8, "POSTGRES_PASSWORD", "credential_literal", new),
    )), debt=one)
    assert not verdict["clean"]
    assert verdict["changed"] == [
        ("docker-compose.yml", "POSTGRES_PASSWORD", "credential_literal", new)
    ], verdict
    assert verdict["new"] == [], "a rotation is reported as changed content, not as a new key"
    assert verdict["resolved"] == [
        ("docker-compose.yml", "POSTGRES_PASSWORD", "credential_literal", old)
    ]
    assert "ledgered position now holds different text" in verdict_text(verdict)

    # And the same for the URL rule, where the password is only one field of the
    # bound value: nothing in the URL may change under an existing exemption.
    url_old = digest(f"postgresql://app:{old}@{HOST}:5432/{NAME}")
    url_new = digest(f"postgresql://app:{new}@{HOST}:5432/{NAME}")
    url_debt = (("docker-compose.yml", "DATABASE_URL", "literal_url_password", 1, url_old),)
    url_verdict = reconcile(Analysis(10, 4, (
        Finding("docker-compose.yml", 9, "DATABASE_URL", "literal_url_password", url_new),
    )), debt=url_debt)
    assert not url_verdict["clean"] and url_verdict["changed"], url_verdict

    # Proof 3 on the same path: the inline password is what made the line a
    # finding in the first place.
    assert [k for k, _v in classify("DATABASE_URL", DEFECT_SHAPE)] == ["literal_url_password"]


def test_an_injected_value_is_not_reported_as_a_literal():
    """Proof 4: the fail-closed input contract still distinguishes injection.

    A gate that cries wolf at ``${VAR}`` gets disabled inside a week, so the
    accepted forms are pinned here as the complement of the bound ledger: the
    ledger covers only literals this tree actually carries.
    """
    for line in (
        "POSTGRES_PASSWORD=${ALETHRA_PG_PASSWORD}",
        "POSTGRES_PASSWORD=${ALETHRA_PG_PASSWORD:?set it}",
        "POSTGRES_PASSWORD=$ALETHRA_PG_PASSWORD",
        "POSTGRES_PASSWORD=$(vault read -field=password db/role)",
        "POSTGRES_PASSWORD=",
    ):
        assert scan_file("docker-compose.temporary.yml", line) == [], line
    assert scan_file("docker-compose.temporary.yml", "POSTGRES_PASSWORD=postgres")[0].kind \
        == "credential_literal"


# -- the three pieces that prevent the next one ----------------------------


def test_template_mirrors_the_tracked_test_settings_file():
    template, live = BACKEND / ".env.test.example", BACKEND / ".env.test"
    assert template.is_file(), "backend/.env.test.example is the tracked half of the pair"

    def keys(path: Path):
        return [ENV_STYLE_ASSIGNMENT.match(ln.strip()).group(1)
                for ln in path.read_text().splitlines()
                if ENV_STYLE_ASSIGNMENT.match(ln.strip())]

    assert keys(template) == keys(live), "template and file drifted apart"
    assert scan(REPO_ROOT, paths=["backend/.env.test", "backend/.env.test.example",
                                  ".env.example"]).findings == ()


def _password_segment(url_value: str) -> str:
    """The password text between the first ``:`` and the last ``@`` of a URL."""
    credentials = url_value.split("://", 1)[1].split("@", 1)[0]
    assert ":" in credentials, credentials
    return credentials.split(":", 1)[1]


def test_template_injects_the_password_fail_closed():
    """``${VAR:?…}`` aborts the source instead of expanding to an empty password.

    Empty expansion is the recorded direct cause of the two
    ``test_safety_gate_negative.py`` failures (EXECUTION_RECORD_20261109.md
    §收斂補充 2): ``backend/.env.test`` carries the bare ``${VAR}`` form, so an
    unset variable became an empty password and a ``no password supplied``
    connection attempt. The template is the file a reader copies, so it is the
    one that has to use the form that refuses.
    """
    template = (BACKEND / ".env.test.example").read_text()
    value = [ln for ln in template.splitlines() if ln.startswith("DATABASE_URL=")][0].split("=", 1)[1]
    password = _password_segment(value)
    assert re.match(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:\?.*\}$", password), password
    assert classify("DATABASE_URL", value) == []
    for key in ENV_TEST_KEYS:
        assert re.search(rf"^{key}=$", template, re.M), key
    # The bare form still counts as injected, which is why HEAD is not a finding.
    head = _env_test_value_at("HEAD")
    assert _password_segment(head) == "${ALETHRA_TEST_RUNTIME_PASSWORD}"
    assert classify("DATABASE_URL", head) == []


def test_gitignore_keeps_a_filled_in_copy_out_of_the_index():
    """The rule exists; whether it yet applies is a separate measured fact."""
    ignored = _git("check-ignore", "-v", "--no-index", "backend/.env.test")
    assert ignored.returncode == 0, ignored.stdout + ignored.stderr
    assert ignored.stdout.strip().endswith(":.env.test\tbackend/.env.test"), ignored.stdout
    # The template has to stay committable, or the fix has nothing to point at.
    assert _git("check-ignore", "--no-index", "backend/.env.test.example").returncode == 1


def test_tracked_state_is_reported_as_measured_not_assumed():
    """``.gitignore`` is inert while the path is in the index — the comment says so."""
    tracked = _git("ls-files", "--", "backend/.env.test").stdout.strip() == "backend/.env.test"
    assert "inert" in (REPO_ROOT / ".gitignore").read_text().lower()
    if tracked:
        # Today: still in the index. Untracking is a git write and needs its own
        # approval; until then the rule protects only future copies. The live
        # file is still held to the literal rule.
        assert "backend/.env.test" not in {f.path for f in tree_scan_findings()}
    else:
        assert _git("check-ignore", "backend/.env.test").returncode == 0


def tree_scan_findings():
    return scan(REPO_ROOT, paths=["backend/.env.test"]).findings


def test_head_of_the_test_settings_file_still_injects_and_stays_blank():
    """Pinned against a "convenient" revert: the fix was the injection, not a value."""
    assert classify("DATABASE_URL", _env_test_value_at("HEAD")) == []
    head = _git("show", "HEAD:backend/.env.test").stdout
    for key in ENV_TEST_KEYS:
        assert re.search(rf"^{key}=$", head, re.M), f"{key} is no longer blank at HEAD"


def test_the_first_commit_of_that_file_did_carry_a_literal():
    """The reason this gate exists, kept as a measured fact rather than a story."""
    bad, good = _env_test_value_at("5af8521"), _env_test_value_at("77e84ca")
    assert classify("DATABASE_URL", bad) == [
        ("literal_url_password", digest(effective_value(bad)))
    ]
    assert classify("DATABASE_URL", good) == []
    assert "${ALETHRA_TEST_RUNTIME_PASSWORD}" in good


def test_an_unreadable_tree_is_never_reported_as_clean():
    """Two independent guards: the CLI checks the count, the ledger checks the debt."""
    empty = Analysis(files_scanned=0, settings_files=0, findings=())
    verdict = reconcile(empty)
    assert not verdict["clean"], "a scan that found nothing is a scan that read nothing"
    assert len(verdict["resolved"]) == len(TRACKED_DEBT)
    assert tracked_files(REPO_ROOT / "does-not-exist") == []
    assert len(tracked_files(REPO_ROOT)) > 100
    assert sum(1 for p in tracked_files(REPO_ROOT) if is_text_file(p)) > 100


# -- the pre-commit scope: the half of a commit the index does not name -------


def _scratch_repo(tmp_path: Path) -> Path:
    """A throwaway repository under pytest's own directory.

    This file used to say the only subprocesses were ``git`` read commands, and
    that is still true of the project repository: the writes below happen inside
    ``tmp_path``, which is outside it, is created by pytest and removed by
    pytest, and is never a path this suite reads as state. Nothing is committed
    -- an index entry from ``git add`` is enough to be "tracked".
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], capture_output=True, timeout=60, check=True)
    return tmp_path


def test_an_untracked_new_file_is_invisible_to_the_index_scope_only(tmp_path):
    """The hole owner 六 named, closed: a new file is scanned before it is added."""
    repo = _scratch_repo(tmp_path)
    (repo / "app.py").write_text("DATABASE_URL = ${INJECTED_BY_DEPLOYMENT}\n")
    subprocess.run(["git", "add", "app.py"], cwd=str(repo), capture_output=True, timeout=60, check=True)
    # The file a developer just wrote and has not staged yet.
    (repo / "settings.yaml").write_text(f"POSTGRES_PASSWORD: {PASSWORD_MARKER}\n")

    index_scope = scan(repo, scope="tracked")
    commit_scope = scan(repo, scope="commit")

    assert index_scope.files_scanned == 1, "the index half should have been read"
    assert index_scope.findings == (), "the staged file injects, so it holds no literal"
    assert commit_scope.files_scanned == 2, (
        "the commit scope lost the untracked half: "
        + str(commit_candidate_files(repo))
    )
    assert [f.path for f in commit_scope.findings] == ["settings.yaml"], commit_scope.findings
    assert commit_scope.scope == "commit" and index_scope.scope == "tracked"


def test_the_commit_scope_refuses_new_text_and_names_what_it_scanned(tmp_path):
    """A refusal must say which set it measured, or two claims read the same."""
    repo = _scratch_repo(tmp_path)
    (repo / "settings.yaml").write_text(f"POSTGRES_PASSWORD: {PASSWORD_MARKER}\n")

    verdict = reconcile(scan(repo, scope="commit"))
    text = verdict_text(verdict)
    assert not verdict["clean"]
    assert verdict["new"], "an unledgered literal must arrive as NEW, never as silence"
    assert "commit" in scan(repo, scope="commit").summary, scan(repo, scope="commit").summary
    assert PASSWORD_MARKER not in text, text
    assert digest(PASSWORD_MARKER) in text, text


def test_an_ignored_file_is_still_outside_the_commit_scope(tmp_path):
    """The remaining hole, pinned so nobody discovers it as a surprise later.

    ``--exclude-standard`` deliberately honours ``.gitignore``: a real ``.env``
    with a filled-in password is not scanned, because it is also not something a
    commit would carry. What it pins is the boundary, not a comfort -- a file
    force-added past the rule enters the index and is then read by both scopes.
    """
    repo = _scratch_repo(tmp_path)
    (repo / ".gitignore").write_text(".env\n")
    (repo / ".env").write_text(f"JWT_SECRET_KEY={PASSWORD_MARKER}\n")

    assert untracked_files(repo) == [".gitignore"], untracked_files(repo)
    assert commit_candidate_files(repo) == [".gitignore"], commit_candidate_files(repo)
    assert scan(repo, scope="commit").findings == (), scan(repo, scope="commit").findings


def test_no_answer_from_git_cannot_be_reported_as_a_commit_with_no_secrets(tmp_path):
    """``None`` (git refused) and ``[]`` (git answered, nothing there) are different."""
    repo = _scratch_repo(tmp_path)
    (repo / "keep.txt").write_text("not a settings file\n")

    assert commit_candidate_files(repo) == ["keep.txt"]
    assert commit_candidate_files(tmp_path / "never-created") is None

    silent = scan(tmp_path / "never-created", scope="commit")
    assert silent.files_scanned == 0, silent.summary
    assert not reconcile(silent)["clean"], "an empty enumeration is not a clean tree"


def test_the_untracked_half_of_this_tree_carries_no_literal_beyond_the_ledger():
    """The measurement the pre-commit gate will actually make today.

    Both scopes must return the same findings: the 5 ledgered compose literals.
    Anything the extra files contained would arrive here as ``new`` rather than
    in a hand-written exemption, and a count that moves on either side fails.
    """
    index = scan(REPO_ROOT)
    commit = scan(REPO_ROOT, scope="commit")
    extra = sum(1 for p in commit_candidate_files(REPO_ROOT) if is_text_file(p)) - \
        sum(1 for p in tracked_files(REPO_ROOT) if is_text_file(p))

    assert commit.files_scanned > index.files_scanned, (
        "the commit scope is not a superset, so the enumeration changed under the test"
    )
    assert extra > 0, "this tree has no untracked text files; the delta this pins is vacuous"
    assert commit.findings == index.findings, (
        "the untracked half of the next commit holds a literal the index never showed: "
        + ", ".join(str(f) for f in commit.findings)
    )
    assert reconcile(commit)["clean"], verdict_text(reconcile(commit))

