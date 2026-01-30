# Polarsteps PDF Generator

Generate beautiful PDF travel journals from your downloaded Polarsteps data.

## Features

- 🗺️ **Overview Map**: Satellite map with your complete route and step markers
- 📍 **Step Maps**: Individual location maps for each step (ESRI World Imagery)
- 📸 **Photo Grids**: Adaptive photo layout (1-6 photos per step)
- 📹 **Video Links**: Compact link collection for local video files
- 🌡️ **Weather Info**: Temperature and conditions for each step
- 📝 **Descriptions**: Full travel journal text with formatting
- 💾 **Cache System**: Tracks rendered trips and provides batch rendering
- 🔄 **Batch Processing**: Render multiple trips with date filters

## Installation

1. Make sure you have Python 3.8+ installed
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Start the program:

```bash
python polarsteps_pdf_generator.py
```

Or with a custom BSPData folder:

```bash
python polarsteps_pdf_generator.py /path/to/BSPData
```

### Available Commands (at the prompt)

```
cancel        - Exit the program
clear-cache   - Clear rendered trips cache
stop          - During rendering: type 'stop' + Enter to abort
trips         - Show all trips
help/h/?      - Show command help

render [flags] [selection]   (or 'r' for short)
```

Notes:
- `render` requires either a selection (e.g., `r 1;4` or `r 1,3`) or a mode flag:
  - `-a` to render all trips (including already rendered)
  - `-ur` to render only unrendered trips
- If you enter `r` without a selection or mode, the program will prompt:
  `No selection or mode given. Render ALL trips? (yes/no) or enter a different command:`
  - Answer `yes` to render all trips, `no` to return to the prompt, or type a different command.
- At startup the help is displayed; use `trips` to list available trips (the list is no longer shown automatically after rendering).


### Render Command Flags

| Flag | Description |
|------|-------------|
| `-a`, `--all` | Include already rendered trips |
| `-ur`, `--unrendered` | Only unrendered trips (use to restrict) |
| `-y YEAR` | Filter by year (e.g., `-y 2025`) |
| `-d START;END` | Date range in dd.mm.yyyy format |
| `-config(KEY=VALUE,...)` | Override config for this render (e.g., `-config(map_style="road", max_photos_per_step=4)`) |

### Selection Formats

| Format | Description |
|--------|-------------|
| `1` | Single trip |
| `1;4` | Range of trips (1 to 4) |
| `1,5,6` | Multiple specific trips |
| `l` or `last` | Last trip |
| `l-1` | Second to last trip |

### Examples

Examples:

```
# Always provide either a selection or -a/-ur
r -a                      Render all trips (including rendered)
r -ur -y 2025             Render unrendered trips from 2025
r -d 01.01.2025;01.06.2025 -ur   Render trips in date range (only unrendered)
r 1;4                     Render trips 1 through 4
r -a l                    Render last trip (even if rendered)
r 1,3,5                   Render trips 1, 3, and 5
r 67 -config(map_style="road", max_photos_per_step=4)  Render trip 67 with overrides
```

```bash
Command> r -a                # Render all trips (explicit -a required if you mean all)
Command> r -a                # Render all trips (including rendered)
Command> r -y 2025           # Render trips from 2025 (default includes rendered)
Command> r -d 01.01.2025;01.06.2025   # Render trips in date range
Command> r 1;4               # Render trips 1 through 4
Command> r -a l              # Render last trip (even if rendered)
Command> r 1,3,5             # Render trips 1, 3, and 5
```

### CLI Options

```bash
# Clear cache from command line
python polarsteps_pdf_generator.py --clear-cache

# Show help
python polarsteps_pdf_generator.py -h
```

## Data Structure

The script expects Polarsteps data in this structure:

```
BSPData/
  └── {date}/
      └── trip/
          └── {trip-slug}_{trip-id}/
              ├── trip.json
              ├── locations.json
              └── {step-slug}_{step-id}/
                  ├── photos/
                  │   └── *.jpg
                  └── videos/
                      └── *.mp4
```

## Configuration

Edit `config.toml` to customize the PDF generation (preferred). TOML supports comments and a clearer syntax. The script will look for `config.toml` first and will fall back to `config.json` (legacy) if present.

Example `config.toml`:

```toml
# Font Settings
step_title_font_size = 18
step_text_font_size = 12
text_font_path = "C:/Windows/Fonts/SegoeUI.ttf"
emoji_font_path = "C:/Windows/Fonts/seguiemj.ttf"
emoji_scale = 1.2

# Layout
safety_margin_mm = 12
max_photos_per_step = 6

# Map settings
map_style = "hybrid"  # "hybrid" (satellite) or "road" (streets); default: "hybrid"
marker_thumb_size = 40

# New bounding-box map settings
maps.vertical_resolution_px = 720  # vertical image resolution in pixels (affects marker sizes)
[maps.overview]
padding_factor = 0.10
min_width_km = 10.0
[maps.step]
padding_factor = 0.10
min_width_km = 2.0
max_distance_farthest_steps_km = 100.0
cluster_distance_km = 5.0
min_zoom = 13
render_scale = 2.0

# Step map
step_map_zoom_out = 0
step_map_padding = 0.06
step_map_auto_tighten = true
step_map_tighten_scale_small = 0.8
step_map_tighten_scale_medium = 0.6
step_map_tighten_scale_large = 0.5
```

Migration note:
- The legacy `config.json` has been removed from the repository. If you still have a local `config.json`, the program will fall back to it, but you are encouraged to move your settings to `config.toml` (copying values and adding `#` comments as needed).

### Configuration Options

- **map_style**: Map tiles to use — either `"hybrid"` (satellite imagery, default) or `"road"` (street tiles). (default: `"hybrid"`)
- **step_title_font_size**: Font size for step titles (default: 18)
- **step_text_font_size**: Font size for step descriptions (default: 12)
- **marker_thumb_size**: Base size of thumbnail markers on maps in pixels (default: 40). This value is a base size and is automatically scaled by the configured map vertical resolution and any supersampling (`maps.step.render_scale`) used when rendering maps.
- **maps.vertical_resolution_px**: Vertical output resolution in pixels used for rendering maps; geographic coverage is determined from this using a fixed 16:9 ratio (example: 720)
- **maps.step.min_zoom**: Minimum zoom for step maps (higher = more detail, default: 13)
- **maps.step.render_scale**: Supersampling render scale for step maps (higher = sharper tiles, default: 2.0)
- **step_map_zoom_out**: Zoom out by N levels on step maps (default: 0 — set >0 to show more context)
- **step_map_padding**: Padding around bounds on step maps (default: 0.06 — smaller means tighter crop)
- **step_map_auto_tighten**: Automatically reduce step-map padding for trips with many steps to produce more zoomed-in step maps (default: true)
- **step_map_tighten_scale_small**: Padding scale for 21-40 steps (default: 0.8 — applied as pad_frac * scale)
- **step_map_tighten_scale_medium**: Padding scale for 41-80 steps (default: 0.6)
- **step_map_tighten_scale_large**: Padding scale for >80 steps (default: 0.5)
- **step_map_neighbor_max_km**: Ignore prev/next neighbors farther than this when fitting step maps (default: 600 km)
- **step_map_neighbor_limit_steps_threshold**: Minimum step count before neighbor distance limiting applies (default: 5)
- **step_map_max_pad_km**: Maximum absolute padding applied to step maps (km). Caps how much padding can be added even on wide spans (default: 25 km).
- **max_photos_per_step**: Maximum photos per step page (default: 6)

## Cache System

The program maintains a cache file (`rendered_trips_cache.json`) that tracks which trips have been rendered. This allows you to:
- See which trips are already rendered (marked with ✓)
- Skip already rendered trips by default
- Use `-a` flag to include rendered trips
- Clear the cache with `clear-cache` command

## Output

PDFs are saved in the `TripPdfs/` folder next to the script with the trip name as filename.

## License

MIT License
