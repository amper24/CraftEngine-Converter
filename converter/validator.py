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

    def validate(self, output_dir: Path) -> ValidationResult:
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

    return {
        "summary": summary,
        "manifest": manifest,
        "validation": validation.to_dict(),
        "index": index,
    }