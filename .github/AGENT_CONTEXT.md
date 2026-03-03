# Agent Context — PolarstepsPDFCreator

Purpose: Give AI coding agents a compact, actionable briefing about repository structure, goals, and specific DOs / AVOIDs so they can make safe, productive changes quickly.

## Quick structure
- `polarsteps_pdf_generator.py` — single-file application and entry point. Most business logic lives here (TripParser, MapGenerator, PDFBuilder, CacheManager, CLI loop).
- `config.json` — runtime configuration (fonts, map zooms, photo limits).
- `BSPData/` — input data layout: `BSPData/{date}/trip/{trip-slug}_{id}/...` (expects `trip.json`, optional step folders with `photos/` and `videos/`).
- `TripPdfs/` — output target for generated PDFs.
- `.emoji_cache/`, `.map_marker_cache/` — on-disk caches used at runtime.
- `rendered_trips_cache.json` — canonical cache of rendered trips (important for CLI behavior).
- `scripts/test_render.py` — simple smoke-test script to run `render_trip()` non-interactively.

## Primary goals for agent changes
- Keep changes small and focused (one behavior per PR).
- Preserve user-facing workflows: interactive commands, `render` flags, cache semantics, and output filenames/locations.
- Maintain resilience to partial/dirty exports (TripParser intentionally uses safe defaults and try/except guards).
- Prefer tests or small smoke scripts for behavior changes (add under `scripts/` or `tests/`).

## DO (practical rules) ✅
- Run `python scripts/test_render.py` for end-to-end smoke checks where appropriate.
- When touching CLI flags, update both `parse_render_command()` and `print_command_help()`.
- When changing layout or font behavior, update `config.json` defaults and mention font fallbacks and OS paths.
- Respect existing cache files and keys. Use `CacheManager` helper rather than manipulating caches directly.
- Add a short reproduction / smoke-test in the PR description demonstrating the change.

## AVOID (important constraints) ⚠️
- Do not change the expected BSPData directory structure or trip discovery heuristics without a very good reason and tests.
- Avoid forcing network access in unit tests (e.g., Twemoji or tile fetching) — mock or use cached files instead.
- Don’t hardcode absolute paths or Windows-only font paths in changes; use config keys or platform checks.
- Don’t remove fallback behavior (e.g., staticmap optional import, emoji fallbacks, font fallback to Helvetica).

## Implementation tips & notable implementation details 🔧
- Maps: `MapGenerator._create_map()` uses `ESRI_SATELLITE_URL` — change here to swap tile providers.
- Thumbnails: marker thumbnails are cached in `.map_marker_cache` and use a SHA1 cache key that includes ring color and mtime — keep key semantics stable.
- Emoji: Twemoji PNGs are fetched to `.emoji_cache` and used both for inline HTML and rendered images.
- PDF layout: `PDFBuilder` approximates heights using `_flowables_height()` and `_remaining_page_space()` — changing these affects pagination and page breaks.
- Selection parsing: `parse_selection()` and `_resolve_index_token()` implement CLI selection logic — add tests here when modifying behavior.

## Testing & manual checks
- Use `python polarsteps_pdf_generator.py /path/to/PolarstepsData` for interactive validation.
- Use `python polarsteps_pdf_generator.py --clear-cache` to clear state before tests.
- For CI-friendly tests, write small functions and test scripts that avoid fetching external assets.

## PR checklist for agents
- Small, focused change with a short smoke-test script or steps in the PR description.
- Update `README.md` and `.github/copilot-instructions.md` if user-visible behavior or config defaults change.
- Keep backward compatibility in mind and add tests when modifying parsing or discovery logic.

---
If you'd like, I can also add a minimal test template for `parse_selection()` and a PR template for this repository.