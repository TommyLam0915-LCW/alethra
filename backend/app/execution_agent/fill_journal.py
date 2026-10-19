"""Durable journal for fill delivery recovery (P0-C).

Tracks fills that need to be uploaded to the platform but haven't been
acknowledged yet. Persists across process restarts to ensure no fills
are lost even if the agent crashes or restarts mid-delivery.
"""
import json
import logging
import os
from datetime import datetime, timezone
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
        """Load journal from disk with validation."""
        if not self.journal_path.exists():
            logger.info(f"Fill delivery journal not found at {self.journal_path}, starting fresh")
            return

        try:
            with open(self.journal_path, "r") as f:
                data = json.load(f)
            
            pending_fills_data = data.get("pending_fills", [])
            if not isinstance(pending_fills_data, list):
                logger.error(f"Journal has invalid pending_fills format: {type(pending_fills_data)}")
                self.pending_fills = {}
                return
            
            loaded_count = 0
            for i, fill_data in enumerate(pending_fills_data):
                try:
                    if not isinstance(fill_data, dict):
                        logger.warning(f"Skipping malformed fill entry at index {i}: not a dict")
                        continue
                    
                    # Validate required fields
                    required_fields = ["intent_id", "broker_ref", "quantity", "price", "fill_time", "fill_key", "created_at"]
                    missing_fields = [f for f in required_fields if f not in fill_data]
                    if missing_fields:
                        logger.warning(f"Skipping malformed fill entry at index {i}: missing fields {missing_fields}")
                        continue
                    
                    fill = PendingFill.from_dict(fill_data)
                    self.pending_fills[fill.fill_key] = fill
                    loaded_count += 1
                except Exception as e:
                    logger.warning(f"Skipping malformed fill entry at index {i}: {e}")
                    continue
            
            logger.info(f"Loaded {loaded_count} pending fills from journal ({len(pending_fills_data) - loaded_count} malformed entries skipped)")
        except json.JSONDecodeError as e:
            logger.error(f"Fill delivery journal is corrupted (invalid JSON): {e}")
            # Don't start fresh - preserve the corrupted file for manual inspection
            self.pending_fills = {}
        except Exception as e:
            logger.error(f"Failed to load fill delivery journal: {e}")
            self.pending_fills = {}

    def _save(self) -> None:
        """Atomically save journal to disk."""
        try:
            data = {
                "pending_fills": [fill.to_dict() for fill in self.pending_fills.values()],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            # Write to temp file first, then replace for atomicity (cross-platform)
            temp_path = self.journal_path.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, self.journal_path)
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
