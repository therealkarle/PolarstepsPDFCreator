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

### Selection Formats

| Format | Description |
|--------|-------------|
| `1` | Single trip |
| `1;4` | Range of trips (1 to 4) |
| `1,5,6` | Multiple specific trips |
| `l` or `last` | Last trip |
| `l-1` | Second to last trip |

### Examples

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

Edit `config.json` to customize the PDF generation:

```json
{
  "step_title_font_size": 18,
  "step_text_font_size": 12,
  "safety_margin_mm": 12,
  "default_map_zoom": 12,
  "min_map_zoom": 3,
  "emoji_scale": 1.2,
  "max_photos_per_step": 6,
  "text_font_path": "C:/Windows/Fonts/SegoeUI.ttf",
  "emoji_font_path": "C:/Windows/Fonts/seguiemj.ttf"
}
```

### Configuration Options

- **step_title_font_size**: Font size for step titles (default: 18)
- **step_text_font_size**: Font size for step descriptions (default: 12)
- **default_map_zoom**: Default zoom level for maps (default: 12)
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
