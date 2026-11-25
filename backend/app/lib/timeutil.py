from datetime import date, datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_aware() -> datetime:
    """Wall clock for a ``TIMESTAMP WITH TIME ZONE`` column.

    :func:`utcnow` is naive, so Postgres labels it with the *session* TimeZone.
    On a UTC session that is harmless; on a +08 session the stored instant moves
    eight hours while a sibling column written from an aware value does not. Any
    column declared ``DateTime(timezone=True)`` needs this function, not
    ``utcnow``, to keep two timestamps on the same row comparable.
    """
    return datetime.now(timezone.utc)


def iso_from_stored(value: object) -> Optional[str]:
    """ISO text for a date/datetime that came back from the database.

    A ``text()`` read applies no column types, so what arrives is whatever the
    driver stored: psycopg2 hands back ``datetime``/``date`` objects, sqlite3
    hands back TEXT -- ``'2026-11-16 12:00:00.000000'`` for a
    ``CURRENT_TIMESTAMP`` and ``'2026-11-16'`` for a ``CURRENT_DATE``. Calling
    ``.isoformat()`` on the second is an ``AttributeError``, which surfaced as a
    500 from endpoints whose rows PostgreSQL serves from the same SQL.

    Both spellings land here, so a response model that declares the field as
    ``Optional[str]`` carries one form on either backend rather than a
    space-separated timestamp from SQLite and a ``T``-separated one from
    PostgreSQL.
    """
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if isoformat is not None:
        return isoformat()
    text_value = str(value).strip()
    if not text_value:
        return None
    if len(text_value) == 10:  # date-only, as CURRENT_DATE is stored
        return date.fromisoformat(text_value).isoformat()
    return datetime.fromisoformat(text_value).isoformat()
