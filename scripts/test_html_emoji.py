import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polarsteps_pdf_generator import HtmlPDFBuilder, TripParser, MapGenerator
p=TripParser(Path('BSPData/2026.01.14/trip/hochlitten-weihnachten_23335465'))
p.load()
mapg=MapGenerator()
b=HtmlPDFBuilder(Path('out.pdf'), p, mapg)
for s in p.steps:
    d=s.get('data',{})
    desc=d.get('description','')
    if '\u26f7' in desc or '⛷' in desc:
        print('found desc snippet:')
        print(desc[:200])
        print('html snippet:')
        print(b._html_escape_with_emoji(desc)[:400])
        break
