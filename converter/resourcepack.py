"""Resource-pack import: generate CraftEngine configs from models and textures.

The other adapters read a *content* format (mod bytecode, ItemsAdder YAML). This
one starts from a plain vanilla resource pack: whatever ``assets/<ns>/models/item/``
and ``assets/<ns>/textures/item/`` contain becomes CraftEngine items, so an
artist's pack turns into a working CraftEngine package without hand-writing a
config per texture.

Design rules carried over from the rest of the converter:

* every scanned asset is either converted or reported - nothing is dropped
  silently;
* a model ``.json`` wins over a bare texture, because emitting a CE
  ``generation`` block next to a real model would override the author's model;
* ``assets/minecraft/`` is a vanilla *override*, not new content, so it is
  reported separately rather than minted into items.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import status, util
from .analyzer import AnalysisResult
from .archive import ModArchive
from .config import Settings
from .detector import ModMetadata
from .ir import ItemNode, ResourceNode
from .itemsadder import Ledger, _material_id
from .util import Log

# Loader metadata means the bytecode adapter owns this archive.
_MOD_MARKERS = ("fabric.mod.json", "quilt.mod.json", "neoforge.mods.toml", "mods.toml")

_TEXTURE_EXTS = (".png",)


@dataclass
class NamespaceAssets:
    """Models and textures found under one ``assets/<ns>/`` root."""

    root: str
    models: dict[str, str] = field(default_factory=dict)      # item name -> archive path
    textures: dict[str, str] = field(default_factory=dict)    # item name -> archive path
    lang_files: list[str] = field(default_factory=list)
    block_models: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "item_models": sorted(self.models),
            "item_textures": sorted(self.textures),
            "block_models": sorted(self.block_models),
            "lang_files": sorted(self.lang_files),
        }


@dataclass
class ResourcePackLayout:
    """Result of resource-pack detection over an archive."""

    namespaces: dict[str, NamespaceAssets] = field(default_factory=dict)
    vanilla_overrides: dict[str, NamespaceAssets] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": self.evidence,
            "namespaces": {k: v.to_dict() for k, v in sorted(self.namespaces.items())},
            "vanilla_overrides": {k: v.to_dict() for k, v in sorted(self.vanilla_overrides.items())},
        }


def _mod_marker_present(archive: ModArchive) -> bool:
    return any(archive.exists(n) for n in _MOD_MARKERS) or any(
        n.endswith("mods.toml") for n in archive.names
    )


def detect_resourcepack(archive: ModArchive, log: Log | None = None) -> ResourcePackLayout | None:
    """Return a layout when the archive is a plain resource pack, else None.

    A mod jar is rejected on loader metadata, exactly like the ItemsAdder
    detector does: shipping ``assets/`` next to bytecode does not make the pack
    the right adapter.
    """
    log = log or Log()
    if _mod_marker_present(archive):
        return None

    layout = ResourcePackLayout()
    for name in archive.names:
        parts = name.split("/")
        if len(parts) < 4 or parts[0] != "assets":
            continue
        ns = parts[1]
        rel = parts[2:]
        bucket = layout.vanilla_overrides if ns == "minecraft" else layout.namespaces
        assets = bucket.setdefault(ns, NamespaceAssets(root=f"assets/{ns}"))

        if rel[0] == "models" and len(rel) >= 3 and name.endswith(".json"):
            stem = "/".join(rel[2:])[:-len(".json")]
            if rel[1] == "item":
                assets.models[stem] = name
            elif rel[1] == "block":
                assets.block_models[stem] = name
        elif rel[0] == "textures" and len(rel) >= 3 and name.lower().endswith(_TEXTURE_EXTS):
            stem = "/".join(rel[2:])
            stem = stem[: -len(".png")]
            if rel[1] == "item":
                assets.textures[stem] = name
        elif rel[0] == "lang" and name.endswith(".json"):
            assets.lang_files.append(name)

    # Drop namespaces that carry nothing an item config could describe.
    layout.namespaces = {
        ns: a for ns, a in layout.namespaces.items() if a.models or a.textures
    }
    if not layout.namespaces:
        return None

    for ns, assets in sorted(layout.namespaces.items()):
        layout.evidence.append(
            f"{assets.root}: {len(assets.models)} item model(s), {len(assets.textures)} item texture(s)"
        )
    log.info("Resource pack detected",
             namespaces=len(layout.namespaces),
             items=sum(len(a.models) + len(a.textures) for a in layout.namespaces.values()))
    return layout


class ResourcePackAnalyzer:
    """Build IR from a resource pack's item models and textures."""

    def __init__(self, log: Log, minecraft_version: str = "1.21.4",
                 settings: Settings | None = None) -> None:
        self.log = log
        self.minecraft_version = minecraft_version
        self.settings = settings or Settings()
        self.ledger = Ledger()
        self._archive: ModArchive | None = None
        self._layout: ResourcePackLayout | None = None

    # --- entry point ------------------------------------------------------
    def analyze(self, archive: ModArchive, layout: ResourcePackLayout | None = None) -> AnalysisResult:
        self._archive = archive
        self._layout = layout or detect_resourcepack(archive, self.log)
        if self._layout is None:
            raise ValueError("archive is not a resource pack")

        source_files = [n for a in self._layout.namespaces.values()
                        for n in (*a.models.values(), *a.textures.values())]
        result = AnalysisResult(
            metadata=ModMetadata(
                id=sorted(self._layout.namespaces)[0] if self._layout.namespaces else "resourcepack",
                name="Resource pack",
                loader="resourcepack",
                namespace=sorted(self._layout.namespaces)[0] if self._layout.namespaces else "minecraft",
                source_files=sorted(source_files),
            ),
            minecraft_version=self.minecraft_version,
            content_namespaces=sorted(self._layout.namespaces),
            source_kind="resourcepack",
        )

        for ns in sorted(self._layout.namespaces):
            self._build_namespace(result, ns, self._layout.namespaces[ns])
        self._report_vanilla_overrides(result)

        result.conversion_ledger = [row.to_dict() for row in self.ledger.rows]
        self.log.info("Resource pack analyzed",
                      items=len(result.items),
                      namespaces=len(result.content_namespaces))
        return result

    # --- per namespace ----------------------------------------------------
    def _build_namespace(self, result: AnalysisResult, ns: str, assets: NamespaceAssets) -> None:
        lang = self._load_lang(assets)
        # Normalized the same way the ItemsAdder path does, so `paper` and
        # `minecraft:paper` behave identically.
        default_material = _material_id(
            str(getattr(self.settings, "rp_default_material", "nether_brick")))

        for name in sorted(set(assets.models) | set(assets.textures)):
            model_path = assets.models.get(name)
            texture_path = assets.textures.get(name)
            self._build_item(result, ns, name, model_path, texture_path, lang, default_material)

        for name in sorted(assets.block_models):
            self.ledger.add(f"{ns}:{name}", "pack", f"assets/{ns}/models/block/{name}.json", "-",
                            "unsupported",
                            "Block models need a CraftEngine block config with states; only item "
                            "models are generated from a resource pack.")

        for path in sorted(assets.models.values()) + sorted(assets.textures.values()):
            data = (self._archive.read(path) if self._archive else None) or b""
            result.resources.append(ResourceNode(
                source_path=path, namespace=ns, kind="asset",
                raw_hash=util.sha256_bytes(data), is_resourcepack=True,
            ))

    def _build_item(self, result: AnalysisResult, ns: str, name: str,
                    model_path: str | None, texture_path: str | None,
                    lang: dict[str, str], default_material: str) -> None:
        full_id = f"{ns}:{name}"
        node = ItemNode(
            id=full_id,
            namespace=ns,
            kind="resourcepack_item",
            confidence=1.0,
            source={"file": model_path or texture_path or ""},
            base_material=default_material,
        )
        self.ledger.add(full_id, "item", "-", "material", "transform",
                        f"A resource pack carries no material; defaulting to `{default_material}` "
                        f"(setting rp_default_material).")

        display = lang.get(f"item.{ns}.{name.replace('/', '.')}",
                           lang.get(f"item.{ns}.{name}", ""))
        if display:
            node.display_name = display
            self.ledger.add(full_id, "item", "lang:item." + ns + "." + name, "data.item_name", "direct")

        if model_path:
            # The pack already ships a model, so reference it explicitly and do
            # NOT emit a `generation` block - that would override the author's.
            node.model = f"{ns}:item/{name}"
            node.model_refs = [model_path]
            self.ledger.add(full_id, "item", f"assets/{ns}/models/item/{name}.json", "model", "direct",
                            "Existing model .json is referenced as-is; no generation block is emitted.")
            if texture_path:
                node.texture_refs = [texture_path]
        elif texture_path:
            # No model: CraftEngine's simplified `texture` form synthesizes an
            # item/generated model, which is exactly what a bare texture wants.
            node.textures = [f"{ns}:item/{name}"]
            node.texture_refs = [texture_path]
            self.ledger.add(full_id, "item", f"assets/{ns}/textures/item/{name}.png", "texture",
                            "transform", "CraftEngine generates an item/generated model for it.")
            if name.endswith(_HANDHELD_SUFFIXES):
                node.model_tree = {"type": "minecraft:model", "path": f"{ns}:item/{name}",
                                   "generation": {"parent": "minecraft:item/handheld",
                                                   "textures": {"layer0": f"{ns}:item/{name}"}}}
                node.model = None
                node.textures = []
                self.ledger.add(full_id, "item", "name-suffix", "model.generation.parent",
                                "transform", "Tool-like name: handheld parent applied.")

        node.status = status.ANALYZED
        result.items[full_id] = node

    def _report_vanilla_overrides(self, result: AnalysisResult) -> None:
        for ns, assets in sorted((self._layout.vanilla_overrides if self._layout else {}).items()):
            count = len(assets.models) + len(assets.textures)
            if not count:
                continue
            skip = bool(getattr(self.settings, "rp_skip_vanilla_overrides", True))
            self.ledger.add(f"{ns}:<assets>", "pack", f"assets/{ns}/", "-",
                            "unsupported" if skip else "partial",
                            (f"{count} vanilla override asset(s). These change vanilla items rather "
                             "than adding content, so no CraftEngine item is generated "
                             "(setting rp_skip_vanilla_overrides).") if skip else
                            f"{count} vanilla override asset(s) copied into the pack.")

    # --- lang -------------------------------------------------------------
    def _load_lang(self, assets: NamespaceAssets) -> dict[str, str]:
        """Merge the pack's lang files, preferring the configured locale."""
        wanted = str(getattr(self.settings, "ia_default_locale", "en")).lower()
        order = {"en_us": 0, "en": 0}.get(wanted, 1)
        merged: dict[str, str] = {}
        for path in sorted(assets.lang_files, key=lambda p: (0 if _locale_rank(p, wanted) else 1, p)):
            data = self._archive.read(path) if self._archive else None
            if not data:
                continue
            try:
                parsed = json.loads(data.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                self.log.warn("resource pack lang file is not valid JSON", path=path)
                continue
            if isinstance(parsed, dict):
                merged.update({str(k): str(v) for k, v in parsed.items()})
        del order
        return merged


_HANDHELD_SUFFIXES = ("_sword", "_pickaxe", "_axe", "_shovel", "_hoe", "_dagger",
                      "_katana", "_spear", "_trident", "_shears", "_fishing_rod")


def _locale_rank(path: str, wanted: str) -> bool:
    stem = path.rsplit("/", 1)[-1][:-len(".json")]
    return stem.replace("_", "") == wanted.replace("_", "") or stem == wanted


def conversion_report(analysis: AnalysisResult) -> str:
    """Render ``reports/resourcepack.md``: what was scanned, what was generated."""
    rows = analysis.conversion_ledger or []
    lines = [
        "# Resource pack -> CraftEngine ledger",
        "",
        f"Source kind: `{analysis.source_kind}`",
        f"Namespaces: {', '.join(analysis.content_namespaces) or '-'}",
        f"Items: {len(analysis.items)}",
        "",
        "## Summary",
        "",
        "| Support | Keys |",
        "| --- | --- |",
    ]
    summary: dict[str, int] = {}
    for row in rows:
        summary[row.get("support", "")] = summary.get(row.get("support", ""), 0) + 1
    for support, count in sorted(summary.items()):
        lines.append(f"| {support} | {count} |")

    needs = [r for r in rows if r.get("support") in ("unsupported", "partial")]
    if needs:
        lines += ["", "## Needs review", "", "| Object | Source key | Support | Note |", "| --- | --- | --- | --- |"]
        for row in sorted(needs, key=lambda r: (r.get("object", ""), r.get("source_key", ""))):
            lines.append("| `{}` | `{}` | {} | {} |".format(
                row.get("object", ""), row.get("source_key", ""),
                row.get("support", ""), row.get("note", "")))

    lines += ["", "## Full ledger", "",
              "| Object | Source key | CraftEngine target | Support |", "| --- | --- | --- | --- |"]
    for row in sorted(rows, key=lambda r: (r.get("object", ""), r.get("source_key", ""))):
        lines.append("| `{}` | `{}` | `{}` | {} |".format(
            row.get("object", ""), row.get("source_key", ""),
            row.get("target", ""), row.get("support", "")))
    return "\n".join(lines) + "\n"
