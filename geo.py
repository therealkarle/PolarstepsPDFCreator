"""
Geographic utilities for map calculations.

Provides:
- Haversine distance calculation
- km ↔ degree conversions (latitude-aware for longitude)
- Dateline-safe longitude normalization and circular mean
- Great-circle midpoint calculation
"""

from __future__ import annotations
import math
from typing import Tuple, List, Optional

# Earth radius in km (mean radius)
EARTH_RADIUS_KM = 6371.0

# Approximate km per degree latitude (constant)
KM_PER_DEG_LAT = 111.32

# Web Mercator latitude limits
WEB_MERCATOR_MAX_LAT = 85.05112878


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points using the Haversine formula.
    
    Args:
        lat1, lon1: First point coordinates in degrees
        lat2, lon2: Second point coordinates in degrees
    
    Returns:
        Distance in kilometers
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return EARTH_RADIUS_KM * c


def normalize_lon(lon: float) -> float:
    """
    Normalize longitude to [-180, 180) range.
    
    Args:
        lon: Longitude in degrees
    
    Returns:
        Normalized longitude in [-180, 180)
    """
    while lon >= 180:
        lon -= 360
    while lon < -180:
        lon += 360
    return lon


def km_to_deg_lat(km: float) -> float:
    """
    Convert kilometers to degrees latitude.
    
    Args:
        km: Distance in kilometers
    
    Returns:
        Equivalent degrees latitude
    """
    return km / KM_PER_DEG_LAT


def km_to_deg_lon(km: float, lat: float) -> float:
    """
    Convert kilometers to degrees longitude at a given latitude.
    
    The longitudinal distance per degree varies with latitude due to
    Earth's spherical shape: 1° lon ≈ 111.32 * cos(lat) km
    
    Args:
        km: Distance in kilometers
        lat: Reference latitude in degrees
    
    Returns:
        Equivalent degrees longitude at the given latitude
    """
    lat_rad = math.radians(lat)
    cos_lat = math.cos(lat_rad)
    if cos_lat < 1e-10:  # Near poles
        return 360.0  # Effectively infinite
    return km / (KM_PER_DEG_LAT * cos_lat)


def deg_lat_to_km(deg: float) -> float:
    """
    Convert degrees latitude to kilometers.
    
    Args:
        deg: Degrees latitude
    
    Returns:
        Distance in kilometers
    """
    return deg * KM_PER_DEG_LAT


def deg_lon_to_km(deg: float, lat: float) -> float:
    """
    Convert degrees longitude to kilometers at a given latitude.
    
    Args:
        deg: Degrees longitude
        lat: Reference latitude in degrees
    
    Returns:
        Distance in kilometers
    """
    lat_rad = math.radians(lat)
    return deg * KM_PER_DEG_LAT * math.cos(lat_rad)


def circular_mean_lon(longitudes: List[float]) -> float:
    """
    Calculate the circular mean of longitudes, handling dateline wrap-around.
    
    Uses vector averaging in Cartesian coordinates to correctly handle
    points that span the antimeridian (e.g., 170°E and 170°W).
    
    Args:
        longitudes: List of longitudes in degrees
    
    Returns:
        Circular mean longitude in [-180, 180)
    """
    if not longitudes:
        return 0.0
    if len(longitudes) == 1:
        return normalize_lon(longitudes[0])
    
    # Convert to unit vectors and average
    sum_sin = sum(math.sin(math.radians(lon)) for lon in longitudes)
    sum_cos = sum(math.cos(math.radians(lon)) for lon in longitudes)
    
    # Handle case where vectors cancel out (e.g., 0° and 180°)
    if abs(sum_sin) < 1e-10 and abs(sum_cos) < 1e-10:
        return normalize_lon(longitudes[0])
    
    mean_lon = math.degrees(math.atan2(sum_sin, sum_cos))
    return normalize_lon(mean_lon)


def geographic_midpoint(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """
    Calculate the geographic midpoint (centroid) of multiple lat/lon points.
    
    Uses 3D Cartesian averaging for accuracy across the globe,
    including dateline-crossing scenarios.
    
    Args:
        points: List of (lat, lon) tuples in degrees
    
    Returns:
        (lat, lon) tuple of the geographic midpoint
    """
    if not points:
        return (0.0, 0.0)
    if len(points) == 1:
        return points[0]
    
    # Convert to 3D Cartesian coordinates
    x_sum = y_sum = z_sum = 0.0
    for lat, lon in points:
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        x_sum += math.cos(lat_rad) * math.cos(lon_rad)
        y_sum += math.cos(lat_rad) * math.sin(lon_rad)
        z_sum += math.sin(lat_rad)
    
    n = len(points)
    x_avg = x_sum / n
    y_avg = y_sum / n
    z_avg = z_sum / n
    
    # Convert back to lat/lon
    lon_rad = math.atan2(y_avg, x_avg)
    hyp = math.sqrt(x_avg ** 2 + y_avg ** 2)
    lat_rad = math.atan2(z_avg, hyp)
    
    return (math.degrees(lat_rad), normalize_lon(math.degrees(lon_rad)))


def lon_span(lon_west: float, lon_east: float) -> float:
    """
    Calculate the longitude span, handling dateline wrap-around.
    
    Returns the smallest positive span from west to east, which may
    cross the antimeridian.
    
    Args:
        lon_west: Western longitude in degrees
        lon_east: Eastern longitude in degrees
    
    Returns:
        Span in degrees (0 to 360)
    """
    lon_west = normalize_lon(lon_west)
    lon_east = normalize_lon(lon_east)
    
    span = lon_east - lon_west
    if span < 0:
        span += 360
    return span


def expand_lon_bounds(lon_west: float, lon_east: float, expansion_deg: float) -> Tuple[float, float]:
    """
    Expand longitude bounds symmetrically, preserving wrap-around semantics.
    
    Args:
        lon_west: Western longitude
        lon_east: Eastern longitude
        expansion_deg: Degrees to add on each side
    
    Returns:
        (new_lon_west, new_lon_east) tuple
    """
    new_west = normalize_lon(lon_west - expansion_deg)
    new_east = normalize_lon(lon_east + expansion_deg)
    return (new_west, new_east)


def clamp_lat(lat: float, max_lat: float = WEB_MERCATOR_MAX_LAT) -> float:
    """
    Clamp latitude to valid Web Mercator range.
    
    Args:
        lat: Latitude in degrees
        max_lat: Maximum absolute latitude (default: Web Mercator limit)
    
    Returns:
        Clamped latitude
    """
    return max(-max_lat, min(max_lat, lat))


def lon_spans_dateline(lon_west: float, lon_east: float) -> bool:
    """
    Check if the longitude range crosses the antimeridian (dateline).
    
    Args:
        lon_west: Western bound (normalized to [-180, 180))
        lon_east: Eastern bound (normalized to [-180, 180))
    
    Returns:
        True if the range crosses ±180°
    """
    return normalize_lon(lon_west) > normalize_lon(lon_east)
