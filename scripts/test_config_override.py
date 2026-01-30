import sys
from pathlib import Path

sys.path.append('.')
from polarsteps_pdf_generator import parse_render_command, CacheManager

cm = CacheManager(Path('cache') / 'rendered_trips_cache.json')
trips = [Path(f"trip_{i}") for i in range(1, 101)]

cmd = 'r 67 -config(map_style = "street", max_photos_per_step = 4)'
res = parse_render_command(cmd, trips, cm)
print('cmd:', cmd)
print('valid:', res['valid'])
print('error:', res['error'])
print('config_overrides:', res.get('config_overrides'))
print('selection:', res.get('selection'))

# Simulate merging and normalization (what render_trip does)
merged = dict()
merged.update({'map_style': res.get('config_overrides', {}).get('map_style')})
ms = str(merged.get('map_style', 'hybrid')).lower().strip()
if ms in ('street','streets'):
    ms = 'road'
print('\nNormalized map style (after merge):', ms)

cmd2 = 'r -config(map_style="satellite", max_photos_per_step=3) 2'
res2 = parse_render_command(cmd2, trips, cm)
print('\ncmd2:', cmd2)
print('valid:', res2['valid'])
print('error:', res2['error'])
print('config_overrides:', res2.get('config_overrides'))
print('selection:', res2.get('selection'))
