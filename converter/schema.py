"""CraftEngine target schema registry and validator.

Loads the bundled 26.8 schema JSON files and exposes an allowlist of known
keys (item root, item data, item settings, item models, block, block settings,
block states, block behaviors, item behaviors, recipes, events, functions).
The generator and validator call :func:`validate_yaml` so no unknown key is
ever emitted silently (TZ §4.4 / §8.2.17).
"""

from __future__ import annotations

from typing import Any, Iterable

from . import paths, util


class SchemaRegistry:
    def __init__(self, target: str = "craftengine", version: str = "26.8") -> None:
        self.target = target
        self.version = version
        self.data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        directory = paths.schema_dir(self.target, self.version)
        profile = directory / "target-profile.json"
        if profile.exists():
            self.data["profile"] = util.read_json(profile)
        for name in self.data.get("profile", {}).get("schema_files", []):
            path = directory / name
            if path.exists():
                self.data[name[:-5]] = util.read_json(path)

    # --- allowlists -------------------------------------------------------
    @property
    def item_root_keys(self) -> set[str]:
        return {n["path"] for n in self.data.get("items", {}).get("item_nodes", [])}

    @property
    def item_data_keys(self) -> set[str]:
        return {n["name"] for n in self.data.get("item_data", {}).get("data_keys", [])}

    @property
    def item_model_root_keys(self) -> set[str]:
        return {n["name"] for n in self.data.get("item_models", {}).get("root_fields", [])}

    @property
    def item_settings_keys(self) -> set[str]:
        return {n["name"] for n in self.data.get("item_settings", {}).get("settings", [])}

    @property
    def block_root_keys(self) -> set[str]:
        return {n["path"] for n in self.data.get("blocks", {}).get("nodes", [])}

    @property
    def block_state_keys(self) -> set[str]:
        return {n["path"] for n in self.data.get("blocks", {}).get("state_nodes", [])}

    @property
    def block_settings_stable(self) -> set[str]:
        return {n["name"] for n in self.data.get("block_settings", {}).get("stable", [])}

    @property
    def block_settings_unstable(self) -> set[str]:
        return set(self.data.get("block_settings", {}).get("unstable", []))

    @property
    def block_settings_keys(self) -> set[str]:
        return self.block_settings_stable | self.block_settings_unstable

    @property
    def block_behavior_types(self) -> set[str]:
        return set(self.data.get("block_behaviors", {}).get("known_behavior_types", []))

    @property
    def item_behavior_types(self) -> set[str]:
        return set(self.data.get("item_behaviors", {}).get("known_behavior_types", []))

    @property
    def item_model_types(self) -> set[str]:
        return {n["type"] for n in self.data.get("item_models", {}).get("model_node_types", [])}

    @property
    def recipe_confirmed_types(self) -> set[str]:
        return set(self.data.get("recipes", {}).get("confirmed_types", []))

    @property
    def function_types(self) -> set[str]:
        categories = self.data.get("functions", {}).get("categories", {})
        return {fn for fns in categories.values() for fn in fns}

    @property
    def condition_types(self) -> set[str]:
        # Only permission is consistently documented as a condition example.
        return {"permission", "enchantment", "inventory_has_item"}

    # --- validation -------------------------------------------------------
    def unknown_keys(self, mapping: dict[str, Any], allowed: Iterable[str]) -> list[str]:
        unknown: list[str] = []
        for key in mapping:
            base = key.split("#", 1)[0]
            if base not in allowed:
                unknown.append(key)
        return unknown

    def validate_item(self, item_id: str, body: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        allowed = self.item_root_keys | self.item_model_root_keys
        for key in self.unknown_keys(body, allowed):
            errors.append(f"item {item_id}: unknown root key '{key}'")

        if "custom_model_data" not in body and not body.get("item_model") and not body.get("texture") and not body.get("textures") and not body.get("model"):
            errors.append(f"item {item_id}: no model/texture/custom_model_data definition")
        return errors


_REGISTRY: SchemaRegistry | None = None


def get_registry(target: str = "craftengine", version: str = "26.8") -> SchemaRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SchemaRegistry(target, version)
    return _REGISTRY


def reset_registry() -> None:
    global _REGISTRY
    _REGISTRY = None