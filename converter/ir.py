"""Intermediate Representation (IR) node models.

Every detected object becomes an IR node with provenance, independent of the
CraftEngine target. This is the source of truth for reports and source-maps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import status


@dataclass
class BaseNode:
    id: str
    namespace: str
    source: dict[str, Any] = field(default_factory=dict)
    kind: str = ""
    confidence: float = 0.0
    status: str = status.DETECTED
    references: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "namespace": self.namespace,
            "source": self.source,
            "kind": self.kind,
            "confidence": self.confidence,
            "status": self.status,
            "references": self.references,
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass
class ItemNode(BaseNode):
    base_material: str | None = None
    display_name: str | None = None
    lore: list[str] = field(default_factory=list)
    components: dict[str, Any] = field(default_factory=dict)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    enchantments: dict[str, Any] = field(default_factory=dict)
    durability: int | None = None
    max_stack_size: int | None = None
    food: dict[str, Any] | None = None
    equip: dict[str, Any] | None = None
    use_remainder: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    model: str | None = None
    textures: list[str] = field(default_factory=list)
    behavior: dict[str, Any] | None = None
    recipe_references: list[str] = field(default_factory=list)
    tool_tier: str | None = None
    gear_kind: str | None = None
    attack_damage: float | None = None
    attack_speed: float | None = None
    attack_knockback: float | None = None
    item_model: str | None = None
    model_tree: dict[str, Any] | None = None
    gui_icon_texture: str | None = None
    gui_icon_model: str | None = None
    # References captured by the asset index. These are diagnostic metrics,
    # not keys emitted to CraftEngine.
    model_refs: list[str] = field(default_factory=list)
    texture_refs: list[str] = field(default_factory=list)
    category_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update(
            {
                "base_material": self.base_material,
                "display_name": self.display_name,
                "lore": self.lore,
                "components": self.components,
                "attributes": self.attributes,
                "enchantments": self.enchantments,
                "durability": self.durability,
                "max_stack_size": self.max_stack_size,
                "food": self.food,
                "equip": self.equip,
                "use_remainder": self.use_remainder,
                "settings": self.settings,
                "tags": self.tags,
                "model": self.model,
                "textures": self.textures,
                "behavior": self.behavior,
                "recipe_references": self.recipe_references,
                "tool_tier": self.tool_tier,
                "gear_kind": self.gear_kind,
                "attack_damage": self.attack_damage,
                "attack_speed": self.attack_speed,
                "attack_knockback": self.attack_knockback,
                "item_model": self.item_model,
                "model_tree": self.model_tree,
                "gui_icon_texture": self.gui_icon_texture,
                "gui_icon_model": self.gui_icon_model,
                "model_refs": self.model_refs,
                "texture_refs": self.texture_refs,
                "category_hints": self.category_hints,
            }
        )
        return data


@dataclass
class BlockStateNode:
    property_name: str
    property_type: str
    allowed_values: list[str] = field(default_factory=list)
    default_value: str | None = None
    source_declaration: str | None = None
    variants: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_name": self.property_name,
            "property_type": self.property_type,
            "allowed_values": self.allowed_values,
            "default_value": self.default_value,
            "source_declaration": self.source_declaration,
            "variants": self.variants,
        }


@dataclass
class BlockNode(BaseNode):
    base_material: str | None = None
    states: list[BlockStateNode] = field(default_factory=list)
    default_state: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    loot: str | None = None
    model: str | None = None
    block_model_path: str | None = None
    textures: list[str] = field(default_factory=list)
    display_name: str | None = None
    blockstate_variants: list[dict[str, Any]] = field(default_factory=list)
    blockstate_multipart: list[dict[str, Any]] = field(default_factory=list)
    behaviors: list[str] = field(default_factory=list)
    behavior_configs: list[dict[str, Any]] = field(default_factory=list)
    block_entity: dict[str, Any] | None = None
    tool_tier: str | None = None
    auto_state: str | dict[str, Any] | None = None
    item_binding: str | None = None
    tags: list[str] = field(default_factory=list)
    transparent: bool = False
    render_type: str | None = None
    flat: bool = False
    # References captured by the asset index (diagnostic metrics).
    model_refs: list[str] = field(default_factory=list)
    texture_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update(
            {
                "base_material": self.base_material,
                "states": [s.to_dict() for s in self.states],
                "default_state": self.default_state,
                "settings": self.settings,
                "loot": self.loot,
                "model": self.model,
                "block_model_path": self.block_model_path,
                "textures": self.textures,
                "display_name": self.display_name,
                "blockstate_variants": self.blockstate_variants,
                "blockstate_multipart": self.blockstate_multipart,
                "behaviors": self.behaviors,
                "behavior_configs": self.behavior_configs,
                "block_entity": self.block_entity,
                "tool_tier": self.tool_tier,
                "auto_state": self.auto_state,
                "item_binding": self.item_binding,
                "tags": self.tags,
                "transparent": self.transparent,
                "render_type": self.render_type,
                "flat": self.flat,
                "model_refs": self.model_refs,
                "texture_refs": self.texture_refs,
            }
        )
        return data


@dataclass
class RecipeNode(BaseNode):
    recipe_type: str = ""
    original_type: str = ""
    station: str = "unknown"
    ingredients: list[dict[str, Any]] = field(default_factory=list)
    pattern: list[str] = field(default_factory=list)
    key: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    count: int = 1
    experience: float | None = None
    time: int | None = None
    conditions: list[dict[str, Any]] = field(default_factory=list)
    unlock: dict[str, Any] = field(default_factory=dict)
    post_processors: list[dict[str, Any]] = field(default_factory=list)
    transform_processors: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update(
            {
                "recipe_type": self.recipe_type,
                "original_type": self.original_type,
                "station": self.station,
                "ingredients": self.ingredients,
                "pattern": self.pattern,
                "key": self.key,
                "result": self.result,
                "results": self.results,
                "count": self.count,
                "experience": self.experience,
                "time": self.time,
                "conditions": self.conditions,
                "unlock": self.unlock,
                "post_processors": self.post_processors,
                "transform_processors": self.transform_processors,
                "raw": self.raw,
            }
        )
        return data


@dataclass
class FurnitureNode(BaseNode):
    """Entity-based decoration (ItemsAdder furniture -> CraftEngine furniture).

    ``variants`` mirrors the CraftEngine furniture section of the same name:
    each variant holds ``elements`` (display entities) and ``hitboxes``
    (collision boxes, optional seats).
    """

    item: str | None = None
    display_name: str | None = None
    variants: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    loot_item: str | None = None
    light_level: int | None = None
    # Source-side provenance that has no CraftEngine key; kept for the reports.
    unmapped: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update(
            {
                "item": self.item,
                "display_name": self.display_name,
                "variants": self.variants,
                "settings": self.settings,
                "loot_item": self.loot_item,
                "light_level": self.light_level,
                "unmapped": self.unmapped,
            }
        )
        return data


@dataclass
class LootNode(BaseNode):
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["raw"] = self.raw
        return data


@dataclass
class ResourceNode:
    source_path: str
    namespace: str
    kind: str
    raw_hash: str
    references: list[str] = field(default_factory=list)
    parsed: dict[str, Any] = field(default_factory=dict)
    asset_domain: str | None = None
    is_resourcepack: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "namespace": self.namespace,
            "kind": self.kind,
            "raw_hash": self.raw_hash,
            "references": self.references,
            "parsed": self.parsed,
            "asset_domain": self.asset_domain,
            "is_resourcepack": self.is_resourcepack,
        }
