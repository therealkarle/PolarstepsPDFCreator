#!/usr/bin/env python3
"""
Test forward-fill behavior: days without steps are assigned the previous day's country.
"""
import json
import shutil
import sys
from pathlib import Path

# Add repo root to path
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))

from polarsteps_pdf_generator import StatisticsGenerator

TRIP_DIR = Path('BSPData/test_forward_fill_trip')
if TRIP_DIR.exists():
    shutil.rmtree(TRIP_DIR)
TRIP_DIR.mkdir(parents=True, exist_ok=True)

trip_json = {
    "name": "Forward Fill Test",
    "start_date": "2025-06-01",
    "end_date": "2025-06-05",
    "steps": [
        {"data": {"start_date": "2025-06-01", "location": {"country_name": "Germany"}}},
        {"data": {"start_date": "2025-06-03", "location": {"country_name": "Italy"}}}
    ]
}
with open(TRIP_DIR / 'trip.json', 'w', encoding='utf-8') as f:
    json.dump(trip_json, f, indent=2, ensure_ascii=False)

sg = StatisticsGenerator()
agg = sg.compute_aggregate_stats([TRIP_DIR], verbose=True, debug_countries=True)
print(json.dumps(agg, indent=2, ensure_ascii=False))

# Assertions
assert agg['unmatched_days'] == 0, f"Expected no unmatched days, got {agg['unmatched_days']}"
assert agg['countries'].get('Germany', 0) == 2, f"Germany should have 2 days (1+2), got {agg['countries'].get('Germany')}"
assert agg['countries'].get('Italy', 0) == 3, f"Italy should have 3 days (3-5), got {agg['countries'].get('Italy')}"
print('Forward-fill test passed')

# cleanup
shutil.rmtree(TRIP_DIR)
