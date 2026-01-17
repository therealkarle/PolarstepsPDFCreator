from pathlib import Path
import sys
sys.path.append('f:/AppDevelopment/PolarstepsPDFCreator')
import polarsteps_pdf_generator as m
script_dir=Path('f:/AppDevelopment/PolarstepsPDFCreator')
trip=Path('BSPData/2026.01.14/trip/hochlitten-weihnachten_23335465')
cm=m.CacheManager(script_dir / 'rendered_trips_cache.json')
print('calling render_trip')
res=m.render_trip(trip, script_dir, {}, cm, lambda: False)
print('returned', res)
