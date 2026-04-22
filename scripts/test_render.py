from pathlib import Path
import sys
sys.path.append('h:/AppDevelopment/PolarstepsPDFCreator')
import polarsteps_pdf_generator as m
script_dir=Path('h:/AppDevelopment/PolarstepsPDFCreator')
trip=Path('BSPData/trip/02mrz-fasnacht-in-hochlitten_16535860')
cm=m.CacheManager(script_dir / 'cache' / 'rendered_trips_cache.json')
print('calling render_trip')
res=m.render_trip(trip, script_dir, {}, cm, lambda: False)
print('returned', res)
