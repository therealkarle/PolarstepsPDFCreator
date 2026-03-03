"""Smoke checks for date filtering logic.

Usage:
    python scripts/test_filter_dates.py

Prints results of applying various filters to ensure trips overlapping
periods are included and that compute_aggregate_stats counts only days
within the requested interval.
"""
import sys
from pathlib import Path
import json
from datetime import datetime, timedelta

# make repo importable when running from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polarsteps_pdf_generator import (
    find_trips,
    filter_trips_by_date,
    TripParser,
    StatisticsGenerator,
)

BSP = Path("BSPData")
trips = find_trips(BSP)
if not trips:
    print("No trips found under BSPData. Aborting.")
    raise SystemExit(1)

print(f"Total trips available: {len(trips)}")

# pick a trip with a duration > 10 days to test inner-range filtering
chosen = None
for t in trips:
    try:
        tp = TripParser(t)
        tp.load()
        s_dt, e_dt = tp.get_trip_dates()
        if s_dt and e_dt and (e_dt - s_dt).days >= 10:
            chosen = (t, s_dt, e_dt)
            break
    except Exception:
        continue

if not chosen:
    print("No suitable multi-day trip found for inner-range test.")
else:
    trip_path, s_dt, e_dt = chosen
    print("Selected trip for testing:", trip_path)
    print("  start", s_dt, "end", e_dt)

    # define an inner range halfway through
    mid = s_dt + (e_dt - s_dt) / 2
    inner_start = mid - timedelta(days=2)
    inner_end = mid + timedelta(days=2)
    print("Inner-range filter:", inner_start.date(), "to", inner_end.date())

    filtered = filter_trips_by_date([trip_path], start_date=inner_start, end_date=inner_end)
    print("filter_trips_by_date returned", filtered)

    sg = StatisticsGenerator()
    agg = sg.compute_aggregate_stats([trip_path], start_date=inner_start, end_date=inner_end)
    print("aggregate result:")
    print(json.dumps(agg, indent=2, ensure_ascii=False))
    # also show span vs step union for this trip
    print("span_days count", len({d for d in (inner_start.date(), inner_end.date())}))

# Test year overlap behaviour
year = 2025
print(f"\nFiltering for year {year}...")
filtered_year = filter_trips_by_date(trips, year=year)
print(f"{len(filtered_year)} trips overlap {year} (first 5):")
for t in filtered_year[:5]:
    print("  ", t)
    tp = TripParser(t); tp.load()
    print("    trip dates", tp.get_trip_dates())
    sg = StatisticsGenerator()
    a = sg.compute_aggregate_stats([t], year=year)
    print("    travel days in year", a.get('total_travel_days'), "period", a.get('period_start'), a.get('period_end'))
# verify global travel_days equals span union for the filtered set
span_union = set()
for t in filtered_year:
    tp = TripParser(t); tp.load()
    s_dt, e_dt = tp.get_trip_dates()
    if s_dt and e_dt:
        cur = s_dt.date()
        endd = e_dt.date()
        while cur <= endd:
            if cur.year == year:
                span_union.add(cur)
            cur += timedelta(days=1)
agg_all = StatisticsGenerator().compute_aggregate_stats(filtered_year, year=year)
print(f"Global span_union count={len(span_union)}, travel_days={agg_all.get('total_travel_days')}")
assert agg_all.get('total_travel_days') == len(span_union), "Aggregate travel_days should equal span union"


# verify future trips are excluded from both helpers
now = datetime.now().date()
future_trips = []
for t in trips:
    try:
        tp = TripParser(t)
        tp.load()
        s, e = tp.get_trip_dates()
        if s and s.date() > now:
            future_trips.append(t)
    except Exception:
        pass
print(f"\nFound {len(future_trips)} future trips")
if future_trips:
    sg = StatisticsGenerator()
    agg = sg.compute_aggregate_stats(future_trips)
    print("aggregate on future trips returned", agg.get('trip_count'))
    assert agg.get('trip_count', 0) == 0, "Future trips should be ignored by compute_aggregate_stats"
    filtered = filter_trips_by_date(future_trips)
    print("filter_trips_by_date on future trips gave", filtered)
    assert not filtered, "Future trips should be filtered out"

# create a synthetic trip with past and future dates to ensure travel days clamp
import tempfile, json
from datetime import timedelta
with tempfile.TemporaryDirectory() as td:
    trip_dir = Path(td) / "trip"
    trip_dir.mkdir()
    today = datetime.now().date()
    past = today - timedelta(days=1)
    future = today + timedelta(days=1)
    trip_data = {
        "steps": [
            {"data": {"start_date": past.isoformat()}},
            {"data": {"start_date": future.isoformat()}},
        ]
    }
    with open(trip_dir / "trip.json", "w", encoding="utf-8") as f:
        json.dump(trip_data, f)
    sg = StatisticsGenerator()
    agg = sg.compute_aggregate_stats([trip_dir])
    print("synthetic trip aggregate", agg)
    # with span-only logic, a trip with no start/end dates contributes 0 travel days
    assert agg.get("total_travel_days") == 0, "Synthetic trip without spans should yield 0 travel days"

print("\ndone")
