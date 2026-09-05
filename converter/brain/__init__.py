"""Micro neural network ("brain") for semantic classification during conversion.

The brain answers three questions the rest of the converter used to answer with
long keyword chains:

1. *What is this object?* — item category, gear kind, food family, block kind.
2. *How should CraftEngine represent it?* — auto_state, transparency, whether an
   entity renderer is needed.
3. *What should the user run in-game to check it?* — ready-to-paste CraftEngine
   commands emitted into the conversion log and reports.

Implementation is a small dense multi-head MLP over hashed character n-grams +
structured features, running on NumPy only (no torch / no ONNX). Weights live in
``models/brain/*.npz`` and are a few hundred KB.
"""

from __future__ import annotations

from .runtime import (  # noqa: F401
    Brain,
    BlockPrediction,
    ItemPrediction,
    get_brain,
)

__all__ = ["Brain", "ItemPrediction", "BlockPrediction", "get_brain"]
