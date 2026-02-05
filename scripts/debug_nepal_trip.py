#!/usr/bin/env python3
"""
Debug script for Nepal-India trip country detection
"""
import sys
from pathlib import Path

# Add parent directory to path
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))

from polarsteps_pdf_generator import TripParser, StatisticsGenerator

# Path to the Nepal-India trip
nepal_trip_path = Path("BSPData/2026.01.14/trip/nepal-indien-reise_12290261")

print(f"Debugging trip: {nepal_trip_path}")

# Parse the trip
tp = TripParser(nepal_trip_path)
tp.load()

# Create StatisticsGenerator
sg = StatisticsGenerator()

# Debug the specific trip
trip_stats = sg.compute_trip_stats(tp)
print(f"\nTrip Stats:")
print(f"Name: {trip_stats['name']}")
print(f"Steps: {trip_stats['steps']}")
print(f"Travel days: {trip_stats['travel_days']}")
print(f"Countries: {trip_stats['countries']}")

print(f"\nDetailed step analysis:")
for i, step in enumerate(tp.steps):
    data = step.get('data', {}) or {}
    print(f"\nStep {i+1}: {step.get('name', 'unnamed')}")
    
    # Check location
    loc = data.get('location', {})
    if loc:
        print(f"  Location fields: {list(loc.keys())}")
        for field in ['country', 'country_name', 'countryCode', 'country_code', 'detail', 'full_detail', 'name', 'display_name']:
            val = loc.get(field)
            if val:
                print(f"    {field}: {val}")
        country, source, raw = sg._extract_country_from_location(loc, debug=True)
        print(f"  Final country: {country} (from {source}: {raw})")
    else:
        print(f"  No location data")
    
    # Check dates
    for key in ('start_time', 'startDate', 'start_date', 'time', 'date', 'timestamp'):
        if key in data and data[key]:
            dt = sg._parse_date(data[key])
            if dt:
                print(f"  Date: {dt.date()} (from {key})")
                break
    else:
        print(f"  No date found")