"""Training-set synthesis for the micro neural network.

There is no public corpus of "mod item name -> CraftEngine semantics", so the
training set is generated from a curated vocabulary of Minecraft / modded
naming conventions combined with realistic structured features. Each sample is
(name, struct, labels); the generator applies prefix/suffix/material noise so
the network learns morphology instead of memorizing exact strings.

Run ``python -m converter.brain.train`` to regenerate the weights.
"""

from __future__ import annotations

import random
from typing import Any, Iterator

from . import features, labels

# --- vocabulary -------------------------------------------------------------

MATERIALS = [
    "wooden", "stone", "copper", "iron", "golden", "diamond", "netherite", "flint",
    "obsidian", "amethyst", "bone", "emerald", "steel", "bronze", "silver", "ruby",
    "crimson", "warped", "oak", "birch", "spruce", "jungle", "acacia", "cherry",
    "bamboo", "mangrove", "dark_oak", "blackstone", "deepslate", "quartz",
]

TIER_WORDS = {
    "wood": ["wooden", "wood", "oak", "birch", "spruce", "bamboo", "jungle"],
    "stone": ["stone", "cobblestone", "flint", "deepslate", "blackstone", "andesite"],
    "copper": ["copper", "bronze"],
    "iron": ["iron", "steel"],
    "gold": ["golden", "gold"],
    "diamond": ["diamond", "emerald", "amethyst"],
    "netherite": ["netherite", "obsidian", "ancient"],
}

TOOLS = ["pickaxe", "axe", "shovel", "spade", "hoe", "shears", "wrench", "hammer_tool", "sickle", "chisel", "trowel", "mattock", "drill", "saw", "pliers"]
WEAPONS = ["sword", "dagger", "katana", "greatsword", "rapier", "mace", "club", "cleaver", "knife", "scimitar", "sabre", "warhammer", "battleaxe", "cutlass", "machete"]
RANGED = ["bow", "longbow", "shortbow", "recurve_bow", "crossbow", "heavy_crossbow", "repeating_crossbow", "hunting_bow"]
SPEARS = ["spear", "lance", "javelin", "halberd", "glaive", "pike", "naginata", "harpoon"]
TRIDENTS = ["trident", "storm_trident", "abyssal_trident", "tidal_trident"]
SHIELDS = ["shield", "buckler", "tower_shield", "kite_shield", "round_shield"]
ARMOR = ["helmet", "chestplate", "leggings", "boots", "cap", "tunic", "pants", "greaves", "gauntlets", "cuirass", "hood", "chainmail_helmet"]

DRINKS = ["juice", "cider", "tea", "coffee", "milk", "syrup", "nectar", "smoothie", "lemonade", "soda", "wine", "mead", "latte", "cocoa", "kombucha", "tonic", "ale", "brew", "shake", "elixir", "cocktail", "punch"]
SOUPS = ["soup", "stew", "chowder", "broth", "bisque", "hotpot", "gumbo", "ramen", "goulash", "porridge", "curry"]
SWEETS = ["cake", "cookie", "pie", "brownie", "cupcake", "muffin", "donut", "pudding", "custard", "popsicle", "ice_cream", "candy", "truffle", "cheesecake", "tart", "jelly", "marshmallow", "waffle", "eclair"]
MEALS = ["sandwich", "burger", "wrap", "pizza", "pasta", "noodles", "salad", "skewer", "roast", "dinner", "platter", "bowl_meal", "casserole", "stir_fry", "bento", "kebab", "taco", "burrito", "omelette", "risotto"]
FOODS = ["bread", "cheese", "jerky", "bacon", "ham", "steak", "fillet", "cutlet", "nugget", "pancake", "toast", "dumpling", "pretzel", "cracker", "chips", "fries", "biscuit", "sausage", "pastry", "roll", "teriyaki", "wheel", "loaf", "bun", "wrap_food", "fritter", "croquette", "meatball", "rib", "wing", "drumstick", "sushi", "onigiri", "tempura", "gyoza", "quiche", "souffle", "terrine", "confit", "kimchi", "pickles", "preserve", "jam", "butter", "yogurt", "curd", "tofu_block_food", "egg_dish", "porridge_bowl"]

CROPS = ["cabbage", "tomato", "onion", "rice", "carrot", "potato", "wheat", "barley", "corn", "lettuce", "pepper", "cucumber", "zucchini", "eggplant", "pumpkin", "melon", "garlic", "ginger", "beet", "radish", "spinach", "peanut", "soybean", "grape", "strawberry", "blueberry"]
CROP_SUFFIX = ["seeds", "seed", "crop", "sapling", "sprout", "bush", "vine", "panicle", "stalk", "wild_plant"]

INGREDIENTS = ["dough", "flour", "slice", "slices", "minced_meat", "patty", "paste", "powder", "extract", "essence", "shard", "ingot", "nugget_metal", "plate_metal", "rod", "gear", "bolt", "fiber", "canvas", "straw", "bark", "resin", "wax", "dye", "pulp", "chunk", "cut", "strip", "shreds", "crumbs"]

BLOCK_ITEMS = ["block", "bricks", "tiles", "planks", "log", "stairs", "slab", "fence", "wall", "door", "trapdoor", "pillar", "crate", "barrel", "pot", "lamp", "lantern", "table", "chair", "stool", "bench", "shelf", "counter", "sign"]
CABINETS = ["cabinet", "cupboard", "wardrobe", "locker", "drawer", "chest_cabinet", "pantry", "dresser", "sideboard"]

MISC_ITEMS = ["coin", "token", "key", "map_piece", "compass_part", "book", "scroll", "charm", "amulet", "ring", "badge", "ticket", "core", "battery", "circuit", "module", "canister", "flask", "bottle_empty", "bucket_empty", "net", "rope", "bandage", "manual", "orb", "talisman", "sigil", "rune", "totem", "relic", "idol", "medallion", "lens_item", "prism", "catalyst", "upgrade", "blueprint", "schematic", "fuse", "spark", "crystal_item", "feather_item", "horn", "whistle", "lantern_item", "banner_item", "trophy"]

# --- block vocabulary -------------------------------------------------------

SOLID_BLOCKS = ["block", "bricks", "tiles", "planks", "stone_block", "concrete", "smooth_block", "polished_block", "ore", "deepslate_ore", "compressed_block", "storage_block", "cobbled_block"]
LOG_BLOCKS = ["log", "stem", "pillar", "wood", "stripped_log", "column", "beam", "bamboo_block"]
LEAVES_BLOCKS = ["leaves", "foliage", "canopy", "leaf_block", "hedge"]
PLANT_BLOCKS = ["flower", "sapling", "bush", "shrub", "fern", "grass_plant", "mushroom", "sprout", "weed", "herb", "cactus", "sugar_cane", "kelp", "vine", "moss_carpet"]
CROP_BLOCKS = [f"{c}_crop" for c in CROPS[:16]] + ["wheat_crop", "rice_paddy", "berry_bush_crop"]
THIN_BLOCKS = ["cutting_board", "tray", "plate_block", "pan", "carpet", "pressure_plate", "rug", "mat", "board", "basket", "pie_block", "cake_block", "bowl_block", "paper_wall"]
GLASS_BLOCKS = ["glass", "glass_pane", "stained_glass", "window", "ice", "crystal", "lens", "amethyst_glass"]
SLAB_BLOCKS = ["slab", "stairs", "fence", "wall", "fence_gate", "step", "ledge"]
MACHINE_BLOCKS = ["furnace", "generator", "crusher", "smelter", "assembler", "reactor", "tank", "pipe", "conveyor", "terminal", "controller", "press", "mixer", "kiln", "brewer", "cooking_pot", "grill", "stove", "oven"]
FURNITURE_BLOCKS = ["chair", "stool", "table", "bench", "shelf", "counter", "cabinet", "wardrobe", "lamp", "lantern", "candle_holder", "sofa", "desk", "clock_block", "vase", "statue", "sign_post", "curtain"]

PREFIXES = ["", "wild_", "ancient_", "royal_", "rustic_", "dark_", "enchanted_", "reinforced_", "primitive_", "advanced_", "arcane_", "frozen_", "molten_", "gilded_", "shadow_"]
NAMESPACES = ["mymod", "farmersdelight", "veggiesdelight", "createmod", "adventure", "tech", "cuisine", "artifacts", "nature", "industry"]


def _tier_for(name: str) -> str:
    low = name.lower()
    for tier, words in TIER_WORDS.items():
        if any(w in low for w in words):
            return tier
    return "none"


def _decorate(rng: random.Random, base: str, allow_material: bool = True, food_prefix: bool = False) -> str:
    """Compose a realistic modded id around ``base``.

    The *head noun* (``base``) must stay the decisive token: qualifiers are only
    ever prepended. Food-like objects get ingredient qualifiers (``pumpkin_``,
    ``tofu_``) because that is how mods actually name them, while gear/blocks
    get material qualifiers.
    """
    parts: list[str] = []
    if food_prefix and rng.random() < 0.6:
        parts.append(rng.choice(CROPS + ["tofu", "chicken", "beef", "pork", "fish", "salmon", "cheese", "honey", "chocolate", "mushroom"]))
    elif allow_material and rng.random() < 0.55:
        parts.append(rng.choice(MATERIALS))
    if rng.random() < 0.2:
        adjective = rng.choice(PREFIXES).strip("_")
        if adjective:
            parts.insert(0, adjective)
    parts.append(base)
    name = "_".join(p for p in parts if p)
    return f"{rng.choice(NAMESPACES)}:{name}"


# --- item sample generation -------------------------------------------------


def _item_sample(rng: random.Random, category: str, base: str) -> tuple[str, dict[str, float], dict[str, str]]:
    gear = "none"
    food = "none"
    struct: dict[str, float] = {}
    allow_material = True

    if category == "Tools":
        gear = "tool"
    elif category == "Weapons":
        gear = "weapon"
    elif category == "Ranged":
        gear = "crossbow" if "crossbow" in base else "bow"
    elif category == "Spears":
        gear = "spear"
    elif category == "Tridents":
        gear = "trident"
    elif category == "Shields":
        gear = "shield"
    elif category == "Armor":
        gear = "armor"

    if category == "Drinks":
        food, allow_material = "drink", False
    elif category == "Meals":
        food, allow_material = ("soup" if base in SOUPS else "food"), False
    elif category == "Sweets":
        food, allow_material = "sweet", False
    elif category == "Food":
        food, allow_material = "food", False

    # 15% of non-food objects also get an edible-sounding qualifier
    # (e.g. "pumpkin_hammer"): a hard negative that forces the head noun, not
    # the qualifier, to decide the category.
    food_prefix = food != "none" or (category == "Crops") or rng.random() < 0.15
    name = _decorate(rng, base, allow_material, food_prefix=food_prefix and category != "Crops")

    # Structured signals correlated with the label, plus realistic noise.
    if gear in ("tool", "weapon", "spear", "trident"):
        struct["has_durability"] = 1.0
        struct["has_attack_damage"] = 1.0 if gear != "tool" else float(rng.random() < 0.4)
        struct["model_parent_handheld"] = float(rng.random() < 0.8)
        struct["has_max_stack_1"] = 1.0
        struct["tag_tools"] = 1.0 if gear == "tool" and rng.random() < 0.6 else 0.0
        struct["tag_weapons"] = 1.0 if gear == "weapon" and rng.random() < 0.6 else 0.0
    if gear == "armor":
        struct.update({"has_durability": 1.0, "has_equippable": 1.0, "has_max_stack_1": 1.0})
        struct["tag_armor"] = float(rng.random() < 0.6)
    if gear in ("bow", "crossbow", "shield"):
        struct.update({"has_durability": 1.0, "has_max_stack_1": 1.0})
    if food != "none":
        struct["has_food_component"] = float(rng.random() < 0.75)
        struct["has_consumable_component"] = float(rng.random() < 0.6)
        struct["tag_food"] = float(rng.random() < 0.5)
        struct["model_parent_generated"] = float(rng.random() < 0.85)
        if food in ("drink", "soup"):
            struct["has_use_remainder"] = float(rng.random() < 0.7)
            struct["has_max_stack_1"] = float(rng.random() < 0.6)
    if category in ("Blocks", "Cabinets"):
        struct["has_block_binding"] = 1.0
        struct["has_3d_model"] = float(rng.random() < 0.7)
        struct["hint_building"] = float(rng.random() < 0.5)
    if category == "Crops":
        struct["tag_crops"] = float(rng.random() < 0.6)
        struct["model_parent_generated"] = float(rng.random() < 0.9)
    if category == "Ingredients":
        struct["tag_ingredients"] = float(rng.random() < 0.4)
        struct["model_parent_generated"] = float(rng.random() < 0.9)

    struct["texture_count_log"] = features.log1p_scaled(rng.randint(1, 3))
    struct["name_word_count"] = features.log1p_scaled(len(features.tokenize(name)), 5)
    struct["name_length_log"] = features.log1p_scaled(len(name), 32)
    struct["from_recipe_output"] = float(rng.random() < 0.5)

    tier = _tier_for(name) if gear in ("tool", "weapon", "spear", "armor") else "none"
    tier_index = {"wood": 0.15, "stone": 0.3, "copper": 0.4, "iron": 0.55, "gold": 0.7, "diamond": 0.85, "netherite": 1.0}
    struct["tier_hint"] = tier_index.get(tier, 0.0) * (1.0 if rng.random() < 0.8 else 0.0)

    return name, struct, {"category": category, "gear_kind": gear, "food_family": food, "tool_tier": tier}


ITEM_GROUPS: list[tuple[str, list[str]]] = [
    ("Tools", TOOLS),
    ("Weapons", WEAPONS),
    ("Ranged", RANGED),
    ("Spears", SPEARS),
    ("Tridents", TRIDENTS),
    ("Shields", SHIELDS),
    ("Armor", ARMOR),
    ("Drinks", DRINKS),
    ("Meals", SOUPS + MEALS),
    ("Sweets", SWEETS),
    ("Food", FOODS),
    ("Crops", [f"{c}_{s}" for c in CROPS for s in CROP_SUFFIX[:4]][:120] + CROPS),
    ("Ingredients", INGREDIENTS),
    ("Blocks", BLOCK_ITEMS),
    ("Cabinets", CABINETS),
    ("Items", MISC_ITEMS),
]


_SYLLABLES = ["zor", "kel", "vun", "mip", "tra", "quo", "blen", "shu", "grim", "wid", "nal", "phe", "rus", "tik", "oben", "xar", "yul", "dro", "sev", "lum"]


def _nonsense_word(rng: random.Random) -> str:
    return "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 3)))


def _ood_item_sample(rng: random.Random) -> tuple[str, dict[str, float], dict[str, str]]:
    """An id whose head noun carries no known meaning.

    These teach the network the honest answer for unknown vocabulary — a
    generic item with no gear kind and no food family — instead of confidently
    matching a random n-gram neighbour.
    """
    name = f"{rng.choice(NAMESPACES)}:{_nonsense_word(rng)}"
    if rng.random() < 0.4:
        name = f"{name}_{_nonsense_word(rng)}"
    struct = {
        "texture_count_log": features.log1p_scaled(1),
        "name_word_count": features.log1p_scaled(len(features.tokenize(name)), 5),
        "name_length_log": features.log1p_scaled(len(name), 32),
    }
    return name, struct, {"category": "Items", "gear_kind": "none", "food_family": "none", "tool_tier": "none"}


# Share of synthesized samples with unknown vocabulary.
OOD_RATIO = 0.12


def generate_item_samples(count: int, seed: int = 20260906) -> Iterator[tuple[str, dict[str, float], dict[str, str]]]:
    rng = random.Random(seed)
    groups = ITEM_GROUPS
    for i in range(count):
        if rng.random() < OOD_RATIO:
            yield _ood_item_sample(rng)
            continue
        category, bases = groups[i % len(groups)]
        yield _item_sample(rng, category, rng.choice(bases))


# --- block sample generation ------------------------------------------------

BLOCK_GROUPS: list[tuple[str, list[str]]] = [
    ("solid", SOLID_BLOCKS),
    ("log", LOG_BLOCKS),
    ("leaves", LEAVES_BLOCKS),
    ("plant", PLANT_BLOCKS),
    ("crop", CROP_BLOCKS),
    ("thin", THIN_BLOCKS),
    ("glass", GLASS_BLOCKS),
    ("slab_like", SLAB_BLOCKS),
    ("machine", MACHINE_BLOCKS),
    ("furniture", FURNITURE_BLOCKS),
]

# Canonical CraftEngine representation per detected block kind.
KIND_TO_AUTO_STATE: dict[str, str] = {
    "solid": "note_block",
    "log": "note_block",
    "leaves": "leaves",
    "plant": "sapling",
    "crop": "higher_tripwire",
    "thin": "lower_tripwire",
    "glass": "note_block",
    "slab_like": "note_block",
    "machine": "note_block",
    "furniture": "lower_tripwire",
}

KIND_TRANSPARENT = {"leaves", "plant", "crop", "thin", "glass", "furniture"}
KIND_ENTITY = {"thin", "furniture", "crop"}


def _block_sample(rng: random.Random, kind: str, base: str) -> tuple[str, dict[str, float], dict[str, str]]:
    name = _decorate(rng, base, allow_material=kind in ("solid", "log", "slab_like", "glass", "furniture", "thin"))
    struct: dict[str, float] = {}

    if kind == "crop":
        struct.update({"has_age_property": 1.0, "render_cutout": 1.0, "parent_cross": float(rng.random() < 0.7),
                       "state_count_log": features.log1p_scaled(1, 6),
                       "variant_count_log": features.log1p_scaled(rng.randint(4, 8), 16),
                       "tag_crops": float(rng.random() < 0.6)})
    elif kind == "plant":
        struct.update({"render_cutout": 1.0, "parent_cross": float(rng.random() < 0.8),
                       "hardness_log": 0.0})
    elif kind == "leaves":
        struct.update({"render_cutout": 1.0, "parent_cube_all": float(rng.random() < 0.7),
                       "tag_leaves": float(rng.random() < 0.7), "tag_mineable_axe": float(rng.random() < 0.3)})
    elif kind == "glass":
        struct.update({"render_translucent": float(rng.random() < 0.6), "render_cutout": float(rng.random() < 0.4),
                       "parent_cube_all": float(rng.random() < 0.6)})
    elif kind == "thin":
        struct.update({"model_non_full_cube": 1.0, "model_element_count_log": features.log1p_scaled(rng.randint(1, 6), 12),
                       "render_cutout": float(rng.random() < 0.5), "has_facing_property": float(rng.random() < 0.6)})
    elif kind == "furniture":
        struct.update({"model_non_full_cube": 1.0, "model_has_rotation": float(rng.random() < 0.5),
                       "has_facing_property": 1.0,
                       "model_element_count_log": features.log1p_scaled(rng.randint(3, 12), 12)})
    elif kind == "machine":
        struct.update({"has_block_entity": 1.0, "has_facing_property": float(rng.random() < 0.7),
                       "has_powered_property": float(rng.random() < 0.5),
                       "parent_cube_all": float(rng.random() < 0.5),
                       "tag_mineable_pickaxe": float(rng.random() < 0.6)})
    elif kind == "slab_like":
        struct.update({"parent_slab_or_stairs": float("slab" in base or "stairs" in base),
                       "parent_fence_or_wall": float("fence" in base or "wall" in base),
                       "has_half_property": float(rng.random() < 0.7),
                       "model_non_full_cube": 1.0,
                       "is_multipart": float("fence" in base or "wall" in base)})
    elif kind == "log":
        struct.update({"has_axis_property": 1.0, "tag_logs": float(rng.random() < 0.6),
                       "tag_mineable_axe": float(rng.random() < 0.7), "parent_cube_all": float(rng.random() < 0.4)})
    else:  # solid
        struct.update({"parent_cube_all": float(rng.random() < 0.85),
                       "tag_mineable_pickaxe": float(rng.random() < 0.6),
                       "hardness_log": features.log1p_scaled(rng.uniform(1.0, 5.0), 10)})

    struct["has_block_item"] = float(rng.random() < 0.9)
    struct["has_loot_table"] = float(rng.random() < 0.8)
    struct["name_word_count"] = features.log1p_scaled(len(features.tokenize(name)), 5)
    struct["name_length_log"] = features.log1p_scaled(len(name), 32)

    return name, struct, {
        "block_kind": kind,
        "auto_state": KIND_TO_AUTO_STATE[kind],
        "transparent": "transparent" if kind in KIND_TRANSPARENT else "opaque",
        "entity_renderer": "entity" if kind in KIND_ENTITY else "native",
    }


def generate_block_samples(count: int, seed: int = 20260907) -> Iterator[tuple[str, dict[str, float], dict[str, str]]]:
    rng = random.Random(seed)
    for i in range(count):
        kind, bases = BLOCK_GROUPS[i % len(BLOCK_GROUPS)]
        yield _block_sample(rng, kind, rng.choice(bases))


def build_matrices(samples: Any, kind: str, seed: int = 1234) -> tuple[Any, dict[str, Any]]:
    """Vectorize samples into (X, {head: y}) NumPy arrays.

    Real mods rarely expose every structured signal, so each sample is emitted
    several times with different *feature dropout* masks:

    * full features (analyzer found everything),
    * name only (a bare jar with no models/tags/components),
    * partial structured features.

    Without this augmentation the network learns to rely exclusively on the
    structured block and collapses to a constant prediction whenever the
    analyzer could not fill it in.
    """
    import numpy as np

    rng = random.Random(seed)
    head_space = labels.ITEM_HEADS if kind == "item" else labels.BLOCK_HEADS
    vec_fn = features.item_vector if kind == "item" else features.block_vector
    struct_keys = features.ITEM_STRUCT_FEATURES if kind == "item" else features.BLOCK_STRUCT_FEATURES

    xs: list[Any] = []
    ys: dict[str, list[int]] = {head: [] for head in head_space}
    for name, struct, label_map in samples:
        variants = [struct, {}]
        partial = {k: v for k, v in struct.items() if rng.random() < 0.5}
        variants.append(partial)
        # Occasionally inject misleading structure so the model does not treat
        # any single flag as an absolute rule.
        noisy = dict(struct)
        noisy[rng.choice(struct_keys)] = 1.0
        variants.append(noisy)
        for variant in variants:
            xs.append(vec_fn(name, variant))
            for head, space in head_space.items():
                ys[head].append(space.index(label_map[head]))
    return np.stack(xs), {head: np.array(v, dtype=np.int64) for head, v in ys.items()}
