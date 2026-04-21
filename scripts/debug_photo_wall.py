from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polarsteps_pdf_generator import TripParser, MapGenerator, HtmlPDFBuilder

trip_root = Path('BSPData/trip/hochlitten_15110468')
print('exists', trip_root.exists())
if not trip_root.exists():
    sys.exit(1)

try:
    tp = TripParser(trip_root)
    tp.load()
    mg = MapGenerator(width=800, height=600)
    builder = HtmlPDFBuilder(Path('test_output.html'), tp, mg, config={'renderer_mode':'html'})
    html = builder._build_html()
    with open('debug_photo_wall_test.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print('saved debug_photo_wall_test.html')
except Exception as e:
    import traceback
    traceback.print_exc()
