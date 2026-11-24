"""Register the exact stored bar series a run priced off as a dataset snapshot.

``research_runs.dataset_snapshot_id`` is a foreign key into
``dataset_snapshots``, and for years it stayed NULL while the backtest engine
returned a freshly minted UUID that was written nowhere. That made a run's data
provenance untraceable: two runs over the same instrument could disagree about
which rows they read, and nothing in the database could say which series each
one actually priced.

This module closes that gap for stored runs only. A snapshot id here is
deterministic — derived from the provider, the instrument set, the declared
window and the content checksum — so the same stored series always resolves to
the same snapshot row, and an edited or overwritten series resolves to a
different one. The identifiers are derived from the manifest, never invented at
runtime: a lost row can be re-created with the same id by re-registering it.

Series that were fetched rather than read from storage are not registered:
their rows live only in the venue's rolling window, so a snapshot would name a
dataset the platform cannot reproduce. The caller records that decision as an
explicit reason instead of a silent NULL.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Optional, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.dataset import Dataset, DatasetSnapshot, SourceType

logger = logging.getLogger(__name__)

# The namespace every stored-market-data identifier is derived from. Changing
# it re-ids every snapshot, so it is a constant rather than a value per call.
STORED_MARKET_DATA_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://alethra.local/datasets/stored-market-data/v1"
)

REGISTRY_NAME = "alethra:market_data_bars"
PROVIDER_UNKNOWN = "unknown"


def _dataset_manifest(provider_ids: Sequence[str]) -> str:
    return f"dataset|{'|'.join(provider_ids)}"


def _snapshot_manifest(
    dataset_id: uuid.UUID,
    checksum: str,
    asset_ids: Sequence[str],
    start_date: str,
    end_date: str,
) -> str:
    return (
        f"snapshot|{dataset_id}|{checksum}|{'|'.join(asset_ids)}|"
        f"{start_date}|{end_date}"
    )


def _get_or_create(model, db: Session, object_id: uuid.UUID, build) -> None:
    """Insert one row by its deterministic primary key, tolerate the race.

    The savepoint is what keeps this safe: a conflicting insert rolls back only
    the nested block, never the caller's transaction, and the row that is then
    re-read is the same row this call would have written.
    """
    if db.get(model, object_id) is not None:
        return
    try:
        with db.begin_nested():
            db.add(build())
    except IntegrityError:
        if db.get(model, object_id) is None:
            raise


def register_stored_bars_snapshot(
    db: Session,
    *,
    asset_ids: Sequence[str],
    provider_ids: Sequence[str],
    start_date: str,
    end_date: str,
    checksum: Optional[str],
) -> Optional[uuid.UUID]:
    """Create or reuse the snapshot of one stored read; return its id.

    ``checksum`` is the run's ``dataset_checksum``: it is what makes two reads
    over the same window distinguishable. Without it the snapshot would name a
    window, not the rows, and an overwritten series would keep the old id.
    A stored series with no checksum is therefore not registered at all.

    Nothing is committed here. The rows belong to the caller's transaction, so
    a run that is never published takes its snapshot down with it, and a
    snapshot that no run points at cannot be mistaken for a series the
    platform billed for.
    """
    if not checksum:
        return None

    assets = sorted(set(asset_ids))
    providers = sorted(set(provider_ids)) or [PROVIDER_UNKNOWN]
    if not assets:
        return None

    dataset_id = uuid.uuid5(STORED_MARKET_DATA_NAMESPACE, _dataset_manifest(providers))
    snapshot_id = uuid.uuid5(
        STORED_MARKET_DATA_NAMESPACE,
        _snapshot_manifest(dataset_id, checksum, assets, start_date, end_date),
    )

    provider_label = ",".join(providers)
    _get_or_create(
        Dataset,
        db,
        dataset_id,
        lambda: Dataset(
            id=dataset_id,
            name=f"{REGISTRY_NAME}|{provider_label}",
            provider=provider_label[:255],
            source_type=SourceType.venue_stored,
            # No licence is asserted: these are the venue candles the platform
            # itself wrote down, and this row says nothing about their terms.
            license_status=None,
            description=(
                "System registry for bars read back from market_data_bars. "
                "Rows are addressed by instrument, provider and grain, not by "
                "a file path."
            ),
        ),
    )

    snapshot_date = date.fromisoformat(end_date)
    locator = (
        f"db:market_data_bars?provider={provider_label}"
        f"&window={start_date}..{end_date}"
    )[:500]
    _get_or_create(
        DatasetSnapshot,
        db,
        snapshot_id,
        lambda: DatasetSnapshot(
            id=snapshot_id,
            dataset_id=dataset_id,
            snapshot_date=snapshot_date,
            data_file_path=locator,
            checksum=checksum[:64],
            # Left at the column default. ``corporate_actions_adjusted`` is a
            # nullable Boolean whose only reader renders it with ``bool()``, so
            # an explicit NULL and a False are the same answer to that reader;
            # writing None would look like a claim of "unknown" while nothing
            # downstream can tell it apart from "not adjusted". The note below
            # is where the actual absence of an assessment is stated.
            missing_data_notes=(
                f"manifest: instruments={','.join(assets)}; "
                f"window={start_date}..{end_date}; provider={provider_label}; "
                "series=rows of market_data_bars matching this window, at the "
                "grain recorded on each row. The row count is not a session "
                "count: see the run's dataset_coverage bar_count/session_count. "
                "corporate_actions_adjusted is the column default, not a finding: "
                "each bar's own is_adjusted label is free text and is often unset, "
                "and this registry reads none of it."
            ),
        ),
    )
    return snapshot_id
