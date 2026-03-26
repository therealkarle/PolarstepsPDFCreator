"""Smoke-check for recursive find_trips behavior.

Usage:
    python scripts/test_find_trips_recursive.py
"""

import sys
from pathlib import Path
import tempfile
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polarsteps_pdf_generator import find_trips

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "BSPData"
    # two path formats
    old_trip = root / "2026.01.01" / "trip" / "my-trip_123"
    old_trip.mkdir(parents=True, exist_ok=True)
    with open(old_trip / "trip.json", "w", encoding="utf-8") as f:
        json.dump({"start_date": 1672531200}, f)

    nested_trip = root / "foo" / "bar" / "baz" / "other-trip_456"
    nested_trip.mkdir(parents=True, exist_ok=True)
    with open(nested_trip / "trip.json", "w", encoding="utf-8") as f:
        json.dump({"start_date": 1672617600}, f)

    found = find_trips(root)
    print("Found trips:", found)
    assert any("my-trip_123" in str(p) for p in found), "old style trip not found"
    assert any("other-trip_456" in str(p) for p in found), "nested trip not found"

print("OK")
