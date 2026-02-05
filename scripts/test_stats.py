"""Quick smoke test for StatisticsGenerator (CLI-like)

Usage:
    python scripts/test_stats.py

Prints summary and writes a small stats.json + map.png in repo root.
"""
from pathlib import Path
import json

from polarsteps_pdf_generator import StatisticsGenerator, MapGenerator, find_trips

BSP = Path('BSPData')
trips = find_trips(BSP)
if not trips:
    print('No trips found under BSPData. Aborting.')
    raise SystemExit(1)

# take first 10 trips for smoke
sel = trips[:10]
mg = MapGenerator()
sg = StatisticsGenerator(map_generator=mg)
agg = sg.compute_aggregate_stats(sel)
print('Summary:')
print(json.dumps(agg, indent=2, ensure_ascii=False))
# export json and map
out_json = Path('stats_smoke.json')
ok = sg.export_stats_json(agg, out_json)
print('JSON write', ok, out_json)
mp = sg.generate_overview_map(sel)
if mp:
    with open('stats_smoke_map.png', 'wb') as f:
        f.write(mp)
    print('Wrote stats_smoke_map.png')
else:
    print('No map generated')