"""Conversion driver: orchestrates detect -> analyze -> map -> generate ->
package -> validate, and exposes high-level functions for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__, analyzer, capability, generator, packager, validator, util, fidelity, status
from .archive import ModArchive, open_archive
from .config import Settings, load_settings
from .sliceboard import generate_sliceboard
from .util import Log


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
            self.log.phase("ANALYZE", "Анализ исходного мода")
            analysis = analyzer.analyze_archive(archive, self.log, self.minecraft_version, settings)
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
        }

    def _copy_resources(self, archive: ModArchive, analysis: analyzer.AnalysisResult) -> None:
        rp_dir = self.output_dir / "resourcepack"
        for resource in analysis.resources:
            if resource.kind not in ("asset", "textures", "models", "blockstates", "sounds", "particles", "fonts", "lang", "equipment"):
                continue
            data = archive.read(resource.source_path)
            if data is None:
                continue
            dest = rp_dir / resource.source_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Preserve generated CE category translations when the source mod
            # already has the same lang file: merge instead of overwriting.
            if resource.source_path.endswith('/lang/en_us.json') or resource.source_path.endswith('/lang/ru_ru.json'):
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
    target: str = "craftengine:26.8.2",
    minecraft_version: str = "1.21.4",
    verbose: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    log = Log(verbose, getattr(settings, "log_level", None) if settings is not None else None)
    driver = ConversionDriver(input_path, output_dir, target, minecraft_version, log, settings)
    return driver.run()



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


def analyze(input_path: str | Path, minecraft_version: str, verbose: bool = False) -> dict[str, Any]:
    log = Log(verbose)
    with open_archive(input_path, log) as archive:
        result = analyzer.analyze_archive(archive, log, minecraft_version)
        return result.to_dict()


def generate_report(output_dir: str | Path, verbose: bool = False) -> dict[str, Any]:
    log = Log(verbose)
    return validator.generate_report(Path(output_dir), log)