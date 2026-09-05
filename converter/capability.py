"""Capability / mapping engine.

Classifies each IR node into DIRECT / TRANSFORM / PARTIAL / SCRIPT / EXTENSION /
MANUAL / UNSUPPORTED and records a reason. The generator uses these decisions
and never emits content for an UNSUPPORTED object (it becomes diagnostic-only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import status
from .analyzer import AnalysisResult
from .config import Settings
from .ir import BlockNode, ItemNode, LootNode, RecipeNode


@dataclass
class Decision:
    object_id: str
    domain: str
    result: str
    reason: str = ""
    confidence: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "domain": self.domain,
            "result": self.result,
            "reason": self.reason,
            "confidence": self.confidence,
            "details": self.details,
        }


@dataclass
class MappingResult:
    decisions: dict[str, Decision] = field(default_factory=dict)

    def add(self, decision: Decision) -> None:
        self.decisions[decision.object_id] = decision

    def get(self, object_id: str) -> Decision | None:
        return self.decisions.get(object_id)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {level: 0 for level in status.CAPABILITY_LEVELS}
        for decision in self.decisions.values():
            counts[decision.result] = counts.get(decision.result, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in sorted(self.decisions.items())}


def build_mapping(result: AnalysisResult, settings: Settings | None = None) -> MappingResult:
    mapping = MappingResult()
    settings = settings or Settings()

    for item in result.items.values():
        mapping.add(classify_item(item))
    for block in result.blocks.values():
        mapping.add(classify_block(block))
    for recipe in result.recipes.values():
        mapping.add(classify_recipe(recipe, settings))
    for loot in result.loot.values():
        mapping.add(classify_loot(loot))

    return mapping


def classify_item(item: ItemNode) -> Decision:
    details: dict[str, Any] = {
        "material": item.base_material,
        "tool_tier": item.tool_tier,
        "has_block_binding": bool(item.behavior and item.behavior.get("type") == "block_item"),
        "has_food": item.food is not None,
        "has_equip": item.equip is not None,
    }
    # Items are always directly generatable with material + model/texture.
    return Decision(
        object_id=item.id,
        domain=status.DOMAIN_ITEM,
        result=status.DIRECT,
        reason="Item maps directly; semantic carrier material is emitted only for drink/soup food families, otherwise material is omitted.",
        confidence=item.confidence,
        details=details,
    )


def classify_block(block: BlockNode) -> Decision:
    if block.block_entity:
        return Decision(
            object_id=block.id,
            domain=status.DOMAIN_BLOCK,
            result=status.PARTIAL,
            reason="BlockEntity detected: static block/state/model is preserved, but entity ticking/logic requires an extension.",
            confidence=block.confidence,
            details={"block_entity": block.block_entity},
        )

    details: dict[str, Any] = {
        "state_count": len(block.states),
        "has_multipart": bool(block.blockstate_multipart),
        "auto_state": block.auto_state,
        "tool_tier": block.tool_tier,
    }

    if block.blockstate_multipart:
        return Decision(
            object_id=block.id,
            domain=status.DOMAIN_BLOCK,
            result=status.PARTIAL,
            reason="Multipart blockstate model conditions are preserved in source-map but only approximated in CraftEngine variants.",
            confidence=block.confidence,
            details=details,
        )

    if not block.states:
        return Decision(
            object_id=block.id,
            domain=status.DOMAIN_BLOCK,
            result=status.DIRECT,
            reason="Single-state block maps directly to a CraftEngine state/auto_state definition.",
            confidence=block.confidence,
            details=details,
        )

    # Multi-state blocks with known property types map as TRANSFORM (state mapping).
    return Decision(
        object_id=block.id,
        domain=status.DOMAIN_BLOCK,
        result=status.TRANSFORM,
        reason="Multi-state block: vanilla state properties are translated into CraftEngine states/properties/variants.",
        confidence=block.confidence,
        details=details,
    )


def classify_recipe(recipe: RecipeNode, settings: Settings | None = None) -> Decision:
    settings = settings or Settings()
    short = recipe.recipe_type.split(":")[-1] if ":" in recipe.recipe_type else recipe.recipe_type

    # Resolve the station using settings (applies remap of unknown stations).
    station = settings.effective_station(recipe.recipe_type)
    recipe.station = station

    if station == "crafting_table":
        if short == "crafting_shapeless":
            result = status.DIRECT
            reason = "Shapeless crafting recipe maps to shapeless_transform."
        else:
            result = status.TRANSFORM
            reason = f"Recipe type '{recipe.recipe_type}' remapped to the crafting table (shapeless_transform)."
    elif station == "sliceboard":
        result = status.TRANSFORM
        reason = f"Recipe type '{recipe.recipe_type}' mapped to a SliceBoard cutting-board recipe."
    elif station == "unknown":
        result = status.UNSUPPORTED
        reason = f"UNKNOWN_SOURCE_SERIALIZER: unsupported recipe type '{recipe.recipe_type}'."
    elif station in ("furnace", "blast_furnace", "smoker", "campfire"):
        result = status.PARTIAL
        reason = f"{short} recipe has a known result/ingredient but cooking semantics are not fully verified for 26.8."
    elif station in ("smithing_table",):
        result = status.PARTIAL
        reason = f"{short} recipe ingredients preserved; direct target schema for smithing is unconfirmed."
    else:
        result = status.UNSUPPORTED
        reason = f"UNKNOWN_SOURCE_SERIALIZER: unsupported recipe type '{recipe.recipe_type}'."

    return Decision(
        object_id=recipe.id,
        domain=status.DOMAIN_RECIPE,
        result=result,
        reason=reason,
        confidence=recipe.confidence,
        details={"original_type": recipe.original_type, "station": station},
    )


def classify_loot(loot: LootNode) -> Decision:
    return Decision(
        object_id=loot.id,
        domain=status.DOMAIN_LOOT,
        result=status.DIRECT,
        reason="Loot table preserved as a self-contained CraftEngine loot definition.",
        confidence=loot.confidence,
    )