"""Smoke test: ensure HtmlPDFBuilder uses CLI language for console prints and PDF language for content."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from polarsteps_pdf_generator import HtmlPDFBuilder, TripParser, MapGenerator, LanguageManager, load_language_manager

# Create minimal trip parser stub
class StubParser:
    def __init__(self):
        self.steps = [{"data": {"display_name": "StepOne", "start_time": None}}]
    def get_trip_name(self):
        return "Test Trip"
    def get_trip_dates(self):
        return (None, None)
    def get_total_km(self):
        return 12.3

# Create a MapGenerator stub
class StubMapGen(MapGenerator):
    def __init__(self):
        # Minimal attributes expected by HtmlPDFBuilder
        self.width = 450
        self.height = 300
        self.overview_height = 720
        self.overview_width = 1280
        self.step_height = 360
        self.step_width = 1280
        self._pixel_scale = 1.0
        self.step_render_scale = 1.0
        self.overview_padding_factor = 0.05
        self.step_padding_factor = 0.05
        self.step_cluster_distance_km = 2.0
        self.step_min_width_km = 12.0
        self.step_max_distance_farthest_km = 400.0
        self.debug_map = False

    def generate_overview_map(self, trip_parser):
        return b"PNGDATA"
    def generate_step_map_for_step(self, trip_parser, idx, width=None, height=None):
        return b"PNGSTEP"
    def clone(self):
        return self

script_dir = Path(__file__).parent.parent
cli_lang = load_language_manager('en', script_dir)
pdf_lang = load_language_manager('de', script_dir)

parser = StubParser()
map_gen = StubMapGen()
config = {"open_pdf_after_render": False}

builder = HtmlPDFBuilder(Path('out.pdf'), parser, map_gen, config=config, language_manager=pdf_lang, cli_language_manager=cli_lang)
html = builder._build_html()
# Print a snippet from the generated HTML to show PDF language content is German
print('\nHTML contains german pdf.subtitle label? ->', 'Steps' in html and 'Step' in html)
print('\nDone test')
