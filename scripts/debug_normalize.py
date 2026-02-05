#!/usr/bin/env python3
"""
Debug script for country normalization
"""
import sys
from pathlib import Path

# Add parent directory to path
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))

from polarsteps_pdf_generator import StatisticsGenerator

sg = StatisticsGenerator()

# Test country normalization with UAE
test_cases = [
    "United Arab Emirates",
    "AE",
    "UAE",
    "Dubai",
    "India",
    "Nepal"
]

print("Testing country normalization:")
for case in test_cases:
    result = sg._normalize_country(case)
    print(f"  '{case}' -> '{result}'")

# Test the specific problematic location
print("\nTesting problematic location:")
location_data = {
    'country_code': 'AE',
    'detail': 'United Arab Emirates',
    'full_detail': 'United Arab Emirates',
    'name': 'Dubai'
}

country, source, raw = sg._extract_country_from_location(location_data, debug=True)
print(f"Final result: {country} from {source} ({raw})")