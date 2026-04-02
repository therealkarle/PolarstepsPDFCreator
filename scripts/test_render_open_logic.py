from pathlib import Path
import sys

# Add repository root to import path
script_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(script_dir))

import polarsteps_pdf_generator as m

print('Testing open-after logic...')

h = m.HtmlPDFBuilder(Path('output.pdf'), None, None, {'renderer_mode': 'html', 'open_pdf_after_render': True})
assert h._should_open_pdf() is False, 'HTML-only render mode should suppress PDF auto-open'

h2 = m.HtmlPDFBuilder(Path('output.pdf'), None, None, {'renderer_mode': 'pdf', 'open_pdf_after_render': True})
assert h2._should_open_pdf() is True, 'PDF mode should allow PDF auto-open if configured'

h3 = m.HtmlPDFBuilder(Path('output.pdf'), None, None, {'renderer_mode': 'both', 'open_pdf_after_render': False})
assert h3._should_open_pdf() is False, 'Explicit false open_pdf_after_render still honored in both mode'

assert m._should_open_html({'open_html_after_render': True}) is True
assert m._should_open_html({'open_html_after_render': False}) is False

print('PASS')
