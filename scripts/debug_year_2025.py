#!/usr/bin/env python3
"""Diagnostics for year 2025

- Lists trips overlapping 2025
- For each trip shows steps that have dates in 2025 but no country (or suspicious raw tokens)
- Prints aggregate JSON for year=2025 (verbose + debug_countries)
"""
import sys
from pathlib import Path
import json
import re

# make repo importable when running from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polarsteps_pdf_generator import find_trips, TripParser, StatisticsGenerator

YEAR = 2025
DATE_KEYS = ("start_time", "startDate", "start_date", "time", "date", "timestamp")
SUSPICIOUS_RE = re.compile(r'^[A-Za-z]{1,3}$')  # 1-3 letter raw tokens considered suspicious


def step_dates_from_step(step, sg):
    dates = set()
    data = step.get('data', {}) or {}
    for k in DATE_KEYS:
        if k in data and data[k]:
            dt = sg._parse_date(data[k])
            if dt:
                dates.add(dt.date())
    return dates


def trip_overlaps_year(tp, year: int) -> bool:
    s_dt, e_dt = tp.get_trip_dates()
    if not s_dt and not e_dt:
        return False
    # if start or end directly in year
    if s_dt and s_dt.year == year:
        return True
    if e_dt and e_dt.year == year:
        return True
    # if both dates exist, check for any overlap with the year interval
    try:
        if s_dt and e_dt:
            ystart = __import__('datetime').date(year, 1, 1)
            yend = __import__('datetime').date(year, 12, 31)
            s = s_dt.date()
            e = e_dt.date()
            return (s <= yend and e >= ystart)
    except Exception:
        pass
    return False


def main():
    bsp = Path('BSPData')
    trips = find_trips(bsp)
    sg = StatisticsGenerator()

    trips_2025 = []
    for p in trips:
        try:
            tp = TripParser(p)
            tp.load()
        except Exception:
            continue
        if trip_overlaps_year(tp, YEAR):
            trips_2025.append(p)

    print(f"Found {len(trips_2025)} trip(s) overlapping {YEAR}\n")
    overall_unmatched = 0
    for p in sorted(trips_2025):
        tp = TripParser(p)
        tp.load()
        name = tp.get_trip_name()
        total_steps = len(tp.steps)
        travel_days = tp.get_trip_dates()[1].date() - tp.get_trip_dates()[0].date() if tp.get_trip_dates()[0] and tp.get_trip_dates()[1] else None
        per_trip_unmatched_dates = set()
        suspicious_tokens = set()

        for step in tp.steps:
            dates = step_dates_from_step(step, sg)
            # filter only dates inside YEAR
            dates = set(d for d in dates if d.year == YEAR)
            if not dates:
                continue
            loc = (step.get('data', {}) or {}).get('location') if isinstance((step.get('data', {}) or {}).get('location'), dict) else None
            country, source, raw = sg._extract_country_from_location(loc) if loc else ('', 'none', '')
            raw_str = (raw or '').strip()
            # if no normalized country OR raw looks like a suspicious short token, mark as unmatched
            if not country or (raw_str and SUSPICIOUS_RE.fullmatch(raw_str)):
                per_trip_unmatched_dates.update(dates)
                if raw_str:
                    suspicious_tokens.add(raw_str)
                else:
                    suspicious_tokens.add('<EMPTY>')

        if per_trip_unmatched_dates:
            overall_unmatched += len(per_trip_unmatched_dates)
            print(f"Trip: {p}")
            print(f"  Name: {name}")
            print(f"  Steps: {total_steps}")
            print(f"  Unmatched 2025 step dates ({len(per_trip_unmatched_dates)}): {sorted([d.isoformat() for d in per_trip_unmatched_dates])}")
            print(f"  Suspicious raw tokens: {sorted(suspicious_tokens)}\n")

    if overall_unmatched == 0:
        print("No per-step unmatched dates found for 2025.\n")

    # aggregate stats for 2025 (verbose + debug)
    print("Computing aggregate stats for 2025 (verbose + debug_countries)...\n")
    agg = sg.compute_aggregate_stats(trips_2025, year=YEAR, verbose=True, debug_countries=True)
    print(json.dumps(agg, indent=2, ensure_ascii=False))
    print(f"Computed period days: {agg.get('period_total_days')} (should be 365 for {YEAR})")


if __name__ == '__main__':
    main()
