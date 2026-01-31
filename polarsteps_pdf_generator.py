#!/usr/bin/env python3
"""
Polarsteps PDF Generator

Generates beautiful PDF travel journals from downloaded Polarsteps data.
Features:
- Overview map with route and step markers (first photo per step)
- Per-step pages with location map, weather, description, and photo grid
- Appendix with undisplayed step photos and video links
- ESRI World Imagery satellite tiles
"""
import io
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime
import argparse
import json
import re
import base64
# Optional TOML loader (tomllib for Python 3.11+, fallback to the 'toml' package)
try:
    import tomllib as _tomllib
except Exception:
    try:
        import toml as _tomllib  # type: ignore - optional runtime dependency
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
import shutil
from collections import deque, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Root folders for temp output, debug output, and caches
SCRIPT_DIR = Path(__file__).parent
CACHE_ROOT = SCRIPT_DIR / "cache"
TEMP_ROOT = SCRIPT_DIR / "temp"
DEBUG_ROOT = SCRIPT_DIR / "debug"
LANGUAGE_PACK_DIR = SCRIPT_DIR / "LanguagePack"


class LanguageManager:
    def __init__(self, language_code: str, pack: dict, fallback_pack: dict):
        self.language_code = language_code
        self.pack = pack or {}
        self.fallback_pack = fallback_pack or {}

    def t(self, key: str, **kwargs) -> str:
        text = self.pack.get(key)
        if text is None:
            text = self.fallback_pack.get(key)
        if text is None:
            text = key
        try:
            return text.format(**kwargs)
        except Exception:
            return text

    def get_list(self, key: str, default: Optional[List[str]] = None) -> List[str]:
        value = self.pack.get(key)
        if value is None:
            value = self.fallback_pack.get(key)
        if isinstance(value, list):
            return [str(v).lower() for v in value]
        return [str(v).lower() for v in (default or [])]

    def is_yes(self, text: str) -> bool:
        return str(text).strip().lower() in self.get_list("input.yes_values", ["yes", "y"])

    def is_no(self, text: str) -> bool:
        return str(text).strip().lower() in self.get_list("input.no_values", ["no", "n"])

    def get_date_format(self, key: str, fallback: str = "%d.%m.%Y") -> str:
        value = self.pack.get(key)
        if value is None:
            value = self.fallback_pack.get(key)
        if isinstance(value, str) and value.strip():
            return value
        return fallback


_DEFAULT_LANGUAGE_MANAGER = LanguageManager("en", {}, {})


def _normalize_language_code(code: str) -> str:
    if not code:
        return "en"
    normalized = str(code).strip().lower()
    if normalized in ("de", "deutsch", "german", "ger"):
        return "de"
    if normalized in ("en", "english", "englisch"):
        return "en"
    return normalized


def _try_set_locale_for_language(language_code: str):
    try:
        import locale
    except Exception:
        return None
    candidates = {
        "de": ["de_DE.UTF-8", "de_DE", "deu_deu", "German_Germany", "de-DE"],
        "en": ["en_US.UTF-8", "en_US", "English_United States", "en-GB", "en_US.utf8"],
    }
    for name in candidates.get(language_code, []):
        try:
            locale.setlocale(locale.LC_TIME, name)
            return name
        except Exception:
            continue
    return None


def _load_language_file(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _parse_simple_toml(content: str) -> dict:
    data: dict = {}
    current = data
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            current = data
            if section:
                for part in section.split("."):
                    part = part.strip()
                    if not part:
                        continue
                    current = current.setdefault(part, {})
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        parsed: object
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            parsed = val[1:-1]
        elif val.lower() in ("true", "false"):
            parsed = val.lower() == "true"
        else:
            try:
                if "." in val:
                    parsed = float(val)
                else:
                    parsed = int(val)
            except Exception:
                parsed = val
        current[key] = parsed
    return data


def load_language_manager(language_code: str, script_dir: Path) -> LanguageManager:
    normalized = _normalize_language_code(language_code)
    selected_path = (script_dir / "LanguagePack") / f"{normalized}.json"
    fallback_path = (script_dir / "LanguagePack") / "en.json"

    fallback_pack = _load_language_file(fallback_path)
    selected_pack = _load_language_file(selected_path)
    if not selected_pack and normalized != "en":
        selected_pack = fallback_pack

    _try_set_locale_for_language(normalized)
    return LanguageManager(normalized, selected_pack, fallback_pack)


def get_default_language_manager() -> LanguageManager:
    return _DEFAULT_LANGUAGE_MANAGER


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_dir(*parts: str) -> Path:
    return _ensure_dir(CACHE_ROOT.joinpath(*parts))


def get_temp_dir(*parts: str) -> Path:
    return _ensure_dir(TEMP_ROOT.joinpath(*parts))


def get_debug_dir(*parts: str) -> Path:
    return _ensure_dir(DEBUG_ROOT.joinpath(*parts))


def _migrate_legacy_cache_paths():
    """Move legacy cache locations into cache/ for backward compatibility."""
    legacy_cache_file = SCRIPT_DIR / "rendered_trips_cache.json"
    new_cache_file = get_cache_dir() / "rendered_trips_cache.json"
    if legacy_cache_file.exists() and not new_cache_file.exists():
        try:
            _ensure_dir(new_cache_file.parent)
            shutil.move(str(legacy_cache_file), str(new_cache_file))
        except Exception:
            pass

    def _migrate_dir(legacy_dir: Path, new_dir: Path):
        if not legacy_dir.exists() or not legacy_dir.is_dir():
            return
        _ensure_dir(new_dir)
        for item in legacy_dir.iterdir():
            try:
                shutil.move(str(item), str(new_dir / item.name))
            except Exception:
                continue
        try:
            legacy_dir.rmdir()
        except Exception:
            pass

    _migrate_dir(SCRIPT_DIR / ".emoji_cache", get_cache_dir("emoji"))
    _migrate_dir(SCRIPT_DIR / ".map_marker_cache", get_cache_dir("map_marker"))

# Optional Playwright (HTML -> PDF renderer)
try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

# Optional emoji library for robust emoji segmentation
try:
    import emoji as _emoji  # type: ignore
except Exception:
    _emoji = None

# Geographic utilities and viewport calculation (new bounding-box system)
from geo import haversine_km as _geo_haversine_km
from map_viewport import (
    StepLocation,
    GeoBounds,
    MapViewport,
    compute_overview_viewport,
    compute_step_viewport,
    get_path_coordinates,
    compute_zoom_for_bounds,
    ASPECT_RATIO as MAP_ASPECT_RATIO,
)

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

                # Attempt to find a matching local folder by step id + location/slug, then fallback matches
                photos = []
                videos = []
                step_id = data.get("id") if isinstance(data, dict) else None
                if step_id is not None:
                    step_id = str(step_id)
                slug = (data.get("slug") or data.get("display_slug") or "").lower()
                display = (data.get("display_name") or "").lower().replace(" ", "-")
                loc_name = ""
                if isinstance(data, dict):
                    loc = data.get("location")
                    if isinstance(loc, dict):
                        loc_name = (loc.get("name") or "").lower().replace(" ", "-")

                def _folder_token(value: str) -> str:
                    return "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "-" for ch in value).strip("-")

                expected_names = set()
                if step_id:
                    for base in (slug, display, loc_name):
                        if base:
                            expected_names.add(f"{_folder_token(base)}_{step_id}")

                candidate = None
                for c in trip_children:
                    name = c.name.lower()
                    if expected_names and name in expected_names:
                        candidate = c
                        break
                    if step_id and (name.endswith(f"_{step_id}") or f"_{step_id}_" in name or name == step_id):
                        candidate = c
                        break
                # Only fall back to slug/display matching if step has no ID (legacy data)
                if not candidate and not step_id:
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
    r'([\U0001F1E6-\U0001F1FF\U0001F300-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U0001FB00-\U0001FBFF\u2300-\u23FF\u2600-\u26FF\u2700-\u27BF\u2B00-\u2BFF\u200d\ufe0f]+)',
    flags=re.UNICODE
)

# Pillow (PIL) for image processing
from PIL import Image, ImageDraw, ImageOps


class MapGenerator:
    """Generates static maps using ESRI World Imagery tiles.
    
    NEW BOUNDING-BOX SYSTEM (2026):
    Maps are generated using a deterministic Geographic Bounding Box approach:
    1. Calculate bounds from required points
    2. Apply configurable padding
    3. Enforce min/max width constraints
    4. Expand to 16:9 aspect ratio (never shrink)
    5. Convert to center/zoom for static map API
    
    Geographic coverage is independent of render_scale (resolution only).

    Config keys used (new [maps] section):
      - maps.vertical_resolution_px: vertical output resolution in pixels (affects image res)
            - maps.aspect_ratio: default aspect ratio (width:height) for maps
            - maps.overview.aspect_ratio: aspect ratio for overview maps
      - maps.overview.padding_factor: padding for overview maps
      - maps.overview.min_width_km: minimum width for overview
            - maps.step.aspect_ratio: aspect ratio for step maps
      - maps.step.padding_factor: padding for step maps
      - maps.step.min_width_km: minimum width for step maps
      - maps.step.max_distance_farthest_steps_km: max distance between farthest visible steps
      - maps.step.cluster_distance_km: cluster distance for neighbors
      
    Other supported keys:
      - marker_thumb_size (base size in pixels)
    """

    def __init__(self, width: int = 800, height: int = 450, marker_thumb_size: int = 40, url_template: str = ESRI_SATELLITE_URL, label_overlay_url: str = None, label_overlay_opacity: float = 0.7):
        # Logical viewport dimensions (16:9 default)
        self.width = width
        self.height = height
        # Per-map dimensions (can be overridden from config)
        self.overview_width = width
        self.overview_height = height
        self.step_width = width
        self.step_height = height
        # Per-map aspect ratios (default to current width/height)
        default_aspect = float(width) / float(height) if height else (16.0 / 9.0)
        self.overview_aspect_ratio = default_aspect
        self.step_aspect_ratio = default_aspect

        # render_scale only affects resolution, NOT geographic coverage
        # _pixel_scale is derived from the configured vertical_resolution_px and
        # is used only for pixel-scaling (marker sizes, overlay thickness). Geographic
        # coverage is determined by logical width/height and is independent of this.
        self._pixel_scale = 1.0
        self.url_template = url_template
        self.label_overlay_url = label_overlay_url
        self.label_overlay_opacity = float(label_overlay_opacity) if label_overlay_opacity is not None else 0.7
        self._tile_cache = {}
        # maximum thumbnail size used for markers
        self.marker_thumb_size = marker_thumb_size
        # reuse HTTP session for tile/image downloads
        self._requests_session = requests.Session()
        # in-memory caches (per run)
        self._marker_image_cache = OrderedDict()
        self._marker_image_cache_items = 256
        self._trip_route_cache = {}
        self._trip_step_coords_cache = {}
        
        # ========== NEW BOUNDING-BOX CONFIG (2026) ==========
        # These are the primary settings for the new system.
        # They can be overridden via config.toml [maps] section.
        
        # Overview map settings
        self.overview_padding_factor = 0.10  # 10% padding on each side
        self.overview_min_width_km = 10.0
        
        # Step map settings
        self.step_padding_factor = 0.10
        self.step_min_width_km = 2.0
        self.step_max_distance_farthest_km = 100.0
        self.step_cluster_distance_km = 5.0
        # Supersampling render scale for step maps (higher = sharper tiles)
        # 1.0 = normal; 2.0 = render at 2x resolution and downscale in PDF
        self.step_render_scale = 2.0
        
        # Debug flag
        self.debug_map = False
        


    def _cache_get(self, cache: OrderedDict, key):
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        return None

    def _cache_set(self, cache: OrderedDict, key, value, max_items: int):
        if max_items <= 0:
            return
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > max_items:
            cache.popitem(last=False)

    def _http_get(self, url: str, timeout: int = 6):
        try:
            return self._requests_session.get(url, timeout=timeout)
        except Exception:
            return None

    def _trip_cache_key(self, trip_parser: TripParser) -> str:
        try:
            trip_path = getattr(trip_parser, "trip_path", None)
            if trip_path:
                return str(Path(trip_path))
        except Exception:
            pass
        return str(id(trip_parser))

    def _get_trip_step_coords(self, trip_parser: TripParser) -> List[Optional[tuple]]:
        key = self._trip_cache_key(trip_parser)
        cached = self._trip_step_coords_cache.get(key)
        if cached is not None and len(cached) == len(trip_parser.steps):
            return cached
        coords = [self._extract_lon_lat(step) for step in trip_parser.steps]
        self._trip_step_coords_cache[key] = coords
        return coords

    def _get_trip_route_coords(self, trip_parser: TripParser) -> List[tuple]:
        key = self._trip_cache_key(trip_parser)
        cached = self._trip_route_cache.get(key)
        if cached is not None:
            return cached
        coords = trip_parser.get_route_coordinates()
        self._trip_route_cache[key] = coords
        return coords

    def _load_marker_image(self, path: Path, size: Optional[int] = None) -> Optional[Image.Image]:
        try:
            key = (str(path), int(size or 0))
            cached = self._cache_get(self._marker_image_cache, key)
            if cached is not None:
                return cached
            with Image.open(str(path)) as img:
                img = img.convert("RGBA")
                if size and (img.width != size or img.height != size):
                    img = img.resize((size, size), Image.LANCZOS)
                img = img.copy()
            self._cache_set(self._marker_image_cache, key, img, self._marker_image_cache_items)
            return img
        except Exception:
            return None

    def clone(self) -> "MapGenerator":
        """Create a new MapGenerator with copied settings for parallel rendering."""
        mg = MapGenerator(
            width=self.width,
            height=self.height,
            marker_thumb_size=self.marker_thumb_size,
            url_template=self.url_template,
            label_overlay_url=self.label_overlay_url,
            label_overlay_opacity=self.label_overlay_opacity,
        )
        mg.overview_width = self.overview_width
        mg.overview_height = self.overview_height
        mg.step_width = self.step_width
        mg.step_height = self.step_height
        mg.overview_aspect_ratio = self.overview_aspect_ratio
        mg.step_aspect_ratio = self.step_aspect_ratio
        mg._pixel_scale = self._pixel_scale
        mg.overview_padding_factor = self.overview_padding_factor
        mg.overview_min_width_km = self.overview_min_width_km
        mg.step_padding_factor = self.step_padding_factor
        mg.step_min_width_km = self.step_min_width_km
        mg.step_max_distance_farthest_km = self.step_max_distance_farthest_km
        mg.step_cluster_distance_km = self.step_cluster_distance_km
        mg.step_render_scale = self.step_render_scale
        mg.debug_map = self.debug_map
        mg._marker_image_cache_items = self._marker_image_cache_items
        return mg


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
                            r = self._http_get(url, timeout=6)
                            if r is not None and r.status_code == 200:
                                tile_img = Image.open(io.BytesIO(r.content)).convert("RGBA")
                                tile = tile_img.copy()
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

    def _project_to_image_pixel(self, lon: float, lat: float, zoom: int, center: tuple, width: int, height: int) -> Optional[Tuple[float, float]]:
        try:
            if center is None:
                return None
            center_lon, center_lat = center
            pt_px = self._lonlat_to_pixel(lon, lat, zoom)
            center_px = self._lonlat_to_pixel(center_lon, center_lat, zoom)
            world_px = 256 * (2 ** int(zoom))
            dx = pt_px[0] - center_px[0]
            if dx > (world_px / 2.0):
                dx -= world_px
            elif dx < (-world_px / 2.0):
                dx += world_px
            dy = pt_px[1] - center_px[1]
            x = (width / 2.0) + dx
            y = (height / 2.0) + dy
            return (x, y)
        except Exception:
            return None

    @staticmethod
    def _color_to_rgba(color, alpha: int = 255) -> tuple:
        try:
            if isinstance(color, (tuple, list)):
                if len(color) == 4:
                    return (int(color[0]), int(color[1]), int(color[2]), int(color[3]))
                if len(color) == 3:
                    return (int(color[0]), int(color[1]), int(color[2]), int(alpha))
            if isinstance(color, str) and color.startswith("#") and len(color) == 7:
                r = int(color[1:3], 16)
                g = int(color[3:5], 16)
                b = int(color[5:7], 16)
                return (r, g, b, int(alpha))
        except Exception:
            pass
        return (255, 255, 255, int(alpha))

    def _draw_markers_on_image(self, base_image: Image.Image, markers: List[dict], zoom: int, center: tuple) -> Image.Image:
        if not markers:
            return base_image
        try:
            base = base_image.convert("RGBA")
            draw = ImageDraw.Draw(base)
            width, height = base.size
            for marker in markers:
                lon = marker.get("lon")
                lat = marker.get("lat")
                marker_px = int(marker.get("marker_px", 0) or 0)
                marker_radius = int(marker.get("marker_radius", 0) or 0)
                if lon is None or lat is None:
                    continue
                pos = self._project_to_image_pixel(lon, lat, zoom, center, width, height)
                if not pos:
                    continue
                x, y = pos
                if x < -marker_px or x > (width + marker_px) or y < -marker_px or y > (height + marker_px):
                    continue

                halo_color = marker.get("halo_color")
                halo_radius = marker.get("halo_radius")
                if halo_color and halo_radius:
                    hc = self._color_to_rgba(halo_color)
                    r = float(halo_radius)
                    draw.ellipse([x - r, y - r, x + r, y + r], fill=hc)

                thumb = marker.get("thumb")
                if thumb:
                    try:
                        img = self._load_marker_image(Path(thumb), size=marker_px if marker_px else None)
                        if img is not None:
                            ox = int(round(x - (img.width / 2.0)))
                            oy = int(round(y - (img.height / 2.0)))
                            base.alpha_composite(img, dest=(ox, oy))
                    except Exception:
                        pass
                else:
                    color = self._color_to_rgba(marker.get("color"))
                    r = float(marker_radius)
                    draw.ellipse([x - r, y - r, x + r, y + r], fill=color)

                ring_overlay = marker.get("ring_overlay")
                if ring_overlay:
                    try:
                        img = self._load_marker_image(Path(ring_overlay))
                        if img is not None:
                            ox = int(round(x - (img.width / 2.0)))
                            oy = int(round(y - (img.height / 2.0)))
                            base.alpha_composite(img, dest=(ox, oy))
                    except Exception:
                        pass
            return base
        except Exception:
            return base_image

    def _draw_route_on_image(self, base_image: Image.Image, route_coords: List[tuple], zoom: int, center: tuple, line_color: str, line_width: int, outline_color: Optional[str] = None, outline_width: Optional[int] = None) -> Image.Image:
        if not route_coords:
            return base_image
        try:
            base = base_image.convert("RGBA")
            draw = ImageDraw.Draw(base)
            width, height = base.size
            points: List[tuple] = []
            for lon, lat in route_coords:
                pos = self._project_to_image_pixel(lon, lat, zoom, center, width, height)
                if pos:
                    points.append(pos)
            if len(points) < 2:
                return base

            if outline_color and outline_width and outline_width > 0:
                oc = self._color_to_rgba(outline_color)
                draw.line(points, fill=oc, width=int(outline_width), joint="curve")

            lc = self._color_to_rgba(line_color)
            draw.line(points, fill=lc, width=int(line_width), joint="curve")
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

    def _find_neighbor_index(self, trip_parser: TripParser, step_index: int, direction: int, coords_cache: Optional[List[Optional[tuple]]] = None) -> Optional[int]:
        """Find the nearest previous/next step that has coordinates.

        direction: -1 for previous, +1 for next
        """
        if direction not in (-1, 1):
            return None
        if not (0 <= step_index < len(trip_parser.steps)):
            return None

        i = step_index + direction
        while 0 <= i < len(trip_parser.steps):
            if coords_cache is not None and 0 <= i < len(coords_cache):
                coord = coords_cache[i]
            else:
                coord = self._extract_lon_lat(trip_parser.steps[i])
            if coord:
                return i
            i += direction

        return None

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
            return 12
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
            test_url = url_template.format(z=2, x=1, y=1)
            r = self._http_get(test_url, timeout=5)
            if r is None:
                return False
            ctype = r.headers.get("content-type", "")
            return r.status_code == 200 and ctype.startswith("image")
        except Exception:
            return False

    def _create_map(self, width: int = None, height: int = None) -> "object":
        """Create a StaticMap with configured tiles. If the configured tile provider
        is unavailable, fall back to road tiles to keep map generation working."""
        if StaticMap is None:
            lang = getattr(self, "lang", get_default_language_manager())
            raise RuntimeError(lang.t("render.staticmap_missing"))
        w = int(round((width or self.width)))
        h = int(round((height or self.height)))

        url = self.url_template
        # If satellite/hybrid fails, try road tiles as a fallback (keeps generation usable)
        if url == ESRI_SATELLITE_URL and not self._is_tile_available(url):
            lang = getattr(self, "lang", get_default_language_manager())
            print(lang.t("map.satellite_unavailable"))
            url = ESRI_ROAD_URL

        return StaticMap(
            w, h,
            url_template=url,
            tile_size=256
        )

    def generate_overview_map(self, trip_parser: TripParser) -> bytes:
        """Generate overview map with route and step markers.
        
        Uses the new Geographic Bounding Box system:
        1. Collect all step locations
        2. Compute bounds with padding and configured aspect ratio
        3. Render map at computed center/zoom
        """
        w = int(round(getattr(self, "overview_width", self.width)))
        h = int(round(getattr(self, "overview_height", self.height)))
        m = self._create_map(w, h)

        # Marker size (pixels) for padding calculations
        pixel_scale = float(w) / float(max(1, getattr(self, 'overview_width', self.width)))
        marker_px = max(8, int(round(self.marker_thumb_size * pixel_scale)))
        extra_pad_px = max(6, int(round(marker_px * 0.6)))

        # Collect step locations for viewport calculation
        step_locations: List[StepLocation] = []
        coords_cache = self._get_trip_step_coords(trip_parser)
        for i, coord in enumerate(coords_cache):
            if coord:
                lon, lat = coord
                step_locations.append(StepLocation(lat=lat, lon=lon, step_id=str(i)))
        
        # Compute viewport using new bounding-box system
        if step_locations:
            try:
                viewport = compute_overview_viewport(
                    steps=step_locations,
                    padding_factor=float(getattr(self, 'overview_padding_factor', 0.10)),
                    min_width_km=float(getattr(self, 'overview_min_width_km', 10.0)),
                    viewport_width_px=w,
                    viewport_height_px=h,
                    aspect_ratio=float(getattr(self, "overview_aspect_ratio", MAP_ASPECT_RATIO)),
                    extra_padding_px=extra_pad_px,
                )
                zoom = max(0, min(19, viewport.zoom))
                center = (viewport.center_lon, viewport.center_lat)
                
                if getattr(self, 'debug_map', False):
                    lang = getattr(self, "lang", get_default_language_manager())
                    print(lang.t(
                        "map.overview_debug",
                        count=len(step_locations),
                        width=viewport.bounds.width_km(),
                        zoom=zoom,
                        center=center,
                    ))
            except Exception as e:
                if getattr(self, 'debug_map', False):
                    lang = getattr(self, "lang", get_default_language_manager())
                    print(lang.t("map.overview_calc_failed", error=e))
                zoom = 12
                center = None
        else:
            zoom = 12
            center = None

        # Add route line (white only for overview; outline omitted to keep map clean)
        route_coords = self._get_trip_route_coords(trip_parser)
        if len(route_coords) > 1:
            line = Line(route_coords, ROUTE_COLOR, ROUTE_LINE_WIDTH)
            m.add_line(line)

        # Add step markers (use photo thumbnails when possible)
        draw_markers_on_top = bool(self.label_overlay_url and center is not None)
        markers_to_draw: List[dict] = []
        for i, step in enumerate(trip_parser.steps):
            step_data = step["data"]
            coord = coords_cache[i] if i < len(coords_cache) else self._extract_lon_lat(step)
            if coord:
                lon, lat = coord
                # create thumbnail (white ring); prefer IconMarker when available
                # marker_px is absolute pixels (configured by marker_thumb_size) scaled by render pixel scale
                thumb = self._get_step_thumbnail(step, size=marker_px, ring_color=(255,255,255,230))
                if thumb and (IconMarker is not None or draw_markers_on_top):
                    if draw_markers_on_top:
                        markers_to_draw.append({
                            "lon": lon,
                            "lat": lat,
                            "thumb": thumb,
                            "marker_px": marker_px,
                            "marker_radius": max(4, int(round(marker_px * 0.3))),
                            "color": MARKER_COLOR_START if i == 0 else MARKER_COLOR_STEP,
                        })
                        continue
                    else:
                        off_x = int(marker_px / 2)
                        off_y = int(marker_px / 2)
                        try:
                            m.add_marker(IconMarker((lon, lat), str(thumb), off_x, off_y))
                            continue
                        except Exception:
                            pass

                # fallback to circle marker (no red in overview map)
                color = MARKER_COLOR_START if i == 0 else MARKER_COLOR_STEP
                # Use an absolute radius proportional to thumbnail size
                marker_radius = max(4, int(round(marker_px * 0.3)))
                if draw_markers_on_top:
                    markers_to_draw.append({
                        "lon": lon,
                        "lat": lat,
                        "thumb": None,
                        "marker_px": marker_px,
                        "marker_radius": marker_radius,
                        "color": color,
                    })
                else:
                    m.add_marker(CircleMarker((lon, lat), color, marker_radius))

        # Render map
        if center is not None:
            image = m.render(zoom=zoom, center=center)
            image = self._apply_label_overlay(image, zoom, center)
            if draw_markers_on_top and len(route_coords) > 1:
                image = self._draw_route_on_image(image, route_coords, zoom, center, ROUTE_COLOR, ROUTE_LINE_WIDTH)
            if draw_markers_on_top:
                image = self._draw_markers_on_image(image, markers_to_draw, zoom, center)
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

        cache_dir = get_cache_dir("map_marker")

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
                    r = self._http_get(url, timeout=10)
                    if r is not None and r.status_code == 200:
                        cache_dir = get_cache_dir("map_marker")
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
            cache_dir = get_cache_dir("map_marker")
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
                return 12

            # degrees per pixel needed for lon
            dpp_lon = lon_span / float(max(width_px, 1))
            z_lon = math.log2(360.0 / (256.0 * dpp_lon)) if dpp_lon > 0 else 12

            # account for latitude using cosine of center lat
            center_lat = (min_lat + max_lat) / 2.0
            cos_lat = max(0.01, abs(math.cos(math.radians(center_lat))))
            dpp_lat = lat_span / float(max(height_px, 1))
            # rough adjustment for lat
            z_lat = math.log2(360.0 / (256.0 * (dpp_lat / cos_lat))) if dpp_lat > 0 else z_lon

            z = int(min(z_lon, z_lat))
        except Exception:
            z = 12

        # clamp to global allowed zoom range (0..19)
        z = max(0, min(19, z))
        return z

    def generate_step_map_for_step(self, trip_parser: TripParser, step_index: int, width: int = 0, height: int = 0) -> bytes:
        """Generate a map for a specific step using Geographic Bounding Box approach.
        
        NEW DISTANCE-BASED SYSTEM (2026):
        1. Get current step + immediate prev/next neighbors
        2. If distance between farthest steps <= max_distance_farthest_steps_km, include all
        3. Otherwise drop the neighbor farthest from current and re-check
        4. If remaining neighbor still exceeds threshold, show only current step
        5. Center map on geographic midpoint of all visible steps
        6. ALWAYS draw path from prev -> current -> next (regardless of viewport bounds)

        Args:
            trip_parser: The trip data parser
            step_index: 0-based index of the current step
            width: Override viewport width (uses self.width if 0)
            height: Override viewport height (uses self.height if 0)
            padding: Ignored in new system (uses step_padding_factor from config)
        """
        if StaticMap is None:
            lang = getattr(self, "lang", get_default_language_manager())
            raise RuntimeError(lang.t("render.staticmap_missing"))

        w = width or getattr(self, "step_width", self.width)
        h = height or getattr(self, "step_height", self.height)

        # Extract current step coordinates
        coords_cache = self._get_trip_step_coords(trip_parser)
        current_coord = coords_cache[step_index] if (0 <= step_index < len(coords_cache)) else None
        if not current_coord:
            m = self._create_map(w, h)
            image = m.render()
            img_bytes = io.BytesIO()
            image.save(img_bytes, format="PNG")
            img_bytes.seek(0)
            return img_bytes.getvalue()

        # Get immediate neighbors (n=1 only; skip steps without coordinates)
        prev_idx = self._find_neighbor_index(trip_parser, step_index, -1, coords_cache=coords_cache)
        next_idx = self._find_neighbor_index(trip_parser, step_index, +1, coords_cache=coords_cache)
        prev_coord = coords_cache[prev_idx] if prev_idx is not None else None
        next_coord = coords_cache[next_idx] if next_idx is not None else None

        # Create StepLocation objects for viewport calculation
        current_step = StepLocation(lat=current_coord[1], lon=current_coord[0], step_id=str(step_index))
        prev_step = StepLocation(lat=prev_coord[1], lon=prev_coord[0], step_id=str(prev_idx)) if prev_coord else None
        next_step = StepLocation(lat=next_coord[1], lon=next_coord[0], step_id=str(next_idx)) if next_coord else None

        # Marker size (pixels) for padding calculations
        pixel_scale = float(w) / float(max(1, getattr(self, 'step_width', self.width)))
        marker_px = max(8, int(round(self.marker_thumb_size * pixel_scale)))
        extra_pad_px = max(6, int(round(marker_px * 0.6)))

        # Compute viewport using distance-based step selection system
        try:
            viewport = compute_step_viewport(
                current_step=current_step,
                prev_step=prev_step,
                next_step=next_step,
                max_distance_farthest_km=float(getattr(self, 'step_max_distance_farthest_km', 100.0)),
                min_width_km=float(getattr(self, 'step_min_width_km', 2.0)),
                cluster_distance_km=float(getattr(self, 'step_cluster_distance_km', 5.0)),
                padding_factor=float(getattr(self, 'step_padding_factor', 0.10)),
                viewport_width_px=w,
                viewport_height_px=h,
                aspect_ratio=float(getattr(self, "step_aspect_ratio", MAP_ASPECT_RATIO)),
                extra_padding_px=extra_pad_px,
            )
            zoom = max(0, min(19, viewport.zoom))
            # Note: We no longer enforce min_zoom here because it can cause markers
            # to be cut off. The viewport calculation already ensures proper fit.
            # If you want more detail, reduce max_distance_farthest_steps_km instead.
            center = (viewport.center_lon, viewport.center_lat)
            
            if getattr(self, 'debug_map', False):
                lang = getattr(self, "lang", get_default_language_manager())
                print(lang.t(
                    "map.step_debug",
                    index=step_index,
                    width=viewport.bounds.width_km(),
                    zoom=zoom,
                    lat=center[1],
                    lon=center[0],
                    prev=lang.t("general.yes") if prev_step else lang.t("general.no"),
                    next=lang.t("general.yes") if next_step else lang.t("general.no"),
                ))
        except Exception as e:
            if getattr(self, 'debug_map', False):
                lang = getattr(self, "lang", get_default_language_manager())
                print(lang.t("map.step_calc_failed", error=e))
            zoom = 12
            center = (current_coord[0], current_coord[1])

        m = self._create_map(w, h)

        # ALWAYS draw route line for context (prev -> current -> next)
        # This is independent of whether neighbors are in viewport bounds
        route_coords = self._get_trip_route_coords(trip_parser)
        if len(route_coords) > 1:
            outline = Line(route_coords, ROUTE_OUTLINE_COLOR, ROUTE_OUTLINE_WIDTH)
            m.add_line(outline)
            line = Line(route_coords, ROUTE_COLOR, ROUTE_LINE_WIDTH)
            m.add_line(line)

        # Add all step markers; draw current last so it's always on top.
        # marker_px and marker_radius are absolute pixels (configured by marker_thumb_size) scaled by render pixel scale
        marker_radius = max(4, int(round(marker_px * 0.3)))
        normal_indices = [i for i in range(len(trip_parser.steps)) if i != step_index]
        draw_order = normal_indices + ([step_index] if 0 <= step_index < len(trip_parser.steps) else [])
        draw_markers_on_top = bool(self.label_overlay_url)
        markers_to_draw: List[dict] = []
        
        for i in draw_order:
            st = trip_parser.steps[i]
            coord = coords_cache[i] if i < len(coords_cache) else self._extract_lon_lat(st)
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
                if draw_markers_on_top:
                    pass
                else:
                    try:
                        m.add_marker(CircleMarker((lon, lat), MISSING_PHOTO_COLOR, marker_radius + 4))
                    except Exception:
                        pass

            if thumb and (IconMarker is not None or draw_markers_on_top):
                if draw_markers_on_top:
                    overlay = None
                    if is_current and has_photo:
                        try:
                            overlay = self._get_ring_overlay(
                                marker_px + 8,
                                color=MISSING_PHOTO_COLOR,
                                # thickness in absolute pixels (small fraction of marker size)
                                thickness=max(2, int(round(marker_px * 0.05)))
                            )
                        except Exception:
                            overlay = None
                    markers_to_draw.append({
                        "lon": lon,
                        "lat": lat,
                        "thumb": thumb,
                        "marker_px": marker_px,
                        "marker_radius": marker_radius,
                        "color": MARKER_COLOR_START if i == 0 else ("#FF4D4F" if is_current else MARKER_COLOR_STEP),
                        "halo_color": MISSING_PHOTO_COLOR if (is_current and has_photo) else None,
                        "halo_radius": (marker_radius + 4) if (is_current and has_photo) else None,
                        "ring_overlay": overlay,
                    })
                    continue
                else:
                    off_x = int(marker_px / 2)
                    off_y = int(marker_px / 2)
                    try:
                        m.add_marker(IconMarker((lon, lat), str(thumb), off_x, off_y))
                        # If this is the current step with a photo, overlay a ring image on top
                        if is_current and has_photo:
                            try:
                                overlay = self._get_ring_overlay(
                                    marker_px + 8, 
                                    color=MISSING_PHOTO_COLOR, 
                                    # thickness in absolute pixels (small fraction of marker size)
                                    thickness=max(2, int(round(marker_px * 0.05)))
                                )
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
            if draw_markers_on_top:
                markers_to_draw.append({
                    "lon": lon,
                    "lat": lat,
                    "thumb": None,
                    "marker_px": marker_px,
                    "marker_radius": marker_radius,
                    "color": color,
                    "halo_color": MISSING_PHOTO_COLOR if (is_current and has_photo) else None,
                    "halo_radius": (marker_radius + 4) if (is_current and has_photo) else None,
                    "ring_overlay": None,
                })
            else:
                m.add_marker(CircleMarker((lon, lat), color, marker_radius))

        # Render map
        image = m.render(zoom=zoom, center=center)
        image = self._apply_label_overlay(image, zoom, center)
        if draw_markers_on_top and len(route_coords) > 1:
            image = self._draw_route_on_image(
                image,
                route_coords,
                zoom,
                center,
                ROUTE_COLOR,
                ROUTE_LINE_WIDTH,
                outline_color=ROUTE_OUTLINE_COLOR,
                outline_width=ROUTE_OUTLINE_WIDTH,
            )
        if draw_markers_on_top:
            image = self._draw_markers_on_image(image, markers_to_draw, zoom, center)
        
        img_bytes = io.BytesIO()
        image.save(img_bytes, format="PNG")
        img_bytes.seek(0)
        return img_bytes.getvalue()


# Back-compat helper in case other modules need dates from a TripParser
def trip_parser_get_dates(trip_path: Path):
    tp = TripParser(trip_path)
    tp.load()
    return tp.get_trip_dates() if hasattr(tp, 'get_trip_dates') else (None, None)


class HtmlPDFBuilder:
    """Builds the PDF document using HTML/CSS rendered by Playwright (Chromium)."""

    def __init__(self, output_path: Path, trip_parser: TripParser, map_generator: MapGenerator, config: dict = None, language_manager: LanguageManager = None):
        self.output_path = Path(output_path)
        self.trip_parser = trip_parser
        self.map_generator = map_generator
        self.config = config or {}
        self.lang = language_manager or get_default_language_manager()

        # Layout options
        self.max_photos_per_step = int(self.config.get("max_photos_per_step", 6))
        self.appendix_show_undisplayed_media = bool(self.config.get("appendix_show_undisplayed_media", True))
        self.photo_max_width = int(self.config.get("html_photo_max_width", 1200))
        self._memory_cache_items = int(self.config.get("html_memory_cache_items", 256))
        self._image_data_cache = OrderedDict()
        self._map_data_cache = OrderedDict()
        self._photo_workers = int(self.config.get("html_photo_workers", 4))
        try:
            default_map_workers = max(1, min(4, int(os.cpu_count() or 4)))
        except Exception:
            default_map_workers = 2
        self._map_workers = int(self.config.get("html_map_workers", default_map_workers))
        self._map_thread_local = threading.local()

    def _cache_get(self, cache: OrderedDict, key):
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        return None

    def _cache_set(self, cache: OrderedDict, key, value):
        if self._memory_cache_items <= 0:
            return
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._memory_cache_items:
            cache.popitem(last=False)

    def _image_bytes_to_data_url(self, data: bytes, mime: str = "image/png") -> str:
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _get_thread_map_generator(self) -> MapGenerator:
        mg = getattr(self._map_thread_local, "map_generator", None)
        if mg is None:
            mg = self.map_generator.clone()
            self._map_thread_local.map_generator = mg
        return mg

    def _map_bytes_to_data_url(self, data: bytes, mime: str = "image/png") -> str:
        try:
            key = (hashlib.sha1(data).hexdigest(), mime)
            cached = self._cache_get(self._map_data_cache, key)
            if cached is not None:
                return cached
        except Exception:
            key = None
        url = self._image_bytes_to_data_url(data, mime=mime)
        if key is not None:
            self._cache_set(self._map_data_cache, key, url)
        return url

    def _image_file_to_data_url(self, path: Path) -> Optional[str]:
        try:
            key = None
            try:
                stat = path.stat()
                key = (str(path), int(stat.st_mtime_ns), self.photo_max_width)
                cached = self._cache_get(self._image_data_cache, key)
                if cached is not None:
                    return cached
            except Exception:
                key = None
            with Image.open(path) as img:
                img = img.convert("RGB")
                if self.photo_max_width > 0:
                    if img.width > self.photo_max_width:
                        ratio = float(self.photo_max_width) / float(img.width)
                        new_h = max(1, int(round(img.height * ratio)))
                        img = img.resize((self.photo_max_width, new_h), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                buf.seek(0)
                url = self._image_bytes_to_data_url(buf.read(), mime="image/jpeg")
                if key is not None:
                    self._cache_set(self._image_data_cache, key, url)
                return url
        except Exception:
            return None

    def _escape(self, text: str) -> str:
        return html.escape(text or "")

    def _format_weather(self, condition: str, temperature: float) -> str:
        """Format weather info as plain text (no emoji)."""
        label = self.lang.t(f"weather.label.{condition}")
        if label == f"weather.label.{condition}":
            label = self.lang.t("weather.label.default")
        try:
            return f"{label}, {float(temperature):.0f}°C"
        except Exception:
            return f"{label}"

    def _linkify(self, text: str) -> str:
        """Convert URLs in text to clickable anchor tags (text must already be HTML-escaped)."""
        # Pattern to match URLs (http, https, www)
        url_pattern = r'(https?://[^\s<>"]+|www\.[^\s<>"]+)'
        
        def replace_url(match):
            url = match.group(1)
            # Add http:// prefix for www. links
            href = url if url.startswith('http') else f'http://{url}'
            return f'<a class="link" href="{href}">{url}</a>'
        
        return re.sub(url_pattern, replace_url, text)

    def _build_description_html(self, text: str) -> str:
        if not text:
            return ""

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

        parts = []
        for kind, data in blocks:
            if kind == "para":
                safe = self._escape(data).replace("\n", "<br/>")
                safe = self._linkify(safe)
                parts.append(f"<p class=\"step-desc\">{safe}</p>")
            else:
                items_html = "".join(
                    f"<li>{self._linkify(self._escape(item))}</li>" for item in data
                )
                parts.append(f"<ul class=\"step-list\">{items_html}</ul>")

        return "\n".join(parts)

    def _build_photo_grid_html(self, photo_paths: List[Path]) -> str:
        if not photo_paths:
            return ""
        items = []
        workers = max(1, min(int(self._photo_workers), len(photo_paths)))
        if workers > 1:
            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    urls = list(executor.map(self._image_file_to_data_url, photo_paths))
                for url in urls:
                    if url:
                        items.append(f"<img src=\"{url}\"/>")
            except Exception:
                for p in photo_paths:
                    try:
                        url = self._image_file_to_data_url(p)
                        if url:
                            items.append(f"<img src=\"{url}\"/>")
                    except Exception:
                        continue
        else:
            for p in photo_paths:
                try:
                    url = self._image_file_to_data_url(p)
                    if url:
                        items.append(f"<img src=\"{url}\"/>")
                except Exception:
                    continue
        if items:
            return f"<div class=\"photo-grid\">{''.join(items)}</div>"
        return ""

    def _build_html(self) -> str:
        trip_name = self.trip_parser.get_trip_name()
        start_date, end_date = self.trip_parser.get_trip_dates()
        total_km = self.trip_parser.get_total_km()
        step_count = len(self.trip_parser.steps)

        date_str = ""
        date_fmt = self.lang.get_date_format("date.format.trip", "%d.%m.%Y")
        if start_date and end_date:
            date_str = f"{start_date.strftime(date_fmt)} - {end_date.strftime(date_fmt)}"
        elif start_date:
            date_str = start_date.strftime(date_fmt)

        subtitle = self.lang.t(
            "pdf.subtitle",
            date=date_str,
            steps=step_count,
            steps_label=self.lang.t("pdf.steps_label"),
            km=total_km,
            km_label=self.lang.t("units.km"),
        )

        # Title page overview map
        overview_img = ""
        step_maps: dict = {}
        if step_count > 0 and self._map_workers > 1:
            step_width = int(getattr(self.map_generator, "step_width", self.map_generator.width))
            step_height = int(getattr(self.map_generator, "step_height", self.map_generator.height))
            render_scale = float(getattr(self.map_generator, "step_render_scale", 1.0))
            render_scale = max(1.0, min(4.0, render_scale))
            width_px = int(step_width * render_scale)
            height_px = int(step_height * render_scale)
            workers = max(1, min(int(self._map_workers), step_count + 1))

            def _render_overview_map():
                try:
                    print(self.lang.t("render.rendering_title_overview"))
                    t0 = time.perf_counter()
                    mg = self._get_thread_map_generator()
                    data = mg.generate_overview_map(self.trip_parser)
                    dt = time.perf_counter() - t0
                    print(self.lang.t("render.overview_done", seconds=dt))
                    return data
                except Exception:
                    return None

            def _render_step_map(idx: int):
                try:
                    mg = self._get_thread_map_generator()
                    data = mg.generate_step_map_for_step(
                        self.trip_parser,
                        idx,
                        width=width_px,
                        height=height_px,
                    )
                    return (idx, data)
                except Exception:
                    return (idx, None)

            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    overview_future = executor.submit(_render_overview_map)
                    futures = [executor.submit(_render_step_map, idx) for idx in range(step_count)]
                    for future in as_completed(futures):
                        idx, data = future.result()
                        if data:
                            step_maps[idx] = data
                    map_bytes = overview_future.result()
                    if map_bytes:
                        overview_img = f"<img class=\"map\" src=\"{self._map_bytes_to_data_url(map_bytes)}\"/>"
            except Exception:
                step_maps = {}
                overview_img = ""
        else:
            try:
                print(self.lang.t("render.rendering_title_overview"))
                t0 = time.perf_counter()
                map_bytes = self.map_generator.generate_overview_map(self.trip_parser)
                dt = time.perf_counter() - t0
                print(self.lang.t("render.overview_done", seconds=dt))
                if map_bytes:
                    overview_img = f"<img class=\"map\" src=\"{self._map_bytes_to_data_url(map_bytes)}\"/>"
            except Exception:
                overview_img = ""

        photo_wall_gap = int(self.config.get("photo_wall_gap", 0))
        photo_wall_columns = int(self.config.get("photo_wall_columns", 3))

        html_parts = [
            "<!doctype html>",
            "<html>",
            "<head>",
            "<meta charset=\"utf-8\"/>",
            "<style>",
            "@page { size: A4; margin: 15mm; }",
            "body { font-family: 'Segoe UI', 'Segoe UI Emoji', 'Segoe UI Symbol', sans-serif; color: #333; }",
            ".title { text-align: center; color: #1A5F7A; font-size: 28pt; margin-top: 20mm; }",
            ".subtitle { text-align: center; font-size: 14pt; margin-bottom: 10mm; }",
            ".map { width: 100%; height: auto; display: block; margin: 0 auto; }",
            ".page-break { page-break-after: always; }",
            ".step-title { color: #1A5F7A; font-size: 18pt; margin: 6mm 0 2mm; }",
            ".step-meta { color: #666; font-size: 10pt; margin: 0 0 4mm; }",
            ".step-desc { font-size: 11pt; line-height: 1.35; margin: 0 0 4mm; }",
            ".step-list { margin: 0 0 4mm 6mm; }",
            f".photo-grid {{ column-count: {photo_wall_columns}; column-gap: {photo_wall_gap}px; margin: 2mm 0 4mm; }}",
            f".photo-grid img {{ width: 100%; height: auto; display: block; break-inside: avoid; margin: 0 0 {photo_wall_gap}px 0; }}",
            ".appendix-title { color: #1A5F7A; font-size: 20pt; margin: 4mm 0 2mm; }",
            ".appendix-subtitle { color: #666; font-size: 10pt; margin: 0 0 4mm; }",
            ".appendix-step-title { color: #1A5F7A; font-size: 14pt; margin: 6mm 0 2mm; }",
            ".video-header { margin-top: 3mm; font-weight: 600; }",
            ".video-link { display: block; color: #0066CC; text-decoration: none; font-size: 10pt; }",
            "a.link { color: #0066CC; text-decoration: none; }",
            "a.link:hover { text-decoration: underline; }",
            "</style>",
            "</head>",
            "<body>",
            f"<div class=\"title\">{self._escape(trip_name)}</div>",
            f"<div class=\"subtitle\">{subtitle}</div>",
            overview_img,
            "<div class=\"page-break\"></div>",
        ]

        appendix_items = []
        for i, step in enumerate(self.trip_parser.steps):
            step_number = i + 1
            step_data = step.get("data", {}) if isinstance(step, dict) else {}
            photos = step.get("photos", []) if isinstance(step, dict) else []
            videos = step.get("videos", []) if isinstance(step, dict) else []

            display_name = step_data.get("display_name", f"{self.lang.t('pdf.step_label')} {step_number}")
            title_text = f"{step_number}. {display_name}"

            print(self.lang.t("render.rendering_step", current=step_number, total=step_count, name=display_name))

            location = step_data.get("location", {}) if isinstance(step_data, dict) else {}
            location_name = location.get("name", "") if isinstance(location, dict) else ""
            location_detail = location.get("detail", "") if isinstance(location, dict) else ""

            start_time = step_data.get("start_time") if isinstance(step_data, dict) else None
            date_str = ""
            if start_time:
                try:
                    date_fmt = self.lang.get_date_format("date.format.step", "%A, %d. %B %Y")
                    date_str = datetime.fromtimestamp(start_time).strftime(date_fmt)
                except Exception:
                    date_str = ""

            weather_condition = step_data.get("weather_condition") if isinstance(step_data, dict) else None
            weather_temp = step_data.get("weather_temperature") if isinstance(step_data, dict) else None
            weather_text = ""
            if weather_condition and weather_temp is not None:
                weather_text = self._format_weather(weather_condition, weather_temp)

            location_parts = [p for p in (location_name, location_detail) if p]
            location_text = ", ".join(location_parts)
            meta_parts = []
            if location_text:
                meta_parts.append(self.lang.t("pdf.meta_location", location=location_text))
            if date_str:
                meta_parts.append(self.lang.t("pdf.meta_date", date=date_str))
            if weather_text:
                meta_parts.append(self.lang.t("pdf.meta_weather", weather=weather_text))
            meta_text = " • ".join(meta_parts)

            step_map_html = ""
            try:
                map_bytes = step_maps.get(step_number - 1)
                if map_bytes is None:
                    step_width = int(getattr(self.map_generator, "step_width", self.map_generator.width))
                    step_height = int(getattr(self.map_generator, "step_height", self.map_generator.height))
                    render_scale = float(getattr(self.map_generator, "step_render_scale", 1.0))
                    render_scale = max(1.0, min(4.0, render_scale))
                    map_bytes = self.map_generator.generate_step_map_for_step(
                        self.trip_parser,
                        step_number - 1,
                        width=int(step_width * render_scale),
                        height=int(step_height * render_scale)
                    )
                if map_bytes:
                    step_map_html = f"<img class=\"map\" src=\"{self._map_bytes_to_data_url(map_bytes)}\"/>"
            except Exception:
                step_map_html = ""

            description = step_data.get("description", "") if isinstance(step_data, dict) else ""
            desc_html = self._build_description_html(description)

            # Photo grid
            photos_to_show = photos[: self.max_photos_per_step]
            extra_photos = photos[self.max_photos_per_step :]
            photo_html = self._build_photo_grid_html([Path(p) for p in photos_to_show])

            if self.appendix_show_undisplayed_media and (extra_photos or videos):
                appendix_items.append({
                    "step_number": step_number,
                    "display_name": display_name or f"{self.lang.t('pdf.step_label')} {step_number}",
                    "extra_photos": extra_photos,
                    "videos": videos,
                })

            html_parts.extend([
                "<div class=\"step\">",
                f"<div class=\"step-title\">{self._escape(title_text)}</div>",
                f"<div class=\"step-meta\">{self._escape(meta_text)}</div>",
                step_map_html,
                desc_html,
                photo_html,
                "</div>",
                "<div class=\"page-break\"></div>",
            ])

        if self.appendix_show_undisplayed_media and appendix_items:
            html_parts.extend([
                "<div class=\"page-break\"></div>",
                "<div class=\"appendix\">",
                f"<div class=\"appendix-title\">{self._escape(self.lang.t('pdf.additional_media_title'))}</div>",
                f"<div class=\"appendix-subtitle\">{self._escape(self.lang.t('pdf.additional_media_subtitle'))}</div>",
            ])
            for item in appendix_items:
                appendix_title = f"{item['step_number']}. {item['display_name']}"
                html_parts.append(f"<div class=\"appendix-step-title\">{self._escape(appendix_title)}</div>")

                extra_photos = item.get("extra_photos", [])
                if extra_photos:
                    extra_html = self._build_photo_grid_html([Path(p) for p in extra_photos])
                    if extra_html:
                        html_parts.append(extra_html)

                videos = item.get("videos", [])
                if videos:
                    links = []
                    for video_path in videos:
                        try:
                            file_url = Path(video_path).resolve().as_uri()
                        except Exception:
                            file_url = str(video_path)
                        name = Path(video_path).name
                        links.append(f"<a class=\"video-link\" href=\"{self._escape(file_url)}\">{self._escape(name)}</a>")
                    html_parts.append(
                        f"<div class=\"video-header\">📹 {self._escape(self.lang.t('pdf.videos_label'))}</div>" + "".join(links)
                    )
            html_parts.append("</div>")

        html_parts.append("</body></html>")
        return "\n".join([p for p in html_parts if p is not None])

    def build(self):
        if sync_playwright is None:
            raise RuntimeError(self.lang.t("render.playwright_missing"))

        t0 = time.perf_counter()
        html_doc = self._build_html()
        t1 = time.perf_counter()
        print(self.lang.t("render.html_build_done", seconds=t1 - t0))

        # Write HTML to a temp file and load via file:// URL to avoid Chromium crashes
        # when passing very large HTML strings via set_content()
        temp_dir = get_temp_dir("html")
        temp_html = temp_dir / f"render_{os.getpid()}.html"
        try:
            temp_html.write_text(html_doc, encoding="utf-8")
        except Exception as e:
            raise RuntimeError(self.lang.t("render.temp_html_write_failed", error=e))

        html_file_url = temp_html.as_uri()

        try:
            with sync_playwright() as p:
                last_error = None
                for attempt in range(1, 3):
                    browser = None
                    try:
                        browser = p.chromium.launch()
                        page = browser.new_page()
                        # Navigate to file instead of set_content to handle large docs better
                        page.goto(html_file_url, wait_until="domcontentloaded", timeout=120000)
                        try:
                            page.wait_for_load_state("load", timeout=120000)
                        except Exception:
                            pass
                        t_pdf = time.perf_counter()
                        page.pdf(
                            path=str(self.output_path),
                            format="A4",
                            margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
                            print_background=True,
                        )
                        print(self.lang.t("render.pdf_render_done", seconds=time.perf_counter() - t_pdf))
                        browser.close()
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        try:
                            if browser is not None:
                                browser.close()
                        except Exception:
                            pass
                        if attempt < 2:
                            print(self.lang.t("render.html_render_retry"))
                if last_error is not None:
                    raise last_error
        finally:
            # Clean up temp HTML file
            try:
                temp_html.unlink(missing_ok=True)
            except Exception:
                pass

        # Optionally open the rendered PDF file after creation (config key: open_pdf_after_render)
        try:
            open_after = bool(self.config.get("open_pdf_after_render", True))
        except Exception:
            open_after = True

        if open_after:
            try:
                t_open = time.perf_counter()
                if os.name == "nt":
                    # Windows
                    os.startfile(str(self.output_path))
                elif sys.platform == "darwin":
                    subprocess.run(["open", str(self.output_path)], check=False)
                else:
                    # Linux/Unix
                    subprocess.run(["xdg-open", str(self.output_path)], check=False)
                print(self.lang.t("render.open_pdf_done", seconds=time.perf_counter() - t_open))
            except Exception as e:
                print(self.lang.t("render.open_pdf_failed", error=e))


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
            print(get_default_language_manager().t("cache.save_failed", error=e))
    
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


def parse_render_command(cmd_str: str, trips: list, cache_manager: CacheManager, lang: LanguageManager = None) -> dict:
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
        'error_code': None,
        'trips': [],
        'include_rendered': True,  # default: include rendered trips (use -ur to restrict)
        'year': None,
        'start_date': None,
        'end_date': None,
        'selection': None,
        'config_overrides': {}
    }

    lang = lang or get_default_language_manager()

    # Remove 'render' or 'r' prefix
    cmd = cmd_str.strip()
    if cmd.lower().startswith('render'):
        cmd = cmd[6:].strip()
    elif cmd.lower().startswith('r ') or cmd.lower() == 'r':
        cmd = cmd[1:].strip()
    else:
        result['error'] = lang.t("parse.command_must_start")
        result['error_code'] = "command_must_start"
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
                    result['error'] = lang.t("parse.invalid_year", year=parts[i + 1])
                    result['error_code'] = "invalid_year"
                    return result
            else:
                result['error'] = lang.t("parse.year_requires_value")
                result['error_code'] = "year_requires_value"
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
                            result['error'] = lang.t("parse.invalid_date_format_semicolon")
                            result['error_code'] = "invalid_date_format_semicolon"
                            return result
                    else:
                        result['error'] = lang.t("parse.date_range_required")
                        result['error_code'] = "date_range_required"
                        return result

                # Support "-d 01.01.2025 01.06.2025" (separate tokens)
                elif i + 2 < len(parts):
                    try:
                        result['start_date'] = datetime.strptime(date_token.strip(), "%d.%m.%Y")
                        result['end_date'] = datetime.strptime(parts[i + 2].strip(), "%d.%m.%Y")
                        i += 3
                    except ValueError:
                        result['error'] = lang.t("parse.invalid_date_format_space")
                        result['error_code'] = "invalid_date_format_space"
                        return result
                else:
                    result['error'] = lang.t("parse.date_range_required")
                    result['error_code'] = "date_range_required"
                    return result
            else:
                result['error'] = lang.t("parse.date_requires_range")
                result['error_code'] = "date_requires_range"
                return result
        elif p.startswith('-config'):
            # Support -config(key=value, key2=value2)
            # The token may contain spaces; gather until matching ')'
            mode_specified = True
            token = p
            inner = ''
            # If '(' is in current token, start collecting after it
            if '(' in token:
                after = token[token.find('(') + 1:]
                if ')' in after:
                    inner = after.split(')', 1)[0]
                    i += 1
                else:
                    inner = after
                    j = i + 1
                    found = False
                    while j < len(parts):
                        inner += ' ' + parts[j]
                        if ')' in parts[j]:
                            inner = inner.split(')', 1)[0]
                            i = j + 1
                            found = True
                            break
                        j += 1
                    if not found:
                        result['error'] = lang.t("parse.config_requires_parentheses")
                        result['error_code'] = "config_requires_parentheses"
                        return result
            else:
                result['error'] = lang.t("parse.config_invalid_usage")
                result['error_code'] = "config_invalid_usage"
                return result

            # Parse comma-separated key=value pairs (ignoring commas inside strings)
            import ast
            def _split_top_level_commas(s: str) -> list:
                items = []
                cur = ''
                in_str = None
                esc = False
                for ch in s:
                    if esc:
                        cur += ch
                        esc = False
                        continue
                    if ch == '\\':
                        cur += ch
                        esc = True
                        continue
                    if ch in ('"', "'"):
                        if in_str is None:
                            in_str = ch
                        elif in_str == ch:
                            in_str = None
                        cur += ch
                        continue
                    if ch == ',' and in_str is None:
                        items.append(cur)
                        cur = ''
                    else:
                        cur += ch
                if cur.strip():
                    items.append(cur)
                return items

            overrides = {}
            for item in _split_top_level_commas(inner):
                if '=' not in item:
                    continue
                k, v = item.split('=', 1)
                key = k.strip()
                val_s = v.strip()
                try:
                    val = ast.literal_eval(val_s)
                except Exception:
                    # Fallback: barewords as strings or booleans/numbers
                    val = val_s.strip('"').strip("'")
                    if val.lower() in ('true', 'false'):
                        val = True if val.lower() == 'true' else False
                    else:
                        try:
                            if '.' in val:
                                val = float(val)
                            else:
                                val = int(val)
                        except Exception:
                            pass
                overrides[key] = val

            result['config_overrides'] = overrides
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
        result['error'] = lang.t("parse.no_trips_match")
        result['error_code'] = "no_trips_match"
        return result

    # Apply selection (selection_str may include a trailing -config(...) override)
    if selection_str:
        # Extract -config(...) if it was appended to the selection (e.g., '67 -config(...)')
        sel = selection_str
        if '-config' in sel:
            cfg_idx = sel.find('-config')
            cfg_part = sel[cfg_idx:]
            sel = sel[:cfg_idx].strip()

            # parse cfg_part similar to flag parsing
            if cfg_part.startswith('-config') and '(' in cfg_part:
                inner = ''
                after = cfg_part[cfg_part.find('(') + 1:]
                if ')' in after:
                    inner = after.split(')', 1)[0]
                else:
                    # attempt to find closing paren (unlikely here), otherwise ignore
                    inner = after
                # parse pairs
                import ast
                def _split_top_level_commas(s: str) -> list:
                    items = []
                    cur = ''
                    in_str = None
                    esc = False
                    for ch in s:
                        if esc:
                            cur += ch
                            esc = False
                            continue
                        if ch == '\\':
                            cur += ch
                            esc = True
                            continue
                        if ch in ('"', "'"):
                            if in_str is None:
                                in_str = ch
                            elif in_str == ch:
                                in_str = None
                            cur += ch
                            continue
                        if ch == ',' and in_str is None:
                            items.append(cur)
                            cur = ''
                        else:
                            cur += ch
                    if cur.strip():
                        items.append(cur)
                    return items

                overrides = {}
                for item in _split_top_level_commas(inner):
                    if '=' not in item:
                        continue
                    k, v = item.split('=', 1)
                    key = k.strip()
                    val_s = v.strip()
                    try:
                        val = ast.literal_eval(val_s)
                    except Exception:
                        # Fallback: barewords as strings or booleans/numbers
                        val = val_s.strip('"').strip("'")
                        if val.lower() in ('true', 'false'):
                            val = True if val.lower() == 'true' else False
                        else:
                            try:
                                if '.' in val:
                                    val = float(val)
                                else:
                                    val = int(val)
                            except Exception:
                                pass
                    overrides[key] = val

                result['config_overrides'] = overrides

        # Use selection without inline config for parsing
        result['selection'] = sel
        indices = parse_selection(sel, len(filtered_trips))
        if not indices:
            result['error'] = lang.t("parse.invalid_selection", selection=sel)
            result['error_code'] = "invalid_selection"
            return result
        result['trips'] = [filtered_trips[i - 1] for i in indices]
    else:
        # No selection provided: require explicit mode (-a or -ur)
        if not mode_specified:
            result['error'] = lang.t("parse.no_selection_or_mode")
            result['error_code'] = "no_selection_or_mode"
            return result
        result['trips'] = filtered_trips

    result['valid'] = True
    return result


def display_trips(trips: list, cache_manager: CacheManager, title: str = None, lang: LanguageManager = None):
    """Display a numbered list of trips with rendered status."""
    lang = lang or get_default_language_manager()
    header_title = title or lang.t("cli.available_trips_title")
    print(f"\n{'='*70}")
    print(f"  {header_title}")
    print(f"{'='*70}")
    print(lang.t("cli.list_total", total=len(trips), rendered=cache_manager.get_rendered_count()) + "\n")

    for i, trip in enumerate(trips, 1):
        try:
            with open(trip / "trip.json", "r", encoding="utf-8") as f:
                trip_data = json.load(f)
            name = trip_data.get("name", trip.name)
            start_ts = trip_data.get("start_date", 0)
            date_fmt = lang.get_date_format("date.format.trip", "%d.%m.%Y")
            date_str = datetime.fromtimestamp(start_ts).strftime(date_fmt) if start_ts else "?"
            rendered_mark = "✓" if cache_manager.is_rendered(trip) else " "
            print(f"  [{i:2d}] [{rendered_mark}] {name} ({date_str})")
        except:
            rendered_mark = "✓" if cache_manager.is_rendered(trip) else " "
            print(f"  [{i:2d}] [{rendered_mark}] {trip.name}")
    print()


def print_command_help(lang: LanguageManager = None):
        """Print available commands."""
        lang = lang or get_default_language_manager()
        print(lang.t("cli.command_help"))


def prompt_loop(trips: list, cache_manager: CacheManager, script_dir: Path, config: dict, lang: LanguageManager):
    """Unified command prompt loop."""
    import sys
    
    # Display help and trips on start (show trips before first render)
    print_command_help(lang)
    display_trips(trips, cache_manager, lang.t("cli.trip_list_title"), lang=lang)

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
                    print(lang.t("cli.command_prompt"), end="", flush=True)
                cmd = input_queue.get()

            cmd = cmd.strip()
            if not cmd:
                continue
            
            cmd_lower = cmd.lower()
            
            # Exit commands
            if cmd_lower in ('cancel', 'exit', 'quit', 'q'):
                print(lang.t("cli.exiting"))
                break
            
            # Clear cache
            if cmd_lower == 'clear-cache':
                confirm = input(lang.t(
                    "cli.cache_clear_prompt",
                    yes=lang.t("general.yes"),
                    no=lang.t("general.no"),
                )).strip()
                if lang.is_yes(confirm):
                    cache_manager.clear_cache()
                    print(lang.t("cli.cache_cleared"))
                elif lang.is_no(confirm):
                    print(lang.t("cli.cancelled"))
                else:
                    print(lang.t("cli.cancelled"))
                continue
            
            # Help
            if cmd_lower in ('help', 'h', '?'):
                print_command_help(lang)
                continue
            
            # List/refresh / show all trips
            if cmd_lower in ('list', 'ls', 'trips', 'll'):  # 'll' for list, but 'l' is last
                display_trips(trips, cache_manager, lang=lang)
                continue
            
            # Render command
            if cmd_lower.startswith('render') or cmd_lower.startswith('r ') or cmd_lower == 'r':
                result = parse_render_command(cmd, trips, cache_manager, lang=lang)

                if not result['valid']:
                    # If the only error is missing selection/mode, offer to render ALL
                    if result.get('error_code') == 'no_selection_or_mode':
                        user_choice = input(lang.t(
                            "cli.no_selection_prompt",
                            yes=lang.t("general.yes"),
                            no=lang.t("general.no"),
                        )).strip()
                        if not user_choice:
                            print(lang.t("cli.cancelled_return"))
                            continue
                        if lang.is_yes(user_choice):
                            # Re-parse using explicit -a to include rendered
                            cmd = 'r -a'
                            result = parse_render_command(cmd, trips, cache_manager, lang=lang)
                            if not result['valid']:
                                print(lang.t("cli.error_prefix", error=result['error']))
                                continue
                        elif lang.is_no(user_choice):
                            print(lang.t("cli.cancelled_return"))
                            continue
                        else:
                            # Treat the user's input as a new command and process it
                            cmd = user_choice
                            continue
                    else:
                        print(lang.t("cli.error_prefix", error=result['error']))
                        continue

                trips_to_render = result['trips']
                print(lang.t("cli.render_will_render", count=len(trips_to_render)))
                for i, trip in enumerate(trips_to_render, 1):
                    try:
                        with open(trip / "trip.json", "r", encoding="utf-8") as f:
                            trip_data = json.load(f)
                        name = trip_data.get("name", trip.name)
                        print(f"  [{i}] {name}")
                    except:
                        print(f"  [{i}] {trip.name}")

                # Start rendering immediately
                print(lang.t("cli.render_abort_hint"))

                # Apply config overrides for this render (if any)
                merged_config = dict(config)
                merged_config.update(result.get('config_overrides', {}))
                render_lang = load_language_manager(merged_config.get("language", lang.language_code), script_dir)

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
                            print(lang.t("cli.stop_signal_received"))
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
                    if render_trip(trip, script_dir, merged_config, cache_manager, check_stop, lang=render_lang):
                        success_count += 1
                    drain_input_for_stop()

                # Summary
                print()
                print('=' * 70)
                if stopped:
                    print(lang.t("cli.stop_requested_summary", success=success_count, total=len(trips_to_render)))
                else:
                    print(lang.t("cli.completed_summary", success=success_count, total=len(trips_to_render)))
                print('=' * 70)
                print()

                # After rendering: do not automatically show trips (use 'trips' to view)
                print(lang.t("cli.type_trips_hint"))
                continue
        except KeyboardInterrupt:
            print("\n" + lang.t("cli.exiting"))
            break
        except EOFError:
            print("\n" + lang.t("cli.exiting"))
            break

    def select_trip(trips: list, cache_manager: CacheManager, show_rendered: bool = True) -> Optional[Path]:
        """Let user select a trip from the console."""
        if not trips:
            print(lang.t("cli.no_trips_found"))
            return None

        # Filter trips based on show_rendered setting
        display_trips = trips if show_rendered else [t for t in trips if not cache_manager.is_rendered(t)]

        if not display_trips:
            print(lang.t("cli.no_trips_filtered"))
            return None

        print("\n" + "=" * 70)
        print(f"  {lang.t('cli.trip_list_title')}")
        print("=" * 70)
        showing_text = lang.t("cli.showing_all") if show_rendered else lang.t("cli.showing_unrendered")
        print(f"\n{lang.t('cli.showing_label')} {showing_text}")
        print(lang.t("cli.total_trips_rendered", total=len(display_trips), rendered=cache_manager.get_rendered_count()) + "\n")
        print(lang.t("cli.available_trips") + "\n")

        for i, trip in enumerate(display_trips, 1):
            # Load trip name from trip.json
            try:
                with open(trip / "trip.json", "r", encoding="utf-8") as f:
                    trip_data = json.load(f)
                name = trip_data.get("name", trip.name)
                total_km = trip_data.get("total_km", 0)
                step_count = trip_data.get("step_count", 0)
                start_ts = trip_data.get("start_date", 0)
                date_fmt = lang.get_date_format("date.format.trip", "%d.%m.%Y")
                date_str = datetime.fromtimestamp(start_ts).strftime(date_fmt) if start_ts else "?"

                rendered_mark = "✓" if cache_manager.is_rendered(trip) else " "
                print(f"  [{i:2d}] [{rendered_mark}] {name} ({date_str})")
                print(lang.t(
                    "cli.trip_summary_line",
                    steps=step_count,
                    steps_label=lang.t("pdf.steps_label"),
                    km=total_km,
                    km_label=lang.t("units.km"),
                ))
                print()
            except Exception:
                rendered_mark = "✓" if cache_manager.is_rendered(trip) else " "
                print(f"  [{i:2d}] [{rendered_mark}] {trip.name}")
                print()

        print("\n" + "=" * 70)
        print(lang.t("cli.commands_title"))
        print(lang.t("cli.select_trip_commands"))
        print("=" * 70)
        print()

        while True:
            try:
                choice = input(lang.t("cli.select_option")).strip().lower()

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
                        print(lang.t("cli.invalid_selection"))
            except ValueError:
                print(lang.t("cli.invalid_input"))
            except KeyboardInterrupt:
                return None


def _parse_aspect_ratio(value, fallback: float = MAP_ASPECT_RATIO) -> float:
    """Parse aspect ratio from string (e.g., "16:9") or numeric value."""
    if value is None:
        return float(fallback)
    try:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if ":" in text:
            left, right = text.split(":", 1)
            left_v = float(left)
            right_v = float(right)
            if right_v == 0:
                return float(fallback)
            ratio = left_v / right_v
            return ratio if ratio > 0 else float(fallback)
        ratio = float(text)
        return ratio if ratio > 0 else float(fallback)
    except Exception:
        return float(fallback)


def render_trip(trip_path: Path, script_dir: Path, config: dict, cache_manager: CacheManager, check_stop=None, lang: LanguageManager = None) -> bool:
    """Render a single trip to PDF. Returns True if successful, False if error or stopped."""
    lang = lang or get_default_language_manager()
    try:
        # Check for stop signal
        if check_stop and check_stop():
            print(lang.t("cli.stopped_by_user"))
            return False
        
        print(lang.t("render.processing_trip", name=trip_path.name))
        
        # Parse trip
        parser = TripParser(trip_path)
        parser.load()
        
        print(lang.t("render.trip_name", name=parser.get_trip_name()))
        print(lang.t("render.steps_count", count=len(parser.steps)))
        print(lang.t("render.total_km", km=parser.get_total_km()))
        
        # Generate PDF
        trip_name_safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in parser.get_trip_name())
        pdfs_dir = script_dir / "TripPdfs"
        try:
            pdfs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pdfs_dir = trip_path.parent

        output_path = pdfs_dir / f"{trip_name_safe}.pdf"
        
        # Determine map URL + hybrid labels
        map_style = str(config.get("map_style", "hybrid")).lower().strip()
        # Accept common synonyms for convenience
        if map_style in ("street", "streets"):
            map_style = "road"
        if map_style in ("sat",):
            map_style = "satellite"

        label_overlay_url = None
        label_overlay_opacity = float(config.get("hybrid_labels_opacity", 0.7))
        print(lang.t("render.map_style", style=map_style))
        if map_style == "road":
            map_url = ESRI_ROAD_URL
        elif map_style == "satellite":
            map_url = ESRI_SATELLITE_URL
        else:
            # Hybrid: satellite base with label overlay
            map_url = ESRI_SATELLITE_URL
            label_overlay_url = ESRI_LABELS_URL

        map_gen = MapGenerator(
            marker_thumb_size=int(config.get("marker_thumb_size", 40)),
            url_template=map_url,
            label_overlay_url=label_overlay_url,
            label_overlay_opacity=label_overlay_opacity
        )
        map_gen.lang = lang
        
        # ========== NEW BOUNDING-BOX CONFIG (2026) ==========
        # Load settings from [maps] section if present
        maps_config = config.get("maps", {})
        overview_config = maps_config.get("overview", {})
        step_config = maps_config.get("step", {})
        
        # Vertical resolution controls pixel output and marker sizing. Geographic
        # coverage is computed from vertical resolution using the configured aspect ratio.
        default_vertical_px = int(maps_config.get("vertical_resolution_px", 450))
        overview_vertical_px = int(overview_config.get("vertical_resolution_px", default_vertical_px))
        step_vertical_px = int(step_config.get("vertical_resolution_px", default_vertical_px))

        default_ratio = _parse_aspect_ratio(maps_config.get("aspect_ratio"), MAP_ASPECT_RATIO)
        overview_ratio = _parse_aspect_ratio(overview_config.get("aspect_ratio", default_ratio), default_ratio)
        step_ratio = _parse_aspect_ratio(step_config.get("aspect_ratio", default_ratio), default_ratio)

        map_gen.height = default_vertical_px
        map_gen.width = int(round(default_vertical_px * default_ratio))
        map_gen.overview_height = overview_vertical_px
        map_gen.overview_width = int(round(overview_vertical_px * overview_ratio))
        map_gen.step_height = step_vertical_px
        map_gen.step_width = int(round(step_vertical_px * step_ratio))
        map_gen.overview_aspect_ratio = overview_ratio
        map_gen.step_aspect_ratio = step_ratio
        # Internal pixel scale relative to legacy 450px height (used for markers)
        map_gen._pixel_scale = float(default_vertical_px) / 450.0
        
        # Overview map settings
        map_gen.overview_padding_factor = float(overview_config.get("padding_factor", 0.10))
        map_gen.overview_min_width_km = float(overview_config.get("min_width_km", 10.0))
        # Step-specific config
        map_gen.step_padding_factor = float(step_config.get("padding_factor", map_gen.step_padding_factor))
        map_gen.step_min_width_km = float(step_config.get("min_width_km", map_gen.step_min_width_km))
        map_gen.step_max_distance_farthest_km = float(step_config.get("max_distance_farthest_steps_km", map_gen.step_max_distance_farthest_km))
        map_gen.step_cluster_distance_km = float(step_config.get("cluster_distance_km", map_gen.step_cluster_distance_km))
        map_gen.step_render_scale = float(step_config.get("render_scale", map_gen.step_render_scale))
        
        # Debug flag
        map_gen.debug_map = bool(config.get("debug_map", False))
        
        # No legacy config loading - the new [maps] section is authoritative for map sizing and padding.

        print(lang.t("render.renderer"))
        pdf_builder = HtmlPDFBuilder(output_path, parser, map_gen, config=config, language_manager=lang)
        pdf_builder.build()
        
        # Mark as rendered
        cache_manager.mark_rendered(trip_path)
        
        print(lang.t("render.done_pdf", path=output_path))
        return True
    except Exception as e:
        print(lang.t("render.error", error=e))
        return False


def get_date_filter_from_user(lang: LanguageManager = None) -> tuple:
    """Ask user for date filter (year or date range). Returns (year, start_date, end_date)."""
    lang = lang or get_default_language_manager()
    print("\n" + lang.t("cli.date_filter_title"))
    print(lang.t("cli.date_filter_year"))
    print(lang.t("cli.date_filter_range"))
    print(lang.t("cli.date_filter_none"))
    
    while True:
        try:
            choice = input(lang.t("cli.date_filter_select")).strip()
            
            if choice == "1":
                year = int(input(lang.t("cli.date_filter_enter_year")).strip())
                return (year, None, None)
            elif choice == "2":
                start_str = input(lang.t("cli.date_filter_enter_start")).strip()
                end_str = input(lang.t("cli.date_filter_enter_end")).strip()
                start_date = datetime.strptime(start_str, "%Y-%m-%d") if start_str else None
                end_date = datetime.strptime(end_str, "%Y-%m-%d") if end_str else None
                return (None, start_date, end_date)
            elif choice == "3":
                return (None, None, None)
            else:
                print(lang.t("cli.date_filter_invalid_choice"))
        except ValueError as e:
            print(lang.t("cli.date_filter_invalid_input", error=e))
        except KeyboardInterrupt:
            return (None, None, None)


def main():
    """Main entry point."""
    import sys
    
    script_dir = Path(__file__).parent
    global _DEFAULT_LANGUAGE_MANAGER
    _DEFAULT_LANGUAGE_MANAGER = load_language_manager("en", script_dir)

    # Load config (supports TOML with comments; falls back to commented JSON)
    config = {}
    config_toml = script_dir / "config.toml"
    config_json = script_dir / "config.json"
    try:
        if config_toml.exists():
            if _tomllib is None:
                with open(config_toml, "r", encoding="utf-8") as cf:
                    toml_content = cf.read()
                config = _parse_simple_toml(toml_content)
            else:
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
        print(get_default_language_manager().t("cli.config_load_warning", error=e))
        config = {}

    lang = load_language_manager(config.get("language", "en"), script_dir)
    _DEFAULT_LANGUAGE_MANAGER = lang
    config["_language_code"] = lang.language_code

    parser = argparse.ArgumentParser(
        description=lang.t("cli.argparse_description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=lang.t("cli.argparse_epilog"),
    )
    
    parser.add_argument('bsp_folder', nargs='?', help=lang.t("cli.argparse_bsp_help"))
    parser.add_argument('--clear-cache', action='store_true', help=lang.t("cli.argparse_clear_cache"))
    
    args = parser.parse_args()
    
    # Determine BSPData folder
    if args.bsp_folder:
        bsp_data_folder = Path(args.bsp_folder)
    else:
        bsp_data_folder = script_dir / "BSPData"
        
        if not bsp_data_folder.exists():
            bsp_data_folder = Path.cwd() / "BSPData"
    
    if not bsp_data_folder.exists():
        print(lang.t("cli.error_bsp_not_found", path=bsp_data_folder))
        print(lang.t("cli.usage"))
        sys.exit(1)
    
    # Move legacy cache locations into cache/
    _migrate_legacy_cache_paths()

    # Initialize cache manager
    cache_file = get_cache_dir() / "rendered_trips_cache.json"
    cache_manager = CacheManager(cache_file)
    
    # Handle clear cache from CLI
    if args.clear_cache:
        print(lang.t("cli.clearing_cache"))
        cache_manager.clear_cache()
        print(lang.t("cli.cache_cleared_check"))
        return
    
    print(lang.t("cli.scanning_trips", path=bsp_data_folder))
    
    # Find all trips
    trips = find_trips(bsp_data_folder)
    
    if not trips:
        print(lang.t("cli.no_trips_in_bsp"))
        sys.exit(1)
    
    print(lang.t("cli.found_trips", count=len(trips)))
    
    # Enter the unified prompt loop
    prompt_loop(trips, cache_manager, script_dir, config, lang)


if __name__ == "__main__":
    main()
