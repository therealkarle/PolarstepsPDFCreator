from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from polarsteps_pdf_generator import CombinedHtmlBuilder, find_trips

SCRIPT_DIR = Path(__file__).resolve().parent.parent
BSP_DIR = SCRIPT_DIR / 'BSPData'

if not BSP_DIR.exists():
    print('BSPData folder not found, cannot run combined HTML smoke test.')
    sys.exit(1)

trip_paths = find_trips(BSP_DIR)
if not trip_paths:
    print('No trips found in BSPData.')
    sys.exit(1)

output_path = SCRIPT_DIR / 'temp' / 'combined_trips_test.html'
output_path.parent.mkdir(parents=True, exist_ok=True)

# Use the first 3 trips for a quick smoke test.
selected_trips = trip_paths[:3]
print(f'Building combined HTML for {len(selected_trips)} trips...')

builder = CombinedHtmlBuilder(output_path, selected_trips, config={})
result = builder.build()
if not result:
    print('Combined HTML build failed.')
    sys.exit(1)

text = output_path.read_text(encoding='utf-8')
assert 'Combined Trip Overview' in text, 'Expected overview title not found in generated HTML.'
assert 'combined-map' in text, 'Expected map container not found in generated HTML.'
assert 'function openMediaModal' in text or 'id="media-modal"' in text, 'MEDIA MODAL NOT FOUND in combined HTML'
print('Combined HTML smoke test passed:', output_path)
