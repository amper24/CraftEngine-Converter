"""Translation helpers for modern Minecraft 1.21.4+ item model definitions."""
from __future__ import annotations
from typing import Any


def translate_item_model_definition(data: dict[str, Any], default_ns: str) -> dict[str, Any] | None:
    """Translate a vanilla items/*.json definition into CraftEngine YAML model tree.

    The transformation is intentionally structural: unknown fields are retained
    where possible, while the vanilla `model` leaf is renamed to CE's `path`.
    This preserves condition/select/range_dispatch/composite trees instead of
    collapsing them to a single static model.
    """
    root = data.get("model", data)
    if not isinstance(root, dict):
        return None
    return _node(root, default_ns)


def _node(node: dict[str, Any], default_ns: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    typ = node.get("type")
    if isinstance(typ, str):
        out["type"] = typ

    for key, value in node.items():
        if key == "type":
            continue
        if key in ("model", "on_true", "on_false", "fallback") and isinstance(value, dict):
            out["path" if key == "model" and typ == "minecraft:model" else key] = _node(value, default_ns)
        elif key in ("models", "entries", "cases") and isinstance(value, list):
            out[key] = [_node(v, default_ns) if isinstance(v, dict) else v for v in value]
        elif key == "model" and isinstance(value, str):
            out["path"] = _qualify(value, default_ns)
        elif isinstance(value, dict):
            out[key] = _node(value, default_ns)
        elif isinstance(value, list):
            out[key] = [_node(v, default_ns) if isinstance(v, dict) and "type" in v else v for v in value]
        else:
            out[key] = _qualify(value, default_ns) if key in {"asset_id", "texture"} and isinstance(value, str) else value
    return out


def _qualify(value: str, default_ns: str) -> str:
    return value if ":" in value else f"{default_ns}:{value}"
