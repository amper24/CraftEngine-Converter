"""High-fidelity Farmer's Delight Cutting Board -> SliceBoard generator.

The SliceBoard plugin uses its own compact recipe DSL.  This module deliberately
builds that DSL from the *raw* source cutting recipe instead of the normalized
single-result CraftEngine RecipeNode, because Farmer's Delight 1.21+ cutting
recipes may contain multiple results, per-result chances and compound tool
checks (item ability + fallback tag).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import status, util
from .config import Settings
from .generator import GeneratedFile, dump_yaml
from .analyzer import AnalysisResult


VANILLA_TOOL_TAGS: dict[str, list[str]] = {
    "minecraft:axes": [
        "WOODEN_AXE", "STONE_AXE", "COPPER_AXE", "IRON_AXE",
        "GOLDEN_AXE", "DIAMOND_AXE", "NETHERITE_AXE",
    ],
    "minecraft:pickaxes": [
        "WOODEN_PICKAXE", "STONE_PICKAXE", "COPPER_PICKAXE", "IRON_PICKAXE",
        "GOLDEN_PICKAXE", "DIAMOND_PICKAXE", "NETHERITE_PICKAXE",
    ],
    "minecraft:shovels": [
        "WOODEN_SHOVEL", "STONE_SHOVEL", "COPPER_SHOVEL", "IRON_SHOVEL",
        "GOLDEN_SHOVEL", "DIAMOND_SHOVEL", "NETHERITE_SHOVEL",
    ],
    "minecraft:hoes": [
        "WOODEN_HOE", "STONE_HOE", "COPPER_HOE", "IRON_HOE",
        "GOLDEN_HOE", "DIAMOND_HOE", "NETHERITE_HOE",
    ],
    "minecraft:swords": [
        "WOODEN_SWORD", "STONE_SWORD", "COPPER_SWORD", "IRON_SWORD",
        "GOLDEN_SWORD", "DIAMOND_SWORD", "NETHERITE_SWORD",
    ],
    "minecraft:shears": ["SHEARS"],
}

ABILITY_TO_TOOLS: dict[str, list[str]] = {
    "axe_strip": VANILLA_TOOL_TAGS["minecraft:axes"],
    "axe_dig": VANILLA_TOOL_TAGS["minecraft:axes"],
    "pickaxe_dig": VANILLA_TOOL_TAGS["minecraft:pickaxes"],
    "shovel_dig": VANILLA_TOOL_TAGS["minecraft:shovels"],
    "hoe_dig": VANILLA_TOOL_TAGS["minecraft:hoes"],
    "knife_dig": ["KNIFE"],
    "shears_dig": ["SHEARS"],
}


class SliceBoardGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.warnings: list[dict[str, Any]] = []

    def build(self, analysis: AnalysisResult) -> list[GeneratedFile]:
        if not self.settings.sliceboard_enabled:
            return []

        files: list[GeneratedFile] = []
        ns = analysis.metadata.namespace or "minecraft"
        recipes_by_ns: dict[str, dict[str, Any]] = defaultdict(dict)

        for recipe_id in sorted(analysis.recipes):
            recipe = analysis.recipes[recipe_id]
            if recipe.station != "sliceboard":
                continue
            entry = self._recipe_entry(recipe, analysis)
            if entry is None:
                continue
            recipe_ns = util.split_id(recipe.id)[0]
            recipe_path = util.split_id(recipe.id)[1]
            if recipe_path.startswith("cutting/"):
                recipe_path = recipe_path[len("cutting/"):]
            recipes_by_ns[recipe_ns][recipe_path] = entry

        files.append(self._config_file(ns, analysis))
        for recipe_ns in sorted(recipes_by_ns):
            files.append(
                GeneratedFile(
                    rel_path=f"sliceboard/recipes/{util.safe_name(recipe_ns)}.yml",
                    object_id=f"sliceboard:recipes:{recipe_ns}",
                    domain=status.DOMAIN_SLICEBOARD,
                    content=dump_yaml({"recipes": recipes_by_ns[recipe_ns]}),
                    result=status.TRANSFORM,
                )
            )

        return files

    def _config_file(self, ns: str, analysis: AnalysisResult) -> GeneratedFile:
        block_ids = list(self.settings.sliceboard_block_ids)
        if not block_ids:
            for block_id in sorted(analysis.blocks):
                low = block_id.lower()
                if "cutting_board" in low or "cuttingboard" in low:
                    block_ids.append(block_id)

        config = {
            "debug": False,
            "boards": {
                "ia_furniture_ids": [],
                "craftengine_block_ids": block_ids,
            },
            "defaults": {"tools": []},
            "visual": {"item_display": {"scale": 0.75, "y_offset": -0.425}},
        }
        return GeneratedFile(
            rel_path="sliceboard/config.yml",
            object_id="sliceboard:config",
            domain=status.DOMAIN_SLICEBOARD,
            content=dump_yaml(config),
            result=status.DIRECT,
        )

    def _recipe_entry(self, recipe: Any, analysis: AnalysisResult) -> dict[str, Any] | None:
        raw = recipe.raw if isinstance(recipe.raw, dict) else {}
        input_refs = self._extract_inputs(raw.get("ingredients"), analysis)
        if not input_refs:
            self._warn(recipe, "NO_CUTTING_INPUT")
            return None

        # SliceBoard has a single primary input. If FD ever provides more than
        # one non-tool input, refuse to silently squash the recipe.
        if len(input_refs) > 1:
            self._warn(recipe, "MULTIPLE_INPUTS", inputs=input_refs)
            if self.settings.strict_recipe_mode:
                return None

        entry: dict[str, Any] = {"input": input_refs[0]}
        tools = self._extract_tools(raw.get("tool"), recipe, analysis)
        if tools:
            entry["tools"] = tools

        outputs = self._extract_outputs(raw.get("result"), analysis)
        if not outputs:
            self._warn(recipe, "NO_OUTPUTS")
            return None
        entry["outputs"] = outputs

        # Preserve relevant semantics as comments/diagnostic metadata in a
        # machine-readable report, but keep SliceBoard YAML strictly to its
        # supported schema instead of inventing unknown fields.
        extra = {}
        for key in ("sound", "conditions", "processing", "tool"):
            if key in raw and key not in ("tool",):
                extra[key] = raw[key]
        if extra:
            self._warn(recipe, "EXTRA_SOURCE_METADATA", metadata=extra)

        return entry

    def _extract_inputs(self, ingredients: Any, analysis: AnalysisResult) -> list[str]:
        if not isinstance(ingredients, list):
            return []
        found: list[str] = []
        for ing in ingredients:
            for ref in self._ingredient_refs(ing):
                if self._looks_like_tool(ref):
                    continue
                converted = self._to_item(ref, analysis)
                if converted and converted not in found:
                    found.append(converted)
        return found

    def _ingredient_refs(self, ing: Any) -> list[str]:
        if isinstance(ing, str):
            return [ing]
        if isinstance(ing, dict):
            if ing.get("item"):
                return [str(ing["item"])]
            if ing.get("tag"):
                tag = str(ing["tag"])
                return [tag if tag.startswith("#") else f"#{tag}"]
            if ing.get("id"):
                return [str(ing["id"])]
        if isinstance(ing, list):
            out: list[str] = []
            for x in ing:
                out.extend(self._ingredient_refs(x))
            return out
        return []

    def _extract_outputs(self, raw_result: Any, analysis: AnalysisResult) -> list[dict[str, Any]]:
        entries = raw_result if isinstance(raw_result, list) else [raw_result]
        outputs: list[dict[str, Any]] = []
        for value in entries:
            item_id, count, chance = self._normalize_output(value)
            if not item_id:
                continue
            converted = self._to_item(item_id, analysis)
            if not converted:
                continue
            count = max(1, int(count))
            chance = max(0.0, min(1.0, float(chance)))
            outputs.append({"item": converted, "min": count, "max": count, "chance": chance})
        return outputs

    @staticmethod
    def _normalize_output(value: Any) -> tuple[str | None, int, float]:
        if isinstance(value, str):
            return value, 1, 1.0
        if not isinstance(value, dict):
            return None, 1, 1.0

        chance = value.get("chance", 1.0)
        try:
            chance_f = float(chance)
        except (TypeError, ValueError):
            chance_f = 1.0

        inner = value.get("item")
        if isinstance(inner, dict):
            item_id = inner.get("id") or inner.get("item")
            try:
                count = int(inner.get("count", value.get("count", 1)))
            except (TypeError, ValueError):
                count = 1
            return (str(item_id) if item_id else None), count, chance_f
        item_id = inner or value.get("id")
        try:
            count = int(value.get("count", 1))
        except (TypeError, ValueError):
            count = 1
        return (str(item_id) if item_id else None), count, chance_f

    def _extract_tools(self, raw_tool: Any, recipe: Any, analysis: AnalysisResult) -> list[str]:
        refs: list[str] = []
        abilities: list[str] = []
        if isinstance(raw_tool, list):
            values = raw_tool
        else:
            values = [raw_tool] if raw_tool is not None else []

        for value in values:
            if not isinstance(value, dict):
                continue
            if value.get("tag"):
                tag = str(value["tag"])
                tag = tag if tag.startswith("#") else f"#{tag}"
                expanded = VANILLA_TOOL_TAGS.get(tag[1:])
                if expanded:
                    for material in expanded:
                        if material not in refs:
                            refs.append(material)
                elif tag.lower() in ("#c:tools/knife", "#c:tools/knives", "#farmersdelight:tools/knives"):
                    # Farmer's Delight 1.21+ uses a compound knife check.
                    # Resolve it to the configured/known FD knife item instead
                    # of throwing away the tool restriction.
                    prefix = self.settings.sliceboard_namespace_prefixes.get("farmersdelight", "craftengine:farmersdelight")
                    knife = f"{prefix}:knife"
                    if knife not in refs:
                        refs.append(knife)
                elif tag.lower() in ("#c:tools/cleaver", "#c:tools/cleavers"):
                    prefix = self.settings.sliceboard_namespace_prefixes.get("farmersdelight", "craftengine:farmersdelight")
                    cleaver = f"{prefix}:cleaver"
                    if cleaver not in refs:
                        refs.append(cleaver)
                else:
                    self._warn(recipe, "UNRESOLVED_TOOL_TAG", tag=tag)
            if value.get("type") == "farmersdelight:item_ability" and value.get("action"):
                abilities.append(str(value["action"]))

        for ability in abilities:
            for material in ABILITY_TO_TOOLS.get(ability, []):
                if material not in refs:
                    refs.append(material)
            if ability not in ABILITY_TO_TOOLS:
                self._warn(recipe, "UNRESOLVED_TOOL_ABILITY", action=ability)

        # Explicit source tool item can be accepted too.
        for value in values:
            if isinstance(value, dict) and value.get("item"):
                ref = str(value["item"])
                if util.split_id(ref)[0] == "minecraft":
                    material = util.split_id(ref)[1].upper()
                else:
                    material = self._custom_tool_ref(ref)
                if material and material not in refs:
                    refs.append(material)

        # Stable output order matching the example format: vanilla first, then custom.
        return refs

    def _to_item(self, ref: str, analysis: AnalysisResult) -> str:
        if ref.startswith("#"):
            # SliceBoard's example format is item-based. Tags are legal in the
            # source but cannot be losslessly represented as an input/output item.
            raw = ref[1:]
            ns, path = util.split_id(raw)
            self._warn(None, "TAG_REFERENCE_IN_ITEM_POSITION", tag=ref)
            if ns == "minecraft":
                return f"#{ns}:{path}"
            return f"#{ns}:{path}"

        ns, path = util.split_id(ref)
        if ns == "minecraft":
            # Any vanilla item ID is accepted; this intentionally avoids a short
            # hard-coded material list and therefore keeps working as Minecraft evolves.
            return path.upper()

        prefix = self.settings.sliceboard_namespace_prefixes.get(ns)
        if not prefix:
            prefix = self.settings.sliceboard_custom_provider or "craftengine"
            prefix = f"{prefix}:{ns}" if not prefix.endswith(f":{ns}") else prefix
        return f"{prefix}:{path}"

    def _custom_tool_ref(self, ref: str) -> str:
        ns, path = util.split_id(ref)
        prefix = self.settings.sliceboard_namespace_prefixes.get(ns)
        if prefix:
            return f"{prefix}:{path}"
        provider = self.settings.sliceboard_custom_provider or "craftengine"
        return f"{provider}:{ns}:{path}"

    def _looks_like_tool(self, ref: str) -> bool:
        low = ref.lower().removeprefix("#")
        if low in VANILLA_TOOL_TAGS:
            return True
        path = util.split_id(low)[1]
        return any(keyword in path for keyword in self.settings.sliceboard_tool_keywords)

    def _warn(self, recipe: Any | None, reason: str, **details: Any) -> None:
        self.warnings.append({"recipe": getattr(recipe, "id", None), "reason": reason, **details})


def generate_sliceboard(analysis: AnalysisResult, settings: Settings) -> list[GeneratedFile]:
    generator = SliceBoardGenerator(settings)
    files = generator.build(analysis)
    # Diagnostics are intentionally attached to the AnalysisResult metadata by
    # the driver after generation so the normal GenerationResult remains focused
    # on actual generated files.
    setattr(analysis, "sliceboard_warnings", generator.warnings)
    return files
