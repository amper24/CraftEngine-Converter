"""Label spaces for the micro neural network heads.

Order is part of the model contract: the index of a label is the index of the
output neuron. Never reorder — retrain instead.
"""

from __future__ import annotations

# --- item heads -------------------------------------------------------------

ITEM_CATEGORIES: tuple[str, ...] = (
    "Crops",
    "Ingredients",
    "Drinks",
    "Food",
    "Meals",
    "Sweets",
    "Tools",
    "Weapons",
    "Ranged",
    "Spears",
    "Tridents",
    "Shields",
    "Armor",
    "Blocks",
    "Cabinets",
    "Items",
)

GEAR_KINDS: tuple[str, ...] = (
    "none",
    "tool",
    "weapon",
    "bow",
    "crossbow",
    "trident",
    "spear",
    "shield",
    "armor",
)

FOOD_FAMILIES: tuple[str, ...] = ("none", "food", "drink", "soup", "sweet")

TOOL_TIERS: tuple[str, ...] = ("none", "wood", "stone", "copper", "iron", "gold", "diamond", "netherite")

# --- block heads ------------------------------------------------------------

BLOCK_KINDS: tuple[str, ...] = (
    "solid",
    "crop",
    "plant",
    "leaves",
    "thin",
    "glass",
    "log",
    "slab_like",
    "machine",
    "furniture",
)

AUTO_STATES: tuple[str, ...] = (
    "solid",
    "note_block",
    "leaves",
    "sapling",
    "cactus",
    "sugar_cane",
    "mushroom",
    "mushroom_stem",
    "kelp",
    "weeping_vine",
    "pressure_plate",
    "tripwire",
    "lower_tripwire",
    "higher_tripwire",
)

ITEM_HEADS: dict[str, tuple[str, ...]] = {
    "category": ITEM_CATEGORIES,
    "gear_kind": GEAR_KINDS,
    "food_family": FOOD_FAMILIES,
    "tool_tier": TOOL_TIERS,
}

BLOCK_HEADS: dict[str, tuple[str, ...]] = {
    "block_kind": BLOCK_KINDS,
    "auto_state": AUTO_STATES,
    "transparent": ("opaque", "transparent"),
    "entity_renderer": ("native", "entity"),
}

# Human-readable Russian descriptions used in the conversion log.
CATEGORY_RU: dict[str, str] = {
    "Crops": "растение / посев",
    "Ingredients": "ингредиент",
    "Drinks": "напиток",
    "Food": "еда",
    "Meals": "блюдо",
    "Sweets": "сладость",
    "Tools": "инструмент",
    "Weapons": "оружие",
    "Ranged": "дальнобойное оружие",
    "Spears": "копьё",
    "Tridents": "трезубец",
    "Shields": "щит",
    "Armor": "броня",
    "Blocks": "блок (предмет-блок)",
    "Cabinets": "шкаф / контейнер",
    "Items": "прочий предмет",
}

BLOCK_KIND_RU: dict[str, str] = {
    "solid": "сплошной блок",
    "crop": "грядка / растущая культура",
    "plant": "растение",
    "leaves": "листва",
    "thin": "тонкий/плоский блок",
    "glass": "прозрачный блок",
    "log": "бревно / столб",
    "slab_like": "плита / ступени / забор",
    "machine": "механизм (block entity)",
    "furniture": "мебель / декор",
}
