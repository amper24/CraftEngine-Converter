"""Filesystem helpers for locating bundled schemas, mappings, and source docs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Support PyInstaller frozen execution: schemas/mappings are bundled next to
# the executable (or inside _MEIPASS for onefile builds).
if getattr(sys, "frozen", False):  # pragma: no cover - executed when frozen
    _BASE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _BASE = Path(__file__).resolve().parent.parent

PACKAGE_ROOT = _BASE
PROJECT_ROOT = _BASE

SCHEMAS_DIR = _BASE / "schemas"
MAPPINGS_DIR = _BASE / "mappings"


def schema_dir(target: str = "craftengine", version: str = "26.8") -> Path:
    return SCHEMAS_DIR / target / version


def mapping_file(name: str) -> Path:
    return MAPPINGS_DIR / name


def settings_file() -> Path:
    """Return the path to settings.yml (next to the executable/source root)."""
    return app_root() / "settings.yml"


def app_root() -> Path:
    """Directory for user-writable files (settings, outputs)."""
    env = os.environ.get("CRAFTENGINE_CONVERTER_HOME")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return PROJECT_ROOT