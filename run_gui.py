"""Run the GUI from VS Code or by running this file directly.

This script ensures we operate from the repository root and invokes the dependency
checker which will launch the GUI when ready.

Run from VS Code: press F5 or 'Run Python File in Terminal' on this file.
"""
from pathlib import Path
import os
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
# ensure working dir is project root
os.chdir(str(SCRIPT_DIR))

try:
    from scripts import ensure_deps
except Exception as e:
    print("Failed to import scripts.ensure_deps:", e)
    raise

rc = ensure_deps.main_entry()
if rc != 0:
    print(f"ensure_deps returned exit code {rc}")
    sys.exit(rc)
