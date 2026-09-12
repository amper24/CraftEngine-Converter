"""End-to-end tests for the resource-pack source: models/ + textures/ -> configs."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from converter import driver, resourcepack  # noqa: E402
from converter.archive import open_archive  # noqa: E402
from converter.config import Settings  # noqa: E402
from converter.util import Log  # noqa: E402

from make_resourcepack_fixture import build as build_fixture  # noqa: E402

NS = "gemworks"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class ResourcePackDetectionTests(unittest.TestCase):
    def setUp(self):
        self.pack = build_fixture()

    def test_pack_is_detected(self):
        with open_archive(self.pack, Log()) as archive:
            layout = resourcepack.detect_resourcepack(archive, Log())
        self.assertIsNotNone(layout)
        self.assertEqual(list(layout.namespaces), [NS])
        self.assertEqual(sorted(layout.namespaces[NS].models), ["topaz", "topaz_sword"])
        self.assertEqual(sorted(layout.namespaces[NS].textures),
                         ["ruby", "topaz", "topaz_pickaxe", "topaz_sword"])

    def test_vanilla_overrides_are_separated_from_content(self):
        with open_archive(self.pack, Log()) as archive:
            layout = resourcepack.detect_resourcepack(archive, Log())
        self.assertNotIn("minecraft", layout.namespaces)
        self.assertIn("minecraft", layout.vanilla_overrides)

    def test_mod_jar_is_not_mistaken_for_a_resource_pack(self):
        jar = ROOT / "tests" / "farmersdelight_fixture.jar"
        if not jar.exists():
            self.skipTest("mod fixture not built")
        with open_archive(jar, Log()) as archive:
            self.assertIsNone(resourcepack.detect_resourcepack(archive, Log()))
            self.assertEqual(driver.detect_source_kind(archive, Log(), "auto"), "mod")

    def test_auto_detection_prefers_itemsadder_over_a_resource_pack(self):
        """An ItemsAdder pack also ships assets/, so the more specific format wins."""
        sys.path.insert(0, str(ROOT / "tests"))
        from make_itemsadder_fixture import build as build_ia
        with open_archive(build_ia(), Log()) as archive:
            self.assertEqual(driver.detect_source_kind(archive, Log(), "auto"), "itemsadder")

    def test_auto_detection_finds_the_resource_pack(self):
        with open_archive(self.pack, Log()) as archive:
            self.assertEqual(driver.detect_source_kind(archive, Log(), "auto"), "resourcepack")

    def test_source_mode_can_force_resourcepack(self):
        with open_archive(self.pack, Log()) as archive:
            self.assertEqual(driver.detect_source_kind(archive, Log(), "resourcepack"), "resourcepack")

    def test_zip_of_the_pack_is_detected(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(shutil.make_archive(str(Path(tmp) / "pack"), "zip", self.pack))
            with open_archive(zip_path, Log()) as archive:
                layout = resourcepack.detect_resourcepack(archive, Log())
            self.assertIsNotNone(layout)
            self.assertEqual(list(layout.namespaces), [NS])


class ResourcePackConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = build_fixture()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "converted"
        settings = Settings()
        settings.write_conversion_log = False
        cls.result = driver.convert(cls.pack, cls.out, settings=settings, source="resourcepack")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _item(self, name: str) -> dict:
        path = self.out / "configuration" / "items" / NS / f"{name}.yml"
        self.assertTrue(path.exists(), f"no generated file for {name}")
        return _load(path)["items"][f"{NS}:{name}"]

    # --- overall ----------------------------------------------------------
    def test_output_validates(self):
        self.assertTrue(self.result["validation"]["valid"], self.result["validation"])
        self.assertEqual(self.result["fidelity"]["issues"], [])

    def test_source_kind_is_recorded(self):
        self.assertEqual(self.result["counts"]["items"], 4)

    def test_report_is_written(self):
        self.assertTrue((self.out / "reports" / "resourcepack.md").exists())

    # --- textures and models ----------------------------------------------
    def test_texture_only_uses_the_simplified_texture_form(self):
        item = self._item("ruby")
        self.assertEqual(item["texture"], f"{NS}:item/ruby")
        self.assertNotIn("model", item, "CraftEngine generates the model for a bare texture")

    def test_existing_model_is_referenced_without_a_generation_block(self):
        """A generation block would override the author's own model .json."""
        item = self._item("topaz")
        self.assertEqual(item["model"]["path"], f"{NS}:item/topaz")
        self.assertNotIn("generation", item["model"])
        self.assertNotIn("texture", item)

    def test_tool_texture_only_gets_the_handheld_parent(self):
        item = self._item("topaz_pickaxe")
        self.assertEqual(item["model"]["generation"]["parent"], "minecraft:item/handheld")
        self.assertEqual(item["model"]["generation"]["textures"]["layer0"],
                         f"{NS}:item/topaz_pickaxe")

    def test_tool_with_its_own_model_keeps_that_model(self):
        item = self._item("topaz_sword")
        self.assertEqual(item["model"]["path"], f"{NS}:item/topaz_sword")
        self.assertNotIn("generation", item["model"], "the pack's model already asks for handheld")

    # --- lang -------------------------------------------------------------
    def test_display_names_come_from_the_pack_lang(self):
        self.assertEqual(self._item("ruby")["data"]["item_name"], "<!i>Ruby")
        self.assertEqual(self._item("topaz")["data"]["item_name"], "<!i>Shiny Topaz")

    # --- reporting --------------------------------------------------------
    def test_block_model_is_reported_not_converted(self):
        self.assertFalse((self.out / "configuration" / "blocks" / NS / "topaz_block.yml").exists())
        report = (self.out / "reports" / "resourcepack.md").read_text(encoding="utf-8")
        self.assertIn("topaz_block", report)
        self.assertIn("only item models are generated", report)

    def test_vanilla_override_is_reported_not_minted(self):
        self.assertFalse((self.out / "configuration" / "items" / "minecraft" / "diamond.yml").exists())
        report = (self.out / "reports" / "resourcepack.md").read_text(encoding="utf-8")
        self.assertIn("vanilla override", report)

    def test_assets_are_copied_into_the_output_pack(self):
        rp = self.out / "resourcepack" / "assets" / NS
        self.assertTrue((rp / "textures" / "item" / "ruby.png").exists())
        self.assertTrue((rp / "models" / "item" / "topaz.json").exists())

    def test_no_unknown_keys_in_generated_yaml(self):
        """Every emitted root key must be a documented CraftEngine key."""
        allowed = {
            "items": {"material", "data", "model", "behavior", "behaviors", "settings", "events",
                      "custom_model_data", "item_model", "texture", "textures", "models",
                      "hand_animation_on_swap", "oversized_in_gui", "swap_animation_scale"},
            "blocks": {"state", "states", "settings", "behavior", "behaviors", "loot", "events"},
            "furniture": {"variants", "settings", "behaviors", "loot", "events"},
        }
        checked = 0
        for path in (self.out / "configuration").rglob("*.yml"):
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
        self.assertGreater(checked, 3)


class ResourcePackSettingsTests(unittest.TestCase):
    def setUp(self):
        self.pack = build_fixture()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _convert(self, **overrides) -> Path:
        settings = Settings()
        settings.write_conversion_log = False
        for key, value in overrides.items():
            setattr(settings, key, value)
        out = Path(self.tmp.name) / f"out_{abs(hash(tuple(sorted(overrides.items()))))}"
        driver.convert(self.pack, out, settings=settings, source="resourcepack")
        return out

    def test_default_material_setting_changes_output(self):
        out = self._convert(rp_default_material="paper")
        item = _load(out / "configuration" / "items" / NS / "ruby.yml")["items"][f"{NS}:ruby"]
        self.assertEqual(item["material"], "minecraft:paper")

    def test_vanilla_override_setting_is_reported(self):
        out = self._convert(rp_skip_vanilla_overrides=False)
        report = (out / "reports" / "resourcepack.md").read_text(encoding="utf-8")
        self.assertIn("copied into the pack", report)


if __name__ == "__main__":
    unittest.main()
