import json
import tempfile
import unittest
from pathlib import Path
import sys
import yaml
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from converter.config import Settings
from converter.driver import suggest_namespace_mappings
from converter import validator

FIXTURE = ROOT / 'mod-for-tests' / 'VeggiesDelight-1.21.1-1.9.3.jar'
if not FIXTURE.exists():
    FIXTURE = Path('/mnt/data/dev/mod-for-tests/VeggiesDelight-1.21.1-1.9.3.jar')


@pytest.mark.skipif(not FIXTURE.exists(), reason="requires the external mod fixture mod-for-tests/VeggiesDelight-1.21.1-1.9.3.jar, which is not in this repository (see README)")
class QualityTests(unittest.TestCase):
    def test_namespace_suggestions_ignore_compatibility_tags(self):
        result = suggest_namespace_mappings(FIXTURE, '1.21.1', Settings())
        self.assertNotIn('c', result['namespaces'])
        self.assertNotIn('forge', result['namespaces'])
        self.assertIn('farmersdelight', result['namespaces'])

    def test_generated_output_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            import subprocess
            subprocess.run([
                sys.executable, '-m', 'converter', 'convert', str(FIXTURE),
                '--output', tmp, '--minecraft', '1.21.1', '--target', 'craftengine:26.8'
            ], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            report = validator.validate_output(Path(tmp), validator.Log())
            self.assertTrue(report.valid, report.issues)
            self.assertTrue((Path(tmp) / 'sliceboard' / 'config.yml').exists())
            sb = yaml.safe_load((Path(tmp) / 'sliceboard' / 'recipes' / 'veggiesdelight.yml').read_text(encoding='utf8'))
            self.assertEqual(len(sb['recipes']), 16)
            sample = sb['recipes']['wild_zucchini']['outputs']
            self.assertEqual(len(sample), 3)
            self.assertEqual(sample[-1]['chance'], 0.2)
            self.assertTrue((Path(tmp) / 'resourcepack' / 'assets').exists())


if __name__ == '__main__':
    unittest.main()



def test_food_carrier_materials():
    from converter.generator import Generator
    from converter.config import Settings
    from converter.analyzer import AnalysisResult
    from converter.detector import ModMetadata
    from converter.ir import ItemNode
    from converter.capability import MappingResult
    from converter.util import Log

    settings = Settings()
    analysis = AnalysisResult(ModMetadata(namespace="test"), "1.21.4")
    gen = Generator(analysis, MappingResult(), settings)

    drink = ItemNode(id="test:apple_cider", namespace="test", food={"nutrition":4,"saturation":4.0})
    soup = ItemNode(id="test:mushroom_soup", namespace="test", food={"nutrition":6,"saturation":7.2})
    generic = ItemNode(id="test:berry", namespace="test", food={"nutrition":2,"saturation":1.2})

    assert gen._semantic_carrier_material(drink) == "honey_bottle"
    assert gen._semantic_carrier_material(soup) == "mushroom_stew"
    assert gen._semantic_carrier_material(generic) is None
