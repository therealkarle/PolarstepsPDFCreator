from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from polarsteps_pdf_generator import find_trips, CombinedHtmlBuilder

bsp = SCRIPT_DIR / 'BSPData'
if not bsp.exists():
    print('BSPData not found', bsp)
    sys.exit(1)

trips = find_trips(bsp)
print('Trips found:', len(trips))
if not trips:
    sys.exit(1)

selected = trips[:3]
print('Using trips:', [t.name for t in selected])

out = SCRIPT_DIR / 'temp' / 'debug_combined.html'
out.parent.mkdir(parents=True, exist_ok=True)

builder = CombinedHtmlBuilder(out, selected, config={}, language_manager=None, cli_language_manager=None)
ok = builder.build()
print('Builder returned', ok)
print('Output exists:', out.exists(), 'size', out.stat().st_size if out.exists() else 'n/a')

html = out.read_text(encoding='utf-8')
print('Contains combined-map', 'id="combined-map"' in html)
print('Contains trip-list', 'trip-list' in html)
print('Contains var trips', 'var trips =' in html)

idx = html.find('var trips =')
if idx == -1:
    print('Cannot find trips JSON marker')
    sys.exit(1)
rest = html[idx+len('var trips ='):]
semi = rest.find(';')
json_text = rest[:semi]
print('JSON text starts:', json_text[:120])

import json
trip_data = json.loads(json_text)
coords = 0
for trip in trip_data:
    for step in trip['steps']:
        if step['lat'] is not None and step['lon'] is not None:
            coords += 1
print('Coordinate steps:', coords)
print('Trip data loaded:', len(trip_data))
for trip in trip_data:
    print('Trip', trip['name'], 'steps', len(trip['steps']), 'coords', sum(1 for s in trip['steps'] if s['lat'] is not None and s['lon'] is not None))
