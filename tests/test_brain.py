"""Tests for the micro neural network and the neural semantic pass."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from converter.analyzer import AnalysisResult
from converter.brain import features, get_brain, labels
from converter.config import Settings
from converter.detector import ModMetadata
from converter.ir import BlockNode, BlockStateNode, ItemNode
from converter.semantics import apply_semantics
from converter.util import Log


def item(id_: str, **kwargs) -> ItemNode:
    node = ItemNode(id=id_, namespace=id_.split(":", 1)[0], kind="item")
    for key, value in kwargs.items():
        setattr(node, key, value)
    return node


def block(id_: str, **kwargs) -> BlockNode:
    node = BlockNode(id=id_, namespace=id_.split(":", 1)[0], kind="block")
    for key, value in kwargs.items():
        setattr(node, key, value)
    return node


class FeatureTests(unittest.TestCase):
    def test_vector_dimensions_match_the_model_contract(self):
        self.assertEqual(features.item_vector("mymod:apple").shape, (features.ITEM_DIM,))
        self.assertEqual(features.block_vector("mymod:stone").shape, (features.BLOCK_DIM,))

    def test_hashing_is_stable_across_calls(self):
        first = features.item_vector("mymod:tomato_soup", {"has_food_component": 1.0})
        second = features.item_vector("mymod:tomato_soup", {"has_food_component": 1.0})
        self.assertTrue((first == second).all())

    def test_namespace_does_not_change_the_semantics(self):
        a = features.text_vector("modone:iron_pickaxe")
        b = features.text_vector("modtwo:iron_pickaxe")
        self.assertTrue((a == b).all())

    def test_tokenizer_drops_the_namespace(self):
        self.assertEqual(features.tokenize("mymod:wild_cabbage"), ["wild", "cabbage"])


class BrainInferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brain = get_brain()
        if not cls.brain.available:
            raise unittest.SkipTest(f"brain unavailable: {cls.brain.load_error}")

    def assert_category(self, item_id: str, expected: str):
        pred = self.brain.classify_item(item(item_id))
        self.assertEqual(pred.labels["category"], expected, f"{item_id} -> {pred.explanation}")

    def test_gear_categories(self):
        self.assert_category("mymod:iron_sword", "Weapons")
        self.assert_category("mymod:copper_pickaxe", "Tools")
        self.assert_category("mymod:diamond_helmet", "Armor")
        self.assert_category("mymod:steel_crossbow", "Ranged")

    def test_food_families(self):
        for item_id, family in (
            ("mymod:apple_juice", "drink"),
            ("mymod:tomato_soup", "soup"),
            ("mymod:blueberry_muffin", "sweet"),
        ):
            pred = self.brain.classify_item(item(item_id))
            self.assertEqual(pred.labels["food_family"], family, f"{item_id} -> {pred.explanation}")

    def test_tool_tier_is_read_from_the_material_word(self):
        pred = self.brain.classify_item(item("mymod:netherite_axe"))
        self.assertEqual(pred.labels["tool_tier"], "netherite")

    def test_block_representation(self):
        pred = self.brain.classify_block(block("mymod:rice_crop"))
        self.assertEqual(pred.labels["block_kind"], "crop")
        self.assertEqual(pred.labels["transparent"], "transparent")

        pred = self.brain.classify_block(block("mymod:marble_bricks"))
        self.assertEqual(pred.labels["block_kind"], "solid")
        self.assertEqual(pred.labels["transparent"], "opaque")

    def test_every_label_is_inside_its_declared_space(self):
        pred = self.brain.classify_item(item("mymod:whatever_thing"))
        for head, space in labels.ITEM_HEADS.items():
            self.assertIn(pred.labels[head], space)

    def test_commands_are_generated_for_every_object(self):
        pred = self.brain.classify_item(item("mymod:iron_sword"))
        self.assertTrue(any(c.startswith("/ce give") for c in pred.commands))
        self.assertIn("mymod:iron_sword", pred.commands[0])

        pred = self.brain.classify_block(block("mymod:oak_chair"))
        self.assertTrue(any("setblock" in c for c in pred.commands))

    def test_predictions_are_cached_and_deterministic(self):
        a = self.brain.classify_item(item("mymod:cabbage_stew"))
        b = self.brain.classify_item(item("mymod:cabbage_stew"))
        self.assertEqual(a.labels, b.labels)


class SemanticPassTests(unittest.TestCase):
    def _analysis(self) -> AnalysisResult:
        result = AnalysisResult(ModMetadata(namespace="mymod"), "1.21.4")
        return result

    def test_disabled_by_settings(self):
        settings = Settings()
        settings.brain_enabled = False
        result = apply_semantics(self._analysis(), settings, Log())
        self.assertFalse(result.enabled)
        self.assertIn("disabled", result.reason)

    def test_gear_kind_is_filled_but_never_overwritten(self):
        if not get_brain().available:
            self.skipTest("brain unavailable")
        analysis = self._analysis()
        analysis.items["mymod:steel_glaive"] = item("mymod:steel_glaive")
        analysis.items["mymod:odd_sword"] = item("mymod:odd_sword", gear_kind="tool")
        apply_semantics(analysis, Settings(), Log())

        self.assertEqual(analysis.items["mymod:steel_glaive"].gear_kind, "spear")
        # An analyzer decision stays authoritative.
        self.assertEqual(analysis.items["mymod:odd_sword"].gear_kind, "tool")

    def test_existing_food_component_is_authoritative(self):
        if not get_brain().available:
            self.skipTest("brain unavailable")
        analysis = self._analysis()
        analysis.items["mymod:beef_stew"] = item("mymod:beef_stew", food={"nutrition": 9})
        apply_semantics(analysis, Settings(), Log())
        node = analysis.items["mymod:beef_stew"]
        self.assertEqual(node.food, {"nutrition": 9})
        self.assertNotIn("food_candidate", node.metadata)

    def test_crop_evidence_is_not_overridden(self):
        if not get_brain().available:
            self.skipTest("brain unavailable")
        analysis = self._analysis()
        node = block("mymod:whatever_block", auto_state="higher_tripwire")
        node.metadata["is_crop"] = True
        analysis.blocks[node.id] = node
        apply_semantics(analysis, Settings(), Log())
        self.assertEqual(node.auto_state, "higher_tripwire")

    def test_report_payload_is_serializable(self):
        if not get_brain().available:
            self.skipTest("brain unavailable")
        import json

        analysis = self._analysis()
        analysis.items["mymod:iron_sword"] = item("mymod:iron_sword")
        analysis.blocks["mymod:oak_chair"] = block("mymod:oak_chair")
        result = apply_semantics(analysis, Settings(), Log())
        payload = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertIn("mymod:iron_sword", payload)
        self.assertIn("/ce give", payload)


if __name__ == "__main__":
    unittest.main()
