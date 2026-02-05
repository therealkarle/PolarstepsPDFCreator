#!/usr/bin/env python3
"""
Debug script for country normalization substring issue
"""
import sys
from pathlib import Path

# Add parent directory to path
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))

from polarsteps_pdf_generator import StatisticsGenerator

sg = StatisticsGenerator()

test_text = "United Arab Emirates"
test_token_low = test_text.lower()

print(f"Testing: '{test_text}'")
print(f"Token low: '{test_token_low}'")

# Direct alias check
print(f"Direct alias check: {test_token_low in sg._COUNTRY_ALIASES}")

# Substring check
print("Substring matches:")
for k, v in sg._COUNTRY_ALIASES.items():
    if k in test_token_low:
        print(f"  '{k}' -> '{v}' (found in '{test_token_low}')")

# Full normalization
result = sg._normalize_country(test_text)
print(f"\nFinal result: '{result}'")