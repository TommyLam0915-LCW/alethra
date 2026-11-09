"""Durable journal for fill delivery recovery (P0-C).

Tracks fills that need to be uploaded to the platform but haven't been
acknowledged yet. Persists across process restarts to ensure no fills
are lost even if the agent crashes or restarts mid-delivery.
"""
import json
import logging
import os
import fcntl
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

    def __init__(self, journal_path: Optional[str] = None, agent_id: Optional[str] = None):
        if journal_path is None:
            # Require explicit path or agent_id for identity scoping
            env_path = os.environ.get("FILL_DELIVERY_JOURNAL_PATH")
            if env_path:
                journal_path = env_path
            elif agent_id:
                # Scope by agent_id to prevent cross-agent collision
                journal_path = f"/tmp/alethra-fill-journal-{agent_id}.json"
            else:
                raise ValueError(
                    "FillDeliveryJournal requires explicit journal_path or agent_id. "
                    "Shared /tmp path is not safe for multi-agent deployments."
                )
        self.journal_path = Path(journal_path)
        self.pending_fills: Dict[str, PendingFill] = {}  # fill_key -> PendingFill
        self._loaded_keys: set = set()  # Track keys loaded from disk (for merge logic)
        self.modified_keys: set = set()  # Track keys modified in memory (for merge logic)
        self.removed_keys: set = set()  # Track removed keys for merge logic
        self.corrupted: bool = False  # True if journal file was corrupted on load
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
                    self._loaded_keys.add(fill.fill_key)  # Track for merge logic
                    loaded_count += 1
                except Exception as e:
                    logger.warning(f"Skipping malformed fill entry at index {i}: {e}")
                    continue

            logger.info(f"Loaded {loaded_count} pending fills from journal ({len(pending_fills_data) - loaded_count} malformed entries skipped)")
        except json.JSONDecodeError as e:
            logger.error(f"Fill delivery journal is corrupted (invalid JSON): {e}")
            # Preserve the corrupted file for manual inspection, mark as corrupted
            self.corrupted = True
            self.pending_fills = {}
        except Exception as e:
            logger.error(f"Failed to load fill delivery journal: {e}")
            self.pending_fills = {}

    def _save(self) -> bool:
        """Atomically save journal to disk with file locking. Returns True on success, False on failure."""
        lock_path = self.journal_path.with_suffix(".lock")
        try:
            # Acquire exclusive lock
            lock_fd = open(lock_path, "w")
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

                # Reload from disk under lock and merge
                if self.journal_path.exists():
                    try:
                        with open(self.journal_path, "r") as f:
                            disk_data = json.load(f)
                        disk_fills = {
                            fill["fill_key"]: PendingFill.from_dict(fill)
                            for fill in disk_data.get("pending_fills", [])
                            if isinstance(fill, dict) and "fill_key" in fill
                        }
                        # Merge: disk + new additions + modified items from memory - removed keys
                        # New additions = items in memory that weren't in the original load
                        # Modified items = items in memory that were loaded but changed AND still in disk
                        new_additions = {
                            k: v for k, v in self.pending_fills.items()
                            if k not in self._loaded_keys
                        }
                        modified_items = {
                            k: v for k, v in self.pending_fills.items()
                            if k in self.modified_keys and k in disk_fills  # Only if still in disk
                        }
                        merged = {**disk_fills, **new_additions, **modified_items}
                        for key in self.removed_keys:
                            merged.pop(key, None)
                        self.pending_fills = merged
                        self.removed_keys.clear()
                        self.modified_keys.clear()
                        self._loaded_keys = set(merged.keys())  # Update loaded keys for next save
                    except (json.JSONDecodeError, KeyError):
                        # If disk is corrupted, keep memory state and clear tracking sets
                        self.removed_keys.clear()
                        self.modified_keys.clear()

                # Write merged state to disk
                data = {
                    "pending_fills": [fill.to_dict() for fill in self.pending_fills.values()],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                temp_path = self.journal_path.with_suffix(f".tmp.{os.getpid()}")
                with open(temp_path, "w") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.journal_path)

                return True
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
        except Exception as e:
            logger.error(f"Failed to save fill delivery journal: {e}")
            return False

    def add_pending_fill(self, fill: PendingFill) -> bool:
        """Add a fill to the journal before attempting upload. Returns True on success, False on failure."""
        self.pending_fills[fill.fill_key] = fill
        if not self._save():
            # Rollback: remove from memory if save failed
            del self.pending_fills[fill.fill_key]
            return False
        logger.debug(f"Added pending fill {fill.fill_key} to journal")
        return True

    def remove_pending_fill(self, fill_key: str) -> bool:
        """Remove a fill from the journal after successful upload. Returns True on success, False on failure."""
        if fill_key not in self.pending_fills:
            return True  # Already removed, success
        removed_fill = self.pending_fills.pop(fill_key)
        self.removed_keys.add(fill_key)  # Track for merge logic
        if not self._save():
            # Rollback: restore if save failed
            self.pending_fills[fill_key] = removed_fill
            self.removed_keys.discard(fill_key)
            return False
        logger.debug(f"Removed pending fill {fill_key} from journal")
        return True

    def increment_retry(self, fill_key: str) -> bool:
        """Increment retry count for a pending fill.

        Returns False if the journal write failed (in-memory state is still updated).
        """
        if fill_key in self.pending_fills:
            self.pending_fills[fill_key].retry_count += 1
            self.modified_keys.add(fill_key)  # Track modification for merge logic
            return self._save()
        return True

    def get_pending_fills(self) -> List[PendingFill]:
        """Get all pending fills."""
        return list(self.pending_fills.values())

    def has_pending_fill(self, fill_key: str) -> bool:
        """Check if a fill is already in the journal."""
        return fill_key in self.pending_fills

    def clear(self) -> None:
        """Clear all pending fills (for testing)."""
        self.pending_fills.clear()
        self.corrupted = False
        self._save()
