"""Durable journal for fill delivery recovery (P0-C).

Tracks fills that need to be uploaded to the platform but haven't been
acknowledged yet. Persists across process restarts to ensure no fills
are lost even if the agent crashes or restarts mid-delivery.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class PendingFill:
    """A fill that needs to be delivered to the platform."""
    intent_id: str
    broker_ref: str
    quantity: int
    price: float
    fill_time: str  # ISO format
    commission: Optional[float]
    fill_key: str  # Unique key for deduplication
    created_at: str  # ISO format
    retry_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PendingFill":
        return cls(**data)


class FillDeliveryJournal:
    """Durable journal for pending fill deliveries.

    Writes are atomic (write to temp file, then rename) to prevent corruption.
    The journal is loaded on startup and persisted after each change.
    """

    def __init__(self, journal_path: Optional[str] = None):
        if journal_path is None:
            # Default to a file in the current working directory
            journal_path = os.environ.get(
                "FILL_DELIVERY_JOURNAL_PATH",
                "/tmp/alethra-fill-delivery-journal.json"
            )
        self.journal_path = Path(journal_path)
        self.pending_fills: Dict[str, PendingFill] = {}  # fill_key -> PendingFill
        self._load()

    def _load(self) -> None:
        """Load journal from disk."""
        if not self.journal_path.exists():
            logger.info(f"Fill delivery journal not found at {self.journal_path}, starting fresh")
            return

        try:
            with open(self.journal_path, "r") as f:
                data = json.load(f)
                for fill_data in data.get("pending_fills", []):
                    fill = PendingFill.from_dict(fill_data)
                    self.pending_fills[fill.fill_key] = fill
            logger.info(f"Loaded {len(self.pending_fills)} pending fills from journal")
        except Exception as e:
            logger.error(f"Failed to load fill delivery journal: {e}")
            # Start fresh if journal is corrupted
            self.pending_fills = {}

    def _save(self) -> None:
        """Atomically save journal to disk."""
        try:
            data = {
                "pending_fills": [fill.to_dict() for fill in self.pending_fills.values()],
                "updated_at": datetime.utcnow().isoformat(),
            }
            # Write to temp file first, then rename for atomicity
            temp_path = self.journal_path.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                json.dump(data, f, indent=2)
            temp_path.rename(self.journal_path)
        except Exception as e:
            logger.error(f"Failed to save fill delivery journal: {e}")

    def add_pending_fill(self, fill: PendingFill) -> None:
        """Add a fill to the journal before attempting upload."""
        self.pending_fills[fill.fill_key] = fill
        self._save()
        logger.debug(f"Added pending fill {fill.fill_key} to journal")

    def remove_pending_fill(self, fill_key: str) -> None:
        """Remove a fill from the journal after successful upload."""
        if fill_key in self.pending_fills:
            del self.pending_fills[fill_key]
            self._save()
            logger.debug(f"Removed pending fill {fill_key} from journal")

    def increment_retry(self, fill_key: str) -> None:
        """Increment retry count for a pending fill."""
        if fill_key in self.pending_fills:
            self.pending_fills[fill_key].retry_count += 1
            self._save()

    def get_pending_fills(self) -> List[PendingFill]:
        """Get all pending fills."""
        return list(self.pending_fills.values())

    def has_pending_fill(self, fill_key: str) -> bool:
        """Check if a fill is already in the journal."""
        return fill_key in self.pending_fills

    def clear(self) -> None:
        """Clear all pending fills (for testing)."""
        self.pending_fills.clear()
        self._save()
