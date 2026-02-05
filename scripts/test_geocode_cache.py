#!/usr/bin/env python3
"""Test for reverse geocode batching and cache persistence"""
import time
from pathlib import Path
import json
import sys
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))

from polarsteps_pdf_generator import StatisticsGenerator

sg = StatisticsGenerator()
start = time.time()
# run on a moderate subset or all trips
from polarsteps_pdf_generator import find_trips
trips = find_trips(Path('BSPData'))
if not trips:
    print('No trips found under BSPData. Aborting.')
    raise SystemExit(1)
sel = [p for p in trips if p.exists()][:30]
print(f'Running geocode-enabled stats on {len(sel)} trips...')
agg = sg.compute_aggregate_stats(sel, verbose=False, debug_countries=True)
end = time.time()
print('\nResults:')
print(f"Total travel days: {agg.get('total_travel_days')}")
print(f"Total country days: {agg.get('total_country_days')}")
print(f"Unmatched days: {agg.get('unmatched_days')}")
print(f"Visited countries: {agg.get('visited_countries_count')}")
print(f"Visited continents: {agg.get('visited_continents_count')}")
print(f"Runtime: {round(end-start,2)}s")
# cache info
cache_file = Path('cache/reverse_geocode_cache.json')
if cache_file.exists():
    try:
        d = json.loads(cache_file.read_text(encoding='utf-8'))
        print(f"Cache entries: {len(d)}")
    except Exception:
        print('Cache exists but could not read it')
else:
    print('No cache file found')
