"""Settings loader with defaults and optional user overrides.

Settings are stored in `settings.yml` (project root). A missing file falls back
to defaults. The recipe-station mapping is the key feature that lets the user
remap non-vanilla crafting stations (e.g. Farmer's Delight cooking pot) onto
the vanilla crafting table (workbench) so those recipes become shapeless
crafting recipes instead of being reported as unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths, util
from .util import Log

# Well-known third-party recipe serializers and their default target "station".
# The default for Farmer's Delight cooking is the workbench because CraftEngine
# 26.8 has no native cooking-pot serializer, but its shapeless-transform recipe
# can preserve the multi-ingredient input + result faithfully apart from the
# station itself. Cutting recipes have a dedicated SliceBoard integration.
KNOWN_RECIPE_STATIONS: dict[str, str] = {
    "farmersdelight:cooking": "crafting_table",
    "farmersdelight:cutting": "sliceboard",
}

# Human-readable reasons for the same serializers, used in reports when a user
# deliberately overrides the default station (e.g. sets it to "unknown").
KNOWN_RECIPE_STATION_NOTES: dict[str, str] = {
    "farmersdelight:cooking": "Farmer's Delight cooking-pot recipe; CraftEngine has no cooking-pot serializer, default is a workbench shapeless_transform (ingredients+result preserved, station becomes a workbench).",
    "farmersdelight:cutting": "Farmer's Delight cutting-board recipe; mapped to the SliceBoard recipe format by default.",
}


@dataclass
class Settings:
    craftengine_version: str = "26.8"
    minecraft_version: str = "1.21.4"
    # Kept only for backward-compatible settings files. The generator does not
    # emit material for custom items anymore.
    item_material_fallback: str = "paper"
    emit_item_material: bool = False
    emit_food_apple_material: bool = False
    generate_categories: bool = True
    generate_category_translations: bool = True
    organize_recipes: bool = True
    recipe_sort_mode: str = "type_then_id"
    log_level: str = "INFO"
    write_conversion_log: bool = True
    copy_all_assets: bool = True
    validate_resource_links: bool = True
    block_auto_state: str = "solid"
    # Maps a source recipe type (e.g. "farmersdelight:cooking") to a target
    # "station". The special value "crafting_table" means: convert to a
    # shapeless crafting (workbench) recipe. Built-in defaults cover the common
    # third-party serializers; user entries override, and setting a value to
    # "unknown" disables the default mapping for that type.
    recipe_stations: dict[str, str] = field(default_factory=lambda: dict(KNOWN_RECIPE_STATIONS))
    # When True, any recipe whose source type is not in recipe_stations and not
    # a recognized vanilla station is remapped to the crafting table.
    remap_unknown_stations: bool = False
    remap_unknown_station_target: str = "crafting_table"
    # When a recipe is remapped to the crafting table, include its container
    # item (e.g. cooking pot) as one of the ingredients.
    include_container_ingredient: bool = False
    # Maps a source recipe type (or namespace) to a container item id that is
    # added as an ingredient when the recipe is remapped to the crafting table.
    # Example: {"farmersdelight:cooking": "farmersdelight:cooking_pot"}.
    container_items: dict[str, str] = field(default_factory=lambda: {
        "farmersdelight:cooking": "farmersdelight:cooking_pot",
        "farmersdelight:cutting": "farmersdelight:cutting_board",
    })
    # SliceBoard (cutting board) integration.
    sliceboard_enabled: bool = True
    # Which provider to use for custom items in SliceBoard recipes:
    # "craftengine" -> "craftengine:ns:name" or "itemsadder" -> "itemsadder:ns:name".
    sliceboard_custom_provider: str = "craftengine"
    # Prefixes used by SliceBoard for non-vanilla namespaces. Value is
    # prepended to the path, e.g. craftengine:farmersdelight + "cabbage".
    # These mappings are proposed automatically from the source mod's recipe
    # references and can be edited in the GUI before conversion.
    sliceboard_namespace_prefixes: dict[str, str] = field(default_factory=dict)
    # External namespaces that should be removed from generated recipes.
    # Their ingredient slots are deleted; recipes that become empty are dropped.
    excluded_recipe_namespaces: list[str] = field(default_factory=list)
    # When true, GUI asks for namespace resolution before conversion.
    interactive_namespace_mapping: bool = True
    # Fail the conversion when a recipe cannot be represented faithfully.
    strict_recipe_mode: bool = False
    unresolved_recipe_tag_policy: str = "skip"
    # Copy source datapack JSON for unsupported/custom recipe types to reports.
    preserve_unsupported_recipes: bool = True
    # Generate source maps for every generated object.
    generate_source_map: bool = True
    # Words that mark a recipe ingredient as the cutting tool.
    sliceboard_tool_keywords: list[str] = field(
        default_factory=lambda: ["knife", "cleaver", "sword", "scissors", "blade"]
    )
    # CraftEngine block ids acting as cutting boards (for config.yml generation).
    sliceboard_block_ids: list[str] = field(default_factory=list)
    # Output layout: "split" = one file per object grouped in category folders
    # (items/blocks/recipes/crops/food/...); "single" = everything in one file.
    output_layout: str = "split"
    # --- micro neural network ("brain") ---
    # Classifies every detected object (what it is, which block representation
    # it needs) and emits in-game check commands into the reports.
    brain_enabled: bool = True
    # Minimum softmax confidence before a prediction is applied to the IR.
    brain_min_confidence: float = 0.6
    brain_block_min_confidence: float = 0.7
    # Log one line per classified object during conversion.
    brain_log_every_object: bool = True
    # Let the brain assign a category when tags/recipe hints are absent.
    brain_categories: bool = True
    # Let the brain mark food candidates missed by the keyword list.
    brain_food_detection: bool = True

    # --- automatic bytecode semantic reconstruction ---
    bytecode_semantic_enabled: bool = True
    gear_enabled: bool = True
    gear_tool_keywords: list[str] = field(default_factory=lambda: ["pickaxe","axe","shovel","hoe","shears","wrench"])
    gear_weapon_keywords: list[str] = field(default_factory=lambda: ["sword","mace","dagger","hammer","club","rapier","katana","greatsword"])
    gear_spear_keywords: list[str] = field(default_factory=lambda: ["spear","lance","javelin","halberd","glaive","pike"])
    gear_ranged_keywords: list[str] = field(default_factory=lambda: ["bow","crossbow"])
    gear_shield_keywords: list[str] = field(default_factory=lambda: ["shield","buckler"])
    armor_keywords: list[str] = field(default_factory=lambda: ["helmet","chestplate","leggings","boots","armor","armour"])
    reconstruct_block_behaviors: bool = True
    reconstruct_item_components: bool = True
    reconstruct_loot_from_datagen: bool = True
    reconstruct_events_from_bytecode: bool = True
    conservative_phantom_filter: bool = True
    # When a 3D/world/hand model has a dedicated 2D item icon, route only the
    # GUI display context to that icon instead of creating a second item.
    gui_icon_enabled: bool = True
    gui_icon_suffixes: list[str] = field(default_factory=lambda: ["_icon", "_inventory", "_gui"])
    gui_icon_prefer_exact_item_texture: bool = True
    # --- crop reconstruction ---
    crop_reconstruction_enabled: bool = True
    crop_default_grow_speed: float = 0.5
    crop_light_requirement: int = 9
    crop_default_hardness: float = 0.0
    crop_default_resistance: float = 0.0
    crop_generate_loot_when_missing: bool = True
    crop_seed_relation_from_bytecode: bool = True
    crop_seed_name_suffixes: list[str] = field(default_factory=lambda: ["_seeds", "_seed"])
    # Food handling: emit baseline data.food + consumable for ordinary items
    # whose id matches a keyword below. Values are baselines (edit manually).
    food_enabled: bool = True
    bytecode_food_enabled: bool = True
    bytecode_food_strict: bool = False
    food_nutrition: int = 4
    food_saturation: float = 3.0
    # Carrier materials are only emitted for semantic vanilla families.
    drink_material: str = "honey_bottle"
    soup_material: str = "mushroom_stew"
    drink_keywords: list[str] = field(default_factory=lambda: [
        "drink", "juice", "cider", "tea", "coffee", "milk", "water",
        "syrup", "nectar", "smoothie", "lemonade", "soda", "wine",
        "mead", "broth", "latte", "cocoa", "kombucha", "tonic",
    ])
    soup_keywords: list[str] = field(default_factory=lambda: [
        "soup", "stew", "chowder", "broth", "bisque", "hotpot",
    ])
    food_keywords: list[str] = field(
        default_factory=lambda: [
            "soup", "salad", "stew", "sandwich", "burger", "wrap", "pie",
            "cake", "brownie", "noodle", "pancake", "cupcake", "patty",
            "pizza", "skewer", "bread", "slaw", "mushroom", "juice", "water",
            "honey", "chip", "rice", "pasta", "mhadjeb",
        ]
    )

    # --- source adapter selection ---
    # "auto" inspects the input and picks the mod or ItemsAdder adapter;
    # "mod" / "itemsadder" force one of them.
    source_mode: str = "auto"

    # --- ItemsAdder import ---
    # ItemsAdder's resource.material is semantic (it selects the vanilla item
    # the custom item is built on), so unlike mod conversion it is preserved.
    ia_preserve_material: bool = True
    # CraftEngine has no "immune to explosions" flag; this resistance value is
    # used for ItemsAdder `no_explosion: true` blocks (bedrock-grade).
    ia_explosion_immune_resistance: float = 3600000.0
    # ItemsAdder assigns custom_model_data itself. CraftEngine assigns model
    # ids too, so source values are recorded but not forced unless enabled.
    ia_force_custom_model_data: bool = False
    ia_generate_furniture: bool = True
    # Locale used to resolve ItemsAdder translation keys into display text.
    ia_default_locale: str = "en"
    # Emit vanilla `consumable` sub-fields (consume_seconds, sound, animation)
    # when the source pack defines them.
    ia_emit_consumable_details: bool = True

    # --- resource pack source (models/ + textures/ -> configs) -------------
    # A resource pack carries no material, so every generated item needs one.
    rp_default_material: str = "nether_brick"
    # assets/minecraft/ overrides change vanilla items rather than adding
    # content; by default they are reported, not minted into CraftEngine items.
    rp_skip_vanilla_overrides: bool = True

    def station_for(self, recipe_type: str) -> str:
        if recipe_type in self.recipe_stations:
            return self.recipe_stations[recipe_type]
        short = recipe_type.split(":")[-1] if ":" in recipe_type else recipe_type
        return self._default_station_for_short(short)

    @staticmethod
    def _default_station_for_short(short: str) -> str:
        vanilla = {
            "crafting_shapeless": "crafting_table",
            "crafting_shaped": "crafting_table",
            "smelting": "furnace",
            "blasting": "blast_furnace",
            "smoking": "smoker",
            "campfire_cooking": "campfire",
            "stonecutting": "stonecutter",
            "smithing_transform": "smithing_table",
            "smithing_trim": "smithing_table",
        }
        return vanilla.get(short, "unknown")

    def effective_station(self, recipe_type: str) -> str:
        """Return the target station for a recipe type, applying remap rules."""
        station = self.station_for(recipe_type)
        if station == "unknown" and self.remap_unknown_stations:
            return self.remap_unknown_station_target
        return station

    def container_for(self, recipe_type: str) -> str | None:
        if not self.include_container_ingredient:
            return None
        if recipe_type in self.container_items:
            return self.container_items[recipe_type]
        ns = recipe_type.split(":")[0] if ":" in recipe_type else recipe_type
        return self.container_items.get(ns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "craftengine_version": self.craftengine_version,
            "minecraft_version": self.minecraft_version,
            "item_material_fallback": self.item_material_fallback,
            "emit_item_material": self.emit_item_material,
            "emit_food_apple_material": self.emit_food_apple_material,
            "generate_categories": self.generate_categories,
            "generate_category_translations": self.generate_category_translations,
            "organize_recipes": self.organize_recipes,
            "recipe_sort_mode": self.recipe_sort_mode,
            "log_level": self.log_level,
            "write_conversion_log": self.write_conversion_log,
            "copy_all_assets": self.copy_all_assets,
            "validate_resource_links": self.validate_resource_links,
            "block_auto_state": self.block_auto_state,
            "recipe_stations": self.recipe_stations,
            "remap_unknown_stations": self.remap_unknown_stations,
            "remap_unknown_station_target": self.remap_unknown_station_target,
            "include_container_ingredient": self.include_container_ingredient,
            "container_items": self.container_items,
            "sliceboard_enabled": self.sliceboard_enabled,
            "sliceboard_custom_provider": self.sliceboard_custom_provider,
            "sliceboard_namespace_prefixes": self.sliceboard_namespace_prefixes,
            "excluded_recipe_namespaces": self.excluded_recipe_namespaces,
            "interactive_namespace_mapping": self.interactive_namespace_mapping,
            "strict_recipe_mode": self.strict_recipe_mode,
            "unresolved_recipe_tag_policy": self.unresolved_recipe_tag_policy,
            "preserve_unsupported_recipes": self.preserve_unsupported_recipes,
            "generate_source_map": self.generate_source_map,
            "sliceboard_tool_keywords": self.sliceboard_tool_keywords,
            "sliceboard_block_ids": self.sliceboard_block_ids,
            "output_layout": self.output_layout,
            "food_enabled": self.food_enabled,
            "bytecode_food_enabled": self.bytecode_food_enabled,
            "bytecode_food_strict": self.bytecode_food_strict,
            "food_nutrition": self.food_nutrition,
            "food_saturation": self.food_saturation,
            "drink_material": self.drink_material,
            "soup_material": self.soup_material,
            "drink_keywords": self.drink_keywords,
            "soup_keywords": self.soup_keywords,
            "gear_enabled": self.gear_enabled,
            "gear_tool_keywords": self.gear_tool_keywords,
            "gear_weapon_keywords": self.gear_weapon_keywords,
            "gear_spear_keywords": self.gear_spear_keywords,
            "gear_ranged_keywords": self.gear_ranged_keywords,
            "gear_shield_keywords": self.gear_shield_keywords,
            "armor_keywords": self.armor_keywords,
            "bytecode_semantic_enabled": self.bytecode_semantic_enabled,
            "reconstruct_block_behaviors": self.reconstruct_block_behaviors,
            "reconstruct_item_components": self.reconstruct_item_components,
            "reconstruct_loot_from_datagen": self.reconstruct_loot_from_datagen,
            "reconstruct_events_from_bytecode": self.reconstruct_events_from_bytecode,
            "conservative_phantom_filter": self.conservative_phantom_filter,
            "gui_icon_enabled": self.gui_icon_enabled,
            "gui_icon_suffixes": self.gui_icon_suffixes,
            "gui_icon_prefer_exact_item_texture": self.gui_icon_prefer_exact_item_texture,
            "crop_reconstruction_enabled": self.crop_reconstruction_enabled,
            "crop_default_grow_speed": self.crop_default_grow_speed,
            "crop_light_requirement": self.crop_light_requirement,
            "crop_default_hardness": self.crop_default_hardness,
            "crop_default_resistance": self.crop_default_resistance,
            "crop_generate_loot_when_missing": self.crop_generate_loot_when_missing,
            "crop_seed_relation_from_bytecode": self.crop_seed_relation_from_bytecode,
            "crop_seed_name_suffixes": self.crop_seed_name_suffixes,
            "food_keywords": self.food_keywords,
            "source_mode": self.source_mode,
            "ia_preserve_material": self.ia_preserve_material,
            "ia_explosion_immune_resistance": self.ia_explosion_immune_resistance,
            "ia_force_custom_model_data": self.ia_force_custom_model_data,
            "ia_generate_furniture": self.ia_generate_furniture,
            "ia_default_locale": self.ia_default_locale,
            "ia_emit_consumable_details": self.ia_emit_consumable_details,
            "rp_default_material": self.rp_default_material,
            "rp_skip_vanilla_overrides": self.rp_skip_vanilla_overrides,
        }


DEFAULTS = Settings()


def _as_settings(data: Any) -> Settings:
    if not isinstance(data, dict):
        return Settings()
    s = Settings()
    s.craftengine_version = str(data.get("craftengine_version", s.craftengine_version))
    s.minecraft_version = str(data.get("minecraft_version", s.minecraft_version))
    s.item_material_fallback = str(data.get("item_material_fallback", s.item_material_fallback))
    s.emit_item_material = bool(data.get("emit_item_material", s.emit_item_material))
    s.emit_food_apple_material = bool(data.get("emit_food_apple_material", s.emit_food_apple_material))
    s.generate_categories = bool(data.get("generate_categories", s.generate_categories))
    s.generate_category_translations = bool(data.get("generate_category_translations", s.generate_category_translations))
    s.organize_recipes = bool(data.get("organize_recipes", s.organize_recipes))
    s.recipe_sort_mode = str(data.get("recipe_sort_mode", s.recipe_sort_mode))
    s.log_level = str(data.get("log_level", s.log_level)).upper()
    s.write_conversion_log = bool(data.get("write_conversion_log", s.write_conversion_log))
    s.copy_all_assets = bool(data.get("copy_all_assets", s.copy_all_assets))
    s.validate_resource_links = bool(data.get("validate_resource_links", s.validate_resource_links))
    s.block_auto_state = str(data.get("block_auto_state", s.block_auto_state))
    if isinstance(data.get("recipe_stations"), dict):
        user_stations = {str(k): str(v) for k, v in data["recipe_stations"].items()}
        s.recipe_stations = {**KNOWN_RECIPE_STATIONS, **user_stations}
    s.remap_unknown_stations = bool(data.get("remap_unknown_stations", s.remap_unknown_stations))
    s.remap_unknown_station_target = str(data.get("remap_unknown_station_target", s.remap_unknown_station_target))
    s.include_container_ingredient = bool(data.get("include_container_ingredient", s.include_container_ingredient))
    if isinstance(data.get("container_items"), dict):
        user_containers = {str(k): str(v) for k, v in data["container_items"].items()}
        s.container_items = {
            "farmersdelight:cooking": "farmersdelight:cooking_pot",
            "farmersdelight:cutting": "farmersdelight:cutting_board",
            **user_containers,
        }
    s.sliceboard_enabled = bool(data.get("sliceboard_enabled", s.sliceboard_enabled))
    s.sliceboard_custom_provider = str(data.get("sliceboard_custom_provider", s.sliceboard_custom_provider))
    if isinstance(data.get("sliceboard_namespace_prefixes"), dict):
        s.sliceboard_namespace_prefixes = {str(k): str(v) for k, v in data["sliceboard_namespace_prefixes"].items()}
    if isinstance(data.get("excluded_recipe_namespaces"), list):
        s.excluded_recipe_namespaces = [str(x).strip() for x in data["excluded_recipe_namespaces"] if str(x).strip()]
    s.interactive_namespace_mapping = bool(data.get("interactive_namespace_mapping", s.interactive_namespace_mapping))
    s.unresolved_recipe_tag_policy = str(data.get("unresolved_recipe_tag_policy", s.unresolved_recipe_tag_policy))
    s.strict_recipe_mode = bool(data.get("strict_recipe_mode", s.strict_recipe_mode))
    s.preserve_unsupported_recipes = bool(data.get("preserve_unsupported_recipes", s.preserve_unsupported_recipes))
    s.generate_source_map = bool(data.get("generate_source_map", s.generate_source_map))
    if isinstance(data.get("sliceboard_tool_keywords"), list):
        s.sliceboard_tool_keywords = [str(x) for x in data["sliceboard_tool_keywords"]]
    if isinstance(data.get("sliceboard_block_ids"), list):
        s.sliceboard_block_ids = [str(x) for x in data["sliceboard_block_ids"]]
    s.output_layout = str(data.get("output_layout", s.output_layout))
    s.food_enabled = bool(data.get("food_enabled", s.food_enabled))
    s.bytecode_food_enabled = bool(data.get("bytecode_food_enabled", s.bytecode_food_enabled))
    s.bytecode_food_strict = bool(data.get("bytecode_food_strict", s.bytecode_food_strict))
    s.food_nutrition = int(data.get("food_nutrition", s.food_nutrition))
    s.food_saturation = float(data.get("food_saturation", s.food_saturation))
    s.drink_material = str(data.get("drink_material", s.drink_material))
    s.soup_material = str(data.get("soup_material", s.soup_material))
    if isinstance(data.get("drink_keywords"), list):
        s.drink_keywords = [str(x) for x in data["drink_keywords"]]
    if isinstance(data.get("soup_keywords"), list):
        s.soup_keywords = [str(x) for x in data["soup_keywords"]]
    s.gear_enabled = bool(data.get("gear_enabled", s.gear_enabled))
    for attr in ("gear_tool_keywords","gear_weapon_keywords","gear_spear_keywords","gear_ranged_keywords","gear_shield_keywords","armor_keywords"):
        if isinstance(data.get(attr), list): setattr(s, attr, [str(x) for x in data[attr]])
    s.bytecode_semantic_enabled = bool(data.get("bytecode_semantic_enabled", s.bytecode_semantic_enabled))
    s.reconstruct_block_behaviors = bool(data.get("reconstruct_block_behaviors", s.reconstruct_block_behaviors))
    s.reconstruct_item_components = bool(data.get("reconstruct_item_components", s.reconstruct_item_components))
    s.reconstruct_loot_from_datagen = bool(data.get("reconstruct_loot_from_datagen", s.reconstruct_loot_from_datagen))
    s.reconstruct_events_from_bytecode = bool(data.get("reconstruct_events_from_bytecode", s.reconstruct_events_from_bytecode))
    s.conservative_phantom_filter = bool(data.get("conservative_phantom_filter", s.conservative_phantom_filter))
    s.gui_icon_enabled = bool(data.get("gui_icon_enabled", s.gui_icon_enabled))
    if isinstance(data.get("gui_icon_suffixes"), list):
        s.gui_icon_suffixes = [str(x) for x in data["gui_icon_suffixes"]]
    s.gui_icon_prefer_exact_item_texture = bool(data.get("gui_icon_prefer_exact_item_texture", s.gui_icon_prefer_exact_item_texture))
    s.crop_reconstruction_enabled = bool(data.get("crop_reconstruction_enabled", s.crop_reconstruction_enabled))
    s.crop_default_grow_speed = float(data.get("crop_default_grow_speed", s.crop_default_grow_speed))
    s.crop_light_requirement = int(data.get("crop_light_requirement", s.crop_light_requirement))
    s.crop_default_hardness = float(data.get("crop_default_hardness", s.crop_default_hardness))
    s.crop_default_resistance = float(data.get("crop_default_resistance", s.crop_default_resistance))
    s.crop_generate_loot_when_missing = bool(data.get("crop_generate_loot_when_missing", s.crop_generate_loot_when_missing))
    s.crop_seed_relation_from_bytecode = bool(data.get("crop_seed_relation_from_bytecode", s.crop_seed_relation_from_bytecode))
    if isinstance(data.get("crop_seed_name_suffixes"), list):
        s.crop_seed_name_suffixes = [str(x) for x in data["crop_seed_name_suffixes"]]
    if isinstance(data.get("food_keywords"), list):
        s.food_keywords = [str(x) for x in data["food_keywords"]]
    s.source_mode = str(data.get("source_mode", s.source_mode)).lower()
    if s.source_mode not in ("auto", "mod", "itemsadder", "resourcepack"):
        s.source_mode = "auto"
    s.ia_preserve_material = bool(data.get("ia_preserve_material", s.ia_preserve_material))
    s.ia_explosion_immune_resistance = float(data.get("ia_explosion_immune_resistance", s.ia_explosion_immune_resistance))
    s.ia_force_custom_model_data = bool(data.get("ia_force_custom_model_data", s.ia_force_custom_model_data))
    s.ia_generate_furniture = bool(data.get("ia_generate_furniture", s.ia_generate_furniture))
    s.ia_default_locale = str(data.get("ia_default_locale", s.ia_default_locale))
    s.ia_emit_consumable_details = bool(data.get("ia_emit_consumable_details", s.ia_emit_consumable_details))
    s.rp_default_material = str(data.get("rp_default_material", s.rp_default_material))
    s.rp_skip_vanilla_overrides = bool(data.get("rp_skip_vanilla_overrides", s.rp_skip_vanilla_overrides))
    return s


class SettingsManager:
    def __init__(self, path: Path | None = None, log: Log | None = None) -> None:
        self.path = path or paths.settings_file()
        self.log = log or Log()
        self.settings = self.load()

    def load(self) -> Settings:
        if self.path.exists():
            try:
                data = util.read_yaml(self.path)
                return _as_settings(data)
            except Exception as exc:  # noqa: BLE001 — settings boundary
                self.log.error("failed to load settings; using defaults", path=str(self.path), error=str(exc))
                return Settings()
        return Settings()

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        util.write_yaml_ordered(self.path, settings.to_dict())
        self.settings = settings

    def ensure_default_file(self) -> Path:
        if not self.path.exists():
            self.save(Settings())
        return self.path


_CACHE: SettingsManager | None = None


def get_settings(path: Path | None = None) -> Settings:
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE.settings
    mgr = SettingsManager(path)
    if path is None:
        _CACHE = mgr
    return mgr.settings


def load_settings(path: Path | None = None, log: Log | None = None) -> SettingsManager:
    return SettingsManager(path, log)