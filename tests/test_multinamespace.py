"""Return coverage for multi-namespace content detection."""

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
from converter.driver import convert
from converter.util import Log


class MultiNamespaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._write("fabric.mod.json", {
            "schemaVersion": 1,
            "id": "alpha",
            "version": "1.0",
            "name": "Multi NS Mod",
        })
        # Items and lang under two different asset namespaces.
        self._write("assets/alpha/models/item/foo.json", {
            "parent": "minecraft:item/generated",
            "textures": {"layer0": "alpha:item/foo"},
        })
        self._write("assets/alpha/textures/item/foo.png", b"\x89PNG\r\n\x1a\n")
        self._write("assets/alpha/lang/en_us.json", {"item.alpha.foo": "Alpha Foo"})
        self._write("assets/beta/models/item/bar.json", {
            "parent": "minecraft:item/generated",
            "textures": {"layer0": "beta:item/bar"},
        })
        self._write("assets/beta/textures/item/bar.png", b"\x89PNG\r\n\x1a\n")
        self._write("assets/beta/lang/en_us.json", {"item.beta.bar": "Beta Bar"})
        # Recipes for both namespaces, including a compatibility namespace.
        self._write("data/alpha/recipes/foo_recipe.json", {
            "type": "minecraft:crafting_shapeless",
            "ingredients": [{"item": "minecraft:stick"}],
            "result": {"id": "alpha:foo", "count": 1},
        })
        self._write("data/beta/recipes/bar_recipe.json", {
            "type": "minecraft:crafting_shapeless",
            "ingredients": [{"item": "minecraft:stick"}],
            "result": {"id": "beta:bar", "count": 1},
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, rel: str, data) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (dict, list)):
            path.write_text(json.dumps(data), encoding="utf-8")
        else:
            path.write_bytes(data)

    def test_items_recipes_lang_discovered_in_all_content_namespaces(self):
        archive = ModArchive(self.root, Log())
        analysis = analyze_archive(archive, Log(), "1.21.4", Settings())
        self.assertIn("alpha", analysis.content_namespaces)
        self.assertIn("beta", analysis.content_namespaces)
        self.assertIn("alpha:foo", analysis.items)
        self.assertIn("beta:bar", analysis.items)
        self.assertIn("alpha:foo_recipe", analysis.recipes)
        self.assertIn("beta:bar_recipe", analysis.recipes)
        self.assertEqual(analysis.items["alpha:foo"].display_name, "Alpha Foo")
        self.assertEqual(analysis.items["beta:bar"].display_name, "Beta Bar")
        # Item bodies carry the correct per-namespace texture reference.
        self.assertEqual(analysis.items["alpha:foo"].textures, ["alpha:item/foo"])
        self.assertEqual(analysis.items["beta:bar"].textures, ["beta:item/bar"])

    def test_categories_generated_per_namespace(self):
        with tempfile.TemporaryDirectory() as out:
            result = convert(str(self.root), out, minecraft_version="1.21.4", settings=Settings())
            self.assertTrue(result["validation"]["valid"], result["validation"]["issues"])
            cat_alpha = Path(out) / "configuration" / "categories" / "alpha.yml"
            cat_beta = Path(out) / "configuration" / "categories" / "beta.yml"
            self.assertTrue(cat_alpha.exists())
            self.assertTrue(cat_beta.exists())
            self.assertTrue((Path(out) / "configuration" / "items" / "alpha" / "foo.yml").exists())
            self.assertTrue((Path(out) / "configuration" / "items" / "beta" / "bar.yml").exists())

    def test_texture_only_item_referenced_by_recipe_is_materialized(self):
        root = Path(self.tmp.name) / "ref"
        root.mkdir(parents=True)
        (root / "fabric.mod.json").write_text(json.dumps({
            "schemaVersion": 1, "id": "refNS", "version": "1.0", "name": "Ref",
        }), encoding="utf-8")
        tex = root / "assets" / "refNS" / "textures" / "item" / "lone.png"
        tex.parent.mkdir(parents=True)
        tex.write_bytes(b"\x89PNG\r\n\x1a\n")
        rec = root / "data" / "refNS" / "recipes" / "lone_recipe.json"
        rec.parent.mkdir(parents=True)
        rec.write_text(json.dumps({
            "type": "minecraft:crafting_shapeless",
            "ingredients": [{"item": "minecraft:stick"}],
            "result": {"id": "refNS:lone", "count": 1},
        }), encoding="utf-8")
        analysis = analyze_archive(ModArchive(root, Log()), Log(), "1.21.4", Settings())
        self.assertIn("refNS:lone", analysis.items)
        self.assertEqual(analysis.items["refNS:lone"].textures, ["refNS:item/lone"])

    def test_resourcepack_is_copied_verbatim_including_minecraft_overrides(self):
        root = Path(self.tmp.name) / "vb"
        root.mkdir(parents=True)
        (root / "fabric.mod.json").write_text(json.dumps({
            "schemaVersion": 1, "id": "vb", "version": "1.0", "name": "VB",
        }), encoding="utf-8")
        # Own mod asset.
        self._write_asset(root, "assets/vb/models/item/foo.json", {
            "parent": "minecraft:item/generated",
            "textures": {"layer0": "vb:item/foo"},
        })
        self._write_bytes(root, "assets/vb/textures/item/foo.png", b"\x89PNG\r\n\x1a\n")
        # Vanilla override previously excluded by the namespace filter.
        self._write_asset(root, "assets/minecraft/models/item/stone.json", {
            "parent": "minecraft:item/generated",
            "textures": {"layer0": "minecraft:item/stone"},
        })
        self._write_bytes(root, "assets/minecraft/textures/item/stone.png", b"\x89PNG\r\n\x1a\n")
        # Arbitrary extra asset namespace.
        self._write_asset(root, "assets/extra/models/block/thing.json", {
            "parent": "minecraft:block/cube_all",
            "textures": {"all": "extra:block/thing"},
        })
        self._write_bytes(root, "assets/extra/textures/block/thing.png", b"\x89PNG\r\n\x1a\n")

        with tempfile.TemporaryDirectory() as out:
            result = convert(str(root), out, minecraft_version="1.21.4", settings=Settings())
            self.assertTrue(result["validation"]["valid"], result["validation"]["issues"])
            rp = Path(out) / "resourcepack"
            self.assertTrue((rp / "assets/minecraft/models/item/stone.json").exists())
            self.assertTrue((rp / "assets/minecraft/textures/item/stone.png").exists())
            self.assertTrue((rp / "assets/extra/models/block/thing.json").exists())
            self.assertTrue((rp / "assets/extra/textures/block/thing.png").exists())
            self.assertTrue((rp / "assets/vb/models/item/foo.json").exists())
            manifest = json.loads((rp / "pack-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest.get("verbatim"))
            self.assertGreaterEqual(len(manifest["imported"]), 6)

    @staticmethod
    def _write_bytes(root: Path, rel: str, data: bytes) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _write_asset(self, root: Path, rel: str, data: dict) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
