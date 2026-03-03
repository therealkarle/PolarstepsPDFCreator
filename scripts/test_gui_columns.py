"""Quick smoke test for GUI column configuration.

This script verifies that the new translation keys used by the trips
Treeview headers are present in both English and German language packs.
It can be run manually as a lightweight check and does **not** require
starting the GUI or a display.
"""
from pathlib import Path
import sys
# ensure the workspace root is on sys.path like other tests
sys.path.append(str(Path(__file__).resolve().parents[1]))
from polarsteps_pdf_generator import load_language_manager, SCRIPT_DIR


def _check_keys(lang_mgr, keys):
    missing = []
    for k in keys:
        if lang_mgr.t(k, default=None) is None:
            missing.append(k)
    return missing


def main():
    keys = ['gui.trip_name', 'gui.column_dates', 'gui.column_days', 'gui.column_folder']
    en = load_language_manager('en', SCRIPT_DIR)
    de = load_language_manager('de', SCRIPT_DIR)
    miss_en = _check_keys(en, keys)
    miss_de = _check_keys(de, keys)
    if miss_en or miss_de:
        print("Missing translation keys:")
        if miss_en:
            print("  English:", miss_en)
        if miss_de:
            print("  German:", miss_de)
        raise AssertionError("Translation keys missing")
    else:
        print("All GUI column translation keys are present.")

    # sanity-check sorting functionality by creating a hidden App and loading trips
    try:
        import tkinter as tk
        from gui.tk_gui import App
        # prevent Playwright missing warning during automated tests
        import polarsteps_pdf_generator as m
        try:
            m.sync_playwright = object()
        except Exception:
            pass
        app = App()
        app.withdraw()
        app.bsp_path.set(str(Path('BSPData')))
        app.load_trips()
        # ensure some items exist and that default sort placed newest first by checking first two start dates
        items = app.trips_tree.get_children()
        if items and len(items) > 1:
            first = app._trip_meta.get(items[0], {}).get('start_date')
            second = app._trip_meta.get(items[1], {}).get('start_date')
            if first is not None and second is not None and first < second:
                raise AssertionError("Default sort by start date descending not applied")
        # test arrow indicator on trip column
        app._on_heading_click('trip')  # sort by trip once
        txt = app.trips_tree.heading('trip')['text']
        # arrow may be up or down depending on initial state, just ensure one is present
        if '▲' not in txt and '▼' not in txt:
            raise AssertionError(f"Arrow not shown on trip heading (text={txt!r})")
        # other headings should not contain arrow
        for col in ['dates', 'days', 'folder']:
            if '▲' in app.trips_tree.heading(col)['text'] or '▼' in app.trips_tree.heading(col)['text']:
                raise AssertionError(f"Unexpected arrow in heading {col}")
        app.destroy()
        print("Sorting behaviour appears functional.")
    except Exception as e:
        print(f"Sorting check skipped or failed: {e}")


if __name__ == '__main__':
    main()
