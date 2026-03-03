# temporary debug script
from pathlib import Path
from polarsteps_pdf_generator import TripParser
from datetime import datetime

now=datetime.now().date()
print('today', now)
for t in Path('BSPData/2026.01.14/trip').iterdir():
    if t.is_dir():
        tp=TripParser(t)
        tp.load()
        s,e = tp.get_trip_dates()
        if s and s.date()>now:
            print('trip', t)
            print(' start', s, 'end', e)
            for step in tp.steps:
                data=step.get('data',{}) or {}
                for key in('start_time','startDate','start_date','time','date','timestamp'):
                    if key in data and data[key]:
                        print(' step', key, data[key])
                        break
