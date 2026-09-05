"""Inference runtime for the micro neural network.

A tiny multi-head MLP:

    x -> Linear(d, H) -> GELU -> Linear(H, H2) -> GELU -> {head_i: Linear(H2, C_i)}

Weights are stored as ``.npz`` next to the package (``models/brain/``). The
runtime is defensive on purpose: if NumPy or the weight files are missing, the
brain reports ``available == False`` and every caller falls back to the legacy
keyword heuristics, so conversion never breaks because of the model.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import paths
from . import labels

try:  # NumPy is a hard requirement of the brain, a soft one of the converter.
    import numpy as np
except Exception:  # pragma: no cover - exercised only in stripped environments
    np = None  # type: ignore[assignment]


MODEL_DIR_NAME = "models/brain"
ITEM_MODEL = "item_head.npz"
BLOCK_MODEL = "block_head.npz"


def model_dir() -> Path:
    return paths.PACKAGE_ROOT / MODEL_DIR_NAME


# --- prediction containers --------------------------------------------------


@dataclass
class Prediction:
    """One multi-head prediction with per-head confidence."""

    labels: dict[str, str] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)

    def get(self, head: str, minimum: float = 0.0) -> str | None:
        """Return the label for ``head`` only if it clears ``minimum`` confidence."""
        if head not in self.labels:
            return None
        if self.confidence.get(head, 0.0) < minimum:
            return None
        return self.labels[head]

    def conf(self, head: str) -> float:
        return float(self.confidence.get(head, 0.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": dict(self.labels),
            "confidence": {k: round(v, 4) for k, v in self.confidence.items()},
        }


@dataclass
class ItemPrediction(Prediction):
    object_id: str = ""
    commands: list[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update({"id": self.object_id, "commands": self.commands, "explanation": self.explanation})
        return data


@dataclass
class BlockPrediction(Prediction):
    object_id: str = ""
    commands: list[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update({"id": self.object_id, "commands": self.commands, "explanation": self.explanation})
        return data


# --- network ----------------------------------------------------------------


def _gelu(x: "np.ndarray") -> "np.ndarray":
    return 0.5 * x * (1.0 + np.tanh(0.7978845608028654 * (x + 0.044715 * x**3)))


def _softmax(x: "np.ndarray") -> "np.ndarray":
    x = x - x.max(axis=-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=-1, keepdims=True)


class MultiHeadMLP:
    """Forward-only multi-head MLP loaded from an ``.npz`` archive."""

    def __init__(self, arrays: dict[str, "np.ndarray"], head_labels: dict[str, tuple[str, ...]]) -> None:
        self.w1 = arrays["w1"]
        self.b1 = arrays["b1"]
        self.w2 = arrays["w2"]
        self.b2 = arrays["b2"]
        self.head_labels = head_labels
        self.heads = {
            head: (arrays[f"head_{head}_w"], arrays[f"head_{head}_b"])
            for head in head_labels
            if f"head_{head}_w" in arrays
        }

    @classmethod
    def load(cls, path: Path, head_labels: dict[str, tuple[str, ...]]) -> "MultiHeadMLP":
        with np.load(path) as data:
            arrays = {k: data[k] for k in data.files}
        return cls(arrays, head_labels)

    def predict(self, x: "np.ndarray") -> tuple[dict[str, str], dict[str, float], dict[str, dict[str, float]]]:
        h = _gelu(x @ self.w1 + self.b1)
        h = _gelu(h @ self.w2 + self.b2)
        out_labels: dict[str, str] = {}
        out_conf: dict[str, float] = {}
        out_probs: dict[str, dict[str, float]] = {}
        for head, (w, b) in self.heads.items():
            probs = _softmax(h @ w + b)
            names = self.head_labels[head]
            idx = int(np.argmax(probs))
            out_labels[head] = names[idx]
            out_conf[head] = float(probs[idx])
            out_probs[head] = {names[i]: float(p) for i, p in enumerate(probs)}
        return out_labels, out_conf, out_probs


# --- brain ------------------------------------------------------------------


class Brain:
    """Semantic classifier used by the analyzer, generator and reports."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or model_dir()
        self.item_net: MultiHeadMLP | None = None
        self.block_net: MultiHeadMLP | None = None
        self.load_error: str | None = None
        self._cache: dict[tuple[str, str], Prediction] = {}
        self._load()

    # -- lifecycle
    def _load(self) -> None:
        if np is None:
            self.load_error = "numpy is not installed; brain disabled"
            return
        try:
            item_path = self.directory / ITEM_MODEL
            block_path = self.directory / BLOCK_MODEL
            if item_path.exists():
                self.item_net = MultiHeadMLP.load(item_path, labels.ITEM_HEADS)
            if block_path.exists():
                self.block_net = MultiHeadMLP.load(block_path, labels.BLOCK_HEADS)
            if self.item_net is None and self.block_net is None:
                self.load_error = f"no brain weights found in {self.directory}"
        except Exception as exc:  # pragma: no cover - corrupted weights
            self.item_net = None
            self.block_net = None
            self.load_error = f"failed to load brain weights: {exc}"

    @property
    def available(self) -> bool:
        return self.item_net is not None or self.block_net is not None

    # -- inference
    def classify_item(self, item: Any) -> ItemPrediction | None:
        if self.item_net is None:
            return None
        from . import features

        item_id = str(getattr(item, "id", "") or "")
        key = ("item", item_id)
        cached = self._cache.get(key)
        if isinstance(cached, ItemPrediction):
            return cached

        extra = [str(getattr(item, "display_name", "") or "")]
        vec = features.item_vector(item_id, features.item_struct_from_node(item), extra)
        lbl, conf, probs = self.item_net.predict(vec)
        pred = ItemPrediction(labels=lbl, confidence=conf, probabilities=probs, object_id=item_id)
        pred.explanation = describe_item(pred)
        pred.commands = item_commands(item_id, pred)
        self._cache[key] = pred
        return pred

    def classify_block(self, block: Any) -> BlockPrediction | None:
        if self.block_net is None:
            return None
        from . import features

        block_id = str(getattr(block, "id", "") or "")
        key = ("block", block_id)
        cached = self._cache.get(key)
        if isinstance(cached, BlockPrediction):
            return cached

        vec = features.block_vector(block_id, features.block_struct_from_node(block))
        lbl, conf, probs = self.block_net.predict(vec)
        pred = BlockPrediction(labels=lbl, confidence=conf, probabilities=probs, object_id=block_id)
        pred.explanation = describe_block(pred)
        pred.commands = block_commands(block_id, pred)
        self._cache[key] = pred
        return pred


# --- human-facing text and in-game commands ---------------------------------


def describe_item(pred: Prediction) -> str:
    category = pred.labels.get("category", "Items")
    ru = labels.CATEGORY_RU.get(category, category)
    parts = [f"{ru} ({category}, {pred.conf('category') * 100:.0f}%)"]
    gear = pred.labels.get("gear_kind", "none")
    if gear != "none":
        parts.append(f"снаряжение: {gear} ({pred.conf('gear_kind') * 100:.0f}%)")
    food = pred.labels.get("food_family", "none")
    if food != "none":
        parts.append(f"пищевая семья: {food} ({pred.conf('food_family') * 100:.0f}%)")
    tier = pred.labels.get("tool_tier", "none")
    if tier != "none":
        parts.append(f"тир: {tier}")
    return "; ".join(parts)


def describe_block(pred: Prediction) -> str:
    kind = pred.labels.get("block_kind", "solid")
    ru = labels.BLOCK_KIND_RU.get(kind, kind)
    parts = [f"{ru} ({kind}, {pred.conf('block_kind') * 100:.0f}%)"]
    parts.append(f"auto_state: {pred.labels.get('auto_state', 'solid')} ({pred.conf('auto_state') * 100:.0f}%)")
    parts.append("прозрачный" if pred.labels.get("transparent") == "transparent" else "непрозрачный")
    if pred.labels.get("entity_renderer") == "entity":
        parts.append("нужен entity-renderer")
    return "; ".join(parts)


def item_commands(item_id: str, pred: Prediction) -> list[str]:
    """CraftEngine commands to test the converted item in-game."""
    cmds = [f"/ce give @s {item_id} 1"]
    gear = pred.labels.get("gear_kind", "none")
    if gear in ("tool", "weapon", "spear", "trident", "bow", "crossbow", "shield", "armor"):
        cmds.append(f"/ce debug item {item_id}")
    if pred.labels.get("food_family", "none") != "none":
        cmds.append(f"/ce give @s {item_id} 16")
    if pred.labels.get("category") in ("Blocks", "Cabinets"):
        cmds.append(f"/ce debug block {item_id}")
    return cmds


def block_commands(block_id: str, pred: Prediction) -> list[str]:
    cmds = [f"/ce setblock ~ ~ ~ {block_id}", f"/ce debug block {block_id}"]
    if pred.labels.get("block_kind") == "crop":
        cmds.append(f"/ce debug states {block_id}")
    return cmds


# --- singleton --------------------------------------------------------------

_LOCK = threading.Lock()
_BRAIN: Brain | None = None


def get_brain(directory: Path | None = None, reload: bool = False) -> Brain:
    """Return the process-wide brain instance (loaded lazily, thread-safe)."""
    global _BRAIN
    with _LOCK:
        if _BRAIN is None or reload or directory is not None:
            _BRAIN = Brain(directory)
        return _BRAIN
