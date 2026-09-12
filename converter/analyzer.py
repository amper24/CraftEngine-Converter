"""Semantic analyzer: extract items, blocks, blockstates, recipes, loot, tags
and lang from a mod archive into an Intermediate Representation (IR).

Every detected object is preserved; nothing is silently dropped.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import assetindex, paths, status, util
from .archive import ModArchive
from .config import Settings
from .detector import ModMetadata, ModDetector
from .bytecode import extract_foods_from_archive, FoodValue, extract_crop_relations_from_archive
from .modeldefs import translate_item_model_definition
from .bytecode_semantics import extract_semantics_from_archive
from .ir import BlockNode, BlockStateNode, FurnitureNode, ItemNode, LootNode, RecipeNode, ResourceNode
from .util import Log


@dataclass
class AnalysisResult:
    metadata: ModMetadata
    minecraft_version: str
    items: dict[str, ItemNode] = field(default_factory=dict)
    blocks: dict[str, BlockNode] = field(default_factory=dict)
    recipes: dict[str, RecipeNode] = field(default_factory=dict)
    loot: dict[str, LootNode] = field(default_factory=dict)
    resources: list[ResourceNode] = field(default_factory=list)
    tags: dict[str, dict[str, Any]] = field(default_factory=dict)
    lang: dict[str, str] = field(default_factory=dict)
    lang_by_locale: dict[str, dict[str, str]] = field(default_factory=dict)
    sounds: dict[str, Any] = field(default_factory=dict)
    bytecode_foods: dict[str, dict[str, Any]] = field(default_factory=dict)
    bytecode_food_unmatched: list[str] = field(default_factory=list)
    bytecode_semantics: dict[str, dict[str, Any]] = field(default_factory=dict)
    bytecode_crops: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Every asset/data namespace that contains real mod content. Many mods ship
    # items/models/textures under a namespace that differs from the metadata mod
    # id (or under several compatibility namespaces), so content scanning must
    # not be limited to the primary namespace.
    content_namespaces: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Filled by converter.semantics after analysis (neural verdicts per object).
    semantics: Any = None

    # --- source-adapter extensions ---------------------------------------
    # Which import adapter produced this IR: "mod" (Forge/Fabric/NeoForge jar)
    # or "itemsadder" (ItemsAdder contents/ pack).
    source_kind: str = "mod"
    # Entity-based decorations. Empty for mod sources.
    furniture: dict[str, "FurnitureNode"] = field(default_factory=dict)
    # Explicit source categories (namespace -> category id -> definition).
    # When present, category generation uses these instead of inferring groups.
    source_categories: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Archive path -> path relative to the output resourcepack root. Mod sources
    # copy ``assets/**`` verbatim and leave this empty; ItemsAdder packs keep
    # their resources under ``contents/<ns>/...`` and need a rewrite.
    resource_map: dict[str, str] = field(default_factory=dict)
    # Row-per-key conversion ledger written to reports/<source>.md.
    conversion_ledger: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "minecraft_version": self.minecraft_version,
            "source_kind": self.source_kind,
            "items": {k: v.to_dict() for k, v in sorted(self.items.items())},
            "blocks": {k: v.to_dict() for k, v in sorted(self.blocks.items())},
            "furniture": {k: v.to_dict() for k, v in sorted(self.furniture.items())},
            "recipes": {k: v.to_dict() for k, v in sorted(self.recipes.items())},
            "loot": {k: v.to_dict() for k, v in sorted(self.loot.items())},
            "resources": [r.to_dict() for r in self.resources],
            "tags": self.tags,
            "lang": dict(sorted(self.lang.items())),
            "lang_by_locale": {k: dict(sorted(v.items())) for k, v in sorted(self.lang_by_locale.items())},
            "sounds": dict(sorted(self.sounds.items())),
            "bytecode_foods": dict(sorted(self.bytecode_foods.items())),
            "bytecode_food_unmatched": self.bytecode_food_unmatched,
            "bytecode_semantics": dict(sorted(self.bytecode_semantics.items())),
            "bytecode_crops": dict(sorted(self.bytecode_crops.items())),
            "content_namespaces": list(self.content_namespaces),
            "source_categories": self.source_categories,
            "resource_map": dict(sorted(self.resource_map.items())),
            "conversion_ledger": self.conversion_ledger,
            "warnings": self.warnings,
        }


class Analyzer:
    def __init__(
        self,
        log: Log,
        minecraft_version: str = "1.21.4",
        settings: Settings | None = None,
    ) -> None:
        self.log = log
        self.minecraft_version = minecraft_version
        self.settings = settings or Settings()
        self.property_types = self._load_property_types()
        self.auto_state_map = self._load_auto_state_map()
        self.vanilla_materials = self._load_vanilla_materials()

    # --- mapping loaders --------------------------------------------------
    def _load_property_types(self) -> dict[str, str]:
        data = util.read_json(paths.mapping_file("property_name_types.json"))
        return data.get("types", {})

    def _load_auto_state_map(self) -> dict[str, str]:
        data = util.read_json(paths.mapping_file("block_class_to_auto_state.json"))
        return data.get("mappings", {})

    def _load_vanilla_materials(self) -> dict[str, str]:
        data = util.read_json(paths.mapping_file("vanilla_item_materials.json"))
        return data.get("mappings", {})

    # --- public entry -----------------------------------------------------
    def analyze(self, archive: ModArchive) -> AnalysisResult:
        metadata = ModDetector(self.log).detect(archive)
        namespace = metadata.namespace
        self._archive = archive

        # Content may live under several namespaces: the mod id, a namespace
        # that differs from the metadata id, or compatibility namespaces that
        # ship recipes/assets in the same jar. Scanning only the primary
        # namespace silently drops those items/models/textures/recipes.
        namespaces = self._content_namespaces(archive, namespace)
        self._current_namespace = namespaces[0] if namespaces else namespace
        result = AnalysisResult(
            metadata=metadata,
            minecraft_version=self.minecraft_version,
            content_namespaces=list(namespaces),
        )

        resources = self._collect_resources(archive, set(namespaces))
        result.resources = resources

        lang, lang_by_locale = self._collect_lang(archive, set(namespaces))
        result.lang = lang
        result.lang_by_locale = lang_by_locale
        result.sounds = self._collect_sounds(archive, set(namespaces))

        for ns in namespaces:
            self._current_namespace = ns
            item_models, item_textures = self._index_item_sources(archive, ns)
            self._build_items(result, item_models, item_textures, lang, ns)
            self._build_modern_item_definitions(result, archive, ns, lang)
        # Apply bytecode-derived food values after item registration is known,
        # so the food fields can be matched to real item IDs.
        self._apply_bytecode_foods(result, archive, namespace)
        self._apply_bytecode_semantics(result, archive, namespace)
        for ns in namespaces:
            self._current_namespace = ns
            blockstates, block_models, block_textures = self._index_block_sources(archive, ns)
            self._build_blocks(result, blockstates, block_models, block_textures, lang, ns)
        self._build_recipes(result, archive, set(namespaces))
        self._build_loot(result, archive, set(namespaces))
        self._build_tags(result, archive, set(namespaces))
        self._link_crop_semantics(result, archive, namespace)
        self._ensure_crop_loot(result)
        self._create_items_from_references(result, archive, set(namespaces), lang)

        self._link_recipes_to_items(result)
        self._link_blocks_to_items(result)
        self._build_crop_display_items(result)
        self._build_entity_display_items(result)
        self._link_blocks_to_loot(result)
        self._link_block_tags(result)
        self._link_item_tags(result)
        # Asset references are intentionally the final linking phase, after
        # every item/block/display helper has had a chance to resolve its
        # model/texture paths (e.g. crop stages and entity-render helpers).
        self._populate_asset_refs(result, archive)

        return result

    def _apply_bytecode_foods(self, result: AnalysisResult, archive: ModArchive, namespace: str) -> None:
        """Extract exact food values from compiled classes and attach them to IR items.

        Java/NeoForge uses ``FoodProperties.Builder`` and Fabric uses
        ``FoodComponent.Builder``. The bytecode scanner records the source
        saturation modifier as evidence and stores the actual saturation restored
        by vanilla as ``2 * nutrition * saturation_modifier``.
        """
        if not self.settings.bytecode_food_enabled:
            return
        foods = extract_foods_from_archive(archive)
        if not foods:
            return
        result.bytecode_foods = {k: v.to_dict() for k, v in sorted(foods.items())}

        # First pass: exact field-name/path matches. Mod authors overwhelmingly
        # name FoodProperties fields after their item registry path.
        def canonical_food_name(value: str) -> str:
            value = value.lower().replace("-", "_")
            value = value.replace("_with_", "_and_")
            for suffix in ("_food", "food"):
                if value.endswith(suffix) and len(value) > len(suffix):
                    value = value[:-len(suffix)].rstrip("_")
            if value.startswith("food_"):
                value = value[5:]
            return value

        aliases: dict[str, set[str]] = {}
        for field_name in foods:
            base = field_name.lower()
            aliases[field_name] = {base, canonical_food_name(base)}

        unmatched: list[str] = []
        used_fields: set[str] = set()
        for item_id, item in result.items.items():
            path = item_id.split(":", 1)[1].lower()
            path_canonical = canonical_food_name(path)
            match = None
            for field_name, variants in aliases.items():
                if field_name in used_fields:
                    continue
                if path in variants or path_canonical in variants:
                    match = foods[field_name]
                    used_fields.add(field_name)
                    break
            if match is None:
                continue
            item.food = {
                "nutrition": match.nutrition,
                "saturation": round(match.saturation, 6),
                "can_always_eat": match.can_always_eat,
            }
            item.metadata["food_source"] = {
                "type": "bytecode",
                "field": match.field,
                "source_class": match.source_class,
                "source_method": match.source_method,
                "saturation_modifier": match.saturation_modifier,
            }

        # Food fields that could not be matched to an item are intentionally
        # retained in the report instead of being guessed onto a random item.
        matched_fields = {
            str(item.metadata.get("food_source", {}).get("field", "")).lower()
            for item in result.items.values()
            if item.metadata.get("food_source")
        }
        unmatched = sorted(set(foods) - matched_fields)
        if unmatched:
            result.warnings.append(f"bytecode food definitions without item match: {len(unmatched)}")
            result.bytecode_food_unmatched = unmatched

    def _apply_bytecode_semantics(self, result: AnalysisResult, archive: ModArchive, namespace: str) -> None:
        if not getattr(self.settings, "bytecode_semantic_enabled", True):
            return
        facts = extract_semantics_from_archive(archive)
        applied = {}
        used_facts: set[str] = set()
        for item in result.items.values():
            key = item.id.split(":",1)[1].lower()
            fact = facts.get(key)
            if not fact or key in used_facts:
                continue
            used_facts.add(key)
            if fact.max_damage is not None and item.durability is None:
                item.durability = fact.max_damage
            if fact.max_stack_size is not None and item.max_stack_size is None:
                item.max_stack_size = fact.max_stack_size
            if fact.unbreakable:
                item.components["unbreakable"] = True
            item.metadata["bytecode_semantics"] = fact.to_dict()
            applied[item.id] = fact.to_dict()
        result.warnings.append(f"bytecode semantic facts: {len(facts)}, matched items: {len(applied)}")
        result.bytecode_semantics = applied

    # --- namespaces -------------------------------------------------------
    def _content_namespaces(self, archive: ModArchive, primary: str) -> list[str]:
        """Return every content namespace that should be converted.

        Asset/search domains are authoritative for models/textures/blockstates
        (and therefore for the items and blocks we can faithfully render).
        Compat namespaces are also included when they ship recipes/loot/tags.
        """
        namespaces: set[str] = set()
        for name in archive.names:
            parts = name.split("/")
            if len(parts) >= 2 and name.startswith("assets/"):
                ns = parts[1]
                if ns and ns != "minecraft":
                    namespaces.add(ns)
            elif len(parts) >= 2 and name.startswith("data/"):
                ns = parts[1]
                if ns and ns != "minecraft":
                    namespaces.add(ns)
        if not namespaces:
            namespaces.add(primary if primary else "minecraft")
        namespaces.add(primary if primary else "minecraft")
        # Primary namespace first; deterministic order for everything else.
        ordered = [primary] if primary in namespaces else []
        ordered.extend(sorted(namespaces - {primary}, key=lambda x: (x != primary, x)))
        return ordered or [primary or "minecraft"]

    # --- resources --------------------------------------------------------
    def _collect_resources(self, archive: ModArchive, namespaces: set[str]) -> list[ResourceNode]:
        nodes: list[ResourceNode] = []
        for name in archive.names:
            kind = self._classify_path(name, namespaces)
            if kind is None:
                continue
            raw = archive.read(name)
            if raw is None:
                continue
            ns = name.split("/")[1] if len(name.split("/")) > 1 else (next(iter(namespaces), "minecraft"))
            node = ResourceNode(
                source_path=name,
                namespace=ns,
                kind=kind,
                raw_hash=util.sha256_bytes(raw),
                asset_domain=assetindex.asset_domain(name),
                is_resourcepack=assetindex.is_resourcepack(name),
            )
            if name.endswith(".json"):
                parsed = archive.read_json(name)
                if isinstance(parsed, dict):
                    node.parsed = parsed
            nodes.append(node)
        return nodes

    @staticmethod
    def _classify_path(name: str, namespaces: set[str]) -> str | None:
        """Classify resources needed for the CraftEngine resource pack.

        The resource pack is intentionally *verbatim*: every file under
        ``assets/`` is kept, including ``assets/minecraft`` overrides and any
        namespace not detected as primary mod content, because real mods
        frequently replace/extend vanilla models and textures. Datapack files
        (recipes/loot/tags) are only parsed into IR, not copied here.
        """
        parts = name.split("/")
        if len(parts) >= 3 and parts[0] == "assets":
            return "asset"
        if len(parts) >= 3 and parts[0] == "data" and parts[1] in namespaces:
            rest = "/".join(parts[2:])
            if rest.startswith("recipe/") or rest.startswith("recipes/"):
                return "recipes"
            if rest.startswith("loot_tables/"):
                return "loot_tables"
            if rest.startswith("tags/"):
                return "tags"
            return None
        return None

    # --- lang -------------------------------------------------------------
    def _collect_lang(
        self, archive: ModArchive, namespaces: set[str]
    ) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        """Collect per-locale translations and a merged map (en_us priority)."""
        by_locale: dict[str, dict[str, str]] = {}
        ordered_names = sorted(archive.names)
        # Prefer en_us first so merged English names are not overwritten.
        ordered_names.sort(key=lambda n: (0 if n.endswith("/en_us.json") else 1, n))

        for name in ordered_names:
            parts = name.split("/")
            if len(parts) < 4 or parts[0] != "assets" or parts[1] not in namespaces:
                continue
            if parts[2] != "lang" or not name.endswith(".json"):
                continue
            locale = name.rsplit("/", 1)[-1][:-5]
            data = archive.read_json(name)
            if isinstance(data, dict):
                entries = {str(k): v for k, v in data.items() if isinstance(v, str)}
                if locale not in by_locale:
                    by_locale[locale] = entries
                else:
                    by_locale[locale].update(entries)

        merged: dict[str, str] = {}
        for locale in by_locale:
            for k, v in by_locale[locale].items():
                if k not in merged:
                    merged[k] = v
        return merged, by_locale

    # --- item sources -----------------------------------------------------
    def _index_item_sources(
        self, archive: ModArchive, namespace: str
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        models: dict[str, dict[str, Any]] = {}
        textures: set[str] = set()
        prefix = f"assets/{namespace}/models/item/"
        for name in archive.names:
            if name.startswith(prefix) and name.endswith(".json"):
                rel = name[len(prefix):-5]
                parsed = archive.read_json(name)
                if isinstance(parsed, dict):
                    models[rel] = parsed
            elif name.startswith(f"assets/{namespace}/textures/item/") and name.endswith(".png"):
                rel = name[len(f"assets/{namespace}/textures/item/"):-4]
                textures.add(rel)
        return models, textures

    # --- block sources ----------------------------------------------------
    def _index_block_sources(
        self, archive: ModArchive, namespace: str
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
        blockstates: dict[str, dict[str, Any]] = {}
        models: dict[str, dict[str, Any]] = {}
        textures: set[str] = set()

        bs_prefix = f"assets/{namespace}/blockstates/"
        for name in archive.names:
            if name.startswith(bs_prefix) and name.endswith(".json"):
                parsed = archive.read_json(name)
                if isinstance(parsed, dict):
                    blockstates[name[len(bs_prefix):-5]] = parsed

        model_prefix = f"assets/{namespace}/models/block/"
        for name in archive.names:
            if name.startswith(model_prefix) and name.endswith(".json"):
                parsed = archive.read_json(name)
                if isinstance(parsed, dict):
                    models[name[len(model_prefix):-5]] = parsed

        tex_prefix = f"assets/{namespace}/textures/block/"
        for name in archive.names:
            if name.startswith(tex_prefix) and name.endswith(".png"):
                textures.add(name[len(tex_prefix):-4])

        return blockstates, models, textures

    def _collect_sounds(self, archive: ModArchive, namespaces: set[str]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for ns in sorted(namespaces):
            data = archive.read_json(f"assets/{ns}/sounds.json")
            if isinstance(data, dict):
                # Event keys are namespace-qualified in the resourcepack but the
                # IR/block-sound lookup uses the local event id first. Keep both
                # forms so multi-namespace mods resolve block sounds correctly.
                for key, value in data.items():
                    merged[str(key)] = value
                    merged[f"{ns}:{key}"] = value
        return merged

    # --- items ------------------------------------------------------------
    def _build_items(
        self,
        result: AnalysisResult,
        item_models: dict[str, dict[str, Any]],
        item_textures: set[str],
        lang: dict[str, str],
        namespace: str | None = None,
    ) -> None:
        ns = namespace or result.metadata.namespace
        ids = sorted(set(item_models.keys()))
        # A texture by itself is only a resource, never an item. Legacy item
        # packs may have no models folder; allow texture-only items only when a
        # matching translation key exists. Helper/overlay/stage textures are
        # excluded from item creation.
        for tex in sorted(item_textures):
            low = tex.lower()
            helper = any(tok in low for tok in ("_stage", "/stage", "_slice", "_overlay", "_top", "_bottom", "_side", "_particle", "_glint"))
            if helper:
                continue
            if f"item.{tex}" in lang or f"block.{tex}" in lang or f"item.{ns}.{tex}" in lang or f"block.{ns}.{tex}" in lang:
                ids.append(tex)
        ids = sorted(set(ids))

        for path_id in ids:
            full_id = f"{ns}:{path_id}"
            node = ItemNode(
                id=full_id,
                namespace=ns,
                kind="item",
                source={
                    "model": f"assets/{ns}/models/item/{path_id}.json" if path_id in item_models else None,
                    "texture": f"assets/{ns}/textures/item/{path_id}.png" if path_id in item_textures else None,
                },
                confidence=0.99 if path_id in item_models else 0.9,
                status=status.ANALYZED,
            )
            node.references = [r for r in node.source.values() if r]

            model_json = item_models.get(path_id)
            if model_json:
                parent = str(model_json.get("parent", ""))
                textures = self._model_textures(model_json, ns)
                node.textures = textures
                # Prefer CraftEngine's concise texture form for standard 2D
                # item/generated and item/handheld models. For block-backed
                # item models, point directly at the referenced block model so
                # the item and block share exactly the same source geometry.
                layer0 = model_json.get("textures", {}).get("layer0") if isinstance(model_json.get("textures"), dict) else None
                if parent in ("minecraft:item/generated", "minecraft:item/handheld") and isinstance(layer0, str):
                    node.model = None
                    node.textures = [self._normalize_resource_location(layer0, ns)]
                elif parent and ":" in parent and (":block/" in parent or parent.split(":", 1)[1].startswith("block/")) and len(model_json) <= 2:
                    node.model = self._normalize_resource_location(parent, ns)
                else:
                    node.model = f"{ns}:item/{path_id}"
            else:
                node.textures = [f"{ns}:item/{path_id}"]

            node.base_material = self._infer_material(full_id, model_json)
            node.display_name = self._lookup_name(lang, "item", path_id) or self._lookup_name(lang, "block", path_id)
            node.tool_tier = self._infer_tool_tier(full_id)
            node.gear_kind = self._infer_gear_kind(full_id, model_json)
            node.metadata["model_slots"] = self._find_gear_model_slots(path_id, item_models, node.gear_kind)
            self._infer_gear_stats(node)
            self._infer_gui_icon(node, path_id, model_json, item_textures, set(node.textures))

            result.items[full_id] = node

    def _build_modern_item_definitions(self, result: AnalysisResult, archive: ModArchive, namespace: str, lang: dict[str, str]) -> None:
        prefix = f"assets/{namespace}/items/"
        for name in archive.names:
            if not name.startswith(prefix) or not name.endswith(".json"):
                continue
            path_id = name[len(prefix):-5]
            full_id = f"{namespace}:{path_id}"
            data = archive.read_json(name)
            if not isinstance(data, dict):
                continue
            node = result.items.get(full_id)
            if node is None:
                node = ItemNode(id=full_id, namespace=namespace, kind="item", source={}, confidence=0.99, status=status.ANALYZED)
                result.items[full_id] = node
            node.source["item_model_definition"] = name
            node.item_model = full_id
            try:
                node.model_tree = translate_item_model_definition(data, namespace)
            except Exception:
                node.model_tree = None
            model = data.get("model")
            if isinstance(model, dict) and model.get("type") == "minecraft:model":
                mpath = model.get("model") or model.get("path")
                if isinstance(mpath, str): node.model = self._normalize_resource_location(mpath, namespace)
            elif isinstance(model, str):
                node.model = self._normalize_resource_location(model, namespace)
            if not node.display_name:
                node.display_name = self._lookup_name(lang, "item", path_id) or self._lookup_name(lang, "block", path_id)
            node.gear_kind = self._infer_gear_kind(full_id, model if isinstance(model, dict) else None)
            node.metadata["model_slots"] = self._find_gear_model_slots(path_id, {p: {} for p in self._modern_item_model_paths(archive, namespace)}, node.gear_kind)
            self._infer_gear_stats(node)
            # Modern item definitions may already select a GUI model. Only infer
            # a fallback GUI icon when the source definition does not do so.
            if not self._model_tree_has_gui_select(node.model_tree):
                item_texture_paths = {
                    n[len(f"assets/{namespace}/textures/item/"):-4]
                    for n in archive.names
                    if n.startswith(f"assets/{namespace}/textures/item/") and n.endswith('.png')
                }
                self._infer_gui_icon(node, path_id, None, item_texture_paths, set(node.textures))

    @staticmethod
    def _modern_item_model_paths(archive: ModArchive, namespace: str) -> list[str]:
        prefix = f"assets/{namespace}/models/item/"
        return [n[len(prefix):-5] for n in archive.names if n.startswith(prefix) and n.endswith(".json")]

    def _find_gear_model_slots(self, path_id: str, item_models: dict[str, dict[str, Any]], kind: str | None) -> list[str]:
        if kind not in ("bow", "crossbow", "shield", "spear"):
            return []
        suffixes = {
            "bow": ["", "_pulling_0", "_pulling_1", "_pulling_2"],
            "crossbow": ["", "_pulling_0", "_pulling_1", "_pulling_2", "_arrow", "_firework"],
            "shield": ["", "_blocking"],
            "spear": ["", "_hand"],
        }[kind]
        return [f"{self._current_namespace}:item/{path_id}{suffix}" for suffix in suffixes if path_id + suffix in item_models]

    def _infer_gear_kind(self, full_id: str, model_json: dict[str, Any] | None) -> str | None:
        if not getattr(self.settings, "gear_enabled", True): return None
        p = full_id.split(":",1)[-1].lower()
        if "crossbow" in p: return "crossbow"
        if "bow" in p: return "bow"
        if "trident" in p: return "trident"
        if any(x in p for x in getattr(self.settings, "gear_spear_keywords", ["spear","lance","javelin","halberd","glaive","pike"])): return "spear"
        if any(x in p for x in getattr(self.settings, "gear_shield_keywords", ["shield","buckler"])): return "shield"
        if any(x in p for x in getattr(self.settings, "gear_weapon_keywords", ["sword","mace","dagger","hammer","club","rapier","katana","greatsword","weapon","knife"])): return "weapon"
        if any(x in p for x in getattr(self.settings, "gear_tool_keywords", ["pickaxe","axe","shovel","hoe","shears","tool","wrench"])): return "tool"
        if any(x in p for x in getattr(self.settings, "armor_keywords", ["helmet","chestplate","leggings","boots","armor","armour"])): return "armor"
        return None

    def _infer_gear_stats(self, node: ItemNode) -> None:
        k=node.gear_kind
        if not k: return
        tier=node.tool_tier or "wood"
        damage={"wood":4.0,"stone":5.0,"copper":5.0,"iron":6.0,"gold":4.0,"diamond":7.0,"netherite":8.0}
        if k in ("weapon","spear","trident"):
            p=node.id.split(":")[-1].lower(); base=damage.get(tier,4.0)
            if "trident" in p: node.attack_damage,node.attack_speed=9.0,-2.9
            elif "sword" in p: node.attack_damage,node.attack_speed=base-1.0,-2.4
            elif "dagger" in p: node.attack_damage,node.attack_speed=max(2.0,base-2.0),-1.8
            else: node.attack_damage,node.attack_speed=base,-2.8
        if node.durability is None and k in ("tool","weapon","spear","trident"):
            node.durability={"wood":59,"stone":131,"copper":190,"iron":250,"gold":32,"diamond":1561,"netherite":2031}.get(tier)
        if k == "armor" and node.durability is None:
            slot=node.id.split(":")[-1].lower()
            base={"helmet":165,"chestplate":240,"leggings":225,"boots":195}
            for key,val in base.items():
                if key in slot: node.durability=val

    def _model_tree_has_gui_select(self, tree: dict[str, Any] | None) -> bool:
        if not isinstance(tree, dict):
            return False
        if tree.get("type") == "minecraft:select" and tree.get("property") == "minecraft:display_context":
            for case in tree.get("cases", []):
                if isinstance(case, dict):
                    when = case.get("when")
                    if when == "gui" or (isinstance(when, list) and "gui" in when):
                        return True
        for value in tree.values():
            if isinstance(value, dict) and self._model_tree_has_gui_select(value):
                return True
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict) and self._model_tree_has_gui_select(entry):
                        return True
        return False

    def _infer_gui_icon(
        self,
        node: ItemNode,
        path_id: str,
        model_json: dict[str, Any] | None,
        item_textures: set[str],
        model_textures: set[str] | None = None,
    ) -> None:
        """Detect a dedicated 2D GUI icon for a 3D/block-backed item.

        A texture never creates an item by itself. Here it is only linked when
        an already-detected item also has a genuinely 3D model, and an explicit
        icon candidate can be found. This prevents helper/stage PNGs from
        becoming phantom items while allowing one item to use 2D in GUI and 3D
        everywhere else.
        """
        if not getattr(self.settings, "gui_icon_enabled", True):
            return
        if self._model_tree_has_gui_select(node.model_tree):
            return
        # A static 2D generated/handheld model does not need a split.
        parent = str((model_json or {}).get("parent", ""))
        if parent in ("minecraft:item/generated", "minecraft:item/handheld") and not (model_json and model_json.get("elements")):
            return
        is_3d = bool(model_json and model_json.get("elements")) or (
            bool(parent) and parent not in ("minecraft:item/generated", "minecraft:item/handheld")
            and not parent.endswith("/item/generated") and not parent.endswith("/item/handheld")
        )
        if not is_3d:
            return

        suffixes = list(getattr(self.settings, "gui_icon_suffixes", ["_icon", "_inventory", "_gui"]))
        candidates: list[str] = []
        model_textures = model_textures or set()
        exact = path_id in item_textures
        exact_ref = f"{node.namespace}:item/{path_id}"
        # Never treat the same texture as a GUI icon if the 3D model actually
        # consumes it. This is the critical distinction between a model texture
        # and a dedicated inventory icon.
        exact_is_model_texture = exact_ref in model_textures
        if getattr(self.settings, "gui_icon_prefer_exact_item_texture", True) and exact and not exact_is_model_texture:
            candidates.append(path_id)
        for suffix in suffixes:
            cand = path_id + suffix
            if cand in item_textures and f"{node.namespace}:item/{cand}" not in model_textures:
                candidates.append(cand)
        # Do not guess arbitrary unrelated textures. Only explicit exact/suffix
        # names are eligible. The common exact case is especially useful for
        # block-backed items that have a dedicated flat inventory texture.
        if not candidates:
            return
        chosen = candidates[0]
        node.gui_icon_texture = f"{node.namespace}:item/{chosen}"
        node.metadata["gui_icon_source"] = {
            "texture": node.gui_icon_texture,
            "reason": "dedicated_item_texture_for_3d_model",
        }

    def _model_textures(self, model: dict[str, Any], ns: str) -> list[str]:
        textures = model.get("textures", {})
        out: list[str] = []
        if isinstance(textures, dict):
            for value in textures.values():
                if isinstance(value, str):
                    out.append(self._normalize_resource_location(value, ns))
        return sorted(set(out))

    def _normalize_resource_location(self, value: str, default_ns: str) -> str:
        if ":" in value:
            return value
        return f"{default_ns}:{value}"

    def _infer_material(self, full_id: str, model_json: dict[str, Any] | None) -> str:
        key = full_id
        if key in self.vanilla_materials:
            return self.vanilla_materials[key]
        last = full_id.split(":")[-1]
        if last in self.vanilla_materials:
            return self.vanilla_materials[last]
        # Minecraft registry names mirror Bukkit/CraftEngine material keys for
        # the great majority of vanilla items. This makes the converter useful
        # for the complete vanilla registry instead of a small hand-maintained
        # allow-list; explicit mappings still win above.
        if full_id.startswith("minecraft:"):
            return last
        return self.settings.item_material_fallback

    def _infer_tool_tier(self, full_id: str) -> str | None:
        last = full_id.split(":")[-1].lower()
        keywords = ("sword", "pickaxe", "axe", "shovel", "hoe")
        if not any(kw in last for kw in keywords):
            return None
        for tier_kw, mapped in (("wooden", "wood"), ("stone", "stone"), ("iron", "iron"), ("golden", "gold"), ("gold", "gold"), ("diamond", "diamond"), ("netherite", "netherite")):
            if tier_kw in last:
                return mapped
        return "wood"

    def _lookup_name(self, lang: dict[str, str], prefix: str, path_id: str) -> str | None:
        ns = getattr(self, "_current_namespace", None)
        candidates = [f"{prefix}.{path_id}"]
        if ns:
            candidates.insert(0, f"{prefix}.{ns}.{path_id}")
        for key in candidates:
            if key in lang:
                return lang[key]
        return None

    # --- blocks -----------------------------------------------------------
    def _build_blocks(
        self,
        result: AnalysisResult,
        blockstates: dict[str, dict[str, Any]],
        block_models: dict[str, dict[str, Any]],
        block_textures: set[str],
        lang: dict[str, str],
        namespace: str | None = None,
    ) -> None:
        ns = namespace or result.metadata.namespace
        # A block is an actual placeable block only when it has a blockstate
        # definition. Model files without a blockstate (stage/slice variants,
        # side/texture-only models) are variants, not independent blocks.
        ids = sorted(blockstates.keys())

        for path_id in ids:
            full_id = f"{ns}:{path_id}"
            node = BlockNode(
                id=full_id,
                namespace=ns,
                kind="block",
                source={
                    "blockstate": f"assets/{ns}/blockstates/{path_id}.json" if path_id in blockstates else None,
                    "model": f"assets/{ns}/models/block/{path_id}.json" if path_id in block_models else None,
                },
                confidence=0.99 if path_id in blockstates else 0.85,
                status=status.ANALYZED,
            )
            node.references = [r for r in node.source.values() if r]

            bs = blockstates.get(path_id)
            if bs:
                self._parse_blockstate_into(node, bs, ns)

            primary_model = block_models.get(path_id)
            if primary_model is None and node.blockstate_variants:
                first_model = node.blockstate_variants[0].get("model")
                if isinstance(first_model, str):
                    model_loc = self._normalize_resource_location(first_model, ns)
                    model_path = model_loc.split(":", 1)[1] if ":" in model_loc else model_loc
                    if model_path.startswith("block/"):
                        primary_model = block_models.get(model_path[len("block/"):])
            if primary_model is not None:
                node.block_model_path = f"{ns}:block/{path_id}" if path_id in block_models else None
                node.textures = self._model_textures(primary_model, ns)

            self._detect_block_kind(node, path_id, primary_model, blockstates.get(path_id))

            node.display_name = self._lookup_name(lang, "block", path_id)

            result.blocks[full_id] = node

    def _parse_blockstate_into(self, node: BlockNode, bs: dict[str, Any], ns: str) -> None:
        variants = bs.get("variants", {})
        multipart = bs.get("multipart", [])
        node.blockstate_variants = []
        node.blockstate_multipart = []
        props: dict[str, set[str]] = defaultdict(set)
        variant_models: dict[str, str] = {}

        if isinstance(variants, dict):
            for key, entry in variants.items():
                entry_list = entry if isinstance(entry, list) else [entry]
                for variant in entry_list:
                    if not isinstance(variant, dict):
                        continue
                    model = variant.get("model")
                    record = {
                        "condition": str(key),
                        "model": model,
                        "x": variant.get("x"),
                        "y": variant.get("y"),
                        "z": variant.get("z"),
                        "uvlock": variant.get("uvlock"),
                        "weight": variant.get("weight"),
                    }
                    node.blockstate_variants.append(record)
                    if key and isinstance(model, str):
                        variant_models[key] = self._strip_block_variant_model(model, ns)
                if key:
                    for pair in str(key).split(","):
                        if "=" in pair:
                            pname, _, pvalue = pair.partition("=")
                            props[pname].add(pvalue)

        if isinstance(multipart, list):
            for part in multipart:
                if not isinstance(part, dict):
                    continue
                node.blockstate_multipart.append(part)
                when = part.get("when")
                self._collect_when_properties(when, props)

        for pname in sorted(props):
            values = sorted(props[pname])
            ptype = self._infer_property_type(pname, values)
            default = self._property_default(pname, values, node.blockstate_variants)
            node.states.append(BlockStateNode(property_name=pname, property_type=ptype, allowed_values=values, default_value=default, source_declaration=f"assets/{node.namespace}/blockstates/{node.id.split(':')[-1]}.json", variants={"models": {k: v for k, v in sorted(variant_models.items()) if f"{pname}=" in k}}))

        if node.blockstate_variants:
            first = node.blockstate_variants[0]
            if first.get("condition"):
                node.default_state = first["condition"]
            if first.get("model"):
                node.model = self._normalize_resource_location(str(first["model"]), ns)

    @staticmethod
    def _collect_when_properties(when: Any, props: dict[str, set[str]]) -> None:
        if not isinstance(when, dict):
            return
        for pname, pvalue in when.items():
            if pname == "OR":
                if isinstance(pvalue, list):
                    for child in pvalue:
                        Analyzer._collect_when_properties(child, props)
                continue
            values = pvalue if isinstance(pvalue, list) else [pvalue]
            for value in values:
                props[str(pname)].add(str(value))

    def _strip_block_variant_model(self, model: str, ns: str) -> str:
        loc = self._normalize_resource_location(model, ns)
        return loc

    # Registered CraftEngine 26.8 property type ids (from Properties.java).  Any
    # source property that cannot be represented by one of them is emitted as a
    # `string` property with an explicit `values` list instead of inventing an
    # unknown union/enum type which CraftEngine would reject at load time.
    VALID_PROPERTY_TYPES = {
        "boolean", "int", "string", "axis", "horizontal_direction", "4-direction",
        "direction", "6-direction", "single_block_half", "double_block_half",
        "hinge", "stairs_shape", "slab_type", "sofa_shape", "anchor_type",
        "bed_part",
    }

    def _infer_property_type(self, pname: str, values: Iterable[str]) -> str:
        vals = [str(v) for v in values]
        # Vanilla `half` is ambiguous: doors/tall plants use upper/lower
        # (double_block_half) while trapdoors use top/bottom
        # (single_block_half). Always resolve it from the observed values.
        if pname == "half":
            if set(vals) <= {"top", "bottom"}:
                return "single_block_half"
            if set(vals) <= {"upper", "lower"}:
                return "double_block_half"
            return "string"
        if pname in self.property_types and self.property_types[pname] in self.VALID_PROPERTY_TYPES:
            return self.property_types[pname]
        if all(v in ("true", "false") for v in vals):
            return "boolean"
        if all(v.lstrip("-").isdigit() for v in vals):
            return "int"
        if set(vals) <= {"x", "y", "z"}:
            return "axis"
        if set(vals) <= {"north", "south", "east", "west"}:
            return "horizontal_direction"
        if set(vals) <= {"north", "south", "east", "west", "up", "down"}:
            return "direction"
        if set(vals) <= {"left", "right"}:
            return "hinge"
        if set(vals) <= {"head", "foot"}:
            return "bed_part"
        if set(vals) <= {"ceiling", "floor", "wall"}:
            return "anchor_type"
        if set(vals) <= {"top", "bottom", "double"}:
            return "slab_type"
        if set(vals) <= {"straight", "inner_left", "inner_right", "outer_left", "outer_right"}:
            return "stairs_shape"
        if set(vals) <= {"north", "east", "south", "west", "up", "down", "none"}:
            return "direction"
        return "string"

    def _property_default(self, pname: str, values: list[str], variants: list[dict[str, Any]]) -> str | None:
        if variants:
            first_condition = variants[0].get("condition")
            if first_condition:
                for pair in first_condition.split(","):
                    if pair.startswith(f"{pname}="):
                        return pair.split("=", 1)[1]
        # Heuristic default for booleans
        if values and all(v in ("true", "false") for v in values):
            return "false"
        return values[0] if values else None

    def _infer_auto_state(self, path_id: str, transparent: bool) -> str:
        low = path_id.lower()
        for key, group in self.auto_state_map.items():
            if key in low:
                return group
        return self.settings.block_auto_state

    @staticmethod
    def _model_needs_entity(model: dict[str, Any] | None) -> bool:
        if not isinstance(model, dict):
            return False
        elements = model.get("elements")
        if not isinstance(elements, list) or not elements:
            return False
        # A true full cube can safely stay a native block model. Anything thin,
        # offset, rotated, or otherwise non-cubic benefits from entity rendering
        # because the carrier state provides a stable collision/storage state.
        for el in elements:
            if not isinstance(el, dict):
                return True
            fr = el.get("from")
            to = el.get("to")
            if fr != [0, 0, 0] or to != [16, 16, 16]:
                return True
            if el.get("rotation"):
                return True
        return False

    def _detect_block_kind(self, node: BlockNode, path_id: str, model: dict[str, Any] | None, bs: dict[str, Any] | None) -> None:
        low = path_id.lower()
        parent = str((model or {}).get("parent", ""))
        render_type = (model or {}).get("render_type")
        has_age = any(s.property_name == "age" and s.property_type == "int" for s in node.states)
        is_tripwire = "tripwire" in parent or "tripwire" in low or node.auto_state == "tripwire"
        is_cross = "cross" in parent or "crop_cross" in parent
        is_crop = has_age or low.endswith("_crop") or "crop" in low
        thin_markers = ("cutting_board", "tray", "pan", "pie", "cake", "board", "bowl", "plate", "basket")
        is_thin = any(x in low for x in thin_markers) or "tripwire" in parent

        if render_type in ("minecraft:cutout", "cutout", "minecraft:translucent", "translucent") or is_cross or is_crop:
            node.transparent = True
            node.flat = is_cross or is_crop

        if is_crop:
            node.auto_state = "higher_tripwire"
            node.metadata["is_crop"] = True
            node.metadata["stage_max"] = max([int(s.allowed_values[-1]) for s in node.states if s.property_name == "age" and s.allowed_values and all(str(v).lstrip('-').isdigit() for v in s.allowed_values)] or [7])
            node.behavior_configs = [
                {"type": "crop_block", "grow_speed": self.settings.crop_default_grow_speed, "is_bone_meal_target": True},
                {
                    "type": "bush_block",
                    "bottom_blocks": ["minecraft:farmland"],
                    "bottom_block_tags": ["minecraft:farmland"],
                },
                {"type": "liquid_flowable_block"},
            ]
            node.settings["hardness"] = self.settings.crop_default_hardness
            node.settings["resistance"] = self.settings.crop_default_resistance
            node.settings["push_reaction"] = "destroy"
            # Bush/support behavior is only added when the source block visibly
            # behaves like a plant. The target behavior provides the gameplay;
            # visual stages are mapped separately below.
            stages: dict[str, str] = {}
            for variant in node.blockstate_variants:
                cond = str(variant.get("condition", ""))
                model_ref = variant.get("model")
                if "age=" in cond and isinstance(model_ref, str):
                    age = cond.split("age=", 1)[1].split(",", 1)[0]
                    stages[age] = self._normalize_resource_location(model_ref, node.namespace)
            if stages:
                max_age = int(node.metadata.get("stage_max", 7))
                # A mod can reuse a visual model across several ages. Expand
                # the mapping to every real state so no age is left without an
                # appearance. Prefer the closest lower age when a state is
                # omitted; fall back to the closest available model.
                normalized = {str(k): v for k, v in stages.items()}
                available = sorted(int(k) for k in normalized if str(k).lstrip("-").isdigit())
                if available:
                    for age in range(max_age + 1):
                        if str(age) not in normalized:
                            lower = [a for a in available if a <= age]
                            pick = max(lower) if lower else min(available, key=lambda a: abs(a - age))
                            normalized[str(age)] = normalized[str(pick)]
                node.metadata["stage_models"] = {k: normalized[k] for k in sorted(normalized, key=lambda x: int(x))}
            # Growth behavior is exact at the state level; rates are not guessed.
        elif is_tripwire or is_thin or is_cross or self._model_needs_entity(model):
            node.auto_state = "higher_tripwire" if is_tripwire else "lower_tripwire"
            node.transparent = True
            node.metadata["use_entity_renderer"] = True
            variants: dict[str, dict[str, Any]] = {}
            for idx, variant in enumerate(node.blockstate_variants):
                model_ref = variant.get("model")
                if not model_ref:
                    continue
                condition = str(variant.get("condition") or "")
                key = condition or "__default__"
                item_id = f"{node.namespace}:{low}__display_{idx}"
                variants[key] = {"item": item_id, "model": self._normalize_resource_location(str(model_ref), node.namespace), "rotation": variant.get("y")}
            if not variants and node.block_model_path:
                variants["__default__"] = {"item": f"{node.namespace}:{low}__display_0", "model": node.block_model_path, "rotation": None}
            node.metadata["entity_render_variants"] = variants
        elif node.blockstate_multipart:
            # Multipart blocks cannot be faithfully represented by one block
            # model. We preserve the multipart resource and use an item_display
            # composite as the entity renderer.
            node.auto_state = self._infer_auto_state(path_id, node.transparent)
            node.metadata["use_entity_renderer"] = True
        elif node.flat or is_cross:
            node.auto_state = "sapling"
        else:
            node.auto_state = self._infer_auto_state(path_id, node.transparent)

    def _infer_block_tool_tier(self, path_id: str) -> str | None:
        return None

    def _link_crop_semantics(self, result: AnalysisResult, archive: ModArchive, namespace: str) -> None:
        if not getattr(self.settings, "reconstruct_block_behaviors", True):
            return
        try:
            relations = extract_crop_relations_from_archive(archive)
        except Exception:
            relations = {}
        applied: dict[str, dict[str, Any]] = {}
        for crop_path, info in relations.items():
            block = self._find_block_by_path(result, crop_path)
            if block is None:
                continue
            namespace = block.namespace
            seed_path = str(info.get("seed_path") or "").lower()
            if not seed_path:
                continue
            seed_id = f"{namespace}:{seed_path}"
            # Prefer exact item registry match; bytecode field names are often
            # uppercase registry constants, while the actual path is lower-case.
            if seed_id not in result.items:
                for item_id in result.items:
                    if item_id.lower() == seed_id.lower():
                        seed_id = item_id
                        break
            block.metadata["is_crop"] = True
            block.metadata["crop_seed_item"] = seed_id
            block.metadata["crop_seed_source"] = dict(info)
            if info.get("max_age") is not None:
                max_age = int(info["max_age"])
                block.metadata["stage_max"] = max_age
                for state in block.states:
                    if state.property_name == "age" and state.property_type == "int":
                        state.allowed_values = [str(v) for v in range(max_age + 1)]
                        state.default_value = "0"
                if not block.behavior_configs:
                    block.behavior_configs = [
                        {
                            "type": "crop_block",
                            "grow_speed": self.settings.crop_default_grow_speed,
                            "light_requirement": getattr(self.settings, "crop_light_requirement", 9),
                            "is_bone_meal_target": True,
                            "bone_meal_age_bonus": {"type": "uniform", "min": 1, "max": 2},
                        },
                        {
                            "type": "bush_block",
                            "bottom_blocks": ["minecraft:farmland"],
                            "bottom_block_tags": ["minecraft:farmland"],
                        },
                        {"type": "liquid_flowable_block"},
                    ]
                else:
                    for behavior in block.behavior_configs:
                        if isinstance(behavior, dict) and behavior.get("type") == "crop_block":
                            behavior.setdefault("grow_speed", self.settings.crop_default_grow_speed)
                            behavior.setdefault("light_requirement", getattr(self.settings, "crop_light_requirement", 9))
                            behavior.setdefault("is_bone_meal_target", True)
                            behavior.setdefault("bone_meal_age_bonus", {"type": "uniform", "min": 1, "max": 2})
                block.settings["hardness"] = 0.0
                block.settings["resistance"] = 0.0
            applied[block.id] = {"seed_item": seed_id, **dict(info)}
            seed_item = result.items.get(seed_id)
            if seed_item:
                seed_item.metadata["crop_block"] = block.id
                seed_item.metadata["crop_seed_for"] = block.id
        result.bytecode_crops = applied

    @staticmethod
    def _find_block_by_path(result: AnalysisResult, path: str) -> BlockNode | None:
        """Find a block by its registry path, tolerating a missing namespace.

        Bytecode crop relations are recorded as registry paths without a
        namespace (for example ``bellpepper_crop``), while a mod may place the
        block under the primary namespace or under an asset compatibility
        namespace.
        """
        path = path.lower().lstrip("/")
        candidates = [p for p in result.blocks if p.split(":", 1)[-1].lower() == path]
        if not candidates and not path.endswith("_crop"):
            candidates = [p for p in result.blocks if p.split(":", 1)[-1].lower() == f"{path}_crop"]
        if candidates:
            # Prefer the primary namespace, then lexicographic order.
            primary = result.metadata.namespace
            candidates.sort(key=lambda p: (0 if p.split(":", 1)[0] == primary else 1, p))
            return result.blocks[candidates[0]]
        return None

    def _ensure_crop_loot(self, result: AnalysisResult) -> None:
        """Ensure crop blocks have CE-native loot when source loot is absent.
        Existing source loot is preserved and normalized later by LootGenerator.
        """
        from .ir import LootNode
        for block in result.blocks.values():
            if not block.metadata.get("is_crop"):
                continue
            seed = block.metadata.get("crop_seed_item")
            if not seed:
                continue
            path = block.id.split(":", 1)[1]
            namespace = block.namespace
            base = path[:-5] if path.endswith("_crop") else path
            produce = f"{namespace}:{base}"
            if produce not in result.items:
                produce = seed
            max_age = int(block.metadata.get("stage_max", 7))
            loot_id = f"{namespace}:blocks/{path}"
            if loot_id not in result.loot:
                pools = [
                    {
                        "rolls": 1,
                        "entries": [{"type": "item", "item": seed}],
                    }
                ]
                if produce != seed:
                    pools.append({
                        "rolls": 1,
                        "conditions": [{"type": "match_block_property", "properties": {"age": max_age}}],
                        "entries": [{"type": "item", "item": produce}],
                    })
                raw = {"type": "block", "pools": pools}
                result.loot[loot_id] = LootNode(
                    id=loot_id, namespace=namespace, kind="loot",
                    source={"generated": "crop_semantics", "block": block.id},
                    confidence=0.93, status=status.TRANSFORM, raw=raw)
            block.loot = loot_id
            if loot_id not in block.references:
                block.references.append(loot_id)
    
    # --- recipes ----------------------------------------------------------
    def _build_recipes(self, result: AnalysisResult, archive: ModArchive, namespaces: set[str]) -> None:
        for ns in sorted(namespaces):
            for dirname in ("recipes", "recipe"):
                prefix = f"data/{ns}/{dirname}/"
                for name in archive.names:
                    if not name.startswith(prefix) or not name.endswith(".json"):
                        continue
                    rel = name[len(prefix):-5]
                    data = archive.read_json(name)
                    if not isinstance(data, dict):
                        continue
                    node = self._recipe_from_json(f"{ns}:{rel}", data, name)
                    result.recipes[node.id] = node

    def _recipe_from_json(self, full_id: str, data: dict[str, Any], source_path: str) -> RecipeNode:
        rtype = str(data.get("type", "") or "")
        node = RecipeNode(id=full_id, namespace=full_id.split(":")[0], kind="recipe", source={"path": source_path}, confidence=0.99, status=status.ANALYZED, recipe_type=rtype, original_type=rtype, raw=data)
        short = rtype.split(":")[-1]
        result = data.get("result")
        normalized_results = self._normalize_results(result)
        node.results = normalized_results
        node.result = normalized_results[0] if normalized_results else self._normalize_result(result)
        node.count = int((node.result or {}).get("count", 1))
        node.unlock = {k: data[k] for k in ("unlock_on_join", "unlock_on_ingredient_obtained") if k in data}
        node.conditions = data.get("conditions", []) if isinstance(data.get("conditions"), list) else []
        node.post_processors = data.get("post_processors", []) if isinstance(data.get("post_processors"), list) else []
        node.transform_processors = data.get("transform_processors", []) if isinstance(data.get("transform_processors"), list) else []
        if isinstance(node.result, dict) and isinstance(result, dict) and result.get("post_processors"):
            node.result["post_processors"] = result["post_processors"]

        if short == "crafting_shaped":
            node.pattern = [str(x) for x in data.get("pattern", [])]
            node.key = data.get("key", {}) if isinstance(data.get("key", {}), dict) else {}
            node.ingredients = self._key_ingredients(node.key)
        elif short == "crafting_shapeless":
            node.ingredients = self._shapeless_ingredients(data.get("ingredients", []))
        elif short in ("smelting", "blasting", "smoking", "campfire_cooking"):
            node.ingredients = self._shapeless_ingredients([data.get("ingredient")] if data.get("ingredient") is not None else [])
            node.experience = data.get("experience")
            node.time = data.get("cookingtime", data.get("time"))
        elif short == "stonecutting":
            node.ingredients = self._shapeless_ingredients([data.get("ingredient")] if data.get("ingredient") is not None else [])
        elif short in ("smithing_transform", "smithing_trim"):
            parts = [data.get("template"), data.get("template_type"), data.get("base"), data.get("addition")]
            node.ingredients = self._shapeless_ingredients([x for x in parts if x is not None])
        elif short == "brewing":
            parts = [data.get("ingredient"), data.get("container")]
            node.ingredients = self._shapeless_ingredients([x for x in parts if x is not None])
        elif rtype in ("farmersdelight:cooking", "farmersdelight:cutting"):
            # Farmer's Delight custom serializers. They are not native CraftEngine
            # recipe types; the station mapping in Settings decides whether they
            # become a workbench (cooking) or a SliceBoard recipe (cutting). Keep
            # the raw fields on the node so the report can explain the trade-off.
            if isinstance(data.get("ingredients"), list):
                node.ingredients = self._shapeless_ingredients(data["ingredients"])
            elif data.get("ingredient") is not None:
                node.ingredients = self._shapeless_ingredients([data["ingredient"]])
            if rtype == "farmersdelight:cooking":
                node.experience = data.get("experience")
                node.time = data.get("cookingtime", data.get("time"))
        else:
            if isinstance(data.get("ingredients"), list):
                node.ingredients = self._shapeless_ingredients(data["ingredients"])
            elif data.get("ingredient") is not None:
                node.ingredients = self._shapeless_ingredients([data["ingredient"]])
        return node

    def _normalize_result(self, result: Any) -> dict[str, Any] | None:
        if result is None:
            return None
        if isinstance(result, str):
            return {"id": self._normalize_resource_location(result, "minecraft"), "count": 1}
        if isinstance(result, dict):
            item_id = result.get("id") or result.get("item")
            if item_id is None:
                return None
            normalized = {
                "id": self._normalize_resource_location(str(item_id), "minecraft"),
                "count": int(result.get("count", 1)),
            }
            if result.get("chance") is not None:
                try:
                    normalized["chance"] = float(result.get("chance"))
                except (TypeError, ValueError):
                    normalized["chance"] = 1.0
            return normalized
        return None

    def _normalize_results(self, result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            out: list[dict[str, Any]] = []
            for entry in result:
                normalized = self._normalize_result(entry)
                if normalized:
                    if isinstance(entry, dict) and entry.get("chance") is not None:
                        try:
                            normalized["chance"] = float(entry["chance"])
                        except (TypeError, ValueError):
                            normalized["chance"] = 1.0
                    out.append(normalized)
            return out
        normalized = self._normalize_result(result)
        return [normalized] if normalized else []

    def _result_count(self, result: Any) -> int:
        normalized = self._normalize_result(result)
        return normalized.get("count", 1) if normalized else 1

    def _key_ingredients(self, key: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for symbol in sorted(key):
            value = key[symbol]
            if isinstance(value, dict):
                item = value.get("item") or value.get("tag") or value.get("id")
                if item:
                    ref = str(item)
                    if value.get("tag") and not ref.startswith("#"):
                        ref = "#" + ref
                    out.append({"items": ref if ref.startswith("#") else self._normalize_resource_location(ref, "minecraft"), "symbol": symbol})
            elif isinstance(value, list):
                options = []
                for opt in value:
                    if isinstance(opt, str):
                        options.append(self._normalize_resource_location(opt, "minecraft"))
                    elif isinstance(opt, dict):
                        v = opt.get("item") or opt.get("tag") or opt.get("id")
                        if v:
                            vv = str(v)
                            if opt.get("tag") and not vv.startswith("#"):
                                vv = "#" + vv
                            options.append(vv if vv.startswith("#") else self._normalize_resource_location(vv, "minecraft"))
                out.append({"items": options, "symbol": symbol})
        return out

    def _shapeless_ingredients(self, ingredients: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(ingredients, list):
            return out
        for ing in ingredients:
            if isinstance(ing, str):
                out.append({"items": ing if ing.startswith("#") else self._normalize_resource_location(ing, "minecraft")})
                continue
            if isinstance(ing, dict):
                item = ing.get("item") or ing.get("tag") or ing.get("id")
                if item:
                    ref = str(item)
                    if ing.get("tag") and not ref.startswith("#"):
                        ref = "#" + ref
                    ref = ref if ref.startswith("#") else self._normalize_resource_location(ref, "minecraft")
                    entry: dict[str, Any] = {"items": ref}
                    if "count" in ing: entry["count"] = ing["count"]
                    if ing.get("source") is not None: entry["source"] = ing["source"]
                    if ing.get("predicate") is not None: entry["predicate"] = ing["predicate"]
                    out.append(entry)
                    continue
            if isinstance(ing, list):
                options = []
                for opt in ing:
                    if isinstance(opt, str):
                        options.append(opt if opt.startswith("#") else self._normalize_resource_location(opt, "minecraft"))
                    elif isinstance(opt, dict):
                        v = opt.get("item") or opt.get("tag") or opt.get("id")
                        if v:
                            vv = str(v)
                            if opt.get("tag") and not vv.startswith("#"):
                                vv = "#" + vv
                            options.append(vv if vv.startswith("#") else self._normalize_resource_location(vv, "minecraft"))
                if options:
                    out.append({"items": options})
        return out

    # --- loot -------------------------------------------------------------
    def _build_loot(self, result: AnalysisResult, archive: ModArchive, namespaces: set[str]) -> None:
        for namespace in sorted(namespaces):
            prefixes = (f"data/{namespace}/loot_table/", f"data/{namespace}/loot_tables/")
            for name in archive.names:
                prefix = next((p for p in prefixes if name.startswith(p)), None)
                if prefix is None or not name.endswith(".json"):
                    continue
                rel = name[len(prefix):-5]
                data = archive.read_json(name)
                if not isinstance(data, dict):
                    continue
                node = LootNode(
                    id=f"{namespace}:{rel}",
                    namespace=namespace,
                    kind="loot",
                    source={"path": name},
                    confidence=1.0,
                    status=status.ANALYZED,
                    raw=data,
                )
                result.loot[node.id] = node

    # --- tags -------------------------------------------------------------
    def _build_tags(self, result: AnalysisResult, archive: ModArchive, namespaces: set[str]) -> None:
        """Import all item/block tags from ``data/<namespace>/tags/<registry>``.

        Minecraft/NeoForge uses singular registry folders (``item`` / ``block``).
        The IR stores them with plural registry names so recipe resolution can
        distinguish item and block tags without changing the external tag id.
        """
        for name in archive.names:
            if not name.startswith("data/") or not name.endswith(".json"):
                continue
            parts = name.split("/")
            if len(parts) < 6 or parts[2] != "tags":
                continue
            tag_namespace = parts[1]
            if tag_namespace not in namespaces:
                continue
            registry_raw = parts[3]
            if registry_raw not in ("item", "block", "items", "blocks"):
                continue
            registry = "items" if registry_raw in ("item", "items") else "blocks"
            tag_path = "/".join(parts[4:])[:-5]
            data = archive.read_json(name)
            if isinstance(data, dict):
                result.tags[f"{tag_namespace}:{registry}/{tag_path}"] = data

    # --- reference-driven item creation ----------------------------------
    def _create_items_from_references(
        self, result: AnalysisResult, archive: ModArchive, namespaces: set[str], lang: dict[str, str]
    ) -> None:
        """Create items that are only discoverable through recipes / loot.

        Some mods register items in data-driven recipes/loot but ship no
        `models/item` JSON, no `items/` model definition and no falling-back
        lang key for the texture. Those items must still be materialized, and
        the source archive already contains the texture (or a textual model).
        """
        if not namespaces:
            return
        references: set[str] = set()

        def add_ref(value: Any) -> None:
            if isinstance(value, str):
                if not value.startswith("#") and ":" in value:
                    references.add(value)
            elif isinstance(value, dict):
                for key in ("item", "id", "result", "base", "addition", "template", "ingredient", "container"):
                    if key in value:
                        add_ref(value[key])
                for key in ("ingredients", "results", "entries", "values"):
                    if key in value:
                        add_ref(value[key])
            elif isinstance(value, list):
                for x in value:
                    add_ref(x)

        for recipe in result.recipes.values():
            add_ref(recipe.result)
            add_ref(recipe.results)
            add_ref(recipe.ingredients)
            if isinstance(recipe.raw, dict):
                add_ref(recipe.raw)

        for loot in result.loot.values():
            add_ref(loot.raw)

        for ref in sorted(references):
            if ":" not in ref:
                continue
            ns, path = ref.split(":", 1)
            if ns not in namespaces or not path:
                continue
            if ref in result.items or ref in result.blocks:
                continue
            model_json = f"assets/{ns}/models/item/{path}.json"
            modern_json = f"assets/{ns}/items/{path}.json"
            texture_png = f"assets/{ns}/textures/item/{path}.png"
            if not (archive.exists(model_json) or archive.exists(modern_json) or archive.exists(texture_png)):
                continue
            self._current_namespace = ns
            node = ItemNode(id=ref, namespace=ns, kind="item", confidence=0.95, status=status.ANALYZED)
            node.display_name = self._lookup_name(lang, "item", path)
            if archive.exists(model_json):
                node.source["model"] = model_json
                data = archive.read_json(model_json)
                if isinstance(data, dict):
                    parent = str(data.get("parent", ""))
                    layer0 = data.get("textures", {}).get("layer0") if isinstance(data.get("textures"), dict) else None
                    if parent in ("minecraft:item/generated", "minecraft:item/handheld") and isinstance(layer0, str):
                        node.textures = [self._normalize_resource_location(layer0, ns)]
                    else:
                        node.model = self._normalize_resource_location(parent, ns) if parent and ":" in parent else f"{ns}:item/{path}"
            elif archive.exists(modern_json):
                node.source["item_model_definition"] = modern_json
                node.item_model = ref
                data = archive.read_json(modern_json)
                if isinstance(data, dict):
                    try:
                        node.model_tree = translate_item_model_definition(data, ns)
                    except Exception:
                        node.model_tree = None
            elif archive.exists(texture_png):
                node.source["texture"] = texture_png
                node.textures = [f"{ns}:item/{path}"]
            result.items[ref] = node

    # --- asset references -------------------------------------------------
    def _populate_asset_refs(self, result: AnalysisResult, archive: ModArchive) -> None:
        """Populate diagnostic model/texture references on every IR node.

        This is deliberately *not* an emitter: values are used by fidelity and
        the coverage report to distinguish "asset present but unreferenced"
        from "reference points at a missing asset". It also records which
        source models/textures should be considered owned by each object.
        """
        for item in result.items.values():
            model_refs: set[str] = set()
            texture_refs: set[str] = set()
            if item.model and ":" in item.model:
                model_refs.add(item.model)
            if item.item_model and ":" in item.item_model:
                model_refs.add(item.item_model)
            if isinstance(item.model_tree, dict):
                m, t = assetindex.tree_model_refs(item.model_tree)
                model_refs.update(m)
                texture_refs.update(t)
            for tex in item.textures:
                if isinstance(tex, str) and ":" in tex:
                    texture_refs.add(tex)
            item.model_refs = list(sorted(model_refs))
            item.texture_refs = list(sorted(texture_refs))

        for block in result.blocks.values():
            model_refs: set[str] = set()
            texture_refs: set[str] = set()
            for ref in (block.model, block.block_model_path):
                if ref and ":" in ref:
                    model_refs.add(ref)
            if isinstance(block.blockstate_variants, list):
                for variant in block.blockstate_variants:
                    if isinstance(variant, dict):
                        m = variant.get("model")
                        if isinstance(m, str) and ":" in m:
                            model_refs.add(m)
            if isinstance(block.metadata.get("stage_models"), dict):
                for ref in block.metadata["stage_models"].values():
                    if isinstance(ref, str) and ":" in ref:
                        model_refs.add(ref)
            for tex in block.textures:
                if isinstance(tex, str) and ":" in tex:
                    texture_refs.add(tex)
            block.model_refs = list(sorted(model_refs))
            block.texture_refs = list(sorted(texture_refs))

        # Mark every copied texture/model that is directly referenced by an
        # object; the coverage report uses this to distinguish orphans from
        # actively-used assets. This is a diagnostic flag, never emitted.
        refs = {
            ("model", r) for node in result.items.values() for r in node.model_refs
        }
        refs.update(("model", r) for node in result.blocks.values() for r in node.model_refs)
        refs.update(("texture", r) for node in result.items.values() for r in node.texture_refs)
        refs.update(("texture", r) for node in result.blocks.values() for r in node.texture_refs)
        for resource in result.resources:
            if not resource.is_resourcepack:
                continue
            model_ref = assetindex.source_path_to_model_ref(resource.source_path)
            texture_ref = assetindex.source_path_to_texture_ref(resource.source_path)
            if (("model", model_ref) in refs) or (("texture", texture_ref) in refs):
                resource.parsed.setdefault("_converter_referenced", True)

    # --- linking ----------------------------------------------------------
    def _link_recipes_to_items(self, result: AnalysisResult) -> None:
        for recipe in result.recipes.values():
            result_id = (recipe.result or {}).get("id")
            if result_id:
                recipe.references.append(result_id)
            for ing in recipe.ingredients:
                items = ing.get("items")
                values = items if isinstance(items, list) else [items]
                for v in values:
                    if isinstance(v, str) and not v.startswith("#"):
                        recipe.references.append(v)

        # Attach recipe references and source recipe-book hints to produced
        # items. The hints are used by category generation, which currently
        # relies too heavily on registry-name heuristics.
        for recipe in result.recipes.values():
            result_id = (recipe.result or {}).get("id")
            if not result_id or result_id not in result.items:
                continue
            item = result.items[result_id]
            item.recipe_references.append(recipe.id)
            for key in ("category", "group", "recipe_book_tab", "recipe_book"):
                value = recipe.raw.get(key) if isinstance(recipe.raw, dict) else None
                if isinstance(value, str) and value and value not in item.category_hints:
                    item.category_hints.append(value)
            if isinstance(recipe.raw, dict) and isinstance(recipe.raw.get("recipes"), list):
                for r in recipe.raw["recipes"]:
                    cat = r.get("category") if isinstance(r, dict) else None
                    if isinstance(cat, str) and cat and cat not in item.category_hints:
                        item.category_hints.append(cat)

    def _link_blocks_to_items(self, result: AnalysisResult) -> None:
        # A block normally has a BlockItem with the same id. Crops are special:
        # their placeable item is the seed, so do not invent a <crop> item that
        # never existed in the source mod.
        for block in result.blocks.values():
            if block.metadata.get("is_crop") and block.metadata.get("crop_seed_item"):
                seed_id = str(block.metadata["crop_seed_item"])
                seed_item = result.items.get(seed_id)
                if seed_item is None:
                    seed_item = ItemNode(
                        id=seed_id, namespace=seed_id.split(":",1)[0], kind="item",
                        source={"generated": "crop_seed_binding", "block": block.id},
                        confidence=0.93, status=status.ANALYZED,
                    )
                    result.items[seed_id] = seed_item
                seed_item.behavior = {"type": "block_item", "block": block.id}
                seed_item.metadata["crop_block"] = block.id
                block.item_binding = seed_id
                continue
            item = result.items.get(block.id)
            if item is None:
                item = ItemNode(
                    id=block.id, namespace=block.namespace, kind="item",
                    source={"block": block.id}, confidence=0.95, status=status.ANALYZED,
                )
                result.items[block.id] = item
            item.behavior = {"type": "block_item", "block": block.id}
            if item.model is None:
                item.model = block.model or block.block_model_path or f"{block.namespace}:block/{block.id.split(':')[-1]}"
            block.item_binding = block.id
            item.metadata["block_item"] = True

    def _build_crop_display_items(self, result: AnalysisResult) -> None:
        """Create one stable display item per real crop age.

        Display helpers use minecraft:stick only as an internal carrier, never
        as a user-facing gameplay item. For cross/crop stage models whose source
        parent belongs to another mod, generate a self-contained vanilla-cross
        model so the stage cannot break when the dependency model is absent.
        """
        archive = getattr(self, "_archive", None)
        for block in result.blocks.values():
            if not block.metadata.get("is_crop"):
                continue
            stages = block.metadata.get("stage_models", {})
            if not isinstance(stages, dict):
                continue
            base_name = block.id.split(":")[-1]
            display_base = base_name[:-5] if base_name.endswith("_crop") else base_name
            for age in sorted(stages, key=lambda x: int(x)):
                model_ref = str(stages[age])
                item_id = f"{block.namespace}:{display_base}_stage{age}"
                if item_id in result.items:
                    continue
                item = ItemNode(
                    id=item_id,
                    namespace=block.namespace,
                    kind="item",
                    source={"stage_model": model_ref},
                    confidence=1.0,
                    status=status.ANALYZED,
                )
                item.display_name = block.display_name
                item.metadata["display_item"] = True
                item.metadata["stage"] = str(age)

                # Some crop configs describe the stage model as
                # `<ns>:block/custom/<crop>_stage<N>`, but the resourcepack
                # ships the actual model at `<ns>:block/<crop>_stage<N>`.
                # The authoritative source is the blockstate/block model ref,
                # which the analyzer already resolved from `models/block/`, so
                # reuse it verbatim. No `custom/` segment is ever reintroduced.
                item.metadata["stage_model_ref"] = model_ref
                item.metadata["stage_model_generated"] = False
                if archive is not None:
                    try:
                        ns, loc = model_ref.split(":", 1)
                        raw_model = archive.read_json(f"assets/{ns}/models/{loc}.json")
                    except Exception:
                        raw_model = None
                    if isinstance(raw_model, dict):
                        parent = str(raw_model.get("parent", ""))
                        if "/custom/" in parent:
                            parent = parent.replace("/custom/", "/")
                        item.metadata["stage_model_parent"] = parent
                        if parent not in ("", "minecraft:block/cross", "block/cross") and "template_crop_cross" not in parent:
                            # A custom dependency parent is kept as-is; the model
                            # file is copied verbatim, so the external parent may
                            # be supplied by its own mod at runtime.
                            item.model = model_ref
                if item.model is None:
                    item.model = model_ref

                result.items[item_id] = item

    def _build_entity_display_items(self, result: AnalysisResult) -> None:
        """Materialize visual helper items used by item_display block renderers.

        Each helper item points directly to the exact source block model. This
        avoids CMD allocation and lets CraftEngine render arbitrary tripwire/
        thin-block states through an entity while the internal block state
        remains fully functional.
        """
        for block in result.blocks.values():
            variants = block.metadata.get("entity_render_variants", {})
            if not isinstance(variants, dict):
                continue
            for spec in variants.values():
                if not isinstance(spec, dict):
                    continue
                item_id = spec.get("item")
                model = spec.get("model")
                if not item_id or not model or item_id in result.items:
                    continue
                item = ItemNode(id=item_id, namespace=block.namespace, kind="item", source={"display_model": model}, confidence=1.0, status=status.ANALYZED)
                item.model = model
                item.display_name = block.display_name
                item.metadata.update({"display_item": True, "generated_for_block": block.id})
                result.items[item_id] = item

    def _link_block_tags(self, result: AnalysisResult) -> None:
        block_values: dict[str, list[str]] = {}
        for tag_key, data in result.tags.items():
            ns, rel = util.split_id(tag_key)
            if not rel.startswith("blocks/") or not isinstance(data, dict):
                continue
            tag_id = f"{ns}:{rel[len('blocks/') :]}"
            for v in data.get("values", []):
                if isinstance(v, str) and not v.startswith("#"):
                    block_values.setdefault(v, []).append(tag_id)

        for block in result.blocks.values():
            tags = sorted(block_values.get(block.id, []))
            if not tags:
                continue
            block.tags = tags
            block.settings.setdefault("tags", tags)
            # mineable/<tool> implies correct tool requirement.
            tool_names = [t.split(":", 1)[1] if ":" in t else t for t in tags]
            tool = self._mining_tool(tool_names)
            if tool:
                block.settings.setdefault("require_correct_tools", True)
                block.settings.setdefault("correct_tools", tool)

    def _link_item_tags(self, result: AnalysisResult) -> None:
        item_values: dict[str, list[str]] = {}
        for tag_key, data in result.tags.items():
            ns, rel = util.split_id(tag_key)
            if not rel.startswith("items/") or not isinstance(data, dict):
                continue
            tag_id = f"{ns}:{rel[len('items/') :]}"
            for v in data.get("values", []):
                if isinstance(v, str) and not v.startswith("#"):
                    item_values.setdefault(v, []).append(tag_id)

        for item in result.items.values():
            tags = sorted(item_values.get(item.id, []))
            if not tags:
                continue
            item.tags = tags
            item.settings.setdefault("tags", tags)

    @staticmethod
    def _tag_id(tagname: str, namespace: str) -> str:
        # Backwards-compatible helper: callers that already have a namespace
        # should pass it through untouched.
        if ":" in tagname:
            return tagname.lstrip("#")
        vanilla_prefixes = ("mineable/", "logs", "leaves", "planks", "stairs", "slabs", "walls", "fences", "doors")
        if any(tagname == p or tagname.startswith(p) for p in vanilla_prefixes):
            return f"minecraft:{tagname}"
        return f"{namespace}:{tagname}"

    @staticmethod
    def _mining_tool(tags: list[str]) -> list[str] | None:
        tool_map = {
            "mineable/pickaxe": ["minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe", "minecraft:netherite_pickaxe"],
            "mineable/axe": ["minecraft:wooden_axe", "minecraft:stone_axe", "minecraft:iron_axe", "minecraft:diamond_axe", "minecraft:netherite_axe"],
            "mineable/shovel": ["minecraft:wooden_shovel", "minecraft:stone_shovel", "minecraft:iron_shovel", "minecraft:diamond_shovel", "minecraft:netherite_shovel"],
            "mineable/hoe": ["minecraft:wooden_hoe", "minecraft:stone_hoe", "minecraft:iron_hoe", "minecraft:diamond_hoe", "minecraft:netherite_hoe"],
        }
        for tag in tags:
            if tag in tool_map:
                return tool_map[tag]
        return None

    def _link_blocks_to_loot(self, result: AnalysisResult) -> None:
        for block in result.blocks.values():
            path_id = block.id.split(":")[-1]
            loot_key = f"{block.namespace}:blocks/{path_id}"
            if loot_key in result.loot:
                block.loot = loot_key
                block.references.append(loot_key)


def analyze_archive(
    archive: ModArchive,
    log: Log,
    minecraft_version: str = "1.21.4",
    settings: Settings | None = None,
) -> AnalysisResult:
    return Analyzer(log, minecraft_version, settings).analyze(archive)
