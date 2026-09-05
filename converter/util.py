
"""Small shared utilities: hashing, filesystem-safe names, structured logging."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    """Return a filesystem-safe basename, preserving lowercase budget."""
    value = value.strip().lower()
    value = _SAFE_NAME_RE.sub("_", value)
    value = value.strip("._")
    return value or "unnamed"


def split_id(namespaced_id: str) -> tuple[str, str]:
    """Split 'namespace:path' into (namespace, path), falling back to 'minecraft'."""
    if ":" not in namespaced_id:
        return "minecraft", namespaced_id
    ns, _, path = namespaced_id.partition(":")
    return ns or "minecraft", path or "item"


def ensure_namespaced(value: str, default_ns: str = "minecraft") -> str:
    if ":" in value:
        return value
    return f"{default_ns}:{value}"


class Log:
    """Structured + human-readable conversion logger.

    Always emits concise human lines to stderr and, when configured, mirrors
    the same events as JSONL into the conversion report directory.
    """

    LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}

    def __init__(self, verbose: bool = False, level: str | None = None) -> None:
        self.verbose = verbose
        self.level = (level or ("DEBUG" if verbose else "INFO")).upper()
        self._jsonl = None
        self._human = None

    def attach_file(self, human_path: Path | None = None, jsonl_path: Path | None = None) -> None:
        if human_path:
            human_path.parent.mkdir(parents=True, exist_ok=True)
            self._human = human_path.open("a", encoding="utf-8", newline="\n")
        if jsonl_path:
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self._jsonl = jsonl_path.open("a", encoding="utf-8", newline="\n")

    def close(self) -> None:
        for fh in (self._human, self._jsonl):
            if fh:
                fh.close()
        self._human = self._jsonl = None

    def _emit(self, level: str, message: str, **fields: Any) -> None:
        if self.LEVELS.get(level.upper(), 20) < self.LEVELS.get(self.level, 20):
            return
        ts = datetime.now().astimezone().strftime("%H:%M:%S")
        safe_fields = {k: v for k, v in fields.items() if v is not None}
        suffix = " " + " ".join(f"{k}={v}" for k, v in safe_fields.items()) if safe_fields else ""
        human = f"[{ts}] [{level.upper():5}] {message}{suffix}"
        print(human, file=sys.stderr)
        if self._human:
            self._human.write(human + "\n"); self._human.flush()
        record = {"timestamp": utc_now_iso(), "level": level.lower(), "message": message, **safe_fields}
        if self._jsonl:
            self._jsonl.write(json.dumps(record, ensure_ascii=False) + "\n"); self._jsonl.flush()

    def info(self, message: str, **fields: Any) -> None: self._emit("INFO", message, **fields)
    def warn(self, message: str, **fields: Any) -> None: self._emit("WARN", message, **fields)
    def error(self, message: str, **fields: Any) -> None: self._emit("ERROR", message, **fields)
    def debug(self, message: str, **fields: Any) -> None: self._emit("DEBUG", message, **fields)

    def phase(self, name: str, message: str, **fields: Any) -> None:
        self.info(message, phase=name.upper(), **fields)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(stable_json_dumps(obj))
        fh.write("\n")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def write_yaml_ordered(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(obj, fh, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)
