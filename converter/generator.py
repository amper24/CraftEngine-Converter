"""CraftEngine translators: turn IR nodes into validated CraftEngine YAML.

Only documented keys are emitted. Each generated file contains exactly one
object id (one item / one block / one recipe / one loot table).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import capability, schema, status, util
from .analyzer import AnalysisResult
from .ir import BlockNode, BlockStateNode, FurnitureNode, ItemNode, LootNode, RecipeNode


@dataclass
class GeneratedFile:
    rel_path: str
    object_id: str
    domain: str
    content: str
    result: str
    source_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.rel_path,
            "object_id": self.object_id,
            "domain": self.domain,
            "result": self.result,
        }


@dataclass
class GenerationResult:
    files: list[GeneratedFile] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def add_file(self, f: GeneratedFile) -> None:
        self.files.append(f)


class OrderedDumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


OrderedDumper.add_representer(str, _represent_str)


def dump_yaml(obj: Any) -> str:
    return yaml.dump(obj, Dumper=OrderedDumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)


def _cmd_counter() -> "object":
    class Counter:
        def __init__(self) -> None:
            self.value = 10000

        def next(self) -> int:
            v = self.value
            self.value += 1
            return v

    return Counter()


class Generator:
    # Canonical category order, shared by the neural head and the emitter.
    _category_names: tuple[str, ...] = (
        "Crops", "Ingredients", "Drinks", "Food", "Meals", "Sweets",
        "Tools", "Weapons", "Ranged", "Spears", "Tridents", "Shields",
        "Armor", "Blocks", "Cabinets", "Items",
    )

    def __init__(self, analysis: AnalysisResult, mapping: capability.MappingResult, settings: Any = None) -> None:
        self.analysis = analysis
        self.mapping = mapping
        self.registry = schema.get_registry()
        self.cmd = _cmd_counter()
        from .config import Settings

        self.settings = settings or Settings()
        # Which import adapter produced this IR. Read defensively so the
        # generator also works with the lightweight analysis stubs the tests
        # use, matching how the packager reads optional analysis fields.
        self.source_kind = getattr(analysis, "source_kind", "mod")

    def generate(self) -> GenerationResult:
        out = GenerationResult()

        # Stable ordering.
        for item_id in sorted(self.analysis.items):
            self._generate_item(out, self.analysis.items[item_id])
        for block_id in sorted(self.analysis.blocks):
            self._generate_block(out, self.analysis.blocks[block_id])
        for furniture_id in sorted(getattr(self.analysis, "furniture", {}) or {}):
            self._generate_furniture(out, self.analysis.furniture[furniture_id])
        self._generate_sounds(out)

        # Stable domain ordering. Categories and their translations always
        # remain at the end of the generated package.
        domain_order = {status.DOMAIN_ITEM: 10, status.DOMAIN_BLOCK: 20, status.DOMAIN_FURNITURE: 25, "sound": 30, status.DOMAIN_RECIPE: 40, status.DOMAIN_LOOT: 50, status.DOMAIN_SLICEBOARD: 60, "category": 90, "lang": 95}
        mode = getattr(self.settings, "recipe_sort_mode", "type_then_id")
        def sort_key(f: GeneratedFile):
            if f.domain == status.DOMAIN_RECIPE:
                if mode == "id":
                    return (50, "", f.object_id.lower())
                if mode == "type":
                    parts = f.rel_path.lower().split("/")
                    return (50, "/".join(parts[:-1]), f.object_id.lower())
                parts = f.rel_path.lower().split("/")
                return (50, "/".join(parts[:-1]), f.object_id.lower())
            return (domain_order.get(f.domain, 99), f.rel_path.lower(), f.object_id.lower())
        out.files.sort(key=sort_key)
        return out

    def _generate_sounds(self, out: GenerationResult) -> None:
        sounds = getattr(self.analysis, "sounds", {}) or {}
        if not isinstance(sounds, dict) or not sounds:
            return
        for event_id, spec in sorted(sounds.items()):
            if isinstance(spec, dict):
                value = _ordered_from(spec)
            elif isinstance(spec, list):
                value = {"sounds": list(spec)}
            else:
                value = {"sounds": [spec]}
            safe = util.safe_name(str(event_id).replace(":", "__").replace("/", "_"))
            out.add_file(GeneratedFile(
                rel_path=f"configuration/sounds/{self.analysis.metadata.namespace}/{safe}.yml",
                object_id=str(event_id),
                domain="sound",
                content=dump_yaml({"sounds": {str(event_id): value}}),
                result=status.DIRECT,
            ))

    # --- items ------------------------------------------------------------
    def _generate_item(self, out: GenerationResult, item: ItemNode) -> None:
        decision = self.mapping.get(item.id)
        result = decision.result if decision else status.DIRECT
        ns, path = util.split_id(item.id)

        body = _ordered()

        # Custom items normally have NO material. Two semantic exceptions are
        # intentional: drinks use a honey bottle carrier and soups use a
        # mushroom stew carrier, matching the project's requested baseline.
        is_block_item = bool(item.behavior and item.behavior.get("type") == "block_item")
        carrier_material = self._semantic_carrier_material(item)
        if carrier_material and not is_block_item:
            body["material"] = carrier_material
        elif (
            item.base_material
            and self.source_kind == "itemsadder"
            and getattr(self.settings, "ia_preserve_material", True)
        ):
            # ItemsAdder's `resource.material` is source data, not an invention:
            # it selects the vanilla item the custom item is built on, which
            # decides swing speed, armor slot, potion behaviour, etc. Dropping
            # it would silently change the item, so it is preserved - including
            # for block items, where the mod path deliberately omits material.
            body["material"] = item.base_material

        # CraftEngine's model system is the source of truth. For 1.21.4+
        # packs, configure `model`/`texture` and let CraftEngine generate the
        # modern item_model definition and legacy compatibility when needed.
        if isinstance(item.model_tree, dict):
            body["model"] = item.model_tree
        elif item.gui_icon_texture and item.model:
            # One item, two visual contexts: a flat generated icon in GUI and
            # the original 3D model everywhere else. CraftEngine's select model
            # property is exactly intended for display-context switching.
            body["model"] = {
                "type": "minecraft:select",
                "property": "minecraft:display_context",
                "cases": [
                    {
                        "when": "gui",
                        "model": {
                            "type": "minecraft:model",
                            "path": f"{ns}:item/{path}__gui_icon",
                            "generation": {
                                "parent": "minecraft:item/generated",
                                "textures": {"layer0": item.gui_icon_texture},
                            },
                        },
                    }
                ],
                "fallback": {"type": "minecraft:model", "path": item.model},
            }
        elif item.model:
            body["model"] = {"type": "minecraft:model", "path": item.model}
        elif item.textures:
            # CraftEngine's compact texture form is ideal for normal 2D items.
            if len(item.textures) == 1:
                body["texture"] = item.textures[0]
            else:
                # Layered source models are safest when kept as a real model;
                # never invent a fake texture path.
                body["textures"] = list(item.textures)
        elif is_block_item:
            pass

        data = _ordered()
        lang_key = self._item_lang_key(item)
        if lang_key:
            data["item_name"] = f"<!i><lang:{lang_key}>"
        elif item.display_name:
            data["item_name"] = f"<!i>{item.display_name}"

        # Prefer exact values extracted from compiled bytecode. Only use the
        # old keyword-based baseline when the mod does not expose exact food
        # metadata.
        is_display = bool(item.metadata.get("display_item") or item.metadata.get("stage") is not None)
        # Entity-rendered stage/display items need a stable vanilla carrier.
        # This is intentionally limited to generated helpers; normal custom
        # items remain material-less per the converter policy.
        if is_display and not is_block_item:
            body.setdefault("material", "minecraft:stick")
        if item.food is not None and not is_block_item and not is_display:
            data["food"] = dict(item.food)
            data["consumable"] = {}
        elif self._is_food(item) and not is_block_item and not is_display:
            data["food"] = {
                "nutrition": self.settings.food_nutrition,
                "saturation": self.settings.food_saturation,
                "can_always_eat": False,
            }
            data["consumable"] = {}

        if item.gear_kind in ("tool", "weapon", "spear", "trident", "shield", "armor"):
            # max_stack_size is a Minecraft component, but this CE target schema
            # does not expose it as a canonical data key; do not emit an unknown key.
            if item.durability:
                data.setdefault("max_damage", item.durability)
            if item.attack_damage is not None:
                mods = data.setdefault("attribute_modifiers", [])
                mods.append({"type": "attack_damage", "amount": item.attack_damage, "operation": "add_value", "slot": "mainhand", "id": f"{item.id}/attack_damage"})
            if item.attack_speed is not None:
                mods = data.setdefault("attribute_modifiers", [])
                mods.append({"type": "attack_speed", "amount": item.attack_speed, "operation": "add_value", "slot": "mainhand", "id": f"{item.id}/attack_speed"})
        if item.gear_kind == "armor":
            slot = next((x for x in ("helmet","chestplate","leggings","boots") if x in item.id.split(":")[-1].lower()), None)
            if slot:
                data.setdefault("equippable", {"slot": {"helmet":"head","chestplate":"chest","leggings":"legs","boots":"feet"}[slot]})

        # Generic data-component emission for values the source adapter placed
        # directly on the IR node. Each of these is only written when the source
        # actually declared it, so conversions that never populate them (the
        # mod adapter) are unaffected.
        if item.attributes:
            # Source-declared modifiers win over anything inferred earlier
            # (gear heuristics / brain), matched on type+slot so the same
            # attribute is never emitted twice.
            source_keys = {(m.get("type"), m.get("slot", "mainhand")) for m in item.attributes}
            kept = [
                m for m in data.get("attribute_modifiers", [])
                if (m.get("type"), m.get("slot", "mainhand")) not in source_keys
            ]
            data["attribute_modifiers"] = kept + list(item.attributes)
        if item.equip:
            equippable = dict(data.get("equippable") or {})
            equippable.update(item.equip)
            data["equippable"] = equippable
        if item.enchantments:
            data["enchantments"] = {k: v for k, v in sorted(item.enchantments.items())}
        if item.lore and not data.get("lore"):
            data["lore"] = [f"<!i>{line}" for line in item.lore]
        for key, value in (item.components or {}).items():
            if value is None:
                continue
            if key in ("hide_tooltip", "unbreakable", "dyed_color", "tooltip_style", "jukebox_playable"):
                data[key] = value
            elif key == "raw":
                raw_components = dict(data.get("components") or {})
                raw_components.update(value)
                data["components"] = raw_components
            else:
                components = dict(data.get("components") or {})
                components[key] = value
                data["components"] = components
        if item.food is not None and self.source_kind == "itemsadder":
            details = (item.metadata or {}).get("consumable") or {}
            if details and getattr(self.settings, "ia_emit_consumable_details", True):
                consumable = dict(data.get("consumable") or {})
                consumable.update(details)
                data["consumable"] = consumable
        if (
            self.source_kind == "itemsadder"
            and getattr(self.settings, "ia_force_custom_model_data", False)
        ):
            cmd = ((item.metadata or {}).get("itemsadder") or {}).get("custom_model_data")
            if isinstance(cmd, int):
                body["custom_model_data"] = cmd

        if data:
            body["data"] = data

        settings = _ordered()
        if item.tool_tier:
            settings["break_power"] = _break_power(item.tool_tier)
        if item.gear_kind in ("tool", "weapon", "spear", "trident"):
            settings.setdefault("enchantable", True)
        settings.update(_ordered_from(item.settings) if item.settings else _ordered())
        if settings:
            body["settings"] = settings

        if item.behavior:
            body["behavior"] = _ordered_from(item.behavior)
        events = getattr(item, "events", None)
        if events:
            body["events"] = list(events)

        category = self._item_category(item)
        entry = {item.id: dict(body)}
        doc = {_k: _v for _k, _v in [("items", entry)]}
        content = dump_yaml(doc)

        out.add_file(
            GeneratedFile(
                rel_path=f"configuration/{category}/{ns}/{util.safe_name(path)}.yml",
                object_id=item.id,
                domain=status.DOMAIN_ITEM,
                content=content,
                result=result,
            )
        )

    def _category_from_tags(self, item: ItemNode) -> str | None:
        """Prefer semantic tags from the source mod over name heuristics."""
        tag_rules = (
            ("tools", "Tools"), ("weapons", "Weapons"), ("ranged", "Ranged"),
            ("spears", "Spears"), ("tridents", "Tridents"), ("shields", "Shields"),
            ("armor", "Armor"), ("crops", "Crops"), ("ingredients", "Ingredients"),
            ("drinks", "Drinks"), ("meals", "Meals"), ("sweets", "Sweets"),
            ("foods", "Food"), ("food", "Food"), ("blocks", "Blocks"),
            ("cabinets", "Cabinets"),
        )
        normalized = [str(t).lower().lstrip("#") for t in getattr(item, "tags", [])]
        for tag in normalized:
            path = tag.split(":", 1)[-1] if ":" in tag else tag
            for suffix, category in tag_rules:
                if path == suffix or path.endswith("/" + suffix):
                    return category
        return None

    def _category_from_hints(self, item: ItemNode) -> str | None:
        """Use source recipe-book categories when the source mod provides them.

        Recipe JSON ``category``/``recipe_book_tab`` values are the most
        accurate "what is this item for" signal available without a full
        semantic model, so they take priority over name heuristics.
        """
        hint_rules = {
            "tools": "Tools", "tool": "Tools",
            "combat": "Weapons", "weapon": "Weapons", "weapons": "Weapons",
            "food": "Food", "foods": "Food",
            "building_blocks": "Blocks", "blocks": "Blocks",
            "redstone": "Items", "misc": "Items", "items": "Items",
            "brewing": "Drinks", "drinks": "Drinks",
            "ingredients": "Ingredients", "ingredient": "Ingredients",
            "meals": "Meals", "meal": "Meals",
        }
        for hint in getattr(item, "category_hints", []):
            normalized = str(hint).lower().removeprefix("minecraft:")
            return hint_rules.get(normalized)
        return None

    def _category_from_brain(self, item: ItemNode) -> str | None:
        """Neural category, used when the source provides no explicit signal.

        The brain reads the whole id (head noun, qualifiers, morphology) plus
        the structured features the analyzer collected, which generalizes to
        vocabulary no keyword list covers. It is consulted *after* tags and
        recipe-book hints and only above the configured confidence threshold.
        """
        if not getattr(self.settings, "brain_categories", True):
            return None
        record = (item.metadata or {}).get("brain")
        if not isinstance(record, dict):
            return None
        category = (record.get("applied") or {}).get("category")
        return category if category in self._category_names else None

    # --- categories -------------------------------------------------------
    def _generate_source_categories(self, out: GenerationResult) -> bool:
        """Generate categories from the source pack's own category definitions.

        ItemsAdder ships explicit ``categories:`` sections (the /ia GUI
        grouping) including wildcards and regex membership. Those are more
        accurate than anything we could infer, so they win over semantic
        grouping whenever the source provides them.
        """
        source_categories = getattr(self.analysis, "source_categories", {}) or {}
        if not source_categories:
            return False

        generated_item_ids = {
            str(f.object_id) for f in out.files
            if f.domain == status.DOMAIN_ITEM and f.object_id
        }
        if not generated_item_ids:
            return False

        emitted = False
        for ns, categories in sorted(source_categories.items()):
            cats: dict[str, dict[str, Any]] = {}
            priority = 1
            for cat_id, cat in sorted(categories.items()):
                members = self._expand_source_members(cat.get("items"), ns, generated_item_ids)
                if not members:
                    continue
                icon = self._resolve_source_icon(cat.get("icon"), ns, members, generated_item_ids)
                name = cat.get("name")
                entry = _ordered()
                entry["priority"] = priority
                entry["name"] = f"<!i>{name}" if name else f"<!i>{cat_id.replace('_', ' ').title()}"
                if cat.get("title"):
                    entry["lore"] = [f"<!i>{cat['title']}"]
                entry["icon"] = icon
                entry["list"] = members
                cats[f"{ns}:{util.safe_name(str(cat_id))}"] = entry
                priority += 1
            if not cats:
                continue
            emitted = True
            out.add_file(GeneratedFile(
                rel_path=f"configuration/categories/{ns}.yml",
                object_id=f"{ns}:source_categories",
                domain="category",
                content=dump_yaml({"categories": cats}),
                result=status.DIRECT,
            ))
        return emitted

    def _expand_source_members(
        self, patterns: Any, ns: str, generated_item_ids: set[str]
    ) -> list[str]:
        """Resolve an ItemsAdder category ``items`` list to concrete ids.

        ItemsAdder accepts literal ids, ``namespace:*`` wildcards and full
        regular expressions; all three are resolved against the items that were
        actually generated so no category can reference a dropped object.
        """
        if not isinstance(patterns, list):
            return []
        members: list[str] = []
        for pattern in patterns:
            text = str(pattern).strip()
            if not text:
                continue
            if text.endswith(":*"):
                prefix = text[:-1]
                members.extend(i for i in generated_item_ids if i.startswith(prefix))
            elif ":" not in text:
                candidate = f"{ns}:{text}"
                if candidate in generated_item_ids:
                    members.append(candidate)
            elif text in generated_item_ids:
                members.append(text)
            else:
                # Treat anything else as a regex, which is how ItemsAdder
                # documents advanced membership rules.
                try:
                    matcher = re.compile(text)
                except re.error:
                    continue
                members.extend(i for i in generated_item_ids if matcher.fullmatch(i))
        return sorted(dict.fromkeys(members), key=str.lower)

    def _resolve_source_icon(
        self, icon: Any, ns: str, members: list[str], generated_item_ids: set[str]
    ) -> str:
        """Pick a CraftEngine category icon (always a generated item id)."""
        if icon:
            text = str(icon).strip()
            if text in generated_item_ids:
                return text
            if ":" not in text and f"{ns}:{text}" in generated_item_ids:
                return f"{ns}:{text}"
        return members[0]

    def _generate_categories(self, out: GenerationResult) -> None:
        """Generate CraftEngine categories as the final generation phase.

        Layout intentionally mirrors the hand-authored reference:
          namespace:main
            list: ['#namespace:tools', ...]
          namespace:tools
            hidden: true
            list: [namespace:item1, namespace:item2, ...]

        Categories are built from *actually generated* item objects, not just
        analyzer discoveries. This prevents empty/stale categories after a
        filtering pass (excluded namespaces, unresolved entries, display-only
        stages, etc.).
        """
        # A source pack that declares its own categories (ItemsAdder) is more
        # authoritative than semantic inference, so those win outright.
        if self._generate_source_categories(out):
            return

        names = list(self._category_names)
        labels = {
            "Crops": ("crops", "Растения", "Crops", "#7CB342"),
            "Ingredients": ("ingredients", "Ингредиенты", "Ingredients", "#C9A55C"),
            "Drinks": ("drinks", "Напитки", "Drinks", "#87CEEB"),
            "Food": ("food", "Еда", "Food", "#FFB74D"),
            "Meals": ("meals", "Блюда", "Meals", "#E57373"),
            "Sweets": ("sweets", "Сладости", "Sweets", "#F48FB1"),
            "Tools": ("tools", "Инструменты", "Tools", "#90A4AE"),
            "Weapons": ("weapons", "Оружие", "Weapons", "#EF5350"),
            "Ranged": ("ranged", "Дальний бой", "Ranged", "#FFCA28"),
            "Spears": ("spears", "Копья", "Spears", "#8D6E63"),
            "Tridents": ("tridents", "Трезубцы", "Tridents", "#26A69A"),
            "Shields": ("shields", "Щиты", "Shields", "#78909C"),
            "Armor": ("armor", "Броня", "Armor", "#B0BEC5"),
            "Blocks": ("blocks", "Блоки", "Blocks", "#A1887F"),
            "Cabinets": ("cabinets", "Шкафы", "Cabinets", "#B08968"),
            "Items": ("items", "Предметы", "Items", "#BDBDBD"),
        }

        # Only IDs that really have an emitted item file make it into a category.
        generated_item_ids = {
            str(f.object_id) for f in out.files
            if f.domain == status.DOMAIN_ITEM and f.object_id
        }
        generated_items = {
            item_id: item for item_id, item in self.analysis.items.items()
            if item_id in generated_item_ids
        }

        groups_by_ns: dict[str, dict[str, list[str]]] = {}
        for item in generated_items.values():
            # Rendering helpers/stage carriers are implementation details, not
            # user-facing category members.
            if item.metadata.get("display_item") or item.metadata.get("stage") is not None:
                continue
            g = groups_by_ns.setdefault(item.namespace, {n: [] for n in names})
            kind = getattr(item, "gear_kind", None)
            path = item.id.split(":", 1)[-1].lower()
            is_block_item = bool(item.behavior and item.behavior.get("type") == "block_item")

            tag_category = self._category_from_tags(item)
            hint_category = self._category_from_hints(item)
            brain_category = self._category_from_brain(item)
            if tag_category:
                g[tag_category].append(item.id)
            elif hint_category:
                g[hint_category].append(item.id)
            elif kind == "tool":
                g["Tools"].append(item.id)
            elif kind == "weapon":
                g["Weapons"].append(item.id)
            elif kind in ("bow", "crossbow"):
                g["Ranged"].append(item.id)
            elif kind == "spear":
                g["Spears"].append(item.id)
            elif kind == "trident":
                g["Tridents"].append(item.id)
            elif kind == "shield":
                g["Shields"].append(item.id)
            elif kind == "armor":
                g["Armor"].append(item.id)
            elif is_block_item:
                # Cabinets are identified before the generic block bucket.
                g["Cabinets" if "cabinet" in path else "Blocks"].append(item.id)
            elif item.food is not None or self._is_food(item):
                family = self._food_family(item)
                if any(k in path for k in self.settings.drink_keywords):
                    g["Drinks"].append(item.id)
                elif any(k in path for k in self.settings.soup_keywords) or any(k in path for k in ("stew", "chowder", "hotpot", "bisque")):
                    g["Meals"].append(item.id)
                elif any(k in path for k in ("pie", "cookie", "cake", "cheesecake", "popsicle", "custard", "sweet")):
                    g["Sweets"].append(item.id)
                elif family == "drink":
                    g["Drinks"].append(item.id)
                elif family == "soup":
                    g["Meals"].append(item.id)
                elif family == "sweet":
                    g["Sweets"].append(item.id)
                else:
                    g["Food"].append(item.id)
            elif any(k in path for k in ("seed", "seeds", "wild_", "sprout", "cactus", "crop", "panicle")):
                g["Crops"].append(item.id)
            elif any(k in path for k in ("dough", "slice", "leaf", "bark", "straw", "canvas", "patty", "cut", "minced", "chops")):
                g["Ingredients"].append(item.id)
            elif brain_category:
                # Nothing in the source named this object; the network decides
                # instead of dumping it into the generic "Items" bucket.
                g[brain_category].append(item.id)
            else:
                g["Items"].append(item.id)

        for ns, raw_groups in sorted(groups_by_ns.items()):
            groups = {k: sorted(set(v), key=str.lower) for k, v in raw_groups.items() if v}
            if not groups:
                continue

            cats: dict[str, dict[str, Any]] = {}
            # Main category: references to child categories, exactly like the
            # supplied hand-written reference.
            main_id = f"{ns}:main"
            main = _ordered()
            main["priority"] = 1
            main["name"] = f"<!i><white><l10n:category.{ns}.name></white>"
            main["lore"] = [f"<!i><gray><l10n:category.{ns}.lore></gray>"]
            main["icon"] = next(iter(next(iter(groups.values()))))
            main["list"] = [f"#{ns}:{labels[k][0]}" for k in groups]
            cats[main_id] = main

            priority = 10
            for label in names:
                if label not in groups:
                    continue
                slug, ru_name, en_name, hex_color = labels[label]
                ids = groups[label]
                cat = _ordered()
                cat["name"] = f"<!i><{hex_color}><l10n:category.{ns}.{slug}></{hex_color}>"
                cat["hidden"] = True
                cat["icon"] = ids[0]
                cat["list"] = ids
                cats[f"{ns}:{slug}"] = cat
                priority += 1

            out.add_file(GeneratedFile(
                rel_path=f"configuration/categories/{ns}.yml",
                object_id=main_id,
                domain="category",
                content=dump_yaml({"categories": cats}),
                result=status.DIRECT,
            ))

            if self.settings.generate_category_translations:
                self._category_translation_files(out, ns, groups, labels)

    def _category_translation_files(
        self,
        out: GenerationResult,
        ns: str,
        groups: dict[str, list[str]],
        labels: dict[str, tuple[str, str, str, str]],
    ) -> None:
        # The reference uses the l10n: namespace directly. These keys are later
        # merged into assets/<namespace>/lang/{en_us,ru_ru}.json by the packager.
        display_name = ns.replace("_", " ").title()
        en = {f"category.{ns}.name": display_name, f"category.{ns}.lore": f"{display_name} items"}
        ru = {f"category.{ns}.name": display_name, f"category.{ns}.lore": f"Предметы мода {display_name}"}
        for label in groups:
            slug, ru_name, en_name, _ = labels[label]
            en[f"category.{ns}.{slug}"] = en_name
            ru[f"category.{ns}.{slug}"] = ru_name
        out.add_file(GeneratedFile(
            rel_path=f"configuration/translations/{ns}/categories_en_us.yml",
            object_id=f"{ns}:categories_en_us",
            domain="lang",
            content=dump_yaml({"translations": {"en_us": en}}),
            result=status.DIRECT,
        ))
        out.add_file(GeneratedFile(
            rel_path=f"configuration/translations/{ns}/categories_ru_ru.yml",
            object_id=f"{ns}:categories_ru_ru",
            domain="lang",
            content=dump_yaml({"translations": {"ru_ru": ru}}),
            result=status.DIRECT,
        ))

    # --- blocks -----------------------------------------------------------
    @staticmethod
    def _auto_state_value(block: BlockNode, default: str) -> str | dict[str, Any]:
        """Normalize ``auto_state`` which CraftEngine accepts as a string or map."""
        if isinstance(block.auto_state, dict):
            return block.auto_state
        if isinstance(block.auto_state, str) and block.auto_state:
            return block.auto_state
        return default

    def _generate_block(self, out: GenerationResult, block: BlockNode) -> None:
        decision = self.mapping.get(block.id)
        result = decision.result if decision else status.DIRECT
        ns, path = util.split_id(block.id)

        body = _ordered()

        if block.states:
            body["states"] = self._block_states(block)
        else:
            state = _ordered()
            state["auto_state"] = self._auto_state_value(block, "solid")
            state["model"] = self._block_model(block)
            if block.transparent:
                state["transparent"] = True
            body["state"] = state

        if block.behavior_configs:
            if len(block.behavior_configs) == 1:
                body["behavior"] = block.behavior_configs[0]
            else:
                body["behaviors"] = list(block.behavior_configs)
        elif block.behaviors:
            if len(block.behaviors) == 1:
                body["behavior"] = {"type": block.behaviors[0]}
            else:
                body["behaviors"] = [{"type": b} for b in block.behaviors]

        settings = _ordered()
        generated_sounds = self._block_sound_settings(block)
        if generated_sounds:
            settings["sounds"] = generated_sounds
        if block.item_binding:
            settings["item"] = block.item_binding
        if block.tool_tier:
            settings["require_correct_tools"] = True
            settings["correct_tools"] = [_correct_tools(block.tool_tier)]
        settings.update(_ordered_from(block.settings) if block.settings else _ordered())
        if settings:
            body["settings"] = settings

        if block.loot:
            body["loot"] = block.loot

        entry = {block.id: dict(body)}
        doc = {"blocks": entry}
        content = dump_yaml(doc)

        out.add_file(
            GeneratedFile(
                rel_path=f"configuration/blocks/{ns}/{util.safe_name(path)}.yml",
                object_id=block.id,
                domain=status.DOMAIN_BLOCK,
                content=content,
                result=result,
            )
        )

    def _generate_furniture(self, out: GenerationResult, furniture: FurnitureNode) -> None:
        """Emit a CraftEngine furniture definition.

        Only ``variants`` is mandatory in CraftEngine; ``settings`` carries the
        placement item and sounds. Light emission is a separate behavior.
        """
        decision = self.mapping.get(furniture.id)
        result = decision.result if decision else status.DIRECT
        ns, path = util.split_id(furniture.id)

        body = _ordered()
        settings = _ordered_from(furniture.settings) if furniture.settings else _ordered()
        if furniture.light_level:
            body.setdefault("behaviors", []).append(
                {"type": "glowing_furniture", "light_level": int(furniture.light_level)}
            )
        if settings:
            body["settings"] = settings
        body["variants"] = _ordered_from(furniture.variants)
        if furniture.loot_item:
            body["loot"] = {
                "template": "default:loot_table/furniture",
                "arguments": {"item": furniture.loot_item},
            }

        out.add_file(
            GeneratedFile(
                rel_path=f"configuration/furniture/{ns}/{util.safe_name(path)}.yml",
                object_id=furniture.id,
                domain=status.DOMAIN_FURNITURE,
                content=dump_yaml({"furniture": {furniture.id: dict(body)}}),
                result=result,
            )
        )

    def _item_lang_key(self, item: ItemNode) -> str | None:
        ns, path = util.split_id(item.id)
        candidates = [f"item.{ns}.{path}", f"block.{ns}.{path}"]
        for key in candidates:
            if key in self.analysis.lang:
                return key
        return None

    def _semantic_carrier_material(self, item: ItemNode) -> str | None:
        if item.food is None:
            return None
        path = item.id.split(":", 1)[-1].lower().replace("-", "_")
        # Soup wins over generic drink/broth wording so "mushroom_soup"
        # remains a soup carrier rather than a bottle.
        if any(kw.lower() in path for kw in self.settings.soup_keywords):
            return self.settings.soup_material
        if any(kw.lower() in path for kw in self.settings.drink_keywords):
            return self.settings.drink_material
        # A few common semantic names are better represented as soups even
        # when the registry id does not literally contain "soup".
        if any(token in path for token in ("stew", "chowder", "hotpot", "bisque")):
            return self.settings.soup_material
        # Fall back to the neural food family for names no keyword covers
        # ("ramen", "gumbo", "kombucha", …). Only the two carrier families that
        # actually need a semantic material are honored here.
        family = self._food_family(item)
        if family == "soup":
            return self.settings.soup_material
        if family == "drink":
            return self.settings.drink_material
        return None

    def _is_food(self, item: ItemNode) -> bool:
        """Decide whether an item should receive a food component.

        Order of evidence: an explicit source food component (handled by the
        caller), then the configurable keyword list, then the neural food head.
        The network catches edible items whose names no keyword list covers
        ("teriyaki", "gyoza", "kombucha", …).
        """
        if not self.settings.food_enabled:
            return False
        path = item.id.split(":")[-1].lower()
        if any(kw in path for kw in self.settings.food_keywords):
            return True
        if not getattr(self.settings, "brain_food_detection", True):
            return False
        candidate = (item.metadata or {}).get("food_candidate")
        return bool(candidate and candidate != "none")

    def _food_family(self, item: ItemNode) -> str | None:
        """Neural food family (food / drink / soup / sweet), if any."""
        candidate = (item.metadata or {}).get("food_candidate")
        return candidate if isinstance(candidate, str) and candidate != "none" else None

    def _item_category(self, item: ItemNode) -> str:
        # Sort ordinary items into crops / food / items folders.
        if item.metadata.get("display_item") or item.metadata.get("stage") is not None:
            return "crops"
        is_block_item = bool(item.behavior and item.behavior.get("type") == "block_item")
        if (item.food is not None or self._is_food(item)) and not is_block_item:
            return "food"
        return "items"

    def _block_sound_settings(self, block: BlockNode) -> dict[str, Any]:
        """Use custom sound events from the source sounds.json when available.
        The resourcepack contains the audio files, while CraftEngine's sound
        configuration registers the event and block settings reference it.
        """
        events = getattr(self.analysis, "sounds", {}) or {}
        if not isinstance(events, dict):
            return {}
        path = block.id.split(":", 1)[-1]
        result: dict[str, Any] = {}
        for kind in ("break", "step", "place", "hit", "fall"):
            local_id = f"block.{path}.{kind}"
            event_id = f"{block.namespace}:{local_id}"
            if local_id in events or event_id in events:
                result[kind] = event_id
        return result

    def _block_model(self, block: BlockNode) -> dict[str, Any]:
        # Prefer the (first variant) model when present — for crops the base
        # `<id>.json` model does not exist, only stage models do.
        if block.model:
            return {"path": self._model_path(block.model)}
        if block.block_model_path:
            return {"path": self._model_path(block.block_model_path)}
        if block.textures:
            return {"texture": block.textures[0]}
        ns, path = util.split_id(block.id)
        return {"texture": f"{ns}:block/{path}"}

    @staticmethod
    def _model_path(ref: str) -> str:
        # CraftEngine model path uses namespace:block/<name>; a raw
        # "ns:block/name" ref is already correct.
        return ref

    def _block_states(self, block: BlockNode) -> dict[str, Any]:
        states = _ordered()
        properties = _ordered()
        for state in block.states:
            properties[state.property_name] = _property_def(state)

        is_crop = bool(block.metadata.get("is_crop"))
        stage_models = block.metadata.get("stage_models", {})
        if is_crop and stage_models:
            appearances, variants = self._crop_appearances(block, stage_models)
        elif block.metadata.get("entity_render_variants"):
            appearances, variants = self._entity_variant_appearances(block)
        else:
            appearances = _ordered()
            default_appearance = _ordered()
            default_appearance["auto_state"] = self._auto_state_value(block, "solid")
            default_appearance["model"] = self._block_model(block)
            if block.transparent:
                default_appearance["transparent"] = True
            appearances["default"] = default_appearance

            variants = _ordered()
            if block.blockstate_variants:
                for variant in block.blockstate_variants:
                    condition = variant.get("condition")
                    # Empty condition is the default variant.
                    variants[condition or "__default__"] = {"appearance": "default"}
            elif block.blockstate_multipart:
                variants["__default__"] = {"appearance": "default"}
            else:
                key = ",".join(f"{s.property_name}={s.default_value or s.allowed_values[0]}" for s in block.states if s.allowed_values)
                if key:
                    variants[key] = {"appearance": "default"}

        if properties:
            states["properties"] = properties
        states["appearances"] = appearances
        states["variants"] = variants
        return states

    def _entity_variant_appearances(self, block: BlockNode) -> tuple[dict[str, Any], dict[str, Any]]:
        appearances = _ordered()
        variants = _ordered()
        by_key = block.metadata.get("entity_render_variants", {})
        for idx, (condition, spec) in enumerate(by_key.items()):
            name = f"variant_{idx}"
            ap = _ordered()
            base = {"type": "higher_tripwire"} if not isinstance(block.auto_state, dict) else dict(block.auto_state)
            base.setdefault("type", "higher_tripwire")
            base["id"] = base.get("id", f"{block.id.split(':')[-1]}_transparent")
            ap["auto_state"] = base
            ap["transparent"] = True
            renderer = _ordered()
            renderer["type"] = "item_display"
            renderer["item"] = spec["item"]
            if spec.get("rotation") is not None:
                renderer["rotation"] = spec["rotation"]
            ap["entity_renderer"] = renderer
            appearances[name] = ap
            variants[condition or "__default__"] = {"appearance": name}
        return appearances, variants

    def _crop_appearances(self, block: BlockNode, stage_models: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build per-stage appearances where each stage is an item_display
        entity rendered on a shared higher_tripwire carrier."""
        base_name = block.id.split(":")[-1]
        display_base = base_name[:-5] if base_name.endswith("_crop") else base_name
        appearances = _ordered()
        variants = _ordered()

        for age in sorted(stage_models):
            appearance_name = f"age_{age}"
            appearance = _ordered()
            base = {"type": "higher_tripwire"} if not isinstance(block.auto_state, dict) else dict(block.auto_state)
            base.setdefault("type", "higher_tripwire")
            base["id"] = base.get("id", f"{base_name}_transparent")
            appearance["auto_state"] = base
            appearance["transparent"] = True
            appearance["entity_renderer"] = {
                "type": "item_display",
                "item": f"{block.namespace}:{display_base}_stage{age}",
                "scale": "1,1,1",
                "billboard": "fixed",
            }
            appearances[appearance_name] = appearance
            variants[f"age={age}"] = {"appearance": appearance_name}

        return appearances, variants


def _property_def(state: BlockStateNode) -> dict[str, Any]:
    ptype = state.property_type
    entry = _ordered()
    entry["type"] = ptype
    if ptype == "int":
        vals = sorted(int(v) for v in state.allowed_values)
        default = int(state.default_value) if state.default_value is not None else vals[0]
        entry["default"] = default
        if len(vals) > 1:
            entry["range"] = f"{vals[0]}~{vals[-1]}"
        else:
            entry["range"] = f"{vals[0]}~{vals[0]}"
    elif ptype == "boolean":
        entry["default"] = (state.default_value or "false").lower() in ("true", "1")
    elif ptype == "string":
        # CraftEngine's StringProperty requires an explicit values list.
        values = list(state.allowed_values) if state.allowed_values else [state.default_value or ""]
        entry["values"] = values
        entry["default"] = state.default_value or (values[0] if values else "")
    else:
        # Enum-backed CraftEngine property types (axis, direction,
        # horizontal_direction, hinge, stairs_shape, ...) accept an optional
        # values list that narrows the allowed state set to the source values.
        if state.allowed_values:
            # Direction & alignment types use enum name values already
            # compatible with CraftEngine; keeping the source list is safe.
            entry["values"] = list(state.allowed_values)
        default = state.default_value or (state.allowed_values[0] if state.allowed_values else "")
        if default:
            entry["default"] = default
    return entry


def _break_power(tier: str | None) -> int:
    tier_map: dict[str, int] = {
        "wood": 1, "gold": 1, "stone": 2, "copper": 2, "iron": 3, "diamond": 4, "netherite": 5,
    }
    return tier_map.get(tier or "", 0)


def _correct_tools(tier: str | None) -> str:
    tier = tier or "wood"
    if tier in ("wood", "gold"):
        return f"minecraft:wooden_pickaxe"
    return f"minecraft:{tier}_pickaxe"


# --- recipes ---------------------------------------------------------------
class RecipeGenerator:
    def __init__(
        self,
        analysis: AnalysisResult,
        mapping: capability.MappingResult,
        settings: Any = None,
    ) -> None:
        self.analysis = analysis
        self.mapping = mapping
        from .config import Settings

        self.settings = settings or Settings()

    def _excluded(self, ref: str) -> bool:
        raw = str(ref).lstrip("#")
        ns = raw.split(":", 1)[0] if ":" in raw else "minecraft"
        return ns in set(self.settings.excluded_recipe_namespaces)

    # Common NeoForge/Fabric c: item tags whose vanilla membership is well-defined.
    # These are used only as a compatibility bridge when the source mod references
    # a common tag but the standalone CraftEngine pack has no provider for it.
    VANILLA_COMMON_TAGS = {
        "c:foods/bread": ["minecraft:bread"],
        "c:eggs": ["minecraft:egg"],
        "c:drinks/milk": ["minecraft:milk_bucket"],
        "c:foods/milk": ["minecraft:milk_bucket"],
        "c:foods/cooked_mutton": ["minecraft:cooked_mutton"],
        "c:foods/cooked_chicken": ["minecraft:cooked_chicken"],
        "c:foods/cooked_beef": ["minecraft:cooked_beef"],
        "c:foods/cooked_pork": ["minecraft:cooked_porkchop"],
        "c:foods/cooked_cod": ["minecraft:cooked_cod"],
        "c:foods/cooked_salmon": ["minecraft:cooked_salmon"],
        "c:crops/carrot": ["minecraft:carrot"],
        "c:crops/potato": ["minecraft:potato"],
        "c:crops/beetroot": ["minecraft:beetroot"],
        "c:foods/raw_beef": ["minecraft:beef"],
        "c:foods/raw_chicken": ["minecraft:chicken"],
        "c:crops/tomato": [],
        "c:crops/onion": [],
        "c:crops/cabbage": [],
        "c:crops/rice": ["minecraft:rice"],
        "c:crops/grain": ["minecraft:wheat"],
        "c:foods/pasta": [],
        "c:foods/cooked_egg": ["minecraft:egg"],
        "c:drinks/milk": ["minecraft:milk_bucket"],
        "c:foods/leafy_green": ["minecraft:kelp"],
        "c:foods/safe_raw_fish": ["minecraft:cod", "minecraft:salmon"],
        "c:foods/raw_pork": ["minecraft:porkchop"],
        "c:foods/raw_mutton": ["minecraft:mutton"],
        "c:foods/vegetable": ["minecraft:carrot", "minecraft:potato", "minecraft:beetroot"],
        "c:foods/fruits": ["minecraft:apple", "minecraft:melon_slice", "minecraft:sweet_berries"],
        "c:fruits": ["minecraft:apple", "minecraft:melon_slice", "minecraft:sweet_berries"],
        "c:seeds": ["minecraft:wheat_seeds", "minecraft:beetroot_seeds", "minecraft:carrot", "minecraft:potato"],
    }

    TAG_ALIASES = {
        "c:foods/vegetables": "c:foods/vegetable",
        "c:foods/cooked_porkchop": "c:foods/cooked_pork",
        "c:foods/cooked_meats": "c:foods/cooked_meat",
    }

    OPTIONAL_DEPENDENCY_TAGS = {
        # Farmer's Delight common tags are widely used by its addons.
        "farmersdelight": {
            "c:foods/dough": ["farmersdelight:wheat_dough"],
            "c:crops/onion": ["farmersdelight:onion"],
            "c:crops/cabbage": ["farmersdelight:cabbage"],
            "c:foods/leafy_green": ["farmersdelight:cabbage"],
            "c:foods/cooked_egg": ["farmersdelight:fried_egg"],
            "c:foods/cooked_chicken": ["farmersdelight:cooked_chicken_cuts"],
            "c:foods/cooked_mutton": ["farmersdelight:cooked_mutton_chops"],
            "c:foods/cooked_cod": ["farmersdelight:cooked_cod_slice"],
            "c:foods/cooked_salmon": ["farmersdelight:cooked_salmon_slice"],
            "c:foods/cooked_pork": ["farmersdelight:bacon"],
            "c:crops/rice": ["farmersdelight:rice"],
            "c:crops/tomato": ["farmersdelight:tomato"],
            "c:crops/grain": ["farmersdelight:rice"],
            "c:foods/pasta": ["farmersdelight:raw_pasta"],
            "c:foods/cooked_bacon": ["farmersdelight:bacon"],
        },
    }

    def _tag_members(self, tag_ref: str) -> list[str]:
        """Expand a tag to concrete item ids while preserving source tags.

        We resolve source-provided item tags recursively. For common ecosystem
        tags such as ``c:foods/bread`` we also add known vanilla members. This
        avoids CraftEngine warnings when the original mod relied on NeoForge's
        shared tag provider, which is not present in a standalone conversion.
        """
        ref = str(tag_ref).lstrip("#")
        if not ref:
            return []
        result: list[str] = []
        visiting: set[str] = set()

        def walk(tag_id: str) -> None:
            tag_id = self.TAG_ALIASES.get(tag_id, tag_id)
            if tag_id in visiting:
                return
            visiting.add(tag_id)
            parts = tag_id.split(":", 1)
            if len(parts) != 2:
                return
            ns, rel = parts
            key = f"{ns}:items/{rel}"
            data = self.analysis.tags.get(key)
            values = data.get("values", []) if isinstance(data, dict) else []
            for value in values:
                if not isinstance(value, str):
                    continue
                value = value.strip()
                if not value:
                    continue
                if value.startswith("#"):
                    walk(value[1:])
                elif value not in result:
                    result.append(value)
            for value in self.VANILLA_COMMON_TAGS.get(tag_id, []):
                if value not in result:
                    result.append(value)

            # Only use dependency-specific aliases when the analyzed mod
            # actually declares that dependency. This prevents us from creating
            # hard runtime requirements that the source mod did not have.
            deps = set(getattr(self.analysis.metadata, "dependencies", []) or [])
            deps.update(getattr(self.analysis.metadata, "optional_dependencies", []) or [])
            for dep, aliases in self.OPTIONAL_DEPENDENCY_TAGS.items():
                if dep in deps:
                    for value in aliases.get(tag_id, []):
                        if value not in result:
                            result.append(value)

        walk(ref)
        return result

    def _expand_recipe_tags(self, recipe: RecipeNode) -> RecipeNode:
        import copy
        r = copy.deepcopy(recipe)
        for ing in r.ingredients:
            values = ing.get("items")
            vals = values if isinstance(values, list) else [values]
            expanded: list[str] = []
            had_tag = False
            for value in vals:
                if isinstance(value, str) and value.startswith("#"):
                    had_tag = True
                    members = self._tag_members(value)
                    for member in members:
                        if member not in expanded:
                            expanded.append(member)
                elif value is not None and value not in expanded:
                    expanded.append(value)
            if had_tag:
                if expanded:
                    ing["items"] = expanded if isinstance(values, list) or len(expanded) != 1 else expanded[0]
                else:
                    r.raw = dict(r.raw or {})
                    unresolved = list(r.raw.get("_converter_unresolved_tags", []))
                    unresolved.extend([str(v) for v in vals if isinstance(v, str) and v.startswith("#")])
                    r.raw["_converter_unresolved_tags"] = sorted(set(unresolved))
        return r

    def _filtered_recipe(self, recipe: RecipeNode) -> RecipeNode | None:
        """Remove excluded external namespaces from ingredients while preserving recipe shape.
        Returns None when no usable ingredient remains or when the result itself is excluded.
        """
        import copy
        r = copy.deepcopy(recipe)
        result_id = str((r.result or {}).get("id", ""))
        if result_id and self._excluded(result_id):
            return None

        filtered: list[dict[str, Any]] = []
        removed_symbols: set[str] = set()
        for ing in r.ingredients:
            symbol = ing.get("symbol")
            values = ing.get("items")
            vals = values if isinstance(values, list) else [values]
            kept = [v for v in vals if v is not None and not self._excluded(str(v))]
            if not kept:
                if symbol:
                    removed_symbols.add(str(symbol))
                continue
            ni = dict(ing)
            ni["items"] = kept if isinstance(values, list) else kept[0]
            filtered.append(ni)
        r.ingredients = filtered
        if not filtered:
            return None

        if r.pattern and removed_symbols:
            r.pattern = ["".join(" " if ch in removed_symbols else ch for ch in row) for row in r.pattern]
            while r.pattern and not r.pattern[0].strip():
                r.pattern.pop(0)
            while r.pattern and not r.pattern[-1].strip():
                r.pattern.pop()
            if r.pattern:
                left = min((i for row in r.pattern for i,ch in enumerate(row) if ch != " "), default=0)
                right = max((i for row in r.pattern for i,ch in enumerate(row) if ch != " "), default=-1)
                if right >= left:
                    r.pattern = [row[left:right+1] for row in r.pattern]
            if not any(row.strip() for row in r.pattern):
                return None
        return r

    def generate_recipe(self, out: GenerationResult, recipe: RecipeNode) -> None:
        decision = self.mapping.get(recipe.id)
        result = decision.result if decision else status.UNSUPPORTED

        if not recipe.result or not recipe.result.get("id"):
            out.diagnostics.append({"id": recipe.id, "status": status.UNSUPPORTED, "reason": "NO_RESULT", "raw": recipe.raw})
            return

        original_recipe_id = recipe.id
        recipe = self._filtered_recipe(recipe)
        if recipe is None:
            out.diagnostics.append({"id": original_recipe_id, "status": status.TRANSFORM, "reason": "REMOVED_BY_EXCLUDED_NAMESPACE"})
            return

        # Materialize source/common tags so the standalone CraftEngine pack does
        # not depend on a missing NeoForge/Fabric tag provider.
        recipe = self._expand_recipe_tags(recipe)
        recipe = self._filtered_recipe(recipe)
        if recipe is None:
            out.diagnostics.append({"id": original_recipe_id, "status": status.TRANSFORM, "reason": "REMOVED_AFTER_TAG_EXPANSION"})
            return

        unresolved_tags = recipe.raw.get("_converter_unresolved_tags", []) if isinstance(recipe.raw, dict) else []
        if unresolved_tags:
            policy = getattr(self.settings, "unresolved_recipe_tag_policy", "skip")
            if policy == "skip":
                out.diagnostics.append({"id": original_recipe_id, "status": status.TRANSFORM, "reason": "UNRESOLVED_RECIPE_TAGS", "tags": unresolved_tags})
                return

        short = recipe.recipe_type.split(":")[-1]
        # Preserve every native recipe class we can represent exactly. Only
        # custom station serializers are transformed, and that fact is kept in
        # the diagnostic rather than silently converting everything to crafting.
        type_map = {
            "crafting_shaped": "shaped",
            "crafting_shapeless": "shapeless",
            "smelting": "smelting",
            "blasting": "blasting",
            "smoking": "smoking",
            "campfire_cooking": "campfire_cooking",
            "stonecutting": "stonecutting",
            "smithing_transform": "smithing_transform",
            "smithing_trim": "smithing_trim",
            "brewing": "brewing",
        }
        target_type = type_map.get(short)
        transformed = False
        if target_type is None:
            station = recipe.station
            if station == "sliceboard":
                out.diagnostics.append({"id": recipe.id, "status": result, "reason": "HANDLED_BY_SLICEBOARD", "raw": recipe.raw})
                return
            if station == "crafting_table":
                target_type = "shapeless_transform" if not recipe.pattern else "shaped_transform"
                transformed = True
            else:
                out.diagnostics.append({"id": recipe.id, "status": status.UNSUPPORTED, "reason": f"UNREPRESENTABLE_SERIALIZER:{recipe.recipe_type}", "raw": recipe.raw})
                return

        ns, path = util.split_id(recipe.id)
        body = _ordered()
        body["type"] = target_type

        if target_type in ("shaped", "shaped_transform"):
            body["pattern"] = list(recipe.pattern)
            ingredients = _ordered()
            for ing in recipe.ingredients:
                symbol = ing.get("symbol")
                if not symbol:
                    continue
                value = ing.get("items")
                ingredients[symbol] = value[0] if isinstance(value, list) and len(value) == 1 else value
            body["ingredients"] = ingredients
        elif target_type in ("smelting", "blasting", "smoking", "campfire_cooking", "stonecutting"):
            value = self._first_ingredient(recipe.ingredients)
            if value is None:
                out.diagnostics.append({"id": recipe.id, "status": status.UNSUPPORTED, "reason": "NO_INGREDIENT", "raw": recipe.raw})
                return
            if target_type == "stonecutting":
                body["ingredient"] = value
            else:
                body["ingredient"] = value
                if recipe.experience is not None:
                    body["experience"] = recipe.experience
                if recipe.time is not None:
                    body["time"] = recipe.time
        elif target_type == "shapeless" or target_type == "shapeless_transform":
            ingredients = []
            for ing in recipe.ingredients:
                value = ing.get("items")
                if isinstance(value, list) and len(value) == 1:
                    value = value[0]
                if value is not None:
                    ingredients.append(value)
            if transformed:
                source = self._select_source_index(recipe)
                if source is not None and 0 <= source < len(ingredients):
                    if isinstance(ingredients[source], str):
                        ingredients[source] = {"items": ingredients[source], "source": True}
                    elif isinstance(ingredients[source], dict):
                        ingredients[source].setdefault("source", True)
            container = self._container_for(recipe)
            if transformed and container:
                ingredients.append(container)
            body["ingredients"] = ingredients
        elif target_type in ("smithing_transform", "smithing_trim"):
            for key, attr in (("template_type", "template"), ("base", "base"), ("addition", "addition")):
                value = recipe.raw.get(attr)
                if value is not None:
                    body[key] = value
            if target_type == "smithing_trim" and recipe.raw.get("pattern") is not None:
                body["pattern"] = recipe.raw["pattern"]
        elif target_type == "brewing":
            for key in ("ingredient", "container"):
                if key in recipe.raw:
                    body[key] = recipe.raw[key]

        body["result"] = {"id": recipe.result["id"], "count": recipe.count}
        # Preserve metadata that CraftEngine explicitly supports.
        for key, value in (("category", recipe.raw.get("category")), ("group", recipe.raw.get("group")),
                           ("conditions", recipe.conditions), ("unlock_on_join", recipe.unlock.get("unlock_on_join")),
                           ("unlock_on_ingredient_obtained", recipe.unlock.get("unlock_on_ingredient_obtained"))):
            if value not in (None, [], {}):
                body[key] = value
        if recipe.result.get("post_processors"):
            body["result"]["post_processors"] = recipe.result["post_processors"]

        content = dump_yaml({"recipes": {recipe.id: dict(body)}})
        folder = self._recipe_folder(target_type if target_type else recipe.recipe_type) if getattr(self.settings, "organize_recipes", True) else "misc"
        rel = f"configuration/recipes/{ns}/{folder}/{util.safe_name(path)}.yml"
        out.add_file(GeneratedFile(rel_path=rel, object_id=recipe.id, domain=status.DOMAIN_RECIPE, content=content, result=result))

    @staticmethod
    def _first_ingredient(ingredients: list[dict[str, Any]]) -> Any:
        for ing in ingredients:
            value = ing.get("items")
            if value is not None:
                return value[0] if isinstance(value, list) and len(value) == 1 else value
        return None

    @staticmethod
    def _recipe_folder(recipe_type: str) -> str:
        short = (recipe_type or "misc").split(":")[-1].lower()
        aliases = {
            "crafting_shaped": "crafting/shaped", "crafting_shapeless": "crafting/shapeless",
            "smelting": "cooking/smelting", "blasting": "cooking/blasting", "smoking": "cooking/smoking",
            "campfire_cooking": "cooking/campfire", "stonecutting": "stonecutting", "smithing_transform": "smithing",
            "smithing_trim": "smithing", "sliceboard": "cutting", "cutting": "cutting",
            "shaped": "crafting/shaped", "shapeless": "crafting/shapeless",
        }
        if short in aliases: return aliases[short]
        return util.safe_name(short or "misc")

    @staticmethod
    def _select_source_index(recipe: RecipeNode) -> int | None:
        for i, ing in enumerate(recipe.ingredients):
            if ing.get("source"):
                return i
        return 0 if recipe.ingredients else None

    def _container_for(self, recipe: RecipeNode) -> str | None:
        # Only add a container for remapped (non-vanilla) station types.
        short = recipe.recipe_type.split(":")[-1] if ":" in recipe.recipe_type else recipe.recipe_type
        if short in ("crafting_shapeless", "crafting_shaped"):
            return None
        return self.settings.container_for(recipe.recipe_type)


class LootGenerator:
    # CraftEngine's loot entry registry (LootEntryContainers.java) and loot
    # function registry (LootFunctions.java). Unknown vanilla datapack entries
    # are reported rather than emitted, because CraftEngine rejects them.
    CE_LOOT_ENTRY_ALIASES = {
        "experience": "exp",
        "furniture": "furniture_item",
    }
    CE_LOOT_ENTRY_TYPES = {
        "alternatives", "if_else", "item", "exp", "furniture_item",
        "empty", "function", "loot_table",
    }
    CE_LOOT_FUNCTION_ALIASES = {
        "looting_enchant": "apply_bonus",
    }
    CE_LOOT_FUNCTION_TYPES = {
        "apply_bonus", "apply_data", "set_count", "explosion_decay",
        "drop_exp", "limit_count",
    }

    def __init__(self, analysis: AnalysisResult, mapping: capability.MappingResult) -> None:
        self.analysis = analysis
        self.mapping = mapping
        self.diagnostics: list[dict[str, Any]] = []

    @staticmethod
    def _strip_mc_type(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("minecraft:"):
            return value.split(":", 1)[1]
        return value

    @classmethod
    def _number_provider(cls, value: Any) -> Any:
        if isinstance(value, dict):
            out = {k: cls._normalize_value(v, key=k) for k, v in value.items()}
            if "type" in out:
                out["type"] = cls._strip_mc_type(out["type"])
            return out
        return value

    @classmethod
    def _condition(cls, cond: Any) -> Any:
        if not isinstance(cond, dict):
            return cond
        out: dict[str, Any] = {}
        ctype = cond.get("type", cond.get("condition"))
        if ctype is not None:
            ctype = cls._strip_mc_type(ctype)
            if ctype == "block_state_property":
                ctype = "match_block_property"
            elif ctype == "random_chance":
                # CraftEngine's merged condition system uses `random` with a
                # numeric probability in `value`.
                ctype = "random"
        if ctype is not None:
            out["type"] = ctype
        for k, v in cond.items():
            if k in {"type", "condition"}:
                continue
            if k == "block" and ctype == "match_block_property":
                continue
            if k in {"term", "terms"}:
                if isinstance(v, list):
                    out[k] = [cls._condition(x) for x in v]
                elif isinstance(v, dict):
                    out[k] = cls._condition(v)
                else:
                    out[k] = v
                continue
            if k == "chance" and ctype == "random":
                out["value"] = v
                continue
            out[k] = cls._normalize_value(v, key=k)
        return out

    def _function(self, fn: Any) -> Any:
        if not isinstance(fn, dict):
            return None
        ftype = fn.get("type", fn.get("function"))
        if ftype is None:
            return None
        ctype = self._strip_mc_type(ftype)
        ctype = self.CE_LOOT_FUNCTION_ALIASES.get(ctype, ctype)
        if ctype not in self.CE_LOOT_FUNCTION_TYPES:
            self.diagnostics.append({
                "path": getattr(self, "_current_loot", "loot"),
                "domain": "loot",
                "kind": "function",
                "type": ctype,
                "status": status.UNSUPPORTED,
                "reason": "UNSUPPORTED_LOOT_FUNCTION",
            })
            return None
        out: dict[str, Any] = {}
        out["type"] = ctype
        for k, v in fn.items():
            if k in {"type", "function"}:
                continue
            if k == "formula" and isinstance(v, str):
                out[k] = {"type": self._strip_mc_type(v), **({} if not isinstance(fn.get("parameters"), dict) else fn["parameters"])}
                continue
            if k == "parameters" and isinstance(fn.get("formula"), str):
                continue
            out[k] = self._normalize_value(v, key=k)
        return out

    def _entry(self, entry: Any) -> Any:
        if not isinstance(entry, dict):
            return None
        etype = entry.get("type")
        if etype is None:
            etype = entry.get("entry")
        if etype is None:
            return None
        ctype = self._strip_mc_type(etype)
        ctype = self.CE_LOOT_ENTRY_ALIASES.get(ctype, ctype)
        if ctype not in self.CE_LOOT_ENTRY_TYPES:
            self.diagnostics.append({
                "path": getattr(self, "_current_loot", "loot"),
                "domain": "loot",
                "kind": "entry",
                "type": ctype,
                "status": status.UNSUPPORTED,
                "reason": "UNSUPPORTED_LOOT_ENTRY",
            })
            return None
        out: dict[str, Any] = {}
        out["type"] = ctype
        # Vanilla loot uses `name`; CE uses `item` for an item entry.
        if "item" in entry:
            out["item"] = self._normalize_value(entry["item"], key="item")
        elif ctype == "item" and "name" in entry:
            out["item"] = self._normalize_value(entry["name"], key="item")
        elif ctype == "loot_table" and "name" in entry:
            out["table"] = self._normalize_value(entry["name"], key="table")
        for key in ("children", "entries"):
            if key in entry and isinstance(entry[key], list):
                children = [self._entry(x) for x in entry[key]]
                out[key] = [x for x in children if x is not None]
        if "conditions" in entry:
            out["conditions"] = [self._condition(x) for x in entry.get("conditions", [])]
        if "condition" in entry and "conditions" not in out:
            out["conditions"] = [self._condition(entry["condition"])]
        if "functions" in entry:
            functions = [self._function(x) for x in entry.get("functions", [])]
            out["functions"] = [f for f in functions if f is not None]
        for key in ("weight", "quality"):
            if key in entry:
                out[key] = self._normalize_value(entry[key], key=key)
        return out

    def _normalize_pool(self, pool: Any) -> Any:
        if not isinstance(pool, dict):
            return None
        out: dict[str, Any] = {}
        entries: list[dict[str, Any]] = []
        for k, v in pool.items():
            if k == "rolls":
                out[k] = self._number_provider(v)
            elif k == "bonus_rolls":
                out[k] = self._number_provider(v)
            elif k == "entries" and isinstance(v, list):
                entries = [self._entry(x) for x in v]
                entries = [e for e in entries if e is not None]
                if entries:
                    out[k] = entries
            elif k == "conditions" and isinstance(v, list):
                out[k] = [self._condition(x) for x in v]
            elif k == "functions" and isinstance(v, list):
                funcs = [self._function(x) for x in v]
                out[k] = [f for f in funcs if f is not None]
            else:
                out[k] = self._normalize_value(v, key=k)
        return out if entries else None

    @classmethod
    def _normalize_value(cls, value: Any, key: str = "") -> Any:
        if isinstance(value, list):
            return [cls._normalize_value(v, key=key) for v in value]
        if isinstance(value, dict):
            return {k: cls._normalize_value(v, key=k) for k, v in value.items()}
        if key in {"type", "condition", "function", "formula"}:
            return cls._strip_mc_type(value)
        return value

    def normalize_table(self, raw: dict[str, Any]) -> dict[str, Any]:
        # Accept both vanilla loot-table JSON and already CE-like dictionaries.
        pools = raw.get("pools", [])
        out: dict[str, Any] = {}
        if isinstance(pools, list):
            normalized_pools = [self._normalize_pool(p) for p in pools]
            pools_out = [p for p in normalized_pools if p is not None]
            if pools_out:
                out["pools"] = pools_out
        if "type" in raw:
            # Loot table type is informational for CE; strip vanilla namespace.
            out["type"] = self._strip_mc_type(raw["type"])
        # Global functions are supported by CE; normalize them as well.
        if isinstance(raw.get("functions"), list):
            funcs = [self._function(x) for x in raw["functions"]]
            out["functions"] = [f for f in funcs if f is not None]
        return out

    def generate_loot(self, out: GenerationResult, loot: LootNode) -> None:
        decision = self.mapping.get(loot.id)
        result = decision.result if decision else status.DIRECT
        ns, path = util.split_id(loot.id)
        self._current_loot = loot.id
        self.diagnostics = []
        normalized = self.normalize_table(loot.raw if isinstance(loot.raw, dict) else {})
        doc = {"loot": {loot.id: normalized}}
        content = dump_yaml(doc)
        out.add_file(GeneratedFile(
            rel_path=f"configuration/loot/{ns}/{util.safe_name(path)}.yml",
            object_id=loot.id,
            domain=status.DOMAIN_LOOT,
            content=content,
            result=result,
        ))
        for diag in self.diagnostics:
            diag.setdefault("id", loot.id)
            out.diagnostics.append(diag)


def _ordered() -> dict[str, Any]:
    # Python 3.7+ dicts preserve insertion order; yaml.safe_dump respects it.
    return {}


def _ordered_from(mapping: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in mapping.items()}


def generate_all(
    analysis: AnalysisResult,
    mapping: capability.MappingResult,
    generator: Generator,
    recipe_gen: RecipeGenerator,
    loot_gen: LootGenerator,
) -> GenerationResult:
    out = generator.generate()

    for recipe_id in sorted(analysis.recipes):
        recipe_gen.generate_recipe(out, analysis.recipes[recipe_id])
    for loot_id in sorted(analysis.loot):
        loot_gen.generate_loot(out, analysis.loot[loot_id])

    # Categories are finalized by the driver after SliceBoard generation so
    # they truly are the final semantic-generation phase.

    mode = getattr(generator.settings, "recipe_sort_mode", "type_then_id")
    domain_order = {status.DOMAIN_ITEM: 10, status.DOMAIN_BLOCK: 20, status.DOMAIN_FURNITURE: 25, "sound": 30, status.DOMAIN_RECIPE: 40, status.DOMAIN_LOOT: 50, status.DOMAIN_SLICEBOARD: 60, "category": 90, "lang": 95}
    def final_sort_key(f: GeneratedFile):
        if f.domain == status.DOMAIN_RECIPE:
            rel = f.rel_path.lower().split("/")
            if mode == "id":
                return (40, "", f.object_id.lower())
            return (40, "/".join(rel[:-1]), f.object_id.lower())
        return (domain_order.get(f.domain, 99), f.rel_path.lower(), f.object_id.lower())
    out.files.sort(key=final_sort_key)
    return out
