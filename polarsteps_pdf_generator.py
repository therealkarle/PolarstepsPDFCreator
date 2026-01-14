#!/usr/bin/env python3
"""
Polarsteps PDF Generator

Generates beautiful PDF travel journals from downloaded Polarsteps data.
Features:
- Overview map with route and step markers (first photo per step)
- Per-step pages with location map, weather, description, and photo grid
- Compact video link collection per step
- ESRI World Imagery satellite tiles

Usage:
    python polarsteps_pdf_generator.py [bsp_data_folder]
"""

import json
import os
import sys
import io
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from staticmap import StaticMap, CircleMarker, Line
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image as RLImage,
    Table, TableStyle, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY


# ESRI World Imagery tile URL
ESRI_SATELLITE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

# Colors for map markers (RGB tuples for staticmap)
MARKER_COLOR_START = "#FF6B6B"  # Red for first/single markers
MARKER_COLOR_STEP = "#4ECDC4"   # Teal for other step markers
ROUTE_COLOR = "#FFFFFF"         # White for route line


class TripParser:
    """Parses Polarsteps trip data from downloaded JSON files."""
    
    def __init__(self, trip_folder: Path):
        self.trip_folder = Path(trip_folder)
        self.trip_data = None
        self.locations_data = None
        self.steps = []
        
    def load(self) -> dict:
        """Load trip.json and locations.json."""
        trip_json_path = self.trip_folder / "trip.json"
        locations_json_path = self.trip_folder / "locations.json"
        
        if not trip_json_path.exists():
            raise FileNotFoundError(f"trip.json not found in {self.trip_folder}")
        
        with open(trip_json_path, "r", encoding="utf-8") as f:
            self.trip_data = json.load(f)
        
        if locations_json_path.exists():
            with open(locations_json_path, "r", encoding="utf-8") as f:
                self.locations_data = json.load(f)
        
        self._parse_steps()
        return self.trip_data
    
    def _parse_steps(self):
        """Parse all steps and link them to their local folders."""
        if not self.trip_data:
            return
        
        all_steps = self.trip_data.get("all_steps", [])
        
        for step in all_steps:
            step_id = step.get("id")
            step_slug = step.get("slug", "")
            
            # Find matching folder
            step_folder = None
            for folder in self.trip_folder.iterdir():
                if folder.is_dir() and folder.name.endswith(f"_{step_id}"):
                    step_folder = folder
                    break
            
            # Get photos and videos
            photos = []
            videos = []
            
            if step_folder:
                photos_folder = step_folder / "photos"
                videos_folder = step_folder / "videos"
                
                if photos_folder.exists():
                    photos = sorted([
                        p for p in photos_folder.iterdir()
                        if p.suffix.lower() in [".jpg", ".jpeg", ".png"]
                    ])
                
                if videos_folder.exists():
                    videos = sorted([
                        v for v in videos_folder.iterdir()
                        if v.suffix.lower() in [".mp4", ".mov", ".avi"]
                    ])
            
            self.steps.append({
                "data": step,
                "folder": step_folder,
                "photos": photos,
                "videos": videos
            })
    
    def get_trip_name(self) -> str:
        return self.trip_data.get("name", "Unknown Trip")
    
    def get_trip_dates(self) -> tuple:
        """Return (start_date, end_date) as datetime objects."""
        start_ts = self.trip_data.get("start_date", 0)
        end_ts = self.trip_data.get("end_date", 0)
        
        start_date = datetime.fromtimestamp(start_ts) if start_ts else None
        end_date = datetime.fromtimestamp(end_ts) if end_ts else None
        
        return start_date, end_date
    
    def get_total_km(self) -> float:
        return self.trip_data.get("total_km", 0)
    
    def get_route_coordinates(self) -> list:
        """Get GPS track coordinates from locations.json."""
        if not self.locations_data:
            return []
        
        locations = self.locations_data.get("locations", [])
        return [(loc["lon"], loc["lat"]) for loc in locations]


class MapGenerator:
    """Generates static maps using ESRI World Imagery tiles."""
    
    def __init__(self, width: int = 800, height: int = 600):
        self.width = width
        self.height = height
    
    def _create_map(self, width: int = None, height: int = None) -> StaticMap:
        """Create a StaticMap with ESRI satellite tiles."""
        w = width or self.width
        h = height or self.height
        
        return StaticMap(
            w, h,
            url_template=ESRI_SATELLITE_URL,
            tile_size=256
        )
    
    def generate_overview_map(self, trip_parser: TripParser) -> bytes:
        """Generate overview map with route and step markers."""
        m = self._create_map()
        
        # Add route line
        route_coords = trip_parser.get_route_coordinates()
        if len(route_coords) > 1:
            line = Line(route_coords, ROUTE_COLOR, 3)
            m.add_line(line)
        
        # Add step markers
        for i, step in enumerate(trip_parser.steps):
            step_data = step["data"]
            location = step_data.get("location", {})
            
            if location:
                lat = location.get("lat")
                lon = location.get("lon")
                
                if lat and lon:
                    # Use different colors for markers
                    color = MARKER_COLOR_START if i == 0 else MARKER_COLOR_STEP
                    marker = CircleMarker((lon, lat), color, 12)
                    m.add_marker(marker)
        
        # Render to bytes
        image = m.render()
        img_bytes = io.BytesIO()
        image.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        
        return img_bytes.getvalue()
    
    def generate_step_map(self, lat: float, lon: float, zoom: int = 14) -> bytes:
        """Generate a small map for a single step location."""
        m = self._create_map(400, 300)
        
        # Add marker
        marker = CircleMarker((lon, lat), MARKER_COLOR_START, 12)
        m.add_marker(marker)
        
        # Render
        image = m.render(zoom=zoom)
        img_bytes = io.BytesIO()
        image.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        
        return img_bytes.getvalue()


class PDFBuilder:
    """Builds the PDF document from parsed trip data."""
    
    # Page dimensions
    PAGE_WIDTH, PAGE_HEIGHT = A4
    MARGIN = 15 * mm
    CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
    
    # Colors
    PRIMARY_COLOR = HexColor("#1A5F7A")
    SECONDARY_COLOR = HexColor("#4ECDC4")
    TEXT_COLOR = HexColor("#333333")
    LIGHT_GRAY = HexColor("#F5F5F5")
    
    def __init__(self, output_path: Path, trip_parser: TripParser, map_generator: MapGenerator):
        self.output_path = Path(output_path)
        self.trip_parser = trip_parser
        self.map_generator = map_generator
        self.styles = self._create_styles()
        self.elements = []
    
    def _create_styles(self) -> dict:
        """Create custom paragraph styles."""
        styles = getSampleStyleSheet()
        
        styles.add(ParagraphStyle(
            name="TripTitle",
            fontSize=28,
            textColor=self.PRIMARY_COLOR,
            alignment=TA_CENTER,
            spaceAfter=10,
            fontName="Helvetica-Bold"
        ))
        
        styles.add(ParagraphStyle(
            name="TripSubtitle",
            fontSize=14,
            textColor=self.TEXT_COLOR,
            alignment=TA_CENTER,
            spaceAfter=20
        ))
        
        styles.add(ParagraphStyle(
            name="StepTitle",
            fontSize=18,
            textColor=self.PRIMARY_COLOR,
            alignment=TA_LEFT,
            spaceAfter=5,
            fontName="Helvetica-Bold"
        ))
        
        styles.add(ParagraphStyle(
            name="StepMeta",
            fontSize=10,
            textColor=HexColor("#666666"),
            alignment=TA_LEFT,
            spaceAfter=10
        ))
        
        styles.add(ParagraphStyle(
            name="StepDescription",
            fontSize=11,
            textColor=self.TEXT_COLOR,
            alignment=TA_JUSTIFY,
            spaceAfter=15,
            leading=14
        ))
        
        styles.add(ParagraphStyle(
            name="VideoLink",
            fontSize=9,
            textColor=HexColor("#0066CC"),
            alignment=TA_LEFT,
            spaceAfter=3
        ))
        
        styles.add(ParagraphStyle(
            name="VideoHeader",
            fontSize=10,
            textColor=self.TEXT_COLOR,
            alignment=TA_LEFT,
            spaceBefore=10,
            spaceAfter=5,
            fontName="Helvetica-Bold"
        ))
        
        return styles
    
    def _add_title_page(self):
        """Add the title page with trip name and overview map."""
        trip_name = self.trip_parser.get_trip_name()
        start_date, end_date = self.trip_parser.get_trip_dates()
        total_km = self.trip_parser.get_total_km()
        step_count = len(self.trip_parser.steps)
        
        # Title
        self.elements.append(Spacer(1, 50 * mm))
        self.elements.append(Paragraph(trip_name, self.styles["TripTitle"]))
        
        # Subtitle with dates and stats
        date_str = ""
        if start_date and end_date:
            date_str = f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
        elif start_date:
            date_str = start_date.strftime('%d.%m.%Y')
        
        subtitle = f"{date_str}<br/>{step_count} Steps • {total_km:.0f} km"
        self.elements.append(Paragraph(subtitle, self.styles["TripSubtitle"]))
        
        # Overview map
        self.elements.append(Spacer(1, 10 * mm))
        try:
            map_bytes = self.map_generator.generate_overview_map(self.trip_parser)
            map_img = RLImage(io.BytesIO(map_bytes))
            
            # Scale to fit page width
            aspect = 800 / 600
            map_width = self.CONTENT_WIDTH
            map_height = map_width / aspect
            
            map_img.drawWidth = map_width
            map_img.drawHeight = map_height
            
            self.elements.append(map_img)
        except Exception as e:
            print(f"  Warning: Could not generate overview map: {e}")
        
        self.elements.append(PageBreak())
    
    def _format_weather(self, condition: str, temperature: float) -> str:
        """Format weather info with emoji."""
        weather_icons = {
            "clear-day": "☀️",
            "clear-night": "🌙",
            "partly-cloudy-day": "⛅",
            "partly-cloudy-night": "☁️",
            "cloudy": "☁️",
            "rain": "🌧️",
            "snow": "❄️",
            "wind": "💨",
            "fog": "🌫️"
        }
        
        icon = weather_icons.get(condition, "🌡️")
        return f"{icon} {temperature:.0f}°C"
    
    def _create_photo_grid(self, photos: list, max_photos: int = 6) -> Optional[Table]:
        """Create an adaptive photo grid layout."""
        if not photos:
            return None
        
        # Limit photos per step
        photos_to_show = photos[:max_photos]
        num_photos = len(photos_to_show)
        
        # Determine grid layout
        if num_photos == 1:
            cols, rows = 1, 1
            img_width = self.CONTENT_WIDTH * 0.8
        elif num_photos == 2:
            cols, rows = 2, 1
            img_width = (self.CONTENT_WIDTH - 5*mm) / 2
        elif num_photos <= 4:
            cols, rows = 2, 2
            img_width = (self.CONTENT_WIDTH - 5*mm) / 2
        else:
            cols, rows = 3, 2
            img_width = (self.CONTENT_WIDTH - 10*mm) / 3
        
        # Calculate image height (4:3 aspect ratio default)
        img_height = img_width * 0.75
        
        # Create table data
        table_data = []
        row = []
        
        for i, photo_path in enumerate(photos_to_show):
            try:
                # Open and resize image
                with Image.open(photo_path) as img:
                    # Convert to RGB if necessary
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    
                    # Resize maintaining aspect ratio
                    img.thumbnail((int(img_width * 2), int(img_height * 2)), Image.LANCZOS)
                    
                    # Save to bytes
                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format="JPEG", quality=85)
                    img_bytes.seek(0)
                    
                    # Create ReportLab image
                    rl_img = RLImage(img_bytes)
                    
                    # Calculate actual dimensions maintaining aspect ratio
                    orig_width, orig_height = img.size
                    aspect = orig_width / orig_height
                    
                    if aspect > (img_width / img_height):
                        rl_img.drawWidth = img_width
                        rl_img.drawHeight = img_width / aspect
                    else:
                        rl_img.drawHeight = img_height
                        rl_img.drawWidth = img_height * aspect
                    
                    row.append(rl_img)
            except Exception as e:
                print(f"    Warning: Could not process image {photo_path}: {e}")
                row.append("")
            
            # Start new row
            if len(row) == cols:
                table_data.append(row)
                row = []
        
        # Add remaining photos
        if row:
            while len(row) < cols:
                row.append("")
            table_data.append(row)
        
        if not table_data:
            return None
        
        # Create table
        col_widths = [img_width + 2*mm] * cols
        table = Table(table_data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 1*mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 1*mm),
            ("TOPPADDING", (0, 0), (-1, -1), 1*mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1*mm),
        ]))
        
        return table
    
    def _add_video_links(self, videos: list):
        """Add compact video link collection."""
        if not videos:
            return
        
        self.elements.append(Paragraph("📹 Videos:", self.styles["VideoHeader"]))
        
        for video_path in videos:
            video_name = video_path.name
            # Create file:// link for local file
            file_url = video_path.as_uri()
            link_text = f'<link href="{file_url}">{video_name}</link>'
            self.elements.append(Paragraph(link_text, self.styles["VideoLink"]))
    
    def _add_step(self, step: dict, step_number: int):
        """Add a step to the PDF."""
        step_data = step["data"]
        photos = step["photos"]
        videos = step["videos"]
        
        # Step title
        display_name = step_data.get("display_name", f"Step {step_number}")
        self.elements.append(Paragraph(f"{step_number}. {display_name}", self.styles["StepTitle"]))
        
        # Location and date
        location = step_data.get("location", {})
        location_name = location.get("name", "")
        location_detail = location.get("detail", "")
        country_code = location.get("country_code", "")
        
        start_time = step_data.get("start_time")
        date_str = ""
        if start_time:
            date_str = datetime.fromtimestamp(start_time).strftime("%A, %d. %B %Y")
        
        # Weather
        weather_str = ""
        weather_condition = step_data.get("weather_condition")
        weather_temp = step_data.get("weather_temperature")
        if weather_condition and weather_temp is not None:
            weather_str = f" • {self._format_weather(weather_condition, weather_temp)}"
        
        meta_text = f"📍 {location_name}, {location_detail}"
        if date_str:
            meta_text += f" • 📅 {date_str}"
        meta_text += weather_str
        
        self.elements.append(Paragraph(meta_text, self.styles["StepMeta"]))
        
        # Step map (small, inline)
        lat = location.get("lat")
        lon = location.get("lon")
        
        if lat and lon:
            try:
                map_bytes = self.map_generator.generate_step_map(lat, lon)
                map_img = RLImage(io.BytesIO(map_bytes))
                map_img.drawWidth = 60 * mm
                map_img.drawHeight = 45 * mm
                
                # Wrap map in a table for positioning
                map_table = Table([[map_img]], colWidths=[62*mm])
                map_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5*mm),
                ]))
                self.elements.append(map_table)
            except Exception as e:
                print(f"    Warning: Could not generate step map: {e}")
        
        # Description
        description = step_data.get("description", "")
        if description:
            # Escape HTML entities and preserve line breaks
            description = description.replace("&", "&amp;")
            description = description.replace("<", "&lt;")
            description = description.replace(">", "&gt;")
            description = description.replace("\n", "<br/>")
            self.elements.append(Paragraph(description, self.styles["StepDescription"]))
        
        # Photo grid
        if photos:
            photo_grid = self._create_photo_grid(photos)
            if photo_grid:
                self.elements.append(photo_grid)
                self.elements.append(Spacer(1, 5 * mm))
        
        # Video links
        self._add_video_links(videos)
        
        # Spacer before next step
        self.elements.append(Spacer(1, 10 * mm))
    
    def build(self):
        """Build the complete PDF."""
        print(f"  Building PDF: {self.output_path}")
        
        doc = SimpleDocTemplate(
            str(self.output_path),
            pagesize=A4,
            leftMargin=self.MARGIN,
            rightMargin=self.MARGIN,
            topMargin=self.MARGIN,
            bottomMargin=self.MARGIN
        )
        
        # Add title page
        print("  Adding title page with overview map...")
        self._add_title_page()
        
        # Add steps
        total_steps = len(self.trip_parser.steps)
        for i, step in enumerate(self.trip_parser.steps):
            step_name = step["data"].get("display_name", f"Step {i+1}")
            print(f"  Adding step {i+1}/{total_steps}: {step_name}")
            self._add_step(step, i + 1)
            
            # Add page break every 2 steps (except last)
            if (i + 1) % 2 == 0 and i + 1 < total_steps:
                self.elements.append(PageBreak())
        
        # Build PDF
        print("  Generating PDF file...")
        doc.build(self.elements)
        print(f"  ✅ PDF created: {self.output_path}")


def find_trips(bsp_data_folder: Path) -> list:
    """Find all trip folders in BSPData."""
    trips = []
    
    for date_folder in sorted(bsp_data_folder.iterdir()):
        if not date_folder.is_dir():
            continue
        
        trip_folder = date_folder / "trip"
        if not trip_folder.exists():
            continue
        
        for trip in sorted(trip_folder.iterdir()):
            if trip.is_dir() and (trip / "trip.json").exists():
                trips.append(trip)
    
    return trips


def select_trip(trips: list) -> Optional[Path]:
    """Let user select a trip from the console."""
    if not trips:
        print("No trips found!")
        return None
    
    print("\n" + "=" * 60)
    print("  POLARSTEPS PDF GENERATOR")
    print("=" * 60)
    print("\nAvailable trips:\n")
    
    for i, trip in enumerate(trips, 1):
        # Load trip name from trip.json
        try:
            with open(trip / "trip.json", "r", encoding="utf-8") as f:
                trip_data = json.load(f)
            name = trip_data.get("name", trip.name)
            total_km = trip_data.get("total_km", 0)
            step_count = trip_data.get("step_count", 0)
            print(f"  [{i:2d}] {name}")
            print(f"       {step_count} steps • {total_km:.0f} km")
            print(f"       Folder: {trip.name}")
            print()
        except:
            print(f"  [{i:2d}] {trip.name}")
            print()
    
    print(f"  [0]  Exit")
    print()
    
    while True:
        try:
            choice = input("Select a trip (number): ").strip()
            if choice == "0":
                return None
            
            idx = int(choice) - 1
            if 0 <= idx < len(trips):
                return trips[idx]
            else:
                print("Invalid selection. Try again.")
        except ValueError:
            print("Please enter a number.")
        except KeyboardInterrupt:
            return None


def main():
    """Main entry point."""
    # Determine BSPData folder
    if len(sys.argv) > 1:
        bsp_data_folder = Path(sys.argv[1])
    else:
        # Default: look for BSPData in current directory or script directory
        script_dir = Path(__file__).parent
        bsp_data_folder = script_dir / "BSPData"
        
        if not bsp_data_folder.exists():
            bsp_data_folder = Path.cwd() / "BSPData"
    
    if not bsp_data_folder.exists():
        print(f"Error: BSPData folder not found at {bsp_data_folder}")
        print("Usage: python polarsteps_pdf_generator.py [path/to/BSPData]")
        sys.exit(1)
    
    print(f"Scanning for trips in: {bsp_data_folder}")
    
    # Find all trips
    trips = find_trips(bsp_data_folder)
    
    if not trips:
        print("No trips found in BSPData folder.")
        sys.exit(1)
    
    # Let user select a trip
    selected_trip = select_trip(trips)
    
    if not selected_trip:
        print("Exiting.")
        sys.exit(0)
    
    print(f"\nProcessing trip: {selected_trip.name}")
    
    # Parse trip
    parser = TripParser(selected_trip)
    parser.load()
    
    print(f"  Trip: {parser.get_trip_name()}")
    print(f"  Steps: {len(parser.steps)}")
    print(f"  Total km: {parser.get_total_km():.0f}")
    
    # Generate PDF
    trip_name_safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in parser.get_trip_name())
    # Ensure an output folder named 'pdfs' next to the script exists and use it for PDF files
    script_dir = Path(__file__).parent
    pdfs_dir = script_dir / "TripPdfs"
    try:
        pdfs_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fallback to trip folder if creation fails
        pdfs_dir = selected_trip.parent

    output_path = pdfs_dir / f"{trip_name_safe}.pdf"
    
    map_gen = MapGenerator()
    pdf_builder = PDFBuilder(output_path, parser, map_gen)
    pdf_builder.build()
    
    print(f"\n✅ Done! PDF saved to: {output_path}")


if __name__ == "__main__":
    main()
