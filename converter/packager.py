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

from . import assetindex, capability, generator, schema, status, util
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
        # archive access). Here we only record provenance metadata. The full
        # assets/ tree is recorded so it is obvious that nothing was filtered.
        imported: list[dict[str, Any]] = []
        for resource in self.analysis.resources:
            if not resource.source_path.startswith("assets/"):
                continue
            imported.append(
                {
                    "source": resource.source_path,
                    "output": f"resourcepack/{resource.source_path}",
                    "sha256": resource.raw_hash,
                    "referenced_by": list(resource.references),
                }
            )
        util.write_json(rp_dir / "pack-manifest.json", {
            "verbatim": bool(getattr(self.settings, "copy_all_assets", True)),
            "imported": imported,
        })

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
            "content_namespaces": list(getattr(self.analysis, "content_namespaces", [])),
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

        (reports / "semantics.md").write_text(self._semantics_md(), encoding="utf-8", newline="\n")

        coverage = self._coverage_report()
        util.write_json(reports / "coverage.json", coverage)
        (reports / "coverage.md").write_text(self._coverage_md(coverage), encoding="utf-8", newline="\n")

    def _semantics_md(self) -> str:
        """Human-readable neural classification report.

        Answers, per object: what the network thinks it is, how confident it
        is, what it changed in the conversion, and which in-game commands
        verify the result.
        """
        semantic = getattr(self.analysis, "semantics", None)
        lines = ["# Нейросемантическая классификация", ""]
        if semantic is None or not getattr(semantic, "enabled", False):
            reason = getattr(semantic, "reason", "") or "недоступно"
            lines.append(f"Микро-нейросеть не использовалась: {reason}.")
            lines.append("")
            lines.append("Конвертация выполнена на эвристиках по именам и тегам.")
            return "\n".join(lines)

        counts = semantic.counts()
        lines.append(f"Классифицировано объектов: **{len(semantic.records)}**")
        lines.append("")
        if counts:
            lines.append("| Класс | Объектов |")
            lines.append("|---|---:|")
            for label, count in counts.items():
                lines.append(f"| {label} | {count} |")
            lines.append("")

        for domain, title in (("item", "Предметы"), ("block", "Блоки")):
            group = [r for r in semantic.records.values() if r.domain == domain]
            if not group:
                continue
            lines.append(f"## {title}")
            lines.append("")
            lines.append("| Объект | Определено как | Применено | Команды |")
            lines.append("|---|---|---|---|")
            for record in sorted(group, key=lambda r: r.object_id):
                applied = ", ".join(f"{k}={v}" for k, v in record.applied.items() if not k.endswith("_previous")) or "—"
                cmds = "<br>".join(f"`{c}`" for c in record.commands) or "—"
                lines.append(f"| `{record.object_id}` | {record.description} | {applied} | {cmds} |")
            lines.append("")

        return "\n".join(lines)

    def _coverage_report(self) -> dict[str, Any]:
        """Compute asset/model/texture coverage metrics.

        The resourcepack is copied verbatim, so "coverage" here does not mean
        "number of generated YAML objects". It asks three questions the TZ
        lists as mandatory:

        1. What fraction of source models/textures are reachable through an IR
           object (item/block/recipe/sliceboard)?
        2. Which references point at an asset that is actually present under a
           converted/imported namespace?
        3. Which copied models/textures are not referenced by any object and
           therefore deserve a manual-review row?
        """
        content_ns = set(getattr(self.analysis, "content_namespaces", []) or [])
        # Asset coverage is namespace-scoped to namespaces that actually ship
        # resources in this jar. Data-only dependency namespaces (c:, neoforge,
        # farmersdelight when only tags are present) must not be treated as
        # "mod-owned" assets, otherwise external references appear missing.
        asset_ns = {
            resource.namespace for resource in self.analysis.resources
            if getattr(resource, "is_resourcepack", False) and resource.namespace
        }
        content_ns = content_ns & asset_ns
        item_model_refs: set[str] = set()
        item_texture_refs: set[str] = set()
        block_model_refs: set[str] = set()
        block_texture_refs: set[str] = set()
        for item in self.analysis.items.values():
            item_model_refs.update(item.model_refs or [])
            item_texture_refs.update(item.texture_refs or [])
        for block in self.analysis.blocks.values():
            block_model_refs.update(block.model_refs or [])
            block_texture_refs.update(block.texture_refs or [])

        all_model_refs = item_model_refs | block_model_refs
        all_texture_refs = item_texture_refs | block_texture_refs

        # Index the verbatim assets by their normalized CraftEngine ref.
        source_models: dict[str, str] = {}
        source_textures: dict[str, str] = {}
        resourcepack_files = 0
        domain_counts: dict[str, int] = {}
        referenced_resource_paths: set[str] = set()
        for resource in self.analysis.resources:
            if not getattr(resource, "is_resourcepack", False):
                continue
            if resource.source_path.startswith("assets/minecraft/"):
                # Vanilla overrides are copied but are not "owned" by the mod
                # for coverage purposes; count them in the verbatim total only.
                pass
            resourcepack_files += 1
            domain = getattr(resource, "asset_domain", None)
            domain_counts[domain or "other"] = domain_counts.get(domain or "other", 0) + 1
            model_ref = assetindex.source_path_to_model_ref(resource.source_path)
            texture_ref = assetindex.source_path_to_texture_ref(resource.source_path)
            if model_ref:
                source_models[model_ref] = resource.source_path
            if texture_ref:
                source_textures[texture_ref] = resource.source_path
            if resource.parsed.get("_converter_referenced"):
                referenced_resource_paths.add(resource.source_path)

        # References in mod-owned namespaces that do not resolve to a copied
        # asset are genuine missing assets. External namespaces are kept as
        # "external" rather than "missing" because a dependency pack may supply
        # them at runtime.
        def ref_split(ref: str) -> tuple[str, str]:
            ns, _, rel = ref.partition(":")
            return ns, rel

        missing_models: list[str] = []
        missing_textures: list[str] = []
        external_models: list[str] = []
        external_textures: list[str] = []
        for ref in sorted(all_model_refs):
            ns, _ = ref_split(ref)
            if ns == "minecraft":
                continue
            if ns not in content_ns:
                external_models.append(ref)
            elif ref not in source_models:
                missing_models.append(ref)
        for ref in sorted(all_texture_refs):
            ns, _ = ref_split(ref)
            if ns == "minecraft":
                continue
            if ns not in content_ns:
                external_textures.append(ref)
            elif ref not in source_textures:
                missing_textures.append(ref)

        # Orphans: verbatim model/texture files under a content namespace that
        # no object references. Helper textures (atlas overrides, GUI variants
        # that are consumed by raw model parents but not listed in an IR node)
        # are still reported, because the TZ wants visibility over guesses.
        referenced_model_refs = {assetindex.source_path_to_model_ref(p) for p in referenced_resource_paths}
        referenced_texture_refs = {assetindex.source_path_to_texture_ref(p) for p in referenced_resource_paths}
        orphan_models = sorted(
            ref for ref in source_models
            if ref.split(":", 1)[0] in content_ns and ref not in referenced_model_refs
        )
        orphan_textures = sorted(
            ref for ref in source_textures
            if ref.split(":", 1)[0] in content_ns and ref not in referenced_texture_refs
        )

        by_namespace: dict[str, dict[str, int]] = {}
        for ref in sorted(set(source_models) | set(source_textures)):
            ns = ref.split(":", 1)[0]
            if ns not in content_ns:
                continue
            bucket = by_namespace.setdefault(ns, {
                "models": 0, "textures": 0, "referenced_models": 0,
                "referenced_textures": 0, "orphan_models": 0, "orphan_textures": 0,
            })
            if ref in source_models:
                bucket["models"] += 1
                if ref in referenced_model_refs:
                    bucket["referenced_models"] += 1
                else:
                    bucket["orphan_models"] += 1
            if ref in source_textures:
                bucket["textures"] += 1
                if ref in referenced_texture_refs:
                    bucket["referenced_textures"] += 1
                else:
                    bucket["orphan_textures"] += 1

        def coverage_pct(covered: int, total: int) -> float:
            return round(covered / total * 100.0, 2) if total else 0.0

        return {
            "resourcepack": {
                "verbatim": bool(getattr(self.settings, "copy_all_assets", True)),
                "total_files": resourcepack_files,
                "by_domain": dict(sorted(domain_counts.items())),
                "models": len(source_models),
                "textures": len(source_textures),
                "referenced_models": len(referenced_model_refs),
                "referenced_textures": len(referenced_texture_refs),
                "orphan_models": len(orphan_models),
                "orphan_textures": len(orphan_textures),
                "model_coverage_pct": coverage_pct(len(referenced_model_refs), len(source_models)),
                "texture_coverage_pct": coverage_pct(len(referenced_texture_refs), len(source_textures)),
            },
            "object_references": {
                "model_refs": len(all_model_refs),
                "texture_refs": len(all_texture_refs),
                "missing_models": missing_models,
                "missing_textures": missing_textures,
                "external_models": external_models,
                "external_textures": external_textures,
            },
            "by_namespace": dict(sorted(by_namespace.items())),
            "orphan_models": orphan_models,
            "orphan_textures": orphan_textures,
        }

    @staticmethod
    def _coverage_md(coverage: dict[str, Any]) -> str:
        rp = coverage["resourcepack"]
        refs = coverage["object_references"]
        lines = [
            "# Coverage",
            "",
            "## Resourcepack",
            "",
            f"- Verbatim copy: {str(rp['verbatim']).lower()}",
            f"- Total asset files: {rp['total_files']}",
            f"- Models: {rp['models']} (referenced {rp['referenced_models']}, orphans {rp['orphan_models']})",
            f"- Textures: {rp['textures']} (referenced {rp['referenced_textures']}, orphans {rp['orphan_textures']})",
            f"- Model coverage: {rp['model_coverage_pct']}%",
            f"- Texture coverage: {rp['texture_coverage_pct']}%",
            "",
            "## Object references",
            "",
            f"- Item/block model refs: {refs['model_refs']}",
            f"- Item/block texture refs: {refs['texture_refs']}",
            f"- Missing mod-owned models: {len(refs['missing_models'])}",
            f"- Missing mod-owned textures: {len(refs['missing_textures'])}",
            f"- External dependency refs: {len(refs['external_models']) + len(refs['external_textures'])}",
            "",
        ]
        if refs["missing_models"] or refs["missing_textures"]:
            lines.append("### Missing assets")
            lines.append("")
            for ref in refs["missing_models"]:
                lines.append(f"- model `{ref}`")
            for ref in refs["missing_textures"]:
                lines.append(f"- texture `{ref}`")
            lines.append("")
        return "\n".join(lines) + "\n"

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

        # Recommendations the neural pass deliberately did not apply on its
        # own because they need source data it cannot invent.
        advisories = self._brain_advisories()
        if advisories:
            lines.append("## Рекомендации микро-нейросети")
            lines.append("")
            for object_id, reason in advisories:
                lines.append(f"[LOW] {object_id}")
                lines.append(f"Reason: {reason}")
                lines.append("")
        return "\n".join(lines) + "\n"

    def _brain_advisories(self) -> list[tuple[str, str]]:
        semantic = getattr(self.analysis, "semantics", None)
        if semantic is None or not getattr(semantic, "enabled", False):
            return []
        out: list[tuple[str, str]] = []
        for record in sorted(semantic.records.values(), key=lambda r: r.object_id):
            for head, reason in record.skipped.items():
                if "recommended" in reason:
                    out.append((record.object_id, f"{head}: {reason}"))
        return out

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
                "content_namespaces": list(getattr(self.analysis, "content_namespaces", [])),
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
- `reports/coverage.md` — asset/model/texture coverage and orphan list.
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
