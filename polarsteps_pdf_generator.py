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
from typing import TYPE_CHECKING, Any, Optional, List, Tuple, Sequence, Union
from datetime import datetime, timedelta, date
import re
import webbrowser
try:
    import pycountry  # type: ignore[reportMissingImports]
except Exception:
    pycountry = None
try:
    import reverse_geocoder as rg  # type: ignore[reportMissingImports]
except Exception:
    rg = None
try:
    import pycountry_convert as pc  # type: ignore[reportMissingImports]
except Exception:
    pc = None
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


def _shared_detail_media_css() -> List[str]:
    return [
        '.photo-wrapper { position: relative; margin-top: 8px; }',
        '.photo-viewer { width: 100%; height: 320px; background: #000; position: relative; border: 1px solid #ccc; border-radius: 4px; overflow: hidden; }',
        '.photo-viewer img { width: 100%; height: 100%; object-fit: contain; background: #000; }',
        '.photo-nav { position: absolute; top: 50%; left: 0; right: 0; pointer-events: auto; display: flex; justify-content: space-between; transform: translateY(-50%); padding: 0 6px; }',
        '.photo-nav-btn { pointer-events: auto; border: none; background: rgba(26,95,122,0.8); color: white; width: 34px; height: 34px; border-radius: 50%; cursor: pointer; font-size: 1.1rem; font-weight: bold; }',
        '.photo-idx { position: absolute; bottom: 8px; right: 12px; color: #fff; background: rgba(0,0,0,0.5); padding: 2px 8px; border-radius: 12px; font-size: 0.85rem; }',
        '.video-box { margin-top: 6px; }',
        '.video-box video { width: 100%; border-radius: 4px; border: 1px solid #ccc; }',
    ]


def _shared_step_media_carousel_js() -> List[str]:
    return [
        'function createStepMediaCarousel(step) {',
        '  var mediaItems = [];',
        '  if (step.photos && step.photos.length) { step.photos.forEach(function(src){ mediaItems.push({ type: "image", src: src }); }); }',
        '  if (step.videos && step.videos.length) { step.videos.forEach(function(src){ mediaItems.push({ type: "video", src: src }); }); }',
        '  if (mediaItems.length === 0) { return null; }',
        '  var wrapper = document.createElement("div"); wrapper.className = "photo-wrapper";',
        '  var viewer = document.createElement("div"); viewer.className = "photo-viewer";',
        '  var idxLabel = document.createElement("div"); idxLabel.className = "photo-idx";',
        '  var nav = document.createElement("div"); nav.className = "photo-nav";',
        '  var prevBtn = document.createElement("button"); prevBtn.type = "button"; prevBtn.className = "photo-nav-btn"; prevBtn.textContent = "◀";',
        '  var nextBtn = document.createElement("button"); nextBtn.type = "button"; nextBtn.className = "photo-nav-btn"; nextBtn.textContent = "▶";',
        '  var currentIdx = 0;',
        '  function setMedia(index) {',
        '    if (mediaItems.length === 0) return;',
        '    if (index < 0) index = mediaItems.length - 1;',
        '    if (index >= mediaItems.length) index = 0;',
        '    currentIdx = index;',
        '    var item = mediaItems[index];',
        '    while (viewer.firstChild) viewer.removeChild(viewer.firstChild);',
        '    if (item.type === "video") {',
        '      var videoEl = document.createElement("video");',
        '      videoEl.controls = true;',
        '      videoEl.preload = "metadata";',
        '      videoEl.playsInline = true;',
        '      videoEl.autoplay = true;',
        '      videoEl.style.width = "100%";',
        '      videoEl.style.height = "100%";',
        '      videoEl.style.objectFit = "contain";',
        '      videoEl.src = item.src;',
        '      viewer.appendChild(videoEl);',
        '      var playPromise = videoEl.play();',
        '      if (playPromise && playPromise.catch) {',
        '        playPromise.catch(function(){',
        '          try { videoEl.muted = true; videoEl.play(); } catch (e) {}',
        '        });',
        '      }',
        '    } else {',
        '      var imgEl = document.createElement("img");',
        '      imgEl.src = item.src;',
        '      imgEl.style.width = "100%";',
        '      imgEl.style.height = "100%";',
        '      imgEl.style.objectFit = "contain";',
        '      viewer.appendChild(imgEl);',
        '    }',
        '    viewer.appendChild(idxLabel);',
        '    idxLabel.innerText = (index + 1) + " / " + mediaItems.length;',
        '  }',
        '  prevBtn.addEventListener("click", function(event){ event.stopPropagation(); event.preventDefault(); setMedia(currentIdx - 1); });',
        '  nextBtn.addEventListener("click", function(event){ event.stopPropagation(); event.preventDefault(); setMedia(currentIdx + 1); });',
        '  nav.appendChild(prevBtn); nav.appendChild(nextBtn);',
        '  wrapper.appendChild(viewer);',
        '  wrapper.appendChild(nav);',
        '  setMedia(0);',
        '  return wrapper;',
        '}',
    ]


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


def is_git_repo(path: Path) -> bool:
    """Return True if ``path`` is inside a git working tree."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path,
                              capture_output=True, text=True)
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except Exception:
        return False


def git_local_head(path: Path) -> Optional[str]:
    """Return the current HEAD commit hash, or None on error."""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def git_remote_head(path: Path) -> Optional[str]:
    """Fetch from origin and return the remote HEAD hash, or None."""
    try:
        # fetch quietly, don't alter working tree
        proc = subprocess.run(["git", "fetch", "origin", "HEAD"], cwd=path,
                              capture_output=True, text=True, timeout=30)
        proc = subprocess.run(["git", "rev-parse", "origin/HEAD"], cwd=path,
                              capture_output=True, text=True)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def check_git_updates(path: Path) -> bool:
    """Return True if remote HEAD differs from local HEAD."""
    if not is_git_repo(path):
        return False
    local = git_local_head(path)
    remote = git_remote_head(path)
    if local and remote and local != remote:
        return True
    return False


def backup_config(path: Path) -> None:
    """Copy config.toml to a timestamped .bak file before modifying the repo."""
    config_file = path / "config.toml"
    if config_file.exists():
        try:
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            dest = path / f"config.toml.bak_{ts}"
            if not dest.exists():
                shutil.copy2(str(config_file), str(dest))
        except Exception:
            pass


def perform_git_pull(path: Path) -> bool:
    """Attempt a fast-forward git pull on the repository. Returns True on success."""
    backup_config(path)
    try:
        proc = subprocess.run(["git", "pull", "--ff-only"], cwd=path,
                              capture_output=True, text=True)
        return proc.returncode == 0
    except Exception:
        return False


def perform_pip_upgrade() -> bool:
    """Try to upgrade the package via pip using the GitHub URL."""
    try:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
               "git+https://github.com/therealkarle/PolarstepsPDFCreator.git"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode == 0
    except Exception:
        return False


def check_for_update(script_dir: Path) -> Tuple[bool, str]:
    """Check whether an update is available. Returns (available,message)."""
    # prefer git-based check in repo
    if is_git_repo(script_dir):
        try:
            if check_git_updates(script_dir):
                return True, "git repository has newer commits"
            else:
                return False, "repository is up to date"
        except Exception as e:
            return False, f"git check error: {e}"
    # else fall back to pip upgrade possibility
    # we cannot easily know remote version, so just always offer upgrade
    return True, "pip upgrade may be available"


def do_update(script_dir: Path) -> bool:
    """Perform the update (git pull or pip upgrade). Returns True on success."""
    if is_git_repo(script_dir):
        return perform_git_pull(script_dir)
    else:
        return perform_pip_upgrade()


def maybe_update(script_dir: Path, config: dict, args) -> None:
    """Handle update/check flags and config. May exit the process."""
    lang = get_default_language_manager()
    auto_flag = config.get("auto_update", False) or getattr(args, "auto_update", False)

    if getattr(args, "check_update", False):
        print(lang.t("cli.update_checking"))
        avail, msg = check_for_update(script_dir)
        print(lang.t("cli.update_available" if avail else "cli.update_not_available", msg=msg))
        sys.exit(0)

    if getattr(args, "update", False):
        proceed = args.yes
        if not proceed:
            resp = input(lang.t("cli.update_prompt")).strip().lower()
            proceed = lang.is_yes(resp)
        if proceed:
            ok = do_update(script_dir)
            print(lang.t("cli.update_success" if ok else "cli.update_failed", error="" if ok else "see above"))
            if ok:
                sys.exit(0)
        else:
            print(lang.t("cli.cancelled"))
            sys.exit(0)

    if auto_flag:
        print(lang.t("cli.update_checking"))
        avail, msg = check_for_update(script_dir)
        if avail:
            print(lang.t("cli.update_available", msg=msg))
            proceed = args.yes
            if not proceed:
                resp = input(lang.t("cli.update_prompt")).strip().lower()
                proceed = lang.is_yes(resp)
            if proceed:
                ok = do_update(script_dir)
                if ok:
                    print(lang.t("cli.update_success"))
                    sys.exit(0)
                else:
                    print(lang.t("cli.update_failed", error="see above"))
        else:
            print(lang.t("cli.update_not_available"))

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

    def _resolve_media_reference(self, ref):
        """Resolve media reference to a local path or remote URL string."""
        if not ref:
            return None
        # preserve remote URLs directly
        if isinstance(ref, str):
            r = ref.strip()
            if r.startswith("http://") or r.startswith("https://"):
                return r
            try:
                p = Path(r)
            except Exception:
                p = None
            if p is not None:
                if p.is_file():
                    return p
                local = (self.trip_path / r).resolve()
                if local.is_file():
                    return local
        elif isinstance(ref, Path):
            if ref.is_file():
                return ref
            local = (self.trip_path / ref).resolve()
            if local.is_file():
                return local
        return None

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
                        if not p:
                            continue
                        media = self._resolve_media_reference(p)
                        if media is not None:
                            photos.append(media)
                    for v in s.get("videos", []):
                        if not v:
                            continue
                        media = self._resolve_media_reference(v)
                        if media is not None:
                            videos.append(media)
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

                # If step metadata itself references photos/videos, include them too.
                if isinstance(data, dict):
                    for p in data.get("photos", []) or []:
                        if not p:
                            continue
                        media = self._resolve_media_reference(p)
                        if media is not None and media not in photos:
                            photos.append(media)
                    for variant in ("photo", "media", "media_items", "photo_urls"):
                        for p in data.get(variant, []) or []:
                            if not p:
                                continue
                            media = self._resolve_media_reference(p)
                            if media is not None and media not in photos:
                                photos.append(media)
                    for v in data.get("videos", []) or []:
                        if not v:
                            continue
                        media = self._resolve_media_reference(v)
                        if media is not None and media not in videos:
                            videos.append(media)
                    for variant in ("video", "video_urls", "media_items"):
                        for v in data.get(variant, []) or []:
                            if not v:
                                continue
                            media = self._resolve_media_reference(v)
                            if media is not None and media not in videos:
                                videos.append(media)

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

            # Add any media referenced in step metadata (for robustness)
            if isinstance(step_data, dict):
                for p in step_data.get("photos", []) or []:
                    if not p:
                        continue
                    media = self._resolve_media_reference(p)
                    if media is not None and media not in photos:
                        photos.append(media)
                for variant in ("photo", "media", "media_items", "photo_urls"):
                    for p in step_data.get(variant, []) or []:
                        if not p:
                            continue
                        media = self._resolve_media_reference(p)
                        if media is not None and media not in photos:
                            photos.append(media)
                for v in step_data.get("videos", []) or []:
                    if not v:
                        continue
                    media = self._resolve_media_reference(v)
                    if media is not None and media not in videos:
                        videos.append(media)
                for variant in ("video", "video_urls", "media_items"):
                    for v in step_data.get(variant, []) or []:
                        if not v:
                            continue
                        media = self._resolve_media_reference(v)
                        if media is not None and media not in videos:
                            videos.append(media)

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
# OpenStreetMap standard tile template (Road)
OSM_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
# ESRI Reference labels (transparent overlay for hybrid-style maps)
ESRI_LABELS_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
# Map colors
ROUTE_COLOR = "#FFFFFF"  # white
# Approximate number of countries in the world for percentage calculations
WORLD_COUNTRY_COUNT = 195  # approximate (UN member states + common recognition)
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

try:
    RESAMPLING_LANCZOS = Image.Resampling.LANCZOS
except Exception:
    RESAMPLING_LANCZOS = getattr(Image, "LANCZOS", Image.BICUBIC)

if TYPE_CHECKING:
    from staticmap import StaticMap as _StaticMap, CircleMarker as _CircleMarker, Line as _Line, IconMarker as _IconMarker
    from typing import Protocol

    class TripLike(Protocol):
        steps: Sequence[dict]
        trip_path: Path
        def get_route_coordinates(self) -> List[tuple]: ...
else:
    _StaticMap = Any
    _CircleMarker = Any
    _Line = Any
    _IconMarker = Any
    TripLike = Any


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
        # Algorithm for overview fitting: 'bbox' (default) or 'radius'
        self.overview_algorithm = "bbox"
        
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

    def _get_trip_step_coords(self, trip_parser: "TripLike") -> List[Optional[tuple]]:
        key = self._trip_cache_key(trip_parser)
        cached = self._trip_step_coords_cache.get(key)
        if cached is not None and len(cached) == len(trip_parser.steps):
            return cached
        coords = [self._extract_lon_lat(step) for step in trip_parser.steps]
        self._trip_step_coords_cache[key] = coords
        return coords

    def _get_trip_route_coords(self, trip_parser: "TripLike") -> List[tuple]:
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
                    img = img.resize((size, size), RESAMPLING_LANCZOS)
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
        mg.overview_algorithm = self.overview_algorithm
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

    def _create_map(self, width: int = None, height: int = None) -> "_StaticMap":
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

    def generate_overview_map(self, trip_parser: "TripLike") -> bytes:
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
                    algorithm=str(getattr(self, 'overview_algorithm', 'bbox')),
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
        # Always draw markers on top so thumbnail overlay rendering is used,
        # including when IconMarker is unavailable or static map marker fallback is not ideal.
        draw_markers_on_top = True
        markers_to_draw: List[dict] = []
        marker_entries: List[dict] = []
        for i, step in enumerate(trip_parser.steps):
            coord = coords_cache[i] if i < len(coords_cache) else self._extract_lon_lat(step)
            if not coord:
                continue
            lon, lat = coord
            thumb = self._get_step_thumbnail(step, size=marker_px, ring_color=(255,255,255,230))
            marker_entries.append({
                "i": i,
                "lon": lon,
                "lat": lat,
                "thumb": thumb,
            })

        # Markers without photos first => always in background.
        ordered_marker_entries = sorted(marker_entries, key=lambda e: 0 if not e.get("thumb") else 1)
        for entry in ordered_marker_entries:
            i = entry["i"]
            lon = entry["lon"]
            lat = entry["lat"]
            thumb = entry.get("thumb")

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

        # Prefer a local photo if listed (erste Step-Bild)
        if photos:
            candidate = photos[0]
            if isinstance(candidate, Path):
                if candidate.exists():
                    photo_path = candidate
            elif isinstance(candidate, str):
                if candidate.startswith(("http://", "https://")):
                    photo_path = candidate
                else:
                    p = Path(candidate)
                    if p.exists():
                        photo_path = p
            else:
                try:
                    p = Path(str(candidate))
                    if p.exists():
                        photo_path = p
                except Exception:
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
                img = ImageOps.fit(img, (size, size), method=RESAMPLING_LANCZOS)

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
            draw.ellipse((0, 0, int(size) - 1, int(size) - 1), outline=(int(r), int(g), int(b), int(a)), width=int(thickness))
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

    def generate_step_map_for_step(self, trip_parser: "TripLike", step_index: int, width: int = 0, height: int = 0) -> bytes:
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
        # Always stratify to drawer layer (Pillow overlay) to ensure photo thumbnails are visible.
        draw_markers_on_top = True
        markers_to_draw: List[dict] = []
        
        marker_entries: List[dict] = []
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
            marker_entries.append({
                "i": i,
                "st": st,
                "lon": lon,
                "lat": lat,
                "is_current": is_current,
                "has_photo": has_photo,
                "thumb": thumb,
            })

        # Ensure bubbles without photos are always behind photo markers.
        # Keep current photo-marker last among photo markers.
        ordered_marker_entries = sorted(
            marker_entries,
            key=lambda e: (
                0 if not e.get("has_photo") else 1,
                1 if (e.get("has_photo") and e.get("is_current")) else 0,
            ),
        )

        for entry in ordered_marker_entries:
            i = entry["i"]
            st = entry["st"]
            lon = entry["lon"]
            lat = entry["lat"]
            is_current = bool(entry.get("is_current"))
            has_photo = bool(entry.get("has_photo"))
            thumb = entry.get("thumb")

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


class StatisticsGenerator:
    """Compute aggregated statistics from TripParser objects and generate overview map."""
    # Small mapping and heuristics for country normalization
    _COUNTRY_ALIASES = {
        'deutschland': 'Germany', 'germany': 'Germany', 'de': 'Germany', 'ger': 'Germany',
        'schweiz': 'Switzerland', 'switzerland': 'Switzerland', 'ch': 'Switzerland',
        'frankreich': 'France', 'france': 'France', 'fr': 'France',
        'italien': 'Italy', 'italy': 'Italy', 'it': 'Italy',
        'spanien': 'Spain', 'spain': 'Spain', 'es': 'Spain',
        'niederlande': 'Netherlands', 'netherlands': 'Netherlands', 'nl': 'Netherlands',
        'belgien': 'Belgium', 'belgium': 'Belgium', 'be': 'Belgium',
        'oesterreich': 'Austria', 'österreich': 'Austria', 'austria': 'Austria', 'at': 'Austria',
        'kroatien': 'Croatia', 'croatia': 'Croatia', 'hr': 'Croatia',
        'portugal': 'Portugal', 'pt': 'Portugal',
        'usa': 'United States', 'united states': 'United States', 'us': 'United States',
        'uk': 'United Kingdom', 'united kingdom': 'United Kingdom', 'gb': 'United Kingdom',
        'andorra': 'Andorra', 'ad': 'Andorra',
        'san marino': 'San Marino', 'sm': 'San Marino',
        'united arab emirates': 'United Arab Emirates', 'uae': 'United Arab Emirates', 'ae': 'United Arab Emirates',
        'united arab emirate': 'United Arab Emirates', 'arabische emirate': 'United Arab Emirates',
        'vereinigte arabische emirate': 'United Arab Emirates'
    }

    # Minimal alpha-2 -> name fallback used when pycountry is not available
    _ALPHA2_FALLBACK = {
        'IN': 'India', 'NP': 'Nepal', 'AE': 'United Arab Emirates', 'US': 'United States',
        'DE': 'Germany', 'CH': 'Switzerland', 'FR': 'France', 'IT': 'Italy', 'AT': 'Austria',
        'ES': 'Spain', 'HR': 'Croatia', 'SI': 'Slovenia', 'SM': 'San Marino', 'AD': 'Andorra',
        'VA': 'Vatican City', 'GB': 'United Kingdom', 'NL': 'Netherlands', 'BE': 'Belgium'
    }

    _COUNTRY_NAME_FALLBACK_DE = {
        'Germany': 'Deutschland',
        'Switzerland': 'Schweiz',
        'France': 'Frankreich',
        'Italy': 'Italien',
        'Spain': 'Spanien',
        'Netherlands': 'Niederlande',
        'Belgium': 'Belgien',
        'Austria': 'Österreich',
        'Croatia': 'Kroatien',
        'Slovenia': 'Slowenien',
        'Portugal': 'Portugal',
        'United States': 'Vereinigte Staaten',
        'United Kingdom': 'Vereinigtes Königreich',
        'Andorra': 'Andorra',
        'San Marino': 'San Marino',
        'United Arab Emirates': 'Vereinigte Arabische Emirate',
        'India': 'Indien',
        'Nepal': 'Nepal',
        'Vatican City': 'Vatikanstadt',
        'Unknown': 'Unbekannt',
    }

    _COUNTRY_TRANSLATIONS_URL = "https://restcountries.com/v3.1/all?fields=cca2,name,translations"
    _LANG_ALPHA3_FALLBACK = {
        'de': 'deu',
        'en': 'eng',
        'fr': 'fra',
        'es': 'spa',
        'it': 'ita',
        'pt': 'por',
        'nl': 'nld',
    }

    _CONTINENT_NAME_FALLBACK = {
        'de': {
            'Africa': 'Afrika',
            'Asia': 'Asien',
            'Europe': 'Europa',
            'North America': 'Nordamerika',
            'South America': 'Südamerika',
            'Oceania': 'Ozeanien',
            'Antarctica': 'Antarktis',
        },
        'en': {
            'Africa': 'Africa',
            'Asia': 'Asia',
            'Europe': 'Europe',
            'North America': 'North America',
            'South America': 'South America',
            'Oceania': 'Oceania',
            'Antarctica': 'Antarctica',
        },
    }

    def __init__(self, map_generator: MapGenerator = None, config: dict = None):
        self.map_generator = map_generator or MapGenerator()
        self.config = config or {}
        # cache for reverse-geocode lookups: key -> country name
        self._rg_cache = {}
        self._country_localize_cache = {}
        self._country_gettext_cache = {}
        self._country_online_cache = {}
        self._country_translation_cache = {}
        self._country_translation_cache_file = CACHE_ROOT / 'country_translation_cache.json'
        self._load_country_translation_cache()
        # load persistent reverse-geocode cache
        try:
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            cache_file = CACHE_ROOT / 'reverse_geocode_cache.json'
            if cache_file.exists():
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        try:
                            data = json.load(f)
                            # normalize any existing cache values to full country names when possible
                            for kk, vv in list(data.items()):
                                try:
                                    norm = self._normalize_country(vv) or vv
                                    self._rg_cache[kk] = norm
                                except Exception:
                                    self._rg_cache[kk] = vv
                        except Exception:
                            # if file corrupted just skip
                            pass
                except Exception:
                    # ignore corrupt cache
                    pass
        except Exception:
            pass

    def _save_rg_cache(self):
        try:
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            cache_file = CACHE_ROOT / 'reverse_geocode_cache.json'
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._rg_cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_country_translation_cache(self):
        try:
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            if self._country_translation_cache_file.exists():
                with open(self._country_translation_cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._country_translation_cache = data
        except Exception:
            self._country_translation_cache = {}

    def _save_country_translation_cache(self):
        try:
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            with open(self._country_translation_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._country_translation_cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _language_to_alpha3(self, language_code: str) -> str:
        lc = _normalize_language_code(language_code or 'en')
        if pycountry is not None:
            try:
                lang = pycountry.languages.get(alpha_2=lc)
                if lang:
                    a3 = getattr(lang, 'alpha_3', None)
                    if a3:
                        return str(a3).lower()
            except Exception:
                pass
        return self._LANG_ALPHA3_FALLBACK.get(lc, lc)

    def _load_online_country_translation_map(self, language_code: str) -> dict:
        lc = _normalize_language_code(language_code or 'en')
        if lc in self._country_online_cache:
            return self._country_online_cache[lc]

        cached = self._country_translation_cache.get(lc)
        if isinstance(cached, dict) and cached:
            self._country_online_cache[lc] = cached
            return cached

        try:
            resp = requests.get(self._COUNTRY_TRANSLATIONS_URL, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            self._country_online_cache[lc] = {}
            return {}

        alpha3 = self._language_to_alpha3(lc)
        mapping = {}
        for item in data if isinstance(data, list) else []:
            try:
                if not isinstance(item, dict):
                    continue
                cca2 = str(item.get('cca2') or '').upper().strip()
                raw_name_obj = item.get('name')
                name_obj = raw_name_obj if isinstance(raw_name_obj, dict) else {}
                en_name = str(name_obj.get('common') or '').strip()
                raw_translations = item.get('translations')
                translations = raw_translations if isinstance(raw_translations, dict) else {}
                trans_obj = translations.get(alpha3)
                if not isinstance(trans_obj, dict):
                    trans_obj = translations.get(lc)
                localized = ''
                if isinstance(trans_obj, dict):
                    localized = str(trans_obj.get('common') or trans_obj.get('official') or '').strip()
                if not localized and lc == 'en':
                    localized = en_name
                if localized:
                    if en_name:
                        mapping[en_name] = localized
                    if cca2:
                        mapping[cca2] = localized
            except Exception:
                continue

        self._country_online_cache[lc] = mapping
        if mapping:
            self._country_translation_cache[lc] = mapping
            self._save_country_translation_cache()
        return mapping

    def _cache_key_from_latlon(self, lat, lon, precision: int = 3) -> str:
        try:
            return f"{round(float(lat), precision)},{round(float(lon), precision)}"
        except Exception:
            return ''

    def _country_from_coords(self, location_data: dict, debug: bool = False) -> tuple:
        """Try to determine country from coordinate fields in `location_data`.
        Returns (country_name, source, raw_value) or ('', 'none', '')."""
        if rg is None:
            return '', 'none', ''
        # extract latitude/longitude from common keys
        lat = None
        lon = None
        # common possible keys
        try_keys = ['lat', 'latitude', 'lng', 'lon', 'longitude']
        for k in try_keys:
            if k in location_data and location_data.get(k) not in (None, ''):
                try:
                    val = location_data.get(k)
                    f = float(val)
                    if k in ('lat', 'latitude'):
                        lat = f
                    else:
                        lon = f
                except Exception:
                    pass
        # sometimes coords are provided as a tuple/list in 'coords' or 'latlng'
        if (lat is None or lon is None) and 'coords' in location_data:
            c = location_data.get('coords')
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                try:
                    lat = float(c[0])
                    lon = float(c[1])
                except Exception:
                    pass
        if (lat is None or lon is None) and 'latlng' in location_data:
            c = location_data.get('latlng')
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                try:
                    lat = float(c[0])
                    lon = float(c[1])
                except Exception:
                    pass

        if lat is None or lon is None:
            return '', 'none', ''

        # heuristics: sometimes coords are (lon,lat). Detect and swap if values look wrong
        try:
            if abs(lat) > 90 and abs(lon) <= 90:
                # probably swapped
                lat, lon = lon, lat
                if debug:
                    print(f"  Swapped coords to lat={lat}, lon={lon}")
        except Exception:
            pass

        # round coordinates for caching (3 decimals ~= 110m) to reduce duplicate lookups
        try:
            cache_key = f"{round(lat,3)},{round(lon,3)}"
        except Exception:
            cache_key = None
        if cache_key and cache_key in self._rg_cache:
            c = self._rg_cache[cache_key]
            # ensure cached value is normalized to a full country name when possible
            try:
                norm = self._normalize_country(c) or c
            except Exception:
                norm = c
            if norm != c:
                try:
                    self._rg_cache[cache_key] = norm
                    self._save_rg_cache()
                except Exception:
                    pass
            if debug:
                print(f"  RG cache hit for {cache_key} -> {norm}")
            return norm, 'coords_cache', cache_key

        try:
            # reverse_geocoder expects (lat, lon) as floats; pass as a single tuple
            # This single-point path remains as a fallback if batching is not used
            res = rg.search((lat, lon))
            if res and isinstance(res, (list, tuple)):
                r = res[0]
                cc = r.get('cc')
                raw = f"{lat},{lon}"
                country_name = ''
                if cc and pycountry is not None:
                    try:
                        ctry = pycountry.countries.get(alpha_2=cc.upper())
                        if ctry:
                            country_name = getattr(ctry, 'common_name', None) or getattr(ctry, 'official_name', None) or getattr(ctry, 'name', None) or ''
                    except Exception:
                        country_name = ''
                # fallback to cc code if lookup failed
                result_value = country_name or (cc or '')
                # normalize the result to full country name if possible
                normalized = self._normalize_country(result_value) or result_value
                # store normalized value in cache
                if cache_key:
                    try:
                        self._rg_cache[cache_key] = normalized
                    except Exception:
                        pass
                if normalized:
                    if debug:
                        print(f"  Reverse-geocoded coords {raw} -> {normalized} ({cc})")
                    return normalized, 'coords', raw
        except Exception:
            return '', 'none', ''
        return '', 'none', ''

    def _extract_country_from_location(self, location_data: dict, debug: bool = False) -> tuple:
        """Extract country from location data with fallback strategy.
        Returns tuple: (country_name, source_field, raw_value)"""
        if not isinstance(location_data, dict):
            return '', 'none', ''
        # Prefer coordinate-based lookup when available (reduces missed days)
        try:
            ctry, src, raw = self._country_from_coords(location_data, debug=debug)
            if ctry:
                return ctry, src, raw
        except Exception:
            # fail silently and continue with existing heuristics
            pass
        
        # Known cities/places that should NOT be treated as countries
        known_cities = {
            'kathmandu', 'jaipur', 'dubai', 'mumbai', 'delhi', 'beijing', 'tokyo',
            'bangkok', 'singapore', 'kuala lumpur', 'hong kong', 'macau',
            'everest base camp', 'island peak basecamp', 'annapurna base camp',
            'mount everest', 'lukla', 'namche', 'tengboche', 'pheriche', 'lobuche',
            'gokyo', 'dingboche', 'kala patthar', 'cho la pass', 'renjo pass',
            'gokyo ri', 'ama dablam base camp', 'mera peak', 'island peak',
            'manaslu', 'annapurna circuit', 'annapurna sanctuary',
            'paris', 'london', 'berlin', 'rome', 'madrid', 'barcelona',
            'vienna', 'zurich', 'geneva', 'milan', 'venice', 'florence',
            'nice', 'cannes', 'lyon', 'marseille', 'toulouse', 'bordeaux',
            'munich', 'hamburg', 'cologne', 'frankfurt', 'stuttgart', 'düsseldorf',
            'salzburg', 'innsbruck', 'graz', 'linz',
            'basel', 'bern', 'lausanne', 'luzern', 'st. gallen',
            'zagreb', 'split', 'dubrovnik', 'ljubljana', 'bled',
            'agra', 'goa', 'kolkata', 'pune', 'hyderabad', 'bangalore',
            'chennai', 'cochin', 'varanasi', 'rishikesh', 'pushkar',
            'udaipur', 'jodhpur', 'bikaner', 'jaisalmer', 'mandawa',
            'mcleod ganj', 'dharamshala', 'manali', 'shimla', 'leh', 'ladakh',
            'pokhara', 'chitwan', 'lumbini', 'bhaktapur', 'patan',
            # Common mountain/trekking destinations
            'base camp', 'peak', 'pass', 'glacier', 'summit', 'ridge'
        }
        
        # Priority order for location fields
        fields_to_check = [
            ('country', 'location.country'),
            ('country_name', 'location.country_name'), 
            ('countryCode', 'location.countryCode'),
            ('country_code', 'location.country_code'),
            ('detail', 'location.detail'),
            ('full_detail', 'location.full_detail'),
            ('name', 'location.name'),
            ('display_name', 'location.display_name')
        ]
        
        for field, source in fields_to_check:
            raw_value = location_data.get(field, '').strip()
            if not raw_value:
                continue
                
            normalized = self._normalize_country(raw_value)
            if not normalized:
                continue
                
            # Check if it's a known city (only for name/display_name fields)
            if field in ('name', 'display_name'):
                if normalized.lower() in known_cities:
                    if debug:
                        print(f"  Skipping known city: {normalized} from {source}")
                    continue
                
            if debug:
                print(f"  Found country: {normalized} from {source} (raw: {raw_value})")
            return normalized, source, raw_value
            
        return '', 'none', ''

    def _normalize_country(self, raw: str) -> str:
        if not raw:
            return ''
        s = str(raw).strip()
        # remove parentheses content
        if '(' in s and ')' in s:
            try:
                s = re.sub(r"\([^)]*\)", "", s)
            except Exception:
                pass
        # common replacing (normalize accents/diacritics is out-of-scope but we can lowercase)
        s = s.strip()
        # split on commas and take last token
        parts = [p.strip() for p in re.split(r'[,\-\/]', s) if p.strip()]
        token = parts[-1] if parts else s
        # QUICK WIN: if token is a 2-letter country code, handle it BEFORE removing common prepositions
        if len(token.strip()) == 2:
            code = token.strip().upper()
            if pycountry is not None:
                try:
                    c = pycountry.countries.get(alpha_2=code)
                    if c:
                        name = getattr(c, 'common_name', None) or getattr(c, 'official_name', None) or getattr(c, 'name', None)
                        if name:
                            return name
                except Exception:
                    pass
            try:
                if code in self._ALPHA2_FALLBACK:
                    return self._ALPHA2_FALLBACK[code]
            except Exception:
                pass
        # remove common prepositions and noise
        token = re.sub(r'\b(bei|in|am|der|die|das|von|den|und|auf|la|le)\b', '', token, flags=re.IGNORECASE).strip()
        # remove any trailing digits or extra punctuation
        token = re.sub(r'[^\w\s-]', '', token).strip()
        token_low = token.lower()
        # normalization aliases
        extra_aliases = {
            'andorra la vella': 'andorra',
            'andorra la': 'andorra',
            'andorra la v': 'andorra'
        }
        if token_low in extra_aliases:
            token_low = extra_aliases[token_low]
        # if 2-letter code, try pycountry lookup directly; fallback to built-in map if pycountry missing
        if len(token_low) == 2:
            code = token_low.upper()
            if pycountry is not None:
                try:
                    c = pycountry.countries.get(alpha_2=code)
                    if c:
                        name = getattr(c, 'common_name', None) or getattr(c, 'official_name', None) or getattr(c, 'name', None)
                        if name:
                            return name
                except Exception:
                    pass
            # fallback mapping when pycountry not available or lookup failed
            try:
                if code in self._ALPHA2_FALLBACK:
                    return self._ALPHA2_FALLBACK[code]
            except Exception:
                pass
        # try direct alias match
        if token_low in self._COUNTRY_ALIASES:
            return self._COUNTRY_ALIASES[token_low]
        # discard extremely short tokens (likely abbreviations or noise) unless explicitly known
        if len(token_low) <= 2 and token_low not in self._COUNTRY_ALIASES:
            return ''
        # try to find any alias substring -- require word boundaries to avoid false positives
        for k, v in self._COUNTRY_ALIASES.items():
            try:
                if re.search(r"\b" + re.escape(k) + r"\b", token_low):
                    return v
            except Exception:
                continue
        # try pycountry lookup for robust country matching
        if pycountry is not None:
            try:
                c = pycountry.countries.lookup(token)
                name = getattr(c, 'common_name', None) or getattr(c, 'official_name', None) or getattr(c, 'name', None)
                if name:
                    return name
            except Exception:
                pass
        # fallback: return capitalized short token if plausible (1-3 words, length < 30)
        if 0 < len(token) <= 30 and len(token.split()) <= 3:
            return token.title()
        return ''

    def _batch_reverse_geocode(self, coord_map: dict, debug: bool = False):
        """coord_map: cache_key -> (lat, lon). Performs batch rg.search and stores normalized names in cache."""
        if rg is None or not coord_map:
            return
        # build a list of unique coords
        coords = []
        keys = []
        for k, (lat, lon) in coord_map.items():
            try:
                coords.append((float(lat), float(lon)))
                keys.append(k)
            except Exception:
                continue
        if not coords:
            return
        try:
            # pre-warm/first call will load the rg dataset
            res_list = rg.search(coords)
        except Exception:
            # fallback: try single lookups
            for k, (lat, lon) in coord_map.items():
                try:
                    r = rg.search((float(lat), float(lon)))
                    if r and isinstance(r, (list, tuple)):
                        rr = r[0]
                        cc = rr.get('cc')
                        nm = ''
                        if cc and pycountry is not None:
                            try:
                                ctry = pycountry.countries.get(alpha_2=cc.upper())
                                if ctry:
                                    nm = getattr(ctry, 'common_name', None) or getattr(ctry, 'official_name', None) or getattr(ctry, 'name', None) or ''
                            except Exception:
                                nm = ''
                        normalized = self._normalize_country(nm or (cc or '')) or (nm or (cc or ''))
                        self._rg_cache[k] = normalized
                except Exception:
                    continue
            self._save_rg_cache()
            return
        # map batch results back
        for i, rr in enumerate(res_list):
            try:
                r = rr
                cc = r.get('cc')
                k = keys[i]
                nm = ''
                if cc and pycountry is not None:
                    try:
                        ctry = pycountry.countries.get(alpha_2=cc.upper())
                        if ctry:
                            nm = getattr(ctry, 'common_name', None) or getattr(ctry, 'official_name', None) or getattr(ctry, 'name', None) or ''
                    except Exception:
                        nm = ''
                normalized = self._normalize_country(nm or (cc or '')) or (nm or (cc or ''))
                self._rg_cache[k] = normalized
            except Exception:
                continue
        # persist cache
        self._save_rg_cache()

    def _country_to_continent(self, country_name: str) -> str:
        """Return continent name for a given country name. Uses pycountry_convert when available."""
        if not country_name:
            return ''
        # try pycountry to get alpha_2
        try:
            c = None
            if pycountry is not None:
                try:
                    c = pycountry.countries.lookup(country_name)
                except Exception:
                    c = None
            if c and pc is not None:
                try:
                    alpha2 = getattr(c, 'alpha_2', None)
                    if alpha2:
                        cc = pc.country_alpha2_to_continent_code(alpha2.upper())
                        if cc:
                            return {
                                'AF': 'Africa', 'AS': 'Asia', 'EU': 'Europe', 'NA': 'North America', 'OC': 'Oceania', 'SA': 'South America', 'AN': 'Antarctica'
                            }.get(cc, cc)
                except Exception:
                    pass
        except Exception:
            pass
        # fallback: basic mapping for common countries (expandable)
        fallback = {
            'United States': 'North America', 'Germany': 'Europe', 'France': 'Europe', 'Italy': 'Europe',
            'Switzerland': 'Europe', 'Austria': 'Europe', 'India': 'Asia', 'Nepal': 'Asia', 'Croatia': 'Europe',
            'Slovenia': 'Europe', 'United Arab Emirates': 'Asia'
        }
        return fallback.get(country_name, '')

    def _get_country_gettext_translator(self, language_code: str):
        lc = _normalize_language_code(language_code or 'en')
        if lc in self._country_gettext_cache:
            return self._country_gettext_cache[lc]
        tr = None
        if pycountry is not None:
            try:
                import gettext
                locales_dir = getattr(pycountry, 'LOCALES_DIR', None)
                if locales_dir:
                    tr = gettext.translation('iso3166-1', locales_dir, languages=[lc], fallback=True)
            except Exception:
                tr = None
        self._country_gettext_cache[lc] = tr
        return tr

    def localize_country_name(self, country_name: str, language_code: Optional[str] = None) -> str:
        """Return a localized display name for a normalized English country name."""
        if not country_name:
            return country_name
        lc = _normalize_language_code(language_code or self.config.get('_language_code', 'en'))
        key = (lc, str(country_name))
        if key in self._country_localize_cache:
            return self._country_localize_cache[key]

        normalized = self._normalize_country(country_name) or str(country_name)
        if lc == 'en':
            self._country_localize_cache[key] = normalized
            return normalized

        localized = normalized

        # 1) online country translation map (all countries from restcountries)
        online_map = self._load_online_country_translation_map(lc)
        if online_map:
            try:
                names_to_try = [normalized]
                alpha2 = ''
                if pycountry is not None:
                    try:
                        c = pycountry.countries.lookup(normalized)
                        names_to_try = [
                            getattr(c, 'name', None),
                            getattr(c, 'official_name', None),
                            getattr(c, 'common_name', None),
                            normalized,
                        ]
                        alpha2 = str(getattr(c, 'alpha_2', '') or '').upper()
                    except Exception:
                        pass
                for nm in names_to_try:
                    if not nm:
                        continue
                    val = online_map.get(str(nm))
                    if val:
                        localized = str(val)
                        break
                if localized == normalized and alpha2:
                    code_val = online_map.get(alpha2)
                    if code_val:
                        localized = str(code_val)
            except Exception:
                localized = normalized

        if localized != normalized:
            self._country_localize_cache[key] = localized
            return localized

        # 2) local gettext fallback
        tr = self._get_country_gettext_translator(lc)
        if tr is not None:
            try:
                names_to_try = [normalized]
                if pycountry is not None:
                    try:
                        c = pycountry.countries.lookup(normalized)
                        names_to_try = [
                            getattr(c, 'name', None),
                            getattr(c, 'official_name', None),
                            getattr(c, 'common_name', None),
                            normalized,
                        ]
                    except Exception:
                        pass
                for nm in names_to_try:
                    if not nm:
                        continue
                    translated = tr.gettext(str(nm))
                    if translated and translated != nm:
                        localized = translated
                        break
            except Exception:
                localized = normalized

        if localized == normalized and lc == 'de':
            localized = self._COUNTRY_NAME_FALLBACK_DE.get(normalized, normalized)

        self._country_localize_cache[key] = localized
        return localized

    def localize_country_counts(self, country_counts: dict, language_code: Optional[str] = None) -> dict:
        """Localize country dictionary keys while preserving summed counts."""
        result = {}
        for country, count in (country_counts or {}).items():
            display_name = self.localize_country_name(country, language_code=language_code)
            result[display_name] = result.get(display_name, 0) + int(count or 0)
        return result

    def localize_continent_name(self, continent_name: str, language_code: Optional[str] = None) -> str:
        """Return a localized display name for a continent name."""
        if not continent_name:
            return continent_name
        lc = _normalize_language_code(language_code or self.config.get('_language_code', 'en'))
        key = (f"continent:{lc}", str(continent_name))
        if key in self._country_localize_cache:
            return self._country_localize_cache[key]

        fallback_for_lang = self._CONTINENT_NAME_FALLBACK.get(lc, {})
        localized = fallback_for_lang.get(continent_name, continent_name)
        self._country_localize_cache[key] = localized
        return localized

    def localize_continent_counts(self, continent_counts: dict, language_code: Optional[str] = None) -> dict:
        """Localize continent dictionary keys while preserving summed counts."""
        result = {}
        for continent, count in (continent_counts or {}).items():
            display_name = self.localize_continent_name(continent, language_code=language_code)
            result[display_name] = result.get(display_name, 0) + int(count or 0)
        return result

    def _parse_date(self, v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        try:
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(int(v))
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v)
                except Exception:
                    # try common formats
                    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
                        try:
                            return datetime.strptime(v, fmt)
                        except Exception:
                            pass
        except Exception:
            pass
        return None

    def compute_trip_stats(self, trip_parser: TripParser) -> dict:
        """Compute per-trip stats (steps, photos, videos, km, dates, countries).
        Returns serializable dict."""
        tp = trip_parser
        tp.load()
        name = tp.get_trip_name()
        start_dt, end_dt = tp.get_trip_dates()
        total_km = tp.get_total_km()
        steps = tp.steps or []
        photos = sum(len(s.get('photos', [])) for s in steps)
        videos = sum(len(s.get('videos', [])) for s in steps)
        # collect unique travel dates from step timestamps and trip start/end
        travel_dates = set()
        countries = []
        for s in steps:
            data = s.get('data', {}) or {}
            # collect step date/time hints
            for key in ('start_time', 'startDate', 'start_date', 'time', 'date', 'timestamp'):
                if key in data and data[key]:
                    dt = self._parse_date(data[key])
                    if dt:
                        travel_dates.add(dt.date())
                        break
            # fallback: use trip start date if available
            if not travel_dates and start_dt:
                travel_dates.add(start_dt.date())
            # country detection with improved fallback logic
            country = ''
            loc = data.get('location') if isinstance(data.get('location'), dict) else {}
            if loc:
                country, _, _ = self._extract_country_from_location(loc)
            if country:
                countries.append(country)
        # if trip-level start/end exist, include their days
        if start_dt and end_dt:
            cur = start_dt.date()
            while cur <= (end_dt.date() or cur):
                travel_dates.add(cur)
                cur = cur + timedelta(days=1)
                # safe guard
                if len(travel_dates) > 10000:
                    break
        stats = {
            'name': name,
            'path': str(tp.trip_path),
            'start_date': start_dt.isoformat() if start_dt else None,
            'end_date': end_dt.isoformat() if end_dt else None,
            'steps': len(steps),
            'photos': photos,
            'videos': videos,
            'total_km': total_km,
            'travel_days': len(travel_dates),
            'countries': sorted(set([c for c in countries if c])),
        }
        return stats

    def compute_aggregate_stats(self, trip_paths: list, year: int = None, start_date: datetime = None, end_date: datetime = None, progress_callback=None, verbose: bool = False, debug_countries: bool = False) -> dict:
        """Aggregate stats across a list of trip paths (Path objects).

        Future trips (where the recorded start date is after today's date) are
        ignored entirely; only past and current trips contribute to statistics.
        Additionally, when computing travel days the function will not count any
        dates later than today even if a trip spans into the future.  This means
        aggregated day counts and reported period bounds are always capped at
        the current date.

        Filtering and period handling:
          * If `year` is given, trips whose start date falls in that year are
            considered.  When no explicit start/end range is supplied the returned
            period will default to the full calendar year.
          * If `start_date` and `end_date` are provided the statistics are limited
            to that interval, and the resulting `period_start`/`period_end` in the
            returned aggregate reflect the entire range (not just the travel
            days within it).
          * Otherwise the period is derived from the min/max of the travel dates
            actually encountered (all-time mode).

        Travel day semantics:
          * "Travel days" are computed from each trip's declared start/end dates
            (inclusive) clipped to the requested interval.  This matches the
            intuitive notion of days on a trip and avoids missing days when step
            timestamps are sparse or absent.
          * Step timestamps are still used for finer-grained country detection
            and assignment, but they no longer control the day count.  If a step
            timestamp lies outside the trip span it is ignored for day counting.

        Optional progress_callback(processed:int, total:int, trip:Path) called
        as each trip is processed.

        Returns a dictionary containing summary values. The returned 'trip_count'
        value corresponds to the number of trips actually processed (after
        filtering and excluding future trips) rather than the length of the
        supplied list. In addition to the
          * 'period_start'/'period_end' – ISO dates for the evaluated interval
          * 'period_total_days' – number of days in the period (inclusive)
          * 'period_non_travel_days' – period length minus travel days (or None)
        If verbose is True, includes per-trip breakdown in returned dict under
        key 'per_trip'.
        If debug_countries is True, shows detailed country detection info."""
        all_stats = []
        total_km = 0.0
        total_photos = 0
        total_videos = 0
        total_steps = 0
        travel_dates = set()
        country_days = {}  # country -> set(dates)
        unmatched_days = set()  # track days not assigned to any country
        included_trips = 0  # count of trips actually considered (after filtering)

        # If year is requested but no explicit start/end range supplied, automatically
        # treat the filter as the full calendar year.  This drives both inclusion and
        # subsequent per-trip date trimming.
        if year and not start_date and not end_date:
            try:
                start_date = datetime(int(year), 1, 1)
                end_date = datetime(int(year), 12, 31)
            except Exception:
                # ignore invalid year, leave dates unset
                pass

        total = len(trip_paths)

        # Pre-scan all steps for coordinates and populate reverse-geocode cache in batch.
        coord_map = {}  # cache_key -> (lat, lon)
        for p_scan in trip_paths:
            try:
                tp_scan = TripParser(p_scan)
                tp_scan.load()
            except Exception:
                continue
            for s_scan in tp_scan.steps:
                data_scan = s_scan.get('data', {}) or {}
                loc_scan = data_scan.get('location') if isinstance(data_scan.get('location'), dict) else {}
                if not loc_scan:
                    continue
                # extract candidate coords
                lat = None
                lon = None
                for k in ('lat', 'latitude'):
                    if k in loc_scan and loc_scan.get(k) not in (None, ''):
                        try:
                            lat = float(loc_scan.get(k))
                        except Exception:
                            lat = None
                for k in ('lon', 'lng', 'longitude'):
                    if k in loc_scan and loc_scan.get(k) not in (None, ''):
                        try:
                            lon = float(loc_scan.get(k))
                        except Exception:
                            lon = None
                if (lat is None or lon is None) and 'coords' in loc_scan:
                    c = loc_scan.get('coords')
                    if isinstance(c, (list, tuple)) and len(c) >= 2:
                        try:
                            lat = float(c[0])
                            lon = float(c[1])
                        except Exception:
                            pass
                if (lat is None or lon is None) and 'latlng' in loc_scan:
                    c = loc_scan.get('latlng')
                    if isinstance(c, (list, tuple)) and len(c) >= 2:
                        try:
                            lat = float(c[0])
                            lon = float(c[1])
                        except Exception:
                            pass
                if lat is not None and lon is not None:
                    k = self._cache_key_from_latlon(lat, lon)
                    if k and k not in self._rg_cache:
                        coord_map[k] = (lat, lon)
        if coord_map:
            if debug_countries:
                print(f"Batch reverse-geocoding {len(coord_map)} unique coords...")
            self._batch_reverse_geocode(coord_map, debug=debug_countries)

        for idx, p in enumerate(trip_paths, start=1):
            try:
                tp = TripParser(p)
                tp.load()
            except Exception:
                # still report progress
                if progress_callback:
                    try:
                        progress_callback(idx, total, p)
                    except Exception:
                        pass
                continue
            # report progress before filtering per-trip
            if progress_callback:
                try:
                    progress_callback(idx, total, p)
                except Exception:
                    pass

            # determine if trip should be included by supplied filter range
            s_dt, e_dt = tp.get_trip_dates()
            # skip trips that haven't started yet (future trips should not count)
            try:
                today = datetime.now().date()
                if s_dt and s_dt.date() > today:
                    # entirely in the future, ignore
                    continue
            except Exception:
                pass

            # if there is an active date range, skip trips that do not overlap it
            if start_date or end_date:
                overlaps = True
                if s_dt or e_dt:
                    s = s_dt.date() if s_dt else None
                    e = e_dt.date() if e_dt else None
                    if start_date and e and e < start_date.date():
                        overlaps = False
                    if end_date and s and s > end_date.date():
                        overlaps = False
                else:
                    overlaps = False

                # fallback: include when at least one step date is in range
                if not overlaps:
                    step_overlap = False
                    for st in tp.steps:
                        data = st.get('data', {}) or {}
                        for key in ('start_time', 'startDate', 'start_date', 'time', 'date', 'timestamp'):
                            if key in data and data[key]:
                                dt = self._parse_date(data[key])
                                if not dt:
                                    continue
                                d = dt.date()
                                if start_date and d < start_date.date():
                                    continue
                                if end_date and d > end_date.date():
                                    continue
                                step_overlap = True
                                break
                        if step_overlap:
                            break
                    if not step_overlap:
                        continue
            # if no range provided but year parameter existed (and was invalidated
            # above) we still default to filtering by start year, keeping previous
            # behaviour for callers that passed year without dates.
            elif year and s_dt:
                if s_dt.year != int(year):
                    continue
            # collect per-trip stats via compute_trip_stats but filter step dates by range
            # trip passed all filters – include it
            included_trips += 1
            ts = self.compute_trip_stats(tp)
            total_km += float(ts.get('total_km') or 0)
            total_photos += int(ts.get('photos') or 0)
            total_videos += int(ts.get('videos') or 0)
            total_steps += int(ts.get('steps') or 0)
            # collect travel dates via step dates and trip dates (span)
            # collect span dates (every day between trip start and end)
            span_dates = set()
            if s_dt and e_dt:
                cur = s_dt.date()
                endd = e_dt.date()
                while cur <= endd:
                    span_dates.add(cur)
                    cur = cur + timedelta(days=1)
            # we count travel days as the full span of the trip; step dates
            # are only used for country detection, not for day counting.
            tmp_dates = set(span_dates)
            # filter tmp_dates by provided date range
            if start_date:
                tmp_dates = set(d for d in tmp_dates if d >= start_date.date())
            if end_date:
                tmp_dates = set(d for d in tmp_dates if d <= end_date.date())
            for d in tmp_dates:
                travel_dates.add(d)
            per_trip_country_days = {}
            # two-pass approach: first collect explicit detections, then assign fallbacks so EVERY step has a country
            step_assigned = []            # list of normalized country (or '' if none yet) per step
            step_dates_list = []         # list of date sets per step (aligned with steps)

            # map countries to dates with improved detection (first pass)
            for s in tp.steps:
                data = s.get('data', {}) or {}
                # use improved country detection
                country = ''
                loc = data.get('location') if isinstance(data.get('location'), dict) else {}
                if loc:
                    country, source, raw = self._extract_country_from_location(loc, debug=debug_countries)
                    if debug_countries and country:
                        print(f"    Step country: {country} from {source} (raw: {raw})")

                # extract dates for this step
                step_dates = set()
                for key in ('start_time', 'startDate', 'start_date', 'time', 'date', 'timestamp'):
                    if key in data and data[key]:
                        dt = self._parse_date(data[key])
                        if dt:
                            step_dates.add(dt.date())
                if start_date:
                    step_dates = set(d for d in step_dates if d >= start_date.date())
                if end_date:
                    step_dates = set(d for d in step_dates if d <= end_date.date())

                # record explicit country (normalized) or placeholder for fallback
                if country:
                    norm_ctry = self._normalize_country(country) or country
                    country_days.setdefault(norm_ctry, set()).update(step_dates)
                    per_trip_country_days.setdefault(norm_ctry, set()).update(step_dates)
                    step_assigned.append(norm_ctry)
                else:
                    # leave for second-pass fallback assignment
                    step_assigned.append('')
                step_dates_list.append(step_dates)

            # second pass: ensure every step gets a country (trip meta -> nearest neighbor -> trip-majority -> global-majority -> final fallback)
            # trip-level metadata country
            trip_meta_country = ''
            try:
                trip_loc = tp.trip_data.get('location') if isinstance(tp.trip_data, dict) else None
                if isinstance(trip_loc, dict):
                    trip_meta_country, _, _ = self._extract_country_from_location(trip_loc, debug=debug_countries)
                if not trip_meta_country and isinstance(tp.trip_data, dict):
                    for k in ('country', 'country_name', 'country_code'):
                        v = tp.trip_data.get(k)
                        if v:
                            nm = self._normalize_country(v)
                            if nm:
                                trip_meta_country = nm
                                break
            except Exception:
                trip_meta_country = ''

            # trip-majority (most frequent explicit country in this trip)
            trip_majority_country = ''
            explicit_countries = [c for c in step_assigned if c]
            if per_trip_country_days:
                # prefer the country with most assigned days in per_trip_country_days
                try:
                    trip_majority_country = max(per_trip_country_days.items(), key=lambda x: len(x[1]))[0]
                except Exception:
                    trip_majority_country = ''
            elif explicit_countries:
                counts = {}
                for c in explicit_countries:
                    counts[c] = counts.get(c, 0) + 1
                try:
                    trip_majority_country = max(counts.items(), key=lambda x: x[1])[0]
                except Exception:
                    trip_majority_country = ''


            # Assign fallbacks for steps that have no explicit country
            for i, assigned in enumerate(step_assigned):
                if assigned:
                    continue
                assigned_country = ''
                reason = ''
                # 1) trip meta
                if trip_meta_country:
                    assigned_country = trip_meta_country
                    reason = 'trip_meta'
                else:
                    # 2) nearest neighbor (left then right)
                    left = None
                    for j in range(i - 1, -1, -1):
                        if step_assigned[j]:
                            left = step_assigned[j]
                            break
                    right = None
                    for j in range(i + 1, len(step_assigned)):
                        if step_assigned[j]:
                            right = step_assigned[j]
                            break
                    if left:
                        assigned_country = left
                        reason = 'neighbor_left'
                    elif right:
                        assigned_country = right
                        reason = 'neighbor_right'
                    elif trip_majority_country:
                        assigned_country = trip_majority_country
                        reason = 'trip_majority'
                    else:
                        assigned_country = 'Unknown'
                        reason = 'fallback_unknown'

                norm_assigned = self._normalize_country(assigned_country) or assigned_country
                step_assigned[i] = norm_assigned
                dates_set = step_dates_list[i] or set()
                country_days.setdefault(norm_assigned, set()).update(dates_set)
                per_trip_country_days.setdefault(norm_assigned, set()).update(dates_set)
                if debug_countries:
                    print(f"  Fallback assigned country for step #{i}: {norm_assigned} ({reason})")

            # Forward-fill trip dates without explicit steps: use previous day's country; leading days remain unassigned.
            try:
                # build mapping date -> country for dates already assigned (from steps)
                date_to_country = {}
                for c_name, ds in per_trip_country_days.items():
                    for d in ds:
                        date_to_country[d] = c_name
                last_country = None
                for d in sorted(tmp_dates):
                    if d in date_to_country:
                        last_country = date_to_country[d]
                        continue
                    if last_country:
                        # forward-fill from previous assigned day
                        per_trip_country_days.setdefault(last_country, set()).add(d)
                        country_days.setdefault(last_country, set()).add(d)
                        if debug_countries:
                            print(f"  Forward-fill assigned date {d} -> {last_country}")
                    else:
                        # leading day without previous assignment remains unassigned
                        if debug_countries:
                            print(f"  Leading unassigned date (no previous step): {d}")
            except Exception:
                pass

            # compute per-trip continent aggregation
            per_trip_continent_days = {}
            for c, ds in per_trip_country_days.items():
                cont = self._country_to_continent(c)
                if cont:
                    per_trip_continent_days.setdefault(cont, set()).update(ds)

            per_trip_summary = {
                'path': str(p),
                'name': ts.get('name'),
                'steps': len(tp.steps),
                'travel_days': len(tmp_dates),
                'total_km': ts.get('total_km'),
                'photos': ts.get('photos'),
                'videos': ts.get('videos'),
                'country_days': {c: len(ds) for c, ds in per_trip_country_days.items()},
                'continent_days': {c: len(ds) for c, ds in per_trip_continent_days.items()}
            }
            # store per-trip summary
            if verbose:
                all_stats.append(per_trip_summary)

        # clamp travel_dates to today so future-dates within trips are ignored
        today = None
        try:
            today = datetime.now().date()
            travel_dates = set(d for d in travel_dates if d <= today)
        except Exception:
            pass

        # Normalize/merge country keys (handles short codes from cache or inconsistent keys)
        normalized_country_days = {}
        for raw_country, dates_set in country_days.items():
            try:
                norm = self._normalize_country(raw_country) or raw_country
            except Exception:
                norm = raw_country
            normalized_country_days.setdefault(norm, set()).update(dates_set)
        # Compute country day counts from normalized data
        country_counts = {c: len(ds) for c, ds in normalized_country_days.items()}
        total_travel_days = len(travel_dates)
        total_country_days = sum(country_counts.values())
        # Recompute unmatched days as any travel date not present in the country assignment sets
        all_country_dates = set()
        for ds in normalized_country_days.values():
            all_country_dates.update(ds)
        unmatched_days = set(d for d in travel_dates if d not in all_country_dates)
        unmatched_count = len(unmatched_days)

        # Compute continent aggregation based on normalized country keys
        continents_days = {}
        for c, ds in normalized_country_days.items():
            cont = self._country_to_continent(c)
            if cont:
                continents_days.setdefault(cont, set()).update(ds)
        visited_continents_count = len(continents_days)
        WORLD_CONTINENT_COUNT = 7
        visited_continents_percent = round((visited_continents_count / float(WORLD_CONTINENT_COUNT)) * 100.0, 2) if WORLD_CONTINENT_COUNT else None
        
        if debug_countries:
            print(f"\nCountry Assignment Summary:")
            print(f"  Total travel days: {total_travel_days}")
            print(f"  Days assigned to countries: {total_country_days}")
            print(f"  Days without country assignment: {unmatched_count}")
            if unmatched_days:
                print(f"  Unmatched dates: {sorted(unmatched_days)}")
            if continents_days:
                print(f"  Continents assignment: { {k: len(v) for k,v in continents_days.items()} }")

        # determine overall period for returned aggregate
        # if the caller supplied a start/end range, use that range exactly
        # otherwise if a year was requested (and no explicit range), use the full year
        # fall back to travel_dates bounds only when nothing explicit provided
        if start_date and end_date:
            period_start = start_date.date()
            period_end = end_date.date()
        elif year:
            try:
                period_start = date(year, 1, 1)
                period_end = date(year, 12, 31)
            except Exception:
                # invalid year, fallback to travel_dates
                period_start = min(travel_dates) if travel_dates else None
                period_end = max(travel_dates) if travel_dates else None
        else:
            # compute overall period for all-time mode
            period_start = min(travel_dates) if travel_dates else None
            period_end = max(travel_dates) if travel_dates else None

        # never report a period that extends past today
        try:
            if period_end and period_end > today:
                period_end = today
        except Exception:
            pass

        # compute non-travel days when we have a defined period
        non_travel_days = None
        if period_start and period_end:
            # if period was explicitly specified (date range or year), we count all days
            if start_date or end_date or year:
                total_period = (period_end - period_start).days + 1
                non_travel_days = total_period - total_travel_days
            else:
                # all-time case: leave None (previous behaviour computed until today, but
                # the value is misleading so we drop it)
                non_travel_days = None

        visited_countries_count = len(country_counts)
        visited_countries_percent = round((visited_countries_count / float(WORLD_COUNTRY_COUNT)) * 100.0, 2) if WORLD_COUNTRY_COUNT else None

        aggregate = {
            'trip_count': included_trips,
            'total_km': round(total_km, 2),
            'total_photos': total_photos,
            'total_videos': total_videos,
            'total_steps': total_steps,
            'total_travel_days': total_travel_days,
            'total_country_days': total_country_days,
            'unmatched_days': unmatched_count,
            'period_start': period_start.isoformat() if period_start else None,
            'period_end': period_end.isoformat() if period_end else None,
            'period_total_days': ((period_end - period_start).days + 1) if period_start and period_end else None,
            'period_non_travel_days': non_travel_days,
            'visited_countries_count': visited_countries_count,
            'visited_countries_percent': visited_countries_percent,
            'countries': country_counts,
            'continents': {c: len(ds) for c, ds in continents_days.items()},
            'visited_continents_count': visited_continents_count,
            'visited_continents_percent': visited_continents_percent,
        }
        if verbose:
            aggregate['per_trip'] = all_stats
        if debug_countries:
            aggregate['debug_info'] = {
                'unmatched_dates': [d.isoformat() for d in sorted(unmatched_days)]
            }
        return aggregate

    def generate_overview_map(self, trip_paths: list) -> bytes:
        """Create a combined overview map for the provided trips (list of Path).
        Uses an in-memory combined object compatible with MapGenerator.generate_overview_map."""
        steps = []
        for p in trip_paths:
            try:
                tp = TripParser(p)
                tp.load()
            except Exception:
                continue
            for s in tp.steps:
                steps.append(s)
        # Create a tiny wrapper with steps and get_route_coordinates
        class _Combined:
            def __init__(self, steps):
                self.steps = steps
                self.trip_path = Path('.')
            def get_route_coordinates(self):
                coords = []
                for s in self.steps:
                    loc = s.get('data', {}).get('location') or {}
                    if isinstance(loc, dict):
                        lat = loc.get('lat') or loc.get('latitude')
                        lon = loc.get('lon') or loc.get('lng') or loc.get('longitude')
                        try:
                            if lat is not None and lon is not None:
                                coords.append((float(lon), float(lat)))
                        except Exception:
                            pass
                return coords
        combined = _Combined(steps)
        mg = self.map_generator.clone()
        try:
            return mg.generate_overview_map(combined)
        except Exception:
            return b''

    def export_stats_json(self, stats: dict, out_path: Path):
        try:
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False


class InteractiveHtmlBuilder:
    """Builds a standalone interactive HTML travel journal with Leaflet map."""

    def __init__(self, output_path: Path, trip_parser: TripParser, config: dict = None, language_manager: LanguageManager = None, cli_language_manager: LanguageManager = None):
        self.output_path = Path(output_path)
        self.trip_parser = trip_parser
        self.config = config or {}
        self.lang = language_manager or get_default_language_manager()
        self.cli_lang = cli_language_manager or get_default_language_manager()
        self.photo_max_width = int(self.config.get("html_photo_max_width", 1200))
        self._image_data_cache = OrderedDict()
        self._memory_cache_items = int(self.config.get("html_memory_cache_items", 256))

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
                if self.photo_max_width > 0 and img.width > self.photo_max_width:
                    ratio = float(self.photo_max_width) / float(img.width)
                    new_h = max(1, int(round(img.height * ratio)))
                    img = img.resize((self.photo_max_width, new_h), RESAMPLING_LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                buf.seek(0)
                data = buf.read()
                b64 = base64.b64encode(data).decode("ascii")
                url = f"data:image/jpeg;base64,{b64}"
                if key is not None:
                    self._cache_set(self._image_data_cache, key, url)
                return url
        except Exception:
            return None

    def _is_html_osm_available(self) -> bool:
        try:
            test_url = OSM_TILE_URL.format(s="a", z=2, x=1, y=1)
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; PolarstepsPDFCreator/1.0)",
                "Referer": "https://www.openstreetmap.org/"
            }
            r = requests.get(test_url, headers=headers, timeout=6)
            if r is None or r.status_code != 200:
                return False
            content = r.content or b""
            if b"Access blocked" in content or b"Referer is required" in content:
                return False
            ctype = r.headers.get("content-type", "")
            return ctype.startswith("image") and len(content) > 100
        except Exception:
            return False

    def _escape(self, text: str) -> str:
        return html.escape(text or "")

    def _location_to_coordinates(self, location: dict):
        if not isinstance(location, dict):
            return None, None
        lat = location.get("lat") or location.get("latitude")
        lon = location.get("lon") or location.get("lng") or location.get("longitude")
        try:
            return float(lat), float(lon)
        except Exception:
            return None, None

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

        trip_days = 0
        try:
            if start_date and end_date:
                trip_days = max(1, (end_date - start_date).days + 1)
            elif start_date:
                trip_days = 1
        except Exception:
            trip_days = 0

        subtitle = self.lang.t(
            "pdf.subtitle",
            date=date_str,
            steps=step_count,
            steps_label=self.lang.t("pdf.steps_label"),
            km=total_km,
            km_label=self.lang.t("units.km"),
        )

        steps_data = []
        for i, step in enumerate(self.trip_parser.steps, start=1):
            step_data = step.get("data", {}) if isinstance(step, dict) else {}
            display_name = step_data.get("display_name", f"{self.lang.t('pdf.step_label')} {i}")
            description = step_data.get("description", "") if isinstance(step_data, dict) else ""
            location = step_data.get("location", {}) if isinstance(step_data, dict) else {}
            lat, lon = self._location_to_coordinates(location)

            # normalize step date for timeline display
            start_time = step_data.get("start_time")
            step_date_str = ""
            if start_time:
                try:
                    if isinstance(start_time, (int, float)):
                        step_date_str = datetime.fromtimestamp(start_time).strftime(date_fmt)
                    else:
                        numeric_ts = float(start_time)
                        step_date_str = datetime.fromtimestamp(numeric_ts).strftime(date_fmt)
                except Exception:
                    step_date_str = str(start_time)

            photo_list = []
            for p in step.get("photos", []):
                try:
                    if isinstance(p, str) and (p.startswith("http://") or p.startswith("https://")):
                        photo_list.append(p)
                        continue
                    path = Path(p)
                    if not path.is_file() and hasattr(self.trip_parser, 'trip_path'):
                        path = (self.trip_parser.trip_path / p)
                    if path.is_file():
                        data_url = self._image_file_to_data_url(path)
                        if data_url:
                            photo_list.append(data_url)
                    elif isinstance(p, str) and p.startswith("file:"):
                        photo_list.append(p)
                except Exception:
                    continue

            # Fallback to cover/big photo fields if no local step photos are present
            if not photo_list:
                for key in ("cover_photo", "cover_photo_path", "cover_photo_thumb_path", "main_media_item_path", "cover_photo_url"):
                    try:
                        val = step_data.get(key) if isinstance(step_data, dict) else None
                        if isinstance(val, dict):
                            val = val.get("path") or val.get("small_thumbnail_path") or val.get("large_thumbnail_path")
                        if not isinstance(val, str) or not val:
                            continue
                        if val.startswith("http://") or val.startswith("https://") or val.startswith("file:"):
                            # remote URL or file URL
                            photo_list.append(val)
                            break
                        local_path = Path(val)
                        if not local_path.is_file() and hasattr(self.trip_parser, 'trip_path'):
                            local_path = self.trip_parser.trip_path / val
                        if local_path.is_file():
                            data_url = self._image_file_to_data_url(local_path)
                            if data_url:
                                photo_list.append(data_url)
                                break
                    except Exception:
                        continue

            video_list = []
            for v in step.get("videos", []):
                try:
                    if isinstance(v, str) and (v.startswith("http://") or v.startswith("https://") or v.startswith("file:")):
                        video_list.append(v)
                        continue
                    if isinstance(v, str):
                        file_path = Path(v)
                        if not file_path.is_file() and hasattr(self.trip_parser, 'trip_path'):
                            file_path = (self.trip_parser.trip_path / v)
                        if file_path.is_file():
                            video_list.append(file_path.resolve().as_uri())
                            continue
                    if isinstance(v, Path) and v.is_file():
                        video_list.append(v.resolve().as_uri())
                except Exception:
                    continue

            steps_data.append({
                "step_number": i,
                "title": display_name,
                "meta": {
                    "date": step_date_str or (step_data.get("start_time") if step_data.get("start_time") is not None else ""),
                    "location": location.get("name") or "",
                },
                "description": self._escape(description).replace("\n", "<br/>"),
                "lat": lat,
                "lon": lon,
                "photos": photo_list,
                "videos": video_list,
            })

        steps_json = json.dumps(steps_data, ensure_ascii=False)

        html_parts = [
            '<!DOCTYPE html>',
            '<html lang="en">',
            '<head>',
            '<meta charset="utf-8"/>',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0"/>',
            '<title>' + self._escape(trip_name) + '</title>',
            '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>',
            '<style>',
            'body { font-family: "Segoe UI", sans-serif; background:#f6f7f8; margin:0; padding:0; }',
            '.step-photo-marker { border-radius: 50% !important; overflow: hidden !important; border: 2px solid #fff !important; box-shadow: 0 0 4px rgba(0,0,0,0.4) !important; }',
            '.leaflet-marker-icon.step-photo-marker { width: 44px !important; height: 44px !important; }',
            '.top-bar { background: #1A5F7A; color: #fff; padding: 12px 16px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; }',
            '.top-bar h1 { margin:0; font-size: 1.25rem; }',
            '.top-bar .stats { font-size: 0.9rem; color: #e8f7ff; }',
            '.timeline { display: flex; gap: 8px; overflow-x: auto; overflow-y: hidden; padding: 8px 16px; background: #fff; border-bottom: 1px solid #ddd; }',
            '.timeline-item { flex: 0 0 auto; padding: 6px 10px; border-radius: 12px; background: #f0f5f9; font-size: 0.82rem; cursor: pointer; white-space: nowrap; }',
            '.timeline-item.active { background: #1A5F7A; color: #fff; font-weight: bold; }',
            '.layout { display: flex; flex-wrap: nowrap; }',
            '.sidebar { flex: 0 0 auto; width: 70%; max-width: calc(100% - 320px); min-width: 320px; background: #fff; border-right: 1px solid #ccc; height: calc(100vh - 136px); overflow-y: auto; }',
            '.map-resize-handle { width: 6px; cursor: col-resize; background: rgba(0,0,0,0.15); }',
            '.map-container { flex: 0 0 auto; width: 28%; min-width: 320px; height: calc(100vh - 136px); position: relative; }',
            '#map { width: 100%; height: 100%; }',
            '.step-item { padding: 14px 12px; border-bottom: 1px solid #eee; cursor: pointer; }',
            '.step-item.active { background: #e8f5ff; border-left: 4px solid #1A5F7A; }',
            '.step-title { font-weight: 700; font-size: 1.1rem; margin-bottom: 4px; }',
            '.step-meta { font-size: 0.82rem; color: #666; margin-bottom: 6px; }',
            '.step-desc { font-size: 0.98rem; line-height: 1.5; margin: 8px 0; color: #333; }',
        ] + _shared_detail_media_css() + [
            '.controls { padding: 10px; background: #fff; border-top: 1px solid #ddd; display: flex; justify-content: flex-end; gap: 8px; align-items: center; }',
            '.controls button { padding: 6px 10px; border: 1px solid #1A5F7A; background: #1A5F7A; color: #fff; border-radius: 4px; cursor: pointer; }',
            '.controls button:hover { background: #146077; }',
            '.view-switch { display: flex; border: 1px solid #1A5F7A; border-radius: 6px; overflow: hidden; }',
            '.view-switch button { padding: 6px 10px; border: none; border-right: 1px solid #1A5F7A; background: #e6f4f8; color: #1A5F7A; font-weight: 600; }',
            '.view-switch button:last-child { border-right: none; }',
            '.view-switch button.active { background: #1A5F7A; color: #fff; }',
            '.sidebar.hidden, .map-container.hidden, .map-resize-handle.hidden { display: none !important; }',
            'body.map-only .sidebar, body.map-only .map-resize-handle { display: none !important; }',
            'body.map-only .map-container { width: 100% !important; min-width: 0 !important; max-width: 100% !important; flex: 1 1 100% !important; }',
            'body.steps-only .map-container, body.steps-only .map-resize-handle { display: none !important; }',
            'body.steps-only .sidebar { width: 100% !important; min-width: 0 !important; max-width: 100% !important; flex: 1 1 100% !important; }',
            'body.both .sidebar { width: 70%; }',
            'body.both .map-container { width: 28%; }',
            '</style>',
            '</head>',
            '<body>',
            '<div class="top-bar">',
            '<h1>' + self._escape(trip_name) + '</h1>',
            '<div class="stats">' + self._escape(date_str) + ' • ' + str(trip_days) + ' days • ' + str(step_count) + ' steps • ' + str(total_km) + ' km</div>',
            '</div>',
            '<div class="timeline" id="timeline"></div>',
            '<div class="layout">',
            '<div class="sidebar" id="step-list"></div>',
            '<div id="map-resize-handle" class="map-resize-handle" title="Drag to resize map"></div>',
            '<div class="map-container"><div id="map"></div></div>',
            '</div>',
            '<div class="controls">',
            '<div class="view-switch">',
            '<button id="view-steps-btn">Steps</button>',
            '<button id="view-both-btn" class="active">Both</button>',
            '<button id="view-map-btn">Map</button>',
            '</div>',
            '<button id="prev-step">Previous</button>',
            '<button id="next-step">Next</button>',
            '</div>',
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>',
            '<script>',
            'var steps = ' + steps_json + ';',
            'var htmlMapStyle = ' + json.dumps(str(self.config.get("html_map_style", "road")).lower().strip()) + ';',
            'var htmlOsmAvailable = ' + json.dumps(self._is_html_osm_available()) + ';',
            'var osmLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "&copy; OpenStreetMap contributors", maxZoom: 18, subdomains: ["a", "b", "c"], errorTileUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQImWNgYGD4DwABBAEAQco6VwAAAABJRU5ErkJggg==" });',
            'var esriRoadLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, USGS, NGA, EPA", maxZoom: 18 });',
            'var satelliteLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, USGS, NGA, EPA", maxZoom: 18 });',
            'var hybridLayer = L.layerGroup([satelliteLayer, L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, USGS, NGA, EPA", opacity: 0.7, maxZoom: 18 })]);',
            'var baseLayers = { "Satellite": satelliteLayer, "Hybrid": hybridLayer };',
            'if (htmlOsmAvailable) { baseLayers["OpenStreetMap"] = osmLayer; } else { baseLayers["Road (fallback)"] = esriRoadLayer; }',
            'var selectedLayer = esriRoadLayer;',
            'if (htmlMapStyle === "satellite") { selectedLayer = satelliteLayer; } else if (htmlMapStyle === "hybrid") { selectedLayer = hybridLayer; } else if (htmlMapStyle === "road" && htmlOsmAvailable) { selectedLayer = osmLayer; }',
            'var map = L.map("map", { layers: [selectedLayer] }).setView([0,0],2);',
            'L.control.layers(baseLayers, null, { collapsed: false }).addTo(map);',
            'var markers = [];',
            'var currentIndex = -1;',
            'function normalizeNumber(x) { return (typeof x === "number" && !isNaN(x) ? x : null); }',
            'function updateActiveStep(index) {',
            '  document.querySelectorAll(".step-item").forEach(function(n){ n.classList.toggle("active", n.id === "step-" + index); });',
            '  document.querySelectorAll(".timeline-item").forEach(function(n){ n.classList.toggle("active", n.dataset.stepIndex === String(index)); });',
            '  var activeTimelineItem = document.querySelector(\'.timeline-item.active\');',
            '  if (activeTimelineItem && activeTimelineItem.scrollIntoView) {',
            '    activeTimelineItem.scrollIntoView({ behavior: \'smooth\', inline: \'center\', block: \'nearest\' });',
            '  }',
            '}',
            'function showStep(index, scrollStep=true, forceBoth=false) {',
            '  if (index < 0 || index >= steps.length) return;',
            '  if (forceBoth) { setViewMode(0); }',
            '  if (currentIndex === index) return;',
            '  currentIndex = index;',
            '  var step = steps[index];',

            '  var lat = normalizeNumber(step.lat), lon = normalizeNumber(step.lon);',
            '  if (lat !== null && lon !== null) { map.flyTo([lat, lon], 12, {duration: 0.8}); }',
            '  updateActiveStep(index);',
            '  if (scrollStep) { var el = document.getElementById("step-" + index); if (el) { el.scrollIntoView({behavior: "smooth", block: "center"}); }}',
            '  if (markers[index] && markers[index].openPopup) { markers[index].openPopup(); }',
            '}',
            'var routeCoords = [];',
            'for (var i = 0; i < steps.length; i++) {',
            '  var s = steps[i];',
            '  if (normalizeNumber(s.lat) !== null && normalizeNumber(s.lon) !== null) { routeCoords.push([s.lat, s.lon]); }',
            '}',
            'if (routeCoords.length > 1) { var routeLine = L.polyline(routeCoords, {color: "#1E90FF", weight: 4, opacity: 0.75}).addTo(map); map.fitBounds(routeLine.getBounds().pad(0.08)); } else if (routeCoords.length === 1) { map.setView(routeCoords[0], 10); }',
            'for (var i = 0; i < steps.length; i++) {',
            '  var s = steps[i];',
            '  if (normalizeNumber(s.lat) === null || normalizeNumber(s.lon) === null) continue;',
            '  var marker = null;',
            '  if (s.photos && s.photos.length > 0) {',
            '    try {',
            '      var icon = L.icon({',
            '        iconUrl: s.photos[0],',
            '        iconSize: [44, 44],',
            '        iconAnchor: [22, 44],',
            '        popupAnchor: [0, -44],',
            '        className: "step-photo-marker"',
            '      });',
            '      marker = L.marker([s.lat, s.lon], { icon: icon }).addTo(map);',
            '    } catch (err) {',
            '      marker = L.marker([s.lat, s.lon]).addTo(map);',
            '    }',
            '  } else {',
            '    marker = L.marker([s.lat, s.lon]).addTo(map);',
            '  }',
            '  marker.bindPopup("<strong>" + s.title + "</strong>");',
            '  marker.stepIndex = i;',
            '  marker.on("click", function() { showStep(this.stepIndex, true, true); });',
            '  markers[i] = marker;',
            '}',
            'function renderTimeline() {',
            '  var el = document.getElementById("timeline"); el.innerHTML = "";',
            '  for (var i = 0; i < steps.length; i++) {',
            '    var item = document.createElement("div"); item.className = "timeline-item"; item.dataset.stepIndex = i; item.textContent = (i + 1) + ". " + steps[i].title + (steps[i].meta.date ? " (" + steps[i].meta.date + ")" : "");',
            '    item.addEventListener("click", function(){ showStep(parseInt(this.dataset.stepIndex,10)); });',
            '    el.appendChild(item);',
            '  }',
            '}',
            'function renderStepList() {',
            '  var container = document.getElementById("step-list"); container.innerHTML = "";',
            '  for (let i = 0; i < steps.length; i++) {',
            '    let step = steps[i];',
            '    var wrapper = document.createElement("div"); wrapper.className = "step-item"; wrapper.id = "step-" + i;',
            '    var title = document.createElement("div"); title.className = "step-title"; title.textContent = (i + 1) + ". " + step.title; wrapper.appendChild(title);',
            '    var meta = document.createElement("div"); meta.className = "step-meta"; var mt = []; if (step.meta.location) mt.push(step.meta.location); if (step.meta.date) mt.push(step.meta.date); meta.textContent = mt.join(" • "); wrapper.appendChild(meta);',
            '    var desc = document.createElement("div"); desc.className = "step-desc"; desc.innerHTML = step.description || ""; wrapper.appendChild(desc);',
            '    var carousel = createStepMediaCarousel(step);',
            '    if (carousel) { wrapper.appendChild(carousel); }',
            '    // Video carousel items are handled together with photos in step media carousel.',
            '    wrapper.addEventListener("click", function(event){ var target = event.target; if (target.closest && (target.closest(".photo-nav") || target.closest(".photo-viewer") || target.closest("video"))) { return; } showStep(parseInt(this.id.replace("step-",""),10), true); });',
            '    container.appendChild(wrapper);',
            '  }',
            '}',
        ] + _shared_step_media_carousel_js() + [
            'renderTimeline(); renderStepList();',
            'var timelineEl = document.getElementById("timeline");',
            'if (timelineEl) {',
            '  timelineEl.addEventListener("wheel", function(evt){',
            '    if (!evt.deltaY) return;',
            '    evt.preventDefault();',
            '    var delta = evt.deltaY;',
            '    if (evt.deltaMode === 1) { delta *= 16; }',
            '    timelineEl.scrollLeft += delta;',
            '  });',
            '}',
            'var stepListEl = document.getElementById("step-list");',
            'var observer = new IntersectionObserver(function(entries){ entries.forEach(function(entry){ if (entry.isIntersecting && entry.intersectionRatio > 0.4) { var target = entry.target; var idx = parseInt(target.id.replace("step-",""),10); if (idx !== currentIndex) { showStep(idx, false); } } }); }, { root: stepListEl, threshold: [0.4] });',
            'document.querySelectorAll(".step-item").forEach(function(item){ observer.observe(item); });',
            'if (steps.length > 0) showStep(0);',
            'document.getElementById("prev-step").addEventListener("click", function(){ showStep(Math.max(0, currentIndex - 1), true); });',
            'document.getElementById("next-step").addEventListener("click", function(){ showStep(Math.min(steps.length - 1, currentIndex + 1), true); });',
            'var viewModes = ["both", "map-only", "steps-only"];',
            'var currentViewMode = 0;',
            'function setViewMode(mode) {',
            '  currentViewMode = ((mode % viewModes.length) + viewModes.length) % viewModes.length;',
            '  document.body.className = viewModes[currentViewMode];',
            '  document.getElementById("view-steps-btn").classList.toggle("active", currentViewMode === 2);',
            '  document.getElementById("view-both-btn").classList.toggle("active", currentViewMode === 0);',
            '  document.getElementById("view-map-btn").classList.toggle("active", currentViewMode === 1);',
            '  console.log("setViewMode", currentViewMode, document.body.className);',
            '  if (map && map.invalidateSize) { map.invalidateSize(); }',
            '  var stepList = document.querySelector(".sidebar");',
            '  var mapWrap = document.querySelector(".map-container");',
            '  var resizeHandle = document.querySelector(".map-resize-handle");',
            '  if (currentViewMode === 0) {',
            '    stepList.style.display = "block"; mapWrap.style.display = "block"; resizeHandle.style.display = "block";',
            '    stepList.style.width = "70%"; stepList.style.minWidth = "320px"; stepList.style.maxWidth = "calc(100% - 320px)"; stepList.style.flex = "0 0 auto";',
            '    mapWrap.style.width = "28%"; mapWrap.style.minWidth = "320px"; mapWrap.style.maxWidth = ""; mapWrap.style.flex = "0 0 auto";',
            '  }',
            '  else if (currentViewMode === 1) {',
            '    stepList.style.display = "none"; resizeHandle.style.display = "none"; mapWrap.style.display = "block";',
            '    mapWrap.style.width = "100%"; mapWrap.style.minWidth = "0"; mapWrap.style.maxWidth = "100%"; mapWrap.style.flex = "1 1 100%";',
            '  }',
            '  else if (currentViewMode === 2) {',
            '    mapWrap.style.display = "none"; resizeHandle.style.display = "none"; stepList.style.display = "block";',
            '    stepList.style.width = "100%"; stepList.style.minWidth = "0"; stepList.style.maxWidth = "100%"; stepList.style.flex = "1 1 100%";',
            '  }',
            '}',
            'document.getElementById("view-steps-btn").addEventListener("click", function(){ console.log("view-steps clicked"); setViewMode(2); });',
            'document.getElementById("view-both-btn").addEventListener("click", function(){ console.log("view-both clicked"); setViewMode(0); });',
            'document.getElementById("view-map-btn").addEventListener("click", function(){ console.log("view-map clicked"); setViewMode(1); });',
            'setViewMode(0);',
            'window.addEventListener("resize", function(){ map.invalidateSize(); });',
            'var resizeHandle = document.getElementById("map-resize-handle");',
            'var sidebar = document.querySelector(".sidebar");',
            'var mapContainer = document.querySelector(".map-container");',
            'var isResizing = false;',
            'var lastClientX = 0;',
            'resizeHandle.addEventListener("mousedown", function(evt){ isResizing=true; lastClientX=evt.clientX; document.body.style.cursor="col-resize"; document.body.style.userSelect="none"; evt.preventDefault(); });',
            'document.addEventListener("mousemove", function(evt){ if(!isResizing) return; var dx=evt.clientX-lastClientX; lastClientX=evt.clientX; var newSidebarWidth=Math.max(250, sidebar.offsetWidth+dx); var newMapWidth=Math.max(250, mapContainer.offsetWidth-dx); var totalWidth = newSidebarWidth + newMapWidth + resizeHandle.offsetWidth; var available = window.innerWidth - 20; if(totalWidth > available) { var overflow = totalWidth - available; if(dx > 0) { newSidebarWidth -= overflow; } else { newMapWidth -= overflow; } } if(newSidebarWidth < 250 || newMapWidth < 250) return; sidebar.style.width=newSidebarWidth+"px"; mapContainer.style.width=newMapWidth+"px"; map.invalidateSize(); });',
            'document.addEventListener("mouseup", function(){ if(isResizing){ isResizing=false; document.body.style.cursor=""; document.body.style.userSelect=""; map.invalidateSize(); }});',
            '</script>',
            '</body>',
            '</html>'
        ]

        return "\n".join(html_parts)

    def build(self):
        html_text = self._build_html()
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(html_text, encoding='utf-8')
            return True
        except Exception as e:
            print(self.cli_lang.t("render.error", error=e))
            return False


class CombinedHtmlBuilder:
    """Builds a combined interactive HTML overview for multiple trips."""

    def __init__(self, output_path: Path, trip_paths: list, config: dict = None, language_manager: LanguageManager = None, cli_language_manager: LanguageManager = None):
        self.output_path = Path(output_path)
        self.trip_paths = list(trip_paths or [])
        self.config = config or {}
        self.lang = language_manager or get_default_language_manager()
        self.cli_lang = cli_language_manager or get_default_language_manager()
        self.photo_max_width = int(self.config.get("html_photo_max_width", 1200))
        self._image_data_cache = OrderedDict()
        self._memory_cache_items = int(self.config.get("html_memory_cache_items", 256))

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
                if self.photo_max_width > 0 and img.width > self.photo_max_width:
                    ratio = float(self.photo_max_width) / float(img.width)
                    new_h = max(1, int(round(img.height * ratio)))
                    img = img.resize((self.photo_max_width, new_h), RESAMPLING_LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                buf.seek(0)
                data = buf.read()
                b64 = base64.b64encode(data).decode("ascii")
                url = f"data:image/jpeg;base64,{b64}"
                if key is not None:
                    self._cache_set(self._image_data_cache, key, url)
                return url
        except Exception:
            return None

    def _escape(self, text: str) -> str:
        return html.escape(text or "")

    def _normalize_number(self, value):
        try:
            if value is None:
                return None
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str) and value.strip():
                return float(value.strip())
        except Exception:
            pass
        return None

    def _location_to_coordinates(self, location: dict):
        if not isinstance(location, dict):
            return None, None
        lat = location.get("lat") or location.get("latitude")
        lon = location.get("lon") or location.get("lng") or location.get("longitude")
        try:
            return float(lat), float(lon)
        except Exception:
            return None, None

    def _normalize_step_date(self, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(int(value))
            except Exception:
                pass
        if isinstance(value, str):
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(value, fmt)
                except Exception:
                    continue
            try:
                return datetime.fromisoformat(value)
            except Exception:
                pass
        return None

    def _format_date(self, dt: Optional[datetime]) -> str:
        if not dt:
            return ""
        date_fmt = self.lang.get_date_format("date.format.trip", "%d.%m.%Y")
        try:
            return dt.strftime(date_fmt)
        except Exception:
            return str(dt)

    def _load_trips(self):
        loaded = []
        for trip_path in self.trip_paths:
            try:
                parser = TripParser(trip_path)
                parser.load()
            except Exception:
                continue

            start_date, end_date = parser.get_trip_dates()
            if start_date and end_date and end_date < start_date:
                end_date = start_date

            trip_name = parser.get_trip_name() or trip_path.name
            trip_days = 0
            if start_date and end_date:
                trip_days = max(1, (end_date.date() - start_date.date()).days + 1)
            elif start_date:
                trip_days = 1

            steps = []
            for step_idx, step in enumerate(parser.steps, start=1):
                data = step.get("data", {}) if isinstance(step, dict) else {}
                display_name = data.get("display_name") or f"{self.lang.t('pdf.step_label') if hasattr(self.lang, 't') else 'Step'} {step_idx}"
                location = data.get("location", {}) if isinstance(data, dict) else {}
                lat, lon = self._location_to_coordinates(location)
                start_time = data.get("start_time") or data.get("startDate") or data.get("start_date") or data.get("time") or data.get("date") or data.get("timestamp")
                step_date_dt = self._normalize_step_date(start_time)
                photo_urls = []
                photo_url = None
                for p in step.get("photos", []):
                    try:
                        source_url = None
                        if isinstance(p, str) and (p.startswith("http://") or p.startswith("https://") or p.startswith("file:")):
                            source_url = p
                        else:
                            path = Path(p)
                            if not path.is_file() and hasattr(parser, 'trip_path'):
                                path = parser.trip_path / p
                            if path.is_file():
                                source_url = path.resolve().as_uri()
                        if source_url:
                            if photo_url is None:
                                photo_url = source_url
                            photo_urls.append(source_url)
                    except Exception:
                        continue

                step_videos = []
                for v in step.get("videos", []) or []:
                    try:
                        if isinstance(v, str) and (v.startswith("http://") or v.startswith("https://") or v.startswith("file:")):
                            step_videos.append(v)
                        elif isinstance(v, Path) and v.is_file():
                            step_videos.append(v.resolve().as_uri())
                    except Exception:
                        continue

                steps.append({
                    "step_index": step_idx,
                    "title": display_name,
                    "description": self._escape(data.get("description", "") if isinstance(data, dict) else ""),
                    "date": self._format_date(step_date_dt),
                    "location_name": location.get("name") if isinstance(location, dict) else "",
                    "location_detail": location.get("detail") if isinstance(location, dict) else "",
                    "lat": lat,
                    "lon": lon,
                    "photo": photo_url,
                    "photos": photo_urls,
                    "videos": step_videos,
                })

            loaded.append({
                "id": len(loaded),
                "path": str(trip_path),
                "name": trip_name,
                "start_date": start_date.isoformat() if start_date else "",
                "end_date": end_date.isoformat() if end_date else "",
                "start_date_str": self._format_date(start_date),
                "end_date_str": self._format_date(end_date),
                "date_range": self._format_date(start_date) + (" - " + self._format_date(end_date) if start_date and end_date else ""),
                "trip_days": trip_days,
                "total_km": parser.get_total_km(),
                "steps": steps,
            })

        def sort_key(item):
            try:
                return datetime.fromisoformat(item["start_date"]) if item["start_date"] else datetime.min
            except Exception:
                return datetime.min

        return sorted(loaded, key=sort_key)

    def _compute_stats(self, trips):
        total_steps = 0
        total_photos = 0
        total_videos = 0
        total_km = 0.0
        total_trip_days = 0
        period_start = None
        period_end = None

        for trip in trips:
            total_steps += len(trip.get("steps", []))
            total_km += float(trip.get("total_km", 0) or 0)
            total_trip_days += int(trip.get("trip_days", 0) or 0)
            if trip.get("start_date"):
                try:
                    dt = datetime.fromisoformat(trip.get("start_date"))
                    if not period_start or dt < period_start:
                        period_start = dt
                except Exception:
                    pass
            if trip.get("end_date"):
                try:
                    dt = datetime.fromisoformat(trip.get("end_date"))
                    if not period_end or dt > period_end:
                        period_end = dt
                except Exception:
                    pass
            for step in trip.get("steps", []):
                if step.get("photo"):
                    total_photos += 1
                total_videos += len(step.get("videos", []) or [])

        return {
            "trip_count": len(trips),
            "total_steps": total_steps,
            "total_photos": total_photos,
            "total_videos": total_videos,
            "total_km": total_km,
            "total_trip_days": total_trip_days,
            "period_start": period_start.isoformat() if period_start else "",
            "period_end": period_end.isoformat() if period_end else "",
            "period_range": self._format_date(period_start) + (" - " + self._format_date(period_end) if period_start and period_end else ""),
        }

    def _trip_colors(self):
        return [
            "#1E90FF", "#E63946", "#2A9D8F", "#F4A261", "#457B9D", "#8A2BE2", "#FF6347", "#6A5ACD", "#20B2AA", "#FFB703",
        ]

    def _build_html(self) -> str:
        trips = self._load_trips()
        stats = self._compute_stats(trips)
        trip_data_json = json.dumps(trips, ensure_ascii=False)
        # protect embedded JSON from closing the script tag in HTML if data contains </script>
        trip_data_json = trip_data_json.replace('</script>', '<\\/script>')
        trip_data_json = trip_data_json.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
        colors = self._trip_colors()
        colors_json = json.dumps(colors)

        trip_list_items = []
        for index, trip in enumerate(trips):
            trip_list_items.append(
                '<div class="trip-item" data-trip-id="' + str(trip['id']) + '">' +
                '<div class="title">' + self._escape(trip['name']) + '</div>' +
                '<div class="meta">' + self._escape(trip['date_range'] or 'No dates') + ' • ' + str(int(trip['total_km'] or 0)) + ' km • ' + str(len(trip['steps'])) + ' steps</div>' +
                '</div>'
            )
        trip_list_html = ''.join(trip_list_items)

        html_parts = [
            '<!DOCTYPE html>',
            '<html lang="en">',
            '<head>',
            '<meta charset="utf-8"/>',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0"/>',
            '<title>Combined Trip Overview</title>',
            '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>',
            '<noscript><style>.noscript-warning{padding:16px;background:#ffe8e8;color:#900;text-align:center;font-weight:700;}</style></noscript>',
            '<style>',
            '.top-bar { background: #1A5F7A; color: #fff; padding: 16px; }',
            '.top-bar h1 { margin: 0 0 8px 0; font-size: 1.5rem; }',
            '.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }',
            '.stat-card { background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.2); border-radius: 12px; padding: 12px 14px; }',
            '.stat-card .label { font-size: 0.85rem; opacity: 0.8; }',
            '.stat-card .value { font-size: 1.2rem; font-weight: 700; margin-top: 4px; }',
            'body { font-family: "Segoe UI", sans-serif; background:#f6f7f8; margin:0; padding:0; color:#2c3e50; min-height:100vh; overflow-x:hidden; }',
            '.main-layout { display: grid; grid-template-columns: minmax(250px, auto) 8px minmax(250px, 1fr); gap: 12px; padding: 16px; min-height: calc(100vh - 120px); align-items: stretch; }',
            '.trip-list { background:#fff; border-radius: 16px; box-shadow: 0 2px 12px rgba(0,0,0,0.05); overflow:hidden; display:flex; flex-direction:column; min-width: 250px; }',
            '.trip-list h2 { margin: 0; padding: 16px; font-size: 1rem; border-bottom: 1px solid #eee; }',
            '#trip-list { overflow-y:auto; max-height: none; }',
            '.trip-item { padding: 14px 16px; border-bottom: 1px solid #f0f0f0; cursor: pointer; }',
            '.trip-item:last-child { border-bottom: none; }',
            '.trip-item.active { background: #fff1f1; border-left: 4px solid #ff3b3b; }',
            '.trip-item .title { font-weight: 700; margin-bottom: 4px; }',
            '.trip-item .meta { font-size: 0.85rem; color: #555; }',
            '.map-panel { display: grid; grid-template-rows: minmax(220px, auto) 8px minmax(320px, auto); gap: 0; min-width: 0; }',
            '.map-panel.no-selection { grid-template-columns: 1fr; grid-template-rows: 1fr; }',
            '.map-panel.no-selection .detail-resize-handle, .map-panel.no-selection .details-panel { display: none !important; }',
            '.map-wrap { position: relative; background:#fff; border-radius: 16px; overflow:hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.05); min-height: 220px; min-width: 0; height: 100%; }',
            '.map-wrap .settings-menu { position: absolute; bottom: 16px; right: 16px; z-index: 9999; }',
            '.map-wrap .settings-toggle { border:none; width: 44px; height: 44px; border-radius: 50%; background: rgba(255,255,255,0.9); color: #1A5F7A; font-size: 20px; font-weight: 700; cursor: pointer; box-shadow: 0 10px 24px rgba(0,0,0,0.18); display: flex; align-items: center; justify-content: center; }',
            '.map-wrap .settings-panel { position: absolute; bottom: 72px; right: 0; min-width: 240px; background: rgba(255,255,255,0.92); border-radius: 16px; box-shadow: 0 24px 50px rgba(0,0,0,0.18); padding: 14px; display: none; backdrop-filter: blur(18px); }',
            '.map-wrap .settings-panel.open { display: block; }',
            '.map-wrap .settings-panel h3 { margin: 0 0 12px 0; font-size: 0.95rem; color: #0f3f54; }',
            '.map-wrap .settings-panel .settings-item { margin-bottom: 10px; }',
            '.map-wrap .settings-panel .settings-item:last-child { margin-bottom: 0; }',
            '.map-wrap .settings-panel .style-options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }',
            '.map-wrap .settings-panel .style-option { width: 100%; border: 1px solid #cde7f2; border-radius: 10px; background: #f2fbff; color: #0f3f54; padding: 8px 10px; cursor: pointer; text-align: center; font-weight: 700; }',
            '.map-wrap .settings-panel .style-option.active { background: #1A5F7A; color: #fff; border-color: #0f3f54; }',
            '.map-wrap .settings-panel button.secondary { background: #2a9d8f; }',
            '#combined-map { width: 100%; height: 100%; min-height: 100%; }',
            '.step-marker-icon { border-radius: 50%; overflow: hidden; border: 2px solid rgba(255,255,255,0.9); box-shadow: 0 0 0 3px rgba(255,255,255,0.95); background: #fff; }',
            '.step-marker-icon img { width: 100%; height: 100%; object-fit: cover; display: block; }',
            '.details-panel { background:#fff; border-radius: 16px; padding: 16px; box-shadow: 0 2px 12px rgba(0,0,0,0.05); display:flex; flex-direction:column; overflow:hidden; min-height: 320px; max-height: none; min-width: 0; }',
            '.details-panel h2 { margin-top:0; }',
            '.detail-actions { display:flex; justify-content:flex-end; margin: 12px 0 0 0; }',
            '.main-resize-handle, .detail-resize-handle { background: rgba(0,0,0,0.08); border-radius: 4px; }',
            '.main-resize-handle { width: 8px; cursor: col-resize; }',
            '.detail-resize-handle { width: auto; height: 8px; cursor: row-resize; }',
            'body.detail-fullscreen .detail-resize-handle { width: 8px; height: auto; cursor: col-resize; }',
            'body.detail-fullscreen .main-resize-handle { display:none !important; }',
            '.fullscreen-toggle { border:none; padding: 10px 14px; border-radius: 10px; cursor:pointer; background:#2a9d8f; color:#fff; font-weight:700; }',
            '.step-row { padding: 10px 0; border-bottom: 1px solid #f0f0f0; }',
            '.step-row:last-child { border-bottom: none; }',
            '.step-row .step-title { font-weight: 700; }',
            '.step-row .step-meta { font-size: 0.85rem; color: #555; margin-top: 4px; }',
            '.step-row .step-location-detail { font-size: 0.82rem; color: #666; margin-top: 4px; }',
            '.step-row .step-desc { font-size: 0.95rem; line-height: 1.5; margin: 10px 0 0 0; color: #333; }',
            '.step-row .step-media { margin-top: 10px; }',
            '.step-row .step-media img { width: 100%; max-height: 260px; object-fit: cover; border-radius: 12px; }',
            '#selected-step-list { overflow-y:auto; max-height: none; padding-right: 8px; }',
            'body.detail-fullscreen .main-layout { grid-template-columns: 1fr; }',
            'body.detail-fullscreen .trip-list, body.detail-fullscreen .settings-menu { display:none !important; }',
            'body.detail-fullscreen .map-panel { display: grid; grid-template-columns: 420px 8px minmax(0, 1fr); grid-template-rows: 1fr; gap: 0; align-items: start; }',
            'body.detail-fullscreen .details-panel { order: 1; max-height: calc(100vh - 96px); border-radius: 16px; box-shadow: 0 2px 12px rgba(0,0,0,0.05); min-width: 250px; }',
            'body.detail-fullscreen .detail-resize-handle { order: 2; }',
            'body.detail-fullscreen .map-wrap { order: 3; min-height: calc(100vh - 96px); min-width: 250px; }',
            'body.detail-fullscreen #selected-step-list { max-height: calc(100vh - 210px); }',
        ] + _shared_detail_media_css() + [
            '.step-row .video-list { display:flex; flex-wrap:wrap; gap:8px; margin-top: 10px; }',
            '.step-row .video-link { display:inline-block; padding: 8px 10px; background:#1A5F7A; color:#fff; border-radius: 12px; text-decoration:none; font-size: 0.85rem; }',
            '.controls { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:0; }',
            '.controls button { border:none; padding: 10px 14px; border-radius: 10px; cursor:pointer; background:#1A5F7A; color:#fff; font-weight:700; }',
            '.controls button.secondary { background:#2a9d8f; }',
            '.controls button.active { background:#0f3f54; }',
            '.trip-banner { padding: 12px 16px; display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; border-bottom: 1px solid #eee; }',
            '.trip-banner .badge { background:#1A5F7A; color:#fff; border-radius: 999px; padding: 4px 10px; font-size:0.82rem; }',
            '@media (max-width: 1040px) { .main-layout { grid-template-columns: 1fr; } .map-panel { order: -1; } }',
            '</style>',
            '</head>',
            '<body>',
            '<noscript><div class="noscript-warning">JavaScript muss aktiviert sein, damit die Karte und Trip-Auswahl funktionieren.</div></noscript>',
            '<div class="top-bar">',
            '<h1>Combined Trip Overview</h1>',
            '<div class="stats-grid">',
            f'<div class="stat-card"><div class="label">Trips</div><div class="value">{stats["trip_count"]}</div></div>',
            f'<div class="stat-card"><div class="label">Steps</div><div class="value">{stats["total_steps"]}</div></div>',
            f'<div class="stat-card"><div class="label">Travel days</div><div class="value">{stats["total_trip_days"]}</div></div>',
            f'<div class="stat-card"><div class="label">Kilometers</div><div class="value">{stats["total_km"]:.0f}</div></div>',
            f'<div class="stat-card"><div class="label">Period</div><div class="value">{stats["period_range"] or "n/a"}</div></div>',
            '</div>',
            '</div>',
            '<div class="main-layout">',
            '<div class="trip-list">',
            '<h2>Trips sorted by start date</h2>',
            '<div id="trip-list">' + trip_list_html + '</div>',
            '</div>',
            '<div id="main-resize-handle" class="main-resize-handle" title="Drag to resize trips/map"></div>',
            '<div class="map-panel">',
            '<div class="map-wrap">',
            '<div class="settings-menu">',
            '<button id="map-settings-toggle" class="settings-toggle">⚙</button>',
            '<div class="settings-panel" id="map-settings-panel">',
            '<h3>Map settings</h3>',
            '<div class="settings-item"><button id="show-all" class="secondary">Show all trips</button></div>',
            '<div class="settings-item"><button id="show-selected" class="secondary">Show selected trip</button></div>',
            '<div class="settings-item"><button id="fit-map" class="secondary">Fit map</button></div>',
            '<div class="settings-item">Map style</div>',
            '<div class="settings-item style-options">',
            '<button type="button" class="style-option" data-style="road">Street</button>',
            '<button type="button" class="style-option" data-style="satellite">Satellite</button>',
            '<button type="button" class="style-option" data-style="hybrid">Hybrid</button>',
            '</div>',
            '</div>',
            '</div>',
            '<div id="combined-map"></div>',
            '</div>',
            '<div id="detail-resize-handle" class="detail-resize-handle" title="Drag to resize detail/map"></div>',
            '<div class="details-panel">',
            '<h2 id="selected-trip-title">Select a trip to inspect details</h2>',
            '<div id="selected-trip-meta"></div>',
            '<div id="selected-step-list"></div>',
            '</div>',
            '</div>',
            '</div>',
            '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>',
            '<script>',
            'var trips = ' + trip_data_json + ';',
            'var tripColors = ' + colors_json + ';',
            'var mutedRouteColor = "rgba(120, 130, 140, 0.65)";',
            'var selectedRouteColor = "#ff3b3b";',
            'var selectedTripId = null;',
            'var htmlMapStyle = ' + json.dumps(str(self.config.get("html_map_style", "road")).lower().strip()) + ';',
            'var leafletAvailable = (typeof L !== "undefined");',
            'if (!leafletAvailable) {',
            '  var mapEl = document.getElementById("combined-map");',
            '  if (mapEl) {',
            '    mapEl.innerHTML = \'<div style="height:100%;display:flex;align-items:center;justify-content:center;padding:18px;color:#345;">Map unavailable (Leaflet failed to load). Detail view is still available.</div>\';',
            '  }',
            '  var noopBounds = { pad: function(){ return this; } };',
            '  var noopLayer = { addTo: function(){ return this; }, setStyle: function(){}, getBounds: function(){ return noopBounds; } };',
            '  var noopMarker = { bindPopup: function(){ return this; }, on: function(){ return this; }, addTo: function(){ return this; } };',
            '  var noopMap = { setView: function(){ return this; }, fitBounds: function(){ return this; }, invalidateSize: function(){ return this; }, addLayer: function(){ return this; }, removeLayer: function(){ return this; } };',
            '  L = {',
            '    tileLayer: function(){ return noopLayer; },',
            '    layerGroup: function(){ return noopLayer; },',
            '    map: function(){ return noopMap; },',
            '    polyline: function(){ return noopLayer; },',
            '    divIcon: function(opts){ return opts || {}; },',
            '    marker: function(){ return noopMarker; },',
            '    circleMarker: function(){ return noopMarker; },',
            '    latLngBounds: function(){ return noopBounds; }',
            '  };',
            '}',
            'var esriRoadLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, USGS, NGA, EPA", maxZoom: 18 });',
            'var satelliteLayer = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, USGS, NGA, EPA", maxZoom: 18 });',
            'var hybridLayer = L.layerGroup([satelliteLayer, L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, USGS, NGA, EPA", opacity: 0.7, maxZoom: 18 })]);',
            'var mapStyleLayers = { road: esriRoadLayer, satellite: satelliteLayer, hybrid: hybridLayer };',
            'var currentMapStyle = htmlMapStyle && mapStyleLayers[htmlMapStyle] ? htmlMapStyle : "road";',
            'var map = L.map("combined-map", { layers: [mapStyleLayers[currentMapStyle]], zoomControl: true }).setView([0, 0], 2);',
            'var markers = [];',
            'var polylines = [];',
            'var tripLayers = {};',
            'var mainResize = null;',
            'var detailResize = null;',
            'function buildTripList() {',
            '  var container = document.getElementById("trip-list");',
            '  container.innerHTML = "";',
            '  trips.forEach(function(trip, index) {',
            '    var item = document.createElement("div");',
            '    item.className = "trip-item";',
            '    item.dataset.tripId = trip.id;',
            '    item.innerHTML = \'<div class="title">\' + (index + 1) + ". " + trip.name + \'</div>\' +',
            '      \'<div class="meta">\' + (trip.date_range || "No dates") + " • " + trip.total_km + " km • " + trip.steps.length + " steps</div>";',
            '    item.addEventListener("click", function() {',
            '      if (selectedTripId === trip.id) {',
            '        showAllTrips();',
            '      } else {',
            '        selectTrip(trip.id);',
            '      }',
            '    });',
            '    container.appendChild(item);',
            '  });',
            '}',
            'function buildMap() {',
            '  var allCoords = [];',
            '  trips.forEach(function(trip, index) {',
            '    var coords = trip.steps.filter(function(step){ return step.lat !== null && step.lon !== null; }).map(function(step){ return [step.lat, step.lon]; });',
            '    if (coords.length === 0) { return; }',
            '    var originalColor = tripColors[index % tripColors.length];',
            '    var line = L.polyline(coords, { color: mutedRouteColor, weight: 3, opacity: 0.7 }).addTo(map);',
            '    polylines.push({ tripId: trip.id, layer: line, normalStyle: { color: mutedRouteColor, weight: 3, opacity: 0.7 }, highlightStyle: { color: selectedRouteColor, weight: 6, opacity: 1.0 } });',
            '    if (!tripLayers[trip.id]) { tripLayers[trip.id] = []; }',
            '    coords.forEach(function(coord) { allCoords.push(coord); });',
            '    trip.steps.forEach(function(step) {',
            '      if (step.lat === null || step.lon === null) { return; }',
            '      var marker;',
            '      if (step.photo) {',
            '        var safeTitle = (step.title || "").replace(/\"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");',
            '        var iconHtml = "<div class=\\\"step-marker-icon\\\" data-trip-id=\\\"" + trip.id + "\\\" style=\\\"width:36px;height:36px;border-color:" + mutedRouteColor + ";\\\">" +',
            '          "<img src=\\\"" + step.photo + "\\\" alt=\\\"" + safeTitle + "\\\"/>" +',
            '          "</div>";',
            '        var stepIcon = L.divIcon({ html: iconHtml, className: "step-marker-divicon", iconSize: [36, 36], iconAnchor: [18, 18], popupAnchor: [0, -18] });',
            '        marker = L.marker([step.lat, step.lon], { icon: stepIcon });',
            '      } else {',
            '        marker = L.circleMarker([step.lat, step.lon], { radius: 6, color: mutedRouteColor, fillColor: mutedRouteColor, fillOpacity: 0.75, weight: 1 });',
            '      }',
            '      var popup = "<strong>" + step.title + "</strong><br/>" +',
            '        (trip.name ? "<em>" + trip.name + "</em><br/>" : "") +',
            '        (step.date ? step.date + "<br/>" : "") +',
            '        (step.location_name ? step.location_name + "<br/>" : "");',
            '      marker.bindPopup(popup);',
            '      marker.on("click", function(){ selectTrip(trip.id, true); });',
            '      marker.addTo(map);',
            '      if (marker.bringToFront) { marker.bringToFront(); }',
            '      tripLayers[trip.id].push({ marker: marker, photo: !!step.photo });',
            '      markers.push(marker);',
            '    });',
            '  });',
            '  if (allCoords.length > 0) { var bounds = L.latLngBounds(allCoords); map.fitBounds(bounds.pad(0.1)); }',
            '}',
            'function resetTripMarkers() {',
            '  Object.keys(tripLayers).forEach(function(tripId) {',
            '    tripLayers[tripId].forEach(function(item) {',
            '      if (!item || !item.marker) { return; }',
            '      if (item.photo && item.marker.getElement) {',
            '        var el = item.marker.getElement();',
            '        if (el) {',
            '          var iconEl = el.querySelector(\'.step-marker-icon\');',
            '          if (iconEl) { iconEl.style.borderColor = mutedRouteColor; }',
            '        }',
            '      } else if (item.marker.setStyle) {',
            '        item.marker.setStyle({ color: mutedRouteColor, fillColor: mutedRouteColor, fillOpacity: 0.75, weight: 1 });',
            '      }',
            '    });',
            '  });',
            '}',
            'function setMapStyle(style) {',
            '  if (!mapStyleLayers[style] || style === currentMapStyle) { return; }',
            '  if (mapStyleLayers[currentMapStyle]) { map.removeLayer(mapStyleLayers[currentMapStyle]); }',
            '  currentMapStyle = style;',
            '  map.addLayer(mapStyleLayers[currentMapStyle]);',
            '  map.invalidateSize();',
            '  document.querySelectorAll(".style-option").forEach(function(btn){ btn.classList.toggle("active", btn.dataset.style === currentMapStyle); });',
            '}',
        ] + _shared_step_media_carousel_js() + [
            'function toggleSettingsPanel(open) {',
            '  var panel = document.getElementById("map-settings-panel");',
            '  if (!panel) { return; }',
            '  panel.classList.toggle("open", open === undefined ? !panel.classList.contains("open") : open);',
            '}',
            'function updateSelectedTripDisplay() {',
            '  document.querySelectorAll(".trip-item").forEach(function(item){',
            '    item.classList.toggle("active", item.dataset.tripId === String(selectedTripId));',
            '  });',
            '  var mapPanel = document.querySelector(".map-panel");',
            '  if (mapPanel) { mapPanel.classList.toggle("no-selection", selectedTripId === null); }',
            '  var title = document.getElementById("selected-trip-title");',
            '  var meta = document.getElementById("selected-trip-meta");',
            '  var stepList = document.getElementById("selected-step-list");',
            '  if (selectedTripId === null) {',
            '    title.textContent = "Select a trip to inspect details";',
            '    meta.innerHTML = "";',
            '    stepList.innerHTML = "";',
            '    return;',
            '  }',
            '  var trip = trips.find(function(t){ return t.id === selectedTripId; });',
            '  if (!trip) { return; }',
            '  title.textContent = trip.name;',
            '  meta.innerHTML = \'<div class="trip-banner"><span class="badge">\' + (trip.date_range || "No dates") + \'</span>\' +',
            '    \'<span class="badge">\' + trip.total_km + " km</span>" +',
            '    \'<span class="badge">\' + trip.steps.length + " steps</span></div>";',
            '  if (!document.getElementById("fullscreen-detail-btn")) {',
            '    var detailActions = document.createElement("div");',
            '    detailActions.className = "detail-actions";',
            '    var btn = document.createElement("button");',
            '    btn.id = "fullscreen-detail-btn";',
            '    btn.className = "fullscreen-toggle";',
            '    btn.setAttribute("aria-label", "Toggle detail fullscreen");',
            '    btn.title = "Toggle detail fullscreen";',
            '    btn.innerHTML = \'<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M4 4h6V2H2v8h2V4zm14 0h4v4h2V2h-8v2h2zm0 16h-2v2h8v-8h-2v6h-6zm-14 0v-6H2v8h8v-2H4z" fill="currentColor"/></svg>\';',
            '    btn.addEventListener("click", function(evt){ evt.stopPropagation(); setDetailFullscreen(!document.body.classList.contains("detail-fullscreen")); });',
            '    detailActions.appendChild(btn);',
            '    meta.parentNode.insertBefore(detailActions, meta.nextSibling);',
            '  }',
            '  stepList.innerHTML = "";',
            '  trip.steps.forEach(function(step, index) {',
            '    var row = document.createElement("div"); row.className = "step-row";',
            '    row.innerHTML = \'<div class="step-title">\' + (index + 1) + ". " + step.title + \'</div>\' +',
            '      \'<div class="step-meta">\' + (step.date || "") + (step.location_name ? " • " + step.location_name : "") + \'</div>\' +',
            '      (step.location_detail ? \'<div class="step-location-detail">\' + step.location_detail + \'</div>\' : \'\');',
            '    if (step.description) {',
            '      row.innerHTML += \'<div class="step-desc">\' + step.description + \'</div>\';',
            '    }',
            '    var carousel = createStepMediaCarousel(step);',
            '    if (carousel) {',
            '      row.appendChild(carousel);',
            '    }',
            '    if (!carousel && step.videos && step.videos.length) {',
            '      var videoList = document.createElement("div");',
            '      videoList.className = "step-media video-list";',
            '      step.videos.forEach(function(src, videoIndex) {',
            '        var link = document.createElement("a");',
            '        link.className = "video-link";',
            '        link.href = src;',
            '        link.target = "_blank";',
            '        link.rel = "noopener noreferrer";',
            '        link.textContent = "Video " + (videoIndex + 1);',
            '        videoList.appendChild(link);',
            '      });',
            '      row.appendChild(videoList);',
            '    }',
            '    row.addEventListener("click", function(){',
            '      if (step.lat !== null && step.lon !== null) { map.setView([step.lat, step.lon], 10); }',
            '    });',
            '    stepList.appendChild(row);',
            '  });',
            '}',
            'function clearTripHighlight() {',
            '  polylines.forEach(function(entry){ if (entry.normalStyle) { entry.layer.setStyle(entry.normalStyle); } else { entry.layer.setStyle({ color: mutedRouteColor, weight: 3, opacity: 0.7 }); } });',
            '  resetTripMarkers();',
            '}',
            'function selectTrip(tripId, keepMap=false) {',
            '  selectedTripId = tripId;',
            '  updateSelectedTripDisplay();',
            '  clearTripHighlight();',
            '  var entry = polylines.find(function(entry){ return entry.tripId === tripId; });',
            '  if (entry) { entry.layer.setStyle(entry.highlightStyle || { color: selectedRouteColor, weight: 6, opacity: 1.0 }); if (entry.layer.bringToFront) { entry.layer.bringToFront(); } if (!keepMap) { map.fitBounds(entry.layer.getBounds().pad(0.12)); } }',
            '  if (tripLayers[tripId]) { tripLayers[tripId].forEach(function(item){ if (!item || !item.marker) { return; } if (item.photo && item.marker.getElement) { var el = item.marker.getElement(); if (el) { var iconEl = el.querySelector(\'.step-marker-icon\'); if (iconEl) { iconEl.style.borderColor = selectedRouteColor; } } } else if (item.marker.setStyle) { item.marker.setStyle({ color: selectedRouteColor, fillColor: selectedRouteColor, fillOpacity: 1.0, weight: 2 }); } if (item.marker.bringToFront) { item.marker.bringToFront(); } }); }',
            '  var trip = trips.find(function(t){ return t.id === tripId; });',
            '  if (trip) {',
            '    updateSelectedTripDisplay();',
            '    if (!keepMap && trip.steps.length) {',
            '      var coords = trip.steps.filter(function(step){ return step.lat !== null && step.lon !== null; }).map(function(step){ return [step.lat, step.lon]; });',
            '      if (coords.length) { map.fitBounds(L.latLngBounds(coords).pad(0.12)); }',
            '    }',
            '  }',
            '  updateSelectedTripDisplay();',
            '}',
            'function showAllTrips() {',
            '  selectedTripId = null;',
            '  updateSelectedTripDisplay();',
            '  clearTripHighlight();',
            '  var allCoords = [];',
            '  trips.forEach(function(trip){',
            '    trip.steps.forEach(function(step){ if (step.lat !== null && step.lon !== null) { allCoords.push([step.lat, step.lon]); }});',
            '  });',
            '  if (allCoords.length) { map.fitBounds(L.latLngBounds(allCoords).pad(0.1)); }',
            '}',
            'document.getElementById("show-all").addEventListener("click", function(){ showAllTrips(); });',
            'document.getElementById("show-selected").addEventListener("click", function(){ if (trips.length) { selectTrip(trips[0].id); } });',
            'document.getElementById("fit-map").addEventListener("click", function(){ showAllTrips(); });',
            'document.getElementById("map-settings-toggle").addEventListener("click", function(evt){ evt.stopPropagation(); toggleSettingsPanel(); });',
            'document.getElementById("map-settings-panel").addEventListener("click", function(evt){ evt.stopPropagation(); });',
            'document.addEventListener("click", function(){ toggleSettingsPanel(false); });',
            'document.querySelectorAll(".style-option").forEach(function(btn){ btn.addEventListener("click", function(){ setMapStyle(this.dataset.style); }); });',
            'function setDetailFullscreen(enabled) {',
            '  document.body.classList.toggle("detail-fullscreen", enabled);',
            '  var btn = document.getElementById("fullscreen-detail-btn");',
            '  if (btn) { btn.classList.toggle("active", enabled); }',
            '  if (detailResize) { detailResize.setOrientation(enabled ? "horizontal" : "vertical"); }',
            '  if (!enabled) {',
            '    var detailsPanel = document.querySelector(".details-panel");',
            '    var mapWrapEl = document.querySelector(".map-wrap");',
            '    if (detailsPanel) { detailsPanel.style.width = ""; detailsPanel.style.height = ""; }',
            '    if (mapWrapEl) { mapWrapEl.style.width = ""; mapWrapEl.style.height = ""; }',
            '  }',
            '  if (map && map.invalidateSize) { setTimeout(function(){ map.invalidateSize(); }, 200); }',
            '}',
            'function createDragResize(handleEl, firstEl, secondEl, orientation) {',
            '  var mode = orientation || "horizontal";',
            '  var isDragging = false;',
            '  var lastClientX = 0;',
            '  var lastClientY = 0;',
            '  function setOrientation(newMode) {',
            '    mode = newMode;',
            '    if (mode === "horizontal") { handleEl.style.cursor = "col-resize"; } else { handleEl.style.cursor = "row-resize"; }',
            '  }',
            '  handleEl.addEventListener("mousedown", function(evt){',
            '    isDragging = true;',
            '    lastClientX = evt.clientX;',
            '    lastClientY = evt.clientY;',
            '    document.body.style.cursor = mode === "horizontal" ? "col-resize" : "row-resize";',
            '    document.body.style.userSelect = "none";',
            '    evt.preventDefault();',
            '  });',
            '  document.addEventListener("mousemove", function(evt){',
            '    if (!isDragging) return;',
            '    var dx = evt.clientX - lastClientX;',
            '    var dy = evt.clientY - lastClientY;',
            '    lastClientX = evt.clientX;',
            '    lastClientY = evt.clientY;',
            '    if (mode === "horizontal") {',
            '      var newFirst = Math.max(250, firstEl.offsetWidth + dx);',
            '      var newSecond = Math.max(250, secondEl.offsetWidth - dx);',
            '      var total = newFirst + newSecond + handleEl.offsetWidth;',
            '      var avail = window.innerWidth - 20;',
            '      if (total > avail) { var overflow = total - avail; if (dx > 0) { newFirst -= overflow; } else { newSecond -= overflow; } }',
            '      if (newFirst < 250 || newSecond < 250) return;',
            '      firstEl.style.width = newFirst + "px";',
            '      secondEl.style.width = newSecond + "px";',
            '      if (map && map.invalidateSize) { map.invalidateSize(); }',
            '    } else {',
            '      var newFirst = Math.max(220, firstEl.offsetHeight + dy);',
            '      var newSecond = Math.max(220, secondEl.offsetHeight - dy);',
            '      var total = newFirst + newSecond + handleEl.offsetHeight;',
            '      var avail = window.innerHeight - 20;',
            '      if (total > avail) { var overflow = total - avail; if (dy > 0) { newFirst -= overflow; } else { newSecond -= overflow; } }',
            '      if (newFirst < 220 || newSecond < 220) return;',
            '      var parentGrid = firstEl.parentElement === secondEl.parentElement ? firstEl.parentElement : null;',
            '      if (parentGrid && window.getComputedStyle(parentGrid).display === "grid") {',
            '        parentGrid.style.gridTemplateRows = newFirst + "px " + handleEl.offsetHeight + "px " + newSecond + "px";',
            '      } else {',
            '        firstEl.style.height = newFirst + "px";',
            '        secondEl.style.height = newSecond + "px";',
            '      }',
            '      if (map && map.invalidateSize) { map.invalidateSize(); }',
            '    }',
            '  });',
            '  document.addEventListener("mouseup", function(){',
            '    if (isDragging) { isDragging = false; document.body.style.cursor = ""; document.body.style.userSelect = ""; if (map && map.invalidateSize) { map.invalidateSize(); } }',
            '  });',
            '  return { setOrientation: setOrientation };',
            '}',
            'function initPage() {',
            '  buildTripList();',
            '  try {',
            '    buildMap();',
            '    setMapStyle(currentMapStyle);',
            '  } catch (err) {',
            '    console.warn("Combined map failed to initialize", err);',
            '  }',
            '  updateSelectedTripDisplay();',
            '  var mainResizeHandle = document.getElementById("main-resize-handle");',
            '  var detailResizeHandle = document.getElementById("detail-resize-handle");',
            '  var tripListEl = document.querySelector(".trip-list");',
            '  var mapPanelEl = document.querySelector(".map-panel");',
            '  var detailsPanel = document.querySelector(".details-panel");',
            '  var mapWrapEl = document.querySelector(".map-wrap");',
            '  if (mainResizeHandle && tripListEl && mapPanelEl) { mainResize = createDragResize(mainResizeHandle, tripListEl, mapPanelEl, "horizontal"); }',
            '  if (detailResizeHandle && mapWrapEl && detailsPanel) { detailResize = createDragResize(detailResizeHandle, mapWrapEl, detailsPanel, "vertical"); }',
            '  document.body.addEventListener("keyup", function(evt){ if (evt.key === "Escape" && document.body.classList.contains("detail-fullscreen")) { setDetailFullscreen(false); } });',
            '}',
            'initPage();',
            'window.addEventListener("resize", function(){ map.invalidateSize(); });',
            '</script>',
            '</body>',
            '</html>'
        ]
        return "\n".join(html_parts)

    def build(self):
        html_text = self._build_html()
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(html_text, encoding='utf-8')
            return True
        except Exception as e:
            print(self.cli_lang.t("render.error", error=e))
            return False


class HtmlPDFBuilder:
    """Builds the PDF document using HTML/CSS rendered by Playwright (Chromium)."""

    def __init__(self, output_path: Path, trip_parser: TripParser, map_generator: MapGenerator, config: dict = None, language_manager: LanguageManager = None, cli_language_manager: LanguageManager = None, progress_callback=None):
        self.output_path = Path(output_path)
        self.trip_parser = trip_parser
        self.map_generator = map_generator
        self.config = config or {}
        # language_manager is used for PDF content; cli_language_manager is used for console messages
        self.lang = language_manager or get_default_language_manager()
        self.cli_lang = cli_language_manager or get_default_language_manager()

        # Layout options
        # renamed from max_photos_per_step
        self.photos_before_page_break = int(self.config.get("photos_before_page_break", self.config.get("max_photos_per_step", 6)))
        self.fill_page_with_photos = bool(self.config.get("fill_page_with_photos", True))
        self.min_photos_per_step = int(self.config.get("min_photos_per_step", self.photos_before_page_break))
        self.max_photos_per_page = int(self.config.get("max_photos_per_page", 0))  # 0 = no explicit limit
        self.photo_wall_fill_limit = int(self.config.get("photo_wall_fill_limit", max(self.min_photos_per_step * 2, self.min_photos_per_step)))
        self.appendix_show_undisplayed_media = bool(self.config.get("appendix_show_undisplayed_media", True))
        self.photo_max_width = int(self.config.get("html_photo_max_width", 1200))
        self.photo_masonry_columns = int(self.config.get("html_photo_masonry_columns", self.config.get("photo_wall_columns", 3)))
        self.photo_masonry_gap = int(self.config.get("html_photo_masonry_gap", self.config.get("photo_wall_gap", 8)))
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
        # Optional callback(progress_current:int, progress_total:int)
        self.progress_callback = progress_callback

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

    def _split_step_photos(self, photo_paths: List[Path]) -> Tuple[List[Path], List[Path]]:
        """Return (photos_to_show, extra_photos) for a step.

        Behavior:
                - Base amount per step is photos_before_page_break.
                - If fill_page_with_photos=true, add a limited number of photos intended
                    to fill the current page, then move the rest to extra_photos.
                - If fill_page_with_photos=false, show exactly the base amount.
                - If total photos are below base amount, show all photos.
        """
        if not photo_paths:
            return [], []

        count = len(photo_paths)
        if count == 0:
            return [], []

        # threshold: minimal displayed images before considering page break
        threshold = int(self.config.get("photos_before_page_break", self.photos_before_page_break))
        if threshold <= 0:
            threshold = 1

        if count <= threshold:
            return list(photo_paths), []

        if self.fill_page_with_photos:
            fill_limit = int(self.config.get("photo_wall_fill_limit", self.photo_wall_fill_limit))
            if fill_limit <= 0:
                fill_limit = threshold

            # "photo_wall_fill_limit" dient als absolutes Maximum
            hard_cap = threshold
            if fill_limit > 0:
                hard_cap = min(hard_cap, fill_limit)

            target = min(count, hard_cap)
            photos_to_show = list(photo_paths[:target])
            extra_photos = list(photo_paths[target:])
            return photos_to_show, extra_photos

        # strict threshold behavior: only threshold images, rest extra
        photos_to_show = list(photo_paths[:threshold])
        extra_photos = list(photo_paths[threshold:])
        return photos_to_show, extra_photos

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
                        img = img.resize((self.photo_max_width, new_h), RESAMPLING_LANCZOS)
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

    def _should_open_pdf(self) -> bool:
        renderer_mode = str(self.config.get("renderer_mode", self.config.get("renderer", "both"))).strip().lower()
        if renderer_mode not in ("pdf", "html", "both"):
            renderer_mode = "both"

        if renderer_mode == "html":
            return False

        try:
            return bool(self.config.get("open_pdf_after_render", True))
        except Exception:
            return True

    def _build_photo_grid_html(self, photo_paths: List[Path]) -> str:
        if not photo_paths:
            return ""
        items = []

        def _photo_url(p):
            if isinstance(p, str) and (p.startswith("http://") or p.startswith("https://") or p.startswith("file:")):
                return p
            try:
                return self._image_file_to_data_url(Path(p))
            except Exception:
                return None

        workers = max(1, min(int(self._photo_workers), len(photo_paths)))
        if workers > 1:
            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    urls = list(executor.map(_photo_url, photo_paths))
                for url in urls:
                    if url:
                        items.append(f"<img src=\"{url}\"/>")
            except Exception:
                for p in photo_paths:
                    try:
                        url = _photo_url(p)
                        if url:
                            items.append(f"<img src=\"{url}\"/>")
                    except Exception:
                        continue
        else:
            for p in photo_paths:
                try:
                    url = _photo_url(p)
                    if url:
                        items.append(f"<img src=\"{url}\"/>")
                except Exception:
                    continue
        if items:
            return f"<div class=\"photo-grid\">{''.join(items)}</div>"
        return ""

    def _build_video_grid_html(self, video_paths: List[str]) -> str:
        if not video_paths:
            return ""
        videos = []
        for v in video_paths:
            try:
                if isinstance(v, str) and (v.startswith("http://") or v.startswith("https://") or v.startswith("file:")):
                    video_url = v
                else:
                    video_url = Path(v).resolve().as_uri()
                videos.append(
                    "<div class=\"video-box\"><video controls preload=\"metadata\" style=\"width:100%; max-height:360px;\">"
                    f"<source src=\"{self._escape(video_url)}\" />"
                    "</video></div>"
                )
            except Exception:
                continue
        if videos:
            return "".join(videos)
        return ""

    def _build_html(self) -> str:
        trip_name = self.trip_parser.get_trip_name()
        start_date, end_date = self.trip_parser.get_trip_dates()
        total_km = self.trip_parser.get_total_km()
        step_count = len(self.trip_parser.steps)
        # Count overview map + final PDF render as additional steps for progress reporting
        # (overview = step 1, steps = 2..N+1, final PDF render = last step)
        total_steps = step_count + 2
        # store on instance so build() can access it later
        try:
            self._total_steps = total_steps
        except Exception:
            pass

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
                    # Report overview as first step
                    try:
                        if self.progress_callback:
                            self.progress_callback(1, total_steps, trip_name)
                    except Exception:
                        pass
                    print(self.cli_lang.t("render.rendering_title_overview"))
                    t0 = time.perf_counter()
                    mg = self._get_thread_map_generator()
                    data = mg.generate_overview_map(self.trip_parser)
                    dt = time.perf_counter() - t0
                    print(self.cli_lang.t("render.overview_done", seconds=dt))
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
                # Report overview as first step
                try:
                    if self.progress_callback:
                        self.progress_callback(1, total_steps, trip_name)
                except Exception:
                    pass
                print(self.cli_lang.t("render.rendering_title_overview"))
                t0 = time.perf_counter()
                map_bytes = self.map_generator.generate_overview_map(self.trip_parser)
                dt = time.perf_counter() - t0
                print(self.cli_lang.t("render.overview_done", seconds=dt))
                if map_bytes:
                    overview_img = f"<img class=\"map\" src=\"{self._map_bytes_to_data_url(map_bytes)}\"/>"
            except Exception:
                overview_img = ""

        # Use masonry-style column settings for photo layout
        photo_wall_gap = self.photo_masonry_gap
        photo_wall_columns = self.photo_masonry_columns

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
            ".step { page-break-inside: auto; }",
            ".step-intro { page-break-inside: avoid; page-break-after: avoid; }",
            ".photo-grid { page-break-inside: auto; }",
            ".step-desc-rest { page-break-inside: auto; margin-top: 2mm; }",
            ".step-title { color: #1A5F7A; font-size: 18pt; margin: 6mm 0 2mm; }",
            ".step-meta { color: #666; font-size: 10pt; margin: 0 0 4mm; }",
            ".step-desc { font-size: 11pt; line-height: 1.35; margin: 0 0 4mm; }",
            ".step-list { margin: 0 0 4mm 6mm; }",
            ".photo-grid { column-count: %d; column-gap: %dpx; width: 100%%; margin: 0 0 4mm; page-break-inside: auto; }" % (self.photo_masonry_columns, self.photo_masonry_gap),
            ".photo-grid img { width: 100%%; height: auto; display: block; margin-bottom: %dpx; break-inside: avoid; page-break-inside: auto; }" % (self.photo_masonry_gap),
            ".appendix-title { color: #1A5F7A; font-size: 20pt; margin: 4mm 0 2mm; }",
            ".appendix-subtitle { color: #666; font-size: 10pt; margin: 0 0 4mm; }",
            ".appendix-step-title { color: #1A5F7A; font-size: 14pt; margin: 6mm 0 2mm; }",
            ".video-header { margin-top: 3mm; font-weight: 600; }",
            ".video-link { display: block; color: #0066CC; text-decoration: none; font-size: 10pt; }",
            ".video-box { margin-top: 6px; }",
            ".video-box video { width: 100%; max-height: 360px; border-radius: 4px; border: 1px solid #ccc; }",
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

            print(self.cli_lang.t("render.rendering_step", current=step_number, total=step_count, name=display_name))
            try:
                if self.progress_callback:
                    # Inform caller about step progress (current step includes overview as first step)
                    try:
                        self.progress_callback(step_number + 1, total_steps, trip_name)
                    except Exception:
                        pass
            except Exception:
                pass

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
            desc_lines = description.splitlines()
            desc_intro_text = "\n".join(desc_lines[:10])
            desc_rest_text = "\n".join(desc_lines[10:]) if len(desc_lines) > 10 else ""

            desc_intro_html = self._build_description_html(desc_intro_text)
            desc_rest_html = self._build_description_html(desc_rest_text) if desc_rest_text else ""

            # Photo grid
            photos_to_show = [Path(p) if not (isinstance(p, str) and (p.startswith('http://') or p.startswith('https://') or p.startswith('file:'))) else p for p in photos]
            photo_html = self._build_photo_grid_html(photos_to_show)

            # Video blocks for this step
            video_html = self._build_video_grid_html(videos)

            # Add all media to step (no split hiding) and in appendix only if extra exists due explicit config.
            extra_photos = []
            if self.appendix_show_undisplayed_media and extra_photos:
                appendix_items.append({
                    "step_number": step_number,
                    "display_name": display_name or f"{self.lang.t('pdf.step_label')} {step_number}",
                    "extra_photos": extra_photos,
                    "videos": [],
                })

            step_section = [
                "<div class=\"step\">",
                "<div class=\"step-intro\">",
                f"<div class=\"step-title\">{self._escape(title_text)}</div>",
                f"<div class=\"step-meta\">{self._escape(meta_text)}</div>",
                step_map_html,
                desc_intro_html,
                "</div>",
            ]

            if desc_rest_html:
                step_section.append(f"<div class=\"step-desc-rest\">{desc_rest_html}</div>")

            step_section.extend([photo_html, video_html, "</div>"])
            html_parts.extend(step_section)

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
            raise RuntimeError(self.cli_lang.t("render.playwright_missing"))

        t0 = time.perf_counter()
        html_doc = self._build_html()
        t1 = time.perf_counter()
        print(self.cli_lang.t("render.html_build_done", seconds=t1 - t0))

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
                        print(self.cli_lang.t("render.pdf_render_done", seconds=time.perf_counter() - t_pdf))
                        browser.close()
                        last_error = None
                        # Notify caller that PDF file has been created (final progress step)
                        try:
                            if self.progress_callback:
                                try:
                                    steps = getattr(self, '_total_steps', None) or (len(self.trip_parser.steps) + 2)
                                    self.progress_callback(steps, steps, self.trip_parser.get_trip_name())
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        break
                    except Exception as e:
                        last_error = e
                        # detect missing browser executable or first‑time install message
                        msg = str(e).lower()
                        if ("doesn't exist" in msg or "executable" in msg or "just installed" in msg):
                            if attempt == 1:
                                print(self.cli_lang.t("render.playwright_browsers_missing"))
                                try:
                                    # install all browsers to satisfy Playwright
                                    subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)
                                    print(self.cli_lang.t("render.playwright_browsers_installed"))
                                    # retry immediately
                                    continue
                                except Exception:
                                    print(self.cli_lang.t("render.playwright_browsers_install_failed"))
                        try:
                            if browser:
                                browser.close()
                        except Exception:
                            pass
                        # report the error to user
                        print(self.cli_lang.t("render.error_render", error=e))
                        if attempt < 2:
                            print(self.cli_lang.t("render.html_render_retry"))
                        time.sleep(1)
                if last_error is not None:
                    raise last_error
        finally:
            # Clean up temp HTML file
            try:
                temp_html.unlink(missing_ok=True)
            except Exception:
                pass

        return True


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
    """Find all trip folders in the Polarsteps Data (BSPData) directory and sort by start date (oldest first)."""
    trips = []

    # legacy structure: BSPData/<date>/trip/<trip-folder>
    # and flexible recursive support: BSPData/**/<trip-folder>(with trip.json)
    if not bsp_data_folder.exists() or not bsp_data_folder.is_dir():
        return []

    for folder in sorted(bsp_data_folder.rglob('trip.json')):
        trip_folder = folder.parent
        if trip_folder.is_dir():
            trips.append(trip_folder)

    # If no trips found under recursive scan (including date-folder layout), fallback to old non-recursive path behavior
    if not trips:
        for date_folder in sorted(bsp_data_folder.iterdir()):
            if not date_folder.is_dir():
                continue

            trip_folder = date_folder / "trip"
            if not trip_folder.exists():
                continue

            for trip in sorted(trip_folder.iterdir()):
                if trip.is_dir() and (trip / "trip.json").exists():
                    trips.append(trip)

    # deduplicate in case same trip found multiple times via recursive rglob
    unique_trips = []
    seen = set()
    for t in trips:
        try:
            path_str = str(t.resolve())
        except Exception:
            path_str = str(t)
        if path_str not in seen:
            seen.add(path_str)
            unique_trips.append(t)

    # Sort trips by start_date from trip.json (oldest first)
    unique_trips.sort(key=get_trip_start_date)

    return unique_trips


def filter_trips_by_date(trips: list, year: int = None, start_date: datetime = None, end_date: datetime = None) -> list:
    """Return only trips that overlap the specified period.

    * A year filter behaves like ``start_date=YEAR-01-01, end_date=YEAR-12-31``.
    * A date range will include trips whose date interval intersects the range.
    * Trips with no recorded start/end dates are ignored.
    * Trips that have not begun yet (start date in the future) are also skipped.

    Unlike the old implementation, this inspects the trip's actual start/end
    timestamps (via ``TripParser.get_trip_dates``) rather than only the start
    date, so a selection inside a single trip period will still return that trip.
    """
    # quick exit when no restrictions
    if not year and not start_date and not end_date:
        # still strip out any trips that have a start date in the future
        today = datetime.now().date()
        remaining = []
        for trip in trips:
            try:
                tp = TripParser(trip)
                tp.load()
                s_dt, _ = tp.get_trip_dates()
                if s_dt and s_dt.date() > today:
                    continue
            except Exception:
                pass
            remaining.append(trip)
        return remaining

    # if year was requested but no explicit range passed, treat it as a full-year
    # interval; this mirrors the behaviour added to compute_aggregate_stats.
    if year and not (start_date or end_date):
        try:
            start_date = datetime(int(year), 1, 1)
            end_date = datetime(int(year), 12, 31, 23, 59, 59)
        except Exception:
            start_date = None
            end_date = None

    def _parse_any_dt(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(int(value))
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value)
                except Exception:
                    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
                        try:
                            return datetime.strptime(value, fmt)
                        except Exception:
                            pass
        except Exception:
            pass
        return None

    filtered = []
    for trip in trips:
        try:
            tp = TripParser(trip)
            tp.load()
            s_dt, e_dt = tp.get_trip_dates()
        except Exception:
            continue

        if not s_dt and not e_dt:
            # no useful dates
            continue

        # ignore trips that are entirely in the future
        try:
            today = datetime.now().date()
            if s_dt and s_dt.date() > today:
                continue
        except Exception:
            pass

        # check overlap with range if provided
        if start_date or end_date:
            s = s_dt if s_dt else None
            e = e_dt if e_dt else None
            overlaps = True
            if start_date and e and e < start_date:
                overlaps = False
            if end_date and s and s > end_date:
                overlaps = False

            # fallback for incomplete/rough trip bounds: include trip if any step
            # timestamp falls inside the requested range
            if not overlaps:
                step_overlap = False
                for step in tp.steps or []:
                    data = step.get('data', {}) or {}
                    for key in ('start_time', 'startDate', 'start_date', 'time', 'date', 'timestamp'):
                        dt = _parse_any_dt(data.get(key))
                        if not dt:
                            continue
                        if start_date and dt < start_date:
                            continue
                        if end_date and dt > end_date:
                            continue
                        step_overlap = True
                        break
                    if step_overlap:
                        break
                if not step_overlap:
                    continue

            filtered.append(trip)
        else:
            # no explicit range (should not happen because we early exit)
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
        try:
            # Additional quick stats help
            print(lang.t("cli.stats_help"))
        except Exception:
            pass
        print("Additional commands:")
        print("  html [selection]       Create combined overview HTML for selected trips")
        print("  h [selection]          Shortcut for html")


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
            
            # Stats command
            if cmd_lower.startswith('stats') or cmd_lower.startswith('s ') or cmd_lower == 's':
                # Reuse render selection parsing by prefixing with 'r'
                rest = cmd[5:].strip() if cmd_lower.startswith('stats') else cmd[1:].strip()
                # Support verbose flag '-v' in prompt mode
                tokens = rest.split()
                verbose = False
                filtered_tokens = []
                for t in tokens:
                    if t in ('-v', '--verbose', '-V'):
                        verbose = True
                    else:
                        filtered_tokens.append(t)
                rest = ' '.join(filtered_tokens)
                parse_cmd = 'r ' + rest if rest else 'r'
                result = parse_render_command(parse_cmd, trips, cache_manager, lang=lang)

                if not result['valid']:
                    # If the only error is missing selection/mode, offer to run stats for ALL trips
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
                            parse_cmd = 'r -a'
                            result = parse_render_command(parse_cmd, trips, cache_manager, lang=lang)
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

                trips_to_stat = result['trips']
                # Print a concise header depending on filter
                if result.get('year'):
                    print(f"Reise dieses Jahr: {result.get('year')}")
                elif result.get('start_date') and result.get('end_date'):
                    print(f"Reise im Zeitraum: {result.get('start_date').date()} bis {result.get('end_date').date()}")
                else:
                    print("Reise:")

                for i, trip in enumerate(trips_to_stat, 1):
                    try:
                        with open(trip / "trip.json", "r", encoding="utf-8") as f:
                            trip_data = json.load(f)
                        name = trip_data.get("name", trip.name)
                        print(f"  [{i}] {name}")
                    except Exception:
                        print(f"  [{i}] {trip.name}")

               

                # compute with progress reporting
                stats_map_gen = create_map_generator_from_config(config=config, lang=lang, purpose="stats")
                sg = StatisticsGenerator(map_generator=stats_map_gen, config=config)
                def _progress(idx, total, trip):
                    # Progress reporting disabled for CLI stats (quiet mode)
                    return
                # Ensure start/end period variables reflect requested year or explicit range
                period_start = None
                period_end = None
                if result.get('year'):
                    y = int(result.get('year'))
                    period_start = datetime(y, 1, 1)
                    period_end = datetime(y, 12, 31)
                elif result.get('start_date') and result.get('end_date'):
                    period_start = result.get('start_date')
                    period_end = result.get('end_date')

                agg = sg.compute_aggregate_stats(trips_to_stat, year=result.get('year'), start_date=period_start, end_date=period_end, progress_callback=_progress, verbose=verbose)

                # Determine period for ratio calculation
                if period_start and period_end:
                    ps_date = period_start.date()
                    pe_date = period_end.date()
                else:
                    # fall back to aggregate's period if available
                    ps_iso = agg.get('period_start')
                    pe_iso = agg.get('period_end')
                    ps_date = date.fromisoformat(ps_iso) if ps_iso else None
                    pe_date = date.fromisoformat(pe_iso) if pe_iso else None

                # Compute overall period days and non-travel days ratio when possible
                period_total_days = None
                non_travel_days = None
                travel_pct = None
                if ps_date and pe_date:
                    period_total_days = (pe_date - ps_date).days + 1
                    travel_days = agg.get('total_travel_days', 0)
                    non_travel_days = max(0, period_total_days - travel_days)
                    travel_pct = (travel_days / period_total_days * 100) if period_total_days else None

                # print final summary to console (German)
                print("--- Statistik Ergebnis ---")
                # (headline shown previously) do not repeat the year header here
                print(f"Trips: {agg.get('trip_count',0)}")
                print(f"Steps gesamt: {agg.get('total_steps',0)}")
                print(f"Reisetage gesamt: {agg.get('total_travel_days',0)}")
                if period_total_days is not None:
                    print(f"Reise/Non-Reise: {agg.get('total_travel_days',0)} Reisetage • {non_travel_days} Nicht-Reisetage ({travel_pct:.1f}% Reise)")
                print(f"Gereiste km: {agg.get('total_km',0)}")
                print(f"Fotos: {agg.get('total_photos',0)}, Videos: {agg.get('total_videos',0)}")
                print(f"Länder bereist: {agg.get('visited_countries_count',0)} ({agg.get('visited_countries_percent',0.0)}% der Länder der Welt)")
                print("Länder (Tage):")
                display_countries = sg.localize_country_counts(agg.get('countries', {}), language_code=lang.language_code)
                for c, cnt in sorted(display_countries.items(), key=lambda x: -x[1]):
                    pct = (cnt / max(1, agg.get('total_travel_days',1))) * 100 if agg.get('total_travel_days') else 0
                    print(f"  {c}: {cnt} Tage ({pct:.1f}%)")
                # Continents summary
                print("")
                print(f"Kontinente bereist: {agg.get('visited_continents_count',0)} ({agg.get('visited_continents_percent',0.0)}% aller Kontinente)")
                print("Kontinente (Tage):")
                display_continents = sg.localize_continent_counts(agg.get('continents', {}), language_code=lang.language_code)
                for c, cnt in sorted(display_continents.items(), key=lambda x: -x[1]):
                    pct = (cnt / max(1, agg.get('total_travel_days',1))) * 100 if agg.get('total_travel_days') else 0
                    print(f"  {c}: {cnt} Tage ({pct:.1f}%)")
                # if verbose was requested, print per-trip breakdown
                if verbose and agg.get('per_trip'):
                    print('\n--- Per-Trip Breakdown ---')
                    for i, pt in enumerate(agg.get('per_trip', []), 1):
                        name = pt.get('name') or pt.get('path')
                        steps = pt.get('steps', 0)
                        td = pt.get('travel_days', 0)
                        km = pt.get('total_km', 0)
                        countries = pt.get('country_days', {})
                        display_countries = sg.localize_country_counts(countries, language_code=lang.language_code)
                        country_list = ', '.join([f"{c}({d})" for c, d in sorted(display_countries.items(), key=lambda x: -x[1])])
                        print(f" [{i}] {name} — Steps: {steps}, Reisetage (im Zeitraum): {td}, km: {km}")
                        if country_list:
                            print(f"      Länder: {country_list}")
                    print('--- End of per-trip breakdown ---\n')

                # offer a quick export to default TripPdfs folder
                def _default_name(suffix: str) -> str:
                    # prefer year if given, else period start-end or 'alltime'
                    label = 'alltime'
                    if result.get('year'):
                        label = str(result.get('year'))
                    else:
                        ps = agg.get('period_start')
                        pe = agg.get('period_end')
                        if ps and pe:
                            label = f"{ps}_{pe}"
                    return f"stats_{label}.{suffix}"

                # ask user whether to export JSON/map to default TripPdfs folder
                try:
                    print()  # blank line before prompt
                    quick = input('Quick export JSON+Map to default `TripPdfs` folder? [y/N]: ').strip()
                    if quick and quick.lower() in ('y', 'yes'):
                        outdir = Path('TripPdfs')
                        outdir.mkdir(parents=True, exist_ok=True)
                        json_path = outdir / _default_name('json')
                        ok = sg.export_stats_json(agg, json_path)
                        print(f"JSON export {'erfolgreich' if ok else 'fehlgeschlagen'}: {json_path}")
                        try:
                            mp = sg.generate_overview_map(trips_to_stat)
                            if mp:
                                map_path = outdir / _default_name('png')
                                with open(map_path, 'wb') as mf:
                                    mf.write(mp)
                                print(f"Map export geschrieben: {map_path}")
                            else:
                                print('No overview map generated to export.')
                        except Exception as e:
                            print(f"Map export fehlgeschlagen: {e}")
                except Exception:
                    pass

                print('Statistics completed.')
                continue

            # Combined HTML command
            if cmd_lower.startswith('html') or cmd_lower.startswith('h ') or cmd_lower == 'h':
                rest = cmd[4:].strip() if cmd_lower.startswith('html') else cmd[1:].strip()
                parse_cmd = 'r ' + rest if rest else 'r'
                result = parse_render_command(parse_cmd, trips, cache_manager, lang=lang)

                if not result['valid']:
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
                            parse_cmd = 'r -a'
                            result = parse_render_command(parse_cmd, trips, cache_manager, lang=lang)
                            if not result['valid']:
                                print(lang.t("cli.error_prefix", error=result['error']))
                                continue
                        elif lang.is_no(user_choice):
                            print(lang.t("cli.cancelled_return"))
                            continue
                        else:
                            cmd = user_choice
                            continue
                    else:
                        print(lang.t("cli.error_prefix", error=result['error']))
                        continue

                if not result['trips']:
                    print(lang.t("cli.no_trips_found"))
                    continue

                html_dir = Path(config.get('output_folder_html') or script_dir / 'TripPdfs')
                html_dir.mkdir(parents=True, exist_ok=True)
                html_name = 'combined_trips'
                if result.get('year'):
                    html_name += f"_{result['year']}"
                elif result.get('start_date') and result.get('end_date'):
                    try:
                        html_name += f"_{result['start_date'].date()}_{result['end_date'].date()}"
                    except Exception:
                        html_name += '_range'
                html_name += '.html'
                html_path = html_dir / html_name
                builder = CombinedHtmlBuilder(html_path, result['trips'], config=config, language_manager=lang, cli_language_manager=lang)
                print(lang.t('cli.combined_html_building', path=html_path))
                if builder.build():
                    print(lang.t('cli.combined_html_written', path=html_path))
                else:
                    print(lang.t('cli.combined_html_failed', path=html_path))
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
                
                # PDF language: use pdf_language if set, otherwise fall back to language
                pdf_lang_code = merged_config.get("pdf_language", "").strip()
                if not pdf_lang_code:
                    pdf_lang_code = merged_config.get("language", lang.language_code)
                render_lang = load_language_manager(pdf_lang_code, script_dir)
                merged_config["_pdf_language_manager"] = render_lang

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
                    # Use CLI language for console output; PDF language is provided in merged_config
                    if render_trip(trip, script_dir, merged_config, cache_manager, check_stop, lang=lang):
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

    def select_trip(trips: list, cache_manager: CacheManager, show_rendered: bool = True) -> Union[Path, str, None]:
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


def create_map_generator_from_config(config: dict = None, lang: LanguageManager = None, purpose: str = "render") -> MapGenerator:
    """Create and configure a MapGenerator from app config.

    purpose:
      - "render": default PDF rendering behavior
      - "stats": statistics overview map defaults (higher resolution, tighter fit, smaller bubbles)
    """
    cfg = config or {}
    mode = str(purpose or "render").strip().lower()

    map_style = str(cfg.get("map_style", "hybrid")).lower().strip()
    if map_style in ("street", "streets"):
        map_style = "road"
    if map_style in ("sat",):
        map_style = "satellite"

    label_overlay_url = None
    label_overlay_opacity = float(cfg.get("hybrid_labels_opacity", 0.7))
    if map_style == "road":
        map_url = ESRI_ROAD_URL
    elif map_style == "satellite":
        map_url = ESRI_SATELLITE_URL
    else:
        map_url = ESRI_SATELLITE_URL
        label_overlay_url = ESRI_LABELS_URL

    base_marker_thumb_size = int(cfg.get("marker_thumb_size", 40))
    marker_thumb_size = base_marker_thumb_size
    if mode == "stats":
        marker_scale = float(cfg.get("stats_map_marker_scale", 0.55))
        marker_thumb_size = max(10, int(round(base_marker_thumb_size * marker_scale)))
        marker_thumb_size = int(cfg.get("stats_map_marker_thumb_size", marker_thumb_size))

    map_gen = MapGenerator(
        marker_thumb_size=marker_thumb_size,
        url_template=map_url,
        label_overlay_url=label_overlay_url,
        label_overlay_opacity=label_overlay_opacity,
    )
    map_gen.lang = lang or get_default_language_manager()

    maps_config = cfg.get("maps", {})
    overview_config = maps_config.get("overview", {})
    step_config = maps_config.get("step", {})

    default_vertical_px_fallback = 900 if mode == "stats" else 450
    default_vertical_px = int(maps_config.get("vertical_resolution_px", default_vertical_px_fallback))
    overview_vertical_default = max(default_vertical_px, 900) if mode == "stats" else default_vertical_px
    overview_vertical_px = int(overview_config.get("vertical_resolution_px", overview_vertical_default))
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
    map_gen._pixel_scale = float(default_vertical_px) / 450.0

    overview_padding_default = 0.03 if mode == "stats" else 0.10
    map_gen.overview_padding_factor = float(overview_config.get("padding_factor", overview_padding_default))
    map_gen.overview_min_width_km = float(overview_config.get("min_width_km", 10.0))
    map_gen.overview_algorithm = str(overview_config.get("algorithm", "bbox")).lower()

    map_gen.step_padding_factor = float(step_config.get("padding_factor", map_gen.step_padding_factor))
    map_gen.step_min_width_km = float(step_config.get("min_width_km", map_gen.step_min_width_km))
    map_gen.step_max_distance_farthest_km = float(step_config.get("max_distance_farthest_steps_km", map_gen.step_max_distance_farthest_km))
    map_gen.step_cluster_distance_km = float(step_config.get("cluster_distance_km", map_gen.step_cluster_distance_km))
    map_gen.step_render_scale = float(step_config.get("render_scale", map_gen.step_render_scale))

    map_gen.debug_map = bool(cfg.get("debug_map", False))
    return map_gen


def _resolve_output_dirs(config: dict, script_dir: Path):
    try:
        base_dir = Path(config.get('output_folder')) if config.get('output_folder') else script_dir / 'TripPdfs'
    except Exception:
        base_dir = script_dir / 'TripPdfs'
    pdf_dir = Path(config.get('output_folder_pdf') or base_dir)
    html_dir = Path(config.get('output_folder_html') or base_dir)
    return pdf_dir, html_dir


def _should_open_html(config: dict) -> bool:
    try:
        return bool(config.get('open_html_after_render', True))
    except Exception:
        return True


def render_trip(trip_path: Path, script_dir: Path, config: dict, cache_manager: CacheManager, check_stop=None, lang: LanguageManager = None, progress_callback=None) -> bool:
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
        
        # Generate base output name
        trip_name_safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in parser.get_trip_name())

        # Determine renderer mode (pdf/html/both)
        renderer_mode = str(config.get("renderer_mode", config.get("renderer", "both"))).strip().lower()
        if renderer_mode not in ("pdf", "html", "both"):
            renderer_mode = "both"

        html_open_after = _should_open_html(config)
        try:
            pdf_open_after = bool(config.get("open_pdf_after_render", True))
        except Exception:
            pdf_open_after = True

        pdf_dir, html_dir = _resolve_output_dirs(config, script_dir)
        pdf_dir.mkdir(parents=True, exist_ok=True)
        html_dir.mkdir(parents=True, exist_ok=True)

        # Determine map URL + hybrid labels (only needed for PDF rendering)
        map_style = str(config.get("map_style", "hybrid")).lower().strip()
        if map_style in ("street", "streets"):
            map_style = "road"
        if map_style in ("sat",):
            map_style = "satellite"

        print(lang.t("render.map_style", style=map_style))
        map_gen = create_map_generator_from_config(config=config, lang=lang, purpose="render")

        # Use PDF-specific language if configured
        pdf_lang = config.get("_pdf_language_manager", lang)

        did_output = False
        errors = []

        if renderer_mode in ("html", "both"):
            html_output_path = html_dir / f"{trip_name_safe}.html"
            print(lang.t("render.renderer") + " (HTML)")
            try:
                html_builder = InteractiveHtmlBuilder(html_output_path, parser, config=config, language_manager=pdf_lang, cli_language_manager=lang)
                if html_builder.build():
                    did_output = True
                    print(lang.t("render.done_html", path=html_output_path) if hasattr(lang, 't') else f"HTML generated: {html_output_path}")
                    # auto-open HTML output for HTML-only and both modes (when configured)
                    if html_open_after:
                        try:
                            webbrowser.open_new_tab(html_output_path.as_uri())
                        except Exception:
                            pass
                else:
                    errors.append(f"HTML generation failed for {html_output_path}")
            except Exception as e:
                errors.append(str(e))

        if renderer_mode in ("pdf", "both"):
            output_path = pdf_dir / f"{trip_name_safe}.pdf"
            print(lang.t("render.renderer") + " (PDF)")
            try:
                pdf_builder = HtmlPDFBuilder(output_path, parser, map_gen, config=config, language_manager=pdf_lang, cli_language_manager=lang, progress_callback=progress_callback)
                if pdf_builder.build():
                    did_output = True
                    print(lang.t("render.done_pdf", path=output_path))
                    if pdf_open_after:
                        t0 = time.perf_counter()
                        try:
                            if os.name == "nt":
                                os.startfile(str(output_path))
                            elif sys.platform == "darwin":
                                subprocess.run(["open", str(output_path)], check=False)
                            else:
                                subprocess.run(["xdg-open", str(output_path)], check=False)
                            print(lang.t("render.open_pdf_done", seconds=time.perf_counter() - t0))
                        except Exception as e:
                            print(lang.t("render.open_pdf_failed", error=e))
                else:
                    errors.append(f"PDF generation failed for {output_path}")
            except Exception as e:
                errors.append(str(e))

        if did_output:
            cache_manager.mark_rendered(trip_path)
            return True

        print(lang.t("render.error", error='; '.join(errors)))
        return False
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
    
    # Separate PDF language (falls back to CLI language if not set)
    pdf_lang_code = config.get("pdf_language", "").strip()
    if pdf_lang_code:
        pdf_lang = load_language_manager(pdf_lang_code, script_dir)
    else:
        pdf_lang = lang
    config["_pdf_language_code"] = pdf_lang.language_code
    config["_pdf_language_manager"] = pdf_lang

    parser = argparse.ArgumentParser(
        description=lang.t("cli.argparse_description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=lang.t("cli.argparse_epilog"),
    )
    
    # positional argument for Polarsteps Data (BSPData) folder; optional, can also be set in config
    # allow zero or more Polarsteps Data folder paths; if omitted, fallback to config/default
    parser.add_argument('bsp_folder', nargs='*', help=lang.t("cli.argparse_bsp_help"))
    parser.add_argument('--output-folder', help=lang.t("cli.argparse_output_help"))
    parser.add_argument('--output-folder-pdf', help='Output path for PDF files (optional)')
    parser.add_argument('--output-folder-html', help='Output path for HTML files (optional)')
    parser.add_argument('--renderer-mode', choices=['pdf', 'html', 'both'], default='both', help='Renderer mode: pdf, html, or both')
    parser.add_argument('--clear-cache', action='store_true', help=lang.t("cli.argparse_clear_cache"))
    # Statistics flags
    parser.add_argument('--stats', action='store_true', help='Show statistics for trips (prints summary)')
    parser.add_argument('-y', '--year', dest='stats_year', type=int, help='Filter statistics by year (shorthand -y)')
    parser.add_argument('--from', dest='stats_from', help='Filter statistics from date YYYY-MM-DD')
    parser.add_argument('--to', dest='stats_to', help='Filter statistics to date YYYY-MM-DD')
    parser.add_argument('--unrendered', action='store_true', help='Only include unrendered trips in statistics')
    parser.add_argument('-v', '--stats-verbose', dest='stats_verbose', action='store_true', help='Show per-trip breakdown')
    parser.add_argument('--debug-countries', action='store_true', help='Show debug information for country detection')
    parser.add_argument('--stats-json', help='Write statistics JSON to file')
    parser.add_argument('--stats-map', help='Write overview map PNG to file')
    parser.add_argument('--combined-html', nargs='?', const='combined_trips.html', help='Write combined overview HTML for the selected trips or filters. If no path is given, writes TripPdfs/combined_trips.html.')
    parser.add_argument('--yes', action='store_true', help='Do not prompt for confirmation')
    # update flags
    parser.add_argument('--check-update', action='store_true', help='Check GitHub for a newer version and exit')
    parser.add_argument('--update', action='store_true', help='Download and install a newer version if available')
    parser.add_argument('--auto-update', action='store_true', help='Enable auto-update check for this run (overrides config)')

    # Support legacy style: treat first arg 'stats' like '--stats'
    if len(sys.argv) > 1 and sys.argv[1] in ('stats', 's'):
        sys.argv[1] = '--stats'

    args = parser.parse_args()

    # perform update/check actions before doing anything else
    maybe_update(script_dir, config, args)
    
# Determine Polarsteps Data folder(s) (CLI argument overrides config)
    bsp_data_folders = []
    if args.bsp_folder:
        # args.bsp_folder is a list of path strings
        for p in args.bsp_folder:
            if p:
                bsp_data_folders.append(Path(p))
    else:
        # read config; support list or single string and legacy key
        cfg_val = config.get('polarsteps_data_folder', None)
        if not cfg_val:
            cfg_val = config.get('bsp_folder', None)
        if isinstance(cfg_val, (list, tuple)):
            for p in cfg_val:
                if p:
                    bsp_data_folders.append(Path(p))
        elif cfg_val:
            bsp_data_folders.append(Path(cfg_val))
        else:
            bsp_data_folders.append(script_dir / 'BSPData')
            if not bsp_data_folders[0].exists():
                bsp_data_folders[0] = Path.cwd() / 'BSPData'

    # validate folders and collect trips
    valid_folders = []
    for bf in bsp_data_folders:
        if bf.exists():
            valid_folders.append(bf)
        else:
            print(lang.t("cli.error_bsp_not_found", path=bf))
    if not valid_folders:
        return
    # combine trips from all specified folders
    all_trips = []
    for bf in valid_folders:
        try:
            all_trips.extend(find_trips(bf))
        except Exception as e:
            print(lang.t("cli.error_list_trips", error=e))
            return
    # remove duplicates
    seen = set()
    trips = []
    for t in all_trips:
        s = str(t)
        if s not in seen:
            seen.add(s)
            trips.append(t)

    if not trips:
        print(lang.t("cli.no_trips_in_bsp"))
        sys.exit(1)
    
    # Determine output folders for generated content (config or CLI)
    if args.output_folder:
        output_folder = Path(args.output_folder)
    else:
        output_folder = Path(config.get('output_folder', '')) if config.get('output_folder') else script_dir / 'TripPdfs'
    if args.output_folder_pdf:
        output_folder_pdf = Path(args.output_folder_pdf)
    else:
        output_folder_pdf = Path(config.get('output_folder_pdf', '')) if config.get('output_folder_pdf') else output_folder
    if args.output_folder_html:
        output_folder_html = Path(args.output_folder_html)
    else:
        output_folder_html = Path(config.get('output_folder_html', '')) if config.get('output_folder_html') else output_folder

    # ensure directories exist when used later
    for d in (output_folder, output_folder_pdf, output_folder_html):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # store back into config so render_trip can pick it up
    config['output_folder'] = str(output_folder)
    config['output_folder_pdf'] = str(output_folder_pdf)
    config['output_folder_html'] = str(output_folder_html)
    config['renderer_mode'] = args.renderer_mode
    
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
    
    # report which folders were scanned
    try:
        paths_str = ", ".join(str(p) for p in valid_folders)
        print(lang.t("cli.scanning_trips", path=paths_str))
    except Exception:
        pass

    # Handle combined overview HTML from CLI
    if args.combined_html is not None:
        if not trips:
            print(lang.t("cli.no_trips_in_bsp"))
            return
        start_date = None
        end_date = None
        try:
            if args.stats_from:
                start_date = datetime.fromisoformat(args.stats_from)
            if args.stats_to:
                end_date = datetime.fromisoformat(args.stats_to)
        except Exception:
            print("Invalid date format for --from/--to. Use YYYY-MM-DD.")
            return

        filtered_trips = filter_trips_by_date(trips, args.stats_year, start_date, end_date)
        if args.unrendered:
            cm2 = CacheManager(get_cache_dir() / "rendered_trips_cache.json")
            filtered_trips = [t for t in filtered_trips if not cm2.is_rendered(t)]

        if not filtered_trips:
            print("No trips match the requested filters.")
            return

        combined_path = args.combined_html
        if combined_path is None or str(combined_path).strip() == "":
            combined_path = output_folder_html / "combined_trips.html"
        else:
            combined_path = Path(combined_path)
            if not combined_path.is_absolute() and combined_path.parent == Path('.'):
                combined_path = output_folder_html / combined_path.name
        print(lang.t('cli.combined_html_building', path=combined_path))
        builder = CombinedHtmlBuilder(combined_path, filtered_trips, config=config, language_manager=lang, cli_language_manager=lang)
        if builder.build():
            print(lang.t('cli.combined_html_written', path=combined_path))
        else:
            print(lang.t('cli.combined_html_failed', path=combined_path))
        return

    # Handle statistics from CLI
    if args.stats:
        stats_map_gen = create_map_generator_from_config(config=config, lang=lang, purpose="stats")
        sg = StatisticsGenerator(map_generator=stats_map_gen, config=config)
        # parse date filters
        start_date = None
        end_date = None
        try:
            if args.stats_from:
                start_date = datetime.fromisoformat(args.stats_from)
            if args.stats_to:
                end_date = datetime.fromisoformat(args.stats_to)
        except Exception:
            print("Invalid date format for --from/--to. Use YYYY-MM-DD.")
            return

        # Apply same filters as trips
        filtered = filter_trips_by_date(trips, args.stats_year, start_date, end_date)
        if args.unrendered:
            cm2 = CacheManager(get_cache_dir() / "rendered_trips_cache.json")
            filtered = [t for t in filtered if not cm2.is_rendered(t)]

        if not filtered:
            print("No trips match the requested filters.")
            return

        # Print concise header and list trips once
        if args.stats_year:
            print(f"Reise dieses Jahr: {args.stats_year}")
        elif start_date and end_date:
            print(f"Reise im Zeitraum: {start_date.date()} bis {end_date.date()}")
        else:
            print("Reise:")
        for i, t in enumerate(filtered, 1):
            try:
                with open(t / 'trip.json', 'r', encoding='utf-8') as f:
                    td = json.load(f)
                name = td.get('name', t.name)
                print(f"  [{i}] {name}")
            except Exception:
                print(f"  [{i}] {t.name}")

        # Proceed immediately to compute statistics (no confirmation)
        print("Computing statistics for the selected trips...")

        # compute with progress reporting
        def _progress(idx, total, trip):
            # Progress reporting disabled for CLI stats (quiet mode)
            return

        agg = sg.compute_aggregate_stats(filtered, year=args.stats_year, start_date=start_date, end_date=end_date, progress_callback=_progress, verbose=args.stats_verbose, debug_countries=args.debug_countries)

        # Determine period for ratio calculation (favor explicit args)
        if args.stats_year:
            ps = datetime(args.stats_year, 1, 1).date()
            pe = datetime(args.stats_year, 12, 31).date()
        elif start_date and end_date:
            ps = start_date.date()
            pe = end_date.date()
        else:
            ps_iso = agg.get('period_start')
            pe_iso = agg.get('period_end')
            ps = date.fromisoformat(ps_iso) if ps_iso else None
            pe = date.fromisoformat(pe_iso) if pe_iso else None

        # If verbose, print per-trip breakdown
        if args.stats_verbose and agg.get('per_trip'):
            print('\n--- Per-Trip Breakdown ---')
            for i, pt in enumerate(agg.get('per_trip', []), 1):
                name = pt.get('name') or pt.get('path')
                steps = pt.get('steps', 0)
                td = pt.get('travel_days', 0)
                km = pt.get('total_km', 0)
                countries = pt.get('country_days', {})
                display_countries = sg.localize_country_counts(countries, language_code=lang.language_code)
                country_list = ', '.join([f"{c}({d})" for c, d in sorted(display_countries.items(), key=lambda x: -x[1])])
                print(f" [{i}] {name} — Steps: {steps}, Reisetage (im Zeitraum): {td}, km: {km}")
                if country_list:
                    print(f"      Länder: {country_list}")
            print('--- End of per-trip breakdown ---\n')
        period_total_days = None
        non_travel_days = None
        travel_pct = None
        if ps and pe:
            period_total_days = (pe - ps).days + 1
            travel_days = agg.get('total_travel_days', 0)
            non_travel_days = max(0, period_total_days - travel_days)
            travel_pct = (travel_days / period_total_days * 100) if period_total_days else None

        # print final summary to console (German)
        print("--- Statistik Ergebnis ---")
        print(f"Trips: {agg.get('trip_count',0)}")
        print(f"Steps gesamt: {agg.get('total_steps',0)}")
        print(f"Reisetage gesamt: {agg.get('total_travel_days',0)}")
        if period_total_days is not None:
            print(f"Reise/Non-Reise: {agg.get('total_travel_days',0)} Tage Reiset • {non_travel_days} Tage Nicht-Reise ({travel_pct:.1f}% Reise)")
        print(f"Gereiste km: {agg.get('total_km',0)}")
        print(f"Fotos: {agg.get('total_photos',0)}, Videos: {agg.get('total_videos',0)}")
        print(f"Länder bereist: {agg.get('visited_countries_count',0)} ({agg.get('visited_countries_percent',0.0)}% der Länder der Welt)")
        print("Länder (Tage):")
        display_countries = sg.localize_country_counts(agg.get('countries', {}), language_code=lang.language_code)
        for c, cnt in sorted(display_countries.items(), key=lambda x: -x[1]):
            pct = (cnt / max(1, agg.get('total_travel_days',1))) * 100 if agg.get('total_travel_days') else 0
            print(f"  {c}: {cnt} Tage ({pct:.1f}%)")
        print("")
        print(f"Kontinente bereist: {agg.get('visited_continents_count',0)} ({agg.get('visited_continents_percent',0.0)}% aller Kontinente)")
        print("Kontinente (Tage):")
        display_continents = sg.localize_continent_counts(agg.get('continents', {}), language_code=lang.language_code)
        for c, cnt in sorted(display_continents.items(), key=lambda x: -x[1]):
            pct = (cnt / max(1, agg.get('total_travel_days',1))) * 100 if agg.get('total_travel_days') else 0
            print(f"  {c}: {cnt} Tage ({pct:.1f}%)")

        # optional JSON export
        if args.stats_json:
            ok = sg.export_stats_json(agg, Path(args.stats_json))
            print(f"JSON export {'erfolgreich' if ok else 'fehlgeschlagen'}: {args.stats_json}")
        # optional map export
        if args.stats_map:
            try:
                mp = sg.generate_overview_map(filtered)
                with open(args.stats_map, 'wb') as mf:
                    mf.write(mp)
                print(f"Map export geschrieben: {args.stats_map}")
            except Exception as e:
                print(f"Map export fehlgeschlagen: {e}")
        print('Statistics completed.')
        return
    
    if not trips:
        print(lang.t("cli.no_trips_in_bsp"))
        sys.exit(1)
    
    print(lang.t("cli.found_trips", count=len(trips)))
    
    # Enter the unified prompt loop
    prompt_loop(trips, cache_manager, script_dir, config, lang)


if __name__ == "__main__":
    main()
