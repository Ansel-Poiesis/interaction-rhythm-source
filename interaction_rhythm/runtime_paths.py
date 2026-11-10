"""Shared runtime path resolution for packaged, source, and isolated runs."""
from __future__ import annotations

import os
from pathlib import Path


DATA_DIR_ENV = "INTERACTION_RHYTHM_DATA_DIR"


def resolve_data_dir(default_root: Path) -> Path:
    override = os.getenv(DATA_DIR_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{DATA_DIR_ENV} must be an absolute path")
    else:
        path = Path(default_root) / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path
