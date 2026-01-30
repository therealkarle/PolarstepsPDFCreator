from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polarsteps_pdf_generator import HtmlPDFBuilder, TripParser, MapGenerator
p=TripParser(Path('BSPData/2026.01.14/trip/hochlitten-weihnachten_23335465'))
p.load()
mapg=MapGenerator()
b=HtmlPDFBuilder(Path('out.pdf'), p, mapg)
print('building html...')
try:
    h=b._build_html()
    print('html length', len(h))
    print(h[:1200])
except Exception as e:
    import traceback; traceback.print_exc()
