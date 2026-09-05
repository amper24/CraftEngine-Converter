"""Return coverage for built-in third-party recipe station defaults."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from converter import capability, status
from converter.analyzer import analyze_archive
from converter.archive import ModArchive
from converter.config import Settings
from converter.util import Log


COOKING = {
    "type": "farmersdelight:cooking",
    "ingredients": [{"item": "minecraft:paper"}, {"item": "minecraft:blaze_powder"}],
    "result": {"item": "mynethersdelight:burn_roll"},
    "experience": 0.5,
    "cookingtime": 200,
    "container": {"item": "farmersdelight:cooking_pot"},
}

CUTTING = {
    "type": "farmersdelight:cutting",
    "ingredients": [{"item": "minecraft:blaze_rod"}],
    "tool": {"type": "farmersdelight:item_ability", "action": "axe_dig"},
    "result": [
        {"item": "mynethersdelight:blaze_powder", "count": 2},
        {"item": "minecraft:blaze_powder", "count": 1, "chance": 0.5},
    ],
    "sound": "minecraft:item.axe.strip",
}


class RecipeStationDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._write("data/mynethersdelight/recipes/cooking/cooking/burn_roll.json", COOKING)
        self._write("data/mynethersdelight/recipes/cutting/cutting/blaze_rod.json", CUTTING)
        self.settings = Settings()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, rel: str, data: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def _mapping(self, settings: Settings):
        archive = ModArchive(self.root, Log())
        analysis = analyze_archive(archive, Log(), "1.21.4", settings)
        return analysis, capability.build_mapping(analysis, settings)

    def test_farmers_delight_cooking_defaults_to_workbench(self):
        analysis, mapping = self._mapping(self.settings)
        recipe = analysis.recipes["mynethersdelight:cooking/cooking/burn_roll"]
        self.assertEqual(recipe.station, "crafting_table")
        decision = mapping.get(recipe.id)
        self.assertEqual(decision.result, status.TRANSFORM)
        self.assertEqual(recipe.experience, 0.5)
        self.assertEqual(recipe.time, 200)

    def test_farmers_delight_cutting_defaults_to_sliceboard(self):
        analysis, mapping = self._mapping(self.settings)
        recipe = analysis.recipes["mynethersdelight:cutting/cutting/blaze_rod"]
        self.assertEqual(recipe.station, "sliceboard")
        decision = mapping.get(recipe.id)
        self.assertEqual(decision.result, status.TRANSFORM)

    def test_user_can_disable_known_station_and_get_actionable_reason(self):
        settings = Settings()
        settings.recipe_stations = settings.recipe_stations | {
            "farmersdelight:cooking": "unknown",
            "farmersdelight:cutting": "unknown",
        }
        analysis, mapping = self._mapping(settings)
        decision = mapping.get("mynethersdelight:cooking/cooking/burn_roll")
        self.assertEqual(decision.result, status.UNSUPPORTED)
        self.assertIn("KNOWN_SOURCE_SERIALIZER", decision.reason)
        self.assertIn("farmersdelight:cooking", decision.reason)
        # User overrides win over built-in defaults.
        self.assertEqual(analysis.recipes["mynethersdelight:cooking/cooking/burn_roll"].station, "unknown")


if __name__ == "__main__":
    unittest.main()
