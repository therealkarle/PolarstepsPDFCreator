from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import polarsteps_pdf_generator as m
script_dir=Path(__file__).resolve().parents[1]
trip=Path('BSPData/trip/hochlitten_15110468')
cm=m.CacheManager(script_dir / 'cache' / 'rendered_trips_cache.json')
print('calling render_trip html')
try:
    res = m.render_trip(trip, script_dir, {'renderer_mode':'html'}, cm, lambda: False)
    print('returned', res)
except Exception as e:
    import traceback
    traceback.print_exc()
