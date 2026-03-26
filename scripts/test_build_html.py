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
    b1 = HtmlPDFBuilder(Path('out.pdf'), p, mapg, config={'max_photos_per_step': 6, 'min_photos_per_step': 4, 'photo_wall_fill_limit': 9})
    photos = [Path(f'photo_{i}.jpg') for i in range(10)]
    show, extra = b1._split_step_photos(photos)
    assert len(show) == 9, f'Expected 9 displayed, got {len(show)}'
    assert len(extra) == 1, f'Expected 1 extra, got {len(extra)}'

    b2 = HtmlPDFBuilder(Path('out.pdf'), p, mapg, config={'max_photos_per_step': 6, 'min_photos_per_step': 4, 'photo_wall_fill_limit': 9})
    photos2 = [Path(f'photo_{i}.jpg') for i in range(3)]
    show2, extra2 = b2._split_step_photos(photos2)
    assert len(show2) == 3, f'Expected 3 displayed, got {len(show2)}'
    assert len(extra2) == 0, f'Expected 0 extra, got {len(extra2)}'

    print('photo split tests passed')

run_photo_split_tests()
