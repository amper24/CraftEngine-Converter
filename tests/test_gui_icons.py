from types import SimpleNamespace
from converter.ir import ItemNode
from converter.generator import Generator
from converter.config import Settings


def _generate(item):
    analysis = SimpleNamespace(
        items={item.id: item}, blocks={}, metadata=SimpleNamespace(namespace=item.namespace),
        recipes={}, loot={}, sounds={}, lang={}, lang_by_locale={}, resources=[],
    )
    settings = Settings()
    settings.generate_categories = False
    mapping = SimpleNamespace(get=lambda _id: None)
    return Generator(analysis, mapping, settings).generate().files[0].content


def test_3d_item_gets_gui_icon_select():
    item = ItemNode(id="demo:crate", namespace="demo", model="demo:item/crate")
    item.gui_icon_texture = "demo:item/crate"
    item.display_name = "Crate"
    text = _generate(item)
    assert "minecraft:display_context" in text
    assert "__gui_icon" in text
    assert "minecraft:item/generated" in text


def test_model_texture_is_not_mistaken_for_gui_icon():
    item = ItemNode(id="demo:crate", namespace="demo", model="demo:item/crate")
    # Same texture is part of the 3D model, so it must not create a GUI split.
    item.gui_icon_texture = None
    item.textures = ["demo:item/crate"]
    text = _generate(item)
    assert "minecraft:display_context" not in text
    assert "__gui_icon" not in text
