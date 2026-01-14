# Polarsteps PDF Generator

Generate beautiful PDF travel journals from your downloaded Polarsteps data.

## Features

- 🗺️ **Overview Map**: Satellite map with your complete route and step markers
- 📍 **Step Maps**: Individual location maps for each step (ESRI World Imagery)
- 📸 **Photo Grids**: Adaptive photo layout (1-6 photos per step)
- 📹 **Video Links**: Compact link collection for local video files
- 🌡️ **Weather Info**: Temperature and conditions for each step
- 📝 **Descriptions**: Full travel journal text with formatting

## Installation

1. Make sure you have Python 3.8+ installed
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Run the script and select a trip from the console menu:

```bash
python polarsteps_pdf_generator.py
```

Or specify a custom BSPData folder:

```bash
python polarsteps_pdf_generator.py /path/to/BSPData
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

## Output

PDFs are saved in the `trip/` folder with the trip name as filename.

## License

MIT License
