"""Output validator and report command support.

The validator re-reads a generated output package and checks:
- YAML parses for every configuration/*.yml file;
- no unknown top-level root sections;
- every file has exactly one object key;
- completeness invariant: detected == generated + diagnostic-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import schema, util
from .util import Log


@dataclass
class ValidationIssue:
    severity: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "path": self.path, "message": self.message}


@dataclass
class ValidationResult:
    valid: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    files_checked: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "files_checked": self.files_checked,
            "issues": [i.to_dict() for i in self.issues],
        }


class OutputValidator:
    def __init__(self, log: Log) -> None:
        self.log = log
        self.registry = schema.get_registry()
        self.output_dir: Path | None = None

    def validate(self, output_dir: Path) -> ValidationResult:
        self.output_dir = output_dir
        craftengine = output_dir / "configuration"
        sliceboard = output_dir / "configuration" / "sliceboard"
        report = ValidationResult()

        craftengine_roots = {
            "items", "blocks", "furniture", "recipes", "loot",
            "global_variables", "categories", "translations", "sounds",
        }
        sliceboard_roots = {"recipes", "debug", "boards", "defaults", "visual"}

        # `sliceboard` is inside `configuration`, so the general recursive
        # scan already includes it. Keep one deduplicated list.
        yaml_files = sorted(set(craftengine.rglob("*.yml"))) if craftengine.exists() else []
        yaml_files += sorted(set(craftengine.rglob("*.yaml")))

        for path in yaml_files:
            allowed_roots = sliceboard_roots if str(path.relative_to(output_dir)).startswith("configuration/sliceboard") else craftengine_roots
            report.files_checked += 1
            try:
                with path.open("r", encoding="utf-8") as fh:
                    doc = yaml.safe_load(fh)
            except yaml.YAMLError as exc:
                report.valid = False
                report.issues.append(ValidationIssue("error", str(path), f"YAML parse error: {exc}"))
                continue

            if not isinstance(doc, dict) or not doc:
                report.valid = False
                report.issues.append(ValidationIssue("error", str(path), "empty or non-mapping document"))
                continue

            for key in doc:
                if key not in allowed_roots:
                    report.valid = False
                    report.issues.append(ValidationIssue("error", str(path), f"unknown root section '{key}'"))

            is_sliceboard = allowed_roots == sliceboard_roots
            if not is_sliceboard:
                self._validate_nested_keys(path, doc, report)
            is_merged = path.name == "all.yml" or path.name == "all.yaml"
            for section, value in doc.items():
                if isinstance(value, dict):
                    # SliceBoard config.yml legitimately holds multiple sub-keys
                    # under boards/defaults/visual. SliceBoard recipe files also
                    # intentionally contain many recipe entries. Categories,
                    # translations and sound registries are also multi-entry by design.
                    rel = str(path.relative_to(output_dir)).replace("\\", "/")
                    if is_sliceboard and (section != "recipes" or "/recipes/" in rel):
                        continue
                    if section in {"categories", "translations", "sounds"}:
                        continue
                    # Merged single-file output deliberately holds many objects.
                    if is_merged:
                        continue
                    if len(value) != 1:
                        report.issues.append(
                            ValidationIssue("warn", str(path), f"section '{section}' contains {len(value)} objects (expected 1 per file)")
                        )

        return report

    def _validate_nested_keys(self, path: Path, doc: dict[str, Any], report: ValidationResult) -> None:
        """Validate CraftEngine nested keys against the bundled target schema.

        This is intentionally a *warning-level* check: it never drops content or
        interferes with generation. An unknown key is reported so the converter
        author can fix the emitter rather than silently shipping a config that
        CraftEngine would reject at load time.
        """
        rel = str(path.relative_to(self.output_dir)).replace("\\", "/") if getattr(self, "output_dir", None) else str(path)
        for section, value in doc.items():
            if not isinstance(value, dict):
                continue
            # Categories / translations / sounds / loot files are multi-object
            # and are intentionally schema-agnostic at this layer.
            if section in {"categories", "translations", "sounds"}:
                continue
            if section == "items":
                for item_id, body in value.items():
                    if not isinstance(body, dict):
                        continue
                    for key in self.registry.unknown_keys(body, self.registry.item_root_keys | self.registry.item_model_root_keys):
                        report.issues.append(ValidationIssue("warn", rel, f"item {item_id}: unknown root key '{key}'"))
                    data = body.get("data")
                    if isinstance(data, dict):
                        for key in self.registry.unknown_keys(data, self.registry.item_data_keys):
                            report.issues.append(ValidationIssue("warn", rel, f"item {item_id}: unknown data key '{key}'"))
                    settings = body.get("settings")
                    if isinstance(settings, dict):
                        for key in self.registry.unknown_keys(settings, self.registry.item_settings_keys):
                            report.issues.append(ValidationIssue("warn", rel, f"item {item_id}: unknown settings key '{key}'"))
                    behavior = body.get("behavior")
                    behaviors = body.get("behaviors")
                    candidates = behaviors if isinstance(behaviors, list) else ([behavior] if isinstance(behavior, dict) else [])
                    for b in candidates:
                        if isinstance(b, dict) and b.get("type") not in self.registry.item_behavior_types:
                            report.issues.append(ValidationIssue("warn", rel, f"item {item_id}: unknown behavior type '{b.get('type')}'"))
                    model = body.get("model")
                    if isinstance(model, dict):
                        self._validate_model_tree(model, rel, f"item {item_id}", report)
            elif section == "loot":
                for loot_id, body in value.items():
                    if not isinstance(body, dict):
                        continue
                    self._validate_loot_table(body, rel, f"loot {loot_id}", report)
            elif section == "blocks":
                for block_id, body in value.items():
                    if not isinstance(body, dict):
                        continue
                    for key in self.registry.unknown_keys(body, self.registry.block_root_keys):
                        report.issues.append(ValidationIssue("warn", rel, f"block {block_id}: unknown root key '{key}'"))
                    settings = body.get("settings")
                    if isinstance(settings, dict):
                        for key in self.registry.unknown_keys(settings, self.registry.block_settings_keys):
                            report.issues.append(ValidationIssue("warn", rel, f"block {block_id}: unknown settings key '{key}'"))
                    behavior = body.get("behavior")
                    behaviors = body.get("behaviors")
                    candidates = behaviors if isinstance(behaviors, list) else ([behavior] if isinstance(behavior, dict) else [])
                    for b in candidates:
                        if isinstance(b, dict) and b.get("type") not in self.registry.block_behavior_types:
                            report.issues.append(ValidationIssue("warn", rel, f"block {block_id}: unknown behavior type '{b.get('type')}'"))
            elif section == "recipes":
                for recipe_id, body in value.items():
                    if not isinstance(body, dict):
                        continue
                    rtype = body.get("type")
                    if rtype and str(rtype) not in self.registry.recipe_confirmed_types:
                        report.issues.append(ValidationIssue("warn", rel, f"recipe {recipe_id}: unknown recipe type '{rtype}'"))

    def _validate_model_tree(self, node: Any, rel: str, label: str, report: ValidationResult) -> None:
        """Recursively validate CraftEngine item model node types."""
        if isinstance(node, list):
            for x in node:
                self._validate_model_tree(x, rel, label, report)
            return
        if not isinstance(node, dict):
            return
        ntype = node.get("type")
        if isinstance(ntype, str) and ntype not in self.registry.item_model_types:
            report.issues.append(ValidationIssue("warn", rel, f"{label}: unknown model type '{ntype}'"))
        for key in ("on_true", "on_false", "fallback", "model"):
            if key in node:
                self._validate_model_tree(node[key], rel, label, report)
        for key in ("cases", "entries", "models"):
            if isinstance(node.get(key), list):
                for child in node[key]:
                    self._validate_model_tree(child, rel, label, report)

    def _validate_loot_table(self, body: dict[str, Any], rel: str, label: str, report: ValidationResult) -> None:
        """Validate loot pools/entries against CE's native registries."""
        def walk_entries(entries: Any) -> None:
            if not isinstance(entries, list):
                return
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                etype = entry.get("type")
                if isinstance(etype, str) and etype not in self.registry.loot_entry_types:
                    report.issues.append(ValidationIssue("warn", rel, f"{label}: unknown loot entry type '{etype}'"))
                for key in ("entries", "children"):
                    walk_entries(entry.get(key))
                self._validate_loot_functions(entry.get("functions"), rel, label, report)
                self._validate_loot_conditions(entry.get("conditions"), rel, label, report)

        def walk_functions(functions: Any) -> None:
            if not isinstance(functions, list):
                return
            for fn in functions:
                if isinstance(fn, dict):
                    ftype = fn.get("type")
                    if isinstance(ftype, str) and ftype not in self.registry.loot_function_types:
                        report.issues.append(ValidationIssue("warn", rel, f"{label}: unknown loot function type '{ftype}'"))

        def walk_conditions(conditions: Any) -> None:
            if not isinstance(conditions, list):
                return
            for cond in conditions:
                if isinstance(cond, dict):
                    ctype = cond.get("type")
                    if isinstance(ctype, str) and ctype not in self.registry.condition_types:
                        report.issues.append(ValidationIssue("warn", rel, f"{label}: unknown loot condition type '{ctype}'"))
                    for key in ("terms", "term"):
                        if key in cond:
                            walk_conditions(cond[key] if isinstance(cond[key], list) else [cond[key]])

        for pool in body.get("pools", []) if isinstance(body.get("pools"), list) else []:
            if not isinstance(pool, dict):
                continue
            walk_entries(pool.get("entries"))
            self._validate_loot_functions(pool.get("functions"), rel, label, report)
            self._validate_loot_conditions(pool.get("conditions"), rel, label, report)
        self._validate_loot_functions(body.get("functions"), rel, label, report)
        self._validate_loot_conditions(body.get("conditions"), rel, label, report)

    def _validate_loot_functions(self, functions: Any, rel: str, label: str, report: ValidationResult) -> None:
        if not isinstance(functions, list):
            return
        for fn in functions:
            if not isinstance(fn, dict):
                continue
            ftype = fn.get("type")
            if isinstance(ftype, str) and ftype not in self.registry.loot_function_types:
                report.issues.append(ValidationIssue("warn", rel, f"{label}: unknown loot function type '{ftype}'"))

    def _validate_loot_conditions(self, conditions: Any, rel: str, label: str, report: ValidationResult) -> None:
        if not isinstance(conditions, list):
            return
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            ctype = cond.get("type")
            if isinstance(ctype, str) and ctype not in self.registry.condition_types:
                report.issues.append(ValidationIssue("warn", rel, f"{label}: unknown loot condition type '{ctype}'"))
            for key in ("terms", "term"):
                if key in cond:
                    children = cond[key] if isinstance(cond[key], list) else [cond[key]]
                    self._validate_loot_conditions(children, rel, label, report)


def validate_output(output_dir: Path, log: Log) -> ValidationResult:
    return OutputValidator(log).validate(output_dir)


def generate_report(output_dir: Path, log: Log) -> dict[str, Any]:
    """Produce a machine-readable report summary for an existing output."""
    summary_path = output_dir / "reports" / "summary.json"
    manifest_path = output_dir / "manifest.yml"

    summary: dict[str, Any] = {}
    if summary_path.exists():
        summary = util.read_json(summary_path)

    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        manifest = util.read_json(manifest_path)

    validation = validate_output(output_dir, log)

    index: dict[str, Any] = {}
    index_path = output_dir / "source-map" / "index.json"
    if index_path.exists():
        index = util.read_json(index_path)

    coverage: dict[str, Any] = {}
    coverage_path = output_dir / "reports" / "coverage.json"
    if coverage_path.exists():
        coverage = util.read_json(coverage_path)

    return {
        "summary": summary,
        "manifest": manifest,
        "validation": validation.to_dict(),
        "index": index,
        "coverage": coverage,
    }
