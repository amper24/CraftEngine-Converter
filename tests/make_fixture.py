"""Build a synthetic mod JAR fixture for end-to-end testing.

Creates tests/farmersdelight_fixture.jar with a realistic set of:
- fabric.mod.json
- item models (tomato, onion, iron_knife)
- block model + blockstate (stove with facing variants, cutting_board)
- blockstate multipart (tomato_crop with age)
- recipes (shapeless, shaped, smelting, custom)
- loot table for stove
- lang file
- a couple of textures

Run: python tests/make_fixture.py
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

NS = "farmersdelight"
ROOT = Path(__file__).resolve().parent

ITEM_MODEL = {"parent": "item/generated", "textures": {"layer0": "farmersdelight:item/{}"}}
BLOCK_CUBE = {"parent": "block/cube_all", "textures": {"all": "farmersdelight:block/{}"}}


def recipe(name: str, body: dict) -> tuple[str, dict]:
    return f"data/{NS}/recipes/{name}.json", body


def build() -> Path:
    files: dict[str, bytes] = {}

    files["fabric.mod.json"] = json.dumps(
        {
            "schemaVersion": 1,
            "id": NS,
            "version": "1.0.0",
            "name": "Farmers Delight Fixture",
            "depends": {"minecraft": ">=1.20.1", "fabricloader": ">=0.14.0"},
        },
        indent=2,
    ).encode()

    # lang
    files[f"assets/{NS}/lang/en_us.json"] = json.dumps(
        {
            "item.farmersdelight.tomato": "Tomato",
            "item.farmersdelight.onion": "Onion",
            "item.farmersdelight.iron_knife": "Iron Knife",
            "block.farmersdelight.stove": "Stove",
            "block.farmersdelight.cutting_board": "Cutting Board",
            "block.farmersdelight.tomato_crop": "Tomato Crop",
        },
        indent=2,
    ).encode()

    # items
    for item in ("tomato", "onion", "iron_knife"):
        model = dict(ITEM_MODEL)
        model["textures"] = {"layer0": f"farmersdelight:item/{item}"}
        files[f"assets/{NS}/models/item/{item}.json"] = json.dumps(model, indent=2).encode()
        files[f"assets/{NS}/textures/item/{item}.png"] = b"\x89PNG\r\n\x1a\n" + item.encode()

    # blocks
    for block_name, model_key in (("stove", "stove_side"), ("cutting_board", "cutting_board")):
        files[f"assets/{NS}/models/block/{block_name}.json"] = json.dumps(
            {"parent": "block/cube_all", "textures": {"all": f"farmersdelight:block/{model_key}"}},
            indent=2,
        ).encode()
        files[f"assets/{NS}/textures/block/{model_key}.png"] = b"\x89PNG\r\n\x1a\n" + block_name.encode()

    # stove blockstate: facing + lit
    files[f"assets/{NS}/blockstates/stove.json"] = json.dumps(
        {
            "variants": {
                "facing=north,lit=false": {"model": "farmersdelight:block/stove"},
                "facing=east,lit=false": {"model": "farmersdelight:block/stove", "y": 90},
                "facing=south,lit=false": {"model": "farmersdelight:block/stove", "y": 180},
                "facing=west,lit=false": {"model": "farmersdelight:block/stove", "y": 270},
                "facing=north,lit=true": {"model": "farmersdelight:block/stove"},
                "facing=east,lit=true": {"model": "farmersdelight:block/stove", "y": 90},
                "facing=south,lit=true": {"model": "farmersdelight:block/stove", "y": 180},
                "facing=west,lit=true": {"model": "farmersdelight:block/stove", "y": 270},
            }
        },
        indent=2,
    ).encode()

    # cutting_board: simple (no variants)
    files[f"assets/{NS}/blockstates/cutting_board.json"] = json.dumps(
        {"variants": {"": {"model": "farmersdelight:block/cutting_board"}}},
        indent=2,
    ).encode()

    # tomato_crop: multipart with age
    files[f"assets/{NS}/blockstates/tomato_crop.json"] = json.dumps(
        {
            "multipart": [
                {"apply": {"model": "farmersdelight:block/tomato_crop"}},
                {
                    "when": {"age": "0"},
                    "apply": {"model": "farmersdelight:block/tomato_crop_stage0"},
                },
                {
                    "when": {"age": "1"},
                    "apply": {"model": "farmersdelight:block/tomato_crop_stage1"},
                },
            ]
        },
        indent=2,
    ).encode()
    files[f"assets/{NS}/models/block/tomato_crop.json"] = json.dumps(
        {"parent": "block/crop", "textures": {"crop": "farmersdelight:block/tomato_crop"}},
        indent=2,
    ).encode()

    # recipes
    files[recipe("tomato_soup", {"type": "minecraft:crafting_shapeless", "ingredients": ["minecraft:bowl", "farmersdelight:tomato"], "result": {"id": "farmersdelight:tomato_soup", "count": 1}})[0]] = json.dumps(
        recipe("tomato_soup", {"type": "minecraft:crafting_shapeless", "ingredients": ["minecraft:bowl", "farmersdelight:tomato"], "result": {"id": "farmersdelight:tomato_soup", "count": 1}})[1],
        indent=2,
    ).encode()

    files[recipe("cutting_tomato", {"type": "minecraft:crafting_shaped", "pattern": ["KK", "T "], "key": {"K": {"item": "farmersdelight:iron_knife"}, "T": {"item": "farmersdelight:tomato"}}, "result": {"id": "farmersdelight:tomato_slices", "count": 2}})[0]] = json.dumps(
        recipe("cutting_tomato", {"type": "minecraft:crafting_shaped", "pattern": ["KK", "T "], "key": {"K": {"item": "farmersdelight:iron_knife"}, "T": {"item": "farmersdelight:tomato"}}, "result": {"id": "farmersdelight:tomato_slices", "count": 2}})[1],
        indent=2,
    ).encode()

    files[recipe("cooked_tomato", {"type": "minecraft:smelting", "ingredient": {"item": "farmersdelight:tomato"}, "result": {"id": "farmersdelight:cooked_tomato"}, "experience": 0.35, "cookingtime": 200})[0]] = json.dumps(
        recipe("cooked_tomato", {"type": "minecraft:smelting", "ingredient": {"item": "farmersdelight:tomato"}, "result": {"id": "farmersdelight:cooked_tomato"}, "experience": 0.35, "cookingtime": 200})[1],
        indent=2,
    ).encode()

    # custom (unsupported) recipe
    files[recipe("custom_machine", {"type": "farmersdelight:custom_processing", "ingredient": {"item": "minecraft:iron_ingot"}, "result": {"id": "farmersdelight:steel_ingot"}})[0]] = json.dumps(
        recipe("custom_machine", {"type": "farmersdelight:custom_processing", "ingredient": {"item": "minecraft:iron_ingot"}, "result": {"id": "farmersdelight:steel_ingot"}})[1],
        indent=2,
    ).encode()

    # Farmer's Delight cooking-pot recipe (custom station -> remap to workbench)
    files[recipe("cooked_tomato_soup", {"type": "farmersdelight:cooking", "ingredients": [{"item": "farmersdelight:tomato"}, {"item": "minecraft:bowl"}], "result": {"id": "farmersdelight:tomato_soup_hot", "count": 1}})[0]] = json.dumps(
        recipe("cooked_tomato_soup", {"type": "farmersdelight:cooking", "ingredients": [{"item": "farmersdelight:tomato"}, {"item": "minecraft:bowl"}], "result": {"id": "farmersdelight:tomato_soup_hot", "count": 1}})[1],
        indent=2,
    ).encode()

    # Farmer's Delight cutting-board recipe (custom station -> SliceBoard)
    files[recipe("sliced_tomato", {"type": "farmersdelight:cutting", "ingredients": [{"item": "farmersdelight:tomato"}, {"item": "farmersdelight:iron_knife"}], "result": {"id": "farmersdelight:tomato_slices", "count": 2}})[0]] = json.dumps(
        recipe("sliced_tomato", {"type": "farmersdelight:cutting", "ingredients": [{"item": "farmersdelight:tomato"}, {"item": "farmersdelight:iron_knife"}], "result": {"id": "farmersdelight:tomato_slices", "count": 2}})[1],
        indent=2,
    ).encode()

    # loot table for stove
    files[f"data/{NS}/loot_tables/blocks/stove.json"] = json.dumps(
        {
            "type": "minecraft:block",
            "pools": [
                {
                    "rolls": 1.0,
                    "entries": [{"type": "minecraft:item", "name": "farmersdelight:stove"}],
                    "conditions": [{"condition": "minecraft:survives_explosion"}],
                }
            ],
        },
        indent=2,
    ).encode()

    # tags
    files[f"data/{NS}/tags/blocks/mineable/pickaxe.json"] = json.dumps(
        {"replace": False, "values": ["farmersdelight:stove"]},
        indent=2,
    ).encode()

    jar_path = ROOT / "farmersdelight_fixture.jar"
    with zipfile.ZipFile(jar_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(files.items()):
            zf.writestr(name, data)

    print(f"wrote {jar_path}")
    return jar_path


if __name__ == "__main__":
    build()