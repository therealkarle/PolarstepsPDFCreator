"""Verify that step maps use the first step image as marker thumbnail."""
import json
import tempfile
from pathlib import Path
from PIL import Image

from polarsteps_pdf_generator import MapGenerator, TripParser


def test_step_map_thumbnail():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "first_step.jpg"
        img = Image.new("RGB", (100, 100), (10, 20, 30))
        img.save(tmp_path, format="JPEG")

        trip_dir = Path(tmp_dir) / "trip"
        trip_dir.mkdir(parents=True, exist_ok=True)
        trip_json = {
            "name": "Test Trip",
            "start_date": 1700000000,
            "end_date": 1700000100,
            "all_steps": [
                {
                    "id": 1,
                    "name": "Test Step",
                    "display_name": "Test Step",
                    "location": {"lat": 46.5, "lon": 8.5},
                }
            ],
        }
        (trip_dir / "trip.json").write_text(json.dumps(trip_json), encoding="utf-8")

        trip = TripParser(trip_dir)
        trip.load()
        assert len(trip.steps) == 1
        trip.steps[0]["photos"] = [tmp_path]

        mg = MapGenerator()

        thumb = mg._get_step_thumbnail(trip.steps[0], size=40)
        assert thumb is not None and Path(thumb).exists(), "Step thumbnail should be created"

        map_bytes = mg.generate_step_map_for_step(trip, 0, width=320, height=180)
        assert map_bytes and isinstance(map_bytes, (bytes, bytearray)), "Step map bytes should be returned"

        print("OK: step map thumbnail test passed")


if __name__ == "__main__":
    test_step_map_thumbnail()