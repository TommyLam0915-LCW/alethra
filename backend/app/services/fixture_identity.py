"""Deterministic identities for synthetic E2E fixtures.

Fixture ids are scoped by the fixture email. Without that scope every seed run
reuses one id, so seeding a disposable test account silently replaces whichever
account previously owned those rows (for example the candidate's owner login).
"""

import uuid


def fixture_uuid(marker: str, email: str, name: str) -> uuid.UUID:
    normalized = email.strip().lower()
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{marker}:{normalized}:{name}")
