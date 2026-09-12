"""Build a synthetic vanilla resource pack for end-to-end testing.

Covers the shapes the resource-pack adapter has to tell apart:
  * a texture with no model        -> CE simplified `texture`
  * a model .json (with a texture) -> explicit CE `model`, no generation block
  * a tool-named texture           -> handheld parent
  * a block model                  -> reported, not minted into an item
  * an assets/minecraft override   -> reported, not minted into an item
  * a lang file                    -> display names
"""

from __future__ import annotations

import json
from pathlib import Path

NS = "gemworks"
OUT = Path(__file__).resolve().parent / "resourcepack_fixture"


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 1x1 transparent PNG
    path.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082"))


def _json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


GENERATED_MODEL = {
    "parent": "minecraft:item/generated",
    "textures": {"layer0": f"{NS}:item/topaz"},
}

HANDHELD_PARENT_MODEL = {
    "parent": "minecraft:item/handheld",
    "textures": {"layer0": f"{NS}:item/topaz_sword"},
}

BLOCK_MODEL = {
    "parent": "minecraft:block/cube_all",
    "textures": {"all": f"{NS}:block/topaz_block"},
}


def build(root: Path | None = None) -> Path:
    root = Path(root) if root else OUT
    assets = root / "assets"
    item_models = assets / NS / "models" / "item"
    item_tex = assets / NS / "textures" / "item"

    # texture only -> CE generates the model
    _png(item_tex / "ruby.png")

    # model + texture -> CE references the existing model
    _json(item_models / "topaz.json", GENERATED_MODEL)
    _png(item_tex / "topaz.png")

    # tool name with a model that already asks for handheld
    _json(item_models / "topaz_sword.json", HANDHELD_PARENT_MODEL)
    _png(item_tex / "topaz_sword.png")

    # tool name, texture only -> adapter applies the handheld parent
    _png(item_tex / "topaz_pickaxe.png")

    # block model -> reported, not converted into an item
    _json(assets / NS / "models" / "block" / "topaz_block.json", BLOCK_MODEL)
    _png(assets / NS / "textures" / "block" / "topaz_block.png")

    # vanilla override -> reported, not minted into new content
    _png(assets / "minecraft" / "textures" / "item" / "diamond.png")

    _json(assets / NS / "lang" / "en_us.json", {
        f"item.{NS}.ruby": "Ruby",
        f"item.{NS}.topaz": "Shiny Topaz",
        f"item.{NS}.topaz_sword": "Topaz Sword",
        f"item.{NS}.topaz_pickaxe": "Topaz Pickaxe",
    })

    _json(root / "pack.mcmeta", {"pack": {"pack_format": 34, "description": "GemWorks test pack"}})
    return root


if __name__ == "__main__":
    import shutil
    if OUT.exists():
        shutil.rmtree(OUT)
    built = build()
    files = sorted(p.relative_to(built).as_posix() for p in built.rglob("*") if p.is_file())
    print(f"wrote {built} ({len(files)} files)")
    for f in files:
        print("  ", f)
