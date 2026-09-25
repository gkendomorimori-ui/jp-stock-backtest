"""YAML configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

#: Repository root (``src/utils/config_loader.py`` -> two levels up from ``src``).
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
CONFIG_DIR: Path = PROJECT_ROOT / "config"


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file into a dict. Relative paths are resolved from the project root."""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"top-level YAML in {p} must be a mapping")
    return data
