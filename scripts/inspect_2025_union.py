from pathlib import Path
from datetime import date, timedelta
import sys
sys.path.insert(0, str(Path('.').resolve()))
from polarsteps_pdf_generator import find_trips, TripParser, StatisticsGenerator

YEAR = 2025
ystart = date(YEAR, 1, 1)
yend = date(YEAR, 12, 31)
DATE_KEYS = ("start_time", "startDate", "start_date", "time", "date", "timestamp")

sg = StatisticsGenerator()
all_trips = find_trips(Path('BSPData'))
span_union = set()
step_union = set()
rows = []
for trip_path in all_trips:
    tp = TripParser(trip_path)
    tp.load()
    s_dt, e_dt = tp.get_trip_dates()
    span_days = set()
    if s_dt and e_dt:
        s = s_dt.date(); e = e_dt.date()
        a = max(s, ystart)
        b = min(e, yend)
        if a <= b:
            d = a
            while d <= b:
                span_days.add(d)
                d += timedelta(days=1)
    step_days = set()
    for step in (tp.steps or []):
        data = step.get('data', {}) or {}
        for k in DATE_KEYS:
            v = data.get(k)
            if not v:
                continue
            dt = sg._parse_date(v)
            if dt and ystart <= dt.date() <= yend:
                step_days.add(dt.date())
            if dt:
                break
    if span_days or step_days:
        span_union |= span_days
        step_union |= step_days
        rows.append((str(trip_path), len(span_days), len(step_days), sorted(span_days - step_days)))

print('span_union count', len(span_union))
print('step_union count', len(step_union))
print('missing=', len(span_union - step_union))
print('missing dates', sorted(span_union - step_union))
print('\nPer-trip (path, span_count, step_count, missing_dates):')
for r in rows:
    if r[1] != r[2]:
        print(r)
