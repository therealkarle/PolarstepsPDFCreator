#!/usr/bin/env python3
"""
Test statistics for just the Nepal-India trip
"""
import sys
from pathlib import Path

# Add parent directory to path
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))

from polarsteps_pdf_generator import StatisticsGenerator

# Path to the Nepal-India trip
nepal_trip_path = Path("BSPData/2026.01.14/trip/nepal-indien-reise_12290261")

sg = StatisticsGenerator()

# Test just this one trip
agg = sg.compute_aggregate_stats([nepal_trip_path], debug_countries=True)

print("\nResults:")
print(f"Total travel days: {agg['total_travel_days']}")
print(f"Total country days: {agg['total_country_days']}")
print(f"Unmatched days: {agg['unmatched_days']}")
print("\nCountries:")
for country, days in sorted(agg['countries'].items(), key=lambda x: x[1], reverse=True):
    print(f"  {country}: {days} days")