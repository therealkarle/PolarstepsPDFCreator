from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polarsteps_pdf_generator import HtmlPDFBuilder, TripParser, MapGenerator
p = TripParser(Path('BSPData/trip/abihutte_10517085'))
p.load()
mapg = MapGenerator()
b = HtmlPDFBuilder(Path('out.pdf'), p, mapg)

# Quick manual smoke test
print('building html...')
try:
    h = b._build_html()
    print('html length', len(h))
    print(h[:1200])
except Exception as e:
    import traceback; traceback.print_exc()

# New minimally-configured behavior tests for photo filling logic

def run_photo_split_tests():
    b1 = HtmlPDFBuilder(Path('out.pdf'), p, mapg, config={'photos_before_page_break': 6, 'fill_page_with_photos': True, 'photo_wall_fill_limit': 8})
    photos = [Path(f'photo_{i}.jpg') for i in range(10)]
    show, extra = b1._split_step_photos(photos)
    assert len(show) == 8, f'Expected 8 displayed (fill limit), got {len(show)}'
    assert len(extra) == 2, f'Expected 2 extra, got {len(extra)}'

    b2 = HtmlPDFBuilder(Path('out.pdf'), p, mapg, config={'photos_before_page_break': 6, 'fill_page_with_photos': False})
    photos2 = [Path(f'photo_{i}.jpg') for i in range(10)]
    show2, extra2 = b2._split_step_photos(photos2)
    assert len(show2) == 10, f'Expected 10 displayed (no threshold), got {len(show2)}'
    assert len(extra2) == 0, f'Expected 0 extra, got {len(extra2)}'

    b3 = HtmlPDFBuilder(Path('out.pdf'), p, mapg, config={'photos_before_page_break': 6, 'min_photos_per_step': 4})
    photos3 = [Path(f'photo_{i}.jpg') for i in range(3)]
    show3, extra3 = b3._split_step_photos(photos3)
    assert len(show3) == 3, f'Expected 3 displayed, got {len(show3)}'
    assert len(extra3) == 0, f'Expected 0 extra, got {len(extra3)}'

    print('photo split tests passed')

run_photo_split_tests()
