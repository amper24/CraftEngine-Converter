"""Feature extraction for the micro neural network.

Deterministic, dependency-free (besides NumPy) and stable across runs: the same
mod always produces the same vectors, therefore the same predictions and the
same generated YAML.

Layout of a feature vector::

    [0 : HASH_DIM)                 hashed character n-grams + word unigrams
    [HASH_DIM : HASH_DIM + N_STRUCT)  structured boolean/numeric signals

Hashing uses blake2b (stable across Python processes and versions, unlike the
randomized builtin ``hash``).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Sequence

import numpy as np

# The text block is split into three independent hash spaces so that a rare
# character n-gram can never collide with (and override) a decisive word.
# The head noun — the last word of the id, e.g. "pickaxe" in
# "mithril_pickaxe" — gets its own space because in Minecraft naming it is by
# far the strongest signal of what an object actually is.
WORD_DIM = 1024
HEAD_DIM = 512
CHAR_DIM = 512
HASH_DIM = WORD_DIM + HEAD_DIM + CHAR_DIM

NGRAM_MIN = 3
NGRAM_MAX = 5

_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# Structured features, in a fixed order. Never reorder: weights depend on it.
ITEM_STRUCT_FEATURES: tuple[str, ...] = (
    "has_food_component",
    "has_consumable_component",
    "has_block_binding",
    "has_durability",
    "has_attack_damage",
    "has_equippable",
    "has_enchantments",
    "is_display_helper",
    "has_stage_metadata",
    "has_3d_model",
    "model_parent_generated",
    "model_parent_handheld",
    "has_gui_icon",
    "texture_count_log",
    "tag_tools",
    "tag_weapons",
    "tag_armor",
    "tag_food",
    "tag_crops",
    "tag_ingredients",
    "hint_combat",
    "hint_food",
    "hint_building",
    "hint_tools",
    "hint_misc",
    "name_word_count",
    "name_length_log",
    "from_recipe_output",
    "has_max_stack_1",
    "has_use_remainder",
    "tier_hint",
    "namespace_is_minecraft",
)

BLOCK_STRUCT_FEATURES: tuple[str, ...] = (
    "has_age_property",
    "has_facing_property",
    "has_half_property",
    "has_axis_property",
    "has_waterlogged_property",
    "has_powered_property",
    "state_count_log",
    "variant_count_log",
    "is_multipart",
    "has_block_entity",
    "render_cutout",
    "render_translucent",
    "parent_cube_all",
    "parent_cross",
    "parent_slab_or_stairs",
    "parent_fence_or_wall",
    "model_non_full_cube",
    "model_has_rotation",
    "model_element_count_log",
    "has_loot_table",
    "has_block_item",
    "hardness_log",
    "name_word_count",
    "name_length_log",
    "tag_mineable_pickaxe",
    "tag_mineable_axe",
    "tag_mineable_shovel",
    "tag_leaves",
    "tag_logs",
    "tag_crops",
    "light_emission",
    "namespace_is_minecraft",
)

ITEM_DIM = HASH_DIM + len(ITEM_STRUCT_FEATURES)
BLOCK_DIM = HASH_DIM + len(BLOCK_STRUCT_FEATURES)


def _bucket(token: str, salt: str = "", dim: int = HASH_DIM) -> int:
    digest = hashlib.blake2b((salt + token).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def tokenize(name: str) -> list[str]:
    """Split ``some:mod_item_name`` into normalized word tokens."""
    path = name.split(":", 1)[-1].lower()
    return [t for t in _SPLIT_RE.split(path) if t]


def text_vector(name: str, extra_text: Iterable[str] = ()) -> np.ndarray:
    """Hashed text representation of an object id.

    Three independently normalized sub-vectors are concatenated:

    ``words``  every word of the id (plus optional display name), with prefix
               and suffix shingles so unseen vocabulary still generalizes;
    ``head``   the head noun only, weighted heavily;
    ``chars``  character n-grams of the whole id, for morphology.

    Each block is L2-normalized separately, so a long id cannot drown out the
    head noun and a rare n-gram cannot dominate a known word.
    """
    words = tokenize(name)
    head_words = list(words)
    for extra in extra_text:
        words.extend(tokenize(str(extra)))

    word_vec = np.zeros(WORD_DIM, dtype=np.float32)
    head_vec = np.zeros(HEAD_DIM, dtype=np.float32)
    char_vec = np.zeros(CHAR_DIM, dtype=np.float32)
    if not words:
        return np.concatenate([word_vec, head_vec, char_vec])

    for word in words:
        word_vec[_bucket(word, "w:", WORD_DIM)] += 1.0
        # Suffix/prefix signals generalize to unseen mod vocabulary.
        word_vec[_bucket(word[-4:], "sfx:", WORD_DIM)] += 0.5
        word_vec[_bucket(word[:4], "pre:", WORD_DIM)] += 0.5
    for a, b in zip(words, words[1:]):
        word_vec[_bucket(f"{a}_{b}", "b:", WORD_DIM)] += 0.75

    if head_words:
        head = head_words[-1]
        head_vec[_bucket(head, "h:", HEAD_DIM)] += 1.0
        head_vec[_bucket(head[-4:], "hsfx:", HEAD_DIM)] += 0.6
        head_vec[_bucket(head[-3:], "hsfx3:", HEAD_DIM)] += 0.4
        if len(head_words) >= 2:
            # Two-word tails such as "ice_cream" or "cutting_board".
            head_vec[_bucket("_".join(head_words[-2:]), "h2:", HEAD_DIM)] += 0.8

    padded = "^" + "_".join(head_words) + "$"
    for n in range(NGRAM_MIN, NGRAM_MAX + 1):
        for i in range(len(padded) - n + 1):
            char_vec[_bucket(padded[i : i + n], f"c{n}:", CHAR_DIM)] += 1.0

    out = []
    for block, weight in ((word_vec, 1.0), (head_vec, 1.5), (char_vec, 0.7)):
        norm = float(np.linalg.norm(block))
        out.append(block * (weight / norm) if norm > 0 else block)
    return np.concatenate(out)


def _struct_vector(values: dict[str, float], order: Sequence[str]) -> np.ndarray:
    return np.array([float(values.get(key, 0.0)) for key in order], dtype=np.float32)


def item_vector(name: str, struct: dict[str, float] | None = None, extra_text: Iterable[str] = ()) -> np.ndarray:
    return np.concatenate([text_vector(name, extra_text), _struct_vector(struct or {}, ITEM_STRUCT_FEATURES)])


def block_vector(name: str, struct: dict[str, float] | None = None, extra_text: Iterable[str] = ()) -> np.ndarray:
    return np.concatenate([text_vector(name, extra_text), _struct_vector(struct or {}, BLOCK_STRUCT_FEATURES)])


def log1p_scaled(value: float, scale: float = 8.0) -> float:
    """Squash an unbounded count into a stable ~[0, 1] feature."""
    try:
        return float(np.log1p(max(0.0, float(value))) / np.log1p(scale))
    except Exception:
        return 0.0


# --- adapters from converter IR --------------------------------------------


def item_struct_from_node(item: Any) -> dict[str, float]:
    """Build structured item features from an :class:`~converter.ir.ItemNode`."""
    meta = getattr(item, "metadata", {}) or {}
    components = getattr(item, "components", {}) or {}
    tags = [str(t).lower() for t in (getattr(item, "tags", []) or [])]
    hints = [str(h).lower() for h in (getattr(item, "category_hints", []) or [])]
    behavior = getattr(item, "behavior", None) or {}
    model_tree = getattr(item, "model_tree", None)
    parent = str(meta.get("model_parent", "") or "")
    tier = getattr(item, "tool_tier", None)
    tier_index = {"wood": 0.15, "stone": 0.3, "copper": 0.4, "iron": 0.55, "gold": 0.7, "diamond": 0.85, "netherite": 1.0}

    def any_tag(*needles: str) -> float:
        return 1.0 if any(n in t for t in tags for n in needles) else 0.0

    def any_hint(*needles: str) -> float:
        return 1.0 if any(n in h for h in hints for n in needles) else 0.0

    words = tokenize(getattr(item, "id", ""))
    return {
        "has_food_component": 1.0 if getattr(item, "food", None) else 0.0,
        "has_consumable_component": 1.0 if "consumable" in components else 0.0,
        "has_block_binding": 1.0 if behavior.get("type") == "block_item" else 0.0,
        "has_durability": 1.0 if getattr(item, "durability", None) else 0.0,
        "has_attack_damage": 1.0 if getattr(item, "attack_damage", None) else 0.0,
        "has_equippable": 1.0 if getattr(item, "equip", None) else 0.0,
        "has_enchantments": 1.0 if getattr(item, "enchantments", None) else 0.0,
        "is_display_helper": 1.0 if meta.get("display_item") else 0.0,
        "has_stage_metadata": 1.0 if meta.get("stage") is not None else 0.0,
        "has_3d_model": 1.0 if (model_tree or meta.get("is_3d")) else 0.0,
        "model_parent_generated": 1.0 if parent.endswith("item/generated") else 0.0,
        "model_parent_handheld": 1.0 if parent.endswith("item/handheld") else 0.0,
        "has_gui_icon": 1.0 if getattr(item, "gui_icon_texture", None) else 0.0,
        "texture_count_log": log1p_scaled(len(getattr(item, "textures", []) or [])),
        "tag_tools": any_tag("tool"),
        "tag_weapons": any_tag("weapon", "sword", "combat"),
        "tag_armor": any_tag("armor", "armour"),
        "tag_food": any_tag("food", "meal", "drink", "edible"),
        "tag_crops": any_tag("crop", "seed", "plant"),
        "tag_ingredients": any_tag("ingredient"),
        "hint_combat": any_hint("combat", "weapon"),
        "hint_food": any_hint("food", "meal", "drink"),
        "hint_building": any_hint("building", "block"),
        "hint_tools": any_hint("tool", "equipment"),
        "hint_misc": any_hint("misc", "redstone"),
        "name_word_count": log1p_scaled(len(words), 5),
        "name_length_log": log1p_scaled(len(getattr(item, "id", "")), 32),
        "from_recipe_output": 1.0 if getattr(item, "recipe_references", None) else 0.0,
        "has_max_stack_1": 1.0 if getattr(item, "max_stack_size", None) == 1 else 0.0,
        "has_use_remainder": 1.0 if getattr(item, "use_remainder", None) else 0.0,
        "tier_hint": tier_index.get(str(tier), 0.0),
        "namespace_is_minecraft": 1.0 if getattr(item, "namespace", "") == "minecraft" else 0.0,
    }


def block_struct_from_node(block: Any) -> dict[str, float]:
    """Build structured block features from a :class:`~converter.ir.BlockNode`."""
    meta = getattr(block, "metadata", {}) or {}
    states = getattr(block, "states", []) or []
    props = {str(getattr(s, "property_name", "")).lower() for s in states}
    variants = getattr(block, "blockstate_variants", []) or []
    model = meta.get("block_model_json") if isinstance(meta.get("block_model_json"), dict) else None
    parent = str((model or {}).get("parent", "") or meta.get("model_parent", "") or "")
    render = str((model or {}).get("render_type", "") or meta.get("render_type", "") or "")
    elements = (model or {}).get("elements") if isinstance(model, dict) else None
    elements = elements if isinstance(elements, list) else []
    tags = [str(t).lower() for t in (getattr(block, "tags", []) or [])]
    settings = getattr(block, "settings", {}) or {}

    non_full = False
    rotated = False
    for el in elements:
        if not isinstance(el, dict):
            continue
        if el.get("from") != [0, 0, 0] or el.get("to") != [16, 16, 16]:
            non_full = True
        if el.get("rotation"):
            rotated = True

    def any_tag(*needles: str) -> float:
        return 1.0 if any(n in t for t in tags for n in needles) else 0.0

    return {
        "has_age_property": 1.0 if "age" in props else 0.0,
        "has_facing_property": 1.0 if "facing" in props else 0.0,
        "has_half_property": 1.0 if ("half" in props or "type" in props) else 0.0,
        "has_axis_property": 1.0 if "axis" in props else 0.0,
        "has_waterlogged_property": 1.0 if "waterlogged" in props else 0.0,
        "has_powered_property": 1.0 if ("powered" in props or "lit" in props) else 0.0,
        "state_count_log": log1p_scaled(len(states), 6),
        "variant_count_log": log1p_scaled(len(variants), 16),
        "is_multipart": 1.0 if getattr(block, "blockstate_multipart", None) else 0.0,
        "has_block_entity": 1.0 if getattr(block, "block_entity", None) else 0.0,
        "render_cutout": 1.0 if "cutout" in render else 0.0,
        "render_translucent": 1.0 if "translucent" in render else 0.0,
        "parent_cube_all": 1.0 if parent.endswith("cube_all") or parent.endswith("cube") else 0.0,
        "parent_cross": 1.0 if "cross" in parent else 0.0,
        "parent_slab_or_stairs": 1.0 if ("slab" in parent or "stairs" in parent) else 0.0,
        "parent_fence_or_wall": 1.0 if ("fence" in parent or "wall" in parent) else 0.0,
        "model_non_full_cube": 1.0 if non_full else 0.0,
        "model_has_rotation": 1.0 if rotated else 0.0,
        "model_element_count_log": log1p_scaled(len(elements), 12),
        "has_loot_table": 1.0 if getattr(block, "loot_table", None) else 0.0,
        "has_block_item": 1.0 if getattr(block, "block_item", None) else 0.0,
        "hardness_log": log1p_scaled(settings.get("hardness", 0.0) or 0.0, 10),
        "name_word_count": log1p_scaled(len(tokenize(getattr(block, "id", ""))), 5),
        "name_length_log": log1p_scaled(len(getattr(block, "id", "")), 32),
        "tag_mineable_pickaxe": any_tag("mineable/pickaxe"),
        "tag_mineable_axe": any_tag("mineable/axe"),
        "tag_mineable_shovel": any_tag("mineable/shovel"),
        "tag_leaves": any_tag("leaves"),
        "tag_logs": any_tag("logs", "log"),
        "tag_crops": any_tag("crops", "crop"),
        "light_emission": log1p_scaled(settings.get("light_emission", 0) or 0, 15),
        "namespace_is_minecraft": 1.0 if getattr(block, "namespace", "") == "minecraft" else 0.0,
    }
