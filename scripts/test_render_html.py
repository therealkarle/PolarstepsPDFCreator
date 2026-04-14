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
    if res:
        html_path = script_dir / 'TripPdfs' / 'Hochlitten Fasnacht .html'
        if html_path.exists():
            html_text = html_path.read_text(encoding='utf-8')
            assert 'map-resize-handle' in html_text, 'RESIZE HANDLE NOT FOUND in HTML'
            assert 'function openMediaModal' in html_text or 'id="media-modal"' in html_text, 'MEDIA MODAL NOT FOUND in HTML'
            print('Resize handle and fullscreen media modal confirmed in html output.')
        else:
            print('WARNING: expected html file not found:', html_path)
except Exception as e:
    import traceback
    traceback.print_exc()
