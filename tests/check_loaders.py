"""Verify loader detection (Fabric / Forge / NeoForge / Quilt)."""

import os
import shutil
import sys
import tempfile

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from converter.archive import ModArchive
from converter.detector import ModDetector
from converter.util import Log

FABRIC = '{"schemaVersion":1,"id":"fabricmod","version":"1.0","name":"F","depends":{"minecraft":">=1.20"}}'
QUILT = '{"quilt_loader":{"id":"quiltmod","version":"1.0","metadata":{"name":"Q"},"depends":[{"id":"minecraft"}]}}'
FORGE = 'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="forgemod"\nversion="1.0"\ndisplayName="Forge Mod"\n'


def detect_case(root: Path, name: str) -> None:
    case_dir = root / name
    case_dir.mkdir(parents=True, exist_ok=True)
    if name == "fabric":
        (case_dir / "fabric.mod.json").write_text(FABRIC, encoding="utf-8")
        (case_dir / "assets").mkdir(parents=True, exist_ok=True)
        (case_dir / "assets" / "fabricmod").mkdir(parents=True, exist_ok=True)
    elif name == "quilt":
        (case_dir / "quilt.mod.json").write_text(QUILT, encoding="utf-8")
    elif name == "forge":
        (case_dir / "META-INF").mkdir(parents=True, exist_ok=True)
        (case_dir / "META-INF" / "mods.toml").write_text(FORGE, encoding="utf-8")
    elif name == "neoforge":
        (case_dir / "META-INF").mkdir(parents=True, exist_ok=True)
        (case_dir / "META-INF" / "neoforge.mods.toml").write_text(FORGE, encoding="utf-8")

    with ModArchive(case_dir, Log()) as archive:
        meta = ModDetector(Log()).detect(archive)
    print(f"{name:10s} -> loader={meta.loader!r:10s} id={meta.id!r} namespace={meta.namespace!r}")


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="ce_loaders_"))
    try:
        for name in ("fabric", "quilt", "forge", "neoforge"):
            detect_case(root, name)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()