import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from converter.archive import open_archive
from converter.bytecode import extract_foods_from_archive
from converter.util import Log

FIXTURE = Path('/mnt/data/dev/mod-for-tests/VeggiesDelight-1.21.1-1.9.3.jar')


def test_neoforge_foodproperties_extraction():
    if not FIXTURE.exists():
        return
    with open_archive(FIXTURE, Log()) as archive:
        foods = extract_foods_from_archive(archive)
    assert len(foods) >= 60
    assert foods['CAULIFLOWER'.lower()].nutrition == 4
    assert round(foods['CAULIFLOWER'.lower()].saturation, 1) == 3.2
    assert foods['CARROT_JUICE'.lower()].can_always_eat is True
