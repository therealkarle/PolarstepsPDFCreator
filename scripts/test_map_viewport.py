#!/usr/bin/env python3
"""
Unit tests for the new Geographic Bounding Box map scaling system.

Tests:
- geo.py: Haversine, km<->deg conversions, dateline handling
- map_viewport.py: GeoBounds, 16:9 expansion, viewport computation
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from geo import (
    haversine_km,
    normalize_lon,
    km_to_deg_lat,
    km_to_deg_lon,
    deg_lat_to_km,
    deg_lon_to_km,
    circular_mean_lon,
    geographic_midpoint,
    lon_span,
    lon_spans_dateline,
)

from map_viewport import (
    StepLocation,
    GeoBounds,
    MapViewport,
    compute_overview_viewport,
    compute_step_viewport,
    get_path_coordinates,
    compute_zoom_for_bounds,
    ASPECT_RATIO,
)


def test_haversine():
    """Test Haversine distance calculation."""
    # New York to London: ~5570 km
    dist = haversine_km(40.7128, -74.0060, 51.5074, -0.1278)
    assert 5500 < dist < 5700, f"NYC-London distance should be ~5570km, got {dist:.1f}km"
    
    # Same point should be 0
    dist = haversine_km(0, 0, 0, 0)
    assert dist == 0, "Same point should have distance 0"
    
    # Short distance (Berlin to Potsdam: ~27km)
    dist = haversine_km(52.5200, 13.4050, 52.3906, 13.0645)
    assert 25 < dist < 30, f"Berlin-Potsdam distance should be ~27km, got {dist:.1f}km"
    
    print("✓ haversine_km tests passed")


def test_normalize_lon():
    """Test longitude normalization."""
    assert normalize_lon(0) == 0
    assert normalize_lon(180) == -180  # Edge case: 180 becomes -180
    assert normalize_lon(-180) == -180
    assert normalize_lon(270) == -90
    assert normalize_lon(-270) == 90
    assert normalize_lon(360) == 0
    assert normalize_lon(540) == -180
    
    print("✓ normalize_lon tests passed")


def test_km_deg_conversions():
    """Test km <-> degree conversions."""
    # 1 degree latitude is always ~111.32 km
    assert abs(km_to_deg_lat(111.32) - 1.0) < 0.01
    assert abs(deg_lat_to_km(1.0) - 111.32) < 0.01
    
    # At equator: 1 degree longitude is also ~111 km
    assert abs(km_to_deg_lon(111.32, 0) - 1.0) < 0.1
    assert abs(deg_lon_to_km(1.0, 0) - 111.32) < 0.1
    
    # At 60° latitude: 1 degree longitude is ~55.6 km (cos(60°) = 0.5)
    assert abs(deg_lon_to_km(1.0, 60) - 55.66) < 1.0
    
    print("✓ km/deg conversion tests passed")


def test_circular_mean_lon():
    """Test circular mean longitude (dateline handling)."""
    # Simple case: average of 0 and 10
    mean = circular_mean_lon([0, 10])
    assert abs(mean - 5) < 0.1, f"Mean of 0,10 should be 5, got {mean}"
    
    # Dateline case: 170° and -170° should average to 180° (or -180°)
    mean = circular_mean_lon([170, -170])
    assert abs(abs(mean) - 180) < 1, f"Mean of 170,-170 should be near 180, got {mean}"
    
    # Multiple points across dateline
    mean = circular_mean_lon([175, 179, -179, -175])
    assert abs(abs(mean) - 180) < 5, f"Mean should be near 180, got {mean}"
    
    print("✓ circular_mean_lon tests passed")


def test_geographic_midpoint():
    """Test geographic midpoint calculation."""
    # Simple case
    mid = geographic_midpoint([(0, 0), (0, 10)])
    assert abs(mid[0] - 0) < 0.1 and abs(mid[1] - 5) < 0.1
    
    # Across dateline
    mid = geographic_midpoint([(0, 170), (0, -170)])
    assert abs(mid[0]) < 1, f"Midpoint lat should be 0, got {mid[0]}"
    # Longitude should be near ±180
    assert abs(abs(mid[1]) - 180) < 5, f"Midpoint lon should be near 180, got {mid[1]}"
    
    print("✓ geographic_midpoint tests passed")


def test_lon_span():
    """Test longitude span calculation."""
    # Normal case
    assert abs(lon_span(-10, 10) - 20) < 0.01
    
    # Dateline crossing (170°E to 170°W = 20° span)
    span = lon_span(170, -170)
    assert abs(span - 20) < 0.01, f"170 to -170 should be 20°, got {span}"
    
    # Full circle
    span = lon_span(-180, -180)
    assert span == 0 or span == 360  # Could be either
    
    print("✓ lon_span tests passed")


def test_lon_spans_dateline():
    """Test dateline crossing detection."""
    assert not lon_spans_dateline(-10, 10)
    assert lon_spans_dateline(170, -170)
    assert not lon_spans_dateline(-170, 170)  # Goes the "long way"
    
    print("✓ lon_spans_dateline tests passed")


def test_geobounds_from_points():
    """Test GeoBounds creation from points."""
    # Simple case
    points = [(48.0, 8.0), (49.0, 9.0), (47.0, 10.0)]
    bounds = GeoBounds.from_points(points)
    
    assert bounds.lat_south == 47.0
    assert bounds.lat_north == 49.0
    assert bounds.lon_west == 8.0
    assert bounds.lon_east == 10.0
    
    # Check center
    center = bounds.center
    assert abs(center[0] - 48.0) < 0.1
    assert abs(center[1] - 9.0) < 0.1
    
    print("✓ GeoBounds.from_points tests passed")


def test_geobounds_aspect_ratio():
    """Test 16:9 aspect ratio expansion."""
    # Create a square-ish bounds
    bounds = GeoBounds(lat_south=47.0, lat_north=48.0, lon_west=8.0, lon_east=9.0)
    
    # Expand to 16:9
    expanded = bounds.expand_to_aspect_ratio(16/9)
    
    # Check that width/height ratio is approximately 16:9
    width = expanded.width_km()
    height = expanded.height_km()
    ratio = width / height
    
    assert abs(ratio - 16/9) < 0.1, f"Ratio should be 16/9 (~1.78), got {ratio:.2f}"
    
    # Bounds should only expand, never shrink
    assert expanded.lat_north >= bounds.lat_north
    assert expanded.lat_south <= bounds.lat_south
    
    print("✓ GeoBounds.expand_to_aspect_ratio tests passed")


def test_geobounds_min_width():
    """Test minimum width enforcement."""
    # Create very small bounds (single point effectively)
    bounds = GeoBounds(lat_south=48.0, lat_north=48.001, lon_west=9.0, lon_east=9.001)
    
    # Expand to minimum 10km width
    expanded = bounds.expand_to_min_width_km(10.0)
    
    assert expanded.width_km() >= 9.9, f"Width should be >= 10km, got {expanded.width_km():.1f}km"
    
    print("✓ GeoBounds.expand_to_min_width_km tests passed")


def test_compute_overview_viewport():
    """Test overview viewport computation."""
    steps = [
        StepLocation(lat=48.0, lon=8.0, step_id="0"),
        StepLocation(lat=48.5, lon=9.0, step_id="1"),
        StepLocation(lat=47.5, lon=10.0, step_id="2"),
    ]
    
    viewport = compute_overview_viewport(
        steps=steps,
        padding_factor=0.10,
        min_width_km=10.0,
    )
    
    assert isinstance(viewport, MapViewport)
    assert viewport.zoom >= 0 and viewport.zoom <= 19
    assert -90 <= viewport.center_lat <= 90
    assert -180 <= viewport.center_lon <= 180
    
    # Check 16:9 aspect ratio of bounds
    width = viewport.bounds.width_km()
    height = viewport.bounds.height_km()
    ratio = width / height
    assert abs(ratio - 16/9) < 0.2, f"Bounds ratio should be ~16/9, got {ratio:.2f}"
    
    print("✓ compute_overview_viewport tests passed")


def test_compute_step_viewport():
    """Test step viewport computation with neighbor filtering."""
    current = StepLocation(lat=48.0, lon=9.0, step_id="current")
    prev_step = StepLocation(lat=48.1, lon=8.9, step_id="prev")  # ~15km away
    next_step = StepLocation(lat=47.9, lon=9.1, step_id="next")  # ~15km away
    
    viewport = compute_step_viewport(
        current_step=current,
        prev_step=prev_step,
        next_step=next_step,
        max_width_km=100.0,
        min_width_km=2.0,
        cluster_distance_km=5.0,
        padding_factor=0.10,
    )
    
    assert isinstance(viewport, MapViewport)
    assert viewport.zoom >= 0 and viewport.zoom <= 19
    
    # With max_width_km=100, both neighbors should fit
    assert viewport.bounds.width_km() <= 110  # some margin for padding/aspect
    
    print("✓ compute_step_viewport tests passed")


def test_step_viewport_max_width_filtering():
    """Test that neighbors are excluded when they exceed max_width_km."""
    current = StepLocation(lat=48.0, lon=9.0, step_id="current")
    # Very far neighbor (>500km away)
    far_prev = StepLocation(lat=52.0, lon=9.0, step_id="prev")  # ~440km away
    close_next = StepLocation(lat=48.1, lon=9.1, step_id="next")  # ~15km away
    
    viewport = compute_step_viewport(
        current_step=current,
        prev_step=far_prev,
        next_step=close_next,
        max_width_km=100.0,  # Should exclude far_prev
        min_width_km=2.0,
        cluster_distance_km=5.0,
        padding_factor=0.10,
    )
    
    # Width should be limited (far neighbor excluded)
    assert viewport.bounds.width_km() <= 120, f"Width should be <=120km, got {viewport.bounds.width_km():.1f}km"
    
    print("✓ step viewport max_width filtering tests passed")


def test_get_path_coordinates():
    """Test path coordinate extraction."""
    current = StepLocation(lat=48.0, lon=9.0)
    prev_step = StepLocation(lat=47.5, lon=8.5)
    next_step = StepLocation(lat=48.5, lon=9.5)
    
    # Full path
    path = get_path_coordinates(prev_step, current, next_step)
    assert len(path) == 3
    assert path[0] == (prev_step.lat, prev_step.lon)
    assert path[1] == (current.lat, current.lon)
    assert path[2] == (next_step.lat, next_step.lon)
    
    # No prev
    path = get_path_coordinates(None, current, next_step)
    assert len(path) == 2
    
    # No next
    path = get_path_coordinates(prev_step, current, None)
    assert len(path) == 2
    
    # Only current
    path = get_path_coordinates(None, current, None)
    assert len(path) == 1
    
    print("✓ get_path_coordinates tests passed")


def test_compute_zoom_for_bounds():
    """Test zoom level computation."""
    # Small area (should be high zoom)
    bounds = GeoBounds(lat_south=48.0, lat_north=48.1, lon_west=9.0, lon_east=9.1)
    zoom = compute_zoom_for_bounds(bounds, 800, 450)
    assert 10 <= zoom <= 15, f"Small area zoom should be 10-15, got {zoom}"
    
    # Large area (should be low zoom)
    bounds = GeoBounds(lat_south=30.0, lat_north=60.0, lon_west=-10.0, lon_east=30.0)
    zoom = compute_zoom_for_bounds(bounds, 800, 450)
    assert 3 <= zoom <= 7, f"Large area zoom should be 3-7, got {zoom}"
    
    print("✓ compute_zoom_for_bounds tests passed")


def run_all_tests():
    """Run all unit tests."""
    print("Running geo.py tests...")
    test_haversine()
    test_normalize_lon()
    test_km_deg_conversions()
    test_circular_mean_lon()
    test_geographic_midpoint()
    test_lon_span()
    test_lon_spans_dateline()
    
    print("\nRunning map_viewport.py tests...")
    test_geobounds_from_points()
    test_geobounds_aspect_ratio()
    test_geobounds_min_width()
    test_compute_overview_viewport()
    test_compute_step_viewport()
    test_step_viewport_max_width_filtering()
    test_get_path_coordinates()
    test_compute_zoom_for_bounds()
    
    print("\n" + "="*50)
    print("All tests passed! ✓")
    print("="*50)


if __name__ == "__main__":
    run_all_tests()
