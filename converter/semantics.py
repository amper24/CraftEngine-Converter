"""Neural semantic pass.

Runs the micro brain (:mod:`converter.brain`) over the analyzed IR and applies
its predictions, then records everything it decided so the user can read it in
the conversion log and in ``reports/semantics.*``.

Design rules
------------

* **Evidence wins over prediction.** Anything the analyzer read from real
  source data (a food component in bytecode, a ``minecraft:mineable`` tag, an
  explicit recipe-book category, a crop's ``age`` property) is never overwritten
  by the network. The brain only fills gaps and resolves ambiguity.
* **Confidence gates.** A prediction below the configured threshold is recorded
  as advice but not applied.
* **Fully auditable.** Every applied and rejected decision lands in
  ``item.metadata["brain"]`` / ``block.metadata["brain"]`` and in the report.
* **Optional.** With ``brain_enabled: false`` (or without NumPy) the converter
  behaves exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .util import Log

# Analyzer signals that are hard evidence: when present, the corresponding
# brain head is advisory only.
_EVIDENCE_ITEM_CATEGORY = ("tags", "category_hints")


@dataclass
class SemanticRecord:
    """One object's neural verdict, as shown to the user."""

    object_id: str
    domain: str
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    applied: dict[str, Any] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.object_id,
            "domain": self.domain,
            "description": self.description,
            "labels": self.labels,
            "confidence": {k: round(v, 4) for k, v in self.confidence.items()},
            "applied": self.applied,
            "skipped": self.skipped,
            "commands": self.commands,
        }


@dataclass
class SemanticResult:
    enabled: bool = False
    reason: str = ""
    records: dict[str, SemanticRecord] = field(default_factory=dict)

    def get(self, object_id: str) -> SemanticRecord | None:
        return self.records.get(object_id)

    def category_for(self, object_id: str) -> str | None:
        record = self.records.get(object_id)
        if not record:
            return None
        return record.applied.get("category") or None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for record in self.records.values():
            label = record.labels.get("category") or record.labels.get("block_kind")
            if label:
                out[label] = out.get(label, 0) + 1
        return dict(sorted(out.items()))

    def commands_by_id(self) -> dict[str, list[str]]:
        return {rid: r.commands for rid, r in sorted(self.records.items()) if r.commands}

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "counts": self.counts(),
            "objects": {k: v.to_dict() for k, v in sorted(self.records.items())},
        }


def apply_semantics(analysis: Any, settings: Any, log: Log) -> SemanticResult:
    """Classify every item and block, apply safe conclusions, and report."""
    result = SemanticResult()
    if not getattr(settings, "brain_enabled", True):
        result.reason = "disabled in settings (brain_enabled: false)"
        log.info("BRAIN: нейросемантика отключена в настройках")
        return result

    from .brain import get_brain

    brain = get_brain()
    if not brain.available:
        result.reason = brain.load_error or "brain weights unavailable"
        log.warn(f"BRAIN: микро-нейросеть недоступна, используются эвристики: {result.reason}")
        return result

    result.enabled = True
    item_min = float(getattr(settings, "brain_min_confidence", 0.6))
    block_min = float(getattr(settings, "brain_block_min_confidence", 0.7))
    verbose = bool(getattr(settings, "brain_log_every_object", True))

    log.info("BRAIN: микро-нейросеть загружена", items=len(analysis.items), blocks=len(analysis.blocks))

    for item_id in sorted(analysis.items):
        item = analysis.items[item_id]
        pred = brain.classify_item(item)
        if pred is None:
            continue
        record = SemanticRecord(
            object_id=item_id,
            domain="item",
            description=pred.explanation,
            labels=dict(pred.labels),
            confidence=dict(pred.confidence),
            commands=list(pred.commands),
        )
        _apply_item(item, pred, record, item_min, settings)
        item.metadata["brain"] = record.to_dict()
        result.records[item_id] = record
        if verbose:
            log.info(f"BRAIN {item_id}: {pred.explanation}", commands=pred.commands)

    for block_id in sorted(analysis.blocks):
        block = analysis.blocks[block_id]
        pred = brain.classify_block(block)
        if pred is None:
            continue
        record = SemanticRecord(
            object_id=block_id,
            domain="block",
            description=pred.explanation,
            labels=dict(pred.labels),
            confidence=dict(pred.confidence),
            commands=list(pred.commands),
        )
        _apply_block(block, pred, record, block_min, settings)
        block.metadata["brain"] = record.to_dict()
        result.records[block_id] = record
        if verbose:
            log.info(f"BRAIN {block_id}: {pred.explanation}", commands=pred.commands)

    applied = sum(1 for r in result.records.values() if r.applied)
    log.info("BRAIN: семантическая разметка завершена", classified=len(result.records), applied=applied)
    return result


# --- appliers ---------------------------------------------------------------


def _apply_item(item: Any, pred: Any, record: SemanticRecord, minimum: float, settings: Any) -> None:
    # 1) Category. Explicit source tags / recipe-book hints are authoritative,
    #    so the prediction is stored for the generator to use only as a
    #    fallback (the generator checks tags and hints first).
    category = pred.get("category", minimum)
    if category:
        record.applied["category"] = category
    else:
        record.skipped["category"] = f"confidence {pred.conf('category'):.2f} < {minimum}"

    # 2) Gear kind. The analyzer's keyword pass already ran; only fill a gap,
    #    never contradict an analyzer decision that came from real stats.
    gear = pred.get("gear_kind", minimum)
    if gear and gear != "none":
        if not getattr(item, "gear_kind", None):
            item.gear_kind = gear
            record.applied["gear_kind"] = gear
        elif item.gear_kind != gear:
            record.skipped["gear_kind"] = f"analyzer already decided {item.gear_kind}"

    # 3) Tool tier for gear that has one but was not recognized by keywords.
    tier = pred.get("tool_tier", max(minimum, 0.7))
    if tier and tier != "none" and not getattr(item, "tool_tier", None) and getattr(item, "gear_kind", None):
        item.tool_tier = tier
        record.applied["tool_tier"] = tier

    # 4) Food family. A real food component from datagen/bytecode always wins;
    #    the network only marks *candidates* that the generator may turn into a
    #    food component when the keyword list would have missed them.
    family = pred.get("food_family", minimum)
    if family and family != "none":
        if getattr(item, "food", None) is None:
            item.metadata["food_candidate"] = family
            record.applied["food_family"] = family
        else:
            record.skipped["food_family"] = "source already declares a food component"

    # 5) Gear stats for items the analyzer skipped because keywords missed.
    if record.applied.get("gear_kind"):
        _backfill_gear_stats(item)


def _backfill_gear_stats(item: Any) -> None:
    """Re-derive durability/damage after the brain assigned a gear kind."""
    kind = getattr(item, "gear_kind", None)
    if not kind:
        return
    tier = getattr(item, "tool_tier", None) or "iron"
    durability = {"wood": 59, "stone": 131, "copper": 190, "iron": 250, "gold": 32, "diamond": 1561, "netherite": 2031}
    damage = {"wood": 4.0, "stone": 5.0, "copper": 5.0, "iron": 6.0, "gold": 4.0, "diamond": 7.0, "netherite": 8.0}
    if getattr(item, "durability", None) is None and kind in ("tool", "weapon", "spear", "trident", "bow", "crossbow", "shield"):
        item.durability = durability.get(tier, 250)
    if kind in ("weapon", "spear", "trident") and getattr(item, "attack_damage", None) is None:
        item.attack_damage = damage.get(tier, 6.0)
        item.attack_speed = -2.4 if kind == "weapon" else -2.8


def _apply_block(block: Any, pred: Any, record: SemanticRecord, minimum: float, settings: Any) -> None:
    kind = pred.get("block_kind", minimum)
    if kind:
        record.applied["block_kind"] = kind
        block.metadata["brain_block_kind"] = kind
    else:
        record.skipped["block_kind"] = f"confidence {pred.conf('block_kind'):.2f} < {minimum}"
        return

    # Hard evidence from the source model/blockstate is never overridden: the
    # analyzer already set auto_state from an `age` property, a cross parent,
    # a multipart definition or non-cubic geometry.
    if block.metadata.get("is_crop") or block.metadata.get("use_entity_renderer"):
        record.skipped["auto_state"] = "analyzer derived it from source model/blockstate evidence"
        return

    auto_state = pred.get("auto_state", minimum)
    default_state = getattr(settings, "block_auto_state", "solid")
    if auto_state and getattr(block, "auto_state", None) in (None, "", default_state, "solid", "note_block"):
        # Only replace a generic/default choice — a specific analyzer decision
        # (leaves, sapling, cactus…) came from the mapping table and stays.
        if auto_state != block.auto_state:
            record.applied["auto_state"] = auto_state
            record.applied["auto_state_previous"] = block.auto_state
            block.auto_state = auto_state

    transparent = pred.get("transparent", minimum)
    if transparent == "transparent" and not getattr(block, "transparent", False):
        block.transparent = True
        record.applied["transparent"] = True

    if pred.get("entity_renderer", max(minimum, 0.75)) == "entity" and not block.metadata.get("use_entity_renderer"):
        # Advice only: switching a renderer requires variant data the analyzer
        # builds from the source blockstate, so this is surfaced as a manual
        # task instead of being forced.
        record.skipped["entity_renderer"] = "recommended, but requires source blockstate variants (see manual tasks)"
        block.metadata["brain_suggests_entity_renderer"] = True
