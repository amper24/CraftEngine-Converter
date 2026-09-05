"""ModDetector: identify loader, mod id, namespace, minecraft version, deps."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .archive import ModArchive
from .util import Log

_NAMESPACE_RE = re.compile(r"^[a-z0-9_.-]+$")


@dataclass
class ModMetadata:
    id: str = ""
    name: str = ""
    version: str = ""
    loader: str = "unknown"
    namespace: str = "minecraft"
    minecraft_versions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    optional_dependencies: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    metadata_conflicts: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "loader": self.loader,
            "namespace": self.namespace,
            "minecraft_versions": self.minecraft_versions,
            "dependencies": self.dependencies,
            "optional_dependencies": self.optional_dependencies,
            "source_files": self.source_files,
            "metadata_conflicts": self.metadata_conflicts,
            "raw": self.raw,
        }


class ModDetector:
    def __init__(self, log: Log) -> None:
        self.log = log

    def detect(self, archive: ModArchive) -> ModMetadata:
        meta = ModMetadata()

        fabric = archive.read_json("fabric.mod.json")
        quilt = archive.read_json("quilt.mod.json")
        forges = [n for n in archive.names if n.endswith("mods.toml")]

        candidates: list[tuple[str, ModMetadata]] = []

        if isinstance(fabric, dict):
            fm = self._parse_fabric(fabric)
            if fm.id:
                fm.loader = "fabric"
                fm.source_files.append("fabric.mod.json")
                candidates.append(("fabric.mod.json", fm))

        if isinstance(quilt, dict):
            qm = self._parse_quilt(quilt)
            if qm.id:
                qm.loader = "quilt"
                qm.source_files.append("quilt.mod.json")
                candidates.append(("quilt.mod.json", qm))

        for toml_path in forges:
            text = archive.read_text(toml_path)
            if text is None:
                continue
            fm = self._parse_forge_toml(text)
            is_neo = "neoforge" in toml_path.lower()
            fm.loader = "neoforge" if is_neo else "forge"
            fm.source_files.append(toml_path)
            candidates.append((toml_path, fm))

        if not candidates:
            # Fall back to inferred namespace from assets/data directories.
            namespace = self._infer_namespace(archive)
            meta.namespace = namespace
            meta.id = namespace
            meta.name = namespace
            meta.loader = "unknown"
            meta.raw = {"detection": "directory-inference"}
            self.log.info("no loader metadata found; inferred namespace", namespace=namespace)
            return meta

        # Prefer forge/neoforge metadata (most common), else fabric, else quilt.
        chosen = self._choose_candidate(candidates)
        path, meta = chosen
        meta.namespace = self._resolve_namespace(meta, archive)
        meta.raw = {"metadata_file": path}
        return meta

    def _parse_fabric(self, data: dict[str, Any]) -> ModMetadata:
        meta = ModMetadata()
        meta.id = str(data.get("id", "") or "")
        meta.name = str(data.get("name") or data.get("id") or "")
        meta.version = str(data.get("version") or "")
        if isinstance(data.get("depends"), dict):
            meta.dependencies = sorted(data["depends"].keys())
        if isinstance(data.get("suggests"), dict):
            meta.optional_dependencies = sorted(data["suggests"].keys())
        return meta

    def _parse_quilt(self, data: dict[str, Any]) -> ModMetadata:
        meta = ModMetadata()
        qm = data.get("quilt_loader", {}) if isinstance(data, dict) else {}
        meta.id = str(qm.get("id", "") or "")
        meta.name = str(qm.get("metadata", {}).get("name", "") or meta.id)
        meta.version = str(qm.get("version") or "")
        if isinstance(qm.get("depends"), list):
            meta.dependencies = sorted(str(d.get("id", "")) for d in qm["depends"] if d.get("id"))
        return meta

    def _parse_forge_toml(self, text: str) -> ModMetadata:
        meta = ModMetadata()
        mod_id = self._first_match(text, r"modId\s*=\s*\"([^\"]+)\"")
        display = self._first_match(text, r"displayName\s*=\s*\"([^\"]+)\"")
        version = self._first_match(text, r"version\s*=\s*\"([^\"]+)\"")
        meta.id = mod_id or ""
        meta.name = display or mod_id or ""
        meta.version = version or ""

        deps_section = False
        for line in text.splitlines():
            stripped = line.strip()
            if "[[" in stripped and "dependencies" in stripped:
                deps_section = True
            elif stripped.startswith("[") and deps_section:
                deps_section = False
            if deps_section:
                m = re.search(r"modId\s*=\s*\"([^\"]+)\"", line)
                if m:
                    dep = m.group(1)
                    if "mandatory=true" in line or "mandatory = true" in line:
                        meta.dependencies.append(dep)
                    else:
                        meta.optional_dependencies.append(dep)

        meta.dependencies = sorted(set(meta.dependencies))
        meta.optional_dependencies = sorted(set(meta.optional_dependencies))
        return meta

    @staticmethod
    def _first_match(text: str, pattern: str) -> str | None:
        m = re.search(pattern, text)
        return m.group(1) if m else None

    def _resolve_namespace(self, meta: ModMetadata, archive: ModArchive) -> str:
        if meta.id and _NAMESPACE_RE.match(meta.id):
            return meta.id
        inferred = self._infer_namespace(archive)
        return inferred if inferred != "minecraft" else (meta.id or "minecraft")

    def _infer_namespace(self, archive: ModArchive) -> str:
        namespaces: set[str] = set()
        for name in archive.names:
            if name.startswith("assets/"):
                parts = name.split("/")
                if len(parts) >= 2 and parts[1]:
                    namespaces.add(parts[1])
            if name.startswith("data/"):
                parts = name.split("/")
                if len(parts) >= 2 and parts[1] and parts[1] != "minecraft":
                    namespaces.add(parts[1])
        if not namespaces:
            return "minecraft"
        return sorted(namespaces)[0]

    def _choose_candidate(self, candidates: list[tuple[str, ModMetadata]]) -> tuple[str, ModMetadata]:
        order = {"forge": 0, "neoforge": 1, "fabric": 2, "quilt": 3, "unknown": 4}
        candidates.sort(key=lambda item: order.get(item[1].loader, 4))
        return candidates[0]