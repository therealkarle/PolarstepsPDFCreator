#!/usr/bin/env python3
"""
Polarsteps PDF Generator

Generates beautiful PDF travel journals from downloaded Polarsteps data.
Features:
- Overview map with route and step markers (first photo per step)
- Per-step pages with location map, weather, description, and photo grid
- Compact video link collection per step
- ESRI World Imagery satellite tiles
"""
import io
from pathlib import Path
from typing import Optional
from datetime import datetime
import argparse
import json
import re
import threading
import queue
from collections import deque

# Trip parsing
class TripParser:
    """Minimal TripParser that loads basic trip metadata and media thumbnails.

    Goal: provide the small interface used by the rest of the script:
      - load()
      - get_trip_name()
      - get_trip_dates() -> (start_datetime, end_datetime)
      - get_total_km()
      - get_route_coordinates() -> list of (lon, lat)
      - self.steps -> list of { 'data': {...}, 'photos': [...], 'videos': [...] }

    This is intentionally conservative and resilient to missing fields.
    """
    def __init__(self, trip_path: Path):
        self.trip_path = Path(trip_path)
        self.trip_data = {}
        self.steps = []

    def load(self):
        # Load trip.json
        try:
            with open(self.trip_path / "trip.json", "r", encoding="utf-8") as f:
                self.trip_data = json.load(f)
        except Exception:
            self.trip_data = {}

        # Prefer explicit steps in trip.json if available
        if isinstance(self.trip_data.get("steps"), list) and self.trip_data.get("steps"):
            for s in self.trip_data.get("steps", []):
                data = s.get("data", s) if isinstance(s, dict) else {}
                photos = []
                videos = []
                # try to find photos/videos listed in step entry
                if isinstance(s, dict):
                    for p in s.get("photos", []):
                        photos.append(Path(self.trip_path) / p) if p else None
                    for v in s.get("videos", []):
                        videos.append(Path(self.trip_path) / v) if v else None
                self.steps.append({"data": data, "photos": photos, "videos": videos})
            return

        # Otherwise, heuristically discover step directories
        for child in sorted(self.trip_path.iterdir()):
            if not child.is_dir():
                continue
            # skip auxiliary folders
            if child.name.lower() in ("thumbnails", "thumbs", "meta"):
                continue

            photos_dir = child / "photos"
            videos_dir = child / "videos"

            photos = []
            videos = []

            if photos_dir.exists() and photos_dir.is_dir():
                for ext in ("*.jpg", "*.jpeg", "*.png"):
                    photos.extend(sorted(photos_dir.glob(ext)))
            if videos_dir.exists() and videos_dir.is_dir():
                for ext in ("*.mp4", "*.mov", "*.mkv"):
                    videos.extend(sorted(videos_dir.glob(ext)))

            # load step metadata if present
            step_data = {}
            for name in ("step.json", "step_info.json", "data.json"):
                try:
                    if (child / name).exists():
                        with open(child / name, "r", encoding="utf-8") as sf:
                            step_data = json.load(sf)
                        break
                except Exception:
                    step_data = {}

            if photos or videos or step_data:
                # normalize location field if nested
                loc = step_data.get("location") if isinstance(step_data, dict) else None
                if isinstance(loc, dict):
                    step_data["location"] = loc
                step_data.setdefault("display_name", child.name)
                # try to get start_time from step_data else fallback to trip start
                self.steps.append({"data": step_data, "photos": photos, "videos": videos})

        # If still empty, create a single synthetic empty step using trip.json
        if not self.steps:
            self.steps = [{"data": self.trip_data, "photos": [], "videos": []}]

    def get_trip_name(self) -> str:
        name = self.trip_data.get("name") if isinstance(self.trip_data, dict) else None
        if not name:
            # try to derive a readable name from folder
            name = str(self.trip_path.name).replace("_", " ")
        return name

    def get_trip_dates(self):
        start_ts = self.trip_data.get("start_date")
        end_ts = self.trip_data.get("end_date")
        start_dt = None
        end_dt = None
        try:
            if isinstance(start_ts, (int, float)):
                start_dt = datetime.fromtimestamp(int(start_ts))
            elif isinstance(start_ts, str) and start_ts:
                try:
                    start_dt = datetime.fromisoformat(start_ts)
                except Exception:
                    start_dt = None
        except Exception:
            start_dt = None
        try:
            if isinstance(end_ts, (int, float)):
                end_dt = datetime.fromtimestamp(int(end_ts))
            elif isinstance(end_ts, str) and end_ts:
                try:
                    end_dt = datetime.fromisoformat(end_ts)
                except Exception:
                    end_dt = None
        except Exception:
            end_dt = None
        return (start_dt, end_dt)

    def get_total_km(self) -> float:
        try:
            return float(self.trip_data.get("total_km", 0) or 0)
        except Exception:
            return 0.0

    def get_route_coordinates(self):
        coords = []
        for s in self.steps:
            data = s.get("data", {})
            loc = data.get("location") if isinstance(data, dict) else None
            if not loc:
                continue
            lat = loc.get("lat") or loc.get("latitude") or loc.get("Latitude")
            lon = loc.get("lon") or loc.get("lng") or loc.get("longitude") or loc.get("Longitude")
            try:
                if lat is not None and lon is not None:
                    coords.append((float(lon), float(lat)))
            except Exception:
                continue
        return coords

# Static map helper and defaults
try:
    from staticmap import StaticMap, CircleMarker, Line
except Exception:
    StaticMap = None
    CircleMarker = None
    Line = None

# ESRI World Imagery tile template
ESRI_SATELLITE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
# Map colors
ROUTE_COLOR = "#FF4D4F"  # red-ish
MARKER_COLOR_START = "#1A5F7A"  # teal
MARKER_COLOR_STEP = "#4ECDC4"  # lighter teal

# ReportLab: page sizes, units, styles and flowables
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import Paragraph, Image as RLImage, Table, TableStyle, Spacer, SimpleDocTemplate, PageBreak, KeepTogether

# Pillow (PIL) for image processing
from PIL import Image, ImageDraw, ImageFont


class MapGenerator:
    """Generates static maps using ESRI World Imagery tiles."""

    def __init__(self, width: int = 800, height: int = 600, default_zoom: int = 12):
        self.width = width
        self.height = height
        self.default_zoom = default_zoom

    def _create_map(self, width: int = None, height: int = None) -> StaticMap:
        """Create a StaticMap with ESRI satellite tiles."""
        if StaticMap is None:
            raise RuntimeError("staticmap not available: install the 'staticmap' package to enable map generation")
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


# Back-compat helper in case other modules need dates from a TripParser
def trip_parser_get_dates(trip_path: Path):
    tp = TripParser(trip_path)
    tp.load()
    return tp.get_trip_dates() if hasattr(tp, 'get_trip_dates') else (None, None)

    def generate_step_map(self, lat: float, lon: float, zoom: Optional[int] = None, width: int = 0, height: int = 0) -> bytes:
        """Generate a small map for a single step location.

        Parameters:
        - lat, lon: location
        - zoom: map zoom level (lower = smaller scale / more area)
        - width, height: pixel dimensions for the generated map (0 = use defaults)
        """
        if StaticMap is None or CircleMarker is None:
            raise RuntimeError("staticmap (and CircleMarker) not available: install 'staticmap' to enable step maps")

        # Use provided size if given, otherwise defaults
        w = width or 400
        h = height or 300
        m = self._create_map(w, h)

        # Add marker
        marker = CircleMarker((lon, lat), MARKER_COLOR_START, 12)
        m.add_marker(marker)

        # Render with requested zoom (fallback to default)
        render_zoom = zoom if zoom is not None else self.default_zoom
        image = m.render(zoom=render_zoom)
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
    
    def __init__(self, output_path: Path, trip_parser: TripParser, map_generator: MapGenerator, config: dict = None):
        self.output_path = Path(output_path)
        self.trip_parser = trip_parser
        self.map_generator = map_generator
        self.config = config or {}

        # Enforce fixed font sizes for step text to avoid layout variance
        # These values are integers (points) and should be set before creating styles
        self.STEP_TITLE_FONT_SIZE = int(self.config.get("step_title_font_size", 18))
        self.STEP_TEXT_FONT_SIZE = int(self.config.get("step_text_font_size", 12))

        # Try to register fonts before creating styles so styles can reference them
        self._register_fonts()
        self.styles = self._create_styles()
        self.elements = []
    
    def _create_styles(self) -> dict:
        """Create custom paragraph styles."""
        styles = getSampleStyleSheet()
        # Choose font names (registered in _register_fonts)
        text_font = getattr(self, "_registered_text_font", "Helvetica")
        emoji_font = getattr(self, "_registered_emoji_font", text_font)
        
        styles.add(ParagraphStyle(
            name="TripTitle",
            fontSize=28,
            textColor=self.PRIMARY_COLOR,
            alignment=TA_CENTER,
            spaceAfter=12 * mm,
            fontName=text_font
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
            fontName=text_font
        ))
        
        styles.add(ParagraphStyle(
            name="StepMeta",
            fontSize=self.STEP_TEXT_FONT_SIZE if hasattr(self, 'STEP_TEXT_FONT_SIZE') else 10,
            textColor=HexColor("#666666"),
            alignment=TA_LEFT,
            spaceBefore=4,
            spaceAfter=10,
            leading=12,
            fontName=emoji_font
        ))
        
        styles.add(ParagraphStyle(
            name="StepDescription",
            fontSize=self.STEP_TEXT_FONT_SIZE if hasattr(self, 'STEP_TEXT_FONT_SIZE') else 11,
            textColor=self.TEXT_COLOR,
            alignment=TA_JUSTIFY,
            spaceAfter=15,
            leading=14,
            fontName=text_font
        ))
        
        styles.add(ParagraphStyle(
            name="VideoLink",
            fontSize=self.STEP_TEXT_FONT_SIZE if hasattr(self, 'STEP_TEXT_FONT_SIZE') else 9,
            textColor=HexColor("#0066CC"),
            alignment=TA_LEFT,
            spaceAfter=3,
            fontName=text_font
        ))
        
        styles.add(ParagraphStyle(
            name="VideoHeader",
            fontSize=self.STEP_TEXT_FONT_SIZE if hasattr(self, 'STEP_TEXT_FONT_SIZE') else 10,
            textColor=self.TEXT_COLOR,
            alignment=TA_LEFT,
            spaceBefore=10,
            spaceAfter=5,
            fontName=text_font
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

    def _register_fonts(self):
        """Try to register an emoji-capable font and a text font for consistent PDF text rendering.

        Order of preference can be supplied via `config` keys `text_font_path` and `emoji_font_path`.
        """
        script_dir = Path(__file__).parent
        cfg = getattr(self, 'config', {}) or {}

        # Candidate font paths (Windows and common names)
        candidates = []
        emoji_candidates = []
        if cfg.get('text_font_path'):
            candidates.append(Path(cfg['text_font_path']))
        if cfg.get('emoji_font_path'):
            emoji_candidates.append(Path(cfg['emoji_font_path']))

        # Common Windows fonts
        candidates += [
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/seguisym.ttf"),
            Path("C:/Windows/Fonts/SegoeUI.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        ]

        emoji_candidates += [
            Path("C:/Windows/Fonts/seguiemj.ttf"),
            Path("C:/Windows/Fonts/seguiemj.ttf"),
            Path("C:/Windows/Fonts/seguisym.ttf"),
            Path("C:/Windows/Fonts/Symbola.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        ]

        # Find text font
        registered_text = None
        for p in candidates:
            try:
                if p and p.exists():
                    pdfmetrics.registerFont(TTFont('AppText', str(p)))
                    registered_text = 'AppText'
                    self._registered_text_font = 'AppText'
                    break
            except Exception:
                continue

        if not registered_text:
            self._registered_text_font = 'Helvetica'

        # Find emoji font (may be color; PDF will render monochrome glyphs)
        registered_emoji = None
        for p in emoji_candidates:
            try:
                if p and p.exists():
                    pdfmetrics.registerFont(TTFont('AppEmoji', str(p)))
                    registered_emoji = 'AppEmoji'
                    self._registered_emoji_font = 'AppEmoji'
                    break
            except Exception:
                continue

        if not getattr(self, '_registered_emoji_font', None):
            # fallback to text font
            self._registered_emoji_font = getattr(self, '_registered_text_font', 'Helvetica')

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
                            emoji_scale = float(self.config.get("emoji_scale", 1.2)) if hasattr(self, 'config') else 1.2
                            scale = (font_size_px * emoji_scale) / float(eh)
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
                            emoji_scale = float(self.config.get("emoji_scale", 1.2)) if hasattr(self, 'config') else 1.2
                            scale = (font_size_px * emoji_scale) / float(eh)
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
            try:
                file_url = Path(video_path).resolve().as_uri()
            except Exception:
                file_url = str(video_path)
            link_text = f'<link href="{file_url}">{video_name}</link>'
            self.elements.append(Paragraph(link_text, self.styles["VideoLink"]))
    
    def _add_step(self, step: dict, step_number: int):
        """Add a step to the PDF."""
        step_data = step["data"]
        photos = step["photos"]
        videos = step["videos"]
        
        # Collect flowables for this step, then add as a single unit when possible
        step_flow = []

        # Step title (always as Paragraph for consistent font sizing)
        display_name = step_data.get("display_name", f"Step {step_number}")
        safe_title = f"{step_number}. {display_name}".replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        step_flow.append(Paragraph(safe_title, self.styles["StepTitle"]))

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

        # Meta (always as Paragraph)
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
            # Video header as Paragraph
            step_flow.append(Paragraph("📹 Videos:", self.styles["VideoHeader"]))

            for video_path in videos:
                video_name = video_path.name
                try:
                    file_url = Path(video_path).resolve().as_uri()
                except Exception:
                    file_url = str(video_path)
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


class CacheManager:
    """Manages cache of rendered trips."""
    
    def __init__(self, cache_file: Path):
        self.cache_file = Path(cache_file)
        self.cache = self._load_cache()
    
    def _load_cache(self) -> dict:
        """Load cache from JSON file."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {"rendered_trips": []}
        return {"rendered_trips": []}
    
    def _save_cache(self):
        """Save cache to JSON file."""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save cache: {e}")
    
    def is_rendered(self, trip_path: Path) -> bool:
        """Check if trip has been rendered."""
        return str(trip_path) in self.cache.get("rendered_trips", [])
    
    def mark_rendered(self, trip_path: Path):
        """Mark trip as rendered."""
        trip_str = str(trip_path)
        if trip_str not in self.cache.get("rendered_trips", []):
            self.cache.setdefault("rendered_trips", []).append(trip_str)
            self._save_cache()
    
    def clear_cache(self):
        """Clear all rendered trips from cache."""
        self.cache = {"rendered_trips": []}
        self._save_cache()
    
    def get_rendered_count(self) -> int:
        """Get number of rendered trips."""
        return len(self.cache.get("rendered_trips", []))


def get_trip_start_date(trip_path):
    """Get start date timestamp from trip.json."""
    try:
        with open(trip_path / "trip.json", "r", encoding="utf-8") as f:
            trip_data = json.load(f)
        return trip_data.get("start_date", 0)
    except:
        return 0


def find_trips(bsp_data_folder: Path) -> list:
    """Find all trip folders in BSPData and sort by start date (oldest first)."""
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
    
    # Sort trips by start_date from trip.json (oldest first)
    trips.sort(key=get_trip_start_date)
    
    return trips


def filter_trips_by_date(trips: list, year: int = None, start_date: datetime = None, end_date: datetime = None) -> list:
    """Filter trips by year or date range."""
    if not year and not start_date and not end_date:
        return trips
    
    filtered = []
    for trip in trips:
        trip_start = get_trip_start_date(trip)
        if trip_start == 0:
            continue
        
        trip_date = datetime.fromtimestamp(trip_start)
        
        if year:
            if trip_date.year == year:
                filtered.append(trip)
        elif start_date and end_date:
            if start_date <= trip_date <= end_date:
                filtered.append(trip)
        elif start_date:
            if trip_date >= start_date:
                filtered.append(trip)
        elif end_date:
            if trip_date <= end_date:
                filtered.append(trip)
    
    return filtered


# =============================================================================
# UNIFIED COMMAND PARSING
# =============================================================================

def parse_selection(sel_str: str, total: int) -> list:
    """Parse selection string into 1-based indices within [1, total].

    Supported formats:
    - Single number: "1" -> [1]
    - Range using semicolon: "1;4" -> [1,2,3,4]
    - Multiple items using comma: "1,5,6" -> [1,5,6]
    - 'l' or 'last' = last item
    - 'l-1' or 'last-1' = second to last, etc.

    Note: semicolon (;) is for ranges, comma (,) is for lists.
    """
    sel = sel_str.strip().lower()
    if not sel:
        return []

    # Check if it's a range (contains semicolon but no comma)
    if ';' in sel and ',' not in sel:
        parts = sel.split(';')
        if len(parts) == 2:
            start_part = parts[0].strip()
            end_part = parts[1].strip()
            
            # Resolve start
            start_idx = _resolve_index_token(start_part, total)
            end_idx = _resolve_index_token(end_part, total)
            
            if start_idx is not None and end_idx is not None:
                if start_idx <= end_idx:
                    return [i for i in range(start_idx, end_idx + 1) if 1 <= i <= total]
                else:
                    return [i for i in range(end_idx, start_idx + 1) if 1 <= i <= total]
        return []

    # Otherwise it's a list (comma-separated or single item)
    if ',' in sel:
        parts = [p.strip() for p in sel.split(',') if p.strip()]
    else:
        parts = [sel]

    indices = []
    for p in parts:
        idx = _resolve_index_token(p, total)
        if idx is not None and 1 <= idx <= total:
            indices.append(idx)
    
    return sorted(set(indices))


def _resolve_index_token(token: str, total: int) -> Optional[int]:
    """Resolve a single token to an index. Returns None if invalid."""
    token = token.strip().lower()
    
    # Handle 'l', 'last', 'l-N', 'last-N'
    if token in ('l', 'last'):
        return total
    
    # l-N or last-N
    m = re.match(r'^(l|last)\s*-\s*(\d+)$', token)
    if m:
        off = int(m.group(2))
        return total - off
    
    # Plain number
    if token.isdigit():
        return int(token)
    
    return None


def parse_render_command(cmd_str: str, trips: list, cache_manager: CacheManager) -> dict:
    """Parse a render command string.

    Format: render [flags] [selection]
    Or:     r [flags] [selection]

    Flags:
      -a, --all        Include already rendered trips
      -ur, --unrendered  Only unrendered (use to restrict)
      -y YEAR, --year YEAR  Filter by year
      -d START;END, --date START;END  Date range in dd.mm.yyyy format

    Selection:
      [1]       single trip
      [1;4]     range
      [1,5,6]   list
      l, last   last trip
      l-1       second to last

    Returns dict with keys:
      - 'valid': bool
      - 'error': str (if not valid)
      - 'trips': list of Path (trips to render)
      - 'include_rendered': bool
    """
    result = {
        'valid': False,
        'error': None,
        'trips': [],
        'include_rendered': True,  # default: include rendered trips (use -ur to restrict)
        'year': None,
        'start_date': None,
        'end_date': None,
        'selection': None
    }

    # Remove 'render' or 'r' prefix
    cmd = cmd_str.strip()
    if cmd.lower().startswith('render'):
        cmd = cmd[6:].strip()
    elif cmd.lower().startswith('r ') or cmd.lower() == 'r':
        cmd = cmd[1:].strip()
    else:
        result['error'] = "Command must start with 'render' or 'r'"
        return result

    # Parse flags and selection
    parts = cmd.split()
    i = 0
    selection_str = None
    mode_specified = False  # True if -a or -ur explicitly provided or selection present

    while i < len(parts):
        p = parts[i]

        if p in ('-a', '--all'):
            result['include_rendered'] = True
            mode_specified = True
            i += 1
        elif p in ('-ur', '--unrendered'):
            result['include_rendered'] = False
            mode_specified = True
            i += 1
        elif p in ('-y', '--year'):
            if i + 1 < len(parts):
                try:
                    result['year'] = int(parts[i + 1])
                    mode_specified = True
                    i += 2
                except ValueError:
                    result['error'] = f"Invalid year: {parts[i + 1]}"
                    return result
            else:
                result['error'] = "-y requires a year value"
                return result
        elif p in ('-d', '--date'):
            if i + 1 < len(parts):
                date_token = parts[i + 1]
                mode_specified = True

                # Support "-d 01.01.2025;01.06.2025" or "-d 01.01.2025; 01.06.2025"
                if ';' in date_token:
                    if date_token.endswith(';') and i + 2 < len(parts):
                        date_token = f"{date_token}{parts[i + 2]}"
                        advance = 3
                    else:
                        advance = 2

                    date_parts = date_token.split(';', 1)
                    if len(date_parts) == 2 and date_parts[0].strip() and date_parts[1].strip():
                        try:
                            result['start_date'] = datetime.strptime(date_parts[0].strip(), "%d.%m.%Y")
                            result['end_date'] = datetime.strptime(date_parts[1].strip(), "%d.%m.%Y")
                            i += advance
                        except ValueError:
                            result['error'] = "Invalid date format. Use dd.mm.yyyy;dd.mm.yyyy"
                            return result
                    else:
                        result['error'] = "Date range must be START;END"
                        return result

                # Support "-d 01.01.2025 01.06.2025" (separate tokens)
                elif i + 2 < len(parts):
                    try:
                        result['start_date'] = datetime.strptime(date_token.strip(), "%d.%m.%Y")
                        result['end_date'] = datetime.strptime(parts[i + 2].strip(), "%d.%m.%Y")
                        i += 3
                    except ValueError:
                        result['error'] = "Invalid date format. Use dd.mm.yyyy dd.mm.yyyy"
                        return result
                else:
                    result['error'] = "Date range must be START;END"
                    return result
            else:
                result['error'] = "-d requires a date range"
                return result
        else:
            # Not a flag, must be selection
            # Collect remaining parts as selection
            selection_str = ' '.join(parts[i:])
            mode_specified = True
            break

    # Apply date/year filter to trips
    filtered_trips = filter_trips_by_date(
        trips,
        result['year'],
        result['start_date'],
        result['end_date']
    )

    # Apply rendered filter (default: unrendered only)
    if not result['include_rendered']:
        filtered_trips = [t for t in filtered_trips if not cache_manager.is_rendered(t)]

    if not filtered_trips:
        result['error'] = "No trips match the specified filters"
        return result

    # Apply selection
    if selection_str:
        result['selection'] = selection_str
        indices = parse_selection(selection_str, len(filtered_trips))
        if not indices:
            result['error'] = f"Invalid selection: {selection_str}"
            return result
        result['trips'] = [filtered_trips[i - 1] for i in indices]
    else:
        # No selection provided: require explicit mode (-a or -ur)
        if not mode_specified:
            result['error'] = "No selection or mode specified. Use a selection (e.g., '1;4') or flags '-a' or '-ur'."
            return result
        result['trips'] = filtered_trips

    result['valid'] = True
    return result


def display_trips(trips: list, cache_manager: CacheManager, title: str = "Available trips"):
    """Display a numbered list of trips with rendered status."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    print(f"Total: {len(trips)} | Rendered: {cache_manager.get_rendered_count()}\n")

    for i, trip in enumerate(trips, 1):
        try:
            with open(trip / "trip.json", "r", encoding="utf-8") as f:
                trip_data = json.load(f)
            name = trip_data.get("name", trip.name)
            start_ts = trip_data.get("start_date", 0)
            date_str = datetime.fromtimestamp(start_ts).strftime("%d.%m.%Y") if start_ts else "?"
            rendered_mark = "✓" if cache_manager.is_rendered(trip) else " "
            print(f"  [{i:2d}] [{rendered_mark}] {name} ({date_str})")
        except:
            rendered_mark = "✓" if cache_manager.is_rendered(trip) else " "
            print(f"  [{i:2d}] [{rendered_mark}] {trip.name}")
    print()


def print_command_help():
    """Print available commands."""
    print(f"\n{'='*70}")
    print("  AVAILABLE COMMANDS")
    print(f"{'='*70}")
    print("""
  cancel        - Exit the program
  clear-cache   - Clear rendered trips cache
  stop          - During rendering: type 'stop' + Enter to abort
  trips         - Show all trips

  render [flags] [selection]   (or 'r' for short)
    Flags:
      -a, --all           Include already rendered trips (redundant; default includes rendered)
      -ur, --unrendered   Only unrendered trips (use to restrict)
      -y YEAR             Filter by year (e.g., -y 2025)
      -d START;END        Date range (dd.mm.yyyy;dd.mm.yyyy)

    Selection formats:
      1           Single trip
      1;4         Range of trips (1 to 4)
      1,5,6       Multiple trips
      l or last   Last trip
      l-1         Second to last trip

  Examples:
    # Always provide either a selection or -a/-ur
    r -a                      Render all trips (including rendered)
    r -ur -y 2025             Render unrendered trips from 2025
    r -d 01.01.2025;01.06.2025 -ur   Render trips in date range (only unrendered)
    r 1;4                     Render trips 1 through 4
    r -a l                    Render last trip (even if rendered)
    r 1,3,5                   Render trips 1, 3, and 5
""")
    print(f"{'='*70}\n")


def prompt_loop(trips: list, cache_manager: CacheManager, script_dir: Path, config: dict):
    """Unified command prompt loop."""
    import sys
    
    # Display help and trips on start (show trips before first render)
    print_command_help()
    display_trips(trips, cache_manager, "POLARSTEPS PDF GENERATOR")

    # Dedicated input reader thread to avoid losing commands typed during rendering
    input_queue = queue.Queue()
    deferred_commands = deque()

    def input_reader():
        while True:
            try:
                line = input()
            except EOFError:
                line = "cancel"
            input_queue.put(line)

    input_thread = threading.Thread(target=input_reader, daemon=True)
    input_thread.start()

    while True:
        try:
            if deferred_commands:
                cmd = deferred_commands.popleft()
            else:
                if input_queue.empty():
                    print("Command> ", end="", flush=True)
                cmd = input_queue.get()

            cmd = cmd.strip()
            if not cmd:
                continue
            
            cmd_lower = cmd.lower()
            
            # Exit commands
            if cmd_lower in ('cancel', 'exit', 'quit', 'q'):
                print("Exiting.")
                break
            
            # Clear cache
            if cmd_lower == 'clear-cache':
                confirm = input("⚠️  Clear all rendered marks? (yes/no): ").strip().lower()
                if confirm in ('yes', 'y'):
                    cache_manager.clear_cache()
                    print("✅ Cache cleared!")
                else:
                    print("Cancelled.")
                continue
            
            # Help
            if cmd_lower in ('help', 'h', '?'):
                print_command_help()
                continue
            
            # List/refresh / show all trips
            if cmd_lower in ('list', 'ls', 'trips', 'll'):  # 'll' for list, but 'l' is last
                display_trips(trips, cache_manager)
                continue
            
            # Render command
            if cmd_lower.startswith('render') or cmd_lower.startswith('r ') or cmd_lower == 'r':
                result = parse_render_command(cmd, trips, cache_manager)

                if not result['valid']:
                    # If the only error is missing selection/mode, offer to render ALL
                    if result['error'] and 'No selection or mode specified' in result['error']:
                        user_choice = input("No selection or mode given. Render ALL trips? (yes/no) or enter a different command: ").strip()
                        if not user_choice:
                            print("Cancelled. Returning to command prompt.")
                            continue
                        lc = user_choice.lower()
                        if lc in ('y', 'yes'):
                            # Re-parse using explicit -a to include rendered
                            cmd = 'r -a'
                            result = parse_render_command(cmd, trips, cache_manager)
                            if not result['valid']:
                                print(f"❌ Error: {result['error']}")
                                continue
                        elif lc in ('n', 'no'):
                            print("Cancelled. Returning to command prompt.")
                            continue
                        else:
                            # Treat the user's input as a new command and process it
                            cmd = user_choice
                            continue
                    else:
                        print(f"❌ Error: {result['error']}")
                        continue

                trips_to_render = result['trips']
                print(f"\n📋 Will render {len(trips_to_render)} trip(s):")
                for i, trip in enumerate(trips_to_render, 1):
                    try:
                        with open(trip / "trip.json", "r", encoding="utf-8") as f:
                            trip_data = json.load(f)
                        name = trip_data.get("name", trip.name)
                        print(f"  [{i}] {name}")
                    except:
                        print(f"  [{i}] {trip.name}")

                # Start rendering immediately
                print(f"\n💡 Type 'stop' + Enter to abort after current trip.\n")

                # Setup stop mechanism
                stop_flag = threading.Event()

                def check_stop():
                    return stop_flag.is_set()

                def drain_input_for_stop():
                    while True:
                        try:
                            user_input = input_queue.get_nowait()
                        except queue.Empty:
                            break

                        user_input = user_input.strip()
                        if not user_input:
                            continue
                        if user_input.lower() == 'stop':
                            print("\n⚠️  Stop signal received. Finishing current trip...")
                            stop_flag.set()
                        else:
                            deferred_commands.append(user_input)

                # Render
                success_count = 0
                stopped = False
                for i, trip in enumerate(trips_to_render, 1):
                    drain_input_for_stop()
                    if stop_flag.is_set():
                        stopped = True
                        break

                    print(f"\n{'='*70}")
                    print(f"[{i}/{len(trips_to_render)}]", end=" ")
                    if render_trip(trip, script_dir, config, cache_manager, check_stop):
                        success_count += 1
                    drain_input_for_stop()

                # Summary
                print()
                print('=' * 70)
                if stopped:
                    print(f"⏹️  Stop requested. Completed: {success_count}/{len(trips_to_render)} trip(s) rendered.")
                else:
                    print(f"✅ Completed! {success_count}/{len(trips_to_render)} trip(s) rendered.")
                print('=' * 70)
                print()

                # After rendering: do not automatically show trips (use 'trips' to view)
                print("Type 'trips' to view the list of available trips.")
                continue
        except KeyboardInterrupt:
            print("\nExiting.")
            break
        except EOFError:
            print("\nExiting.")
            break

    def select_trip(trips: list, cache_manager: CacheManager, show_rendered: bool = True) -> Optional[Path]:
        """Let user select a trip from the console."""
        if not trips:
            print("No trips found!")
            return None

        # Filter trips based on show_rendered setting
        display_trips = trips if show_rendered else [t for t in trips if not cache_manager.is_rendered(t)]

        if not display_trips:
            print("No trips to display with current filter!")
            return None

        print("\n" + "=" * 70)
        print("  POLARSTEPS PDF GENERATOR")
        print("=" * 70)
        print(f"\nShowing: {'All trips' if show_rendered else 'Only unrendered trips'}")
        print(f"Total trips: {len(display_trips)} | Rendered: {cache_manager.get_rendered_count()}\n")
        print("Available trips:\n")

        for i, trip in enumerate(display_trips, 1):
            # Load trip name from trip.json
            try:
                with open(trip / "trip.json", "r", encoding="utf-8") as f:
                    trip_data = json.load(f)
                name = trip_data.get("name", trip.name)
                total_km = trip_data.get("total_km", 0)
                step_count = trip_data.get("step_count", 0)
                start_ts = trip_data.get("start_date", 0)
                date_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d") if start_ts else "?"

                rendered_mark = "✓" if cache_manager.is_rendered(trip) else " "
                print(f"  [{i:2d}] [{rendered_mark}] {name} ({date_str})")
                print(f"       {step_count} steps • {total_km:.0f} km")
                print()
            except Exception:
                rendered_mark = "✓" if cache_manager.is_rendered(trip) else " "
                print(f"  [{i:2d}] [{rendered_mark}] {trip.name}")
                print()

        print("\n" + "=" * 70)
        print("Commands:")
        print("  [1-99]       Select and render a specific trip")
        print("  [t]          Toggle show/hide rendered trips")
        print("  [r]          Render all unrendered trips")
        print("  [ra]         Render all trips (including rendered)")
        print("  [c]          Clear cache (remove all rendered marks)")
        print("  [0]          Exit")
        print("=" * 70)
        print()

        while True:
            try:
                choice = input("Select option: ").strip().lower()

                if choice == "0":
                    return None
                elif choice == "t":
                    return "TOGGLE"
                elif choice == "r":
                    return "RENDER_UNRENDERED"
                elif choice == "ra":
                    return "RENDER_ALL"
                elif choice == "c":
                    return "CLEAR_CACHE"
                else:
                    idx = int(choice) - 1
                    if 0 <= idx < len(display_trips):
                        return display_trips[idx]
                    else:
                        print("Invalid selection. Try again.")
            except ValueError:
                print("Invalid input. Please enter a number or command.")
            except KeyboardInterrupt:
                return None


def render_trip(trip_path: Path, script_dir: Path, config: dict, cache_manager: CacheManager, check_stop=None) -> bool:
    """Render a single trip to PDF. Returns True if successful, False if error or stopped."""
    try:
        # Check for stop signal
        if check_stop and check_stop():
            print("  ⏹️  Stopped by user")
            return False
        
        print(f"\nProcessing trip: {trip_path.name}")
        
        # Parse trip
        parser = TripParser(trip_path)
        parser.load()
        
        print(f"  Trip: {parser.get_trip_name()}")
        print(f"  Steps: {len(parser.steps)}")
        print(f"  Total km: {parser.get_total_km():.0f}")
        
        # Generate PDF
        trip_name_safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in parser.get_trip_name())
        pdfs_dir = script_dir / "TripPdfs"
        try:
            pdfs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pdfs_dir = trip_path.parent

        output_path = pdfs_dir / f"{trip_name_safe}.pdf"
        
        map_gen = MapGenerator(default_zoom=int(config.get("default_map_zoom", 12)))
        pdf_builder = PDFBuilder(output_path, parser, map_gen, config=config)
        pdf_builder.build()
        
        # Mark as rendered
        cache_manager.mark_rendered(trip_path)
        
        print(f"  ✅ Done! PDF saved to: {output_path}")
        return True
    except Exception as e:
        print(f"  ❌ Error rendering trip: {e}")
        return False


def get_date_filter_from_user() -> tuple:
    """Ask user for date filter (year or date range). Returns (year, start_date, end_date)."""
    print("\nDate filter options:")
    print("  [1] Filter by year")
    print("  [2] Filter by date range")
    print("  [3] No filter (all trips)")
    
    while True:
        try:
            choice = input("Select filter option: ").strip()
            
            if choice == "1":
                year = int(input("Enter year (e.g., 2025): ").strip())
                return (year, None, None)
            elif choice == "2":
                start_str = input("Enter start date (YYYY-MM-DD): ").strip()
                end_str = input("Enter end date (YYYY-MM-DD): ").strip()
                start_date = datetime.strptime(start_str, "%Y-%m-%d") if start_str else None
                end_date = datetime.strptime(end_str, "%Y-%m-%d") if end_str else None
                return (None, start_date, end_date)
            elif choice == "3":
                return (None, None, None)
            else:
                print("Invalid choice. Please enter 1, 2, or 3.")
        except ValueError as e:
            print(f"Invalid input: {e}. Please try again.")
        except KeyboardInterrupt:
            return (None, None, None)


def main():
    """Main entry point."""
    import sys
    
    parser = argparse.ArgumentParser(
        description='Polarsteps PDF Generator - Render travel journals from Polarsteps data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Available commands (at the prompt):
  cancel        Exit the program
  clear-cache   Clear rendered trips cache
  stop          During rendering: type 'stop' + Enter to abort

  render [flags] [selection]   (or 'r' for short)
    Flags:
      -a, --all           Include already rendered trips (redundant; default includes already rendered)
      -ur, --unrendered   Only unrendered trips (use to restrict)
      -y YEAR             Filter by year (e.g., -y 2025)
      -d START;END        Date range (dd.mm.yyyy;dd.mm.yyyy)

    Selection:
      1           Single trip
      1;4         Range of trips (1 to 4)
      1,5,6       Multiple trips
      l or last   Last trip
      l-1         Second to last trip

Examples:
  # Always provide either a selection or -a/-ur
  r -a                Render all trips (including rendered)
  r -ur -y 2025       Render unrendered trips from 2025
  r -d 01.01.2025;01.06.2025 -ur   Render trips in date range (only unrendered)
  r 1;4               Render trips 1 through 4
  r -a l              Render last trip (even if rendered)
  r 1,3,5             Render trips 1, 3, and 5
        ''')
    
    parser.add_argument('bsp_folder', nargs='?', help='Path to BSPData folder (optional)')
    parser.add_argument('--clear-cache', action='store_true', help='Clear the rendered trips cache and exit')
    
    args = parser.parse_args()
    
    # Determine BSPData folder
    if args.bsp_folder:
        bsp_data_folder = Path(args.bsp_folder)
    else:
        script_dir = Path(__file__).parent
        bsp_data_folder = script_dir / "BSPData"
        
        if not bsp_data_folder.exists():
            bsp_data_folder = Path.cwd() / "BSPData"
    
    if not bsp_data_folder.exists():
        print(f"Error: BSPData folder not found at {bsp_data_folder}")
        print("Usage: python polarsteps_pdf_generator.py [path/to/BSPData]")
        sys.exit(1)
    
    script_dir = Path(__file__).parent
    
    # Load config
    config_path = script_dir / "config.json"
    config = {}
    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as cf:
                config = json.load(cf)
    except Exception:
        config = {}
    
    # Initialize cache manager
    cache_file = script_dir / "rendered_trips_cache.json"
    cache_manager = CacheManager(cache_file)
    
    # Handle clear cache from CLI
    if args.clear_cache:
        print("Clearing cache...")
        cache_manager.clear_cache()
        print("✅ Cache cleared!")
        return
    
    print(f"Scanning for trips in: {bsp_data_folder}")
    
    # Find all trips
    trips = find_trips(bsp_data_folder)
    
    if not trips:
        print("No trips found in BSPData folder.")
        sys.exit(1)
    
    print(f"Found {len(trips)} trip(s)\n")
    
    # Enter the unified prompt loop
    prompt_loop(trips, cache_manager, script_dir, config)


if __name__ == "__main__":
    main()
