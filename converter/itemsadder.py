"""ItemsAdder import adapter: turn an ItemsAdder ``contents/`` pack into IR.

ItemsAdder is a *configuration-driven* content plugin, so its source of truth is
YAML under ``plugins/ItemsAdder/contents/<namespace>/`` rather than compiled
registrations. This module reads that YAML and produces exactly the same
:class:`~converter.analyzer.AnalysisResult` the mod analyzer produces, so the
rest of the pipeline (semantics -> capability -> generator -> packager ->
validator) is reused unchanged.

Layout handled::

    <pack>/                                   # also: plugins/ItemsAdder/contents
      <namespace>/
        _items.yml, configs/*.yml, ...        # any .yml with `info: namespace:`
        textures/**  models/**  blockstates/**    # legacy resource layout
        resources/resourcepack/assets/**          # modern resource layout

Nothing is silently dropped: every source key that has no CraftEngine
equivalent is written to ``analysis.conversion_ledger`` and ends up in
``reports/itemsadder.md``.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import paths, status, util
from .analyzer import AnalysisResult
from .archive import ModArchive
from .config import Settings
from .detector import ModMetadata
from .ir import BlockNode, FurnitureNode, ItemNode, LootNode, RecipeNode, ResourceNode
from .util import Log

# Root keys that mark a YAML file as ItemsAdder content.
IA_ROOT_KEYS = (
    "items", "categories", "lang", "armors_rendering", "entities", "world_populators",
    "biomes", "liquids", "hud", "emotes", "paintings", "music_discs", "recipes",
    "books", "fonts", "sounds", "trims", "blocks", "furnitures",
)

# Directories inside a namespace folder that ItemsAdder's /iazip folds into
# assets/<namespace>/... (the pre-4.0 resource layout).
LEGACY_ASSET_DIRS = ("textures", "models", "blockstates", "sounds", "font", "shaders", "particles", "equipment")

# Directories that hold a full resourcepack (modern layout).
RESOURCEPACK_DIRS = ("resources/resourcepack", "resourcepack", "resource_pack", "pack")

# Bukkit Attribute -> slot aliases for armor.
_ARMOR_SLOTS = {"head", "chest", "legs", "feet", "body"}

# Damage sources that make an item entity fire-proof (IA `fire_resistant`).
_FIRE_DAMAGE_SOURCES = ["in_fire", "on_fire", "lava", "hot_floor"]

# Material suffixes whose vanilla model parent is item/handheld.
_HANDHELD_SUFFIXES = ("_sword", "_pickaxe", "_axe", "_shovel", "_hoe", "_sword2", "_dagger", "_katana", "_spear", "_trident", "_shears", "_fishing_rod")

# Keys ItemsAdder accepts inside `behaviours.block`. Anything else is reported
# rather than silently accepted, so a typo surfaces in reports/itemsadder.md.
_KNOWN_BLOCK_KEYS = frozenset({
    "placed_model", "type", "hardness", "blast_resistance", "no_explosion", "light_level",
    "friction", "speed_factor", "jump_factor", "break_tools_whitelist", "break_tools_blacklist",
    "events_tools_whitelist", "events_tools_blacklist", "sound", "sounds", "drop_when_mined",
    "drop_on_shears", "drop_on_silk_touch", "model_path", "textures", "material",
    "custom_variants", "placeable_on", "cancel_drop", "loots",
})


@dataclass
class NamespaceLayout:
    """Where one ItemsAdder namespace keeps its config and its resources."""

    namespace: str
    root: str  # directory inside the archive that holds this namespace
    config_files: list[str] = field(default_factory=list)
    resourcepack_roots: list[str] = field(default_factory=list)
    legacy_asset_dirs: list[str] = field(default_factory=list)


@dataclass
class ItemsAdderLayout:
    """Result of ItemsAdder detection over an archive."""

    contents_root: str  # archive dir that contains the namespace folders ("" = archive root)
    namespaces: dict[str, NamespaceLayout] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contents_root": self.contents_root,
            "evidence": self.evidence,
            "namespaces": {
                ns: {
                    "root": layout.root,
                    "config_files": sorted(layout.config_files),
                    "resourcepack_roots": sorted(layout.resourcepack_roots),
                    "legacy_asset_dirs": sorted(layout.legacy_asset_dirs),
                }
                for ns, layout in sorted(self.namespaces.items())
            },
        }


@dataclass
class LedgerRow:
    """One source-key -> target-key decision, kept for the conversion report."""

    object_id: str
    domain: str
    source_key: str
    target: str
    support: str  # direct / transform / partial / unsupported
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "object": self.object_id,
            "domain": self.domain,
            "source_key": self.source_key,
            "target": self.target,
            "support": self.support,
            "note": self.note,
        }


class Ledger:
    """Collects per-key conversion decisions without dropping any of them."""

    def __init__(self) -> None:
        self.rows: list[LedgerRow] = []

    def add(self, object_id: str, domain: str, source_key: str, target: str, support: str, note: str = "") -> None:
        self.rows.append(LedgerRow(object_id, domain, source_key, target, support, note))

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.rows:
            out[row.support] = out.get(row.support, 0) + 1
        return out


def _load_mapping(name: str) -> dict[str, Any]:
    """Read a bundled data-driven mapping table (mappings/itemsadder/*.json)."""
    return util.read_json(Path(paths.MAPPINGS_DIR) / "itemsadder" / name)


# --- detection ------------------------------------------------------------

def detect_itemsadder(archive: ModArchive, log: Log | None = None) -> ItemsAdderLayout | None:
    """Return a layout when the archive is an ItemsAdder content pack, else None.

    A real mod jar is rejected first: the presence of loader metadata means the
    bytecode path is the right adapter even if someone dropped an IA pack next
    to it.
    """
    log = log or Log()
    if any(archive.exists(n) for n in ("fabric.mod.json", "quilt.mod.json")):
        return None
    if any(n.endswith("mods.toml") for n in archive.names):
        return None

    ymls = [n for n in archive.names if n.lower().endswith((".yml", ".yaml"))]
    if not ymls:
        return None

    declarations: list[tuple[str, str]] = []
    evidence: list[str] = []
    for name in sorted(ymls):
        data = _safe_yaml(archive, name, log)
        if not isinstance(data, dict):
            continue
        info = data.get("info")
        namespace = info.get("namespace") if isinstance(info, dict) else None
        if namespace:
            declarations.append((name, str(namespace).strip().lower()))
            continue
        # A file may omit `info:` when it only carries lang/categories; it still
        # marks the folder as ItemsAdder content.
        if any(k in data for k in IA_ROOT_KEYS):
            evidence.append(f"{name}: ItemsAdder root key without info.namespace")

    if not declarations:
        return None

    layout = ItemsAdderLayout(contents_root="")
    layout.evidence = [f"{n}: info.namespace={ns}" for n, ns in declarations[:20]] + evidence

    # The `contents/` folder is the namespace container when the pack was copied
    # from plugins/ItemsAdder/. Otherwise the input directory already *is* it.
    with_contents = [n for n, _ in declarations if "/contents/" in f"/{n}"]
    if with_contents:
        layout.contents_root = _common_contents_root(with_contents)

    for name, namespace in declarations:
        rel = name
        if layout.contents_root:
            prefix = layout.contents_root + "/"
            rel = name[len(prefix):] if name.startswith(prefix) else name
        parts = rel.split("/")
        ns_dir = f"{layout.contents_root}/{parts[0]}" if len(parts) > 1 else layout.contents_root
        ns_layout = layout.namespaces.setdefault(
            namespace, NamespaceLayout(namespace=namespace, root=ns_dir.strip("/"))
        )
        if name not in ns_layout.config_files:
            ns_layout.config_files.append(name)

    for ns_layout in layout.namespaces.values():
        _classify_resources(archive, ns_layout)

    log.info(
        "ItemsAdder pack detected",
        namespaces=len(layout.namespaces),
        contents_root=layout.contents_root or "(archive root)",
    )
    return layout


def _safe_yaml(archive: ModArchive, name: str, log: Log) -> Any:
    text = archive.read_text(name)
    if text is None:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        log.warn("skipping malformed ItemsAdder yaml", path=name)
        return None


def _common_contents_root(names: list[str]) -> str:
    """Locate the ``contents/`` directory that contains the namespace folders.

    A leading ``/`` is prepended before splitting so a pack whose archive root
    *is* the contents folder (``contents/<ns>/...``) resolves to ``contents``
    rather than failing to match.
    """
    roots: set[str] = set()
    for name in names:
        padded = "/" + name
        if "/contents/" not in padded:
            continue
        head = padded.split("/contents/", 1)[0]
        roots.add((head + "/contents").lstrip("/"))
    # Prefer the shallowest root; nested `contents` folders belong to it.
    return sorted(roots, key=lambda r: (r.count("/"), r))[0] if roots else ""


def _classify_resources(archive: ModArchive, ns_layout: NamespaceLayout) -> None:
    root = ns_layout.root
    prefix = f"{root}/" if root else ""
    seen: set[str] = set()

    for candidate in RESOURCEPACK_DIRS:
        probe = f"{prefix}{candidate}/"
        if any(n.startswith(probe + "assets/") for n in archive.names):
            ns_layout.resourcepack_roots.append(f"{root}/{candidate}".strip("/"))
            seen.add(probe)
    # A namespace folder that directly contains assets/ is itself a pack root.
    if any(n.startswith(f"{prefix}assets/") for n in archive.names):
        ns_layout.resourcepack_roots.append(root)
        seen.add(prefix)

    for asset_dir in LEGACY_ASSET_DIRS:
        probe = f"{prefix}{asset_dir}/"
        if any(n.startswith(probe) for n in archive.names):
            ns_layout.legacy_asset_dirs.append(asset_dir)


# --- analyzer -------------------------------------------------------------

class ItemsAdderAnalyzer:
    """Build IR from an ItemsAdder content pack."""

    def __init__(
        self,
        log: Log,
        minecraft_version: str = "1.21.4",
        settings: Settings | None = None,
    ) -> None:
        self.log = log
        self.minecraft_version = minecraft_version
        self.settings = settings or Settings()
        self.ledger = Ledger()
        # Per-object scratch state; reset at the top of _build_item.
        self._consumed: set[str] = set()
        self._current_id: str = ""

        attributes = _load_mapping("attributes.json")
        # ItemsAdder accepts camelCase (attackDamage), SCREAMING_SNAKE
        # (ATTACK_DAMAGE) and snake_case (attack_damage). Stripping underscores
        # and lowercasing collapses all three onto one lookup key.
        self.attr_names: dict[str, str] = {
            _norm_key(k): v for k, v in attributes.get("attributes", {}).items()
        }
        self.attr_operations: dict[str, str] = {
            k.lower(): v for k, v in attributes.get("operations", {}).items()
        }
        self.default_operation: str = attributes.get("default_operation", "add_value")
        self.slots: dict[str, str] = {_norm_key(k): v for k, v in attributes.get("slots", {}).items()}

        block_types = _load_mapping("block_types.json").get("types", {})
        self.block_types: dict[str, dict[str, Any]] = {k.upper(): v for k, v in block_types.items()}

        flags = _load_mapping("item_flags.json").get("flags", {})
        self.item_flags: dict[str, dict[str, Any]] = {k.upper(): v for k, v in flags.items()}

        self._events_table: dict[str, Any] = _load_mapping("events.json")

        behaviours_map = _load_mapping("behaviours.json")
        self.behaviour_map: dict[str, dict[str, Any]] = behaviours_map.get("behaviours", {})
        self.specific_map: dict[str, dict[str, Any]] = behaviours_map.get("specific_properties", {})
        self.item_key_map: dict[str, str] = behaviours_map.get("item_keys", {})

        # ItemsAdder packs ship recipes as ordinary datapack JSON, so they are
        # parsed by the very same routine the mod adapter uses. Instantiating
        # the analyzer is cheap (it only loads mapping tables) and guarantees
        # identical normalization on both paths.
        from .analyzer import Analyzer

        self._recipe_parser = Analyzer(log, minecraft_version, self.settings)

    # --- entry point ------------------------------------------------------
    def analyze(self, archive: ModArchive, layout: ItemsAdderLayout | None = None) -> AnalysisResult:
        layout = layout or detect_itemsadder(archive, self.log)
        if layout is None:
            raise ValueError("archive is not an ItemsAdder content pack")

        namespaces = sorted(layout.namespaces)
        primary = namespaces[0] if namespaces else "itemsadder"
        metadata = ModMetadata(
            id=primary,
            name=primary,
            version="0.0.0",
            loader="itemsadder",
            namespace=primary,
            source_files=[f for ns in layout.namespaces.values() for f in ns.config_files],
            raw={"detection": "itemsadder", "layout": layout.to_dict()},
        )
        result = AnalysisResult(
            metadata=metadata,
            minecraft_version=self.minecraft_version,
            content_namespaces=list(namespaces),
            source_kind="itemsadder",
        )

        documents: dict[str, list[tuple[str, dict[str, Any]]]] = {ns: [] for ns in namespaces}
        for ns in namespaces:
            for name in sorted(layout.namespaces[ns].config_files):
                data = _safe_yaml(archive, name, self.log)
                if isinstance(data, dict):
                    documents[ns].append((name, data))

        self._archive = archive
        self._layout = layout
        self._documents = documents

        self._collect_lang(result)
        self._collect_resources(result)
        self._collect_categories(result)
        for ns in namespaces:
            self._build_namespace(result, ns)
        # Needs the built items (they carry the armor_rendering metadata), so it
        # runs after every namespace has been processed.
        self._emit_equipment_assets(result)
        self._emit_sound_registry(result)
        self._report_root_keys(result)
        self._collect_datapack_recipes(result)
        self._link_block_items(result)
        self._link_loot(result)

        result.conversion_ledger = [row.to_dict() for row in self.ledger.rows]
        return result

    # --- lang -------------------------------------------------------------
    def _collect_lang(self, result: AnalysisResult) -> None:
        """Read ItemsAdder ``lang:`` sections into per-locale maps.

        ItemsAdder stores display text as translation keys
        (``display-name-my_item``) and resolves them from these files, so the
        generator needs them to produce a readable CraftEngine item name.
        """
        self._ia_lang: dict[str, dict[str, str]] = {}
        for ns, docs in self._documents.items():
            for name, data in docs:
                lang = data.get("lang")
                if not isinstance(lang, dict):
                    continue
                for locale, entries in lang.items():
                    if not isinstance(entries, dict):
                        continue
                    bucket = result.lang_by_locale.setdefault(_mc_locale(str(locale)), {})
                    ia_bucket = self._ia_lang.setdefault(str(locale).lower(), {})
                    for key, value in entries.items():
                        if value is None:
                            continue
                        bucket[str(key)] = str(value)
                        ia_bucket[str(key)] = str(value)
        result.lang = dict(result.lang_by_locale.get("en_us", {}))
        if result.lang_by_locale:
            self.log.info("ItemsAdder lang collected", locales=len(result.lang_by_locale))

    def _resolve_text(self, value: Any, locale: str | None = None) -> str | None:
        """Resolve an ItemsAdder display string, following translation keys.

        The locale defaults to ``ia_default_locale`` so a pack authored in
        Russian yields Russian item names instead of raw ``display-name-*`` keys.
        """
        if value is None:
            return None
        locale = (locale or getattr(self.settings, "ia_default_locale", "en")).lower()
        text = str(value)
        table = self._ia_lang.get(locale) or self._ia_lang.get("en") or {}
        if text in table:
            return table[text]
        # ItemsAdder also accepts "<lang:key>" style placeholders in some packs.
        if text.startswith("display-name-") or text.startswith("lore-") or text.startswith("display-category-"):
            return table.get(text, text)
        return text

    # --- sound registry ---------------------------------------------------
    def _emit_sound_registry(self, result: AnalysisResult) -> None:
        """Turn an IA ``sounds:`` root map into ``assets/<ns>/sounds.json``.

        ItemsAdder registers sound events from the ``sounds:`` key; the .ogg
        files alone are not enough - without the vanilla ``sounds.json`` the id
        referenced by block/furniture ``sounds`` settings resolves to nothing.
        """
        per_ns: dict[str, dict[str, Any]] = {}
        for ns, documents in self._documents.items():
            for name, data in documents:
                if not isinstance(data, dict):
                    continue
                sounds = data.get("sounds")
                if not isinstance(sounds, dict):
                    continue
                bucket = per_ns.setdefault(ns, {})
                for sound_id, body in sounds.items():
                    if not isinstance(body, dict):
                        continue
                    sub = ((body.get("settings") or {}).get("subtitle")
                           if isinstance(body.get("settings"), dict) else None)
                    sub_path = body.get("path") or ""
                    entry = {
                        "sounds": [f"{ns}:{str(sub_path).strip('/')}/{sound_id}".replace("//", "/")
                                   if sub_path else f"{ns}:{sound_id}"],
                    }
                    if sub:
                        entry["subtitle"] = str(sub)
                    bucket[str(sound_id)] = entry
                    self.ledger.add(f"{ns}:{sound_id}", "pack", "sounds", "sounds.json", "direct",
                                    f"Registered as a vanilla sound event (from {name}).")
        for ns, entries in per_ns.items():
            result.generated_assets[f"assets/{ns}/sounds.json"] = (
                json.dumps(entries, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        if per_ns:
            self.log.info("ItemsAdder sound registry generated", namespaces=len(per_ns))

    # --- root keys with no CraftEngine equivalent -------------------------
    # Root keys whose content this adapter actually consumes.
    _HANDLED_ROOT_KEYS = {"items", "categories", "lang", "armors_rendering", "recipes", "info", "sounds"}
    # Root keys that are real ItemsAdder content but have no CraftEngine
    # counterpart. Reported once per namespace so the user knows what did not
    # travel, instead of finding out in game.
    _UNSUPPORTED_ROOT_KEYS = {
        "entities": "Custom entities are a separate CraftEngine domain and are not converted.",
        "liquids": "CraftEngine has no custom-liquid configuration.",
        "hud": "CraftEngine has no HUD element configuration.",
        "emotes": "CraftEngine has no emote system.",
        "biomes": "CraftEngine does not define biomes; keep them in a datapack.",
        "world_populators": "World generation belongs in a datapack, not CraftEngine.",
        "paintings": "CraftEngine has no painting configuration.",
        "books": "Convert to `written_book_content` per item by hand; there is no book registry in CraftEngine.",
        "music_discs": "Handled per item via `data.jukebox_playable`, not as a root registry.",
        "trims": "Armor trims are vanilla data; CraftEngine does not register them.",
        "fonts": "Fonts are plain resourcepack assets; CraftEngine does not own them.",
        "blocks": "Blocks are ItemsAdder items with `behaviours.block`; a root `blocks` map is not read.",
        "furnitures": "Furniture are ItemsAdder items with `behaviours.furniture`; a root map is not read.",
    }

    def _report_root_keys(self, result: AnalysisResult) -> None:
        """Report IA root keys that carry content this adapter does not read.

        Without this, a pack shipping ``entities:`` or ``liquids:`` looks fully
        converted while whole sections never left the source.
        """
        reported: set[tuple[str, str]] = set()
        for ns, documents in self._documents.items():
            for name, data in documents:
                if not isinstance(data, dict):
                    continue
                for key in data:
                    if key in self._HANDLED_ROOT_KEYS:
                        continue
                    if (ns, key) in reported:
                        continue
                    reported.add((ns, key))
                    note = self._UNSUPPORTED_ROOT_KEYS.get(str(key))
                    if note is None:
                        note = "Unknown ItemsAdder root key; not read by this converter."
                    self.ledger.add(f"{ns}:<root>", "pack", str(key), "-", "unsupported",
                                    f"{note} (from {name})")

    # --- armor equipment assets -------------------------------------------
    # Vanilla resolves an equipment layer texture `<ns>:<path>` to
    # assets/<ns>/textures/entity/equipment/<layer_type>/<path>.png, so an IA
    # layer_1/layer_2 reference only works once the file actually lives there.
    _EQUIP_LAYER_TYPES = {"layer_1": "humanoid", "layer_2": "humanoid_leggings"}

    def _emit_equipment_assets(self, result: AnalysisResult) -> None:
        """Write ``assets/<ns>/equipment/<id>.json`` for every armors_rendering id.

        Without this file ``data.equippable.asset_id`` is a dangling reference
        and the armor simply does not render, so the asset is generated rather
        than left as a manual task.
        """
        by_asset: dict[str, dict[str, Any]] = {}
        for node in result.items.values():
            meta = (node.metadata or {}).get("armor_rendering")
            if isinstance(meta, dict) and meta.get("id"):
                by_asset.setdefault(meta["id"], {"source": meta.get("source") or {},
                                                 "namespace": node.namespace,
                                                 "owner": node.id})
        for asset_id, info in sorted(by_asset.items()):
            ns = info["namespace"]
            rendering = info["source"] if isinstance(info["source"], dict) else {}
            layers: dict[str, list[dict[str, Any]]] = {}
            unresolved: list[str] = []
            for ia_key, layer_type in self._EQUIP_LAYER_TYPES.items():
                ref = rendering.get(ia_key)
                if not ref:
                    continue
                rel = self._relocate_armor_texture(result, ns, str(ref), layer_type)
                if rel is None:
                    unresolved.append(ia_key)
                    continue
                layers.setdefault(layer_type, []).append({"texture": rel})
            if not layers:
                self.ledger.add(info["owner"], "item", "armors_rendering", "-", "partial",
                                "Equipment asset needs layer_1/layer_2; none resolved, so no "
                                "assets/%s/equipment/%s.json was generated." % (ns, asset_id.split(":", 1)[1]))
                continue
            result.generated_assets[f"assets/{ns}/equipment/{asset_id.split(':', 1)[1]}.json"] = (
                json.dumps({"layers": layers}, indent=2, ensure_ascii=False) + "\n")
            note = "Generated assets/%s/equipment/%s.json" % (ns, asset_id.split(":", 1)[1])
            if unresolved:
                note += "; unresolved: " + ", ".join(unresolved)
            self.ledger.add(info["owner"], "item", "armors_rendering",
                            "data.equippable.asset_id + equipment asset", "direct", note)

    def _relocate_armor_texture(self, result: AnalysisResult, ns: str, ref: str,
                                layer_type: str) -> str | None:
        """Point an IA armor layer at a vanilla equipment texture path.

        Returns the resource location for the equipment asset, or ``None`` when
        the texture cannot be found in the pack. The existing ``resource_map``
        entry is rewritten in place so the driver copies the file to its new
        home instead of the ItemsAdder one.
        """
        ref = ref.strip()
        if ref.endswith(".png"):
            ref = ref[:-4]
        wanted = ref.split(":", 1)[-1].lstrip("/")
        for src, dest in list(result.resource_map.items()):
            if not dest.startswith(f"assets/{ns}/textures/"):
                continue
            tail = dest[len(f"assets/{ns}/textures/"):]
            if tail[:-4] != wanted and not tail.endswith("/" + wanted + ".png"):
                continue
            new_dest = f"assets/{ns}/textures/entity/equipment/{layer_type}/{wanted}.png"
            result.resource_map[src] = new_dest
            self._resource_index[new_dest] = src
            return f"{ns}:{wanted}"
        return None

    # --- resources --------------------------------------------------------
    def _collect_resources(self, result: AnalysisResult) -> None:
        """Map every ItemsAdder asset to its output resourcepack path.

        ItemsAdder keeps resources under ``contents/<ns>/``; the generated pack
        needs them under ``resourcepack/assets/<ns>/``. The mapping is recorded
        instead of copying here so the driver stays the only writer.
        """
        self._resource_index: dict[str, str] = {}
        for ns, ns_layout in self._layout.namespaces.items():
            root = ns_layout.root
            prefix = f"{root}/" if root else ""

            for rp_root in ns_layout.resourcepack_roots:
                rp_prefix = f"{rp_root}/" if rp_root else ""
                for name in self._archive.names:
                    if not name.startswith(rp_prefix + "assets/"):
                        continue
                    if name in result.resource_map:
                        continue
                    result.resource_map[name] = name[len(rp_prefix):]
                    self._resource_index[name[len(rp_prefix):]] = name

            for asset_dir in ns_layout.legacy_asset_dirs:
                src_prefix = f"{prefix}{asset_dir}/"
                for name in self._archive.names:
                    if not name.startswith(src_prefix) or name in result.resource_map:
                        continue
                    dest = f"assets/{ns}/{name[len(prefix):]}"
                    result.resource_map[name] = dest
                    self._resource_index[dest] = name

            for name in ns_layout.config_files:
                result.resources.append(
                    ResourceNode(
                        source_path=name,
                        namespace=ns,
                        kind="itemsadder_config",
                        raw_hash=util.sha256_bytes(self._archive.read(name) or b""),
                    )
                )
        self.log.info("ItemsAdder resources mapped", files=len(result.resource_map))

    def _asset_exists(self, rp_path: str) -> bool:
        return rp_path in self._resource_index

    # --- categories -------------------------------------------------------
    def _collect_categories(self, result: AnalysisResult) -> None:
        """Read ItemsAdder ``categories:`` (the /ia GUI grouping).

        Categories with the same id in different namespaces are merged by
        ItemsAdder; the generated CraftEngine category keeps them separate so
        the item lists stay unambiguous.
        """
        for ns, docs in self._documents.items():
            for name, data in docs:
                categories = data.get("categories")
                if not isinstance(categories, dict):
                    continue
                bucket = result.source_categories.setdefault(ns, {})
                for cat_id, cat in categories.items():
                    if not isinstance(cat, dict):
                        continue
                    if cat.get("enabled") is False:
                        self.ledger.add(f"{ns}:{cat_id}", "category", "enabled: false", "-", "unsupported",
                                        "Disabled ItemsAdder category skipped.")
                        continue
                    bucket[str(cat_id)] = cat
                    self.ledger.add(f"{ns}:{cat_id}", "category", "categories", "categories", "direct")
        if result.source_categories:
            self.log.info("ItemsAdder categories collected", count=sum(len(v) for v in result.source_categories.values()))

    # --- per-namespace content -------------------------------------------
    def _build_namespace(self, result: AnalysisResult, ns: str) -> None:
        raw_items: dict[str, dict[str, Any]] = {}
        raw_armors: dict[str, dict[str, Any]] = {}

        for name, data in self._documents.get(ns, []):
            items = data.get("items")
            if isinstance(items, dict):
                for item_id, body in items.items():
                    if not isinstance(body, dict):
                        continue
                    if str(item_id) in raw_items:
                        result.warnings.append(f"{ns}:{item_id}: duplicate ItemsAdder item id (last wins), from {name}")
                    raw_items[str(item_id)] = {"__source_file__": name, **body}
            armors = data.get("armors_rendering")
            if isinstance(armors, dict):
                for armor_id, body in armors.items():
                    if isinstance(body, dict):
                        raw_armors[str(armor_id)] = body

        resolved = self._resolve_templates(raw_items, ns, result)
        for item_id, body in sorted(resolved.items()):
            if body.pop("__is_template__", False):
                self.ledger.add(f"{ns}:{item_id}", "item", "template", "-", "unsupported",
                                "ItemsAdder template: used as a base only, not generated.")
                continue
            try:
                self._build_item(result, ns, item_id, body, raw_armors)
            except Exception as exc:  # noqa: BLE001 - one bad item must not kill the pack
                result.warnings.append(f"{ns}:{item_id}: conversion failed: {exc}")
                self.log.error("ItemsAdder item conversion failed", item=f"{ns}:{item_id}", error=str(exc))

    def _resolve_templates(
        self, raw_items: dict[str, dict[str, Any]], ns: str, result: AnalysisResult
    ) -> dict[str, dict[str, Any]]:
        """Expand ItemsAdder ``template`` / ``variant_of`` inheritance."""
        resolved: dict[str, dict[str, Any]] = {}

        def resolve(item_id: str, seen: set[str]) -> dict[str, Any]:
            if item_id in resolved:
                return resolved[item_id]
            if item_id in seen:
                result.warnings.append(f"{ns}:{item_id}: variant_of cycle detected")
                return {}
            body = raw_items.get(item_id)
            if body is None:
                result.warnings.append(f"{ns}:{item_id}: variant_of target not found")
                return {}
            seen = seen | {item_id}
            merged = copy.deepcopy(body)
            parent = merged.get("variant_of")
            if isinstance(parent, str) and parent:
                parent_id = parent if ":" in parent else f"{ns}:{parent}"
                parent_path = parent_id.split(":", 1)[1]
                parent_body = resolve(parent_path, seen)
                if parent_body:
                    inheritable = {
                        k: v for k, v in parent_body.items()
                        # A template stays a template only for itself: inheriting
                        # `template: true` would silently delete the variant.
                        # `variant_of` is already resolved on the parent, so
                        # re-inheriting it would re-trigger the chain.
                        if k not in ("template", "variant_of", "__is_template__")
                    }
                    merged = _deep_merge(copy.deepcopy(inheritable), merged)
                self.ledger.add(f"{ns}:{item_id}", "item", "variant_of", "inlined", "transform",
                                f"Inherited from {parent_id}.")
            if merged.get("template") is True:
                merged["__is_template__"] = True
            resolved[item_id] = merged
            return merged

        for item_id in raw_items:
            resolve(item_id, set())
        return resolved

    # --- items ------------------------------------------------------------
    def _build_item(
        self,
        result: AnalysisResult,
        ns: str,
        item_id: str,
        raw: dict[str, Any],
        armors: dict[str, dict[str, Any]],
    ) -> None:
        full_id = f"{ns}:{item_id}"
        source_file = str(raw.get("__source_file__", ""))
        # Reset per item: the ledger reports anything left unconsumed.
        self._consumed: set[str] = set()
        self._current_id: str = full_id
        node = ItemNode(
            id=full_id,
            namespace=ns,
            kind="itemsadder_item",
            confidence=1.0,
            status=status.DETECTED,
            source={"itemsadder": source_file},
        )
        node.metadata["itemsadder"] = {"source_file": source_file}

        if raw.get("enabled") is False:
            # `enabled: false` means the author switched the object off, so it
            # is not content at all. It stays out of the IR (keeping
            # Detected = Generated + Diagnostic-only honest) but is still
            # listed in the ledger so nothing disappears without a trace.
            self.ledger.add(full_id, "item", "enabled: false", "-", "unsupported",
                            "Disabled in the source pack; not converted.")
            result.warnings.append(f"{full_id}: skipped, disabled in the ItemsAdder pack")
            return

        # --- identity / display -------------------------------------------
        display = raw.get("display_name", raw.get("name"))
        if "display_name" in raw or "name" in raw:
            self._touch("display_name"); self._touch("name")
        resolved_name = self._resolve_text(display)
        if resolved_name:
            node.display_name = resolved_name
            self.ledger.add(full_id, "item", "display_name", "data.item_name", "direct")

        lore = raw.get("lore")
        if "lore" in raw:
            self._touch("lore")
        if isinstance(lore, list):
            node.lore = [str(self._resolve_text(x) or x) for x in lore]
            self.ledger.add(full_id, "item", "lore", "data.lore", "direct")

        # --- resource / graphics ------------------------------------------
        resource = raw.get("resource") if isinstance(raw.get("resource"), dict) else {}
        graphics = raw.get("graphics") if isinstance(raw.get("graphics"), dict) else {}
        material = resource.get("material") or graphics.get("material")
        if material:
            node.base_material = _material_id(str(material))
            self.ledger.add(full_id, "item", "resource.material", "material", "direct")

        self._apply_model(node, full_id, ns, resource, graphics)

        # --- attributes -----------------------------------------------------
        self._apply_attributes(node, full_id, raw)

        # --- durability -----------------------------------------------------
        self._apply_durability(node, full_id, raw)

        # --- enchants / flags / misc data ----------------------------------
        self._apply_enchants(node, full_id, raw)
        self._apply_item_flags(node, full_id, raw)
        self._apply_consumable(node, full_id, raw)
        self._apply_misc(node, full_id, raw)

        # --- events -> CraftEngine events DSL -------------------------------
        self._apply_events(node, full_id, raw)

        # --- armour rendering ------------------------------------------------
        self._apply_armor(node, full_id, raw, armors)

        # --- behaviours -> block / furniture / item behaviors ----------------
        self._touch("behaviours"); self._touch("behaviors"); self._touch("specific_properties")
        behaviours = raw.get("behaviours") if isinstance(raw.get("behaviours"), dict) else {}
        legacy_behaviours = raw.get("behaviors") if isinstance(raw.get("behaviors"), dict) else {}
        behaviours = _deep_merge(legacy_behaviours, behaviours)
        specific = raw.get("specific_properties") if isinstance(raw.get("specific_properties"), dict) else {}
        self._apply_behaviours(result, node, full_id, ns, raw, behaviours, specific, armors)

        self._report_unhandled(node, full_id, raw)

        node.status = status.ANALYZED
        result.items[full_id] = node

    # --- model ------------------------------------------------------------
    def _apply_model(
        self,
        node: ItemNode,
        full_id: str,
        ns: str,
        resource: dict[str, Any],
        graphics: dict[str, Any],
    ) -> None:
        """Translate ``resource``/``graphics`` into a CraftEngine model layer.

        ``generate: true`` + ``textures`` means "build me an item/generated
        model" - CraftEngine's simplified ``texture``/``textures`` form does
        exactly that. ``generate: false`` + ``model_path`` means "use my .json
        model", which becomes an explicit ``minecraft:model`` node.
        """
        self._touch("resource"); self._touch("graphics")
        generate = resource.get("generate", graphics.get("generate", True))
        model_path = resource.get("model_path") or graphics.get("model") or graphics.get("model_path")
        textures = resource.get("textures") or graphics.get("textures") or graphics.get("texture")
        parent = resource.get("parent") or graphics.get("parent")

        if isinstance(textures, str):
            textures = [textures]
        if isinstance(textures, dict):
            textures = list(textures.values())

        ce_model = self._ce_model(model_path, ns) if model_path else None
        ce_textures = [t for t in (self._ce_texture(x, ns) for x in (textures or [])) if t]

        handheld = str(parent).lower().endswith("handheld") if parent else _is_handheld(node.base_material)

        if generate is False and ce_model:
            node.model = ce_model
            node.metadata["model_source"] = "itemsadder:model_path"
            if ce_textures:
                node.textures = ce_textures
            # No `generation` block here on purpose: the pack ships a real
            # .json model, so CraftEngine must use it as-is. Adding a
            # generation parent would override the author's own model.
            self.ledger.add(full_id, "item", "resource.model_path", "model", "direct")
            return

        if ce_textures:
            node.textures = ce_textures
            node.metadata["model_source"] = "itemsadder:generated"
            if handheld or (parent and not str(parent).lower().endswith("generated")):
                # Handheld tools must not be flattened into item/generated or
                # they render lying down in hand.
                node.model_tree = {
                    "type": "minecraft:model",
                    "path": f"{ns}:item/{full_id.split(':', 1)[1]}",
                    "generation": {
                        "parent": "minecraft:item/handheld",
                        "textures": {f"layer{i}": t for i, t in enumerate(ce_textures)},
                    },
                }
            self.ledger.add(full_id, "item", "resource.textures", "texture/textures", "direct")
            return

        if ce_model:
            node.model = ce_model
            self.ledger.add(full_id, "item", "resource.model_path", "model", "direct")
            return

        self.ledger.add(full_id, "item", "resource", "-", "partial",
                        "No usable texture or model_path; CraftEngine item needs one of them.")

    def _ce_texture(self, ref: Any, ns: str) -> str | None:
        """Normalize an ItemsAdder texture reference into a CraftEngine path.

        Accepted source forms: ``item/foo.png``, ``textures/item/foo``,
        ``textures/items:foo`` (folder:name) and already-namespaced
        ``minecraft:item/foo``.
        """
        if ref is None:
            return None
        text = str(ref).strip()
        if not text:
            return None
        if ":" in text:
            left, _, right = text.partition(":")
            if "/" in left:
                # folder:name form -> plain path
                text = f"{left}/{right}"
            else:
                return self._strip_ext(text, (".png",))
        text = text.lstrip("/")
        text = self._strip_ext(text, (".png",))
        if text.startswith("textures/"):
            text = text[len("textures/"):]
        return f"{ns}:{text}"

    def _ce_model(self, ref: Any, ns: str) -> str | None:
        """Normalize an ItemsAdder ``model_path`` into a CraftEngine model id."""
        if ref is None:
            return None
        text = str(ref).strip()
        if not text:
            return None
        text = self._strip_ext(text, (".json",))
        if ":" in text:
            left, _, right = text.partition(":")
            if "/" in left:
                text = f"{left}/{right}"
            else:
                return text  # minecraft:item/emerald and friends
        text = text.lstrip("/")
        if text.startswith("models/"):
            text = text[len("models/"):]
        return f"{ns}:{text}"

    @staticmethod
    def _strip_ext(text: str, exts: tuple[str, ...]) -> str:
        for ext in exts:
            if text.endswith(ext):
                return text[: -len(ext)]
        return text

    # --- attributes --------------------------------------------------------
    def _apply_attributes(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        source = raw.get("attribute_modifiers")
        if "attribute_modifiers" in raw:
            self._touch("attribute_modifiers")
        if not isinstance(source, dict):
            return
        modifiers: list[dict[str, Any]] = []
        for slot, entries in source.items():
            ce_slot = self.slots.get(_norm_key(str(slot)))
            if ce_slot is None:
                self.ledger.add(full_id, "item", f"attribute_modifiers.{slot}", "-", "unsupported",
                                f"Unknown equipment slot '{slot}'.")
                continue
            for attr_name, value in _flatten_attributes(entries):
                ce_attr = self.attr_names.get(_norm_key(str(attr_name)))
                if ce_attr is None:
                    self.ledger.add(full_id, "item", f"attribute_modifiers.{slot}.{attr_name}", "-", "unsupported",
                                    f"Unknown Bukkit attribute '{attr_name}'.")
                    continue
                amount, operation = value
                modifiers.append({
                    "type": ce_attr,
                    "amount": amount,
                    "operation": self.attr_operations.get(str(operation).lower(), self.default_operation),
                    "slot": ce_slot,
                    "id": f"{full_id}/{ce_attr}/{ce_slot}",
                })
                if ce_attr == "attack_damage":
                    node.attack_damage = float(amount)
                elif ce_attr == "attack_speed":
                    node.attack_speed = float(amount)
                elif ce_attr == "attack_knockback":
                    node.attack_knockback = float(amount)
        if modifiers:
            node.attributes = modifiers
            self.ledger.add(full_id, "item", "attribute_modifiers", "data.attribute_modifiers", "direct",
                            f"{len(modifiers)} modifier(s).")

    # --- durability ---------------------------------------------------------
    def _apply_durability(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        source = raw.get("durability")
        if "durability" in raw:
            self._touch("durability")
        if isinstance(source, (int, float)):
            node.durability = int(source)
            self.ledger.add(full_id, "item", "durability", "data.max_damage", "direct")
            return
        if not isinstance(source, dict):
            return
        max_durability = source.get("max_durability", source.get("max_custom_durability"))
        if max_durability is not None:
            node.durability = int(max_durability)
            self.ledger.add(full_id, "item", "durability.max_durability", "data.max_damage", "direct")
        if source.get("unbreakable") is True:
            node.components["unbreakable"] = True
            self.ledger.add(full_id, "item", "durability.unbreakable", "data.unbreakable", "direct")
        if source.get("disappear_when_broken") is False:
            node.settings["prevent_break"] = True
            self.ledger.add(full_id, "item", "durability.disappear_when_broken", "settings.prevent_break", "transform")
        for key in ("durability", "usages"):
            if source.get(key) is not None:
                self.ledger.add(full_id, "item", f"durability.{key}", "-", "partial",
                                "Initial damage / usage counter has no CraftEngine data component.")

    # --- enchants -----------------------------------------------------------
    def _apply_enchants(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        enchants = raw.get("enchants")
        if "enchants" in raw:
            self._touch("enchants")
        if isinstance(enchants, list):
            table: dict[str, Any] = {}
            for entry in enchants:
                name, _, level = str(entry).rpartition(":")
                if not name:
                    name, level = str(entry), "1"
                table[_enchant_id(name)] = _as_int(level, 1)
            if table:
                node.enchantments = table
                self.ledger.add(full_id, "item", "enchants", "data.enchantments", "transform",
                                "Third-party enchant ids need the owning plugin's namespace.")
        elif isinstance(enchants, dict):
            node.enchantments = {_enchant_id(str(k)): _as_int(v, 1) for k, v in enchants.items()}
            self.ledger.add(full_id, "item", "enchants", "data.enchantments", "transform")

        blocked = raw.get("blocked_enchants")
        if "blocked_enchants" in raw:
            self._touch("blocked_enchants")
        if isinstance(blocked, list) and blocked:
            if any(str(x).upper() == "ALL" for x in blocked):
                node.settings["enchantable"] = False
                self.ledger.add(full_id, "item", "blocked_enchants: ALL", "settings.enchantable: false", "direct")
            else:
                self.ledger.add(full_id, "item", "blocked_enchants", "-", "partial",
                                "Per-enchant blocking has no CraftEngine equivalent.")

    # --- item flags ---------------------------------------------------------
    def _apply_item_flags(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        flags = raw.get("item_flags")
        if "item_flags" in raw:
            self._touch("item_flags")
        if not isinstance(flags, list):
            return
        hidden: list[str] = []
        for flag in flags:
            spec = self.item_flags.get(str(flag).upper())
            if spec is None:
                self.ledger.add(full_id, "item", f"item_flags.{flag}", "-", "unsupported",
                                "Unknown Bukkit ItemFlag.")
                continue
            hidden.extend(spec.get("hide_tooltip", []))
            self.ledger.add(full_id, "item", f"item_flags.{flag}", "data.hide_tooltip",
                            spec.get("support", "transform"), spec.get("note", ""))
        if hidden:
            node.components.setdefault("hide_tooltip", [])
            for entry in dict.fromkeys(hidden):
                node.components["hide_tooltip"].append(entry)

    # --- consumable ---------------------------------------------------------
    def _apply_consumable(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        source = raw.get("consumable")
        if "consumable" in raw:
            self._touch("consumable")
        if isinstance(source, dict):
            food = {"nutrition": _as_int(source.get("nutrition", 0), 0)}
            if source.get("saturation") is not None:
                food["saturation"] = float(source["saturation"])
            if source.get("can_always_eat") is not None:
                food["can_always_eat"] = bool(source["can_always_eat"])
            node.food = food
            node.metadata["consumable"] = {
                k: source[k] for k in ("consume_seconds", "sound", "has_consume_particles", "animation")
                if source.get(k) is not None
            }
            self.ledger.add(full_id, "item", "consumable", "data.food + data.consumable", "direct")
            return

        # Older packs express food through events.eat.feed / events.drink.feed.
        events = raw.get("events")
        if not isinstance(events, dict):
            return
        for trigger in ("eat", "drink", "consume"):
            body = events.get(trigger)
            if not isinstance(body, dict):
                continue
            feed = body.get("feed")
            if isinstance(feed, dict):
                node.food = {
                    "nutrition": _as_int(feed.get("amount", 0), 0),
                    "saturation": float(feed.get("saturation", 0) or 0),
                    "can_always_eat": False,
                }
                self.ledger.add(full_id, "item", f"events.{trigger}.feed", "data.food", "transform",
                                "ItemsAdder applies food through an event; CraftEngine uses the food component.")

    # --- events -> CraftEngine events DSL -----------------------------------

    # Legacy nested shapes consumed by _apply_consumable, not by the DSL path.
    _LEGACY_EVENT_KEYS = {"eat", "drink", "consume"}

    def _apply_events(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        """Translate ItemsAdder ``events:`` into the CraftEngine events DSL.

        One CraftEngine entry is emitted per ItemsAdder event so that a
        per-event ``chance`` stays a condition on its own entry instead of
        leaking onto a sibling that happens to share the same trigger.
        """
        events = raw.get("events")
        if not isinstance(events, dict) or not events:
            return
        self._touch("events")
        table = self._events_table
        triggers: dict[str, Any] = table["event_triggers"]
        action_map: dict[str, Any] = table["actions"]
        gates: dict[str, Any] = table["gate_properties"]

        for ev_name, body in events.items():
            # Legacy `events.eat.feed` is a food component, handled elsewhere.
            if ev_name in self._LEGACY_EVENT_KEYS and isinstance(body, dict) and "actions" not in body:
                continue
            if not isinstance(body, dict):
                continue
            spec = triggers.get(ev_name)
            if spec is None:
                self.ledger.add(full_id, "item", f"events.{ev_name}", "-", "unsupported",
                                "Unknown ItemsAdder event; no CraftEngine trigger registered.")
                continue
            on = spec.get("on")
            if not on:
                self.ledger.add(full_id, "item", f"events.{ev_name}", "-", "unsupported",
                                spec.get("note", "No CraftEngine trigger equivalent."))
                continue

            conditions: list[dict[str, Any]] = []
            chance = body.get("chance")
            if chance is not None:
                gate = gates.get("chance", {})
                conditions.append({"type": gate.get("condition", "random"),
                                   gate.get("value_field", "value"): float(chance)})
                self.ledger.add(full_id, "item", f"events.{ev_name}.chance", "events[].conditions",
                                "direct", "ItemsAdder chance maps onto the CraftEngine `random` condition.")
            cooldown = body.get("cooldown")
            if cooldown is not None:
                gate = gates.get("cooldown", {})
                self.ledger.add(full_id, "item", f"events.{ev_name}.cooldown", "-",
                                gate.get("support", "unsupported"),
                                gate.get("note", "CraftEngine cooldowns are id-based."))

            functions: list[dict[str, Any]] = []
            for act in body.get("actions") or []:
                functions.extend(self._convert_action(full_id, ev_name, act, action_map, table))

            if not functions and not conditions:
                # Every action was unportable; the rows already say so.
                continue
            entry: dict[str, Any] = {"on": on, "functions": functions}
            if conditions:
                entry["conditions"] = conditions
            node.events.append(entry)
            support = spec.get("support", "transform")
            self.ledger.add(full_id, "item", f"events.{ev_name}", f"events[on={on}]", support,
                            spec.get("note", "") or "")

    def _convert_action(self, full_id: str, ev_name: str, act: Any,
                        action_map: dict[str, Any], table: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert one ItemsAdder action entry into zero or more CE functions.

        ItemsAdder repeats an action by suffixing its name (``play_sound_2``),
        so each key is stripped of that suffix before lookup.
        """
        if isinstance(act, str):
            act = {act: True}
        if not isinstance(act, dict):
            return []
        out: list[dict[str, Any]] = []
        suffix = table.get("action_suffix", r"_\d+$")
        for name, props in act.items():
            base = re.sub(suffix, "", name)
            spec = action_map.get(base)
            if spec is None:
                self.ledger.add(full_id, "item", f"events.{ev_name}.actions.{name}", "-", "unsupported",
                                "Unknown ItemsAdder action; no CraftEngine function registered.")
                continue
            ce_type = spec.get("type")
            if not ce_type:
                self.ledger.add(full_id, "item", f"events.{ev_name}.actions.{name}", "-",
                                spec.get("support", "unsupported"),
                                spec.get("note", "No CraftEngine function equivalent."))
                continue
            props = props if isinstance(props, dict) else {}
            fn: dict[str, Any] = {"type": ce_type}
            notes: list[str] = []
            for src, dst in (spec.get("fields") or {}).items():
                if src not in props:
                    continue
                value = props[src]
                if dst == "target":
                    mapped = table["targets"].get(str(value).lower())
                    if mapped is None:
                        notes.append(f"target `{value}` has no CraftEngine selector and was dropped")
                        continue
                    value = mapped
                fn[dst] = value
            # Actions needing extra shape beyond a flat field rename.
            fn, extra = self._finish_function(ce_type, fn, props, ev_name, name, table, notes)
            if extra is None:
                continue
            notes.extend(extra)
            out.append(fn)
            support = spec.get("support", "direct")
            note = spec.get("note", "")
            if notes:
                note = (note + " " if note else "") + "; ".join(notes)
                support = "partial" if support == "direct" else support
            self.ledger.add(full_id, "item", f"events.{ev_name}.actions.{name}",
                            f"events[].functions[type={ce_type}]", support, note)
        return out

    def _potion_id(self, value: str) -> str:
        """Bukkit PotionType / raw id -> vanilla effect id CraftEngine expects."""
        value = value.strip()
        if not value:
            return value
        known = self._events_table.get("potion_effects", {})
        upper = value.upper().replace("minecraft:", "")
        if upper in known:
            return known[upper]
        return value if ":" in value else "minecraft:" + value.lower()

    def _finish_function(self, ce_type: str, fn: dict[str, Any], props: dict[str, Any],
                         ev_name: str, act_name: str, table: dict[str, Any],
                         notes: list[str]) -> tuple[dict[str, Any], list[str] | None]:
        """Apply per-function shaping. Returns ``(fn, None)`` to drop the function."""
        if ce_type == "set_food":
            # IA `feed` carries both food and saturation; CE splits them.
            fn["add"] = True
            if "food" not in fn:
                fn["food"] = _as_int(props.get("amount", 0), 0)
            if props.get("saturation") is not None:
                fn = {"type": "set_food", **fn}
                return fn, notes
            return fn, notes
        if ce_type == "set_count":
            amount = _as_int(props.get("amount", 0), 0)
            fn["add"] = True
            fn["count"] = -amount if "decrement" in act_name else amount
            return fn, notes
        if ce_type == "particle":
            offsets = props.get("offset")
            if isinstance(offsets, (list, tuple)) and len(offsets) == 3:
                fn["offset_x"], fn["offset_y"], fn["offset_z"] = offsets
            elif isinstance(offsets, (int, float)):
                fn["offset_x"] = fn["offset_y"] = fn["offset_z"] = offsets
            return fn, notes
        if ce_type in ("potion_effect", "remove_potion_effect") and fn.get("potion_effect"):
            fn["potion_effect"] = self._potion_id(str(fn["potion_effect"]))
            return fn, notes
        if ce_type == "open_window":
            gui = str(fn.get("gui_type") or props.get("inventory") or "").strip().lower()
            allowed = table.get("open_window_gui_types", [])
            if gui not in allowed:
                # Emitting an out-of-enum `gui_type` would be a broken config, so
                # the intent is reported instead of guessed at.
                self.ledger.add(
                    self._current_id, "item", f"events.{ev_name}.actions.{act_name}", "-", "unsupported",
                    f"CraftEngine `open_window` only accepts {', '.join(allowed)}; "
                    f"`{gui or '<unset>'}` is a custom menu - re-express it as a `command`.")
                return fn, None
            fn["gui_type"] = gui
            return fn, notes
        if ce_type == "toast" and "icon" not in fn:
            return fn, ["CraftEngine `toast` also requires `icon`; add it manually"]
        if ce_type == "teleport":
            coords = {k: props.get(k) for k in ("x", "y", "z") if props.get(k) is not None}
            if len(coords) < 3:
                return fn, ["CraftEngine `teleport` needs explicit x/y/z; fill them in"]
            fn.update(coords)
            for key in ("pitch", "yaw", "world"):
                if props.get(key) is not None:
                    fn[key] = props[key]
            return fn, notes
        if ce_type == "drop_loot":
            item_ref = props.get("item")
            if item_ref:
                count = _as_int(props.get("min_amount", props.get("amount", 1)), 1)
                fn["loot"] = {"pools": [{"entries": [{"type": "minecraft:item",
                                                      "name": str(item_ref),
                                                      "functions": [
                                                          {"function": "minecraft:set_count",
                                                           "count": count}]}]}]}
                return fn, ["drop position defaults to the event position; review"]
            return fn, ["no `item` given; CraftEngine `drop_loot` needs a loot table"]
        return fn, notes

    # --- misc ---------------------------------------------------------------
    def _apply_misc(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        if raw.get("max_stack_size") is not None:
            self._touch("max_stack_size")
            node.components["max_stack_size"] = _as_int(raw["max_stack_size"], 64)
            self.ledger.add(full_id, "item", "max_stack_size", "data.components.max_stack_size", "transform")

        if raw.get("glint") is not None:
            self._touch("glint")
            node.components["enchantment_glint_override"] = bool(raw["glint"])
            self.ledger.add(full_id, "item", "glint", "data.components.enchantment_glint_override", "transform")

        if raw.get("fuel") is not None:
            self._touch("fuel")
            ticks = _as_int(raw["fuel"], 0)
            if ticks:
                node.settings["fuel_time"] = ticks
                self.ledger.add(full_id, "item", "fuel", "settings.fuel_time", "direct")

        if raw.get("tooltip_style"):
            self._touch("tooltip_style")
            node.components["tooltip_style"] = str(raw["tooltip_style"])
            self.ledger.add(full_id, "item", "tooltip_style", "data.tooltip_style", "direct")

        drop = raw.get("drop")
        if "drop" in raw:
            self._touch("drop")
        if isinstance(drop, dict):
            glow = drop.get("glow")
            if isinstance(glow, dict) and glow.get("enabled") and glow.get("color"):
                node.settings["glow_color"] = str(glow["color"]).lower()
                self.ledger.add(full_id, "item", "drop.glow", "settings.glow_color", "transform")
            if drop.get("show_name") is True:
                node.settings["drop_display"] = True
                self.ledger.add(full_id, "item", "drop.show_name", "settings.drop_display", "direct")

        self._apply_nbt(node, full_id, raw)

    def _apply_nbt(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        """Carry over ``nbt`` (mapping or SNBT string) and ``components_nbt_file``.

        ItemsAdder documents three spellings: a mapping, an inline SNBT string,
        and a JSON sidecar file. Only the first two can be converted here; the
        sidecar is reported because the converter never guesses at a file it
        cannot resolve relative to the source pack.
        """
        nbt = raw.get("nbt")
        if "nbt" in raw:
            self._touch("nbt")
        if isinstance(nbt, dict):
            self._absorb_nbt(node, full_id, nbt, "nbt")
        elif isinstance(nbt, str) and nbt.strip():
            parsed = _parse_snbt(nbt)
            if parsed is None:
                node.metadata["itemsadder_raw_nbt"] = nbt
                self.ledger.add(
                    full_id, "item", "nbt", "-", "partial",
                    "Inline SNBT string could not be parsed into data components; the raw "
                    "string is kept in source-map/objects.json for manual conversion.",
                )
            else:
                self._absorb_nbt(node, full_id, parsed, "nbt")

        sidecar = raw.get("components_nbt_file")
        if sidecar:
            self._touch("components_nbt_file")
            node.metadata["itemsadder_components_nbt_file"] = str(sidecar)
            self.ledger.add(
                full_id, "item", "components_nbt_file", "-", "partial",
                "Components come from an external JSON file; merge it into data.components manually.",
            )

    def _absorb_nbt(self, node: ItemNode, full_id: str, data: dict[str, Any], key: str) -> None:
        """Merge parsed NBT into the item, unwrapping a `components:` envelope."""
        if set(data) == {"components"} and isinstance(data["components"], dict):
            node.components.setdefault("raw", {}).update(data["components"])
            self.ledger.add(full_id, "item", key, "data.components", "transform",
                            "The components: envelope was unwrapped into data.components.")
        else:
            node.components.setdefault("raw", {}).update(data)
            self.ledger.add(full_id, "item", key, "data.components", "partial",
                            "Arbitrary NBT is passed through as raw components; review before shipping.")

    # --- armour --------------------------------------------------------------
    def _apply_armor(
        self,
        node: ItemNode,
        full_id: str,
        raw: dict[str, Any],
        armors: dict[str, dict[str, Any]],
    ) -> None:
        specific = raw.get("specific_properties") if isinstance(raw.get("specific_properties"), dict) else {}
        armor = specific.get("armor")
        if not isinstance(armor, dict):
            return
        self._touch("specific_properties")
        slot = armor.get("slot")
        ce_slot = self.slots.get(_norm_key(str(slot))) if slot else None
        equippable: dict[str, Any] = {}
        if ce_slot:
            equippable["slot"] = ce_slot
            self.ledger.add(full_id, "item", "specific_properties.armor.slot", "data.equippable.slot", "direct")
        custom_armor = armor.get("custom_armor")
        rendering = armors.get(str(custom_armor)) if custom_armor else None
        if isinstance(rendering, dict):
            asset_id = f"{node.namespace}:{util.safe_name(str(custom_armor))}"
            equippable["asset_id"] = asset_id
            node.metadata["armor_rendering"] = {"id": asset_id, "source": rendering}
            self.ledger.add(full_id, "item", "armors_rendering", "data.equippable.asset_id", "transform",
                            "Layer textures need an assets/<ns>/equipment/<id>.json equipment asset.")
        if armor.get("color"):
            node.components["dyed_color"] = str(armor["color"])
            self.ledger.add(full_id, "item", "specific_properties.armor.color", "data.dyed_color", "direct")
        if equippable:
            node.equip = equippable

    # --- behaviours -----------------------------------------------------------
    def _apply_book(self, node: ItemNode, full_id: str, cfg: dict[str, Any],
                  origin: str = "book") -> None:
        """Move static ItemsAdder book pages into ``data.written_book_content``.

        Only plain text survives: ItemsAdder placeholders and interactive pages
        have no CraftEngine counterpart, so anything else is reported rather
        than emitted as a broken page.
        """
        pages_out: list[str] = []
        skipped = 0
        for page in cfg.get("pages") or []:
            if isinstance(page, str):
                pages_out.append(page)
            elif isinstance(page, dict) and isinstance(page.get("text"), str) and not set(page) - {"text"}:
                pages_out.append(page["text"])
            else:
                # Interactive/placeholder pages cannot be static text; emitting
                # them would contradict the ledger row below.
                skipped += 1
        if not pages_out:
            self.ledger.add(full_id, "item", origin, "-", "unsupported",
                            "No plain-text pages found; CraftEngine books need static text.")
            return
        content: dict[str, Any] = {"pages": pages_out}
        for src, dst in (("title", "title"), ("author", "author")):
            if isinstance(cfg.get(src), str):
                content[dst] = cfg[src]
        node.components["written_book_content"] = content
        note = "Static pages converted."
        if skipped:
            note += f" {skipped} page(s) with placeholders or interactive content were skipped."
        self.ledger.add(full_id, "item", origin, "data.written_book_content",
                        "partial" if skipped else "transform", note)

    def _apply_behaviours(
        self,
        result: AnalysisResult,
        node: ItemNode,
        full_id: str,
        ns: str,
        raw: dict[str, Any],
        behaviours: dict[str, Any],
        specific: dict[str, Any],
        armors: dict[str, dict[str, Any]],
    ) -> None:
        block_cfg = behaviours.get("block") or specific.get("block")
        if isinstance(block_cfg, dict):
            self._build_block(result, node, full_id, ns, raw, block_cfg)

        furniture_cfg = behaviours.get("furniture") or specific.get("furniture")
        if isinstance(furniture_cfg, dict):
            if getattr(self.settings, "ia_generate_furniture", True):
                self._build_furniture(result, node, full_id, ns, raw, furniture_cfg)
            else:
                # Opted out, so say so in the ledger instead of dropping it.
                self.ledger.add(full_id, "furniture", "behaviours.furniture", "-", "unsupported",
                                "ia_generate_furniture is disabled; the item is converted as a plain item.")

        if behaviours.get("compostable") is not None or specific.get("compostable") is not None:
            chance = behaviours.get("compostable") or specific.get("compostable")
            behavior = {"type": "compostable_item"}
            if isinstance(chance, dict) and chance.get("chance") is not None:
                behavior["chance"] = float(chance["chance"])
            elif isinstance(chance, (int, float)):
                behavior["chance"] = float(chance)
            node.behavior = behavior
            self.ledger.add(full_id, "item", "behaviours.compostable", "behavior.compostable_item", "direct")

        if behaviours.get("hat") is True or specific.get("hat") is True:
            node.equip = {**(node.equip or {}), "slot": "head"}
            self.ledger.add(full_id, "item", "behaviours.hat", "data.equippable.slot: head", "transform")

        if behaviours.get("fire_resistant") is True or specific.get("fire_resistant") is True:
            node.settings["invulnerable"] = list(_FIRE_DAMAGE_SOURCES)
            self.ledger.add(full_id, "item", "behaviours.fire_resistant", "settings.invulnerable", "transform")

        if behaviours.get("music_disc") or specific.get("music_disc"):
            disc = behaviours.get("music_disc") or specific.get("music_disc")
            song = disc.get("song") if isinstance(disc, dict) else disc
            if song:
                node.components["jukebox_playable"] = str(song)
                self.ledger.add(full_id, "item", "behaviours.music_disc", "data.jukebox_playable", "transform")

        book_cfg = behaviours.get("book") or specific.get("book")
        if isinstance(book_cfg, dict):
            origin = "behaviours.book" if "book" in behaviours else "specific_properties.book"
            self._apply_book(node, full_id, book_cfg, origin)

        for key in behaviours:
            if key in ("block", "furniture", "compostable", "hat", "fire_resistant", "music_disc",
                       "book"):
                continue
            spec = self.behaviour_map.get(str(key), {})
            self.ledger.add(
                full_id, "item", f"behaviours.{key}",
                spec.get("target") or "-",
                spec.get("support", "unsupported"),
                spec.get("note", "No CraftEngine equivalent registered in mappings/itemsadder/behaviours.json."),
            )
        for key in specific:
            if key in ("block", "armor", "furniture", "book"):
                continue
            spec = self.specific_map.get(str(key), {})
            self.ledger.add(
                full_id, "item", f"specific_properties.{key}",
                spec.get("target") or "-",
                spec.get("support", "unsupported"),
                spec.get("note", "No CraftEngine equivalent registered in mappings/itemsadder/behaviours.json."),
            )

    # --- blocks ---------------------------------------------------------------
    def _build_block(
        self,
        result: AnalysisResult,
        node: ItemNode,
        full_id: str,
        ns: str,
        raw: dict[str, Any],
        cfg: dict[str, Any],
    ) -> None:
        """Turn an ItemsAdder ``behaviours.block`` item into a CraftEngine block.

        ItemsAdder has no separate block registry: a block *is* an item with a
        block behaviour. CraftEngine keeps them apart, so this creates a
        BlockNode with the same id and binds the item with ``block_item``.
        """
        block = BlockNode(
            id=full_id,
            namespace=ns,
            kind="itemsadder_block",
            confidence=1.0,
            status=status.DETECTED,
            source={"itemsadder": str(raw.get("__source_file__", ""))},
        )
        block.display_name = node.display_name
        block.item_binding = full_id
        node.behavior = {"type": "block_item", "block": full_id}
        self.ledger.add(full_id, "block", "behaviours.block", "block + behavior.block_item", "direct")

        placed_model = cfg.get("placed_model") if isinstance(cfg.get("placed_model"), dict) else {}
        ia_type = str(placed_model.get("type", cfg.get("type", "REAL_NOTE"))).upper()
        spec = self.block_types.get(ia_type, self.block_types.get("REAL_NOTE", {}))
        auto_state = spec.get("auto_state")
        if auto_state:
            block.auto_state = auto_state
            block.transparent = bool(spec.get("transparent"))
        else:
            block.auto_state = self.settings.block_auto_state
        self.ledger.add(full_id, "block", f"placed_model.type: {ia_type}",
                        f"state.auto_state: {auto_state or self.settings.block_auto_state}",
                        spec.get("support", "partial"), spec.get("note", ""))

        for key in ("rotx", "roty", "shift_up", "custom_variants", "placeable_on_water",
                    "placeable_on_lava", "placeable_on_other_real_wire"):
            if placed_model.get(key) not in (None, False, 0):
                self.ledger.add(full_id, "block", f"placed_model.{key}", "-", "partial",
                                "Static model rotation / placement surface needs manual review in CraftEngine.")

        # --- block settings -------------------------------------------------
        settings: dict[str, Any] = {}
        if cfg.get("hardness") is not None:
            settings["hardness"] = float(cfg["hardness"])
            self.ledger.add(full_id, "block", "hardness", "settings.hardness", "direct")
        if cfg.get("blast_resistance") is not None:
            settings["resistance"] = float(cfg["blast_resistance"])
            self.ledger.add(full_id, "block", "blast_resistance", "settings.resistance", "direct")
        if cfg.get("no_explosion") is True:
            settings["resistance"] = float(getattr(self.settings, "ia_explosion_immune_resistance", 3600000))
            self.ledger.add(full_id, "block", "no_explosion", "settings.resistance", "transform",
                            "CraftEngine has no explosion-immune flag; the vanilla bedrock-grade resistance is used.")
        if cfg.get("light_level") is not None:
            settings["luminance"] = _as_int(cfg["light_level"], 0)
            self.ledger.add(full_id, "block", "light_level", "settings.luminance", "direct")
        if cfg.get("friction") is not None:
            settings["friction"] = float(cfg["friction"])
            self.ledger.add(full_id, "block", "friction", "settings.friction", "direct")
        if cfg.get("speed_factor") is not None:
            settings["speed_factor"] = float(cfg["speed_factor"])
        if cfg.get("jump_factor") is not None:
            settings["jump_factor"] = float(cfg["jump_factor"])

        whitelist = cfg.get("break_tools_whitelist")
        if isinstance(whitelist, list) and whitelist:
            tools = sorted({t for entry in whitelist for t in _expand_tool(entry)})
            settings["require_correct_tools"] = True
            settings["correct_tools"] = tools
            block.tool_tier = _tier_from_tools(tools)
            self.ledger.add(full_id, "block", "break_tools_whitelist", "settings.correct_tools", "transform",
                            "ItemsAdder matches substrings; CraftEngine needs concrete item ids.")
        if isinstance(cfg.get("break_tools_blacklist"), list) and cfg.get("break_tools_blacklist"):
            self.ledger.add(full_id, "block", "break_tools_blacklist", "-", "unsupported",
                            "CraftEngine has no tool blacklist.")

        sounds = cfg.get("sound") if isinstance(cfg.get("sound"), dict) else {}
        ce_sounds: dict[str, Any] = {}
        for kind in ("break", "step", "place", "hit", "fall"):
            entry = sounds.get(kind)
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            value: Any = _sound_id(str(entry["name"]), ns)
            if entry.get("pitch") is not None or entry.get("volume") is not None:
                value = {"id": _sound_id(str(entry["name"]), ns)}
                if entry.get("pitch") is not None:
                    value["pitch"] = float(entry["pitch"])
                if entry.get("volume") is not None:
                    value["volume"] = float(entry["volume"])
            ce_sounds[kind] = value
        if ce_sounds:
            settings["sounds"] = ce_sounds
            self.ledger.add(full_id, "block", "sound", "settings.sounds", "direct")

        for key in ("drop_when_mined", "drop_on_shears", "drop_on_silk_touch", "events_tools_blacklist",
                    "events_tools_whitelist"):
            if cfg.get(key) is not None:
                self.ledger.add(full_id, "block", key, "-", "partial",
                                "Drop/tool-event gating is loot- and event-driven in CraftEngine; see manual-tasks.")

        for key in cfg:
            if key in _KNOWN_BLOCK_KEYS:
                continue
            self.ledger.add(full_id, "block", f"behaviours.block.{key}", "-", "unsupported",
                            "Not an ItemsAdder block property; no CraftEngine equivalent.")

        if settings:
            block.settings = settings

        # --- visuals --------------------------------------------------------
        resource = raw.get("resource") if isinstance(raw.get("resource"), dict) else {}
        model_path = cfg.get("model_path") or resource.get("model_path")
        ce_model = self._ce_model(model_path, ns) if model_path else None
        textures = cfg.get("textures") or resource.get("textures")
        block_textures: list[str] = []
        if isinstance(textures, dict):
            block_textures = [t for t in (self._ce_texture(v, ns) for v in textures.values()) if t]
            block.metadata["texture_slots"] = {k: self._ce_texture(v, ns) for k, v in textures.items()}
        elif isinstance(textures, list):
            block_textures = [t for t in (self._ce_texture(v, ns) for v in textures) if t]
        elif isinstance(textures, str):
            block_textures = [t for t in [self._ce_texture(textures, ns)] if t]

        if ce_model:
            block.model = ce_model
        elif block_textures:
            block.textures = block_textures
        else:
            path = full_id.split(":", 1)[1]
            block.textures = [f"{ns}:block/{path}"]
        self.ledger.add(full_id, "block", "resource", "state.model", "direct")

        block.status = status.ANALYZED
        result.blocks[full_id] = block

    # --- furniture ------------------------------------------------------------
    def _build_furniture(
        self,
        result: AnalysisResult,
        node: ItemNode,
        full_id: str,
        ns: str,
        raw: dict[str, Any],
        cfg: dict[str, Any],
    ) -> None:
        """Translate an ItemsAdder furniture into a CraftEngine furniture.

        CraftEngine furniture is display-entity based like ItemsAdder's
        ``item_display`` furniture, so the transform, hitbox and seat data
        carries over. Variants are named after the placement surface, which is
        what the default ``furniture_item`` behavior expects.
        """
        furniture = FurnitureNode(
            id=full_id,
            namespace=ns,
            kind="itemsadder_furniture",
            confidence=1.0,
            status=status.DETECTED,
            source={"itemsadder": str(raw.get("__source_file__", ""))},
        )
        furniture.item = full_id
        furniture.display_name = node.display_name

        element: dict[str, Any] = {"type": "item_display", "item": full_id}
        transform = cfg.get("display_transformation")
        if isinstance(transform, dict):
            if transform.get("transform"):
                element["display_transform"] = str(transform["transform"]).lower()
            translation = transform.get("translation")
            if isinstance(translation, dict):
                element["translation"] = _vec3(translation)
            scale = transform.get("scale")
            if isinstance(scale, dict):
                element["scale"] = _vec3(scale)
            elif isinstance(scale, (int, float)):
                element["scale"] = scale
            rotation = transform.get("right_rotation")
            angle = None
            if isinstance(rotation, dict):
                axis_angle = rotation.get("axis_angle")
                if isinstance(axis_angle, dict):
                    angle = axis_angle.get("angle")
            if angle is not None:
                element["rotation"] = float(angle)
            self.ledger.add(full_id, "furniture", "display_transformation", "variants.*.elements", "transform")
        else:
            self.ledger.add(full_id, "furniture", "entity: " + str(cfg.get("entity", "armor_stand")),
                            "variants.ground.elements", "transform",
                            "ItemsAdder armor_stand/item_frame furniture becomes a CraftEngine item_display element.")

        hitbox_src = cfg.get("hitbox") if isinstance(cfg.get("hitbox"), dict) else {}
        hitbox: dict[str, Any] = {
            "type": "interaction",
            "width": float(hitbox_src.get("width", 1)),
            "height": float(hitbox_src.get("height", 1)),
            "blocks_building": bool(cfg.get("solid", False)),
            "interactive": True,
        }
        if cfg.get("solid") is True:
            hitbox["interaction_entity"] = True
        self.ledger.add(full_id, "furniture", "hitbox", "variants.*.hitboxes", "transform")

        variants: dict[str, Any] = {}
        placeable = cfg.get("placeable_on") if isinstance(cfg.get("placeable_on"), dict) else {}
        surfaces = [name for name, key in (("ground", "floor"), ("ceiling", "ceiling"), ("wall", "walls"))
                    if placeable.get(key, name == "ground")]
        if not surfaces:
            surfaces = ["ground"]
        for surface in surfaces:
            variant: dict[str, Any] = {"elements": [dict(element)], "hitboxes": [dict(hitbox)]}
            # Wall/ceiling display entities render black at position 0,0,0.
            if surface == "wall":
                variant["elements"][0]["position"] = "0,0,0.5"
                variant["elements"][0]["translation"] = "0,0,-0.5"
            variants[surface] = variant
        furniture.variants = variants

        settings: dict[str, Any] = {"item": full_id}
        if cfg.get("light_level"):
            furniture.light_level = _as_int(cfg["light_level"], 0)
            self.ledger.add(full_id, "furniture", "light_level", "behaviors.glowing_furniture", "transform")
        sound = cfg.get("sound") if isinstance(cfg.get("sound"), dict) else {}
        ce_sounds = {k: _sound_id(str(sound[k]["name"]), ns) for k in ("break", "place", "hit")
                     if isinstance(sound.get(k), dict) and sound[k].get("name")}
        if ce_sounds:
            settings["sounds"] = ce_sounds
        furniture.settings = settings

        for key in ("small", "fixed_rotation", "auto_update_in_world", "render_size", "sit"):
            if cfg.get(key) is not None:
                furniture.unmapped[key] = cfg[key]
                self.ledger.add(full_id, "furniture", key, "-", "partial",
                                "No direct CraftEngine furniture field; review manually.")

        furniture.status = status.ANALYZED
        result.furniture[full_id] = furniture
        node.behavior = {"type": "furniture_item", "furniture": full_id}
        self.ledger.add(full_id, "furniture", "behaviours.furniture", "furniture + behavior.furniture_item", "direct")

    # --- drop -> loot -----------------------------------------------------------
    def _link_loot(self, result: AnalysisResult) -> None:
        """Convert ItemsAdder ``drop.break_block.loots`` into CraftEngine loot.

        The table is emitted in vanilla loot-table shape so the existing
        LootGenerator normalizes it exactly like a mod's block loot.
        """
        for item_id, node in list(result.items.items()):
            raw = self._raw_for(item_id)
            if not raw:
                continue
            drop = raw.get("drop")
            if not isinstance(drop, dict):
                continue
            break_block = drop.get("break_block")
            if not isinstance(break_block, dict):
                continue
            loots = break_block.get("loots")
            if not isinstance(loots, list) or not loots:
                continue
            block = result.blocks.get(item_id)
            if block is None:
                continue
            pools: list[dict[str, Any]] = []
            for entry in loots:
                if not isinstance(entry, dict) or not entry.get("item"):
                    continue
                name = str(entry["item"])
                item_ref = name if ":" in name else f"{item_id.split(':', 1)[0]}:{name}"
                minimum = _as_int(entry.get("min_amount", 1), 1)
                maximum = _as_int(entry.get("max_amount", minimum), minimum)
                chance = entry.get("chance")
                functions: list[dict[str, Any]] = []
                if minimum != 1 or maximum != 1:
                    functions.append({
                        "function": "minecraft:set_count",
                        "count": {"min": minimum, "max": maximum},
                    })
                pool: dict[str, Any] = {
                    "rolls": 1,
                    "entries": [{"type": "minecraft:item", "name": item_ref, "functions": functions}],
                }
                if chance is not None:
                    pool["conditions"] = [{"condition": "minecraft:random_chance", "chance": float(chance) / 100.0}]
                pools.append(pool)
            if not pools:
                continue
            loot_id = f"{item_id}_drop"
            result.loot[loot_id] = LootNode(
                id=loot_id,
                namespace=item_id.split(":", 1)[0],
                kind="itemsadder_drop",
                confidence=1.0,
                source={"itemsadder": str(raw.get("__source_file__", ""))},
                raw={"type": "minecraft:block", "pools": pools},
            )
            block.loot = loot_id
            self.ledger.add(item_id, "loot", "drop.break_block.loots", "loot", "transform",
                            f"{len(pools)} pool(s); ItemsAdder chance 0-100 became a 0-1 probability.")

    def _raw_for(self, full_id: str) -> dict[str, Any] | None:
        ns, _, path = full_id.partition(":")
        for name, data in self._documents.get(ns, []):
            items = data.get("items")
            if isinstance(items, dict) and path in items and isinstance(items[path], dict):
                return {"__source_file__": name, **items[path]}
        return None

    # --- block item linkage ------------------------------------------------------
    def _link_block_items(self, result: AnalysisResult) -> None:
        """Ensure every generated block has the item that places it."""
        for block_id, block in result.blocks.items():
            item = result.items.get(block_id)
            if item is not None and not item.behavior:
                item.behavior = {"type": "block_item", "block": block_id}
            if item is not None:
                item.metadata["block_bound"] = True

    # --- datapack recipes ----------------------------------------------------------
    def _collect_datapack_recipes(self, result: AnalysisResult) -> None:
        """Pick up vanilla recipe JSON shipped inside the ItemsAdder pack.

        ItemsAdder has no recipe format of its own - packs add recipes as
        ordinary datapack JSON. Those reuse the existing recipe pipeline.
        """
        for name in self._archive.names:
            parts = name.split("/")
            if "data" not in parts:
                continue
            idx = parts.index("data")
            if len(parts) < idx + 4:
                continue
            if parts[idx + 2] not in ("recipes", "recipe"):
                continue
            if not name.endswith(".json"):
                continue
            data = self._archive.read_json(name)
            if not isinstance(data, dict):
                continue
            ns = parts[idx + 1]
            rel = "/".join(parts[idx + 3:])
            recipe_id = f"{ns}:{rel[:-5] if rel.endswith('.json') else Path(name).stem}"
            node = self._recipe_parser._recipe_from_json(recipe_id, data, name)
            node.kind = "itemsadder_recipe"
            result.recipes[node.id] = node
            self.ledger.add(node.id, "recipe", "data/recipes/*.json", "recipes", "direct")
        if result.recipes:
            self.log.info("ItemsAdder datapack recipes collected", count=len(result.recipes))

    # --- reporting ---------------------------------------------------------------
    def _touch(self, key: str) -> None:
        """Mark a top-level source key as actually consumed.

        The ledger's promise is that no source key disappears without a trace.
        A static "handled keys" list cannot keep that promise: a key can be
        listed as handled while the branch that reads it never fires (a string
        where a mapping was expected, an unsupported value, ...). Tracking real
        consumption makes silent drops impossible by construction.
        """
        self._consumed.add(key)

    def _report_unhandled(self, node: ItemNode, full_id: str, raw: dict[str, Any]) -> None:
        # Keys consumed structurally rather than by an _apply_* helper.
        structural = {
            "__source_file__", "enabled", "variant_of", "template",
            "behaviours", "behaviors", "specific_properties",
        }
        for key in raw:
            if key in structural or key in self._consumed:
                continue
            target = self.item_key_map.get(str(key), "REPORT")
            support = "unsupported" if target == "REPORT" else "partial"
            self.ledger.add(
                full_id, "item", key, "-" if target == "REPORT" else target, support,
                "No CraftEngine equivalent; kept in source-map only." if target == "REPORT"
                else "Present in the source but not fully representable; review the generated config.",
            )


# --- helpers ----------------------------------------------------------------

def _norm_key(name: str) -> str:
    """Collapse camelCase / SCREAMING_SNAKE / snake_case onto one lookup key."""
    return name.replace("_", "").replace("-", "").lower()


class _SnbtError(ValueError):
    pass


def _parse_snbt(text: str) -> dict[str, Any] | None:
    """Parse the SNBT subset ItemsAdder uses in ``nbt:`` strings.

    Deliberately conservative: anything outside compounds, lists, quoted
    strings, numbers and booleans raises, and the caller then reports the key
    instead of emitting a half-parsed guess. Returning ``None`` is the signal
    for "needs a human".
    """
    parser = _SnbtParser(text)
    try:
        value = parser.parse_compound()
        parser.skip_ws()
        if not parser.eof():
            return None
        return value if isinstance(value, dict) else None
    except _SnbtError:
        return None


class _SnbtParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def eof(self) -> bool:
        return self.pos >= len(self.text)

    def skip_ws(self) -> None:
        while not self.eof() and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def expect(self, char: str) -> None:
        self.skip_ws()
        if self.eof() or self.text[self.pos] != char:
            raise _SnbtError(f"expected {char!r} at {self.pos}")
        self.pos += 1

    def parse_compound(self) -> dict[str, Any]:
        self.expect("{")
        out: dict[str, Any] = {}
        self.skip_ws()
        if not self.eof() and self.text[self.pos] == "}":
            self.pos += 1
            return out
        while True:
            key = self.parse_key()
            self.expect(":")
            out[key] = self.parse_value()
            self.skip_ws()
            if self.eof():
                raise _SnbtError("unterminated compound")
            char = self.text[self.pos]
            if char == ",":
                self.pos += 1
                self.skip_ws()
                # A trailing comma before } is tolerated.
                if not self.eof() and self.text[self.pos] == "}":
                    self.pos += 1
                    return out
                continue
            if char == "}":
                self.pos += 1
                return out
            raise _SnbtError(f"unexpected {char!r} at {self.pos}")

    def parse_key(self) -> str:
        self.skip_ws()
        if self.eof():
            raise _SnbtError("missing key")
        if self.text[self.pos] in "\"'":
            return str(self.parse_quoted())
        start = self.pos
        while not self.eof() and (self.text[self.pos].isalnum() or self.text[self.pos] in "._-+/"):
            self.pos += 1
        if start == self.pos:
            raise _SnbtError(f"invalid key at {self.pos}")
        return self.text[start:self.pos]

    def parse_value(self) -> Any:
        self.skip_ws()
        if self.eof():
            raise _SnbtError("missing value")
        char = self.text[self.pos]
        if char == "{":
            return self.parse_compound()
        if char == "[":
            return self.parse_list()
        if char in "\"'":
            return self.parse_quoted()
        return self.parse_primitive()

    def parse_list(self) -> list[Any]:
        self.expect("[")
        items: list[Any] = []
        self.skip_ws()
        if not self.eof() and self.text[self.pos] == "]":
            self.pos += 1
            return items
        while True:
            items.append(self.parse_value())
            self.skip_ws()
            if self.eof():
                raise _SnbtError("unterminated list")
            char = self.text[self.pos]
            if char == ",":
                self.pos += 1
                continue
            if char == "]":
                self.pos += 1
                return items
            raise _SnbtError(f"unexpected {char!r} at {self.pos}")

    def parse_quoted(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        out: list[str] = []
        while not self.eof():
            char = self.text[self.pos]
            if char == "\\" and self.pos + 1 < len(self.text):
                nxt = self.text[self.pos + 1]
                out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}.get(nxt, nxt))
                self.pos += 2
                continue
            if char == quote:
                self.pos += 1
                return "".join(out)
            out.append(char)
            self.pos += 1
        raise _SnbtError("unterminated string")

    def parse_primitive(self) -> Any:
        start = self.pos
        while not self.eof() and self.text[self.pos] not in ",}]":
            self.pos += 1
        token = self.text[start:self.pos].strip()
        if not token:
            raise _SnbtError("empty value")
        lowered = token.lower()
        if lowered in ("true", "false"):
            return lowered == "true"
        # Numeric type suffixes (1b, 2s, 3l, 4f, 5d) are stripped: the value is
        # what matters for a YAML data component.
        body = token[:-1] if len(token) > 1 and token[-1].lower() in "bslfd" else token
        try:
            if any(c in body for c in ".eE"):
                return float(body)
            return int(body)
        except ValueError:
            raise _SnbtError(f"cannot parse {token!r}") from None


def _mc_locale(code: str) -> str:
    """Map an ItemsAdder locale code to a Minecraft lang filename."""
    code = code.strip().lower()
    if "_" in code or "-" in code:
        return code.replace("-", "_")
    return {"en": "en_us", "zh": "zh_cn", "pt": "pt_br", "es": "es_es"}.get(code, f"{code}_{code}")


def _material_id(material: str) -> str:
    """Bukkit material enum -> CraftEngine material id."""
    text = material.strip()
    if ":" in text:
        return text.lower()
    return f"minecraft:{text.lower()}"


def _enchant_id(name: str) -> str:
    text = name.strip()
    if ":" in text and not text.startswith("#"):
        # plugin:enchant or minecraft:enchant - keep as-is
        return text.lower()
    return f"minecraft:{text.lower()}"


def _sound_id(name: str, ns: str) -> str:
    text = name.strip()
    if ":" in text:
        return text.lower()
    # Bukkit enum (BLOCK_WOOD_BREAK) and vanilla id (block.wood.break) both appear.
    normalized = text.lower().replace("_", ".")
    if normalized.startswith("minecraft."):
        normalized = normalized[len("minecraft."):]
    return f"minecraft:{normalized}"


def _is_handheld(material: str | None) -> bool:
    if not material:
        return False
    path = material.split(":", 1)[-1]
    return any(path.endswith(suffix) for suffix in _HANDHELD_SUFFIXES)


def _expand_tool(entry: Any) -> list[str]:
    """Expand an ItemsAdder substring tool rule into concrete vanilla item ids.

    ItemsAdder matches ``PICKAXE`` against every material containing that word;
    CraftEngine's ``correct_tools`` needs explicit ids, so the six vanilla tiers
    are materialized.
    """
    text = str(entry).strip().lower()
    if ":" in text:
        return [text]
    kinds = ("wooden", "stone", "iron", "golden", "diamond", "netherite")
    suffix = text.removeprefix("_")
    if suffix in ("pickaxe", "axe", "shovel", "hoe", "sword"):
        return [f"minecraft:{kind}_{suffix}" for kind in kinds]
    return [f"minecraft:{text}"]


_TOOL_TIERS = ("netherite", "diamond", "iron", "stone", "golden", "wooden")


def _tier_from_tools(tools: list[str]) -> str | None:
    for tier in _TOOL_TIERS:
        if any(t.startswith(f"minecraft:{tier}_") for t in tools):
            return tier
    return None


def _flatten_attributes(entries: Any) -> list[tuple[str, tuple[float, str]]]:
    """Normalize both ItemsAdder attribute spellings.

    Mapping form: ``{attackDamage: 19}`` (operation defaults to ADD_NUMBER).
    List form: ``[{type: ATTACK_DAMAGE, amount: 7, operation: ADD_NUMBER}]``.
    """
    out: list[tuple[str, tuple[float, str]]] = []
    if isinstance(entries, dict):
        for attr, value in entries.items():
            if isinstance(value, dict):
                out.append((str(attr), (float(value.get("amount", 0) or 0),
                                        str(value.get("operation", "ADD_NUMBER")))))
            else:
                out.append((str(attr), (float(value or 0), "ADD_NUMBER")))
    elif isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("type") or entry.get("attribute") or entry.get("name")
            if not name:
                continue
            out.append((str(name), (float(entry.get("amount", 0) or 0),
                                    str(entry.get("operation", "ADD_NUMBER")))))
    return out


def _vec3(mapping: dict[str, Any]) -> str:
    return f"{float(mapping.get('x', 0))},{float(mapping.get('y', 0))},{float(mapping.get('z', 0))}"


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` over ``base`` (ItemsAdder variant_of)."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# --- report -------------------------------------------------------------------

def conversion_report(analysis: AnalysisResult) -> str:
    """Render ``reports/itemsadder.md``: what moved, what needs a human."""
    rows: list[LedgerRow] = []
    for entry in analysis.conversion_ledger:
        rows.append(LedgerRow(
            object_id=entry.get("object", ""),
            domain=entry.get("domain", ""),
            source_key=entry.get("source_key", ""),
            target=entry.get("target", ""),
            support=entry.get("support", ""),
            note=entry.get("note", ""),
        ))

    lines = [
        "# ItemsAdder -> CraftEngine conversion ledger",
        "",
        f"Source kind: `{analysis.source_kind}`",
        f"Namespaces: {', '.join(analysis.content_namespaces) or '-'}",
        f"Items: {len(analysis.items)} | Blocks: {len(analysis.blocks)} | "
        f"Furniture: {len(analysis.furniture)} | Recipes: {len(analysis.recipes)} | "
        f"Loot tables: {len(analysis.loot)}",
        "",
    ]

    summary: dict[str, int] = {}
    for row in rows:
        summary[row.support] = summary.get(row.support, 0) + 1
    lines.append("## Summary")
    lines.append("")
    lines.append("| Support | Keys |")
    lines.append("| --- | --- |")
    for support in ("direct", "transform", "partial", "unsupported"):
        lines.append(f"| {support} | {summary.get(support, 0)} |")
    lines.append("")

    needs_review = [r for r in rows if r.support in ("partial", "unsupported")]
    lines.append("## Needs review")
    lines.append("")
    if not needs_review:
        lines.append("Every source key had a CraftEngine equivalent.")
    else:
        lines.append("| Object | Source key | Support | Note |")
        lines.append("| --- | --- | --- | --- |")
        seen: set[tuple[str, str, str]] = set()
        for row in sorted(needs_review, key=lambda r: (r.object_id, r.source_key)):
            key = (row.object_id, row.source_key, row.support)
            if key in seen:
                continue
            seen.add(key)
            note = row.note.replace("|", "\\|")
            lines.append(f"| `{row.object_id}` | `{row.source_key}` | {row.support} | {note} |")
    lines.append("")

    lines.append("## Full ledger")
    lines.append("")
    lines.append("| Object | Source key | CraftEngine target | Support |")
    lines.append("| --- | --- | --- | --- |")
    for row in sorted(rows, key=lambda r: (r.domain, r.object_id, r.source_key)):
        lines.append(f"| `{row.object_id}` | `{row.source_key}` | `{row.target}` | {row.support} |")
    lines.append("")
    return "\n".join(lines)
