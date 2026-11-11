"""``docker-compose.acceptance.yml`` and its only consumer must move together.

The acceptance compose file carried a literal database password, that password
embedded in a literal ``DATABASE_URL``, and a literal JWT signing key. Converting
only the compose file would break CI silently, because
``.github/workflows/browser-acceptance.yml`` is the sole caller and defines no
such variables — that pairing is why this file was listed as "属实但不单方面改"
in the credential classification.

These tests pin the pair without Docker, without a daemon and without an image
pull: every assertion is on the text of the two checked-in files. Nothing here
proves that ``docker compose up`` succeeds — that stays UNATTEMPTED this round.

No PyYAML: ``scripts/run_tests.sh`` defaults to ``python3``, which does not ship
it, so a YAML-parsing guard would fail collection on the default interpreter.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.acceptance.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "browser-acceptance.yml"

#: Variables the compose file must demand from the environment.
REQUIRED = ("POSTGRES_PASSWORD", "JWT_SECRET_KEY")

_INTERP = re.compile(r"\$\{([A-Z0-9_]+):\?([^}]*)\}")
_COMPOSE_VALUE = re.compile(r"^\s*(POSTGRES_PASSWORD|JWT_SECRET_KEY|DATABASE_URL):\s*(.*?)\s*$")
_UP_STEP = re.compile(r"docker compose -f \S*docker-compose\.acceptance\.yml up\b")
_DOWN_STEP = re.compile(r"docker compose -f \S*docker-compose\.acceptance\.yml down\b(\s+-v)?")
_EXPORT = re.compile(r'echo "(POSTGRES_PASSWORD|JWT_SECRET_KEY)=\$([a-z_]+)" >> "\$GITHUB_ENV"')
_RANDOM_HEX = re.compile(r'\b([a-z_]+)="\$\(openssl rand -hex (\d+)\)"')


def compose_values() -> dict[str, str]:
    """The three credential-bearing compose scalars, exactly as written."""
    found = {}
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        m = _COMPOSE_VALUE.match(line)
        if m:
            found[m.group(1)] = m.group(2).strip('"')
    return found


def required_names(text: str) -> set[str]:
    return {m.group(1) for m in _INTERP.finditer(text)}


def literal_credentials(text: str) -> list[str]:
    """Compose lines that supply a credential as a literal instead of demanding one.

    Used both on the real file and on a mutated copy, so a passing test means the
    guard can actually see a regression rather than merely not being looked at.
    """
    offenders = []
    for key, value in _compose_values_in(text).items():
        if key == "DATABASE_URL":
            # postgresql://user:<secret>@host... — anything not interpolated fails
            userinfo = re.match(r"postgresql://([^/]*)@", value)
            if userinfo and not userinfo.group(1).endswith("}"):
                offenders.append(f"{key} embeds a literal password")
        elif not value.startswith("${"):
            offenders.append(f"{key} defaults to a literal")
    return offenders


def _compose_values_in(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        m = _COMPOSE_VALUE.match(line)
        if m:
            found[m.group(1)] = m.group(2).strip('"')
    return found


def workflow_steps() -> list[tuple[int, str]]:
    """(line number, run body) for every ``run:`` step, in file order.

    Handles both forms GitHub Actions allows — ``run: <one-liner>`` and
    ``run: |`` followed by an indented block.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    steps = []
    for index, line in enumerate(lines):
        m = re.match(r"^(\s*)run:\s*(\|?)\s*(.*)$", line)
        if not m:
            continue
        indent, _block, inline = m.groups()
        if inline:
            steps.append((index + 1, inline))
            continue
        body = []
        for following in lines[index + 1:]:
            if following.strip() and not following.startswith(indent + " "):
                break
            body.append(following)
        steps.append((index + 1, "\n".join(body)))
    return steps


# --------------------------------------------------------------------------
# the compose side
# --------------------------------------------------------------------------


def test_compose_demands_every_credential_it_used_to_default():
    values = compose_values()
    assert set(values) == {"POSTGRES_PASSWORD", "JWT_SECRET_KEY", "DATABASE_URL"}, values
    assert required_names(COMPOSE.read_text(encoding="utf-8")) == set(REQUIRED)
    assert literal_credentials(COMPOSE.read_text(encoding="utf-8")) == []


def test_compose_has_no_leftover_literal_anywhere_in_the_file():
    text = COMPOSE.read_text(encoding="utf-8")
    # the old shape: ": postgres" as a value, or a password between ://user: and @
    assert not re.search(r"^\s*POSTGRES_PASSWORD:\s*postgres\s*$", text, re.M)
    assert not re.search(r"//[^:/\s]+:(?!\$\{)[^@\s]+@", text), "literal password in a URL"
    assert "acceptance-test-secret" not in text


def test_interpolation_carries_an_actionable_message():
    for name, message in _INTERP.findall(COMPOSE.read_text(encoding="utf-8")):
        assert name in message, f"{name}: failure text must name the variable it wants"


def test_acceptance_target_identity_is_unchanged():
    """Ports and DB name stay put — §六 selects one API/UI address off these."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert re.search(r"^\s*-\s*\"5433:5432\"\s*$", text, re.M), "db host port moved"
    assert re.search(r"^\s*-\s*\"8001:8000\"\s*$", text, re.M), "backend host port moved"
    assert re.search(r"^\s*-\s*\"3001:3000\"\s*$", text, re.M), "frontend host port moved"
    assert "ai_investment_lab_acceptance" in text
    assert "NEXT_PUBLIC_API_URL: http://localhost:8001" in text


# --------------------------------------------------------------------------
# the workflow side of the pair
# --------------------------------------------------------------------------


def test_workflow_supplies_every_required_variable_before_it_starts_the_stack():
    steps = workflow_steps()
    up_at = [n for n, body in steps if _UP_STEP.search(body)]
    assert len(up_at) == 1, f"expected exactly one 'compose up' step, line numbers {up_at}"

    supplied: dict[str, int] = {}
    for number, body in steps:
        for m in _EXPORT.finditer(body):
            supplied.setdefault(m.group(1), number)

    missing = [name for name in REQUIRED if name not in supplied]
    assert not missing, f"{missing} never exported to $GITHUB_ENV"
    for name, line in supplied.items():
        assert line < up_at[0], f"{name} is exported at :{line}, after 'compose up' at :{up_at[0]}"


def test_generated_secrets_are_long_enough_and_masked_in_logs():
    body = "\n".join(b for _, b in workflow_steps())
    generated = dict((m.group(1), int(m.group(2))) for m in _RANDOM_HEX.finditer(body))
    assert generated, "no openssl rand -hex assignment found"
    for name, nbytes in generated.items():
        assert name in {"pg_password", "jwt_secret"}, name
        # hex doubles the byte count; the backend requires JWT_SECRET_KEY >= 32 chars
        assert nbytes * 2 >= 32, f"{name} would yield {nbytes * 2} chars, under 32"
    masked = re.findall(r'echo "::add-mask::\$(\w+)"', body)
    assert set(masked) >= set(generated), f"unmasked generated secret: {set(generated) - set(masked)}"


def test_stack_is_still_volume_torn_down():
    """A per-run password only works if the volume that stored the old one goes."""
    bodies = [(n, b) for n, b in workflow_steps() if _DOWN_STEP.search(b)]
    assert bodies, "no 'compose down' step left"
    for number, body in bodies:
        assert re.search(r"down\s+-v", body), f"teardown at :{number} keeps the volume"


# --------------------------------------------------------------------------
# negative control: the guard must be able to see the old shape
# --------------------------------------------------------------------------


def test_literal_default_would_be_caught_not_tolerated():
    text = COMPOSE.read_text(encoding="utf-8")
    mutated = text.replace(
        "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set in .env or the environment}",
        "POSTGRES_PASSWORD: postgres",
    )
    assert mutated != text, "the sentinel replacement found nothing — this control proves nothing"
    offenders = literal_credentials(mutated)
    assert any("POSTGRES_PASSWORD" in o for o in offenders), offenders

    url_mutated = re.sub(
        r'DATABASE_URL: "postgresql://postgres:[^@]+@db',
        "DATABASE_URL: postgresql://postgres:postgres@db",
        text,
    )
    assert url_mutated != text, "no interpolation left in DATABASE_URL to mutate"
    assert any("DATABASE_URL" in o for o in literal_credentials(url_mutated))
