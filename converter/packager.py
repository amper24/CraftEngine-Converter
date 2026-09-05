"""Output packager: write the organized converter output directory.

Layout (TZ §20.2): configuration/, resourcepack/, extensions/, source-map/,
reports/, manifest.yml, README.md. Every generated file is converter-owned and
tracked in the manifest/source-map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import capability, generator, schema, status, util
from .analyzer import AnalysisResult
from .detector import ModMetadata
from .util import Log


@dataclass
class OwnershipEntry:
    path: str
    owner: str
    generated_from: str
    source_hash: str = ""
    generator_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "owner": self.owner,
            "generated_from": self.generated_from,
            "source_hash": self.source_hash,
            "generator_hash": self.generator_hash,
        }


@dataclass
class PackageResult:
    output_dir: Path
    counts: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class Packager:
    def __init__(
        self,
        analysis: AnalysisResult,
        mapping: capability.MappingResult,
        generation: generator.GenerationResult,
        output_dir: Path,
        log: Log,
        generator_version: str,
        settings: Any = None,
    ) -> None:
        self.analysis = analysis
        self.mapping = mapping
        self.generation = generation
        self.output_dir = output_dir
        self.log = log
        self.generator_version = generator_version
        self.registry = schema.get_registry()
        from .config import Settings

        self.settings = settings or Settings()
        self.layout = self.settings.output_layout or "split"

    def build(self) -> PackageResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ownership = self._write_craftengine_files()
        self._write_pack_metadata()
        self._write_category_lang_resources()
        self._write_resourcepack()
        self._write_source_map(ownership)
        self._write_reports()
        self._write_manifest(ownership)
        self._write_readme()

        counts = {
            "items": len(self.analysis.items),
            "blocks": len(self.analysis.blocks),
            "recipes": len(self.analysis.recipes),
            "furniture": 0,
            "loot": len(self.analysis.loot),
            "resources": len(self.analysis.resources),
        }
        statuses = self.mapping.counts()

        result = PackageResult(
            output_dir=self.output_dir,
            counts=counts,
            statuses=statuses,
            errors=[],
        )
        return result

    # --- craftengine files -------------------------------------------------
    def _write_pack_metadata(self) -> None:
        """Write the CraftEngine ``pack.yml`` at the pack root.

        CraftEngine discovers packs by iterating the directories under its
        resources folder and reading ``pack.yml`` for the namespace/author/
        version/description metadata. Without it the output directory can be
        loaded only with the folder-name default namespace and with empty
        metadata, so this file is generated for every conversion.
        """
        meta = self.analysis.metadata
        namespace = meta.namespace or "minecraft"
        payload = {
            "enable": True,
            "namespace": namespace,
            "author": (meta.name or "unknown"),
            "version": (meta.version or self.generator_version),
            "description": "Converted from %s (%s) by CraftEngine Mod Converter" % (
                meta.id or namespace,
                meta.loader or "unknown",
            ),
        }
        (self.output_dir / "pack.yml").write_text(
            generator.dump_yaml(payload),
            encoding="utf-8",
            newline="\n",
        )

    def _write_craftengine_files(self) -> list[OwnershipEntry]:
        ownership: list[OwnershipEntry] = []
        if self.layout == "single":
            return self._write_single_file()
        for f in self.generation.files:
            target = self.output_dir / f.rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.content, encoding="utf-8", newline="\n")
            ownership.append(
                OwnershipEntry(
                    path=f.rel_path,
                    owner="mod-converter",
                    generated_from=f.object_id,
                    source_hash=f.source_hash,
                    generator_hash=self.generator_version,
                )
            )
        return ownership

    def _write_single_file(self) -> list[OwnershipEntry]:
        """Merge all generated objects into one configuration/all.yml."""
        import yaml

        merged: dict[str, dict] = {}
        for f in self.generation.files:
            if not f.rel_path.startswith("configuration/"):
                continue
            doc = yaml.safe_load(f.content)
            if not isinstance(doc, dict):
                continue
            for section, value in doc.items():
                if not isinstance(value, dict):
                    continue
                merged.setdefault(section, {}).update(value)

        target = self.output_dir / "configuration" / "all.yml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            generator.dump_yaml(merged),
            encoding="utf-8",
            newline="\n",
        )
        return [
            OwnershipEntry(
                path="configuration/all.yml",
                owner="mod-converter",
                generated_from="*",
                generator_hash=self.generator_version,
            )
        ]

    def _write_category_lang_resources(self) -> None:
        """Materialize generated CE category translation YAML into MC lang JSON.

        The category config uses <lang:craftengine.category.*> keys. Writing them
        into the generated namespace lang files gives both English and Russian
        UI labels while preserving the source mod language files.
        """
        if not getattr(self.settings, "generate_category_translations", True):
            return
        rp = self.output_dir / "resourcepack"
        import json
        for f in self.generation.files:
            if f.domain != "lang":
                continue
            try:
                doc = yaml.safe_load(f.content)
            except Exception:
                continue
            trans = doc.get("translations", {}) if isinstance(doc, dict) else {}
            ns = f.object_id.split(":",1)[0]
            for locale, values in trans.items():
                if not isinstance(values, dict):
                    continue
                path = rp / "assets" / ns / "lang" / f"{locale}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                existing = {}
                if path.exists():
                    try: existing = json.loads(path.read_text(encoding="utf-8"))
                    except Exception: existing = {}
                existing.update({str(k): str(v) for k,v in values.items()})
                path.write_text(json.dumps(dict(sorted(existing.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # --- resourcepack ------------------------------------------------------
    def _write_resourcepack(self) -> None:
        rp_dir = self.output_dir / "resourcepack"
        rp_dir.mkdir(parents=True, exist_ok=True)
        mcmeta = {
            "pack": {
                "pack_format": 34,
                "description": f"Converted by craftengine-jar-converter {self.generator_version}",
            }
        }
        util.write_json(rp_dir / "pack.mcmeta", mcmeta)

        # Resource bytes are copied verbatim by the conversion driver (which has
        # archive access). Here we only record provenance metadata.
        imported: list[dict[str, Any]] = []
        for resource in self.analysis.resources:
            if resource.kind in ("asset", "textures", "models", "blockstates", "sounds", "particles", "fonts", "lang", "equipment"):
                src = resource.source_path
                imported.append(
                    {
                        "source": src,
                        "output": f"resourcepack/{src}",
                        "sha256": resource.raw_hash,
                        "referenced_by": list(resource.references),
                    }
                )
        util.write_json(rp_dir / "pack-manifest.json", {"imported": imported})

    # --- source-map --------------------------------------------------------
    def _write_source_map(self, ownership: list[OwnershipEntry]) -> None:
        sm_dir = self.output_dir / "source-map"
        sm_dir.mkdir(parents=True, exist_ok=True)

        index: dict[str, dict[str, Any]] = {
            "items": {},
            "blocks": {},
            "recipes": {},
            "furniture": {},
            "loot": {},
            "sliceboard": {},
            "categories": {},
            "sounds": {},
        }
        bucket_by_domain = {
            status.DOMAIN_ITEM: "items",
            status.DOMAIN_BLOCK: "blocks",
            status.DOMAIN_RECIPE: "recipes",
            status.DOMAIN_FURNITURE: "furniture",
            status.DOMAIN_LOOT: "loot",
            status.DOMAIN_SLICEBOARD: "sliceboard",
            "category": "categories",
            "sound": "sounds",
        }
        for f in self.generation.files:
            bucket = index.get(bucket_by_domain.get(f.domain, "items"))
            bucket[f.object_id] = {
                "file": f.rel_path,
                "status": f.result,
                "confidence": self.mapping.get(f.object_id).confidence if self.mapping.get(f.object_id) else 0.0,
            }

        for key, bucket in index.items():
            util.write_json(sm_dir / f"{key}-index.json", bucket)

        util.write_json(sm_dir / "index.json", index)
        util.write_json(sm_dir / "objects.json", {f.object_id: f.to_dict() for f in self.generation.files})
        util.write_json(sm_dir / "ownership.json", [o.to_dict() for o in ownership])
        util.write_json(sm_dir / "mappings.json", self.mapping.to_dict())

        resources = [r.to_dict() for r in self.analysis.resources]
        util.write_json(sm_dir / "resources.json", resources)
        util.write_json(sm_dir / "registries.json", {"metadata": self.analysis.metadata.to_dict()})

    # --- reports -----------------------------------------------------------
    def _write_reports(self) -> None:
        reports = self.output_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)

        counts = {
            "items": len(self.analysis.items),
            "blocks": len(self.analysis.blocks),
            "recipes": len(self.analysis.recipes),
            "furniture": 0,
            "loot": len(self.analysis.loot),
            "resources": len(self.analysis.resources),
        }
        statuses = self.mapping.counts()
        diag_count = len(self.generation.diagnostics)

        summary = {
            "source": self.analysis.metadata.to_dict(),
            "target": {"platform": "craftengine", "version": "26.8"},
            "counts": counts,
            "statuses": statuses,
            "diagnostics_count": diag_count,
            "completeness_check": {
                "detected_items": counts["items"],
                "generated_files": len(self.generation.files),
                "diagnostic_only": diag_count,
            },
        }
        util.write_json(reports / "summary.json", summary)

        # Exact values recovered from compiled FoodProperties/FoodComponent
        # builders. Keeps the raw source evidence alongside the generated YAML.
        util.write_json(reports / "bytecode-food.json", {
            "enabled": bool(getattr(self.settings, "bytecode_food_enabled", True)),
            "detected": len(getattr(self.analysis, "bytecode_foods", {}) or {}),
            "matched_to_items": sum(1 for item in self.analysis.items.values() if item.metadata.get("food_source")),
            "unmatched": list(getattr(self.analysis, "bytecode_food_unmatched", []) or []),
            "foods": dict(sorted((getattr(self.analysis, "bytecode_foods", {}) or {}).items())),
        })
        util.write_json(reports / "bytecode-semantics.json", {
            "enabled": bool(getattr(self.settings, "bytecode_semantic_enabled", True)),
            "matched_items": len(getattr(self.analysis, "bytecode_semantics", {}) or {}),
            "facts": dict(sorted((getattr(self.analysis, "bytecode_semantics", {}) or {}).items())),
        })

        unsupported = []
        partial = []
        direct = []
        for decision in self.mapping.decisions.values():
            entry = decision.to_dict()
            if decision.result == status.UNSUPPORTED:
                unsupported.append(entry)
            elif decision.result in (status.PARTIAL, status.MANUAL, status.EXTENSION, status.SCRIPT):
                partial.append(entry)
            elif decision.result in (status.DIRECT, status.TRANSFORM):
                direct.append(entry)

        util.write_json(reports / "unsupported.json", unsupported)
        util.write_json(reports / "partial.json", partial)

        # Markdown reports
        (reports / "summary.md").write_text(self._summary_md(counts, statuses), encoding="utf-8", newline="\n")
        (reports / "unsupported.md").write_text(self._unsupported_md(unsupported), encoding="utf-8", newline="\n")
        (reports / "partial.md").write_text(self._partial_md(partial), encoding="utf-8", newline="\n")
        (reports / "manual-tasks.md").write_text(self._manual_tasks_md(partial + unsupported), encoding="utf-8", newline="\n")
        (reports / "validation.md").write_text(self._validation_md(), encoding="utf-8", newline="\n")
        (reports / "warnings.md").write_text("# Warnings\n\n" + "\n".join(f"- {w}" for w in self.analysis.warnings), encoding="utf-8", newline="\n")

    def _summary_md(self, counts: dict[str, int], statuses: dict[str, int]) -> str:
        lines = [
            "# Conversion Summary",
            "",
            f"- Items: {counts['items']}",
            f"- Blocks: {counts['blocks']}",
            f"- Recipes: {counts['recipes']}",
            f"- Loot: {counts['loot']}",
            f"- Resources: {counts['resources']}",
            "",
            "## Statuses",
            "",
        ]
        for level in status.CAPABILITY_LEVELS:
            lines.append(f"- {level}: {statuses.get(level, 0)}")
        return "\n".join(lines) + "\n"

    def _unsupported_md(self, unsupported: list[dict[str, Any]]) -> str:
        lines = ["# Unsupported Objects", ""]
        if not unsupported:
            lines.append("None.")
        for entry in unsupported:
            lines.append(f"## {entry['object_id']}")
            lines.append(f"- Status: {entry['result']}")
            lines.append(f"- Reason: {entry['reason']}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _partial_md(self, partial: list[dict[str, Any]]) -> str:
        lines = ["# Partially Converted Objects", ""]
        if not partial:
            lines.append("None.")
        for entry in partial:
            lines.append(f"## {entry['object_id']}")
            lines.append(f"- Status: {entry['result']}")
            lines.append(f"- Reason: {entry['reason']}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _manual_tasks_md(self, tasks: list[dict[str, Any]]) -> str:
        lines = ["# Manual Tasks", ""]
        if not tasks:
            lines.append("None.")
        for entry in tasks:
            level = "HIGH" if entry["result"] == status.UNSUPPORTED else "MEDIUM"
            lines.append(f"[{level}] {entry['object_id']}")
            lines.append(f"Reason: {entry['reason']}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _validation_md(self) -> str:
        return (
            "# Validation\n\n"
            "Static YAML/schema validation was performed by the converter. "
            "Runtime smoke-test requires an isolated Minecraft server with CraftEngine.\n"
        )

    # --- manifest & readme -------------------------------------------------
    def _write_manifest(self, ownership: list[OwnershipEntry]) -> None:
        meta = self.analysis.metadata
        manifest = {
            "converter": {"version": self.generator_version},
            "source": {
                "loader": meta.loader,
                "mod_id": meta.id,
                "mod_version": meta.version,
                "minecraft_version": self.analysis.minecraft_version,
            },
            "target": {"platform": "craftengine", "version": "26.8", "schema_version": "1"},
            "generation": {
                "timestamp": util.utc_now_iso(),
                "deterministic": True,
                "mapping_version": "0.4.2",
            },
            "counts": {
                "items": len(self.analysis.items),
                "blocks": len(self.analysis.blocks),
                "recipes": len(self.analysis.recipes),
                "furniture": 0,
                "loot": len(self.analysis.loot),
                "resources": len(self.analysis.resources),
            },
            "statuses": self.mapping.counts(),
            "ownership": [o.to_dict() for o in ownership],
        }
        util.write_json(self.output_dir / "manifest.yml", manifest)

    def _write_readme(self) -> None:
        meta = self.analysis.metadata
        readme = f"""# Converted Mod: {meta.name}

- Loader: {meta.loader}
- Mod id: {meta.id}
- Target Minecraft: {self.analysis.minecraft_version}
- Target CraftEngine: 26.8

## Installation

1. Copy `configuration/` into the CraftEngine configuration directory.
2. Copy `resourcepack/` assets into the CraftEngine resource directory (or
   use your configured pack pipeline).
3. Run `/ce reload config` for YAML and `/ce reload all` for resources/models.

## Reports

- `reports/summary.md` — conversion summary.
- `reports/unsupported.md` — fully unsupported objects.
- `reports/partial.md` — partially converted objects.
- `reports/manual-tasks.md` — manual follow-up tasks.
- `reports/validation.md` — validation notes.

## Source map

- `source-map/index.json` — object index with file mapping and status.

Generated by craftengine-jar-converter {self.generator_version}.
"""
        (self.output_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")


def build_package(
    analysis: AnalysisResult,
    mapping: capability.MappingResult,
    generation: generator.GenerationResult,
    output_dir: Path,
    log: Log,
    generator_version: str,
    settings: Any = None,
) -> PackageResult:
    return Packager(analysis, mapping, generation, output_dir, log, generator_version, settings).build()
