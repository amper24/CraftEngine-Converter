"""Source/output fidelity checks.

These checks deliberately compare semantics, not just YAML syntax. They catch
lost blockstate variants, broken model references, missing generated helper
items, and recipes that disappeared during generation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import yaml

from .analyzer import AnalysisResult
from .generator import GenerationResult
from .archive import ModArchive


def verify_fidelity(analysis: AnalysisResult, generation: GenerationResult, output_dir: Path, archive: ModArchive) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    generated_ids = {f.object_id for f in generation.files}
    generated_by_id_domain = {(f.domain, f.object_id): f for f in generation.files}
    external_namespaces = set(getattr(analysis.metadata, "dependencies", []) or [])
    external_namespaces.update({"minecraft", "farmersdelight"})

    for item_id in analysis.items:
        if item_id not in generated_ids:
            issues.append({"kind": "missing_item", "id": item_id})
    for block_id in analysis.blocks:
        if block_id not in generated_ids:
            issues.append({"kind": "missing_block", "id": block_id})

    # Every source visual resource referenced by a generated model should still
    # exist in the copied resource pack. We check both direct IDs and source
    # model paths captured by the IR.
    rp = output_dir / "resourcepack"
    for node in list(analysis.items.values()) + list(analysis.blocks.values()):
        for src in node.source.values():
            if not isinstance(src, str) or not src.startswith("assets/"):
                continue
            if not archive.exists(src):
                issues.append({"kind": "missing_source_resource", "id": node.id, "path": src})
            copied = rp / Path(src)
            if not copied.exists():
                issues.append({"kind": "missing_copied_resource", "id": node.id, "path": src})

    # Visual reference integrity: every generated item/block model and texture
    # path must resolve to a real copied resource. For model JSON, recursively
    # verify parents and referenced texture keys when those files exist in the
    # source archive. This catches the common "YAML looks correct but texture
    # is missing" failure mode.
    rp = output_dir / "resourcepack"
    def resource_exists(ref: str, expect: str) -> bool:
        if not isinstance(ref, str) or ":" not in ref:
            return True
        ns, rel = ref.split(":", 1)
        rel = rel.lstrip("/")
        if expect == "model":
            path = rp / "assets" / ns / "models" / (rel if rel.endswith(".json") else rel + ".json")
        else:
            path = rp / "assets" / ns / "textures" / (rel if rel.endswith(".png") else rel + ".png")
        return path.exists()

    def check_model(ns: str, rel: str, seen: set[str]) -> None:
        key = f"{ns}:{rel}"
        if key in seen:
            return
        seen.add(key)
        if not resource_exists(key, "model"):
            target = warnings if ns in external_namespaces else issues
            target.append({"kind": "missing_external_model_reference" if ns in external_namespaces else "missing_model_reference", "id": key})
            return
        path = rp / "assets" / ns / "models" / (rel + ".json" if not rel.endswith(".json") else rel)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        if not isinstance(data, dict):
            return
        parent = data.get("parent")
        if isinstance(parent, str):
            pns, prel = parent.split(":", 1) if ":" in parent else (ns, parent)
            check_model(pns, prel, seen)
        textures = data.get("textures")
        if isinstance(textures, dict):
            for tref in textures.values():
                if not isinstance(tref, str) or tref.startswith("#"):
                    continue
                # Texture variable indirection resolves inside the same model.
                if ":" not in tref:
                    tval = textures.get(tref)
                    if isinstance(tval, str):
                        tref = tval
                    else:
                        continue
                if not resource_exists(tref, "texture") and not tref.startswith("minecraft:"):
                    tns = tref.split(":", 1)[0]
                    target = warnings if tns in external_namespaces else issues
                    target.append({"kind": "missing_external_texture_reference" if tns in external_namespaces else "missing_texture_reference", "id": key, "texture": tref})

    checked_objects: set[tuple[str, str]] = set()
    for (domain, object_id), gf in generated_by_id_domain.items():
        if domain not in ("item", "block"):
            continue
        if (domain, object_id) in checked_objects:
            continue
        checked_objects.add((domain, object_id))
        try:
            doc = yaml.safe_load(gf.content) or {}
        except Exception:
            continue
        section = doc.get("items") if domain == "item" else doc.get("blocks")
        section = section or {}
        obj = section.get(object_id) if isinstance(section, dict) else None
        if not isinstance(obj, dict):
            continue
        model = obj.get("model")
        if isinstance(model, dict) and isinstance(model.get("path"), str):
            ref = model["path"]
            ns, rel = ref.split(":", 1) if ":" in ref else (object_id.split(":",1)[0], ref)
            # CraftEngine model generation creates this model at load time; it
            # therefore does not need to exist as a source JSON in resourcepack.
            # Still validate every texture explicitly referenced by generation.
            model_generation = model.get("generation")
            if isinstance(model_generation, dict):
                textures = model_generation.get("textures")
                if isinstance(textures, dict):
                    for tref in textures.values():
                        if isinstance(tref, str) and ":" in tref and not tref.startswith("minecraft:") and not resource_exists(tref, "texture"):
                            tns = tref.split(":", 1)[0]
                            target = warnings if tns in external_namespaces else issues
                            target.append({"kind": "missing_external_texture_reference" if tns in external_namespaces else "missing_texture_reference", "id": ref, "texture": tref})
            else:
                check_model(ns, rel, set())
        texture = obj.get("texture")
        if isinstance(texture, str) and ":" in texture and not texture.startswith("minecraft:"):
            if not resource_exists(texture, "texture"):
                issues.append({"kind": "missing_texture_reference", "id": object_id, "texture": texture})

    # Recipe fidelity: every source recipe is either generated or explicitly
    # reported as unsupported/handled externally. No silent loss.
    diag_ids = {d.get("id") for d in generation.diagnostics if d.get("id")}
    for rid in analysis.recipes:
        if rid not in generated_ids and rid not in diag_ids:
            issues.append({"kind": "missing_recipe", "id": rid})

    # Variant coverage for all stateful blocks.
    for block in analysis.blocks.values():
        if not block.blockstate_variants:
            continue
        generated_block = generated_by_id_domain.get(("block", block.id))
        if generated_block is None:
            continue
        path = output_dir / generated_block.rel_path
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            out_block = ((doc.get("blocks") or {}).get(block.id) or {})
            states = out_block.get("states") or {}
            variants = states.get("variants") or {}
            source_conditions = {str(v.get("condition") or "__default__") for v in block.blockstate_variants if isinstance(v, dict)}
            out_keys = set(variants)
            # CraftEngine represents an empty vanilla variant through the first/default appearance.
            if "__default__" in source_conditions and "__default__" not in out_keys and out_block.get("state", {}).get("model"):
                source_conditions.discard("__default__")
            missing = sorted(source_conditions - out_keys)
            if missing:
                issues.append({"kind": "lost_blockstate_variants", "id": block.id, "details": ",".join(missing)})
        except Exception as exc:  # pragma: no cover - validation must never crash conversion
            issues.append({"kind": "fidelity_parse_error", "id": block.id, "details": str(exc)})

    return {
        "valid": not issues,
        "issues": issues,
        "warnings": warnings,
        "source_items": len(analysis.items),
        "source_blocks": len(analysis.blocks),
        "source_recipes": len(analysis.recipes),
        "generated_files": len(generation.files),
        "diagnostic_objects": len(diag_ids),
    }
