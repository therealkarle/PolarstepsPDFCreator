"""Simple Tkinter GUI for PolarstepsPDFCreator

Features:
- Choose BSPData folder
- List available trips (multi-select)
- Render selected trips using existing render_trip function in background
- Simple progress and stop control
"""
from pathlib import Path
import threading
import queue
import os
import sys
import time
import traceback

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception:
    raise RuntimeError("Tkinter is required to run the GUI on this platform.")

import polarsteps_pdf_generator as m

SCRIPT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BSP = SCRIPT_DIR / "BSPData"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Polarsteps PDF Creator")
        self.geometry("800x520")

        self.bsp_path = tk.StringVar(value=str(DEFAULT_BSP))
        self.status_text = tk.StringVar(value="Idle")

        self._create_widgets()

        self.log_queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.render_thread = None

        self._poll_queue()

        # Warn if Playwright not available (PDF generation may fail)
        if m.sync_playwright is None:
            messagebox.showwarning("Playwright missing",
                                   "Playwright not found. HTML->PDF rendering may fail unless Playwright is installed.\n\nYou can run 'pip install playwright' and 'playwright install' to add browsers.")

        self.load_trips()

    def _create_widgets(self):
        frm_top = ttk.Frame(self)
        frm_top.pack(fill=tk.X, padx=10, pady=(10, 6))

        ttk.Label(frm_top, text="BSPData folder:").pack(side=tk.LEFT)
        ttk.Entry(frm_top, textvariable=self.bsp_path, width=60).pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(frm_top, text="Browse...", command=self._on_browse).pack(side=tk.LEFT)
        # If Playwright is missing, show quick-install button
        if m.sync_playwright is None:
            self.playwright_btn = ttk.Button(frm_top, text="Install Playwright", command=self._on_install_playwright)
            self.playwright_btn.pack(side=tk.LEFT, padx=(6, 0))

        frm_mid = ttk.Frame(self)
        frm_mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        lbl = ttk.Label(frm_mid, text="Available trips:")
        lbl.pack(anchor=tk.W)

        self.trips_listbox = tk.Listbox(frm_mid, selectmode=tk.EXTENDED, height=18)
        self.trips_listbox.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scr = ttk.Scrollbar(frm_mid, orient=tk.VERTICAL, command=self.trips_listbox.yview)
        scr.pack(side=tk.LEFT, fill=tk.Y)
        self.trips_listbox.config(yscrollcommand=scr.set)

        frm_controls = ttk.Frame(self)
        frm_controls.pack(fill=tk.X, padx=10, pady=(6, 10))

        ttk.Button(frm_controls, text="Refresh", command=self.load_trips).pack(side=tk.LEFT)
        ttk.Button(frm_controls, text="Select All", command=self._select_all).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(frm_controls, text="Deselect All", command=self._deselect_all).pack(side=tk.LEFT, padx=(6, 0))

        self.render_btn = ttk.Button(frm_controls, text="Render Selected", command=self._on_render)
        self.render_btn.pack(side=tk.RIGHT)

        self.stop_btn = ttk.Button(frm_controls, text="Stop", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, padx=(6, 0))

        self.progress = ttk.Progressbar(self, orient=tk.HORIZONTAL, mode='determinate')
        self.progress.pack(fill=tk.X, padx=10)

        self.status_label = ttk.Label(self, textvariable=self.status_text)
        self.status_label.pack(fill=tk.X, padx=10, pady=(6, 10))

    def _on_browse(self):
        path = filedialog.askdirectory(initialdir=self.bsp_path.get() or str(DEFAULT_BSP))
        if path:
            self.bsp_path.set(path)
            self.load_trips()

    def load_trips(self):
        self.trips_listbox.delete(0, tk.END)
        bsp = Path(self.bsp_path.get())
        if not bsp.exists():
            messagebox.showerror("Folder not found", f"BSPData folder not found: {bsp}")
            return
        try:
            trips = m.find_trips(bsp)
        except Exception as e:
            messagebox.showerror("Error", f"Could not list trips: {e}")
            return
        self._trips = trips
        cm = m.CacheManager(SCRIPT_DIR / 'cache' / 'rendered_trips_cache.json')
        for t in trips:
            display = t.name
            # attempt nicer name from trip.json if available
            try:
                parser = m.TripParser(t)
                parser.load()
                name = parser.get_trip_name()
                display = f"{name} — {t.name}"
            except Exception:
                pass
            if cm.is_rendered(t):
                display = f"{display} (rendered)"
            self.trips_listbox.insert(tk.END, display)

    def _select_all(self):
        self.trips_listbox.select_set(0, tk.END)

    def _deselect_all(self):
        self.trips_listbox.select_clear(0, tk.END)

    def _on_render(self):
        sel = self.trips_listbox.curselection()
        if not sel:
            messagebox.showinfo("No selection", "Please select one or more trips to render.")
            return
        trips = [self._trips[i] for i in sel]
        # disable controls
        self.render_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.stop_flag.clear()
        self.progress['maximum'] = len(trips)
        self.progress['value'] = 0
        self.status_text.set(f"Rendering {len(trips)} trip(s)...")
        self.render_thread = threading.Thread(target=self._render_worker, args=(trips,), daemon=True)
        self.render_thread.start()

    def _on_stop(self):
        self.stop_flag.set()
        self.status_text.set("Stopping...")

    def _render_worker(self, trips):
        try:
            cache_file = SCRIPT_DIR / 'cache' / 'rendered_trips_cache.json'
            cm = m.CacheManager(cache_file)

            # load config.toml if available
            config_file = SCRIPT_DIR / 'config.toml'
            config = {}
            try:
                if config_file.exists():
                    content = config_file.read_text(encoding='utf-8')
                    if hasattr(m, '_tomllib') and m._tomllib:
                        config = m._tomllib.loads(content)
                    else:
                        config = m._parse_simple_toml(content)
            except Exception:
                config = {}

            total = len(trips)
            done = 0
            for idx, trip in enumerate(trips, start=1):
                if self.stop_flag.is_set():
                    self.log_queue.put(("status", "Stopped by user"))
                    break
                self.log_queue.put(("status", f"Rendering {idx}/{total}: {trip.name}"))
                try:
                    res = m.render_trip(trip, SCRIPT_DIR, config, cm, check_stop=lambda: self.stop_flag.is_set())
                    if res:
                        # open resulting PDF if exists
                        try:
                            parser = m.TripParser(trip)
                            parser.load()
                            trip_name_safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in parser.get_trip_name())
                            pdf_path = SCRIPT_DIR / 'TripPdfs' / f"{trip_name_safe}.pdf"
                        except Exception:
                            pdf_path = SCRIPT_DIR / 'TripPdfs' / f"{trip.name}.pdf"
                        if pdf_path.exists():
                            try:
                                os.startfile(str(pdf_path))
                            except Exception:
                                pass
                        self.log_queue.put(("info", f"Rendered: {trip.name}"))
                    else:
                        self.log_queue.put(("error", f"Failed or stopped: {trip.name}"))
                except Exception as e:
                    self.log_queue.put(("error", f"Error rendering {trip.name}: {e}\n{traceback.format_exc()}"))
                done += 1
                self.log_queue.put(("progress", done))
            self.log_queue.put(("done", None))
        except Exception as exc:
            self.log_queue.put(("error", f"Worker crashed: {exc}\n{traceback.format_exc()}"))
            self.log_queue.put(("done", None))

    def _on_install_playwright(self):
        # start install in background thread
        self.playwright_btn.config(state=tk.DISABLED)
        t = threading.Thread(target=self._install_playwright_worker, daemon=True)
        t.start()

    def _install_playwright_worker(self):
        try:
            self.log_queue.put(("status", "Installing Playwright via pip..."))
            # install package
            rc = os.system(f'"{sys.executable}" -m pip install playwright')
            if rc != 0:
                self.log_queue.put(("error", "`pip install playwright` failed. See terminal output."))
                self.log_queue.put(("install_done", False))
                return
            self.log_queue.put(("status", "Installing Playwright browsers..."))
            rc2 = os.system(f'"{sys.executable}" -m playwright install chromium firefox webkit')
            if rc2 != 0:
                self.log_queue.put(("error", "`playwright install` failed. See terminal output."))
                self.log_queue.put(("install_done", False))
                return
            self.log_queue.put(("info", "Playwright and browsers installed. Please restart the app."))
            self.log_queue.put(("install_done", True))
        except Exception as e:
            self.log_queue.put(("error", f"Playwright install error: {e}"))
            self.log_queue.put(("install_done", False))

    def _poll_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                typ, payload = item
                if typ == "status":
                    self.status_text.set(payload)
                elif typ == "info":
                    self.status_text.set(payload)
                elif typ == "error":
                    messagebox.showerror("Error", payload)
                elif typ == "progress":
                    self.progress['value'] = payload
                elif typ == "done":
                    self.status_text.set("Done")
                    self.render_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                elif typ == "install_done":
                    if payload:
                        messagebox.showinfo("Playwright", "Playwright installation succeeded. Please restart the app.")
                    else:
                        messagebox.showerror("Playwright", "Playwright installation failed. See terminal output.")
                    # re-enable install button if present
                    try:
                        self.playwright_btn.config(state=tk.NORMAL)
                    except Exception:
                        pass
                else:
                    self.status_text.set(str(payload))
        except queue.Empty:
            pass
        finally:
            self.after(200, self._poll_queue)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
