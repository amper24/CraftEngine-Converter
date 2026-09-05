"""Asset indexing and reference-resolution helpers.

These helpers are used by the analyzer, fidelity checker and coverage report.
They deliberately operate on *copied-verbatim* asset paths so that a model or
texture that exists in the source archive is never reported as missing merely
because it lives under a namespace/domain that was filtered out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .archive import ModArchive
from . import util


def asset_domain(source_path: str) -> str | None:
    """Return the vanilla resource domain under ``assets/<ns>/``.

    Examples: ``assets/mod/models/item/foo.json`` -> ``models``,
    ``assets/mod/textures/item/foo.png`` -> ``textures``.
    """
    parts = source_path.split("/")
    if len(parts) >= 3 and parts[0] == "assets":
        return parts[2]
    return None


def is_resourcepack(source_path: str) -> bool:
    return source_path.startswith("assets/")


def model_refs(model: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Return (model references, texture references) from a model JSON.

    ``parent`` is a model reference; every value in ``textures`` is a texture
    reference. Texture variables (``#foo``) are not external references.
    """
    model_refs: list[str] = []
    texture_refs: list[str] = []
    if not isinstance(model, dict):
        return model_refs, texture_refs
    parent = model.get("parent")
    if isinstance(parent, str) and ":" in parent:
        model_refs.append(parent)
    textures = model.get("textures")
    if isinstance(textures, dict):
        for value in textures.values():
            if isinstance(value, str) and ":" in value and not value.startswith("#") and not value.startswith("minecraft:"):
                texture_refs.append(value)
    return model_refs, texture_refs


def tree_model_refs(tree: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Collect model/texture references from a CraftEngine item model tree."""
    model_refs: list[str] = []
    gen_refs: list[str] = []
    if not isinstance(tree, dict):
        return model_refs, gen_refs

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        gen = node.get("generation")
        has_generation = isinstance(gen, dict)
        # A `minecraft:model` node with `generation` is assembled by CraftEngine
        # at load time; its `path` is a generated identifier, not a source model
        # file. Only the generation textures are real asset references.
        if not has_generation:
            for key in ("path", "model"):
                value = node.get(key)
                if isinstance(value, str) and ":" in value:
                    model_refs.append(value)
        if has_generation:
            tex = gen.get("textures")
            if isinstance(tex, dict):
                for value in tex.values():
                    if isinstance(value, str) and ":" in value and not value.startswith("#"):
                        gen_refs.append(value)
        # Composite/select/condition/range types.
        for key in ("on_true", "on_false", "fallback", "models", "cases", "entries"):
            if key in node:
                walk(node[key])
        if node.get("type") in ("minecraft:condition", "minecraft:select", "minecraft:composite",
                                "minecraft:range_dispatch", "minecraft:special"):
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

    walk(tree)
    seen_m = set()
    seen_t = set()
    for r in model_refs:
        if r not in seen_m:
            seen_m.add(r)
    for r in gen_refs:
        if r not in seen_t:
            seen_t.add(r)
    return list(sorted(seen_m)), list(sorted(seen_t))


def qualify(value: str, default_ns: str) -> str:
    if ":" in value:
        return value
    return f"{default_ns}:{value}"


def asset_path_for_ref(ref: str, kind: str) -> Path | None:
    """Turn a CraftEngine reference into a ``resourcepack/assets/...`` Path.

    ``kind`` is either ``model`` or ``texture``. Only non-vanilla refs yield a
    path; vanilla refs are expected to come from Minecraft's own assets.
    """
    if not isinstance(ref, str) or ":" not in ref:
        return None
    ns, rel = ref.split(":", 1)
    if ns == "minecraft":
        return None
    rel = rel.lstrip("/")
    if kind == "model":
        suffix = ".json" if rel.endswith(".json") else ".json"
        return Path("assets") / ns / "models" / (rel if rel.endswith(".json") else rel + ".json")
    suffix = ".png" if rel.endswith(".png") else ".png"
    return Path("assets") / ns / "textures" / (rel if rel.endswith(".png") else rel + ".png")


def source_path_to_model_ref(source_path: str) -> str | None:
    """``assets/<ns>/models/x/y.json`` -> ``<ns>:x/y``.

    ``x`` includes the vanilla domain sub-folder (``block/``, ``item/``), so
    ``assets/ns/models/block/foo.json`` becomes ``ns:block/foo``.
    """
    parts = source_path.split("/")
    if len(parts) < 5 or parts[0] != "assets" or parts[2] != "models":
        return None
    rel = "/".join(parts[3:])
    if rel.endswith(".json"):
        rel = rel[:-5]
    return f"{parts[1]}:{rel}"


def source_path_to_texture_ref(source_path: str) -> str | None:
    """``assets/<ns>/textures/x/y.png`` -> ``<ns>:x/y``.

    ``x`` includes the vanilla domain sub-folder (``block/``, ``item/``), so
    ``assets/ns/textures/block/foo.png`` becomes ``ns:block/foo``.
    """
    parts = source_path.split("/")
    if len(parts) < 5 or parts[0] != "assets" or parts[2] != "textures":
        return None
    rel = "/".join(parts[3:])
    if rel.endswith(".png"):
        rel = rel[:-4]
    return f"{parts[1]}:{rel}"


def list_asset_files(archive: ModArchive) -> list[str]:
    return [n for n in archive.names if n.startswith("assets/")]


def index_models(archive: ModArchive) -> dict[str, dict[str, Any]]:
    """Index every model JSON under ``assets/*/models/**`` keyed by source path."""
    out: dict[str, dict[str, Any]] = {}
    for name in archive.names:
        if not name.startswith("assets/") or "/models/" not in name or not name.endswith(".json"):
            continue
        parsed = archive.read_json(name)
        if isinstance(parsed, dict):
            out[name] = parsed
    return out


def index_textures(archive: ModArchive) -> set[str]:
    return {
        n for n in archive.names
        if n.startswith("assets/") and "/textures/" in n and n.endswith(".png")
    }
