"""Test script to debug config.toml parsing."""
import sys
from pathlib import Path

script_dir = Path(__file__).parent.parent
sys.path.insert(0, str(script_dir))

# Check if tomllib exists
_tomllib = None
try:
    import tomllib as _tomllib
    print("Using tomllib (Python 3.11+)")
except ImportError:
    try:
        import toml as _tomllib
        print("Using toml package")
    except ImportError:
        print("No TOML library available, using fallback parser")

# Load config.toml
config_toml = script_dir / "config.toml"
print(f"Config file exists: {config_toml.exists()}")

content = config_toml.read_text(encoding="utf-8")

# Simple TOML parser (copy from main script)
def _parse_simple_toml(content: str) -> dict:
    data = {}
    current = data
    for raw_line in content.splitlines():
        line = raw_line.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip()
            current = data
            if section:
                for part in section.split('.'):
                    part = part.strip()
                    if not part:
                        continue
                    current = current.setdefault(part, {})
            continue
        if '=' not in line:
            continue
        key, val = line.split('=', 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        parsed = None
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            parsed = val[1:-1]
        elif val.lower() in ('true', 'false'):
            parsed = val.lower() == 'true'
        else:
            try:
                if '.' in val:
                    parsed = float(val)
                else:
                    parsed = int(val)
            except Exception:
                parsed = val
        current[key] = parsed
    return data

# Parse
if _tomllib is not None:
    config = _tomllib.loads(content)
    print("Parsed with library")
else:
    config = _parse_simple_toml(content)
    print("Parsed with fallback")

print(f"\nTop-level keys: {list(config.keys())}")
print(f"'language' key present: {'language' in config}")
print(f"config.get('language', 'en'): {repr(config.get('language', 'en'))}")
print(f"polarsteps_data_folder: {config.get('polarsteps_data_folder')}")
print(f"bsp_folder (legacy): {config.get('bsp_folder')}")
print(f"output_folder: {config.get('output_folder')}")

# Also show the last 10 lines of raw content
print("\n--- Last 15 lines of config.toml ---")
for line in content.splitlines()[-15:]:
    print(f"  {repr(line)}")
