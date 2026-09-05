"""Return coverage for crop model-path resolution and the coverage report.

Regression coverage for the defect where crop YAML ``model.path`` (or the
generated stage model path) contained ``block/custom/`` while the real assets
live at ``block/<crop>_stage<N>``.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from converter.config import Settings
from converter.driver import convert


class CropModelPathsAndCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "mod"
        self.root.mkdir(parents=True)
        (self.root / "fabric.mod.json").write_text(json.dumps({
            "schemaVersion": 1, "id": "veggiesdelight", "version": "1.0", "name": "Veggies",
        }), encoding="utf-8")
        # Every age points at a real model in the resourcepack.
        variants = {}
        for age in range(3):
            variants[f"age={age}"] = {"model": f"veggiesdelight:block/bellpepper_stage{age}"}
        self._write("assets/veggiesdelight/blockstates/bellpepper_crop.json", {"variants": variants})
        for age in range(3):
            self._write(
                f"assets/veggiesdelight/models/block/bellpepper_stage{age}.json",
                {"parent": "minecraft:block/cross",
                 "textures": {"cross": f"veggiesdelight:block/bellpepper_stage{age}"}},
            )
            self._write_bytes(
                f"assets/veggiesdelight/textures/block/bellpepper_stage{age}.png",
                b"\x89PNG\r\n\x1a\n",
            )
        lang = self.root / "assets/veggiesdelight/lang/en_us.json"
        lang.parent.mkdir(parents=True, exist_ok=True)
        lang.write_text(json.dumps({
            "block.veggiesdelight.bellpepper_crop": "Bellpepper",
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, rel: str, data: dict) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def _write_bytes(self, rel: str, data: bytes) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_crop_stage_paths_do_not_use_custom_and_coverage_has_no_missing_models(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            result = convert(str(self.root), out, minecraft_version="1.21.4", settings=Settings())
            self.assertTrue(result["validation"]["valid"], result["validation"]["issues"])

            # No generated configuration references block/custom/.
            config = Path(out) / "configuration"
            hit = False
            for path in config.rglob("*.yml"):
                text = path.read_text(encoding="utf-8")
                if "block/custom/" in text:
                    hit = True
                    self.fail(f"{path} contains block/custom/: {text}")
            self.assertFalse(hit)

            # The stage display items point at the real stage model files.
            for age in range(3):
                item = Path(out) / "configuration" / "crops" / "veggiesdelight" / f"bellpepper_stage{age}.yml"
                self.assertTrue(item.exists(), item)
                body = item.read_text(encoding="utf-8")
                self.assertIn(f"veggiesdelight:block/bellpepper_stage{age}", body)

            coverage_path = Path(out) / "reports" / "coverage.json"
            self.assertTrue(coverage_path.exists())
            coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
            refs = coverage["object_references"]
            self.assertEqual(refs["missing_models"], [])
            self.assertEqual(refs["missing_textures"], [])
            self.assertGreaterEqual(coverage["resourcepack"]["model_coverage_pct"], 0)


if __name__ == "__main__":
    unittest.main()
