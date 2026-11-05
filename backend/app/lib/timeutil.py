from datetime import datetime, timezone


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
