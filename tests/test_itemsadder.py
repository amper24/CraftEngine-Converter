"""End-to-end tests for the ItemsAdder -> CraftEngine conversion mode.

Uses the synthetic pack built by ``tests/make_itemsadder_fixture.py``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from converter import driver, itemsadder, validator  # noqa: E402
from converter.archive import open_archive  # noqa: E402
from converter.config import Settings  # noqa: E402
from converter.util import Log  # noqa: E402

from make_itemsadder_fixture import build as build_fixture  # noqa: E402

NS = "rubbishpack"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _ledger_rows(out: Path) -> list[dict]:
    """Parse the `## Full ledger` table of reports/itemsadder.md.

    Reading the rendered report keeps these tests honest about the artifact a
    user actually receives, rather than about an in-memory structure.
    """
    text = (out / "reports" / "itemsadder.md").read_text(encoding="utf-8")
    section = text.split("## Full ledger", 1)[1]
    rows = []
    for line in section.splitlines():
        if not line.startswith("|") or line.startswith("| ---") or "Object" in line:
            continue
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        # Object | Source key | CraftEngine target | Support | Note
        if len(cells) != 5:
            continue
        rows.append({"object_id": cells[0], "source_key": cells[1],
                     "target": cells[2], "support": cells[3], "note": cells[4]})
    return rows


class ItemsAdderDetectionTests(unittest.TestCase):
    def setUp(self):
        self.pack = build_fixture()

    def test_pack_is_detected(self):
        with open_archive(self.pack, Log()) as archive:
            layout = itemsadder.detect_itemsadder(archive, Log())
        self.assertIsNotNone(layout)
        self.assertEqual(layout.contents_root, "contents")
        self.assertEqual(sorted(layout.namespaces), ["decoration", NS])

    def test_both_resource_layouts_are_found(self):
        with open_archive(self.pack, Log()) as archive:
            layout = itemsadder.detect_itemsadder(archive, Log())
        # `sounds` joined the legacy asset dirs when the fixture grew a
        # contents/<ns>/sounds/ tree for the sound registry.
        self.assertEqual(sorted(layout.namespaces[NS].legacy_asset_dirs),
                         ["models", "sounds", "textures"])
        self.assertEqual(
            layout.namespaces["decoration"].resourcepack_roots,
            ["contents/decoration/resources/resourcepack"],
        )

    def test_mod_jar_is_not_mistaken_for_itemsadder(self):
        """A real mod jar must keep using the bytecode adapter."""
        jar = ROOT / "tests" / "farmersdelight_fixture.jar"
        if not jar.exists():
            self.skipTest("mod fixture not built")
        with open_archive(jar, Log()) as archive:
            self.assertIsNone(itemsadder.detect_itemsadder(archive, Log()))
            self.assertEqual(driver.detect_source_kind(archive, Log(), "auto"), "mod")

    def test_source_mode_can_force_mod(self):
        with open_archive(self.pack, Log()) as archive:
            self.assertEqual(driver.detect_source_kind(archive, Log(), "mod"), "mod")


class ItemsAdderConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = build_fixture()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "converted"
        settings = Settings()
        settings.write_conversion_log = False
        cls.result = driver.convert(cls.pack, cls.out, settings=settings, source="itemsadder")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _item(self, item_id: str) -> dict:
        path = item_id.split(":", 1)[1]
        matches = list(self.out.glob(f"configuration/*/{NS}/{path}.yml"))
        self.assertTrue(matches, f"no generated file for {item_id}")
        return _load(matches[0])["items"][item_id]

    # --- overall ------------------------------------------------------------
    def test_output_validates(self):
        self.assertTrue(self.result["validation"]["valid"], self.result["validation"])
        self.assertEqual(self.result["fidelity"]["issues"], [])
        self.assertEqual(self.result["diagnostics"], 0)

    def test_source_kind_is_recorded(self):
        self.assertEqual(self.result["counts"]["items"], 11)
        self.assertEqual(self.result["counts"]["blocks"], 1)
        self.assertEqual(self.result["counts"]["furniture"], 2)
        self.assertEqual(self.result["counts"]["recipes"], 1)
        self.assertEqual(self.result["counts"]["loot"], 1)

    # --- items ---------------------------------------------------------------
    def test_template_is_not_generated_but_variant_is(self):
        self.assertFalse(list(self.out.glob(f"configuration/*/{NS}/gem_template.yml")))
        body = self._item(f"{NS}:ruby_pickaxe")
        # Inherited from the template...
        self.assertEqual(body["material"], "minecraft:diamond")
        # ...but overridden by the variant itself.
        self.assertEqual(body["model"]["path"], f"{NS}:item/ruby_pickaxe")

    def test_sword_attributes_keep_source_values(self):
        """Source amounts must win over gear heuristics and the brain."""
        body = self._item(f"{NS}:ruby_sword")
        mods = {m["type"]: m for m in body["data"]["attribute_modifiers"]}
        self.assertEqual(mods["attack_damage"]["amount"], 7.0)
        self.assertEqual(mods["attack_speed"]["amount"], -2.4)
        self.assertEqual(mods["attack_damage"]["operation"], "add_value")
        self.assertEqual(mods["attack_damage"]["slot"], "mainhand")
        # Exactly one modifier per attribute: no inferred duplicate.
        self.assertEqual(len(body["data"]["attribute_modifiers"]), 2)

    def test_sword_material_and_handheld_model(self):
        body = self._item(f"{NS}:ruby_sword")
        self.assertEqual(body["material"], "minecraft:diamond_sword")
        self.assertEqual(body["model"]["generation"]["parent"], "minecraft:item/handheld")

    def test_display_name_resolved_from_lang(self):
        """`display-name-ruby_sword` is a translation key, not literal text."""
        body = self._item(f"{NS}:ruby_sword")
        self.assertEqual(body["data"]["item_name"], "<!i>Ruby Sword")

    def test_item_flags_become_hide_tooltip(self):
        body = self._item(f"{NS}:ruby_sword")
        self.assertEqual(
            sorted(body["data"]["hide_tooltip"]),
            ["attribute_modifiers", "enchantments"],
        )

    def test_components_and_settings(self):
        body = self._item(f"{NS}:ruby_sword")
        self.assertEqual(body["data"]["max_damage"], 1561)
        self.assertEqual(body["data"]["components"]["max_stack_size"], 1)
        self.assertFalse(body["data"]["components"]["enchantment_glint_override"])
        self.assertEqual(body["settings"]["fuel_time"], 200)
        self.assertEqual(body["data"]["enchantments"], {"minecraft:damage_all": 2})

    def test_consumable_food(self):
        body = self._item(f"{NS}:ruby_apple")
        self.assertEqual(body["data"]["food"]["nutrition"], 7)
        self.assertEqual(body["data"]["food"]["saturation"], 4.5)
        self.assertEqual(body["data"]["consumable"]["consume_seconds"], 1.2)

    def test_legacy_event_food_is_converted(self):
        """events.drink.feed -> data.food (older ItemsAdder packs)."""
        body = self._item(f"{NS}:ruby_juice")
        self.assertEqual(body["data"]["food"]["nutrition"], 2)
        self.assertEqual(body["data"]["food"]["saturation"], 1.0)

    def test_armor_slot_and_equipment_asset(self):
        body = self._item(f"{NS}:ruby_helmet")
        self.assertEqual(body["data"]["equippable"]["slot"], "head")
        self.assertEqual(body["data"]["equippable"]["asset_id"], f"{NS}:rubyarmor")
        self.assertEqual(body["data"]["max_damage"], 363)

    def test_disabled_item_is_not_generated(self):
        self.assertFalse(list(self.out.glob(f"configuration/*/{NS}/ruby_disabled.yml")))

    # --- blocks ---------------------------------------------------------------
    def test_block_carrier_and_settings(self):
        block = _load(self.out / "configuration" / "blocks" / NS / "ruby_block.yml")
        body = block["blocks"][f"{NS}:ruby_block"]
        self.assertEqual(body["state"]["auto_state"], "note_block")
        self.assertEqual(body["state"]["model"]["path"], f"{NS}:block/ruby_block")
        self.assertEqual(body["settings"]["hardness"], 3.5)
        self.assertEqual(body["settings"]["luminance"], 7)
        self.assertEqual(body["settings"]["item"], f"{NS}:ruby_block")

    def test_tool_whitelist_is_materialized(self):
        body = _load(self.out / "configuration" / "blocks" / NS / "ruby_block.yml")["blocks"][f"{NS}:ruby_block"]
        self.assertTrue(body["settings"]["require_correct_tools"])
        self.assertIn("minecraft:netherite_pickaxe", body["settings"]["correct_tools"])
        self.assertEqual(len(body["settings"]["correct_tools"]), 6)

    def test_no_explosion_wins_over_blast_resistance(self):
        """ItemsAdder documents no_explosion as ignoring blast_resistance."""
        body = _load(self.out / "configuration" / "blocks" / NS / "ruby_block.yml")["blocks"][f"{NS}:ruby_block"]
        self.assertEqual(body["settings"]["resistance"], 3600000.0)

    def test_block_sounds(self):
        body = _load(self.out / "configuration" / "blocks" / NS / "ruby_block.yml")["blocks"][f"{NS}:ruby_block"]
        self.assertEqual(body["settings"]["sounds"]["break"]["id"], "minecraft:block.metal.break")
        self.assertEqual(body["settings"]["sounds"]["break"]["pitch"], 0.9)
        self.assertEqual(body["settings"]["sounds"]["place"], "minecraft:block.amethyst_block.place")

    def test_block_item_binding(self):
        body = self._item(f"{NS}:ruby_block")
        self.assertEqual(body["behavior"], {"type": "block_item", "block": f"{NS}:ruby_block"})

    def test_drop_becomes_loot_table(self):
        loot = _load(self.out / "configuration" / "loot" / NS / "ruby_block_drop.yml")
        pool = loot["loot"][f"{NS}:ruby_block_drop"]["pools"][0]
        self.assertEqual(pool["entries"][0]["item"], f"{NS}:ruby")
        self.assertEqual(pool["entries"][0]["functions"][0]["count"], {"min": 2, "max": 4})
        # ItemsAdder chance 75/100 -> probability 0.75
        self.assertEqual(pool["conditions"][0]["value"], 0.75)

    # --- furniture --------------------------------------------------------------
    def test_furniture_variants_and_transform(self):
        doc = _load(self.out / "configuration" / "furniture" / NS / "ruby_lamp.yml")
        body = doc["furniture"][f"{NS}:ruby_lamp"]
        self.assertEqual(sorted(body["variants"]), ["ground", "wall"])
        element = body["variants"]["ground"]["elements"][0]
        self.assertEqual(element["type"], "item_display")
        self.assertEqual(element["translation"], "0.0,0.92,0.0")
        self.assertEqual(element["scale"], "0.45,0.45,0.45")
        self.assertEqual(element["rotation"], 180.0)
        hitbox = body["variants"]["ground"]["hitboxes"][0]
        self.assertEqual(hitbox["height"], 2.0)
        self.assertTrue(hitbox["blocks_building"])
        self.assertEqual(body["behaviors"], [{"type": "glowing_furniture", "light_level": 13}])

    def test_furniture_item_binding(self):
        body = self._item(f"{NS}:ruby_lamp")
        self.assertEqual(body["behavior"]["type"], "furniture_item")
        self.assertEqual(body["behavior"]["furniture"], f"{NS}:ruby_lamp")

    # --- categories ---------------------------------------------------------------
    def test_source_categories_win_over_inference(self):
        cats = _load(self.out / "configuration" / "categories" / f"{NS}.yml")["categories"]
        self.assertIn(f"{NS}:tools", cats)
        self.assertIn(f"{NS}:everything", cats)
        # The disabled ItemsAdder category must not appear.
        self.assertNotIn(f"{NS}:disabled_one", cats)

    def test_wildcard_membership_only_lists_generated_items(self):
        cats = _load(self.out / "configuration" / "categories" / f"{NS}.yml")["categories"]
        members = cats[f"{NS}:everything"]["list"]
        self.assertIn(f"{NS}:ruby", members)
        self.assertIn(f"{NS}:ruby_pickaxe", members)
        self.assertNotIn(f"{NS}:gem_template", members)

    # --- recipes ----------------------------------------------------------------------
    def test_datapack_recipe_is_converted(self):
        files = list(self.out.glob(f"configuration/recipes/{NS}/**/ruby_block.yml"))
        self.assertTrue(files, "datapack recipe was not generated")
        body = _load(files[0])["recipes"][f"{NS}:ruby_block"]
        self.assertEqual(body["type"], "shaped")
        self.assertEqual(body["pattern"], ["RR", "RR"])
        self.assertEqual(body["ingredients"], {"R": f"{NS}:ruby"})
        self.assertEqual(body["result"]["id"], f"{NS}:ruby_block")

    # --- resource pack -------------------------------------------------------------------
    def test_legacy_textures_are_relocated(self):
        self.assertTrue((self.out / "resourcepack" / "assets" / NS / "textures" / "item" / "ruby.png").exists())
        self.assertTrue((self.out / "resourcepack" / "assets" / NS / "models" / "block" / "ruby_block.json").exists())

    def test_modern_resourcepack_layout_is_preserved(self):
        self.assertTrue(
            (self.out / "resourcepack" / "assets" / "decoration" / "models" / "item" / "statue.json").exists()
        )

    def test_second_namespace_is_converted(self):
        body = _load(self.out / "configuration" / "items" / "decoration" / "statue.yml")["items"]["decoration:statue"]
        self.assertEqual(body["model"]["path"], "decoration:item/statue")

    # --- reporting --------------------------------------------------------------------------
    def test_ledger_reports_unsupported_keys(self):
        report = (self.out / "reports" / "itemsadder.md").read_text(encoding="utf-8")
        self.assertIn("break_tools_blacklist", report)
        self.assertIn("behaviours.liquid_analyzer", report)
        self.assertIn("gem_template", report)

    def test_no_unknown_keys_in_generated_yaml(self):
        """Every emitted file must parse and use only documented top-level keys."""
        allowed = {
            "items": {"material", "data", "model", "behavior", "behaviors", "settings", "events",
                      "custom_model_data", "item_model", "texture", "textures", "models",
                      "hand_animation_on_swap", "oversized_in_gui", "swap_animation_scale"},
            "blocks": {"state", "states", "settings", "behavior", "behaviors", "loot", "events"},
            "furniture": {"variants", "settings", "behaviors", "loot", "events"},
        }
        checked = 0
        for path in self.out.glob("configuration/**/*.yml"):
            doc = _load(path)
            if not isinstance(doc, dict):
                continue
            for root, body_map in doc.items():
                if root not in allowed or not isinstance(body_map, dict):
                    continue
                for object_id, body in body_map.items():
                    if not isinstance(body, dict):
                        continue
                    unknown = set(body) - allowed[root]
                    self.assertEqual(unknown, set(), f"{path}: unknown keys under {root}:{object_id}")
                    checked += 1
        self.assertGreater(checked, 8)


class ItemsAdderEventTests(unittest.TestCase):
    """ItemsAdder `events:`/`actions:` -> CraftEngine events DSL."""

    @classmethod
    def setUpClass(cls):
        cls.pack = build_fixture()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "converted"
        settings = Settings()
        settings.write_conversion_log = False
        cls.result = driver.convert(cls.pack, cls.out, settings=settings, source="itemsadder")
        cls.ledger = _ledger_rows(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        path = self.out / "configuration" / "items" / NS / "ruby_sword.yml"
        self.sword = _load(path)["items"][f"{NS}:ruby_sword"]
        self.events = {e["on"]: e for e in self.sword.get("events", [])}

    def rows(self, source_key: str, object_id: str | None = None) -> list[tuple]:
        return [r for r in self.ledger
                if r["source_key"] == source_key
                and (object_id is None or r["object_id"] == object_id)]

    def test_direct_trigger_is_converted(self):
        self.assertIn("attack", self.events)

    def test_trigger_is_renamed_to_the_craftengine_name(self):
        # IA `interact` has no CE counterpart; `right_click` is the same moment.
        self.assertIn("right_click", self.events)
        self.assertNotIn("interact", self.events)

    def test_action_fields_are_renamed(self):
        fn = [f for f in self.events["attack"]["functions"] if f["type"] == "play_sound"][0]
        self.assertEqual(fn["sound"], "entity.pig.ambient")
        self.assertEqual(fn["volume"], 1.5)

    def test_repeated_action_suffix_is_stripped(self):
        # IA repeats an action as `play_sound_2`; both must survive as functions.
        sounds = [f for f in self.events["attack"]["functions"] if f["type"] == "play_sound"]
        self.assertEqual(len(sounds), 2)
        self.assertEqual(sounds[1]["sound"], "entity.pig.hurt")
        self.assertEqual(sounds[1]["pitch"], 2)

    def test_target_is_translated_to_the_craftengine_selector(self):
        fn = [f for f in self.events["attack"]["functions"] if f["type"] == "message"][0]
        self.assertEqual(fn["message"], "&cHit!")
        self.assertEqual(fn["target"], "self")  # IA `player` -> CE `self`

    def test_potion_effect_is_a_vanilla_id(self):
        fn = [f for f in self.events["attack"]["functions"] if f["type"] == "potion_effect"][0]
        self.assertEqual(fn["potion_effect"], "minecraft:speed")
        self.assertEqual(fn["duration"], 60)
        self.assertEqual(fn["amplifier"], 1)

    def test_amount_actions_become_set_count(self):
        fn = [f for f in self.events["attack"]["functions"] if f["type"] == "set_count"][0]
        self.assertEqual(fn, {"type": "set_count", "count": 1, "add": True})

    def test_chance_becomes_a_random_condition(self):
        self.assertEqual(self.events["attack"]["conditions"], [{"type": "random", "value": 0.35}])

    def test_entry_without_a_condition_has_none(self):
        self.assertNotIn("conditions", self.events["right_click"])

    def test_drop_item_becomes_a_loot_table(self):
        fn = [f for f in self.events["right_click"]["functions"] if f["type"] == "drop_loot"][0]
        entry = fn["loot"]["pools"][0]["entries"][0]
        self.assertEqual(entry["name"], "DIAMOND")
        self.assertEqual(entry["functions"][0]["count"], 2)

    def test_events_without_a_craftengine_trigger_are_reported_not_emitted(self):
        self.assertNotIn("wear", self.events)
        rows = self.rows("events.wear", f"{NS}:ruby_sword")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["support"], "unsupported")

    def test_unsupported_action_is_reported_not_emitted(self):
        # `veinminer` is covered by the range_mining_item behaviour, not a function.
        self.assertNotIn("veinminer", [f.get("type") for f in self.events["attack"]["functions"]])
        self.assertEqual(len(self.rows("events.attack.actions.veinminer")), 1)

    def test_out_of_enum_gui_type_is_never_emitted(self):
        """An invalid `gui_type` would be a broken config, so it is reported only."""
        self.assertNotIn("open_window", [f["type"] for f in self.events["right_click"]["functions"]])
        rows = self.rows("events.interact.actions.open_inventory")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["support"], "unsupported")

    def test_inline_cooldown_is_reported(self):
        rows = self.rows("events.attack.cooldown")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["support"], "unsupported")

    def test_no_silent_drop_of_the_events_key(self):
        """`events` must be consumed or reported - never silently ignored."""
        self.assertEqual(self.rows("events", f"{NS}:ruby_sword"), [])
        self.assertTrue(self.events, "events should have been converted")


class ItemsAdderArchiveTests(unittest.TestCase):
    """The source may be a folder, a zip of that folder, or a zip with a wrapper dir."""

    @classmethod
    def setUpClass(cls):
        cls.folder = build_fixture()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_zip_of_the_pack_converts(self):
        import shutil
        zip_path = Path(shutil.make_archive(str(self.root / "pack"), "zip", self.folder))
        out = self.root / "from_zip"
        result = driver.convert(zip_path, out, settings=Settings(), source="itemsadder")
        self.assertTrue(result["validation"]["valid"], result["validation"])
        self.assertEqual(result["fidelity"]["issues"], [])
        self.assertEqual(result["counts"]["items"], 11)

    def test_zip_with_a_wrapper_directory_is_still_detected(self):
        import shutil
        staged = self.root / "MyAwesomePack"
        if staged.exists():
            shutil.rmtree(staged)
        shutil.copytree(self.folder, staged)
        zip_path = Path(shutil.make_archive(str(self.root / "nested"), "zip", self.root, "MyAwesomePack"))
        with open_archive(zip_path, Log()) as archive:
            layout = itemsadder.detect_itemsadder(archive, Log())
        self.assertIsNotNone(layout)
        # Detection survives the extra wrapper directory in the archive.
        self.assertTrue(layout.evidence, layout)
        self.assertEqual(layout.contents_root, "MyAwesomePack/contents")
        self.assertEqual(sorted(layout.namespaces), ["decoration", NS])
        with open_archive(zip_path, Log()) as archive:
            self.assertEqual(driver.detect_source_kind(archive, Log(), "auto"), "itemsadder")

    def test_zip_output_matches_folder_output(self):
        import filecmp
        import shutil
        zip_path = Path(shutil.make_archive(str(self.root / "pack2"), "zip", self.folder))
        out_zip = self.root / "out_zip"
        out_dir = self.root / "out_dir"
        driver.convert(zip_path, out_zip, settings=Settings(), source="itemsadder")
        driver.convert(self.folder, out_dir, settings=Settings(), source="itemsadder")
        # Only the run-scoped artifacts may differ; every generated config must match.
        diffs = filecmp.dircmp(out_zip, out_dir, ignore=["reports", "manifest.yml", "pack.yml"])
        self.assertEqual(diffs.diff_files, [])
        self.assertEqual(diffs.left_only, [])
        self.assertEqual(diffs.right_only, [])


class ItemsAdderEquipmentAssetTests(unittest.TestCase):
    """`data.equippable.asset_id` must not be a dangling reference."""

    @classmethod
    def setUpClass(cls):
        cls.pack = build_fixture()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "converted"
        settings = Settings()
        settings.write_conversion_log = False
        cls.result = driver.convert(cls.pack, cls.out, settings=settings, source="itemsadder")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_equipment_asset_is_generated(self):
        path = self.out / "resourcepack" / "assets" / NS / "equipment" / "rubyarmor.json"
        self.assertTrue(path.exists(), "armors_rendering must produce an equipment asset")
        import json
        asset = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(asset["layers"]["humanoid"][0]["texture"], f"{NS}:armor/rubyarmor/layer_1")
        self.assertEqual(asset["layers"]["humanoid_leggings"][0]["texture"],
                         f"{NS}:armor/rubyarmor/layer_2")

    def test_layer_textures_are_moved_to_the_vanilla_paths(self):
        """An equipment texture id resolves under textures/entity/equipment/<layer_type>/."""
        for layer_type, name in (("humanoid", "layer_1"), ("humanoid_leggings", "layer_2")):
            path = (self.out / "resourcepack" / "assets" / NS / "textures" / "entity" /
                    "equipment" / layer_type / "armor" / "rubyarmor" / f"{name}.png")
            self.assertTrue(path.exists(), path)

    def test_asset_id_reference_resolves(self):
        """The id in the item config must point at a file that exists."""
        cfg = _load(self.out / "configuration" / "items" / NS / "ruby_helmet.yml")
        asset_id = cfg["items"][f"{NS}:ruby_helmet"]["data"]["equippable"]["asset_id"]
        ns, name = asset_id.split(":", 1)
        self.assertTrue((self.out / "resourcepack" / "assets" / ns / "equipment" / f"{name}.json").exists(),
                        f"{asset_id} is dangling")

    def test_missing_layer_textures_are_reported_not_silently_ignored(self):
        """A pack without the layer PNGs gets a ledger row, not a broken asset."""
        import shutil
        stripped = self.tmp.name + "/stripped"
        if Path(stripped).exists():
            shutil.rmtree(stripped)
        shutil.copytree(self.pack, stripped)
        for name in ("layer_1.png", "layer_2.png"):
            (Path(stripped) / "contents" / NS / "textures" / "armor" / "rubyarmor" / name).unlink()
        out = Path(self.tmp.name) / "out_stripped"
        driver.convert(Path(stripped), out, settings=Settings(), source="itemsadder")
        self.assertFalse((out / "resourcepack" / "assets" / NS / "equipment" / "rubyarmor.json").exists())
        report = (out / "reports" / "itemsadder.md").read_text(encoding="utf-8")
        self.assertIn("none resolved", report)


class ItemsAdderFurnitureSitTests(unittest.TestCase):
    """`behaviours.furniture_sit` -> CraftEngine hitbox `seats` (seatable furniture)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "converted"
        settings = Settings()
        settings.write_conversion_log = False
        driver.convert(build_fixture(), cls.out, settings=settings, source="itemsadder")
        cls.furn = _load(cls.out / "configuration" / "furniture" / NS / "ruby_cushion.yml")[
            "furniture"][f"{NS}:ruby_cushion"]
        cls.report = (cls.out / "reports" / "itemsadder.md").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_seat_is_emitted_on_the_hitbox(self):
        hitbox = self.furn["variants"]["ground"]["hitboxes"][0]
        self.assertEqual(hitbox["seats"], ["0,0.5,0"])

    def test_seat_height_comes_from_the_source(self):
        # sit_height: 0.5 in the fixture must be the y of the seat position.
        self.assertIn("0,0.5,0", self.furn["variants"]["ground"]["hitboxes"][0]["seats"][0])

    def test_hitbox_stays_interactive(self):
        hitbox = self.furn["variants"]["ground"]["hitboxes"][0]
        self.assertTrue(hitbox["interactive"])

    def test_sit_all_solid_blocks_is_reported_as_having_no_equivalent(self):
        self.assertIn("sit_all_solid_blocks", self.report)
        self.assertIn("no CraftEngine", self.report)

    def test_furniture_is_still_bound_to_its_item(self):
        item = _load(self.out / "configuration" / "items" / NS / "ruby_cushion.yml")["items"][
            f"{NS}:ruby_cushion"]
        self.assertEqual(item["behavior"]["type"], "furniture_item")
        self.assertEqual(item["behavior"]["furniture"], f"{NS}:ruby_cushion")


class ItemsAdderCustomVariantsTests(unittest.TestCase):
    """`placed_model.custom_variants` is a random model pool with no CE binding."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "converted"
        settings = Settings()
        settings.write_conversion_log = False
        driver.convert(build_fixture(), cls.out, settings=settings, source="itemsadder")
        cls.report = (cls.out / "reports" / "itemsadder.md").read_text(encoding="utf-8")
        cls.block = _load(cls.out / "configuration" / "blocks" / NS / "ruby_block.yml")["blocks"][f"{NS}:ruby_block"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_reported_as_unsupported_with_the_variant_count(self):
        self.assertIn("placed_model.custom_variants` | unsupported | 2 random model variant(s)",
                      self.report)

    def test_report_names_the_source_models(self):
        self.assertIn("minecraft:block/end_stone_bricks", self.report)
        self.assertIn("minecraft:block/diamond_block", self.report)

    def test_no_states_are_invented(self):
        """CraftEngine states need a property; guessing one would be worse than reporting."""
        self.assertNotIn("states", self.block)
        self.assertNotIn("custom_variants", str(self.block))

    def test_block_is_still_generated(self):
        self.assertIn("state", self.block)


class ItemsAdderBookTests(unittest.TestCase):
    """`behaviours.book` -> `data.written_book_content`."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "converted"
        settings = Settings()
        settings.write_conversion_log = False
        driver.convert(build_fixture(), cls.out, settings=settings, source="itemsadder")
        cls.tome = _load(cls.out / "configuration" / "items" / NS / "ruby_tome.yml")["items"][f"{NS}:ruby_tome"]
        cls.report = (cls.out / "reports" / "itemsadder.md").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_static_pages_are_converted(self):
        content = self.tome["data"]["components"]["written_book_content"]
        self.assertEqual(content["pages"], ["Page one, plain text.", "Page two, plain text."])
        self.assertEqual(content["title"], "Tome of Rubies")
        self.assertEqual(content["author"], "rubbishpack")

    def test_interactive_page_is_skipped_and_reported(self):
        """The ledger must not claim a page was skipped while still emitting it."""
        content = self.tome["data"]["components"]["written_book_content"]
        self.assertEqual(len(content["pages"]), 2)
        self.assertNotIn("<player>", " ".join(content["pages"]))
        self.assertIn("1 page(s) with placeholders or interactive content were skipped", self.report)

    def test_bow_behaviour_is_reported_as_unsupported_not_promised(self):
        """`allowed_projectiles` is not a CE 26.8 key, so it must not be claimed."""
        self.assertNotIn("allowed_projectiles", self.report)
        self.assertIn("no projectile whitelist", self.report)


class ItemsAdderRootKeyTests(unittest.TestCase):
    """Sound registry generation and reporting of root keys with no CE equivalent."""

    @classmethod
    def setUpClass(cls):
        cls.pack = build_fixture()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "converted"
        settings = Settings()
        settings.write_conversion_log = False
        driver.convert(cls.pack, cls.out, settings=settings, source="itemsadder")
        cls.report = (cls.out / "reports" / "itemsadder.md").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sound_registry_is_generated(self):
        """Without sounds.json the .ogg files are not addressable at all."""
        import json
        path = self.out / "resourcepack" / "assets" / NS / "sounds.json"
        self.assertTrue(path.exists())
        reg = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(reg["ruby_chime"]["sounds"], [f"{NS}:misc/ruby_chime"])
        self.assertEqual(reg["ruby_chime"]["subtitle"], f"subtitles.{NS}.ruby_chime")

    def test_sound_without_a_subfolder_uses_its_own_name(self):
        import json
        reg = json.loads((self.out / "resourcepack" / "assets" / NS / "sounds.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(reg["ruby_thud"]["sounds"], [f"{NS}:ruby_thud"])
        self.assertNotIn("subtitle", reg["ruby_thud"])

    def test_sound_files_are_copied_next_to_the_registry(self):
        base = self.out / "resourcepack" / "assets" / NS / "sounds"
        self.assertTrue((base / "misc" / "ruby_chime.ogg").exists())
        self.assertTrue((base / "ruby_thud.ogg").exists())

    def test_unsupported_root_key_is_reported(self):
        self.assertIn(f"{NS}:<root>` | `entities`", self.report)
        self.assertIn("not converted", self.report)

    def test_handled_root_keys_are_not_reported_as_unsupported(self):
        for key in ("items", "categories", "armors_rendering", "sounds"):
            self.assertNotIn(f"<root>` | `{key}`", self.report)


class ItemsAdderSnbtTests(unittest.TestCase):
    def test_string_nbt_is_parsed_into_components(self):
        parsed = itemsadder._parse_snbt('{my-tag:"hello", another:"useless"}')
        self.assertEqual(parsed, {"my-tag": "hello", "another": "useless"})

    def test_nested_components_are_parsed(self):
        parsed = itemsadder._parse_snbt(
            '{components:{"minecraft:custom_name":{text:"TEST",italic:false}, '
            '"minecraft:custom_data":{bro:"asd"}}}')
        self.assertEqual(parsed["components"]["minecraft:custom_data"], {"bro": "asd"})
        self.assertIs(parsed["components"]["minecraft:custom_name"]["italic"], False)

    def test_numeric_type_suffixes_are_stripped(self):
        parsed = itemsadder._parse_snbt("{count:5b, damage:12s, big:1000000l, ratio:1.5f, flag:true}")
        self.assertEqual(parsed, {"count": 5, "damage": 12, "big": 1000000, "ratio": 1.5, "flag": True})

    def test_malformed_snbt_returns_none_rather_than_guessing(self):
        for bad in ("{broken:", '{unclosed:"x"', "", "not nbt at all"):
            self.assertIsNone(itemsadder._parse_snbt(bad), bad)


class ItemsAdderSettingsTests(unittest.TestCase):
    """The IA-specific switches must actually change the output."""

    def setUp(self):
        self.pack = build_fixture()

    def _convert(self, **overrides):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings()
        settings.write_conversion_log = False
        for key, value in overrides.items():
            setattr(settings, key, value)
        out = Path(tmp.name) / "out"
        driver.convert(self.pack, out, settings=settings, source="itemsadder")
        return out

    def test_disabling_furniture_keeps_the_item_and_reports_it(self):
        out = self._convert(ia_generate_furniture=False)
        self.assertFalse(list(out.glob("configuration/furniture/**/*.yml")))
        # The item survives as a plain item, and the choice is recorded.
        item = yaml.safe_load(
            (out / "configuration" / "items" / NS / "ruby_lamp.yml").read_text(encoding="utf-8")
        )["items"][f"{NS}:ruby_lamp"]
        self.assertNotIn("behavior", item)
        report = (out / "reports" / "itemsadder.md").read_text(encoding="utf-8")
        self.assertIn("ia_generate_furniture is disabled", report)

    def test_default_locale_controls_display_names(self):
        out = self._convert(ia_default_locale="ru")
        body = yaml.safe_load(
            (out / "configuration" / "items" / NS / "ruby_sword.yml").read_text(encoding="utf-8")
        )["items"][f"{NS}:ruby_sword"]
        # The fixture ships a Russian translation for this key.
        self.assertEqual(body["data"]["item_name"], "<!i>Рубиновый меч")

    def test_english_locale_is_the_default(self):
        out = self._convert()
        body = yaml.safe_load(
            (out / "configuration" / "items" / NS / "ruby_sword.yml").read_text(encoding="utf-8")
        )["items"][f"{NS}:ruby_sword"]
        self.assertEqual(body["data"]["item_name"], "<!i>Ruby Sword")

    def test_dropping_material_is_possible(self):
        out = self._convert(ia_preserve_material=False)
        body = yaml.safe_load(
            (out / "configuration" / "items" / NS / "ruby_sword.yml").read_text(encoding="utf-8")
        )["items"][f"{NS}:ruby_sword"]
        self.assertNotIn("material", body)


if __name__ == "__main__":
    unittest.main()
