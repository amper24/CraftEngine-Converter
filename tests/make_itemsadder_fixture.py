"""Build a synthetic ItemsAdder content pack for end-to-end testing.

Creates ``tests/itemsadder_fixture/`` shaped like
``plugins/ItemsAdder/contents/<namespace>/`` and covers the parts of the
ItemsAdder format the converter has to understand:

- items with attributes / durability / enchants / item_flags
- consumable food (both the modern `consumable:` and the legacy `events.eat.feed`)
- a block (`behaviours.block` with placed_model, hardness, tools, sounds, drop)
- a furniture (`behaviours.furniture` with hitbox, light, placeable_on)
- template / variant_of inheritance
- categories with a wildcard member
- a lang file with translation keys
- legacy ``textures/`` + ``models/`` resource layout
- a second namespace using the modern ``resources/resourcepack/assets`` layout
- a datapack recipe shipped inside the pack

Run: python tests/make_itemsadder_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "itemsadder_fixture"

NS = "rubbishpack"
NS2 = "decoration"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, obj: object) -> None:
    _write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _png(path: Path, size: int = 16) -> None:
    """Write a real (tiny) PNG so the pack contains actual texture bytes."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF
        )

    raw = b"".join(b"\x00" + bytes([200, 60, 60, 255]) * size for _ in range(size))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


ITEMS_YML = f"""info:
  namespace: {NS}

categories:
  tools:
    enabled: true
    icon: "{NS}:ruby_sword"
    name: "&cTools"
    items:
      - "{NS}:ruby_sword"
      - "{NS}:ruby_pickaxe"
  everything:
    enabled: true
    icon: "{NS}:ruby"
    name: "&eEverything"
    items:
      - "{NS}:*"
  disabled_one:
    enabled: false
    icon: "{NS}:ruby"
    name: "Hidden"
    items:
      - "{NS}:ruby"

lang:
  'en':
    display-name-ruby: Ruby
    display-name-ruby_sword: Ruby Sword
    display-name-ruby_block: Ruby Block
    lore-decorative: A decorative thing
  'ru':
    display-name-ruby: Рубин
    display-name-ruby_sword: Рубиновый меч

items:
  ruby:
    display_name: display-name-ruby
    resource:
      material: DIAMOND
      generate: true
      textures:
        - item/ruby.png

  # Explicitly disabled: must not be generated.
  ruby_disabled:
    enabled: false
    display_name: Ruby Disabled
    resource:
      material: DIAMOND
      generate: true
      textures:
        - item/ruby.png

  # Template: must not be generated on its own.
  gem_template:
    template: true
    resource:
      material: DIAMOND
      generate: true
      textures:
        - item/ruby.png

  ruby_pickaxe:
    variant_of: gem_template
    display_name: Ruby Pickaxe
    resource:
      model_path: item/ruby_pickaxe
      generate: false
    durability:
      max_durability: 900
      disappear_when_broken: false
    attribute_modifiers:
      mainhand:
        attackDamage: 4
        attackSpeed: -2.8
    # Item-level behaviour with no CraftEngine equivalent: must be reported.
    behaviours:
      liquid_analyzer: true

  ruby_sword:
    display_name: display-name-ruby_sword
    lore:
      - lore-decorative
    resource:
      material: DIAMOND_SWORD
      generate: true
      textures:
        - item/ruby_sword.png
    durability:
      max_durability: 1561
      unbreakable: false
    attribute_modifiers:
      mainhand:
        - type: ATTACK_DAMAGE
          amount: 7
          operation: ADD_NUMBER
        - type: ATTACK_SPEED
          amount: -2.4
          operation: ADD_NUMBER
    enchants:
      - DAMAGE_ALL:2
    item_flags:
      - HIDE_ENCHANTS
      - HIDE_ATTRIBUTES
    max_stack_size: 1
    glint: false
    fuel: 200
    events:
      attack:
        chance: 0.35
        cooldown: 20
        actions:
          - play_sound:
              name: entity.pig.ambient
              volume: 1.5
          - play_sound_2:
              name: entity.pig.hurt
              pitch: 2
          - message:
              text: "&cHit!"
              target: player
          - potion_effect:
              type: SPEED
              duration: 60
              amplifier: 1
          - increment_amount:
              amount: 1
          - veinminer: true
      interact:
        actions:
          - execute_commands:
              command: "say hello <player>"
          - open_inventory:
              inventory: my_custom_menu
          - drop_item:
              item: DIAMOND
              min_amount: 2
      wear:
        actions:
          - message:
              text: "Equipped"

  ruby_apple:
    display_name: Ruby Apple
    resource:
      material: APPLE
      generate: true
      textures:
        - item/ruby.png
    consumable:
      nutrition: 7
      saturation: 4.5
      consume_seconds: 1.2

  ruby_juice:
    display_name: Ruby Juice
    resource:
      material: POTION
      generate: true
      textures:
        - item/ruby.png
    events:
      drink:
        feed:
          amount: 2
          saturation: 1.0

  ruby_helmet:
    display_name: Ruby Helmet
    resource:
      material: DIAMOND_HELMET
      generate: true
      textures:
        - item/ruby.png
    durability:
      max_custom_durability: 363
    specific_properties:
      armor:
        slot: head
        custom_armor: rubyarmor
    attribute_modifiers:
      head:
        armor: 3
        armorToughness: 2

  ruby_block:
    display_name: display-name-ruby_block
    resource:
      material: PAPER
      generate: false
      model_path: block/ruby_block
    behaviours:
      block:
        placed_model:
          type: REAL_NOTE
        hardness: 3.5
        blast_resistance: 9
        light_level: 7
        no_explosion: true
        break_tools_whitelist:
          - PICKAXE
        break_tools_blacklist:
          - WOODEN_PICKAXE
        drop_when_mined: true
        sound:
          break:
            name: BLOCK_METAL_BREAK
            volume: 1
            pitch: 0.9
          place:
            name: minecraft:block.amethyst_block.place
    drop:
      break_block:
        silktouch: false
        loots:
          - item: ruby
            min_amount: 2
            max_amount: 4
            chance: 75

  ruby_lamp:
    display_name: Ruby Lamp
    lore:
      - lore-decorative
    resource:
      material: PAPER
      generate: false
      model_path: ruby_lamp
    behaviours:
      furniture:
        entity: item_display
        light_level: 13
        solid: true
        placeable_on:
          floor: true
          walls: true
          ceiling: false
        hitbox:
          width: 1
          height: 2
        display_transformation:
          transform: HEAD
          translation:
            x: 0
            y: 0.92
            z: 0
          scale:
            x: 0.45
            y: 0.45
            z: 0.45
          right_rotation:
            axis_angle:
              angle: 180
              axis:
                x: 0
                y: 1
                z: 0

armors_rendering:
  rubyarmor:
    color: "#d60000"
    layer_1: armor/rubyarmor/layer_1
    layer_2: armor/rubyarmor/layer_2
"""

DECOR_ITEMS_YML = f"""info:
  namespace: {NS2}

items:
  statue:
    display_name: Statue
    resource:
      material: PAPER
      generate: false
      model_path: item/statue
"""

SWORD_MODEL = {
    "parent": "minecraft:item/handheld",
    "textures": {"layer0": f"{NS}:item/ruby_sword"},
}

BLOCK_MODEL = {
    "parent": "minecraft:block/cube_all",
    "textures": {"all": f"{NS}:block/ruby_block"},
}

LAMP_MODEL = {
    "parent": "minecraft:block/block",
    "textures": {"particle": f"{NS}:block/ruby_block"},
    "elements": [
        {
            "from": [4, 0, 4],
            "to": [12, 16, 12],
            "faces": {
                side: {"texture": "#particle"}
                for side in ("north", "south", "west", "east", "up", "down")
            },
        }
    ],
}

STATUE_MODEL = {
    "parent": "minecraft:item/generated",
    "textures": {"layer0": f"{NS2}:item/statue"},
}

RECIPE = {
    "type": "minecraft:crafting_shaped",
    "pattern": ["RR", "RR"],
    "key": {"R": {"item": f"{NS}:ruby"}},
    "result": {"item": f"{NS}:ruby_block", "count": 1},
}


def build() -> Path:
    if OUT.exists():
        import shutil

        shutil.rmtree(OUT)

    ns_root = OUT / "contents" / NS
    _write(ns_root / "_items.yml", ITEMS_YML)

    # Legacy resource layout: textures/ and models/ directly in the namespace.
    _png(ns_root / "textures" / "item" / "ruby.png")
    _png(ns_root / "textures" / "item" / "ruby_sword.png")
    _png(ns_root / "textures" / "block" / "ruby_block.png")
    _write_json(ns_root / "models" / "item" / "ruby_pickaxe.json", SWORD_MODEL)
    _write_json(ns_root / "models" / "block" / "ruby_block.json", BLOCK_MODEL)
    _write_json(ns_root / "models" / "ruby_lamp.json", LAMP_MODEL)

    # Modern resource layout in the second namespace.
    ns2_root = OUT / "contents" / NS2
    _write(ns2_root / "items.yml", DECOR_ITEMS_YML)
    _png(ns2_root / "resources" / "resourcepack" / "assets" / NS2 / "textures" / "item" / "statue.png")
    _write_json(
        ns2_root / "resources" / "resourcepack" / "assets" / NS2 / "models" / "item" / "statue.json",
        STATUE_MODEL,
    )

    # Datapack recipe shipped inside the pack.
    _write_json(ns_root / "data" / NS / "recipes" / "ruby_block.json", RECIPE)

    return OUT


if __name__ == "__main__":
    path = build()
    files = sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())
    print(f"wrote {path} ({len(files)} files)")
    for f in files:
        print("  ", f)
