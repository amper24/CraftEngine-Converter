"""Verify that generated model/texture references resolve to copied assets."""

import glob
import os
import sys

import yaml

rp = sys.argv[1] if len(sys.argv) > 1 else "converted/farmersdelight"
refs = []
for f in glob.glob(rp + "/configuration/**/*.yml", recursive=True) + glob.glob(
    rp + "/configuration/**/*.yaml", recursive=True
):
    txt = open(f, encoding="utf-8").read()
    for key in ("item_model", "path", "texture"):
        import re

        for m in re.finditer(rf"{key}:\s*(\S+)", txt):
            refs.append((f, key, m.group(1)))

missing = []
ok = 0
for f, key, value in refs:
    value = value.strip().strip("\"'")
    if ":" not in value:
        continue
    a, b = value.split(":", 1)
    if key == "texture":
        cand = f"{rp}/resourcepack/assets/{a}/textures/{b}.png"
    else:
        cand = f"{rp}/resourcepack/assets/{a}/models/{b}.json"
    if os.path.exists(cand):
        ok += 1
    else:
        missing.append((f, key, value, cand))

print("references:", len(refs))
print("resolved ok:", ok)
print("missing:", len(missing))
for row in missing[:40]:
    print(row)