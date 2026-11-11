#!/usr/bin/env python3
"""Compatibility entry point onto the single importer implementation.

The importer lives in ``backend/scripts/import_real_data.py``. This file exists
only so that ``python3 scripts/import_real_data.py`` keeps working: it loads that
module and hands over the command line, and holds no check of its own — not the
hash, not the payload validation, not the safety gate, not the write. A caller
cannot reach the warehouse through a softer checklist by choosing this path, and
``test_import_entrypoints.py`` asserts that the two entries resolve the same
functions and refuse the same way.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CORE_PATH = Path(__file__).resolve().parent.parent / "backend" / "scripts" / "import_real_data.py"

_spec = importlib.util.spec_from_file_location("alethra_import_real_data_core", _CORE_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load the importer core at {_CORE_PATH}")
_core = importlib.util.module_from_spec(_spec)
sys.modules.setdefault(_spec.name, _core)
_spec.loader.exec_module(_core)


def __getattr__(name: str):
    """Resolve every other attribute through the core, so nothing drifts."""
    return getattr(_core, name)


def main(argv=None) -> int:
    """Return the core's exit code; a refusal propagates, as in the core."""
    return _core.main(list(sys.argv[1:]) if argv is None else argv)


def cli(argv=None) -> int:
    return _core.cli(list(sys.argv[1:]) if argv is None else argv)


if __name__ == "__main__":
    sys.exit(cli())
