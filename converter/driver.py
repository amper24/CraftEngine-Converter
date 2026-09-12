"""Conversion driver: orchestrates detect -> analyze -> map -> generate ->
package -> validate, and exposes high-level functions for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__, analyzer, capability, generator, itemsadder, packager, semantics, validator, util, fidelity, status
from .archive import ModArchive, open_archive
from .config import Settings, load_settings
from .sliceboard import generate_sliceboard
from .util import Log


def detect_source_kind(archive: ModArchive, log: Log, mode: str = "auto") -> str:
    """Decide which import adapter handles this input.

    ``mode`` can force the choice; ``auto`` prefers ItemsAdder when the archive
    is an ItemsAdder content pack and falls back to the mod adapter otherwise.
    """
    if mode == "mod":
        return "mod"
    if mode == "itemsadder":
        if itemsadder.detect_itemsadder(archive, log) is None:
            log.warn("source_mode=itemsadder but no ItemsAdder content found; falling back to the mod adapter")
            return "mod"
        return "itemsadder"
    return "itemsadder" if itemsadder.detect_itemsadder(archive, log) is not None else "mod"



class ConversionDriver:
    def __init__(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        target: str,
        minecraft_version: str,
        log: Log,
        settings: Settings | None = None,
    ) -> None:
        self.input_path = Path(input_path)
        self.output_dir = Path(output_dir)
        self.target = target
        self.minecraft_version = minecraft_version
        self.log = log
        self.settings = settings

    def run(self) -> dict[str, Any]:
        settings = self.settings or load_settings(log=self.log).settings
        if getattr(settings, "write_conversion_log", True):
            reports = self.output_dir / "reports"
            self.log.attach_file(reports / "conversion.log", reports / "conversion.jsonl")
        self.log.phase("INIT", "Начало конвертации", source=str(self.input_path), output=str(self.output_dir))

        with open_archive(self.input_path, self.log) as archive:
            source_kind = detect_source_kind(archive, self.log, getattr(settings, "source_mode", "auto"))
            self.log.phase("DETECT", "Определение формата исходника", source=source_kind)

            if source_kind == "itemsadder":
                self.log.phase("ANALYZE", "Анализ ItemsAdder-пака (contents/)")
                ia_layout = itemsadder.detect_itemsadder(archive, self.log)
                ia_analyzer = itemsadder.ItemsAdderAnalyzer(self.log, self.minecraft_version, settings)
                analysis = ia_analyzer.analyze(archive, ia_layout)
            else:
                self.log.phase("ANALYZE", "Анализ исходного мода")
                analysis = analyzer.analyze_archive(archive, self.log, self.minecraft_version, settings)

            # Neural semantic pass: classify every detected object, apply the
            # safe conclusions to the IR and record what it decided.
            self.log.phase("BRAIN", "Нейросемантическая классификация объектов")
            semantic_result = semantics.apply_semantics(analysis, settings, self.log)
            analysis.semantics = semantic_result

            self.log.phase("MAPPING", "Построение semantic mapping")
            mapping = capability.build_mapping(analysis, settings)

            gen = generator.Generator(analysis, mapping, settings)
            recipe_gen = generator.RecipeGenerator(analysis, mapping, settings)
            loot_gen = generator.LootGenerator(analysis, mapping)

            self.log.phase("GENERATE", "Генерация CraftEngine configuration")
            generation = generator.generate_all(analysis, mapping, gen, recipe_gen, loot_gen)
            if generation is None:
                raise RuntimeError("generator.generate_all() returned None")

            # Generate SliceBoard integration package outside configuration so CraftEngine does not parse its DSL as CE recipes.
            # ItemsAdder packs never carry cutting-board recipes, so the
            # SliceBoard pass is skipped for that source.
            if source_kind != "itemsadder":
                for sb_file in generate_sliceboard(analysis, settings):
                    generation.files.append(sb_file)
                for warning in getattr(analysis, "sliceboard_warnings", []):
                    generation.diagnostics.append({"id": warning.get("recipe"), "domain": "sliceboard", **warning})

            # Categories are generated LAST, after items, blocks, recipes,
            # loot, sounds and SliceBoard. Membership is derived from the
            # final generated item set, not the raw discovery set.
            if getattr(settings, "generate_categories", True):
                gen._generate_categories(generation)
            generation.files.sort(key=lambda f: (
                90 if f.domain == "category" else 95 if f.domain == "lang" else
                70 if f.domain == status.DOMAIN_SLICEBOARD else
                60 if f.domain == status.DOMAIN_LOOT else
                50 if f.domain == status.DOMAIN_RECIPE else
                40 if f.domain == "sound" else
                25 if f.domain == status.DOMAIN_FURNITURE else
                20 if f.domain == status.DOMAIN_BLOCK else 10 if f.domain == status.DOMAIN_ITEM else 99,
                f.rel_path.lower(), f.object_id.lower()
            ))

            self.log.phase("PACKAGE", "Сборка output pack", files=len(generation.files))
            pkg_result = packager.build_package(
                analysis,
                mapping,
                generation,
                self.output_dir,
                self.log,
                __version__,
                settings,
            )

            # Copy resource bytes verbatim into the resourcepack.
            self._copy_resources(archive, analysis)

            self.log.phase("VALIDATE", "Проверка YAML и структуры пакета")
            validation = validator.validate_output(self.output_dir, self.log)
            fidelity_result = fidelity.verify_fidelity(analysis, generation, self.output_dir, archive)
            validation.issues.extend(
                validator.ValidationIssue("error", "<fidelity>", str(issue))
                for issue in fidelity_result["issues"]
            )
            if fidelity_result["issues"]:
                validation.valid = False

            # Persist a machine-readable semantic verification report.
            report_dir = self.output_dir / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "fidelity.json").write_text(
                __import__("json").dumps(fidelity_result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            self._write_semantics_reports(semantic_result)

            if analysis.source_kind == "itemsadder":
                (report_dir / "itemsadder.md").write_text(
                    itemsadder.conversion_report(analysis), encoding="utf-8"
                )

            source_hash = util.sha256_file(self.input_path) if self.input_path.is_file() else "directory"
            self.log.phase("DONE", "Конвертация завершена", generated=len(generation.files), validation=validation.valid, fidelity=not bool(fidelity_result["issues"]))

        self.log.close()
        return {
            "input": str(self.input_path),
            "output": str(self.output_dir),
            "source_hash": source_hash,
            "counts": pkg_result.counts,
            "statuses": pkg_result.statuses,
            "files_generated": len(generation.files),
            "diagnostics": len(generation.diagnostics),
            "validation": validation.to_dict(),
            "fidelity": fidelity_result,
            "semantics": {
                "enabled": semantic_result.enabled,
                "reason": semantic_result.reason,
                "classified": len(semantic_result.records),
                "counts": semantic_result.counts(),
            },
        }

    def _write_semantics_reports(self, semantic_result: "semantics.SemanticResult") -> None:
        """Persist the neural verdicts and the in-game check commands.

        ``reports/semantics.json`` is the machine-readable form; ``commands.txt``
        is a ready-to-paste list of CraftEngine commands for every converted
        object, which is the fastest way to verify a conversion in-game.
        """
        import json

        reports = self.output_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "semantics.json").write_text(
            json.dumps(semantic_result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if not semantic_result.records:
            return

        lines = [
            "# CraftEngine: команды для проверки сконвертированного контента",
            "# Сгенерировано микро-нейросетью конвертера.",
            "",
        ]
        for domain, title in (("item", "Предметы"), ("block", "Блоки")):
            group = [r for r in semantic_result.records.values() if r.domain == domain and r.commands]
            if not group:
                continue
            lines.append(f"## {title}")
            for record in sorted(group, key=lambda r: r.object_id):
                lines.append(f"# {record.object_id} — {record.description}")
                lines.extend(record.commands)
                lines.append("")
        (reports / "commands.txt").write_text("\n".join(lines), encoding="utf-8")

    def _copy_resources(self, archive: ModArchive, analysis: analyzer.AnalysisResult) -> None:
        """Copy source assets into the output resource pack.

        The resource pack is copied *verbatim*: the complete ``assets/`` tree
        from the mod jar (all namespaces, including ``assets/minecraft``
        overrides, models, textures, atlases, shaders, fonts, sound events,
        etc.) is preserved byte-for-byte. No namespace, extension or resource
        kind is filtered out, so models never lose textures because a file was
        skipped. Generated CraftEngine category translations for source lang
        files are still merged in rather than overwritten.
        """
        rp_dir = self.output_dir / "resourcepack"

        # Assets the converter synthesized (e.g. armor equipment assets). These
        # are written first so a verbatim source copy can never shadow them.
        for name, text in sorted(getattr(analysis, "generated_assets", {}).items()):
            self._write_copied_asset(rp_dir, name, text.encode("utf-8"))

        # Sources that do not already store their resources under ``assets/``
        # (ItemsAdder keeps them under ``contents/<ns>/``) publish an explicit
        # archive-path -> resourcepack-path map. That map is authoritative.
        if getattr(analysis, "resource_map", None):
            for name, dest_rel in sorted(analysis.resource_map.items()):
                data = archive.read(name)
                if data is None:
                    continue
                self._write_copied_asset(rp_dir, dest_rel, data)
            return

        if getattr(self.settings, "copy_all_assets", True):
            for name in archive.names:
                if not name.startswith("assets/"):
                    continue
                data = archive.read(name)
                if data is None:
                    continue
                self._write_copied_asset(rp_dir, name, data)
            return

        # Legacy/selective mode for users who intentionally disable verbatim
        # asset copying. Kept for backward compatibility.
        for resource in analysis.resources:
            if resource.kind not in ("asset", "textures", "models", "blockstates", "sounds", "particles", "fonts", "lang", "equipment"):
                continue
            data = archive.read(resource.source_path)
            if data is None:
                continue
            self._write_copied_asset(rp_dir, resource.source_path, data)

    @staticmethod
    def _write_copied_asset(rp_dir: Path, name: str, data: bytes) -> None:
        dest = rp_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Preserve generated CE category translations when the source mod
        # already has the same lang file: merge instead of overwriting.
        if name.endswith('/lang/en_us.json') or name.endswith('/lang/ru_ru.json'):
            import json
            try:
                src_obj = json.loads(data.decode('utf-8'))
                if dest.exists():
                    existing = json.loads(dest.read_text(encoding='utf-8'))
                    if isinstance(existing, dict) and isinstance(src_obj, dict):
                        src_obj = {**src_obj, **existing}
                dest.write_text(json.dumps(dict(sorted(src_obj.items())), ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
            except Exception:
                dest.write_bytes(data)
        else:
            dest.write_bytes(data)


def convert(
    input_path: str | Path,
    output_dir: str | Path,
    target: str = "craftengine:26.8",
    minecraft_version: str = "1.21.4",
    verbose: bool = False,
    settings: Settings | None = None,
    source: str = "auto",
) -> dict[str, Any]:
    log = Log(verbose, getattr(settings, "log_level", None) if settings is not None else None)
    if settings is None:
        settings = load_settings(log=log).settings
    # An explicit --source always beats the saved setting.
    if source and source != "auto":
        settings.source_mode = source
    driver = ConversionDriver(input_path, output_dir, target, minecraft_version, log, settings)
    return driver.run()



def describe_source(input_path: str | Path, settings: Settings | None = None, verbose: bool = False) -> dict[str, Any]:
    """Summarize what kind of input this is, for the GUI preflight.

    Returns the resolved source kind plus, for ItemsAdder packs, the namespace
    and resource layout that will be used - so the user can confirm the
    converter read their pack correctly before converting.
    """
    log = Log(verbose)
    active = settings or load_settings(log=log).settings
    with open_archive(Path(input_path), log) as archive:
        kind = detect_source_kind(archive, log, active.source_mode)
        info: dict[str, Any] = {"kind": kind, "path": str(input_path)}
        if kind == "itemsadder":
            layout = itemsadder.detect_itemsadder(archive, log)
            info["itemsadder"] = layout.to_dict() if layout else None
        else:
            from .detector import ModDetector

            meta = ModDetector(log).detect(archive)
            info["mod"] = meta.to_dict()
        return info


def suggest_namespace_mappings(
    input_path: str | Path,
    minecraft_version: str = "1.21.4",
    settings: Settings | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Inspect recipe references and propose editable SliceBoard namespace prefixes.

    The returned mapping is intentionally only a *proposal*. Vanilla references
    remain vanilla; external namespaces default to the configured SliceBoard
    provider, while known Farmer's Delight knife tags get a specific proposal.
    """
    log = Log(verbose)
    active = settings or load_settings(log=log).settings
    with open_archive(Path(input_path), log) as archive:
        analysis = analyzer.analyze_archive(archive, log, minecraft_version, active)

    namespaces: set[str] = set()
    for recipe in analysis.recipes.values():
        def collect(value: Any) -> None:
            if isinstance(value, str):
                raw = value[1:] if value.startswith("#") else value
                if ":" in raw:
                    namespaces.add(raw.split(":", 1)[0])
            elif isinstance(value, list):
                for x in value:
                    collect(x)
            elif isinstance(value, dict):
                for key in ("item", "id", "tag"):
                    if key in value:
                        collect(value[key])
                for key in ("result", "ingredients", "tool"):
                    if key in value:
                        collect(value[key])
        collect(recipe.raw.get("ingredients"))
        collect(recipe.raw.get("result"))
        collect(recipe.raw.get("tool"))

    # Tags such as #c:tools/knife and #forge:tools are compatibility
    # namespaces, not external item providers. Do not show them as mod
    # mappings in the UI.
    namespaces.difference_update({"minecraft", "c", "forge", "fabric", "neoforge", "quilt"})
    own = analysis.metadata.namespace
    namespaces.discard(own)

    proposals: dict[str, dict[str, Any]] = {}
    for ns in sorted(namespaces | set(active.sliceboard_namespace_prefixes)):
        existing = active.sliceboard_namespace_prefixes.get(ns)
        if existing:
            proposed = existing
            reason = "saved"
        elif ns in analysis.metadata.dependencies and ns == "farmersdelight":
            proposed = "craftengine:farmersdelight"
            reason = "Farmer's Delight dependency"
        else:
            provider = active.sliceboard_custom_provider or "craftengine"
            proposed = f"{provider}:{ns}"
            reason = "default provider"
        proposals[ns] = {"prefix": proposed, "reason": reason}

    return {
        "mod": {"id": analysis.metadata.id, "namespace": own, "dependencies": analysis.metadata.dependencies},
        "namespaces": proposals,
        "excluded": sorted(active.excluded_recipe_namespaces),
    }

def _load_settings(log: Log) -> Settings:
    return load_settings(log=log).settings


def scan(input_path: str | Path, verbose: bool = False) -> dict[str, Any]:
    log = Log(verbose)
    with open_archive(input_path, log) as archive:
        from .detector import ModDetector

        metadata = ModDetector(log).detect(archive)
        return {"metadata": metadata.to_dict()}


def analyze(
    input_path: str | Path,
    minecraft_version: str,
    verbose: bool = False,
    source: str = "auto",
) -> dict[str, Any]:
    log = Log(verbose)
    settings = load_settings(log=log).settings
    with open_archive(input_path, log) as archive:
        kind = detect_source_kind(archive, log, source if source != "auto" else settings.source_mode)
        if kind == "itemsadder":
            result = itemsadder.ItemsAdderAnalyzer(log, minecraft_version, settings).analyze(archive)
        else:
            result = analyzer.analyze_archive(archive, log, minecraft_version)
        return result.to_dict()


def generate_report(output_dir: str | Path, verbose: bool = False) -> dict[str, Any]:
    log = Log(verbose)
    return validator.generate_report(Path(output_dir), log)