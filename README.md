# Polarsteps PDF Generator

**Graphical interface (Tkinter) is the primary user experience; a full-featured
command‑line mode is also available.**

Generate beautiful PDF travel journals from your downloaded Polarsteps data.

## Features

- 🗺️ **Overview Map**: Satellite map with your complete route and step markers
- 📍 **Step Maps**: Individual location maps for each step (ESRI World Imagery)
- 📸 **Photo Grids**: Adaptive photo layout (1-6 photos per step)
- 📎 **Additional Media Appendix**: Undisplayed photos and all video links at the end
- 🌡️ **Weather Info**: Temperature and conditions for each step
- 📝 **Descriptions**: Full travel journal text with formatting
- 🖥️ **Tkinter GUI**: Primary interface; renders and configures trips in a sortable table.
  - 📅 **Optional Date Picker** (requires `tkcalendar`)
- ⚙️ **Settings Tab**: Edit the `config.toml` directly in the GUI and view/install
  required packages from the built-in package manager.
  - New: **Install Uninstalled** button to add only missing packages based on
    `requirements.txt` and optional dependencies.
- 💾 **Cache System**: Tracks rendered trips and provides batch rendering
- 🔄 **Batch Processing**: Render multiple trips with date filters

## Installation

1. Make sure you have Python 3.8+ installed
2. Install dependencies:

```bash
pip install -r requirements.txt
```

> 💡 **Tip:** if you plan to use the GUI (recommended) the full dependency set
> is installed by the above command. Playwright is pulled automatically when you
> render for the first time, but you can also install it earlier for a smoother
> start-up:
>
> ```bash
> pip install playwright
> playwright install
> ```
>
> Alternatively you can perform package management directly in the GUI. Open the
> **Settings** tab and switch to the **Packages** pane; click **Install
> Uninstalled** to add any missing requirements or optional components without
> leaving the app.

The GUI optionally supports a calendar-based date picker. To enable it, add the
`tkcalendar` package:

```bash
pip install tkcalendar
```

### Quick start – GUI (Tkinter)

Most users will find the graphical interface the easiest way to work with the
tool. To launch it:

1. Run `python -m gui.tk_gui` from the project root, or double-click
   `scripts\run_gui.bat` on Windows.
2. In the app window:
   * Choose one or more **Polarsteps Data** folders (separate multiple paths with
     semicolons).
   * Optionally change the **Output folder** for generated PDFs.
   * Select trips from the list; headers are clickable to sort and an arrow
     indicator (▲/▼) shows the current sort direction. The default sort order is
     by start date (newest first).
   * Click **Render Selected** to start processing. A progress log appears in the
     text area below the controls.

The Settings tab in the GUI lets you edit `config.toml` directly, manage
packages via a built‑in installer, and view the current environment. Use the
**Packages** section to see which dependencies are currently installed and to
install any missing ones – the **Install Uninstalled** button will add only the
packages that are absent from your environment (based on `requirements.txt`
and optional extras like `tkcalendar`). If Playwright or other optional
libraries are not present, the GUI will prompt you to install them before the
first render.

> ⚠️ Packaging note: if you intend to distribute a standalone Windows executable,
> tools like `pyinstaller` work well. Remember to bundle Playwright browsers
> following the [Playwright packaging docs](https://playwright.dev/docs/ci).

### Optional CLI tools

If you prefer the command line or are automating batch jobs, the script can be
run directly (see next section). The CLI and GUI share the same underlying
logic, so any feature available in one will be present in the other.


## Usage

Start the program:

```bash
python polarsteps_pdf_generator.py
```

Or with a custom Polarsteps Data folder (and optionally specify where PDFs should be written):

```bash
python polarsteps_pdf_generator.py /path/to/PolarstepsData --output-folder /path/to/output
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
| `--combined-html [FILE]` | Create a combined HTML overview for selected trips or filters. Defaults to `TripPdfs/combined_trips.html`. |
| `-config(KEY=VALUE,...)` | Override config for this render (e.g., `-config(map_style="road", max_photos_per_step=4)`) |

### Selection Formats

| Format | Description |
|--------|-------------|
| `1` | Single trip |
| `1;4` | Range of trips (1 to 4) |
| `1,5,6` | Multiple specific trips |
| `l` or `last` | Last trip |
| `l-1` | Second to last trip |

### Statistics 📊

You can ask the program to print a summary of your trips using the `stats` or `s` command at the prompt, or by running

```bash
python polarsteps_pdf_generator.py --stats
```

By default statistics are computed across all trips.  Use the same filtering flags as
for rendering to restrict the period:

| Flag | Description |
|------|-------------|
| `-y YEAR` | Limit to trips that start in `YEAR`; the returned period will be the whole calendar year (365/366 days) |
| `-d START;END` | Limit to the date range; the summary will report the full interval, not merely the days you were travelling |
| `--from YYYY-MM-DD` / `--to YYYY-MM-DD` | Alternative long‑form range options |

A “travel/non‑travel” line is shown with the number of days in the selected interval and the
subset of those days which fall inside the declared span of each trip.  In other words, we count
physical days on the road between a trip’s start and end dates (clipped to the interval);
missing step timestamps no longer cause whole days to vanish.  Previously only steps with
timestamps were counted, which could skew the result downward; the generator now uses trip
spans, giving the intuitive totals you expect (e.g. 96 days in 2025 for my data).  The interval
still honours your explicit range so a year‑filter always yields the full 365‑day denominator.

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

# Update checks
python polarsteps_pdf_generator.py --check-update    # report if a newer version exists
python polarsteps_pdf_generator.py --update          # download/install newer version and exit
python polarsteps_pdf_generator.py --auto-update     # perform a one-time update check (overrides config)
```

The `config.toml` key `auto_update` (boolean, default `false`) controls whether the
GUI/CLI will automatically check for a newer release when the program starts.

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
# Input/output locations
# `polarsteps_data_folder` may be a single path or a TOML array of paths when
# you want to aggregate trips from multiple exports. CLI accepts multiple
# space-separated folders as well.
polarsteps_data_folder = "C:/path/to/your/PolarstepsData"       # optional default data folder
# Example using multiple locations:
# polarsteps_data_folder = [
#     "C:/BSPData/2024",
#     "D:/OtherTrips",
# ]
output_folder = "C:/path/where/pdfs/are/saved" # defaults to TripPdfs/ next to script

# Font Settings
step_title_font_size = 18
step_text_font_size = 12
text_font_path = "C:/Windows/Fonts/SegoeUI.ttf"
emoji_font_path = "C:/Windows/Fonts/seguiemj.ttf"
emoji_scale = 1.2

# Layout
safety_margin_mm = 12
max_photos_per_step = 6
appendix_show_undisplayed_media = true

# Map settings
map_style = "hybrid"  # "hybrid" (satellite) or "road" (streets); default: "hybrid"
marker_thumb_size = 40

# Language (see LanguagePack/)
language = "en"  # "en" or "de"

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
- **appendix_show_undisplayed_media**: Append undisplayed photos and all video links at the end (default: true)
- **language**: UI/PDF language pack code. Default is "en"; use "de" for German. Add new languages by copying a file in `LanguagePack/`.

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

---

> ⚠️ **Note:** A separate `README_GUI.md` file previously existed with GUI tips.
> All relevant information has now been merged into this document. The old file
> remains for backward compatibility but can be ignored.

