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


def compute_overview_viewport(
    steps: List[StepLocation],
    padding_factor: float = 0.10,
    min_width_km: float = 5.0,
    viewport_width_px: int = 800,
    viewport_height_px: int = 450,  # 16:9 at 800px width
) -> MapViewport:
    """
    Compute viewport for trip overview map showing all steps.
    
    Args:
        steps: List of all step locations
        padding_factor: Padding around steps (0.10 = 10% on each side)
        min_width_km: Minimum map width in kilometers
        viewport_width_px: Logical viewport width (not render-scaled)
        viewport_height_px: Logical viewport height (not render-scaled)
    
    Returns:
        MapViewport ready for static map generation
    """
    if not steps:
        raise ValueError("Cannot compute viewport for empty step list")
    
    # Convert to point tuples
    points = [(s.lat, s.lon) for s in steps]
    
    # Create initial bounds from all points
    bounds = GeoBounds.from_points(points)
    
    # Apply padding
    bounds = bounds.expand_by_factor(padding_factor)
    
    # Ensure minimum width
    bounds = bounds.expand_to_min_width_km(min_width_km)
    
    # Enforce 16:9 aspect ratio
    bounds = bounds.expand_to_aspect_ratio(ASPECT_RATIO)
    
    # Calculate zoom
    zoom = compute_zoom_for_bounds(bounds, viewport_width_px, viewport_height_px)
    
    # Get center
    center_lat, center_lon = bounds.center
    
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
    max_width_km: float = 100.0,
    min_width_km: float = 2.0,
    cluster_distance_km: float = 5.0,
    padding_factor: float = 0.10,
    viewport_width_px: int = 800,
    viewport_height_px: int = 450,
) -> MapViewport:
    """
    Compute viewport for individual step map with smart neighbor inclusion.
    
    Logic:
    1. Always include the current step
    2. Include prev/next step only if they are the immediate neighbors (n=1)
    3. Exception: If a neighbor is within cluster_distance_km, include subsequent
       neighbors in the same direction as long as they remain clustered
    4. If including a neighbor would exceed max_width_km, exclude it
       (remove farthest from current first)
    
    The max_width_km check is based on the LARGER of:
    - The horizontal span (width) of all included points
    - The diagonal distance from current step to the farthest included point
    
    This ensures that even if a neighbor is far away in latitude (north-south),
    it will be excluded if the resulting map would be too large.
    
    Args:
        current_step: The current step location (always included)
        prev_step: Previous step (Sn-1), or None if this is the first step
        next_step: Next step (Sn+1), or None if this is the last step
        max_width_km: Maximum map width in kilometers
        min_width_km: Minimum map width in kilometers
        cluster_distance_km: Max distance to consider steps as clustered
        padding_factor: Padding around included steps
        viewport_width_px: Logical viewport width
        viewport_height_px: Logical viewport height
    
    Returns:
        MapViewport ready for static map generation
    """
    # Start with current step
    included_points = [(current_step.lat, current_step.lon)]
    
    # Calculate distances to neighbors
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
    
    # Determine which neighbors to include
    candidates = []
    
    if prev_step and prev_distance is not None:
        candidates.append({
            'step': prev_step,
            'distance': prev_distance,
            'is_cluster': prev_distance <= cluster_distance_km,
        })
    
    if next_step and next_distance is not None:
        candidates.append({
            'step': next_step,
            'distance': next_distance,
            'is_cluster': next_distance <= cluster_distance_km,
        })
    
    # Sort by distance (closest first) so we can remove farthest if needed
    candidates.sort(key=lambda x: x['distance'])
    
    # Try to include neighbors, respecting max_width constraint
    for candidate in candidates:
        # Calculate the effective "span" if we include this neighbor
        # Use the maximum of:
        # 1. The distance from current step to this neighbor (directly)
        # 2. The width of the bounding box of all included points
        # 3. The height of the bounding box (for 16:9, height matters too)
        
        test_points = included_points + [(candidate['step'].lat, candidate['step'].lon)]
        test_bounds = GeoBounds.from_points(test_points)
        
        # Check max extent: use the larger of width and scaled height
        # For 16:9 aspect ratio, the height will be scaled to match width
        effective_width = max(
            test_bounds.width_km(),
            test_bounds.height_km() * ASPECT_RATIO,  # Height scaled to match 16:9
            candidate['distance'] * 2  # Diameter around current step
        )
        
        # Apply padding factor to get the final effective width
        padded_effective_width = effective_width * (1 + padding_factor * 2)
        
        # Check if adding this point would exceed max width
        if padded_effective_width <= max_width_km:
            included_points.append((candidate['step'].lat, candidate['step'].lon))
        else:
            # Skip this neighbor - it would make the map too wide
            # Since we sorted by distance, farther ones are removed first
            pass
    
    # Create bounds from included points
    bounds = GeoBounds.from_points(included_points)
    
    # Apply padding
    bounds = bounds.expand_by_factor(padding_factor)
    
    # Ensure minimum width
    bounds = bounds.expand_to_min_width_km(min_width_km)
    
    # Enforce 16:9 aspect ratio
    bounds = bounds.expand_to_aspect_ratio(ASPECT_RATIO)
    
    # Calculate zoom
    zoom = compute_zoom_for_bounds(bounds, viewport_width_px, viewport_height_px)
    
    # Center on the geographic midpoint of included points
    center_lat, center_lon = geographic_midpoint(included_points)
    
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
