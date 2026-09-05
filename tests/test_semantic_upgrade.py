import json
from pathlib import Path
import tempfile

from converter.archive import open_archive
from converter.analyzer import Analyzer
from converter.config import Settings
from converter.util import Log

FIXTURE = Path(__file__).resolve().parents[1] / "mod-for-tests" / "VeggiesDelight-1.21.1-1.9.3.jar"

def test_model_helpers_are_internal_display_items_only():
    settings=Settings()
    with open_archive(FIXTURE, Log()) as archive:
        result=Analyzer(Log(), "1.21.1", settings).analyze(archive)
    helpers=[i for i in result.items if "_stage" in i.split(":",1)[1]]
    assert helpers
    for item_id in helpers:
        assert result.items[item_id].metadata.get("display_item") is True

def test_semantic_report_is_serializable():
    settings=Settings()
    with open_archive(FIXTURE, Log()) as archive:
        result=Analyzer(Log(), "1.21.1", settings).analyze(archive)
    payload=result.to_dict()
    json.dumps(payload, ensure_ascii=False)


def test_crop_stage_renderers_reference_existing_helpers():
    import yaml
    from converter.generator import Generator
    from converter.capability import build_mapping
    with open_archive(FIXTURE, Log()) as archive:
        result=Analyzer(Log(), "1.21.1", Settings()).analyze(archive)
    mapping=build_mapping(result, Settings())
    out=Generator(result, mapping, Settings()).generate()
    files={f.object_id:f for f in out.files}
    for block_id, block in result.blocks.items():
        if not block.metadata.get("is_crop"):
            continue
        gf=files.get(block_id)
        assert gf is not None
        doc=yaml.safe_load(gf.content)
        appearances=(doc["blocks"][block_id]["states"]["appearances"])
        for age in block.metadata.get("stage_models", {}):
            item_id=f"{block.namespace}:{block.id.split(":")[-1][:-5] if block.id.split(":")[-1].endswith("_crop") else block.id.split(":")[-1]}_stage{age}"
            assert any(v.get("entity_renderer",{}).get("item") == item_id for v in appearances.values())
            assert item_id in files
