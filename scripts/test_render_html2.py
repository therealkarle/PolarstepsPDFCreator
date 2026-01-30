from pathlib import Path
import sys, traceback
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import polarsteps_pdf_generator as m
script_dir=Path(__file__).resolve().parents[1]
trip=Path('BSPData/2026.01.14/trip/hochlitten-weihnachten_23335465')
cm=m.CacheManager(script_dir / 'cache' / 'rendered_trips_cache.json')
print('calling render_trip html')
try:
    res=m.render_trip(trip, script_dir, {'renderer':'html'}, cm, lambda: False)
    print('returned', res)
except Exception as e:
    traceback.print_exc()
    print('failed with', e)
