import sys
from pathlib import Path
from datetime import datetime, timedelta
# make repo importable when running from scripts
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polarsteps_pdf_generator import TripParser

p = Path('BSPData/2026.01.14/trip/trainingslager2025_15246410')
tp = TripParser(p)
tp.load()
s, e = tp.get_trip_dates()
print('start dt', s, 'end dt', e)
print('start date', s.date(), 'end date', e.date())
print('span days:')
cur = s.date()
while cur <= e.date():
    print(cur)
    cur = cur + timedelta(days=1)
