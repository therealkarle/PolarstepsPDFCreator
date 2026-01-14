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
from PIL import ImageFont, ImageDraw
import re
import requests
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
    
    def generate_step_map(self, lat: float, lon: float, zoom: int = 12, width: int = 0, height: int = 0) -> bytes:
        """Generate a small map for a single step location.

        Parameters:
        - lat, lon: location
        - zoom: map zoom level (lower = smaller scale / more area)
        - width, height: pixel dimensions for the generated map (0 = use defaults)
        """
        # Use provided size if given, otherwise defaults
        w = width or 400
        h = height or 300
        m = self._create_map(w, h)

        # Add marker
        marker = CircleMarker((lon, lat), MARKER_COLOR_START, 12)
        m.add_marker(marker)

        # Render with requested zoom
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

        # Enforce fixed font sizes for step text to avoid layout variance
        # These values are integers (points)
        self.STEP_TITLE_FONT_SIZE = 18
        self.STEP_TEXT_FONT_SIZE = 12
    
    def _create_styles(self) -> dict:
        """Create custom paragraph styles."""
        styles = getSampleStyleSheet()
        
        styles.add(ParagraphStyle(
            name="TripTitle",
            fontSize=28,
            textColor=self.PRIMARY_COLOR,
            alignment=TA_CENTER,
            spaceAfter=12 * mm,
            fontName="Helvetica-Bold"
        ))
        
        styles.add(ParagraphStyle(
            name="TripSubtitle",
            fontSize=14,
            textColor=self.TEXT_COLOR,
            alignment=TA_CENTER,
            leading=16,
            spaceAfter=8 * mm
        ))
        
        styles.add(ParagraphStyle(
            name="StepTitle",
            fontSize=self.STEP_TITLE_FONT_SIZE if hasattr(self, 'STEP_TITLE_FONT_SIZE') else 18,
            textColor=self.PRIMARY_COLOR,
            alignment=TA_LEFT,
            spaceAfter=8,
            fontName="Helvetica-Bold"
        ))
        
        styles.add(ParagraphStyle(
            name="StepMeta",
            fontSize=self.STEP_TEXT_FONT_SIZE if hasattr(self, 'STEP_TEXT_FONT_SIZE') else 10,
            textColor=HexColor("#666666"),
            alignment=TA_LEFT,
            spaceBefore=4,
            spaceAfter=10,
            leading=12
        ))
        
        styles.add(ParagraphStyle(
            name="StepDescription",
            fontSize=self.STEP_TEXT_FONT_SIZE if hasattr(self, 'STEP_TEXT_FONT_SIZE') else 11,
            textColor=self.TEXT_COLOR,
            alignment=TA_JUSTIFY,
            spaceAfter=15,
            leading=14
        ))
        
        styles.add(ParagraphStyle(
            name="VideoLink",
            fontSize=self.STEP_TEXT_FONT_SIZE if hasattr(self, 'STEP_TEXT_FONT_SIZE') else 9,
            textColor=HexColor("#0066CC"),
            alignment=TA_LEFT,
            spaceAfter=3
        ))
        
        styles.add(ParagraphStyle(
            name="VideoHeader",
            fontSize=self.STEP_TEXT_FONT_SIZE if hasattr(self, 'STEP_TEXT_FONT_SIZE') else 10,
            textColor=self.TEXT_COLOR,
            alignment=TA_LEFT,
            spaceBefore=10,
            spaceAfter=5,
            fontName="Helvetica-Bold"
        ))
        
        return styles

    def _contains_emoji(self, text: str) -> bool:
        """Detect if the text contains emoji characters."""
        if not text:
            return False
        emoji_pattern = re.compile(
            "[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
            "\U0001F700-\U0001F77F\U00002600-\U000026FF\U00002700-\U000027BF\U0001F1E6-\U0001F1FF]",
            flags=re.UNICODE,
        )
        return bool(emoji_pattern.search(text))

    def _render_text_to_image(self, text: str, style: ParagraphStyle, max_width: float) -> RLImage:
        """Render given text to an image using an emoji-capable font and return a ReportLab Image.

        - `max_width` is given in points; we render at 72 DPI so 1 point == 1 pixel.
        """
        # Use points as pixels (ReportLab points at 72 DPI)
        width_px = max(int(max_width), 200)
        # Fixed font size for step text (use style fontSize or fallback to STEP_TEXT_FONT_SIZE)
        try:
            font_size_px = int(getattr(style, "fontSize", None) or getattr(self, "STEP_TEXT_FONT_SIZE", 11))
        except Exception:
            font_size_px = 11

        # Choose a regular text font (try Segoe UI, Arial, DejaVuSans)
        regular_font_paths = [
            "C:/Windows/Fonts/seguiui.ttf",
            "C:/Windows/Fonts/SegoeUI.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        regular_font = None
        for p in regular_font_paths:
            try:
                if Path(p).exists():
                    regular_font = ImageFont.truetype(p, font_size_px)
                    break
            except Exception:
                continue
        if regular_font is None:
            regular_font = ImageFont.load_default()

        # Emoji cache folder
        cache_dir = Path(__file__).parent / ".emoji_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Emoji regex (captures sequences including ZWJ/FE0F)
        emoji_pattern = re.compile(r'([\U0001F1E6-\U0001F1FF\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\u2600-\u26FF\u2700-\u27BF\u200d\ufe0f]+)', flags=re.UNICODE)

        lines = text.splitlines() or [text]

        # First pass: compute required image width and height
        line_metrics = []
        max_line_width = 0
        total_height = 0

        for line in lines:
            parts = emoji_pattern.split(line)
            line_width = 0
            line_height = 0
            for part in parts:
                if not part:
                    continue
                if emoji_pattern.fullmatch(part):
                    # Emoji sequence: convert to codepoints
                    cps = [f"{ord(ch):x}" for ch in part]
                    # join by '-' (handles multi-codepoint roughly)
                    cp_seq = '-'.join(cps)
                    emoji_file = cache_dir / f"{cp_seq}.png"
                    if not emoji_file.exists():
                        # Fetch from Twemoji CDN (72x72)
                        url = f"https://twemoji.maxcdn.com/v/latest/72x72/{cp_seq}.png"
                        try:
                            r = requests.get(url, timeout=10)
                            if r.status_code == 200:
                                emoji_file.write_bytes(r.content)
                        except Exception:
                            pass
                    try:
                        with Image.open(emoji_file) as eimg:
                            ew, eh = eimg.size
                            # scale emoji height slightly larger for visibility
                            scale = (font_size_px * 1.2) / float(eh)
                            ew = int(ew * scale)
                            eh = int(eh * scale)
                    except Exception:
                        # fallback to square placeholder
                        ew = font_size_px
                        eh = font_size_px
                    line_width += ew
                    line_height = max(line_height, eh)
                else:
                    bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), part, font=regular_font)
                    pw = bbox[2] - bbox[0]
                    ph = bbox[3] - bbox[1]
                    line_width += pw
                    line_height = max(line_height, ph)
            line_metrics.append((line_width, line_height, parts))
            max_line_width = max(max_line_width, line_width)
            total_height += line_height + 4

        img_width = max(max_line_width, width_px)
        img_height = max(int(total_height), font_size_px + 4)

        img = Image.new("RGBA", (int(img_width), int(img_height)), "WHITE")
        draw = ImageDraw.Draw(img)

        y = 0
        for (line_width, line_height, parts) in line_metrics:
            x = 0
            for part in parts:
                if not part:
                    continue
                if emoji_pattern.fullmatch(part):
                    cps = [f"{ord(ch):x}" for ch in part]
                    cp_seq = '-'.join(cps)
                    emoji_file = cache_dir / f"{cp_seq}.png"
                    try:
                        with Image.open(emoji_file).convert("RGBA") as eimg:
                            ew, eh = eimg.size
                            scale = (font_size_px * 1.2) / float(eh)
                            ew = int(ew * scale)
                            eh = int(eh * scale)
                            eimg = eimg.resize((ew, eh), Image.LANCZOS)
                            img.paste(eimg, (int(x), int(y)), eimg)
                            x += ew
                    except Exception:
                        # draw a placeholder box
                        draw.rectangle([x, y, x + font_size_px, y + font_size_px], outline=(0, 0, 0))
                        x += font_size_px
                else:
                    draw.text((x, y), part, font=regular_font, fill=(0, 0, 0))
                    bbox = draw.textbbox((x, y), part, font=regular_font)
                    pw = bbox[2] - bbox[0]
                    x += pw
            y += line_height + 4

        # Save image to bytes
        img_bytes = io.BytesIO()
        img.convert("RGB").save(img_bytes, format="PNG")
        img_bytes.seek(0)

        rl_img = RLImage(img_bytes)
        # Scale image to fit max_width while preserving aspect ratio
        img_width_pt = min(self.CONTENT_WIDTH, float(rl_img.imageWidth))
        scale = img_width_pt / float(rl_img.imageWidth)
        rl_img.drawWidth = img_width_pt
        rl_img.drawHeight = float(rl_img.imageHeight) * scale
        return rl_img

    def _add_text_or_image(self, text: str, style_name: str, escape_html: bool = True):
        """Add text as a Paragraph or render as image if it contains emoji characters."""
        style = self.styles.get(style_name)
        if text is None:
            return

        # For descriptions we keep original newlines; for Paragraph we need HTML escaped + <br/>
        if self._contains_emoji(text):
            # Render to image and append
            rl_img = self._render_text_to_image(text, style, self.CONTENT_WIDTH)
            self.elements.append(rl_img)
        else:
            safe_text = text
            if escape_html:
                safe_text = safe_text.replace("&", "&amp;")
                safe_text = safe_text.replace("<", "&lt;")
                safe_text = safe_text.replace(">", "&gt;")
                safe_text = safe_text.replace("\n", "<br/>")
            self.elements.append(Paragraph(safe_text, style))
    
    def _add_title_page(self):
        """Add the title page with trip name and overview map."""
        trip_name = self.trip_parser.get_trip_name()
        start_date, end_date = self.trip_parser.get_trip_dates()
        total_km = self.trip_parser.get_total_km()
        step_count = len(self.trip_parser.steps)
        
        # Title: render as Paragraphs to ensure consistent spacing
        self.elements.append(Spacer(1, 30 * mm))
        # Render title and subtitle as Paragraphs (avoid image-based rendering here)
        date_str = ""
        if start_date and end_date:
            date_str = f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
        elif start_date:
            date_str = start_date.strftime('%d.%m.%Y')

        subtitle = f"{date_str}<br/>{step_count} Steps • {total_km:.0f} km"

        self.elements.append(Paragraph(trip_name, self.styles["TripTitle"]))
        self.elements.append(Paragraph(subtitle, self.styles["TripSubtitle"]))
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
        """Format weather info as plain text (no emoji) for reliable PDF rendering."""
        weather_labels = {
            "clear-day": "Clear",
            "clear-night": "Clear night",
            "partly-cloudy-day": "Partly cloudy",
            "partly-cloudy-night": "Partly cloudy night",
            "cloudy": "Cloudy",
            "rain": "Rain",
            "snow": "Snow",
            "wind": "Windy",
            "fog": "Fog"
        }

        label = weather_labels.get(condition, "Weather")
        return f"{label}, {temperature:.0f}°C"
    
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

    def _flowables_height(self, flowables: list) -> float:
        """Estimate total height (in points) of a list of flowables by calling their
        `wrap` method. Falls back to reasonable defaults when wrap fails.
        """
        total = 0.0
        for f in flowables:
            try:
                w, h = f.wrap(self.CONTENT_WIDTH, self.PAGE_HEIGHT)
                total += float(h)
            except Exception:
                # Fallbacks: Spacer has .height, Paragraph/Table/Images may be approximated
                try:
                    from reportlab.platypus import Spacer
                    if isinstance(f, Spacer):
                        total += float(f.height)
                        continue
                except Exception:
                    pass
                # Default conservative estimate for unknown flowables
                total += 60 * mm
        return total

    def _remaining_page_space(self) -> float:
        """Estimate remaining vertical space on the current page (points).

        We compute heights of the flowables added since the last PageBreak.
        """
        # Inner page height is page height minus margins
        page_inner = float(self.PAGE_HEIGHT - 2 * self.MARGIN)

        # Find last PageBreak index
        used_flowables = []
        for f in reversed(self.elements):
            if isinstance(f, PageBreak):
                break
            used_flowables.insert(0, f)

        used_height = self._flowables_height(used_flowables) if used_flowables else 0.0
        remaining = max(0.0, page_inner - used_height)
        return remaining
    
    def _add_video_links(self, videos: list):
        """Add compact video link collection."""
        if not videos:
            return
        
        # Use emoji-aware renderer for the video header
        self._add_text_or_image("📹 Videos:", "VideoHeader", escape_html=False)
        
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
        
        # Collect flowables for this step, then add as a single unit when possible
        step_flow = []

        # Step title
        display_name = step_data.get("display_name", f"Step {step_number}")
        # Use emoji-aware text/image addition but target local list instead of self.elements
        # Title
        if self._contains_emoji(f"{step_number}. {display_name}"):
            step_flow.append(self._render_text_to_image(f"{step_number}. {display_name}", self.styles["StepTitle"], self.CONTENT_WIDTH))
        else:
            step_flow.append(Paragraph(f"{step_number}. {display_name}", self.styles["StepTitle"]))

        # Small spacer to separate title from meta to prevent visual overlap
        from reportlab.platypus import Spacer
        step_flow.append(Spacer(1, 2 * mm))

        # Location and date
        location = step_data.get("location", {})
        location_name = location.get("name", "")
        location_detail = location.get("detail", "")

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

        if self._contains_emoji(meta_text):
            step_flow.append(self._render_text_to_image(meta_text, self.styles["StepMeta"], self.CONTENT_WIDTH))
        else:
            # escape minimal HTML for Paragraph
            safe_meta = meta_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            step_flow.append(Paragraph(safe_meta, self.styles["StepMeta"]))

        # Step map (small, inline)
        lat = location.get("lat")
        lon = location.get("lon")

        if lat and lon:
            try:
                map_height_points = 60 * mm
                map_bytes = self.map_generator.generate_step_map(
                    lat, lon,
                    zoom=12,
                    width=int(self.CONTENT_WIDTH),
                    height=int(map_height_points)
                )
                map_img = RLImage(io.BytesIO(map_bytes))
                map_img.drawWidth = self.CONTENT_WIDTH
                map_img.drawHeight = map_height_points
                step_flow.append(map_img)
            except Exception as e:
                print(f"    Warning: Could not generate step map: {e}")

        # Description
        description = step_data.get("description", "")
        if description:
            if self._contains_emoji(description):
                step_flow.append(self._render_text_to_image(description, self.styles["StepDescription"], self.CONTENT_WIDTH))
            else:
                safe_desc = description.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                safe_desc = safe_desc.replace("\n", "<br/>")
                step_flow.append(Paragraph(safe_desc, self.styles["StepDescription"]))

        # Photo grid
        if photos:
            photo_grid = self._create_photo_grid(photos)
            if photo_grid:
                step_flow.append(photo_grid)
                step_flow.append(Spacer(1, 5 * mm))

        # Video links (append header + links)
        if videos:
            # Video header
            if self._contains_emoji("📹 Videos:"):
                step_flow.append(self._render_text_to_image("📹 Videos:", self.styles["VideoHeader"], self.CONTENT_WIDTH))
            else:
                step_flow.append(Paragraph("Videos:", self.styles["VideoHeader"]))

            for video_path in videos:
                video_name = video_path.name
                file_url = video_path.as_uri()
                link_text = f'<link href="{file_url}">{video_name}</link>'
                step_flow.append(Paragraph(link_text, self.styles["VideoLink"]))

        # Spacer before next step
        step_flow.append(Spacer(1, 10 * mm))

        # Return the prepared flowables for this step to the caller
        return step_flow
    
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
        
        # Add steps with page-space checks
        total_steps = len(self.trip_parser.steps)
        page_inner_height = float(self.PAGE_HEIGHT - 2 * self.MARGIN)
        safety_margin = 12 * mm

        for i, step in enumerate(self.trip_parser.steps):
            step_name = step["data"].get("display_name", f"Step {i+1}")
            print(f"  Adding step {i+1}/{total_steps}: {step_name}")

            # Collect flowables for this step
            step_flow = self._add_step(step, i + 1)

            try:
                step_height = self._flowables_height(step_flow)
            except Exception:
                step_height = page_inner_height

            remaining = self._remaining_page_space()

            # If step fits in the remaining space minus safety, keep together here
            if step_height <= remaining - safety_margin:
                self.elements.append(KeepTogether(step_flow))
            else:
                # If the step fits on an empty page, start a new page and keep together
                if step_height <= page_inner_height - safety_margin:
                    if remaining < safety_margin or remaining < step_height:
                        self.elements.append(PageBreak())
                    self.elements.append(KeepTogether(step_flow))
                else:
                    # Step is taller than a page: start a new page if needed, then allow splitting
                    if remaining < safety_margin:
                        self.elements.append(PageBreak())
                    self.elements.extend(step_flow)
        
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
