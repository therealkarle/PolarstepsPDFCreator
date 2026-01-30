"""
Map viewport calculation with Geographic Bounding Box approach.

Provides:
- GeoBounds: Dateline-aware bounding box with 16:9 aspect ratio enforcement
- MapViewport: Final viewport parameters (center, zoom, bounds) for static map APIs
- compute_overview_viewport: For trip overview maps showing all steps
- compute_step_viewport: For individual step maps with neighbor logic
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, NamedTuple

from geo import (
    haversine_km,
    normalize_lon,
    km_to_deg_lat,
    km_to_deg_lon,
    deg_lon_to_km,
    deg_lat_to_km,
    geographic_midpoint,
    circular_mean_lon,
    lon_span,
    expand_lon_bounds,
    clamp_lat,
    lon_spans_dateline,
    WEB_MERCATOR_MAX_LAT,
)


# Target aspect ratio (width:height)
ASPECT_RATIO = 16 / 9

# Tile size for standard Web Mercator tiles
TILE_SIZE_PX = 256


class StepLocation(NamedTuple):
    """A step's geographic location with identifier."""
    lat: float
    lon: float
    step_id: Optional[str] = None


@dataclass
class GeoBounds:
    """
    Geographic bounding box with dateline-aware longitude handling.
    
    Coordinates are stored as:
    - lat_south, lat_north: Southern and northern latitude bounds
    - lon_west, lon_east: Western and eastern longitude bounds
    
    The box may span the antimeridian if lon_west > lon_east (normalized).
    """
    lat_south: float
    lat_north: float
    lon_west: float
    lon_east: float
    
    def __post_init__(self):
        """Normalize and validate bounds."""
        self.lon_west = normalize_lon(self.lon_west)
        self.lon_east = normalize_lon(self.lon_east)
        self.lat_south = clamp_lat(self.lat_south)
        self.lat_north = clamp_lat(self.lat_north)
        
        if self.lat_south > self.lat_north:
            self.lat_south, self.lat_north = self.lat_north, self.lat_south
    
    @classmethod
    def from_points(cls, points: List[Tuple[float, float]]) -> "GeoBounds":
        """
        Create bounds that contain all given (lat, lon) points.
        
        Uses the smallest longitude span, which may cross the dateline.
        """
        if not points:
            raise ValueError("Cannot create bounds from empty point list")
        
        lats = [p[0] for p in points]
        lons = [normalize_lon(p[1]) for p in points]
        
        lat_south = min(lats)
        lat_north = max(lats)
        
        # Find the smallest longitude span (may cross dateline)
        lon_west, lon_east = cls._smallest_lon_span(lons)
        
        return cls(lat_south, lat_north, lon_west, lon_east)
    
    @staticmethod
    def _smallest_lon_span(lons: List[float]) -> Tuple[float, float]:
        """
        Find the smallest longitude span that contains all points.
        
        This handles wrap-around: if points are at 170°E and 170°W,
        the smallest span is 20° crossing the dateline, not 340° the other way.
        """
        if len(lons) == 1:
            return (lons[0], lons[0])
        
        sorted_lons = sorted(lons)
        n = len(sorted_lons)
        
        # Calculate gaps between consecutive longitudes
        # Include the wrap-around gap from last to first
        gaps = []
        for i in range(n):
            next_i = (i + 1) % n
            if next_i == 0:
                # Wrap-around gap
                gap = (sorted_lons[0] + 360) - sorted_lons[-1]
            else:
                gap = sorted_lons[next_i] - sorted_lons[i]
            gaps.append((gap, i))
        
        # The largest gap defines where to "cut" - bounds are on either side
        largest_gap_idx = max(gaps, key=lambda x: x[0])[1]
        
        # lon_west is the point after the largest gap
        # lon_east is the point at the largest gap
        lon_west_idx = (largest_gap_idx + 1) % n
        lon_east_idx = largest_gap_idx
        
        return (sorted_lons[lon_west_idx], sorted_lons[lon_east_idx])
    
    @property
    def center(self) -> Tuple[float, float]:
        """
        Get the geographic center of the bounds.
        
        Returns:
            (lat, lon) tuple
        """
        center_lat = (self.lat_south + self.lat_north) / 2
        
        # Handle dateline-crossing for longitude center
        if self.spans_dateline:
            # Center is on the "short" side across the dateline
            span = self.lon_span_deg
            center_lon = normalize_lon(self.lon_west + span / 2)
        else:
            center_lon = (self.lon_west + self.lon_east) / 2
        
        return (center_lat, center_lon)
    
    @property
    def spans_dateline(self) -> bool:
        """Check if the bounds cross the antimeridian."""
        return lon_spans_dateline(self.lon_west, self.lon_east)
    
    @property
    def lon_span_deg(self) -> float:
        """Get the longitude span in degrees."""
        return lon_span(self.lon_west, self.lon_east)
    
    @property
    def lat_span_deg(self) -> float:
        """Get the latitude span in degrees."""
        return self.lat_north - self.lat_south
    
    def width_km(self) -> float:
        """Get approximate width in km at the center latitude."""
        center_lat = (self.lat_south + self.lat_north) / 2
        return deg_lon_to_km(self.lon_span_deg, center_lat)
    
    def height_km(self) -> float:
        """Get approximate height in km."""
        return deg_lat_to_km(self.lat_span_deg)
    
    def expand_by_factor(self, factor: float) -> "GeoBounds":
        """
        Expand bounds by a percentage factor (e.g., 0.1 = 10% padding on each side).
        
        Args:
            factor: Expansion factor (0.1 = 10% padding)
        
        Returns:
            New expanded GeoBounds
        """
        lat_expand = self.lat_span_deg * factor
        lon_expand = self.lon_span_deg * factor
        
        new_lat_south = clamp_lat(self.lat_south - lat_expand)
        new_lat_north = clamp_lat(self.lat_north + lat_expand)
        new_lon_west, new_lon_east = expand_lon_bounds(
            self.lon_west, self.lon_east, lon_expand
        )
        
        return GeoBounds(new_lat_south, new_lat_north, new_lon_west, new_lon_east)
    
    def expand_to_min_width_km(self, min_width_km: float) -> "GeoBounds":
        """
        Expand bounds symmetrically to ensure minimum width.
        
        Args:
            min_width_km: Minimum required width in kilometers
        
        Returns:
            New GeoBounds with at least min_width_km width
        """
        current_width = self.width_km()
        if current_width >= min_width_km:
            return self
        
        center_lat, center_lon = self.center
        
        # Calculate how much to expand
        expand_km = (min_width_km - current_width) / 2
        expand_deg = km_to_deg_lon(expand_km, center_lat)
        
        new_lon_west, new_lon_east = expand_lon_bounds(
            self.lon_west, self.lon_east, expand_deg
        )
        
        return GeoBounds(self.lat_south, self.lat_north, new_lon_west, new_lon_east)
    
    def expand_to_aspect_ratio(self, target_ratio: float = ASPECT_RATIO) -> "GeoBounds":
        """
        Expand bounds (never shrink) to match target aspect ratio.
        
        The expansion is symmetric around the center.
        
        Args:
            target_ratio: Target width/height ratio (default: 16/9)
        
        Returns:
            New GeoBounds with the target aspect ratio
        """
        center_lat, center_lon = self.center
        
        width_km = self.width_km()
        height_km = self.height_km()
        
        # Handle edge cases
        if height_km < 0.001:
            height_km = 0.001
        if width_km < 0.001:
            width_km = 0.001
        
        current_ratio = width_km / height_km
        
        if current_ratio < target_ratio:
            # Too tall - expand width
            target_width_km = height_km * target_ratio
            expand_km = (target_width_km - width_km) / 2
            expand_deg = km_to_deg_lon(expand_km, center_lat)
            
            new_lon_west, new_lon_east = expand_lon_bounds(
                self.lon_west, self.lon_east, expand_deg
            )
            return GeoBounds(self.lat_south, self.lat_north, new_lon_west, new_lon_east)
        
        elif current_ratio > target_ratio:
            # Too wide - expand height
            target_height_km = width_km / target_ratio
            expand_km = (target_height_km - height_km) / 2
            expand_deg = km_to_deg_lat(expand_km)
            
            new_lat_south = clamp_lat(self.lat_south - expand_deg)
            new_lat_north = clamp_lat(self.lat_north + expand_deg)
            return GeoBounds(new_lat_south, new_lat_north, self.lon_west, self.lon_east)
        
        return self  # Already correct ratio


@dataclass
class MapViewport:
    """
    Final map viewport parameters ready for static map API consumption.
    
    Provides both center/zoom and bounds formats for API compatibility.
    """
    center_lat: float
    center_lon: float
    zoom: int
    bounds: GeoBounds
    
    # Computed pixel dimensions for the given zoom
    width_px: int = 0
    height_px: int = 0
    
    def to_center_zoom(self) -> Tuple[Tuple[float, float], int]:
        """Return (center, zoom) tuple for staticmap API."""
        return ((self.center_lat, self.center_lon), self.zoom)
    
    def to_bounds_dict(self) -> dict:
        """Return bounds as dict for APIs that accept corner coordinates."""
        return {
            "south": self.bounds.lat_south,
            "north": self.bounds.lat_north,
            "west": self.bounds.lon_west,
            "east": self.bounds.lon_east,
        }


def compute_zoom_for_bounds(
    bounds: GeoBounds,
    viewport_width_px: int,
    viewport_height_px: int,
    tile_size: int = TILE_SIZE_PX,
) -> int:
    """
    Calculate the appropriate zoom level to fit bounds in a viewport.
    
    Uses Web Mercator projection math to find the highest zoom level
    where the bounds fit entirely within the viewport.
    
    Args:
        bounds: Geographic bounds to fit
        viewport_width_px: Viewport width in pixels (logical, not render-scaled)
        viewport_height_px: Viewport height in pixels (logical, not render-scaled)
        tile_size: Tile size in pixels (default 256)
    
    Returns:
        Integer zoom level (typically 0-19)
    """
    if bounds.lon_span_deg == 0 and bounds.lat_span_deg == 0:
        return 15  # Single point - use a reasonable default
    
    # Calculate zoom for longitude span
    if bounds.lon_span_deg > 0:
        zoom_lon = math.log2(
            360 * viewport_width_px / (bounds.lon_span_deg * tile_size)
        )
    else:
        zoom_lon = 20
    
    # Calculate zoom for latitude span using Mercator projection
    if bounds.lat_span_deg > 0:
        # Mercator Y coordinates
        def lat_to_y(lat: float) -> float:
            lat_rad = math.radians(clamp_lat(lat))
            return math.log(math.tan(math.pi / 4 + lat_rad / 2))
        
        y_north = lat_to_y(bounds.lat_north)
        y_south = lat_to_y(bounds.lat_south)
        y_span = abs(y_north - y_south)
        
        if y_span > 0:
            zoom_lat = math.log2(
                2 * math.pi * viewport_height_px / (y_span * tile_size)
            )
        else:
            zoom_lat = 20
    else:
        zoom_lat = 20
    
    # Use the more restrictive zoom (smaller value = more zoomed out)
    zoom = min(zoom_lon, zoom_lat)
    
    # Clamp to valid range and floor to ensure bounds fit
    return max(0, min(19, int(math.floor(zoom))))


def _compute_zoom_for_radius_km(
    radius_km: float,
    center_lat: float,
    viewport_width_px: int,
    viewport_height_px: int,
    aspect_ratio: float = ASPECT_RATIO,
    tile_size: int = TILE_SIZE_PX,
) -> int:
    """
    Compute zoom level that guarantees a circle of radius_km fits in viewport.
    
    This is the core of the new RADIUS-BASED fitting algorithm:
    - We want all points within radius_km of center to be visible
    - The viewport has a certain aspect ratio
    - We compute the zoom where the SMALLER dimension (height for 16:9)
      can contain 2*radius_km
    
    This guarantees points won't be cut off because we fit to the
    constraining dimension.
    """
    if radius_km <= 0:
        return 15
    
    # Convert radius to degrees at the center latitude
    # For latitude: 1 degree ≈ 111 km
    radius_lat_deg = radius_km / 111.0
    
    # For longitude: depends on latitude
    cos_lat = max(0.01, abs(math.cos(math.radians(center_lat))))
    radius_lon_deg = radius_km / (111.0 * cos_lat)
    
    # The viewport shows a certain span at each zoom level
    # At zoom z, the world is 256 * 2^z pixels wide (360 degrees)
    # degrees_per_pixel_lon = 360 / (256 * 2^z)
    # We need: (2 * radius_lon_deg) / degrees_per_pixel_lon <= viewport_width_px
    # Solving: 2^z >= (2 * radius_lon_deg * 256) / (360 * viewport_width_px / tile_size)
    
    # For longitude constraint:
    if radius_lon_deg > 0:
        span_lon = 2 * radius_lon_deg
        zoom_lon = math.log2(360 * viewport_width_px / (span_lon * tile_size))
    else:
        zoom_lon = 19
    
    # For latitude constraint (using Mercator projection):
    if radius_lat_deg > 0:
        # Mercator Y for lat
        def lat_to_merc_y(lat: float) -> float:
            lat_rad = math.radians(clamp_lat(lat))
            return math.log(math.tan(math.pi / 4 + lat_rad / 2))
        
        north_lat = clamp_lat(center_lat + radius_lat_deg)
        south_lat = clamp_lat(center_lat - radius_lat_deg)
        y_span = abs(lat_to_merc_y(north_lat) - lat_to_merc_y(south_lat))
        
        if y_span > 0:
            zoom_lat = math.log2(2 * math.pi * viewport_height_px / (y_span * tile_size))
        else:
            zoom_lat = 19
    else:
        zoom_lat = 19
    
    # Use the MORE RESTRICTIVE zoom (smaller = more zoomed out = safer)
    zoom = min(zoom_lon, zoom_lat)
    
    # Floor and clamp
    return max(0, min(19, int(math.floor(zoom))))


def compute_overview_viewport(
    steps: List[StepLocation],
    padding_factor: float = 0.10,
    min_width_km: float = 5.0,
    viewport_width_px: int = 800,
    viewport_height_px: int = 450,  # 16:9 at 800px width
    aspect_ratio: float = ASPECT_RATIO,
    extra_padding_px: float = 0.0,
) -> MapViewport:
    """
    Compute viewport for trip overview map showing all steps.
    
    NEW RADIUS-BASED ALGORITHM:
    1. Compute geographic center of all points
    2. Find maximum distance from center to ANY point
    3. Add padding margin to that radius
    4. Compute zoom that guarantees that radius fits in viewport
    
    This ensures all markers are visible because we explicitly ensure
    the furthest point (plus margin) fits within the viewport.
    
    Args:
        steps: List of all step locations
        padding_factor: Padding around steps (0.10 = 10% extra radius)
        min_width_km: Minimum map width in kilometers
        viewport_width_px: Logical viewport width (not render-scaled)
        viewport_height_px: Logical viewport height (not render-scaled)
        aspect_ratio: Target aspect ratio (width:height)
        extra_padding_px: Extra pixel padding for markers (added to radius)
    
    Returns:
        MapViewport ready for static map generation
    """
    if not steps:
        raise ValueError("Cannot compute viewport for empty step list")
    
    # Convert to point tuples (lat, lon)
    points = [(s.lat, s.lon) for s in steps]
    
    # Step 1: Compute geographic center
    center_lat, center_lon = geographic_midpoint(points)
    
    # Step 2: Find maximum distance from center to any point
    max_radius_km = 0.0
    for lat, lon in points:
        dist = haversine_km(center_lat, center_lon, lat, lon)
        if dist > max_radius_km:
            max_radius_km = dist
    
    # Step 3: Apply padding factor (e.g., 0.10 = 10% extra)
    # Use a minimum padding of 30% to ensure markers don't touch edges
    effective_padding = max(0.30, padding_factor)
    padded_radius_km = max_radius_km * (1.0 + effective_padding)
    
    # Step 4: Apply extra pixel-based padding converted to km
    # Approximate: at equator, 1 pixel at zoom 15 ≈ 4.78 meters
    # This is rough but adds safety margin
    try:
        pad_px = float(extra_padding_px)
    except Exception:
        pad_px = 0.0
    if pad_px > 0 and viewport_width_px > 0:
        # Assume we need this many pixels of margin, convert to fraction of viewport
        # and then to fraction of radius
        pixel_margin_fraction = (2 * pad_px) / viewport_width_px
        padded_radius_km *= (1.0 + pixel_margin_fraction)
    
    # Step 5: Enforce minimum width (radius = width/2)
    min_radius_km = min_width_km / 2.0
    if padded_radius_km < min_radius_km:
        padded_radius_km = min_radius_km
    
    # Step 6: Compute zoom using radius-based algorithm
    zoom = _compute_zoom_for_radius_km(
        padded_radius_km,
        center_lat,
        viewport_width_px,
        viewport_height_px,
        aspect_ratio,
    )
    
    # Create bounds for compatibility (used for debugging/display)
    # This is approximate - the actual viewport may differ slightly
    bounds = GeoBounds.from_points(points)
    bounds = bounds.expand_by_factor(effective_padding)
    bounds = bounds.expand_to_aspect_ratio(aspect_ratio)
    
    return MapViewport(
        center_lat=center_lat,
        center_lon=center_lon,
        zoom=zoom,
        bounds=bounds,
        width_px=viewport_width_px,
        height_px=viewport_height_px,
    )


def compute_step_viewport(
    current_step: StepLocation,
    prev_step: Optional[StepLocation],
    next_step: Optional[StepLocation],
    max_distance_farthest_km: float = 100.0,
    min_width_km: float = 2.0,
    cluster_distance_km: float = 5.0,
    padding_factor: float = 0.10,
    viewport_width_px: int = 800,
    viewport_height_px: int = 450,
    aspect_ratio: float = ASPECT_RATIO,
    extra_padding_px: float = 0.0,
) -> MapViewport:
    """
    Compute viewport for individual step map with distance-based neighbor selection.
    
    DISTANCE-BASED ALGORITHM:
    1. Get current step + prev/next neighbors
    2. Calculate distance between the two farthest steps
    3. If farthest distance <= max_distance_farthest_km, include all visible
    4. Otherwise drop the neighbor farthest from current, re-check remaining
    5. If still exceeds threshold, show only current step (use min_width_km)
    6. Center map on geographic midpoint of ALL visible steps
    
    Args:
        current_step: The current step location (always included)
        prev_step: Previous step (Sn-1), or None if this is the first step
        next_step: Next step (Sn+1), or None if this is the last step
        max_distance_farthest_km: Maximum allowed distance between farthest visible steps
        min_width_km: Minimum map width in kilometers (used when only current step shown)
        cluster_distance_km: Not used in new algorithm (kept for API compatibility)
        padding_factor: Padding around included steps
        viewport_width_px: Logical viewport width
        viewport_height_px: Logical viewport height
        aspect_ratio: Target aspect ratio (width:height)
        extra_padding_px: Extra pixel padding for markers
    
    Returns:
        MapViewport ready for static map generation
    """
    # Calculate distances from current step to neighbors
    prev_distance = None
    next_distance = None
    
    if prev_step:
        prev_distance = haversine_km(
            current_step.lat, current_step.lon,
            prev_step.lat, prev_step.lon
        )
    
    if next_step:
        next_distance = haversine_km(
            current_step.lat, current_step.lon,
            next_step.lat, next_step.lon
        )
    
    # Calculate distance between prev and next (if both exist)
    prev_next_distance = None
    if prev_step and next_step:
        prev_next_distance = haversine_km(
            prev_step.lat, prev_step.lon,
            next_step.lat, next_step.lon
        )
    
    # Determine which steps to include based on a horizontal max distance
    # (from config) and a derived vertical max distance from the aspect ratio.
    # The horizontal limit is the configured value; vertical limit is scaled
    # by the map's aspect ratio so tall spans are constrained appropriately.
    horizontal_limit_km = max_distance_farthest_km
    vertical_limit_km = max_distance_farthest_km / max(0.1, aspect_ratio)

    def _horizontal_km(a: StepLocation, b: StepLocation) -> float:
        # lon_span expects west->east ordering. Compute the minimal span in
        # either direction to handle arbitrary point ordering.
        span_ab = lon_span(a.lon, b.lon)
        span_ba = lon_span(b.lon, a.lon)
        lon_deg = min(span_ab, span_ba)
        mid_lat = (a.lat + b.lat) / 2.0
        return deg_lon_to_km(lon_deg, mid_lat)

    def _within_limits(horiz_km: float, vert_km: float) -> bool:
        return (horiz_km <= horizontal_limit_km) and (vert_km <= vertical_limit_km)
    include_prev = False
    include_next = False
    
    if prev_step and next_step:
        # Both neighbors exist - check if all three fit
        # Farthest horizontal span among the same pairs
        horiz_prev_next = _horizontal_km(prev_step, next_step)
        horiz_prev_current = _horizontal_km(prev_step, current_step)
        horiz_current_next = _horizontal_km(current_step, next_step)
        farthest_horiz = max(horiz_prev_next, horiz_prev_current, horiz_current_next)

        # Farthest vertical span among the same pairs
        vert_prev_next = abs(prev_step.lat - next_step.lat) * 111.0
        vert_prev_current = abs(prev_step.lat - current_step.lat) * 111.0
        vert_current_next = abs(current_step.lat - next_step.lat) * 111.0
        farthest_vert = max(vert_prev_next, vert_prev_current, vert_current_next)
        
        if _within_limits(farthest_horiz, farthest_vert):
            # All three fit
            include_prev = True
            include_next = True
        else:
            # Drop the neighbor farthest from current
            if (prev_distance or 0) >= (next_distance or 0):
                # prev is farther - drop it, check if next fits alone
                if _within_limits(
                    _horizontal_km(current_step, next_step),
                    abs(current_step.lat - next_step.lat) * 111.0
                ):
                    include_next = True
                # else: only current shown
            else:
                # next is farther - drop it, check if prev fits alone
                if _within_limits(
                    _horizontal_km(current_step, prev_step),
                    abs(current_step.lat - prev_step.lat) * 111.0
                ):
                    include_prev = True
                # else: only current shown
    
    elif prev_step:
        # Only prev neighbor exists (current is last step)
        if _within_limits(
            _horizontal_km(current_step, prev_step),
            abs(current_step.lat - prev_step.lat) * 111.0
        ):
            include_prev = True
    
    elif next_step:
        # Only next neighbor exists (current is first step)
        if _within_limits(
            _horizontal_km(current_step, next_step),
            abs(current_step.lat - next_step.lat) * 111.0
        ):
            include_next = True
    
    # Build list of included points
    included_points = [(current_step.lat, current_step.lon)]
    if include_prev and prev_step:
        included_points.append((prev_step.lat, prev_step.lon))
    if include_next and next_step:
        included_points.append((next_step.lat, next_step.lon))
    
    # CENTER ON MIDPOINT OF ALL VISIBLE STEPS
    if len(included_points) == 1:
        # Only current step - center on it
        center_lat = current_step.lat
        center_lon = current_step.lon
    else:
        # Multiple steps - compute geographic midpoint
        center_lat, center_lon = geographic_midpoint(included_points)
    
    # Create bounds from all included points
    bounds = GeoBounds.from_points(included_points)
    
    # Apply padding from config to keep markers inside viewport.
    # Horizontal padding uses the configured factor; vertical padding is scaled
    # by aspect ratio so wide maps don't get excessive vertical buffer.
    horizontal_padding = padding_factor
    vertical_padding = padding_factor / max(0.1, aspect_ratio)

    lat_expand = bounds.lat_span_deg * vertical_padding
    lon_expand = bounds.lon_span_deg * horizontal_padding
    new_lat_south = clamp_lat(bounds.lat_south - lat_expand)
    new_lat_north = clamp_lat(bounds.lat_north + lat_expand)
    new_lon_west, new_lon_east = expand_lon_bounds(
        bounds.lon_west, bounds.lon_east, lon_expand
    )
    bounds = GeoBounds(new_lat_south, new_lat_north, new_lon_west, new_lon_east)
    
    # Apply extra pixel-based padding for marker sizes
    try:
        pad_px = float(extra_padding_px)
    except Exception:
        pad_px = 0.0
    if pad_px > 0 and viewport_width_px > 0:
        pixel_margin_fraction = (2 * pad_px) / viewport_width_px
        bounds = bounds.expand_by_factor(pixel_margin_fraction)
    
    # Enforce minimum width
    bounds = bounds.expand_to_min_width_km(min_width_km)
    
    # Expand to target aspect ratio (never shrinks, only expands)
    bounds = bounds.expand_to_aspect_ratio(aspect_ratio)
    
    # Compute zoom using the actual bounds (not radius)
    # This ensures the rectangular viewport properly contains all points
    zoom = compute_zoom_for_bounds(
        bounds,
        viewport_width_px,
        viewport_height_px,
    )
    
    # Use the bounds center for the map center (more accurate than midpoint for wide maps)
    center_lat, center_lon = bounds.center
    
    return MapViewport(
        center_lat=center_lat,
        center_lon=center_lon,
        zoom=zoom,
        bounds=bounds,
        width_px=viewport_width_px,
        height_px=viewport_height_px,
    )


def get_path_coordinates(
    prev_step: Optional[StepLocation],
    current_step: StepLocation,
    next_step: Optional[StepLocation],
) -> List[Tuple[float, float]]:
    """
    Get path coordinates for drawing the route line.
    
    Always returns coordinates for the path from prev -> current -> next,
    regardless of whether the neighbors are within the viewport bounds.
    This ensures the path is drawn even when neighbors are outside the view.
    
    Args:
        prev_step: Previous step location (may be None)
        current_step: Current step location
        next_step: Next step location (may be None)
    
    Returns:
        List of (lat, lon) tuples defining the path
    """
    path = []
    
    if prev_step:
        path.append((prev_step.lat, prev_step.lon))
    
    path.append((current_step.lat, current_step.lon))
    
    if next_step:
        path.append((next_step.lat, next_step.lon))
    
    return path
