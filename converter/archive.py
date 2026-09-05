"""Import layer: read mod JAR or directory into a uniform archive view."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Iterable

from .util import Log


class ModArchive:
    """Uniform read-only view over a mod JAR file or an unpacked directory."""

    def __init__(self, path: Path, log: Log) -> None:
        self.path = Path(path)
        self.log = log
        self._zip: zipfile.ZipFile | None = None
        self._names: list[str] = []
        self._dir_base: Path | None = None
        if self.path.is_file():
            self._zip = zipfile.ZipFile(self.path)
            self._names = [n for n in self._zip.namelist() if not n.endswith("/")]
        elif self.path.is_dir():
            self._dir_base = self.path
            self._names = [
                str(p.relative_to(self.path)).replace("\\", "/")
                for p in self.path.rglob("*")
                if p.is_file()
            ]
        else:
            self.log.error("input path does not exist", path=str(self.path))

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def __enter__(self) -> "ModArchive":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def exists(self, name: str) -> bool:
        return name in self._names

    def read(self, name: str) -> bytes | None:
        if self._zip is not None:
            try:
                return self._zip.read(name)
            except KeyError:
                return None
        if self._dir_base is not None:
            safe = self._dir_base.joinpath(*name.split("/")).resolve()
            if not str(safe).startswith(str(self._dir_base.resolve())):
                return None
            if safe.is_file():
                return safe.read_bytes()
        return None

    def read_text(self, name: str) -> str | None:
        data = self.read(name)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")

    def read_json(self, name: str) -> object | None:
        text = self.read_text(name)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            self.log.error("malformed json", path=name)
            return None

    def iter_prefix(self, prefix: str) -> Iterable[str]:
        for name in self._names:
            if name.startswith(prefix):
                yield name

    def json_files_under(self, prefix: str) -> Iterable[tuple[str, object]]:
        for name in self._names:
            if name.startswith(prefix) and name.endswith(".json"):
                parsed = self.read_json(name)
                if parsed is not None:
                    yield name, parsed


def open_archive(path: str | Path, log: Log) -> ModArchive:
    return ModArchive(Path(path), log)