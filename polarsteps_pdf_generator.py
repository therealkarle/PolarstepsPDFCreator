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
# Optional TOML loader (tomllib for Python 3.11+, fallback to the 'toml' package)
try:
    import tomllib as _tomllib
except Exception:
    try:
        import toml as _tomllib
    except Exception:
        _tomllib = None

import threading
import queue
import html
import hashlib
import requests
import os
import sys
import subprocess
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

        def _clean_text(value: str) -> str:
            if value is None:
                return ""
            text = str(value).replace("\r\n", "\n").replace("\r", "\n")
            text = "\n".join(line.rstrip() for line in text.split("\n"))
            return text.strip()

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
                if isinstance(data, dict):
                    data["description"] = _clean_text(data.get("description", ""))
                    data["display_name"] = _clean_text(data.get("display_name", data.get("name", ""))) or data.get("name")
                self.steps.append({"data": data, "photos": photos, "videos": videos})
            return

        # Fallback: use all_steps from trip.json (common export format)
        if isinstance(self.trip_data.get("all_steps"), list) and self.trip_data.get("all_steps"):
            # Try to match local step folders to attach photos/videos when available
            trip_children = [c for c in sorted(self.trip_path.iterdir()) if c.is_dir()]
            for s in self.trip_data.get("all_steps", []):
                data = s if isinstance(s, dict) else {}
                # Normalize location field
                loc = data.get("location") if isinstance(data, dict) else None
                if isinstance(loc, dict):
                    data["location"] = loc
                if isinstance(data, dict):
                    data["description"] = _clean_text(data.get("description", ""))
                    data["display_name"] = _clean_text(data.get("display_name", data.get("name", ""))) or data.get("name")

                # Attempt to find a matching local folder by slug/display_slug/display_name
                photos = []
                videos = []
                slug = (data.get("slug") or data.get("display_slug") or "").lower()
                display = (data.get("display_name") or "").lower().replace(" ", "-")

                candidate = None
                for c in trip_children:
                    name = c.name.lower()
                    if slug and slug in name:
                        candidate = c
                        break
                    if display and display in name:
                        candidate = c
                        break
                # If we found a folder, look for photos/videos inside
                if candidate:
                    photos_dir = candidate / "photos"
                    videos_dir = candidate / "videos"
                    if photos_dir.exists() and photos_dir.is_dir():
                        for ext in ("*.jpg", "*.jpeg", "*.png"):
                            photos.extend(sorted(photos_dir.glob(ext)))
                    if videos_dir.exists() and videos_dir.is_dir():
                        for ext in ("*.mp4", "*.mov", "*.mkv"):
                            videos.extend(sorted(videos_dir.glob(ext)))

                # Fallback: attempt to use a trip-level photos folder named like step
                if not photos:
                    for c in trip_children:
                        if c.name.lower().startswith("photo") and c.is_dir():
                            for ext in ("*.jpg", "*.jpeg", "*.png"):
                                for p in sorted(c.glob(ext)):
                                    # naive heuristic: include first N photos
                                    photos.append(p)
                                    if len(photos) >= 6:
                                        break
                                if len(photos) >= 6:
                                    break
                            if photos:
                                break

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
    from staticmap import StaticMap, CircleMarker, Line, IconMarker
except Exception:
    StaticMap = None
    CircleMarker = None
    Line = None
    IconMarker = None

# ESRI World Imagery tile template (Satellite)
ESRI_SATELLITE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
# ESRI World Street Map tile template (Road)
ESRI_ROAD_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"
# ESRI Reference labels (transparent overlay for hybrid-style maps)
ESRI_LABELS_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
# Map colors
ROUTE_COLOR = "#FFFFFF"  # white
# Outline color/width for the route line to ensure visibility over satellite tiles
ROUTE_OUTLINE_COLOR = "#000000"  # black
ROUTE_OUTLINE_WIDTH = 5
ROUTE_LINE_WIDTH = 3
MARKER_COLOR_START = "#1A5F7A"  # teal
MARKER_COLOR_STEP = "#4ECDC4"  # lighter teal

# Color for steps missing a photo
MISSING_PHOTO_COLOR = "#FF4D4F"  # red

# Emoji regex (captures sequences including ZWJ/FE0F)
EMOJI_PATTERN = re.compile(
    r'([\U0001F1E6-\U0001F1FF\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\u2600-\u26FF\u2700-\u27BF\u200d\ufe0f]+)',
    flags=re.UNICODE
)

# ReportLab: page sizes, units, styles and flowables
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import Paragraph, Image as RLImage, Table, TableStyle, Spacer, SimpleDocTemplate, PageBreak, KeepTogether, ListFlowable, ListItem
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Pillow (PIL) for image processing
from PIL import Image, ImageDraw, ImageFont, ImageOps


class MapGenerator:
    """Generates static maps using ESRI World Imagery tiles.

    Config keys used:
      - default_map_zoom: default zoom level (integer)
      - min_map_zoom: minimum zoom level
      - max_map_zoom: maximum zoom level
      - map_render_scale: render maps at higher pixel density (float)
      - marker_thumb_size: base marker thumbnail size in pixels
    """

    def __init__(self, width: int = 800, height: int = 600, default_zoom: int = 12, min_zoom: int = 6, max_zoom: int = 16, render_scale: float = 1.0, marker_thumb_size: int = 40, url_template: str = ESRI_SATELLITE_URL, label_overlay_url: str = None, label_overlay_opacity: float = 0.7):
        self.width = width
        self.height = height
        self.default_zoom = default_zoom
        self.min_zoom = min_zoom
        self.max_zoom = max_zoom
        self.render_scale = max(1.0, float(render_scale))
        self.url_template = url_template
        self.label_overlay_url = label_overlay_url
        self.label_overlay_opacity = float(label_overlay_opacity) if label_overlay_opacity is not None else 0.7
        self._tile_cache = {}
        # maximum thumbnail size used for markers
        self.marker_thumb_size = marker_thumb_size
        # how many zoom levels to step out for step maps (helps to show prev/current/next comfortably)
        # Default to 0 to avoid unnecessarily zooming out when rendering high-res step maps
        self.step_map_zoom_out = 0
        # padding fraction around computed bounds for step maps (smaller by default to avoid wide zoom-out)
        self.step_map_padding = 0.06

        # Automatically tighten step-map padding for trips with many steps (reduces padding -> more zoomed-in)
        self.step_map_auto_tighten = True
        # Scales to reduce padding when trips have many steps (smaller scale => tighter crop)
        # Applied per thresholds: small (21-40), medium (41-80), large (>80)
        self.step_map_tighten_scale_small = 0.8   # 21-40 steps
        self.step_map_tighten_scale_medium = 0.6  # 41-80 steps
        self.step_map_tighten_scale_large = 0.5   # >80 steps

        # Limit how far prev/next neighbors can be for fitting; helps avoid huge zoom-out on long trips
        self.step_map_neighbor_max_km = 180.0
        # Only apply neighbor distance limiting when trips have at least this many steps (0=always)
        self.step_map_neighbor_limit_steps_threshold = 20

        # Cap the absolute padding applied on step maps (km); keeps far neighbors from forcing huge pads
        self.step_map_max_pad_km = 25.0

        # overview map padding fraction around trip bounds
        self.overview_map_padding = 0.06
        # Minimum padding in pixels for overview padding to be considered visible when forcing zoom-out
        self.overview_min_pad_px = 12
        # Use this to print debug info about computed pads/zoom when True
        self.debug_map = False

        # Step-map horizontal span constraints (km) to avoid over/under zooming.
        # These are soft constraints: we will never crop out prev/current/next.
        self.step_map_min_width_km = 12.0
        self.step_map_max_width_km = 120.0

        # If adjacent steps are within this radius of the current step, treat them as same-location
        # and skip to the next distinct step for fitting.
        self.step_cluster_radius_km = 4.0

        # Weighting for centering (current step is favored)
        self.step_center_weight_current = 2.0
        self.step_center_weight_other = 1.0

    @staticmethod
    def _lonlat_to_pixel(lon: float, lat: float, zoom: int, tile_size: int = 256) -> tuple:
        """Convert lon/lat to global pixel coordinates for the given zoom (Web Mercator)."""
        import math
        lat = max(-85.05112878, min(85.05112878, float(lat)))
        lon = float(lon)
        n = 2 ** int(zoom)
        x = (lon + 180.0) / 360.0 * tile_size * n
        sin_lat = math.sin(math.radians(lat))
        y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * tile_size * n
        return (x, y)

    def _render_label_overlay(self, width_px: int, height_px: int, zoom: int, center: tuple, url_template: str, opacity: float = 0.7) -> Optional[Image.Image]:
        """Render a transparent overlay image from label tiles."""
        if not url_template:
            return None
        try:
            zoom = int(zoom)
            center_lon, center_lat = center
            center_px = self._lonlat_to_pixel(center_lon, center_lat, zoom)
            left_px = center_px[0] - (width_px / 2.0)
            top_px = center_px[1] - (height_px / 2.0)

            tile_size = 256
            world_tiles = 2 ** zoom
            x_start = int((left_px) // tile_size)
            y_start = int((top_px) // tile_size)
            x_end = int((left_px + width_px - 1) // tile_size)
            y_end = int((top_px + height_px - 1) // tile_size)

            overlay = Image.new("RGBA", (int(width_px), int(height_px)), (0, 0, 0, 0))
            for ty in range(y_start, y_end + 1):
                if ty < 0 or ty >= world_tiles:
                    continue
                for tx in range(x_start, x_end + 1):
                    tx_wrapped = tx % world_tiles
                    url = url_template.format(z=zoom, x=tx_wrapped, y=ty)

                    tile = None
                    try:
                        tile = self._tile_cache.get(url)
                        if tile is None:
                            r = requests.get(url, timeout=6)
                            if r.status_code == 200:
                                tile = Image.open(io.BytesIO(r.content)).convert("RGBA")
                                self._tile_cache[url] = tile
                    except Exception:
                        tile = None

                    if tile is None:
                        continue

                    if opacity < 1.0:
                        alpha = tile.split()[-1].point(lambda a: int(a * opacity))
                        tile = tile.copy()
                        tile.putalpha(alpha)

                    px = int(tx * tile_size - left_px)
                    py = int(ty * tile_size - top_px)
                    overlay.alpha_composite(tile, dest=(px, py))

            return overlay
        except Exception:
            return None

    def _apply_label_overlay(self, base_image: Image.Image, zoom: int, center: tuple) -> Image.Image:
        if not self.label_overlay_url:
            return base_image
        try:
            overlay = self._render_label_overlay(base_image.width, base_image.height, zoom, center, self.label_overlay_url, self.label_overlay_opacity)
            if overlay is None:
                return base_image
            base = base_image.convert("RGBA")
            base.alpha_composite(overlay)
            return base
        except Exception:
            return base_image


    @staticmethod
    def _extract_lon_lat(step: dict) -> Optional[tuple]:
        """Return (lon, lat) from a step dict, or None if unavailable."""
        try:
            loc = step.get("data", {}).get("location", {}) if isinstance(step, dict) else {}
            if not loc:
                return None
            lat = loc.get("lat") or loc.get("latitude") or loc.get("Latitude")
            lon = loc.get("lon") or loc.get("lng") or loc.get("longitude") or loc.get("Longitude")
            lat = float(lat) if lat is not None else None
            lon = float(lon) if lon is not None else None
            if lat is None or lon is None:
                return None
            return (lon, lat)
        except Exception:
            return None

    @staticmethod
    def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        """Great-circle distance in kilometers."""
        import math

        r = 6371.0088
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
        return r * c

    def _step_has_photo(self, step: dict) -> bool:
        """Return True if the step likely has a usable photo (local or URL)."""
        try:
            photos = step.get('photos', []) if isinstance(step, dict) else []
            for p in photos:
                try:
                    pp = Path(p)
                    if pp.exists():
                        return True
                except Exception:
                    continue
            data = step.get('data', {}) if isinstance(step, dict) else {}
            for key in ("cover_photo", "cover_photo_path", "cover_photo_thumb_path", "main_media_item_path", "cover_photo_url"):
                val = data.get(key) if isinstance(data, dict) else None
                if isinstance(val, dict) and val.get('path'):
                    return True
                if isinstance(val, str) and val:
                    # treat any non-empty string as a photo reference (URL/path)
                    return True
        except Exception:
            return False
        return False

    def _find_distinct_neighbor_index(self, trip_parser: TripParser, step_index: int, direction: int) -> Optional[int]:
        """Find previous/next step index with coords outside cluster radius.

        direction: -1 for previous, +1 for next
        """
        if direction not in (-1, 1):
            return None
        if not (0 <= step_index < len(trip_parser.steps)):
            return None

        current = self._extract_lon_lat(trip_parser.steps[step_index])
        if not current:
            return None

        best_fallback = None
        cur_lon, cur_lat = current
        i = step_index + direction
        while 0 <= i < len(trip_parser.steps):
            coord = self._extract_lon_lat(trip_parser.steps[i])
            if coord:
                best_fallback = i
                lon, lat = coord
                try:
                    dist = self._haversine_km(cur_lon, cur_lat, lon, lat)
                except Exception:
                    dist = None
                if dist is not None and dist >= float(self.step_cluster_radius_km):
                    return i
            i += direction

        return best_fallback

    def _zoom_for_horizontal_km(self, width_km: float, center_lat: float, width_px: int, *, prefer: str = "at_least") -> int:
        """Compute an integer zoom that targets a horizontal map width in km.

        prefer:
          - "at_least": returns a zoom where view-width is >= width_km (safe, may include more area)
          - "at_most": returns a zoom where view-width is <= width_km (tighter, may crop if used without fit-check)
        """
        import math

        width_km = max(0.1, float(width_km))
        center_lat = float(center_lat)
        cos_lat = max(0.01, abs(math.cos(math.radians(center_lat))))
        km_per_deg_lon = 111.32 * cos_lat
        width_deg = width_km / km_per_deg_lon
        dpp = width_deg / float(max(width_px, 1))
        if dpp <= 0:
            return self.default_zoom
        z = math.log2(360.0 / (256.0 * dpp))
        if prefer == "at_most":
            return int(math.ceil(z))
        return int(z)

    def _view_half_spans_deg(self, zoom: int, center_lat: float, width_px: int, height_px: int) -> tuple:
        """Approximate (half_lon_span_deg, half_lat_span_deg) for the given zoom and center latitude."""
        import math

        z = int(zoom)
        dpp_lon = 360.0 / (256.0 * (2 ** z))
        half_lon = dpp_lon * (float(width_px) / 2.0)

        cos_lat = max(0.01, abs(math.cos(math.radians(float(center_lat)))))
        dpp_lat = dpp_lon * cos_lat
        half_lat = dpp_lat * (float(height_px) / 2.0)
        return half_lon, half_lat

    def _weighted_center(self, points: list) -> Optional[tuple]:
        """points: list of (lon, lat, weight). Returns (lon, lat) or None."""
        if not points:
            return None
        s_w = 0.0
        s_lon = 0.0
        s_lat = 0.0
        for lon, lat, w in points:
            try:
                w = float(w)
                s_w += w
                s_lon += float(lon) * w
                s_lat += float(lat) * w
            except Exception:
                continue
        if s_w <= 0:
            return None
        return (s_lon / s_w, s_lat / s_w)

    def _clamp_center_to_bounds(self, center: tuple, zoom: int, bounds: tuple, width_px: int, height_px: int) -> tuple:
        """Clamp center so that bounds stay within viewport at the given zoom.

        bounds: (min_lon, max_lon, min_lat, max_lat)
        """
        min_lon, max_lon, min_lat, max_lat = bounds
        cen_lon, cen_lat = center

        # Use current center_lat for half-span estimation; this is a good-enough clamp.
        half_lon, half_lat = self._view_half_spans_deg(zoom, cen_lat, width_px, height_px)
        if (max_lon - min_lon) <= 2 * half_lon:
            lo = max_lon - half_lon
            hi = min_lon + half_lon
            if lo <= hi:
                cen_lon = max(lo, min(hi, cen_lon))
        if (max_lat - min_lat) <= 2 * half_lat:
            lo = max_lat - half_lat
            hi = min_lat + half_lat
            if lo <= hi:
                cen_lat = max(lo, min(hi, cen_lat))

        return (cen_lon, cen_lat)


    def _is_tile_available(self, url_template: str) -> bool:
        """Quickly check whether a tile can be retrieved from the given URL template."""
        try:
            test_url = url_template.format(z=max(2, int(self.default_zoom)), x=1, y=1)
            r = requests.get(test_url, timeout=5)
            ctype = r.headers.get("content-type", "")
            return r.status_code == 200 and ctype.startswith("image")
        except Exception:
            return False

    def _create_map(self, width: int = None, height: int = None) -> "object":
        """Create a StaticMap with configured tiles. If the configured tile provider
        is unavailable, fall back to road tiles to keep map generation working."""
        if StaticMap is None:
            raise RuntimeError("staticmap not available: install the 'staticmap' package to enable map generation")
        w = int(round((width or self.width) * self.render_scale))
        h = int(round((height or self.height) * self.render_scale))

        url = self.url_template
        # If satellite/hybrid fails, try road tiles as a fallback (keeps generation usable)
        if url == ESRI_SATELLITE_URL and not self._is_tile_available(url):
            print("Warning: Satellite tiles not available; falling back to road tiles for this run.")
            url = ESRI_ROAD_URL

        return StaticMap(
            w, h,
            url_template=url,
            tile_size=256
        )

    def generate_overview_map(self, trip_parser: TripParser) -> bytes:
        """Generate overview map with route and step markers."""
        m = self._create_map()

        # Add route line (white only for overview; outline omitted to keep map clean)
        route_coords = trip_parser.get_route_coordinates()
        if len(route_coords) > 1:
            line = Line(route_coords, ROUTE_COLOR, ROUTE_LINE_WIDTH)
            m.add_line(line)

        # Add step markers (use photo thumbnails when possible)
        for i, step in enumerate(trip_parser.steps):
            step_data = step["data"]
            location = step_data.get("location", {})

            if location:
                lat = location.get("lat") or location.get("latitude") or location.get("Latitude")
                lon = location.get("lon") or location.get("lng") or location.get("longitude") or location.get("Longitude")

                try:
                    lat = float(lat) if lat is not None else None
                    lon = float(lon) if lon is not None else None
                except Exception:
                    lat = None
                    lon = None

                if lat is not None and lon is not None:
                    # create thumbnail (white ring); prefer IconMarker when available
                    marker_px = max(8, int(round(self.marker_thumb_size * self.render_scale)))
                    thumb = self._get_step_thumbnail(step, size=marker_px, ring_color=(255,255,255,230))
                    if thumb and IconMarker is not None:
                        # IconMarker offsets are relative to the left-bottom of the image; use half size to center
                        off_x = int(marker_px / 2)
                        off_y = int(marker_px / 2)
                        try:
                            m.add_marker(IconMarker((lon, lat), str(thumb), off_x, off_y))
                            continue
                        except Exception:
                            pass

                    # fallback to circle marker (no red in overview map)
                    color = MARKER_COLOR_START if i == 0 else MARKER_COLOR_STEP
                    marker_radius = max(4, int(round(12 * self.render_scale)))
                    m.add_marker(CircleMarker((lon, lat), color, marker_radius))

            # Gather coords (route preferred, fallback to steps)
            coords = route_coords
            if not coords:
                coords = [c for c in (self._extract_lon_lat(s) for s in trip_parser.steps) if c]

            if not coords:
                # nothing to show
                image = m.render()
                img_bytes = io.BytesIO()
                image.save(img_bytes, format="PNG")
                img_bytes.seek(0)
                return img_bytes.getvalue()

            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            min_lon, max_lon = min(lons), max(lons)
            min_lat, max_lat = min(lats), max(lats)

            # Try percent-of-maximum-distance padding when configured (smaller, adaptive)
            overview_pct = float(getattr(self, "overview_padding_percent", 0.0) or 0.0)
            # Prefer computing max distance between STEPS (user expectation); fallback to coords if needed
            step_coords = [self._extract_lon_lat(s) for s in trip_parser.steps]
            step_coords = [c for c in step_coords if c]
            if overview_pct and len(step_coords) > 1:
                max_km = 0.0
                for i in range(len(step_coords)):
                    for j in range(i + 1, len(step_coords)):
                        try:
                            d = self._haversine_km(step_coords[i][0], step_coords[i][1], step_coords[j][0], step_coords[j][1])
                        except Exception:
                            d = 0.0
                        if d > max_km:
                            max_km = d
                # If computed max_km is tiny (points almost identical), fallback to bbox method
                if max_km <= 0.01:
                    overview_pct = 0.0
                else:
                    pad_km = max_km * overview_pct
                    # convert pad_km to degrees at center latitude
                    center_lat = sum([c[1] for c in step_coords]) / len(step_coords) if step_coords else (sum(lats) / len(lats) if lats else 0.0)
                    import math
                    cos_lat = max(0.01, abs(math.cos(math.radians(center_lat))))
                    deg_per_km_lon = 1.0 / (111.32 * cos_lat)
                    deg_per_km_lat = 1.0 / 111.32
                    lon_pad = pad_km * deg_per_km_lon
                    lat_pad = pad_km * deg_per_km_lat
            if overview_pct == 0.0:
                pad_frac = float(getattr(self, "overview_map_padding", 0.06))
                lon_pad = (max_lon - min_lon) * pad_frac if max_lon != min_lon else 0.01
                lat_pad = (max_lat - min_lat) * pad_frac if max_lat != min_lat else 0.01

            min_lon_p, max_lon_p = min_lon - lon_pad, max_lon + lon_pad
            min_lat_p, max_lat_p = min_lat - lat_pad, max_lat + lat_pad

            if getattr(self, 'debug_map', False):
                try:
                    # compute zooms for diagnostics
                    zoom_unpadded = self._compute_zoom_for_bounds(min_lon, max_lon, min_lat, max_lat, self.width, self.height)
                    zoom_padded = self._compute_zoom_for_bounds(min_lon_p, max_lon_p, min_lat_p, max_lat_p, self.width, self.height)
                    # compute pad in pixels (approx)
                    lon_span = max_lon - min_lon if max_lon != min_lon else 1e-6
                    pad_px = (lon_pad / lon_span) * float(self.width)
                    print(f"Overview padding: overview_pct={overview_pct}, max_km={max_km if 'max_km' in locals() else 'N/A'}, pad_km={pad_km if 'pad_km' in locals() else 'N/A'}, lon_pad={lon_pad}, lat_pad={lat_pad}, pad_px~{pad_px:.2f}, zoom_unpadded={zoom_unpadded}, zoom_padded={zoom_padded}, zoom_final={zoom}")
                except Exception:
                    pass

            zoom = self._compute_zoom_for_bounds(min_lon_p, max_lon_p, min_lat_p, max_lat_p, self.width, self.height)

            # Optionally force additional zoom-out when padding was requested but integer zoom didn't change
            try:
                force_zoom_out = bool(getattr(self, 'overview_force_zoom_out_when_padding', False))
            except Exception:
                force_zoom_out = False
            try:
                zoom_unpadded = self._compute_zoom_for_bounds(min_lon, max_lon, min_lat, max_lat, self.width, self.height)
            except Exception:
                zoom_unpadded = zoom

            if force_zoom_out and (overview_pct and overview_pct > 0.0 or float(getattr(self, 'overview_map_padding', 0.0)) > 0.0):
                if zoom == zoom_unpadded:
                    # Only apply zoom-out if doing so will increase visible pad to at least overview_min_pad_px
                    try:
                        min_px = float(getattr(self, 'overview_min_pad_px', 12))
                    except Exception:
                        min_px = 12.0
                    applied = False
                    # Try 1..3 zoom-out levels (don't go insane)
                    for delta in range(1, 4):
                        cand = max(self.min_zoom, int(zoom) - delta)
                        # degrees per pixel at candidate zoom
                        dpp_lon = 360.0 / (256.0 * (2 ** cand))
                        pad_px_cand = lon_pad / dpp_lon if dpp_lon > 0 else 0.0
                        if getattr(self, 'debug_map', False):
                            print(f"Check zoom-out: cand={cand}, pad_px_cand={pad_px_cand:.2f}, needed={min_px}")
                        if pad_px_cand >= min_px:
                            if getattr(self, 'debug_map', False):
                                print(f"Applying zoom out: {zoom} -> {cand} (pad_px={pad_px_cand:.2f} >= {min_px})")
                            zoom = cand
                            applied = True
                            break
                    if not applied and getattr(self, 'debug_map', False):
                        cur_pad_px = lon_pad / (360.0 / (256.0 * (2 ** int(zoom)))) if zoom is not None else 0.0
                        print(f"No zoom-out applied (pad_px={cur_pad_px:.2f} < min_px={min_px})")

            center = ((min_lon_p + max_lon_p) / 2.0, (min_lat_p + max_lat_p) / 2.0)
            image = m.render(zoom=zoom, center=center)
            # Apply label overlay for hybrid maps (if configured)
            image = self._apply_label_overlay(image, zoom, center)
        else:
            image = m.render()

        img_bytes = io.BytesIO()
        image.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        return img_bytes.getvalue()

    def _get_step_thumbnail(self, step: dict, size: int = 36, ring_color: tuple = (255,255,255,230)) -> Optional[Path]:
        """Create a circular thumbnail marker for a step's first photo (cached).

        ring_color: RGBA tuple for the ring around thumbnail. Included in cache key so highlighted thumbnails are separate.
        """
        photos = step.get("photos", [])
        photo_path = None

        # Prefer a local photo if listed
        if photos:
            candidate = photos[0]
            photo_path = Path(candidate)
            if not photo_path.exists():
                photo_path = None

        # Fallback: look for cover photo URL in step data
        if photo_path is None:
            data = step.get("data", {}) if isinstance(step, dict) else {}
            # Try multiple keys that may contain a URL
            for key in ("cover_photo", "cover_photo_path", "cover_photo_thumb_path", "main_media_item_path", "cover_photo_url"):
                val = None
                if isinstance(data, dict):
                    if key in data:
                        v = data.get(key)
                        if isinstance(v, dict) and v.get("path"):
                            val = v.get("path")
                        elif isinstance(v, str):
                            val = v
                if val:
                    photo_path = val  # keep as string (URL)
                    break

        if photo_path is None:
            return None

        # Normalize photo_path: can be a Path or a URL string
        is_url = False
        if isinstance(photo_path, str):
            is_url = photo_path.startswith("http://") or photo_path.startswith("https://")
            if not is_url:
                try:
                    photo_path = Path(photo_path)
                except Exception:
                    return None

        cache_dir = Path(__file__).parent / ".map_marker_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            mtime = photo_path.stat().st_mtime if (not is_url and isinstance(photo_path, Path)) else 0
        except Exception:
            mtime = 0

        # include ring color in cache key
        ring_hex = ''.join(f"{c:02x}" for c in ring_color)
        try:
            key_src = str(photo_path.resolve()) if isinstance(photo_path, Path) else str(photo_path)
        except Exception:
            key_src = str(photo_path)
        cache_key = f"{key_src}|{mtime}|{size}|{ring_hex}"
        cache_name = hashlib.sha1(cache_key.encode("utf-8")).hexdigest() + ".png"
        cache_path = cache_dir / cache_name

        if cache_path.exists():
            return cache_path

        try:
            if is_url:
                raise ValueError("URL thumbnail requires download")
            with Image.open(photo_path) as img:
                img = img.convert("RGBA")
                img = ImageOps.fit(img, (size, size), method=Image.LANCZOS)

                # Circular mask
                mask = Image.new("L", (size, size), 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, size - 1, size - 1), fill=255)

                out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                out.paste(img, (0, 0), mask=mask)

                # Border ring with configurable color
                ring = ImageDraw.Draw(out)
                ring_color_rgba = ring_color if len(ring_color) == 4 else (ring_color[0], ring_color[1], ring_color[2], 230)
                ring.ellipse((1, 1, size - 2, size - 2), outline=ring_color_rgba, width=2)

                out.save(cache_path, format="PNG")
                return cache_path
        except Exception:
            # If photo_path looks like a URL, try to fetch it into cache and retry
            try:
                url = str(photo_path)
                if url.startswith("http://") or url.startswith("https://"):
                    r = requests.get(url, timeout=10)
                    if r.status_code == 200:
                        cache_dir = Path(__file__).parent / ".map_marker_cache"
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        tmp_path = cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg")
                        tmp_path.write_bytes(r.content)
                        # Retry with downloaded image
                        return self._get_step_thumbnail({"photos": [tmp_path]}, size=size, ring_color=ring_color)
            except Exception:
                pass
            return None

    def _get_ring_overlay(self, size: int, color: str = MISSING_PHOTO_COLOR, thickness: int = 3) -> Optional[Path]:
        """Return a cached PNG ring (transparent center) to overlay markers for emphasis.

        - `size` is the outer diameter in pixels
        - `color` is a hex string like "#FF4D4F"
        - `thickness` is the stroke width in pixels
        """
        try:
            cache_dir = Path(__file__).parent / ".map_marker_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            key_src = f"ring|{size}|{color}|{thickness}"
            cache_name = hashlib.sha1(key_src.encode("utf-8")).hexdigest() + ".png"
            cache_path = cache_dir / cache_name
            if cache_path.exists():
                return cache_path

            # Convert hex color to RGBA tuple
            if isinstance(color, str) and color.startswith("#"):
                c = color.lstrip("#")
                if len(c) == 6:
                    r = int(c[0:2], 16)
                    g = int(c[2:4], 16)
                    b = int(c[4:6], 16)
                    a = 220
                else:
                    r, g, b, a = 255, 80, 80, 220
            else:
                try:
                    r, g, b = color[0], color[1], color[2]
                    a = color[3] if len(color) > 3 else 220
                except Exception:
                    r, g, b, a = 255, 80, 80, 220

            img = Image.new("RGBA", (int(size), int(size)), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # Draw ring by outlining ellipse
            draw.ellipse((0, 0, size - 1, size - 1), outline=(r, g, b, a), width=int(thickness))
            img.save(cache_path, format="PNG")
            return cache_path
        except Exception:
            return None

    def _compute_zoom_for_bounds(self, min_lon: float, max_lon: float, min_lat: float, max_lat: float, width_px: int, height_px: int) -> int:
        """Compute an approximate zoom level to fit given bounds into width/height in pixels.

        This is a heuristic using lon-span; Mercator projection and latitude scaling are approximated.
        """
        try:
            import math
            lon_span = max_lon - min_lon
            lat_span = max_lat - min_lat
            if lon_span <= 0:
                return self.default_zoom

            # degrees per pixel needed for lon
            dpp_lon = lon_span / float(max(width_px, 1))
            z_lon = math.log2(360.0 / (256.0 * dpp_lon)) if dpp_lon > 0 else self.default_zoom

            # account for latitude using cosine of center lat
            center_lat = (min_lat + max_lat) / 2.0
            cos_lat = max(0.01, abs(math.cos(math.radians(center_lat))))
            dpp_lat = lat_span / float(max(height_px, 1))
            # rough adjustment for lat
            z_lat = math.log2(360.0 / (256.0 * (dpp_lat / cos_lat))) if dpp_lat > 0 else z_lon

            z = int(min(z_lon, z_lat))
        except Exception:
            z = self.default_zoom

        # clamp
        z = max(self.min_zoom, min(self.max_zoom, z))
        return z

    def generate_step_map_for_step(self, trip_parser: TripParser, step_index: int, width: int = 0, height: int = 0, padding: float = 0.1) -> bytes:
        """Generate a map centered/zoomed to ensure prev/current/next steps are visible.

        - `step_index` is 0-based index of the current step in trip_parser.steps
        - `padding` is relative padding (fraction) around computed bounds
        """
        if StaticMap is None:
            raise RuntimeError("staticmap not available: install the 'staticmap' package to enable step maps")

        w = width or self.width
        h = height or self.height

        # Determine current + distinct previous/next (skip same-location clusters)
        current_coord = self._extract_lon_lat(trip_parser.steps[step_index]) if (0 <= step_index < len(trip_parser.steps)) else None
        if not current_coord:
            m = self._create_map(w, h)
            image = m.render()
            img_bytes = io.BytesIO()
            image.save(img_bytes, format="PNG")
            img_bytes.seek(0)
            return img_bytes.getvalue()

        prev_idx = self._find_distinct_neighbor_index(trip_parser, step_index, -1)
        next_idx = self._find_distinct_neighbor_index(trip_parser, step_index, +1)
        prev_coord = self._extract_lon_lat(trip_parser.steps[prev_idx]) if prev_idx is not None else None
        next_coord = self._extract_lon_lat(trip_parser.steps[next_idx]) if next_idx is not None else None

        # On long trips, ignore far-away neighbors to keep the step map tighter
        try:
            step_count = len(trip_parser.steps) if hasattr(trip_parser, "steps") else 0
            max_neighbor_km = float(getattr(self, "step_map_neighbor_max_km", 0) or 0)
            min_steps_for_limit = int(getattr(self, "step_map_neighbor_limit_steps_threshold", 0) or 0)
            if max_neighbor_km > 0 and (min_steps_for_limit == 0 or step_count >= min_steps_for_limit):
                if prev_coord:
                    try:
                        dist_prev = self._haversine_km(current_coord[0], current_coord[1], prev_coord[0], prev_coord[1])
                        if dist_prev > max_neighbor_km:
                            if getattr(self, 'debug_map', False):
                                print(f"Step map neighbor clamp: prev {dist_prev:.1f}km > {max_neighbor_km}km -> ignore")
                            prev_coord = None
                    except Exception:
                        pass
                if next_coord:
                    try:
                        dist_next = self._haversine_km(current_coord[0], current_coord[1], next_coord[0], next_coord[1])
                        if dist_next > max_neighbor_km:
                            if getattr(self, 'debug_map', False):
                                print(f"Step map neighbor clamp: next {dist_next:.1f}km > {max_neighbor_km}km -> ignore")
                            next_coord = None
                    except Exception:
                        pass
        except Exception:
            pass

        fit_coords = [c for c in (prev_coord, current_coord, next_coord) if c]
        lons = [c[0] for c in fit_coords]
        lats = [c[1] for c in fit_coords]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        pad_frac = float(padding if padding is not None else getattr(self, "step_map_padding", 0.12))

        # Auto-tighten padding for trips with many steps so step maps become more zoomed-in.
        try:
            n_steps = len(trip_parser.steps) if hasattr(trip_parser, "steps") else 0
            if getattr(self, "step_map_auto_tighten", True) and n_steps > 20:
                if n_steps <= 40:
                    pad_scale = float(getattr(self, "step_map_tighten_scale_small", 0.8))
                elif n_steps <= 80:
                    pad_scale = float(getattr(self, "step_map_tighten_scale_medium", 0.6))
                else:
                    pad_scale = float(getattr(self, "step_map_tighten_scale_large", 0.5))
                if getattr(self, 'debug_map', False):
                    print(f"Step map: auto-tighten for {n_steps} steps: pad_frac {pad_frac} -> {pad_frac*pad_scale}")
                pad_frac = max(0.0, pad_frac * pad_scale)
        except Exception:
            pass

        # Cap absolute padding in km to avoid excessive zoom-out on long spans.
        try:
            center_lat_for_pad = (min_lat + max_lat) / 2.0
            span_lon_deg = max_lon - min_lon
            span_lat_deg = max_lat - min_lat
            # approximate horizontal km span at center latitude
            import math
            km_per_deg_lon = 111.32 * max(0.01, abs(math.cos(math.radians(center_lat_for_pad))))
            span_km = span_lon_deg * km_per_deg_lon
            max_pad_km = float(getattr(self, "step_map_max_pad_km", 0.0) or 0.0)
            if max_pad_km > 0 and span_km > 0:
                pad_km = span_km * pad_frac
                if pad_km > max_pad_km:
                    pad_frac = max_pad_km / span_km
                    if getattr(self, 'debug_map', False):
                        print(f"Step map: pad capped by max_pad_km={max_pad_km}km -> pad_frac={pad_frac:.4f}")
        except Exception:
            pass

        lon_pad = (max_lon - min_lon) * pad_frac if max_lon != min_lon else 0.01
        lat_pad = (max_lat - min_lat) * pad_frac if max_lat != min_lat else 0.01
        min_lon_p, max_lon_p = min_lon - lon_pad, max_lon + lon_pad
        min_lat_p, max_lat_p = min_lat - lat_pad, max_lat + lat_pad

        # Base zoom to fit (never crops).
        zoom_fit = self._compute_zoom_for_bounds(min_lon_p, max_lon_p, min_lat_p, max_lat_p, w, h)
        zoom = int(zoom_fit)

        # Apply min-width constraint (km): avoid being too zoomed-in.
        center_lat_for_km = current_coord[1]
        zoom_min_width = self._zoom_for_horizontal_km(getattr(self, "step_map_min_width_km", 12.0), center_lat_for_km, w, prefer="at_least")
        zoom = min(int(zoom), int(zoom_min_width))  # smaller zoom => wider view

        # Apply configured extra zoom-out levels, then clamp to min/max zoom.
        try:
            out_levels = int(getattr(self, "step_map_zoom_out", 0) or 0)
        except Exception:
            out_levels = 0
        zoom = zoom - out_levels
        zoom = max(self.min_zoom, min(self.max_zoom, int(zoom)))

        # Optional max-width constraint (km): only if it doesn't crop the fit bounds.
        try:
            max_km = float(getattr(self, "step_map_max_width_km", 0) or 0)
        except Exception:
            max_km = 0.0
        if max_km > 0:
            zoom_max_width = self._zoom_for_horizontal_km(max_km, center_lat_for_km, w, prefer="at_most")
            # If current zoom exceeds max_km (is too wide), force zooming in.
            # This prioritizes max width and may crop distant neighbors.
            zoom = max(int(zoom), int(zoom_max_width))
            zoom = max(self.min_zoom, min(self.max_zoom, int(zoom)))

        # Centering: weighted towards current step, but clamped so prev/current/next remain visible.
        center_points = [
            (current_coord[0], current_coord[1], getattr(self, "step_center_weight_current", 2.0)),
        ]
        if prev_coord:
            center_points.append((prev_coord[0], prev_coord[1], getattr(self, "step_center_weight_other", 1.0)))
        if next_coord:
            center_points.append((next_coord[0], next_coord[1], getattr(self, "step_center_weight_other", 1.0)))
        wc = self._weighted_center(center_points)
        # filter Nones introduced above
        if wc is None:
            wc = ((min_lon_p + max_lon_p) / 2.0, (min_lat_p + max_lat_p) / 2.0)
        bounds = (min_lon_p, max_lon_p, min_lat_p, max_lat_p)
        center = self._clamp_center_to_bounds(wc, zoom, bounds, w, h)

        m = self._create_map(w, h)

        # Also draw route line for context (outline + main line for visibility)
        route_coords = trip_parser.get_route_coordinates()
        if len(route_coords) > 1:
            outline = Line(route_coords, ROUTE_OUTLINE_COLOR, ROUTE_OUTLINE_WIDTH)
            m.add_line(outline)
            line = Line(route_coords, ROUTE_COLOR, ROUTE_LINE_WIDTH)
            m.add_line(line)

        # Add all step markers; draw current last so it's always on top.
        marker_px = max(8, int(round(self.marker_thumb_size * self.render_scale)))
        marker_radius = max(4, int(round(12 * self.render_scale)))
        normal_indices = [i for i in range(len(trip_parser.steps)) if i != step_index]
        draw_order = normal_indices + ([step_index] if 0 <= step_index < len(trip_parser.steps) else [])
        for i in draw_order:
            st = trip_parser.steps[i]
            coord = self._extract_lon_lat(st)
            if not coord:
                continue
            lon, lat = coord

            is_current = (i == step_index)
            try:
                has_photo = self._step_has_photo(st)
            except Exception:
                has_photo = True
            ring_color = (255, 80, 80, 220) if is_current else (255, 255, 255, 230)
            thumb = self._get_step_thumbnail(st, size=marker_px, ring_color=ring_color)

            # Add a red halo under the current step marker only when a photo exists
            if is_current and has_photo:
                try:
                    m.add_marker(CircleMarker((lon, lat), MISSING_PHOTO_COLOR, marker_radius + 4))
                except Exception:
                    pass

            if thumb and IconMarker is not None:
                off_x = int(marker_px / 2)
                off_y = int(marker_px / 2)
                try:
                    m.add_marker(IconMarker((lon, lat), str(thumb), off_x, off_y))
                    # If this is the current step with a photo, overlay a ring image on top to ensure visibility
                    if is_current and has_photo:
                        try:
                            overlay = self._get_ring_overlay(marker_px + 8, color=MISSING_PHOTO_COLOR, thickness=max(2, int(round(self.render_scale * 2))))
                            if overlay:
                                off_xo = int((marker_px + 8) / 2)
                                off_yo = int((marker_px + 8) / 2)
                                try:
                                    m.add_marker(IconMarker((lon, lat), str(overlay), off_xo, off_yo))
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    continue
                except Exception:
                    pass

            if is_current and not has_photo:
                color = MISSING_PHOTO_COLOR
            else:
                color = MARKER_COLOR_START if i == 0 else ("#FF4D4F" if is_current else MARKER_COLOR_STEP)
            m.add_marker(CircleMarker((lon, lat), color, marker_radius))

        if getattr(self, 'debug_map', False):
            try:
                dpp_lon = 360.0 / (256.0 * (2 ** int(zoom)))
                pad_px = lon_pad / dpp_lon if dpp_lon > 0 else 0.0
                print(f"Step padding: lon_pad={lon_pad:.6f}, pad_px~{pad_px:.2f}, zoom={zoom}, center={center}")
            except Exception:
                pass

        image = m.render(zoom=zoom, center=center)
        # Apply label overlay for hybrid maps (if configured)
        image = self._apply_label_overlay(image, zoom, center)
        img_bytes = io.BytesIO()
        image.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        return img_bytes.getvalue()


# Back-compat helper in case other modules need dates from a TripParser
def trip_parser_get_dates(trip_path: Path):
    tp = TripParser(trip_path)
    tp.load()
    return tp.get_trip_dates() if hasattr(tp, 'get_trip_dates') else (None, None)


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
        return bool(EMOJI_PATTERN.search(text))

    def _get_emoji_png_path(self, emoji: str) -> Optional[Path]:
        """Get or fetch a Twemoji PNG for an emoji sequence (cached)."""
        if not emoji:
            return None

        cache_dir = Path(__file__).parent / ".emoji_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Convert emoji sequence to codepoint sequence
        cps = [f"{ord(ch):x}" for ch in emoji]
        cp_seq = "-".join(cps)
        emoji_file = cache_dir / f"{cp_seq}.png"

        if emoji_file.exists():
            return emoji_file

        # Fetch from Twemoji CDN (72x72)
        url = f"https://twemoji.maxcdn.com/v/latest/72x72/{cp_seq}.png"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                emoji_file.write_bytes(r.content)
                return emoji_file
        except Exception:
            return None

        return None

    def _emoji_img_tag(self, emoji: str, size_px: int) -> str:
        """Return an inline <img> tag for an emoji, or the escaped emoji if unavailable."""
        emoji_path = self._get_emoji_png_path(emoji)
        if not emoji_path:
            return html.escape(emoji)

        # Use POSIX-style path to avoid backslash escaping in XML
        src = emoji_path.as_posix()
        valign = -2
        return f'<img src="{src}" width="{size_px}" height="{size_px}" valign="{valign}"/>'

    def _text_to_inline_emoji_html(self, text: str, style: ParagraphStyle, preserve_newlines: bool = True) -> str:
        """Convert text to ReportLab paragraph markup with inline emoji images."""
        if text is None:
            return ""

        # Scale emoji roughly to text size
        scale = float(self.config.get("emoji_scale", 1.1)) if hasattr(self, "config") else 1.1
        size_px = max(8, int(float(style.fontSize) * scale))

        parts = EMOJI_PATTERN.split(text)
        out = []
        for part in parts:
            if not part:
                continue
            if EMOJI_PATTERN.fullmatch(part):
                out.append(self._emoji_img_tag(part, size_px))
            else:
                escaped = html.escape(part)
                if preserve_newlines:
                    escaped = escaped.replace("\n", "<br/>")
                out.append(escaped)
        return "".join(out)

    def _paragraph_with_inline_emoji(self, text: str, style_name: str, preserve_newlines: bool = True) -> Paragraph:
        """Create a Paragraph with inline emoji images while keeping text copyable."""
        style = self.styles.get(style_name)
        html_text = self._text_to_inline_emoji_html(text or "", style, preserve_newlines=preserve_newlines)
        try:
            return Paragraph(html_text, style)
        except Exception:
            # Fallback: keep text copyable even if inline image parsing fails
            safe_text = html.escape(text or "")
            if preserve_newlines:
                safe_text = safe_text.replace("\n", "<br/>")
            return Paragraph(safe_text, style)

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
        """Add text as a Paragraph with inline emoji images (copyable text)."""
        if text is None:
            return

        preserve_newlines = escape_html
        self.elements.append(self._paragraph_with_inline_emoji(text, style_name, preserve_newlines=preserve_newlines))
    
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
            if getattr(self.map_generator, 'debug_map', False):
                try:
                    tmp = Path.cwd() / f"debug_overview_{self.trip_parser.get_trip_name().replace(' ', '_')}.png"
                    tmp.write_bytes(map_bytes)
                    print(f"Debug: wrote overview image to {tmp} ({len(map_bytes)} bytes)")
                except Exception:
                    pass

            # Normalize image with Pillow (drop alpha, convert to RGB) to avoid PDF embedding issues
            try:
                pil = Image.open(io.BytesIO(map_bytes)).convert('RGB')
                buf = io.BytesIO()
                pil.save(buf, format='PNG')
                buf.seek(0)
                map_img = RLImage(buf)
            except Exception:
                # Fallback to raw bytes if Pillow conversion fails
                try:
                    map_img = RLImage(io.BytesIO(map_bytes))
                except Exception as e:
                    print(f"Warning: could not create overview RLImage: {e}")
                    map_img = None

            if map_img:
                # Scale to fit page width
                aspect = float(self.map_generator.width) / float(self.map_generator.height)
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
    
    def _create_photo_grid(self, photos: list, max_photos: int = 6) -> Optional[RLImage]:
        """Create a packed photo wall based on individual image aspect ratios."""
        if not photos:
            return None

        max_photos = int(self.config.get("max_photos_per_step", max_photos))
        photos_to_show = photos[:max_photos]

        # Wall configuration (points ~ pixels at 72 DPI)
        target_width = int(self.CONTENT_WIDTH)
        gap = int(self.config.get("photo_wall_gap", 6))
        target_row_height = int(self.config.get("photo_wall_row_height", 140))
        min_row_height = int(self.config.get("photo_wall_min_row_height", 90))
        max_row_height = int(self.config.get("photo_wall_max_row_height", 220))

        # Build list of (path, aspect)
        items = []
        for photo_path in photos_to_show:
            try:
                with Image.open(photo_path) as img:
                    w, h = img.size
                if h == 0:
                    continue
                aspect = float(w) / float(h)
                items.append((photo_path, aspect))
            except Exception as e:
                print(f"    Warning: Could not read image {photo_path}: {e}")

        if not items:
            return None

        # Row packing (justified layout)
        rows = []
        current = []
        sum_aspect = 0.0

        for idx, (path, aspect) in enumerate(items):
            current.append((path, aspect))
            sum_aspect += aspect

            row_height = int(target_width / max(sum_aspect, 0.01))
            is_last = idx == len(items) - 1

            if row_height <= target_row_height or len(current) >= 3 or is_last:
                # Clamp row height for aesthetics
                if is_last and row_height > target_row_height * 1.2:
                    row_height = target_row_height
                row_height = max(min_row_height, min(row_height, max_row_height))
                rows.append((list(current), row_height))
                current = []
                sum_aspect = 0.0

        # Compute final wall size
        total_height = 0
        for row, row_h in rows:
            total_height += row_h
        total_height += gap * (len(rows) - 1) if len(rows) > 1 else 0

        wall = Image.new("RGB", (target_width, max(total_height, 1)), (255, 255, 255))

        y = 0
        for row, row_h in rows:
            # Compute widths scaled to fit target_width
            raw_widths = [int(row_h * aspect) for _, aspect in row]
            total_raw = sum(raw_widths)
            available = target_width - gap * (len(row) - 1)
            scale = float(available) / float(max(total_raw, 1))
            widths = [max(1, int(w * scale)) for w in raw_widths]

            # Adjust last width to fill any rounding error
            if widths:
                widths[-1] = max(1, available - sum(widths[:-1]))

            x = 0
            for (path, _aspect), w in zip(row, widths):
                try:
                    with Image.open(path) as img:
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")
                        fitted = ImageOps.fit(img, (w, row_h), method=Image.LANCZOS)
                        wall.paste(fitted, (x, y))
                except Exception as e:
                    print(f"    Warning: Could not process image {path}: {e}")
                x += w + gap

            y += row_h + gap

        # Convert wall to ReportLab image
        img_bytes = io.BytesIO()
        wall.save(img_bytes, format="JPEG", quality=88)
        img_bytes.seek(0)

        rl_img = RLImage(img_bytes)
        rl_img.drawWidth = self.CONTENT_WIDTH
        # Scale height to match the width
        scale = self.CONTENT_WIDTH / float(wall.size[0])
        rl_img.drawHeight = float(wall.size[1]) * scale

        return rl_img

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

    def _build_description_flowables(self, text: str) -> list:
        """Build nicely formatted flowables for step descriptions (paragraphs + bullet lists)."""
        if not text:
            return []

        lines = text.splitlines()
        blocks = []
        current_para = []
        current_list = []

        def flush_para():
            if current_para:
                blocks.append(("para", "\n".join(current_para)))
                current_para.clear()

        def flush_list():
            if current_list:
                blocks.append(("list", list(current_list)))
                current_list.clear()

        for line in lines:
            if not line.strip():
                flush_para()
                flush_list()
                continue

            if re.match(r"^\s*[-*]\s+", line):
                flush_para()
                item = re.sub(r"^\s*[-*]\s+", "", line)
                current_list.append(item)
            else:
                flush_list()
                current_para.append(line)

        flush_para()
        flush_list()

        flowables = []
        for kind, data in blocks:
            if kind == "para":
                flowables.append(self._paragraph_with_inline_emoji(data, "StepDescription", preserve_newlines=True))
            else:
                items = [
                    ListItem(
                        self._paragraph_with_inline_emoji(item, "StepDescription", preserve_newlines=False),
                        leftIndent=12
                    )
                    for item in data
                ]
                flowables.append(
                    ListFlowable(
                        items,
                        bulletType="bullet",
                        leftIndent=12
                    )
                )

        return flowables
    
    def _add_step(self, step: dict, step_number: int):
        """Add a step to the PDF."""
        step_data = step["data"]
        photos = step["photos"]
        videos = step["videos"]
        
        # Collect flowables for this step, then add as a single unit when possible
        step_flow = []

        # Step title (legacy paragraph)
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

        # Meta (legacy paragraph)
        safe_meta = meta_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        step_flow.append(Paragraph(safe_meta, self.styles["StepMeta"]))

        # Step map (small, inline)
        lat = location.get("lat") or location.get("latitude") or location.get("Latitude")
        lon = location.get("lon") or location.get("lng") or location.get("longitude") or location.get("Longitude")

        try:
            lat = float(lat) if lat is not None else None
            lon = float(lon) if lon is not None else None
        except Exception:
            lat = None
            lon = None

        # Always attempt to generate a step map (MapGenerator has fallbacks if coords are missing)
        try:
            map_height_points = 60 * mm
            # generate map ensuring prev & next are visible and current is highlighted
            map_bytes = self.map_generator.generate_step_map_for_step(
                self.trip_parser,
                step_number - 1,
                width=int(self.CONTENT_WIDTH),
                height=int(map_height_points),
                padding=float(self.config.get('step_map_padding', getattr(self.map_generator, 'step_map_padding', 0.12)))
            )
            if getattr(self.map_generator, 'debug_map', False):
                try:
                    tmp = Path.cwd() / f"debug_step_{step_number}_{self.trip_parser.get_trip_name().replace(' ', '_')}.png"
                    tmp.write_bytes(map_bytes)
                    print(f"Debug: wrote step image to {tmp} ({len(map_bytes)} bytes)")
                except Exception:
                    pass

            if map_bytes:
                # Normalize image with Pillow to avoid embedding problems (drop alpha)
                try:
                    pil = Image.open(io.BytesIO(map_bytes)).convert('RGB')
                    buf = io.BytesIO()
                    pil.save(buf, format='PNG')
                    buf.seek(0)
                    map_img = RLImage(buf)
                except Exception:
                    try:
                        map_img = RLImage(io.BytesIO(map_bytes))
                    except Exception as e:
                        print(f"    Warning: could not create RLImage for step map: {e}")
                        map_img = None

                if map_img:
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
        print(f"  PDF created: {self.output_path}")

        # Optionally open the rendered PDF file after creation (config key: open_pdf_after_render)
        try:
            open_after = bool(self.config.get("open_pdf_after_render", True))
        except Exception:
            open_after = True

        if open_after:
            try:
                if os.name == "nt":
                    # Windows
                    os.startfile(str(self.output_path))
                elif sys.platform == "darwin":
                    subprocess.run(["open", str(self.output_path)], check=False)
                else:
                    # Linux/Unix
                    subprocess.run(["xdg-open", str(self.output_path)], check=False)
            except Exception as e:
                print(f"  Warning: Could not open PDF: {e}")


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
                confirm = input("Clear all rendered marks? (yes/no): ").strip().lower()
                if confirm in ('yes', 'y'):
                    cache_manager.clear_cache()
                    print("Cache cleared!")
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
                                print(f"Error: {result['error']}")
                                continue
                        elif lc in ('n', 'no'):
                            print("Cancelled. Returning to command prompt.")
                            continue
                        else:
                            # Treat the user's input as a new command and process it
                            cmd = user_choice
                            continue
                    else:
                        print(f"Error: {result['error']}")
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
                            print("\nStop signal received. Finishing current trip...")
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
                    print(f"Stop requested. Completed: {success_count}/{len(trips_to_render)} trip(s) rendered.")
                else:
                    print(f"Completed: {success_count}/{len(trips_to_render)} trip(s) rendered.")
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
            print("  Stopped by user")
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
        
        # Determine map URL + hybrid labels
        map_style = str(config.get("map_style", "hybrid")).lower()
        label_overlay_url = None
        label_overlay_opacity = float(config.get("hybrid_labels_opacity", 0.7))
        if map_style == "road":
            map_url = ESRI_ROAD_URL
        elif map_style == "satellite":
            map_url = ESRI_SATELLITE_URL
        else:
            # Hybrid: satellite base with label overlay
            map_url = ESRI_SATELLITE_URL
            label_overlay_url = ESRI_LABELS_URL

        map_gen = MapGenerator(
            default_zoom=int(config.get("default_map_zoom", 12)),
            min_zoom=int(config.get("min_map_zoom", 6)),
            max_zoom=int(config.get("max_map_zoom", 16)),
            render_scale=float(config.get("map_render_scale", 1.0)),
            marker_thumb_size=int(config.get("marker_thumb_size", 40)),
            url_template=map_url,
            label_overlay_url=label_overlay_url,
            label_overlay_opacity=label_overlay_opacity
        )
        # optionally allow configuring thumbnail size and step-map behavior
        try:
            map_gen.marker_thumb_size = int(config.get("marker_thumb_size", map_gen.marker_thumb_size))
            map_gen.render_scale = max(1.0, float(config.get("map_render_scale", map_gen.render_scale)))
            map_gen.step_map_zoom_out = int(config.get("step_map_zoom_out", map_gen.step_map_zoom_out))
            map_gen.step_map_padding = float(config.get("step_map_padding", map_gen.step_map_padding))
            map_gen.overview_map_padding = float(config.get("overview_map_padding", map_gen.overview_map_padding))
            map_gen.overview_padding_percent = float(config.get("overview_padding_percent", getattr(map_gen, 'overview_padding_percent', 0.0)))
            map_gen.overview_force_zoom_out_when_padding = bool(config.get("overview_force_zoom_out_when_padding", getattr(map_gen, 'overview_force_zoom_out_when_padding', False)))
            map_gen.overview_min_pad_px = float(config.get("overview_min_pad_px", getattr(map_gen, 'overview_min_pad_px', 12)))
            map_gen.debug_map = bool(config.get("debug_map", getattr(map_gen, 'debug_map', False)))
            map_gen.step_map_min_width_km = float(config.get("step_map_min_width_km", map_gen.step_map_min_width_km))
            map_gen.step_map_max_width_km = float(config.get("step_map_max_width_km", map_gen.step_map_max_width_km))
            map_gen.step_cluster_radius_km = float(config.get("step_cluster_radius_km", map_gen.step_cluster_radius_km))
            map_gen.step_center_weight_current = float(config.get("step_center_weight_current", map_gen.step_center_weight_current))
            map_gen.step_center_weight_other = float(config.get("step_center_weight_other", map_gen.step_center_weight_other))
            # Optional: allow disabling/enabling auto-tighten behavior and tuning scales via config
            map_gen.step_map_auto_tighten = bool(config.get("step_map_auto_tighten", map_gen.step_map_auto_tighten))
            map_gen.step_map_tighten_scale_small = float(config.get("step_map_tighten_scale_small", getattr(map_gen, 'step_map_tighten_scale_small', 0.8)))
            map_gen.step_map_tighten_scale_medium = float(config.get("step_map_tighten_scale_medium", getattr(map_gen, 'step_map_tighten_scale_medium', 0.6)))
            map_gen.step_map_tighten_scale_large = float(config.get("step_map_tighten_scale_large", getattr(map_gen, 'step_map_tighten_scale_large', 0.5)))
            map_gen.step_map_neighbor_max_km = float(config.get("step_map_neighbor_max_km", getattr(map_gen, 'step_map_neighbor_max_km', 250.0)))
            map_gen.step_map_neighbor_limit_steps_threshold = int(config.get("step_map_neighbor_limit_steps_threshold", getattr(map_gen, 'step_map_neighbor_limit_steps_threshold', 20)))
            map_gen.step_map_max_pad_km = float(config.get("step_map_max_pad_km", getattr(map_gen, 'step_map_max_pad_km', 25.0)))
        except Exception:
            pass

        pdf_builder = PDFBuilder(output_path, parser, map_gen, config=config)
        pdf_builder.build()
        
        # Mark as rendered
        cache_manager.mark_rendered(trip_path)
        
        print(f"  Done. PDF saved to: {output_path}")
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
    
    # Load config (supports TOML with comments; falls back to commented JSON)
    config = {}
    config_toml = script_dir / "config.toml"
    config_json = script_dir / "config.json"
    try:
        if config_toml.exists():
            if _tomllib is None:
                raise RuntimeError("TOML config found but tomllib/toml is not available. Install the 'toml' package or run with Python 3.11+.")
            with open(config_toml, "r", encoding="utf-8") as cf:
                toml_content = cf.read()
                # Use loads for both 'toml' package and stdlib 'tomllib'
                config = _tomllib.loads(toml_content)
        elif config_json.exists():
            with open(config_json, "r", encoding="utf-8") as cf:
                content = cf.read()
                # Remove comments // ... and /* ... */ to allow commented JSON
                content = re.sub(r"//.*", "", content)
                content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
                config = json.loads(content)
    except Exception as e:
        print(f"Warning: could not load config: {e}")
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
