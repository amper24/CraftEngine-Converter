from pathlib import Path
import sys, yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from converter.generator import Generator, GenerationResult
from converter.ir import ItemNode
from converter import status, capability


def test_category_reference_shape():
    g = object.__new__(Generator)
    class S:
        generate_category_translations = True
        drink_keywords = ['juice']
        soup_keywords = ['soup']
        food_enabled = True
        food_keywords = []
    g.settings = S()
    g.analysis = type('A', (), {'items': {}})()
    g._is_food = lambda i: False
    g.analysis.items = {
        'demo:tools_item': ItemNode(id='demo:tools_item', namespace='demo', metadata={}, tags=['c:tools'], behavior=None),
        'demo:crop_item': ItemNode(id='demo:crop_item', namespace='demo', metadata={}, tags=['c:crops'], behavior=None),
    }
    out = GenerationResult()
    for i in g.analysis.items.values():
        out.add_file(type('F', (), {'domain': status.DOMAIN_ITEM, 'object_id': i.id})())
    g._generate_categories(out)
    cat = next(f for f in out.files if f.domain == 'category')
    doc = yaml.safe_load(cat.content)
    cats = doc['categories']
    assert cats['demo:main']['list'] == ['#demo:crops', '#demo:tools']
    assert cats['demo:crops']['hidden'] is True
    assert cats['demo:crops']['list'] == ['demo:crop_item']
    assert cats['demo:tools']['list'] == ['demo:tools_item']
    assert 'source' not in cats['demo:crops']
    assert cats['demo:main']['name'] == '<!i><white><l10n:category.demo.name></white>'
