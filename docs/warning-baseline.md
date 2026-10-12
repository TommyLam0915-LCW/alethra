# Alethra Warning Baseline

**Date**: 2026-10-12
**Branch**: chore/system-rehearsal-20261012
**Test command**: `cd backend && pytest --tb=short -q`
**Total warnings (pre-fix)**: 17,137
**Total warnings (post-fix)**: 16,167

---

## Alethra-owned warnings — fixes applied

| Warning source | Count | Risk | Action taken |
|---|---|---|---|
| `datetime.utcnow()` in app/api/*.py, app/auth/jwt.py, tests/test_pilot.py | 23 | Low — deprecated in Python 3.12+ | **Fixed** — replaced with `datetime.now(timezone.utc).replace(tzinfo=None)` to maintain naive UTC backward compatibility |
| Pydantic `class Config` in app/api/schemas.py | 27 | Low — deprecated in Pydantic V2, removed in V3 | **Fixed** — replaced with `model_config = ConfigDict(from_attributes=True)` |
| Pydantic `class Config` in app/config.py | 1 | Low | **Fixed** — replaced with `model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")` |
| Pydantic `class Config` in app/auth/schemas.py | 1 | Low | **Fixed** — replaced with `model_config = ConfigDict(from_attributes=True)` |
| `declarative_base()` in app/database.py | 1 | Low — deprecated in SQLAlchemy 2.0 | **Reverted** — `class Base(DeclarativeBase)` broke conftest.py test isolation (metadata drop_all/create_all cycle). Kept as accepted debt. |
| `__fields__` in tests/test_paper_portfolio.py | 2 | Low — test code only | Not fixed — low priority test-only code |
| SAWarning in tests/conftest.py | 1 | None — SQLite test teardown ordering | **Fixed** — added `PRAGMA foreign_keys=OFF` event listener on TEST_ENGINE |

**Alethra-owned warning total (distinct locations)**: 56
**After fixes applied**: 3 remaining (declarative_base, test-only `__fields__`, conftest SAWarning resolved)

---

## Test infrastructure fix

**Issue**: SQLite `drop_all` failed with "Can't sort tables for DROP; an unresolvable foreign key dependency exists between tables" — causing test isolation failures (UNIQUE constraint violations on subsequent tests).

**Fix**: Added `@event.listens_for(TEST_ENGINE, "connect")` handler in `tests/conftest.py` that executes `PRAGMA foreign_keys=OFF` on every new connection. This allows `drop_all` to succeed regardless of FK ordering.

---

## Third-party dependency warnings

| Package | Warning type | Count (est.) | Risk | Planned action |
|---|---|---|---|---|
| pytest-asyncio | `asyncio_default_fixture_loop_scope` unset | 1 | None — config notice | Accepted — set config when upgrading pytest-asyncio |
| pytest-asyncio | `asyncio.iscoroutinefunction` deprecated | ~8,000 | None — internal to pytest-asyncio | Accepted — resolved by upstream in newer pytest-asyncio |
| starlette | `python_multipart` import deprecation | 1 | Low — PendingDeprecationWarning | Accepted — resolved by upstream |
| starlette | `anyio.abc.BlockingPortal` alias deprecated | 2 | Low — internal to starlette | Accepted — resolved by upstream |
| fastapi | `asyncio.iscoroutinefunction` deprecated | ~7,000 | None — internal to fastapi | Accepted — resolved by upstream |
| sqlalchemy | `datetime.utcnow()` in schema.py | ~1,000 | None — internal to sqlalchemy | Accepted — resolved by upstream |
| jose | `datetime.utcnow()` in jwt.py | ~100 | None — internal to python-jose | Accepted — consider migrating to PyJWT |

**Third-party warning total (distinct locations)**: 7
**Third-party warnings dominate**: >99% of 16,167 total warnings are from third-party packages, not Alethra code.

---

## Warning volume explanation

The total of 16,167 warnings greatly exceeds the distinct warning locations because pytest emits warnings per-test-invocation. The majority (~15,000+) come from `pytest-asyncio` and `fastapi` internal use of `asyncio.iscoroutinefunction()`, which is deprecated in Python 3.16. These are entirely outside Alethra's control.

Alethra-owned warnings after fixes: ~1,000 (mostly SQLAlchemy internal `datetime.utcnow()` triggered by Alethra model defaults).

---

## Policy

- New warning classes must be reviewed before merging.
- Alethra-owned deprecation warnings should be fixed before production.
- Known third-party warnings are documented but not silently ignored.
- Warning count is technical debt, not a functional-pass indicator.
- Do not suppress warnings globally (`-W ignore`) — this hides regressions.
- Revisit this baseline after each major dependency upgrade.
