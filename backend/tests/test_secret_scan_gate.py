"""The secret scan gate must be able to fail, and must be able to escalate.

A scanner that reports "0 findings" has proved nothing until the same run also
proves it would have reported a finding. These cases plant the shapes the gate
is meant to catch and check which tier each one lands in, so the zero HIGH on
the real tree is a verdict rather than an absence of signal.

The tier split (opaque vs readable word literal, runtime path vs test path) is
what a previous round got wrong twice: first a regex that matched every string
made the whole scanner a no-op and reported "0 findings", then the fix reported
1,516 findings and was just as useless. Both failures are covered by a control
here, because neither was visible from the pass column alone.

The third failure of that family went the other way — not a rule that fired too
much, but two that fired too little plus one path classifier that misread a
whole directory. Each has a control below, and each was measured against the
real tree before the rule changed, so the added sensitivity is one new finding
rather than a new wave of noise: a letters+digits key (two classes, the shape of
every hex secret), a TypeScript ``key: 'value',`` entry whose trailing comma
blocked the charset test, and ``frontend/e2e/*.spec.ts`` counted as a runtime
path.

The fourth is a rule that did not exist: two sites hardcode the value the
product itself lists in ``_INSECURE_JWT_DEFAULTS``, and no entropy test can see
them because the value is lowercase words around separators. R6 judges that
shape against the product's own refuse-list instead of against character classes.
Its first version forced one tier for every hit, which a control then caught: a
test that pins the refused default was being counted as runtime debt.

No database, no network.
"""
import importlib.util
import os
import subprocess

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCANNER = os.path.join(REPO, "scripts", "audit_secrets.py")

SPEC = importlib.util.spec_from_file_location("audit_secrets", SCANNER)
aus = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aus)

LITERAL = "Tr4inR0le-Pw2026-literal"          # 23 chars, readable word literal
REF = "${ALETHRA_TEST_RUNTIME_PW}"            # indirection, must stay quiet
# 40 chars, one unbroken run: the shape a generated key has. Invent-only — this
# is not a credential anywhere, it is a pattern probe.
OPAQUE = "aB3dE6fG9hJ2kL5mN8pQ1rS4tU7vW0xYz2C5eG8h"
# 44 chars, readable words: the shape a hand-picked credential has. Invent-only
# and uniqueness-gated by test_probe_literals_are_not_deployed_values below —
# this constant's whole job is to collide with a runtime file inside tmp_path,
# and if it ever matches a real one the fixture becomes a ninth landing point for
# a deployed secret while still reading as a placeholder.
WORD_SECRET = "synthetic-probe-word-literal-not-a-cred-aa12"
# 32 hex chars: letters+digits, so exactly two character classes. The classifier
# used to demand three, which made every generated hex key invisible — the most
# common shape a real API secret has. Named without a credential word on
# purpose: a constant called ``*_SECRET`` here would be read by R1 itself, the
# shape is opaque, and the file would become a HIGH landing point.
HEX_PROBE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

HIGH_BASELINE = set()
WEAK_BASELINE = {
    ("R1", "NORMAL_RUNTIME_EVIDENCE.md", "JWT_SECRET_KEY", 44),
    ("R1", "docker-compose.acceptance.yml", "JWT_SECRET_KEY", 44),
    ("R1", "e2e_fullstack.py", "PASSWORD", 22),
    ("R1", "execute_journey_b_worker.sh", "JWT_SECRET_KEY", 44),
    ("R1", "start_acceptance_env.sh", "JWT_SECRET_KEY", 44),
    ("R1", "verify_data_persistence.sh", "JWT_SECRET_KEY", 44),
    ("R1", "verify_quota_persistence.sh", "JWT_SECRET_KEY", 44),
    # Visible only since R6: a prose default with no digit is invisible to every
    # entropy rule, and the typed field default hides it from KEY_ASSIGN_RE too.
    # The two sites are the product's own refused value being handed out as the
    # default signing key, so the tier is debt, not an emergency.
    ("R6", "backend/app/config.py", "jwt_secret_key", 48),
    ("R6", "docker-compose.yml", "JWT_SECRET_KEY", 48),
}
FIXTURE_BASELINE = {
    ("R1", "backend/tests/test_ai_assistant.py", "SECRET_KEY", 30),
    ("R1", "backend/tests/test_db_env_contract.py", "PASSWORD", 25),
    ("R1", "backend/tests/test_execution_containment.py", "secret_marker", 29),
    # This gate's own probe: word-literal-shaped, on a test path, and now
    # provably not a deployed value (test_probe_literals_are_not_deployed_values).
    ("R1", "backend/tests/test_secret_scan_gate.py", "WORD_SECRET", 44),
    # Visible since the value extractor stopped keeping the TypeScript flow-map
    # comma: the literal is test scaffolding, but until the fix R1 could not see
    # it at all, which is the opposite failure from a false positive.
    ("R1", "frontend/e2e/personal-strategy-learning.spec.ts", "JWT_SECRET_KEY", 32),
}


def _write(root, rel, text):
    path = os.path.join(str(root), rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return rel


def _rules(findings):
    return {f[0] for f in findings}


def _hits(findings, rule):
    return [f for f in findings if f[0] == rule]


def _tiers(findings, severity):
    return sorted((f[0], f[1], f[3], f[4]) for f in findings if f[5] == severity)


def test_positive_control_an_opaque_generated_key_is_high(tmp_path):
    _write(tmp_path, "start_stack.sh", f"export JWT_SECRET_KEY=\"{OPAQUE}\"\n")
    findings = aus.scan(str(tmp_path))
    assert _tiers(findings, "HIGH") == [("R1", "start_stack.sh", "JWT_SECRET_KEY", 40)], findings


def test_positive_control_a_literal_dsn_password_fires_r2(tmp_path):
    _write(tmp_path, "backend/.env.test",
           f"DATABASE_URL=postgresql://alethra_test_runtime:{LITERAL}@localhost:5432/lab_test\n")
    findings = aus.scan(str(tmp_path))
    assert "R2" in _rules(findings), findings
    assert not any(LITERAL in str(f) for f in findings), "the scanner must not echo the value"


def test_positive_control_an_opaque_dsn_password_is_high_severity(tmp_path):
    _write(tmp_path, "backend/.env.test",
           f"DATABASE_URL=postgresql://alethra_test_runtime:{OPAQUE}@localhost:5432/lab_test\n")
    findings = aus.scan(str(tmp_path))
    assert any(f[0] == "R2" and f[5] == "HIGH" for f in findings), findings


def test_positive_control_the_same_literal_in_a_report_fires_r3(tmp_path):
    _write(tmp_path, "backend/.env.test",
           f"DATABASE_URL=postgresql://alethra_test_runtime:{LITERAL}@localhost:5432/lab_test\n")
    _write(tmp_path, "DELIVERY.md",
           f"the runtime role authenticates with {LITERAL} on port 5432\n")
    findings = aus.scan(str(tmp_path))
    r3 = _hits(findings, "R3")
    assert r3, "a credential quoted in a delivery report has to be named"
    assert any(f[1] == "DELIVERY.md" for f in r3), r3


def test_negative_control_an_indirect_reference_stays_quiet(tmp_path):
    _write(tmp_path, "backend/.env.test",
           f"DATABASE_URL=postgresql://alethra_test_runtime:{REF}@localhost:5432/lab_test\n")
    _write(tmp_path, "DELIVERY.md",
           f"the password segment is {REF}, expanded by dotenv\n")
    assert aus.scan(str(tmp_path)) == []


def test_negative_control_documentation_defaults_stay_quiet(tmp_path):
    _write(tmp_path, "app/config.py",
           'database_url: str = "postgresql://user:pass@localhost:5432/dbname"\n')
    _write(tmp_path, "backend/.env.example",
           "JWT_SECRET_KEY=change-me-in-production\nKRAKEN_API_KEY=\n")
    assert aus.scan(str(tmp_path)) == []


def test_r1_fires_on_a_bare_credential_assignment(tmp_path):
    _write(tmp_path, "backend/app/bootstrap.sh",
           f"export STRIPE_WEBHOOK_SECRET='{LITERAL}'\n")
    findings = aus.scan(str(tmp_path))
    assert "R1" in _rules(findings), findings


def test_positive_control_a_generated_hex_key_is_not_invisible(tmp_path):
    """Letters+digits is two classes, and it is what a real key looks like.

    The classifier demanded three character classes, so every hex secret the
    tree could carry was silently passed over — a miss that reads as a clean
    scan, the same failure mode as the dead-regex round one level up.
    """
    assert aus.is_secretish(HEX_PROBE) is True
    _write(tmp_path, "start_stack.sh", f"export API_SECRET_KEY=\"{HEX_PROBE}\"\n")
    findings = aus.scan(str(tmp_path))
    assert _tiers(findings, "HIGH") == [("R1", "start_stack.sh", "API_SECRET_KEY", 32)], findings


def test_negative_control_a_digits_free_default_stays_quiet(tmp_path):
    """The relaxation stops at the separator: prose defaults are not keys.

    This proves only the entropy rules stay quiet. A prose default is not
    innocent — R6 judges it by whether the product already refuses the value,
    which the controls below cover; in a tree with no refuse-list to read, like
    this tmp_path, R6 is off rather than clean.
    """
    prose = "change-me-in-production-before-deploying-this-default-key"
    assert aus.is_secretish(prose) is False
    _write(tmp_path, "start_stack.sh", f'export JWT_SECRET_KEY="{prose}"\n')
    assert aus.scan(str(tmp_path)) == []


def test_value_extraction_keeps_no_type_script_separator(tmp_path):
    """``jwt_secret_key: 'aa..',`` used to arrive with its comma attached.

    The comma sits outside the quotes, so unquoting never ran and the value
    failed the charset test: R1 was blind to every literal written in this
    syntax, which is how frontend tests declare config.
    """
    key, value = aus._assign_value(f"  JWT_SECRET_KEY: '{WORD_SECRET}',")
    assert (key, value) == ("JWT_SECRET_KEY", WORD_SECRET)
    assert aus._assign_value("JWT_SECRET_KEY = 'plain'")[1] == "plain"
    _write(tmp_path, "frontend/e2e/probe.spec.ts",
           f"  JWT_SECRET_KEY: '{WORD_SECRET}',\n")
    findings = aus.scan(str(tmp_path))
    assert _tiers(findings, "FIXTURE") == [
        ("R1", "frontend/e2e/probe.spec.ts", "JWT_SECRET_KEY", 44)], findings
    assert _tiers(findings, "HIGH") == []


def _known_default_from_the_product():
    """The value the product assigns to ``jwt_secret_key`` *and* refuses.

    Read from the source rather than written here, because a literal copy of a
    credential-shaped string in this file would be another landing point — the
    mistake this gate already records once in its own history.
    """
    values, loaded = aus.load_insecure_defaults(REPO)
    assert loaded, (
        "the product's _INSECURE_JWT_DEFAULTS could not be read — R6 is off, "
        "and a gate cannot assert about a rule it switched off")
    for line in aus.read(aus.CONFIG_REL, REPO):
        key, value = aus.typed_assign_value(line)
        if key == "jwt_secret_key" and value in values:
            return value
    raise AssertionError(
        "backend/app/config.py no longer defaults jwt_secret_key to a refused "
        "value: R6's two recorded sites are gone, so regenerate WEAK_BASELINE "
        "deliberately instead of leaving a stale set behind")


def test_r6_judges_the_products_own_refuse_list():
    """The rule is wired to the list the product already enforces, not a copy.

    The value passes the two gates that kept it invisible: it is not secretish
    (no digit, so no entropy rule reaches it) and it sits behind a type
    annotation, so KEY_ASSIGN_RE captured the annotation instead of the value.
    """
    known = _known_default_from_the_product()
    assert aus.is_secretish(known) is False
    assert aus.is_placeholder(known) is False
    assert aus.typed_assign_value(f'    jwt_secret_key: str = "{known}"\n') == (
        "jwt_secret_key", known)
    assert aus._assign_value(f'    jwt_secret_key: str = "{known}"')[1] != known


def test_positive_control_r6_fires_on_both_assignment_shapes(tmp_path):
    known = _known_default_from_the_product()
    _write(tmp_path, aus.CONFIG_REL,
           '_INSECURE_JWT_DEFAULTS = {\n    "%s",\n    "secret",\n}\n'
           'class Settings:\n    jwt_secret_key: str = "%s"\n' % (known, known))
    _write(tmp_path, "docker-compose.yml", "      JWT_SECRET_KEY: %s\n" % known)
    findings = aus.scan(str(tmp_path))
    assert _tiers(findings, "WEAK_LITERAL") == [
        ("R6", "backend/app/config.py", "jwt_secret_key", len(known)),
        ("R6", "docker-compose.yml", "JWT_SECRET_KEY", len(known))], findings
    assert _tiers(findings, "HIGH") == []
    assert not any(known in str(f) for f in findings), "the scanner must not echo the value"


def test_r6_tiers_a_test_path_as_fixture_not_runtime_debt(tmp_path):
    """A test that pins the refused default is scaffolding; ``--strict`` must not call it debt.

    R6 was first written to force WEAK_LITERAL unconditionally, which made the
    runtime-debt count stop meaning "a site that configures a service".
    """
    known = _known_default_from_the_product()
    _write(tmp_path, aus.CONFIG_REL,
           '_INSECURE_JWT_DEFAULTS = {\n    "%s",\n}\n'
           'class Settings:\n    jwt_secret_key: str = "%s"\n' % (known, known))
    _write(tmp_path, "backend/tests/test_known_default_contract.py",
           'JWT_SECRET_KEY = "%s"\n' % known)
    findings = aus.scan(str(tmp_path))
    assert _tiers(findings, "WEAK_LITERAL") == [
        ("R6", "backend/app/config.py", "jwt_secret_key", len(known))], findings
    assert _tiers(findings, "FIXTURE") == [
        ("R6", "backend/tests/test_known_default_contract.py", "JWT_SECRET_KEY", len(known))], findings


def test_r6_reports_off_instead_of_clean_when_the_list_cannot_be_read(tmp_path, capsys):
    known = _known_default_from_the_product()
    _write(tmp_path, "docker-compose.yml", "      JWT_SECRET_KEY: %s\n" % known)
    assert aus.scan(str(tmp_path)) == []
    aus.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "R6 OFF" in out, out


def test_lane_treats_a_playwright_spec_as_a_test_path():
    """A spec drives a stack someone else started; it is not what a service reads.

    The promotion rule still covers the dangerous case: if the same value is
    also in a runtime file, ``runtime ∩ test`` makes it HIGH.
    """
    assert aus.lane("frontend/e2e/personal-strategy-learning.spec.ts") == "test"
    assert aus.lane("frontend/e2e/helpers/login.ts") == "test"
    assert aus.lane("backend/tests/test_login.py") == "test"
    assert aus.lane("start_acceptance_env.sh") == "runtime"
    assert aus.lane("verify_data_persistence.sh") == "runtime"


def test_the_scanner_is_not_a_no_op():
    """Regression for the dead-regex round.

    The first version's placeholder test was unanchored, so it matched every
    string and every finding was filtered out; "0 findings" then looked like a
    clean tree. These two asserts fail if either half of the classifier is
    switched off.
    """
    assert aus.is_placeholder(LITERAL) is False
    assert aus.is_secretish(LITERAL) is True
    assert aus.is_secretish("") is False
    assert aus.is_secretish("x" * 40) is False          # one class, not a generated key


def test_shape_separates_generated_from_readable_literals():
    assert aus.shape(OPAQUE) == "opaque"
    assert aus.shape(LITERAL) == "word-literal"
    assert aus.shape(WORD_SECRET) == "word-literal"
    assert aus.shape("Zm9vYmFy" * 6) == "opaque"


def test_probe_literals_are_not_deployed_values():
    """Each probe must exist in this file and nowhere else in the tree.

    The first version of WORD_SECRET was copied from the acceptance scripts and
    read like a placeholder, but it *was* the deployed JWT signing key. Landing
    it on a test path made the same value appear in both lanes, which is exactly
    the condition the promotion rule below treats as a deployed credential, so
    the fixture silently reclassified six real sites from WEAK_LITERAL to HIGH
    and added a ninth copy of the key to a file the delivery ships. Shape is
    checked here too: a probe that stopped being word-literal would exercise a
    different rule than the one its test names.
    """
    here = os.path.relpath(os.path.abspath(__file__), REPO)
    for name, value in (("OPAQUE", OPAQUE), ("WORD_SECRET", WORD_SECRET),
                        ("HEX_PROBE", HEX_PROBE), ("LITERAL", LITERAL), ("REF", REF)):
        carriers = [rel for rel in aus.tracked_files(REPO)
                    if value in "".join(aus.read(rel, REPO))]
        assert carriers == [here], f"{name} also appears in {carriers}"
        if name != "REF":
            assert aus.is_secretish(value), f"{name} no longer trips the literal rule"
    assert aus.shape(WORD_SECRET) == "word-literal"
    assert aus.shape(HEX_PROBE) == "opaque"


def test_word_literal_on_a_runtime_path_is_debt_not_an_emergency(tmp_path):
    _write(tmp_path, "docker-compose.acceptance.yml", f"  JWT_SECRET_KEY: {WORD_SECRET}\n")
    findings = aus.scan(str(tmp_path))
    assert _tiers(findings, "WEAK_LITERAL") == [
        ("R1", "docker-compose.acceptance.yml", "JWT_SECRET_KEY", 44)], findings
    assert _tiers(findings, "HIGH") == []


def test_word_literal_shared_with_a_runtime_path_is_promoted(tmp_path):
    """A value a test asserts on *and* a service reads is a deployed credential."""
    _write(tmp_path, "backend/tests/test_login.py", f"PASSWORD = \"{WORD_SECRET}\"\n")
    _write(tmp_path, "start_stack.sh", f"export JWT_SECRET_KEY=\"{WORD_SECRET}\"\n")
    findings = aus.scan(str(tmp_path))
    assert _tiers(findings, "HIGH"), findings
    assert _tiers(findings, "FIXTURE") == []


def test_word_literal_only_inside_tests_is_fixture_tier(tmp_path):
    _write(tmp_path, "backend/tests/test_login.py", f"PASSWORD = \"{WORD_SECRET}\"\n")
    findings = aus.scan(str(tmp_path))
    assert _tiers(findings, "FIXTURE") == [
        ("R1", "backend/tests/test_login.py", "PASSWORD", 44)], findings
    assert _tiers(findings, "HIGH") == []


def test_endpoint_url_key_is_exempt_but_its_inline_password_is_not(tmp_path):
    """The one false positive this round removed, with its coverage re-asserted."""
    _write(tmp_path, "backend/app/providers/lseg_provider.py",
           'LSEG_OAUTH2_TOKEN_URL = "https://developers.lseg.com/auth/oauth2/v2/token"\n')
    assert aus.scan(str(tmp_path)) == []
    _write(tmp_path, "backend/app/providers/lseg_provider.py",
           'LSEG_OAUTH2_TOKEN_URL = "https://user:' + OPAQUE + '@api.example.com/token"\n')
    findings = aus.scan(str(tmp_path))
    assert any(f[0] == "R2" for f in findings), findings


def test_exit_code_is_high_severity_only(tmp_path, capsys):
    _write(tmp_path, "start_stack.sh", f"export JWT_SECRET_KEY=\"{WORD_SECRET}\"\n")
    assert aus.main(["--root", str(tmp_path)]) == 0
    _write(tmp_path, "leak.sh", f"export JWT_SECRET_KEY=\"{OPAQUE}\"\n")
    assert aus.main(["--root", str(tmp_path)]) == 1


def test_strict_promotes_runtime_debt_to_a_failure(tmp_path):
    _write(tmp_path, "start_stack.sh", f"export JWT_SECRET_KEY=\"{WORD_SECRET}\"\n")
    assert aus.main(["--root", str(tmp_path)]) == 0
    assert aus.main(["--root", str(tmp_path), "--strict"]) == 1


def test_optout_stays_visible_even_when_it_silences_a_line(tmp_path):
    _write(tmp_path, "backend/tests/test_pw.py",
           f'PASSWORD = "{OPAQUE}"  # secret-scan: fixture\n')
    findings, optouts = aus.scan(str(tmp_path), collect_optouts=True)
    assert findings == []
    assert optouts == ["backend/tests/test_pw.py:1"]


def test_the_scanner_reads_the_tree_that_is_shipped():
    """The file list is git's, not a hand-kept allowlist, so new files cannot opt out."""
    listed = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True,
                            text=True).stdout.splitlines()
    scanned = set(aus.tracked_files(REPO))
    unscanned_text = [f for f in listed
                      if f.endswith((".py", ".md", ".sh", ".yml", ".env.test"))
                      and f not in scanned]
    assert unscanned_text == [], unscanned_text


def test_current_tree_has_no_high_severity_landing_point():
    """The gate the delivery runs on: zero values that look generated."""
    findings = aus.scan(REPO)
    high = _tiers(findings, "HIGH")
    assert high == sorted(HIGH_BASELINE), [f"{r} {rel}:{key} len={ln}" for r, rel, key, ln in high]


def test_current_runtime_debt_set_has_not_grown():
    """The 7 readable literals are known debt, not a clean bill.

    Frozen as the set this round measured — regenerate it with
    ``python scripts/audit_secrets.py --all`` and update deliberately, never as
    a side effect of another change. A new fixed credential in a startup script
    fails here even though the default exit code stays 0.
    """
    findings = aus.scan(REPO)
    assert _tiers(findings, "WEAK_LITERAL") == sorted(WEAK_BASELINE), _tiers(findings, "WEAK_LITERAL")
    assert _tiers(findings, "FIXTURE") == sorted(FIXTURE_BASELINE), _tiers(findings, "FIXTURE")
