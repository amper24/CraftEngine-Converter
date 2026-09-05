from converter import analyzer
from converter.archive import open_archive
from converter.util import Log
from pathlib import Path


def test_crop_stage_helper_ids_match_renderer_refs():
    fixture = Path("/mnt/data/work_actual/mod.jar")
    with open_archive(fixture, Log(False)) as archive:
        result = analyzer.Analyzer(Log(False), "1.21.1").analyze(archive)
    for block in result.blocks.values():
        if not block.metadata.get("is_crop"):
            continue
        base = block.id.split(":")[-1]
        display_base = base[:-5] if base.endswith("_crop") else base
        stages = block.metadata.get("stage_models", {})
        for age in stages:
            helper = f"{block.namespace}:{display_base}_stage{age}"
            assert helper in result.items, (block.id, age, helper)
