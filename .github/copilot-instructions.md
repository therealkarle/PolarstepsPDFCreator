# Copilot / Agent Instructions — PolarstepsPDFCreator

Purpose: Short, actionable guidance to quickly make safe, useful changes in this repository.

## Big picture (what this repo does)
- Single-script CLI tool: `polarsteps_pdf_generator.py` generates PDF travel journals from a local Polarsteps export (`BSPData/` folder).
- Major components:
  - `TripParser` — robustly reads `trip.json`, `all_steps` or discovers step folders and attaches photos/videos.
  - `MapGenerator` — creates overview and per-step static maps using ESRI World Imagery (`ESRI_SATELLITE_URL`) and the `staticmap` package.
  - `PDFBuilder` — builds the PDF with ReportLab, including emoji handling, photo grids and layout heuristics.
  - `CacheManager` — tracks rendered trips in `rendered_trips_cache.json` (schema: `{ "rendered_trips": ["<path>"] }`).

## Important files & locations
- Entrypoint: `polarsteps_pdf_generator.py` (contains most logic; prefer small, focused edits here)
- Config: `config.json` — tune fonts, zooms, photo layout (`step_title_font_size`, `emoji_font_path`, etc.)
- Data: `BSPData/{date}/trip/{trip-slug}_{id}/` — expected folder structure (see README)
- Outputs: `TripPdfs/` (default) and caches: `.emoji_cache/`, `.map_marker_cache/`, `rendered_trips_cache.json`
- Tests / quick-run: `scripts/test_render.py` demonstrates direct invocation of `render_trip()`

## External deps & runtime considerations
- Declared in `requirements.txt`: `reportlab`, `Pillow`, `staticmap`, `requests` (Python 3.8+ expected).
- `staticmap` is optional at import-time: Map functionality raises a clear RuntimeError if missing — ensure tests or changes that depend on it import/handle that appropriately.
- Fonts: the code checks common OS font paths and config overrides (`text_font_path`, `emoji_font_path`). Running CI on different OSs may need font adjustments.

## CLI & behavior conventions (examples agents should follow)
- Run locally: `python polarsteps_pdf_generator.py [path/to/BSPData]` or `python polarsteps_pdf_generator.py --clear-cache`
- Interactive render command syntax (implementations to reference):
  - `r -a` => render all trips (including already rendered)
  - `r -ur -y 2025` => unrendered trips from year 2025
  - Selection parsing supports: `1`, `1;4` (range), `1,3,5` (list), `l` or `last` (last), `l-1` (second-to-last)
  - See functions: `parse_selection()` and `parse_render_command()` — modify parsing there when adding flags

## Implementation patterns & conventions
- Be conservative: functions favor resilience to missing fields, so follow the same approach (safe defaults, try/except around file reads).
- Photo and step discovery: `TripParser.load()` prefers explicit `steps` or `all_steps` in `trip.json`, then fallback to directory heuristics — add unit tests when changing discovery logic.
- PDF layout: `PDFBuilder` uses ReportLab flowables and estimates height with `_flowables_height()` and `_remaining_page_space()`; preserve these heuristics when adjusting layout.
- Emoji handling: Twemoji images are fetched and cached; inline emoji rendering has fallbacks (text-only) — preserve copyable text behavior when modifying emoji logic.
- Caches: marker thumbnails and emoji PNGs are written to repo-side cache directories; maintain cache key behavior when changing thumbnail generation.

## Testing & manual checks
- Quick smoke: `python scripts/test_render.py` (runs `render_trip()` for a specific trip path) — useful for end-to-end checks that don't require interactive prompt flows.
- No test framework is set up; prefer adding small standalone test scripts or simple pytest tests under `scripts/` or `tests/` if you introduce behavior changes.

## Where to change things for common tasks
- Change tile source or tile handling: update `ESRI_SATELLITE_URL` at top of `polarsteps_pdf_generator.py`.
- Add CLI flags or alter parsing: update `parse_render_command()` and `print_command_help()` together.
- Change layout constants (fonts, sizes, photo limits): `config.json` keys or top of `PDFBuilder` (`STEP_*` constants).

## PR guidance for agents
- Keep changes small and focused (one behavior per PR).
- Include a short smoke-test script or instructions in PR description that reproduce the behavior change (e.g., run `scripts/test_render.py` or run `python polarsteps_pdf_generator.py --clear-cache`).
- Update `README.md` and `config.json` defaults if you add user-facing flags or configuration keys.
- Preserve backward compatibility for user workflows (existing `render` flags, cache semantics, and output paths).

---
If any part of the codebase seems ambiguous or you want a specific example (e.g., unit test template for `parse_selection()`), tell me which area to expand and I will add a short example snippet or a test file. ✅