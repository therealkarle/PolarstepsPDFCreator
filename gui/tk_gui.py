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
import subprocess
from datetime import datetime

# Try to make the app DPI-aware on Windows to avoid blurry/scaled canvas rendering
if sys.platform == 'win32':
    try:
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            try:
                # fallback to newer shcore API
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
    except Exception:
        pass

# Try to import Pillow for high-quality antialiased drawing
try:
    from PIL import Image, ImageDraw, ImageTk
    HAVE_PIL = True
except Exception:
    Image = ImageDraw = ImageTk = None
    HAVE_PIL = False

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception:
    raise RuntimeError("Tkinter is required to run the GUI on this platform.")

# Optional calendar widget for improved date input in the GUI
try:
    from tkcalendar import DateEntry
    HAVE_TKCALENDAR = True
except Exception:
    DateEntry = None
    HAVE_TKCALENDAR = False

# Optional importlib.metadata for package version checks
try:
    from importlib.metadata import version, PackageNotFoundError
except Exception:
    try:
        from importlib_metadata import version, PackageNotFoundError  # type: ignore
    except Exception:
        version = None
        class PackageNotFoundError(Exception):
            pass

import polarsteps_pdf_generator as m

# Simple tooltip helper for heading hover
class _Tooltip:
    def __init__(self, parent, text):
        self.parent = parent
        self.text = text
        self._tw = None
    def show(self, x, y):
        if self._tw is not None:
            return
        tw = tk.Toplevel(self.parent)
        tw.wm_overrideredirect(True)
        # small label styled like native tooltips
        lbl = ttk.Label(tw, text=self.text, background='#ffffe0', relief='solid', borderwidth=1)
        lbl.pack(ipadx=4, ipady=2)
        tw.wm_geometry(f"+{x}+{y}")
        self._tw = tw
    def hide(self):
        if self._tw:
            try:
                self._tw.destroy()
            except Exception:
                pass
            self._tw = None


class ToggleSwitch(tk.Canvas):
    """A simple toggle switch widget implemented with Canvas.

    Uses Pillow to draw an anti-aliased image at a higher scale and downsamples
    for smooth edges. Falls back to Canvas primitives if Pillow is not available.

    Usage:
      sw = ToggleSwitch(parent, variable=someBooleanVar, command=callback, use_aa=True)
    """
    def __init__(self, parent, variable=None, width=80, height=44, padding=4,
                 on_color='#17a589', off_color='#e6e6e6', knob_color='white', command=None,
                 anim_duration=150, anim_step_ms=15, use_aa=True, **kwargs):
        bg = None
        try:
            bg = parent.cget('background')
        except Exception:
            bg = kwargs.get('bg', None)
        super().__init__(parent, width=width, height=height, highlightthickness=0, bd=0, bg=bg, **kwargs)
        self.variable = variable if variable is not None else tk.BooleanVar(value=False)
        self.width = width
        self.height = height
        self.padding = padding
        self.on_color = on_color
        self.off_color = off_color
        self.knob_color = knob_color
        self.command = command
        self.use_aa = bool(use_aa) and HAVE_PIL
        self._photo = None
        self._img_id = None
        self.configure(cursor='hand2')

        # Animation state
        self._anim_duration = max(10, int(anim_duration))
        self._anim_step_ms = max(5, int(anim_step_ms))
        self._anim_progress = 1.0 if self.variable.get() else 0.0  # 0.0 left, 1.0 right
        self._anim_target = self._anim_progress
        self._anim_running = False

        # react to external variable changes by animating to new state
        try:
            self.variable.trace_add('write', lambda *a: self._on_variable_changed())
        except Exception:
            try:
                self.variable.trace('w', lambda *a: self._on_variable_changed())
            except Exception:
                pass

        self.bind('<Button-1>', self._on_click)
        # initial draw
        self._draw()

    # color helpers
    def _hex_to_rgb(self, h):
        h = h.lstrip('#')
        if len(h) == 3:
            h = ''.join([c*2 for c in h])
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def _rgb_to_hex(self, rgb):
        return '#{:02x}{:02x}{:02x}'.format(*[int(max(0, min(255, round(v)))) for v in rgb])

    def _interp_color(self, c1, c2, t):
        try:
            a = self._hex_to_rgb(c1)
            b = self._hex_to_rgb(c2)
            interp = tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
            return self._rgb_to_hex(interp)
        except Exception:
            return c2 if t > 0.5 else c1

    def _draw(self):
        # If Pillow is available and use_aa enabled, render an anti-aliased image
        if self.use_aa and HAVE_PIL:
            try:
                scale = 3
                W = int(self.width * scale)
                H = int(self.height * scale)
                p = int(self.padding * scale)
                radius = int((H - 2 * p) / 2)

                # create RGBA image
                # determine a solid background color usable by PIL (convert tk color names)
                try:
                    tk_bg = self['bg'] if self['bg'] else self.master.cget('background')
                    r16, g16, b16 = self.winfo_rgb(tk_bg)
                    bg_color = '#{:02x}{:02x}{:02x}'.format(r16 // 256, g16 // 256, b16 // 256)
                except Exception:
                    bg_color = '#ffffff'

                img = Image.new('RGBA', (W, H), bg_color)
                draw = ImageDraw.Draw(img)

                # blended background fill
                fill = self._interp_color(self.off_color, self.on_color, self._anim_progress)
                # draw rounded rect as background (rounded_rectangle may not exist in older PIL)
                try:
                    draw.rounded_rectangle([p, p, W - p, H - p], radius=radius, fill=fill)
                except Exception:
                    # fallback: rectangle + end circles
                    draw.rectangle([p + radius, p, W - p - radius, H - p], fill=fill)
                    draw.ellipse([p, p, p + 2 * radius, H - p], fill=fill)
                    draw.ellipse([W - p - 2 * radius, p, W - p, H - p], fill=fill)

                # knob position
                left_x = p
                right_x = W - p - 2 * radius
                knob_x = left_x + (right_x - left_x) * self._anim_progress
                draw.ellipse([knob_x, p, knob_x + 2 * radius, H - p], fill=self.knob_color)

                # downsample for smooth edges
                img_small = img.resize((self.width, self.height), resample=Image.LANCZOS)
                # keep a reference to the PhotoImage to avoid GC
                self._photo = ImageTk.PhotoImage(img_small)
                if self._img_id is None:
                    self._img_id = self.create_image(0, 0, anchor='nw', image=self._photo)
                else:
                    self.itemconfig(self._img_id, image=self._photo)
                return
            except Exception:
                # fall back to vector draw on failure
                pass

        # Fallback: simple canvas primitives (no AA)
        self.delete('all')
        w = self.width
        h = self.height
        p = self.padding
        radius = (h - 2 * p) / 2

        # Background fill blends between off/on color based on animation progress
        fill = self._interp_color(self.off_color, self.on_color, self._anim_progress)
        # left circle
        self.create_oval(p, p, p + 2 * radius, h - p, fill=fill, width=0)
        # right circle
        self.create_oval(w - p - 2 * radius, p, w - p, h - p, fill=fill, width=0)
        # center rectangle
        self.create_rectangle(p + radius, p, w - p - radius, h - p, fill=fill, width=0)

        # knob position based on _anim_progress (smooth)
        left_x = p
        right_x = w - p - 2 * radius
        knob_x = left_x + (right_x - left_x) * self._anim_progress
        self.create_oval(knob_x, p, knob_x + 2 * radius, h - p, fill=self.knob_color, width=0)

    def _on_click(self, event=None):
        try:
            self.variable.set(not self.variable.get())
        except Exception:
            pass
        # Keep calling the command on user click (backwards compatible)
        if self.command:
            try:
                self.command()
            except Exception:
                pass

    def _on_variable_changed(self):
        try:
            self._anim_target = 1.0 if self.variable.get() else 0.0
        except Exception:
            self._anim_target = 0.0
        self._start_animation()

    def _start_animation(self):
        # start animation loop if not already running
        if self._anim_running:
            return
        self._anim_running = True
        self.after(0, self._animate)

    def _animate(self):
        # step towards target
        try:
            diff = self._anim_target - self._anim_progress
            if abs(diff) < 0.01:
                self._anim_progress = self._anim_target
                self._anim_running = False
                self._draw()
                return
            # step fraction proportional to step ms and duration
            step = float(self._anim_step_ms) / float(self._anim_duration)
            # ensure a minimum visible step
            step = max(0.02, step)
            self._anim_progress += step if diff > 0 else -step
            self._anim_progress = max(0.0, min(1.0, self._anim_progress))
            self._draw()
            self.after(self._anim_step_ms, self._animate)
        except Exception:
            try:
                self._anim_progress = self._anim_target
                self._anim_running = False
                self._draw()
            except Exception:
                pass


SCRIPT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BSP = SCRIPT_DIR / "BSPData"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Polarsteps PDF Creator")
        self.geometry("800x600")

        self.bsp_path = tk.StringVar(value=str(DEFAULT_BSP))
        self.status_text = tk.StringVar(value="Idle")
        
        # Filter variables
        self.filter_show_all = tk.BooleanVar(value=True)  # True = show all (include rendered)
        self.filter_year = tk.StringVar(value="")
        self.filter_start_date = tk.StringVar(value="")
        self.filter_end_date = tk.StringVar(value="")
        # Mode for date filters: either 'year' or 'date' (mutually exclusive)
        self.filter_date_mode = tk.StringVar(value='year')
        # Toggle switch: True => date mode, False => year mode (keeps in sync with filter_date_mode)
        self.filter_date_is_date = tk.BooleanVar(value=False)
        self.filter_config_overrides = tk.StringVar(value="")  # e.g., key=value, key2=42

        self._create_widgets()

        # Resize and center window to fit content (but limit to screen size)
        try:
            self.update_idletasks()
            req_w = self.winfo_reqwidth()
            req_h = self.winfo_reqheight()
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            max_w = int(screen_w * 0.95)
            max_h = int(screen_h * 0.95)
            w = min(req_w + 40, max_w)
            h = min(req_h + 80, max_h)
            x = max(0, (screen_w - w) // 2)
            y = max(0, (screen_h - h) // 8)
            self.geometry(f"{w}x{h}+{x}+{y}")
            try:
                self.minsize(min(w, max_w), min(h, max_h))
            except Exception:
                pass
        except Exception:
            pass

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
        # Notebook with two tabs: Trips and Settings
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tab_trips = ttk.Frame(self.notebook)
        tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(tab_trips, text='Trips')
        self.notebook.add(tab_settings, text='Einstellungen')

        frm_top = ttk.Frame(tab_trips)
        frm_top.pack(fill=tk.X, padx=10, pady=(10, 6))

        ttk.Label(frm_top, text="BSPData folder:").pack(side=tk.LEFT)
        ttk.Entry(frm_top, textvariable=self.bsp_path, width=60).pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(frm_top, text="Browse...", command=self._on_browse).pack(side=tk.LEFT)
        # If Playwright is missing, show quick-install button
        if m.sync_playwright is None:
            self.playwright_btn = ttk.Button(frm_top, text="Install Playwright", command=self._on_install_playwright)
            self.playwright_btn.pack(side=tk.LEFT, padx=(6, 0))

        frm_mid = ttk.Frame(tab_trips)
        frm_mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        lbl = ttk.Label(frm_mid, text="Available trips:")
        lbl.pack(anchor=tk.W)

        # Treeview with a narrow "Rendered" column (symbol header) and trip name column
        self.trips_tree = ttk.Treeview(frm_mid, columns=('rendered', 'trip'), show='headings', selectmode=tk.EXTENDED, height=18)
        # Use a check symbol as header; make column narrow and non-stretching
        self.trips_tree.heading('rendered', text='✔')
        self.trips_tree.column('rendered', width=28, minwidth=24, anchor='center', stretch=False)
        self.trips_tree.heading('trip', text='Trip')
        self.trips_tree.column('trip', anchor='w')
        self.trips_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scr = ttk.Scrollbar(frm_mid, orient=tk.VERTICAL, command=self.trips_tree.yview)
        scr.pack(side=tk.LEFT, fill=tk.Y)
        self.trips_tree.config(yscrollcommand=scr.set)

        # Tooltip for the rendered header
        try:
            self._rendered_tooltip = _Tooltip(self, "Gerendert")
            self.trips_tree.bind('<Motion>', self._on_tree_motion)
            self.trips_tree.bind('<Leave>', lambda e: self._rendered_tooltip.hide())
        except Exception:
            self._rendered_tooltip = None

        frm_controls = ttk.Frame(tab_trips)
        frm_controls.pack(fill=tk.X, padx=10, pady=(6, 10))

        # Filters
        ttk.Label(frm_controls, text="Filters:").pack(side=tk.LEFT)
        self.chk_show_all = ttk.Checkbutton(frm_controls, text="Include rendered", variable=self.filter_show_all, command=self.load_trips)
        self.chk_show_all.pack(side=tk.LEFT, padx=(6, 0))

        # Date / Year selector (calendar if available) with mode switch
        frm_date = ttk.Frame(frm_controls)
        frm_date.pack(side=tk.LEFT, padx=(12, 0))
        # Mode: choose either Year or Date range (mutually exclusive)
        frm_mode = ttk.Frame(frm_date)
        frm_mode.grid(row=0, column=0, columnspan=2, sticky='w')
        # Toggle placed BETWEEN Year and Date columns (no visible label - tooltip is used)
        # Compact, AA-enabled toggle (smaller size)
        self.chk_date_toggle = ToggleSwitch(frm_date, variable=self.filter_date_is_date, command=self._on_toggle_date_mode, width=64, height=36, padding=3, on_color='#17a589', off_color='#e6e6e6', use_aa=True)
        self.chk_date_toggle.grid(row=1, column=2, padx=(6, 10))
        # Tooltip for the toggle (explanatory text shown on hover)
        try:
            self._toggle_tooltip = _Tooltip(self, "Aktiviert Datumsbereich anstatt Jahr")
            self.chk_date_toggle.bind('<Enter>', lambda e: self._toggle_tooltip.show(e.x_root + 10, e.y_root + 10))
            self.chk_date_toggle.bind('<Motion>', lambda e: self._toggle_tooltip.show(e.x_root + 10, e.y_root + 10))
            self.chk_date_toggle.bind('<Leave>', lambda e: self._toggle_tooltip.hide())
        except Exception:
            self._toggle_tooltip = None

        ttk.Label(frm_date, text="Year:").grid(row=1, column=0, sticky='w')
        years = [''] + [str(y) for y in range(datetime.now().year, datetime.now().year - 30, -1)]
        self.cmb_year = ttk.Combobox(frm_date, width=6, values=years, textvariable=self.filter_year)
        self.cmb_year.grid(row=1, column=1, padx=(6, 4))

        if HAVE_TKCALENDAR:
            ttk.Label(frm_date, text="From:").grid(row=1, column=3, sticky='w', padx=(8, 0))
            self.start_cal = DateEntry(frm_date, width=12, date_pattern='dd.mm.yyyy')
            self.start_cal.grid(row=1, column=4, padx=(6, 4))
            ttk.Label(frm_date, text="To:").grid(row=1, column=5, sticky='w', padx=(8, 0))
            self.end_cal = DateEntry(frm_date, width=12, date_pattern='dd.mm.yyyy')
            self.end_cal.grid(row=1, column=6, padx=(6, 0))
        else:
            ttk.Label(frm_date, text="From:").grid(row=1, column=3, sticky='w', padx=(8, 0))
            self.ent_start = ttk.Entry(frm_date, width=10, textvariable=self.filter_start_date)
            self.ent_start.grid(row=1, column=4, padx=(6, 4))
            ttk.Label(frm_date, text="To:").grid(row=1, column=5, sticky='w', padx=(8, 0))
            self.ent_end = ttk.Entry(frm_date, width=10, textvariable=self.filter_end_date)
            self.ent_end.grid(row=1, column=6, padx=(6, 0))
            self.lbl_cal_hint = ttk.Label(frm_date, text="(Install 'tkcalendar' for calendar picker)", foreground='gray')
            self.lbl_cal_hint.grid(row=2, column=0, columnspan=7, sticky='w')

        # Initialize widget states depending on mode
        try:
            # Sync boolean toggle to current mode
            try:
                self.filter_date_is_date.set(self.filter_date_mode.get() == 'date')
            except Exception:
                pass
            self._on_date_mode_change()
        except Exception:
            pass

        ttk.Label(frm_controls, text="Config:").pack(side=tk.LEFT, padx=(8, 0))
        self.ent_config = ttk.Entry(frm_controls, width=20, textvariable=self.filter_config_overrides)
        self.ent_config.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(frm_controls, text="Apply", command=self.load_trips).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Button(frm_controls, text="Refresh", command=self.load_trips).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(frm_controls, text="Select All", command=self._select_all).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(frm_controls, text="Deselect All", command=self._deselect_all).pack(side=tk.LEFT, padx=(6, 0))
        # Select only trips that have not been rendered yet
        ttk.Button(frm_controls, text="Select Unrendered", command=self._select_unrendered).pack(side=tk.LEFT, padx=(6, 0))

        # NOTE: Render / Stop buttons moved below the progress bar to a dedicated row

        # Settings tab: Config editor and package manager
        try:
            # Config editor
            frm_cfg = ttk.LabelFrame(tab_settings, text='Konfiguration (config.toml)')
            frm_cfg.pack(fill=tk.BOTH, expand=False, padx=10, pady=(10, 6))
            self.cfg_text = tk.Text(frm_cfg, height=12, wrap='none')
            self.cfg_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
            cfg_scr = ttk.Scrollbar(frm_cfg, orient=tk.VERTICAL, command=self.cfg_text.yview)
            cfg_scr.pack(side=tk.LEFT, fill=tk.Y)
            self.cfg_text.config(yscrollcommand=cfg_scr.set)
            frm_cfg_btn = ttk.Frame(frm_cfg)
            frm_cfg_btn.pack(fill=tk.X, padx=6, pady=6)
            ttk.Button(frm_cfg_btn, text='Reload', command=self._load_config_text).pack(side=tk.LEFT)
            ttk.Button(frm_cfg_btn, text='Save', command=self._save_config_text).pack(side=tk.LEFT, padx=(6, 0))

            # Package manager
            frm_pkg = ttk.LabelFrame(tab_settings, text='Packages')
            frm_pkg.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 10))
            # Add a Progress column to show per-package progress / percent
            self.pkg_tree = ttk.Treeview(frm_pkg, columns=('pkg', 'status', 'progress'), show='headings', height=8)
            self.pkg_tree.heading('pkg', text='Package')
            self.pkg_tree.column('pkg', anchor='w')
            self.pkg_tree.heading('status', text='Status')
            self.pkg_tree.column('status', width=140, anchor='center')
            self.pkg_tree.heading('progress', text='Progress')
            self.pkg_tree.column('progress', width=100, anchor='center')
            self.pkg_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
            pkg_scr = ttk.Scrollbar(frm_pkg, orient=tk.VERTICAL, command=self.pkg_tree.yview)
            pkg_scr.pack(side=tk.LEFT, fill=tk.Y)
            self.pkg_tree.config(yscrollcommand=pkg_scr.set)
            frm_pkg_btn = ttk.Frame(frm_pkg)
            frm_pkg_btn.pack(fill=tk.X, padx=6, pady=6)
            self.btn_pkg_refresh = ttk.Button(frm_pkg_btn, text='Refresh', command=self._refresh_packages)
            self.btn_pkg_refresh.pack(side=tk.LEFT)
            self.btn_pkg_install_selected = ttk.Button(frm_pkg_btn, text='Install Selected', command=self._install_selected_packages)
            self.btn_pkg_install_selected.pack(side=tk.LEFT, padx=(6, 0))
            self.btn_pkg_install_all = ttk.Button(frm_pkg_btn, text='Install All', command=self._install_all_packages)
            self.btn_pkg_install_all.pack(side=tk.LEFT, padx=(6, 0))
            self.btn_pkg_install_uninstalled = ttk.Button(frm_pkg_btn, text='Install Uninstalled', command=self._install_uninstalled_packages)
            self.btn_pkg_install_uninstalled.pack(side=tk.LEFT, padx=(6, 0))
            # Progress indicator (spinner) for package installs
            self.pkg_progress = ttk.Progressbar(frm_pkg_btn, mode='indeterminate', length=120)
            # hidden initially
            self.pkg_progress.pack(side=tk.RIGHT, padx=(6, 0))
            self.pkg_progress.pack_forget()

            # Current package status and progress (per-package indicator)
            frm_pkg_status = ttk.Frame(frm_pkg)
            frm_pkg_status.pack(fill=tk.X, padx=6, pady=(6, 4))
            self.lbl_pkg_current = ttk.Label(frm_pkg_status, text='')
            self.lbl_pkg_current.pack(side=tk.LEFT)
            self.cur_pkg_progress = ttk.Progressbar(frm_pkg_status, mode='indeterminate', length=200)
            # hidden initially (shown when package installs)
            # Install log (shows terminal output)
            frm_pkg_log = ttk.Frame(frm_pkg)
            frm_pkg_log.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 6))
            self.pkg_log_text = tk.Text(frm_pkg_log, height=8, wrap='none')
            self.pkg_log_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
            pkg_log_scr = ttk.Scrollbar(frm_pkg_log, orient=tk.VERTICAL, command=self.pkg_log_text.yview)
            pkg_log_scr.pack(side=tk.LEFT, fill=tk.Y)
            self.pkg_log_text.config(yscrollcommand=pkg_log_scr.set)
            frm_log_btn = ttk.Frame(frm_pkg_log)
            frm_log_btn.pack(fill=tk.X, padx=6, pady=(6, 0))
            ttk.Button(frm_log_btn, text='Clear Log', command=self._clear_pkg_log).pack(side=tk.LEFT)
            ttk.Button(frm_log_btn, text='Save Log', command=self._save_pkg_log).pack(side=tk.LEFT, padx=(6, 0))
        except Exception:
            pass

        # Progress bar and styles
        self.style = ttk.Style(self)
        try:
            # ensure current theme is initialized
            self.style.theme_use(self.style.theme_use())
        except Exception:
            pass
        self.success_style = 'Green.Horizontal.TProgressbar'
        self.default_style = 'Horizontal.TProgressbar'
        try:
            # Configure a green style for success (may vary between platforms)
            self.style.configure(self.success_style, troughcolor='#dff0d8', background='#4caf50')
        except Exception:
            pass

        self.progress = ttk.Progressbar(tab_trips, orient=tk.HORIZONTAL, mode='determinate', style=self.default_style)
        self.progress.pack(fill=tk.X, padx=10)

        self.status_label = ttk.Label(tab_trips, textvariable=self.status_text)
        self.status_label.pack(fill=tk.X, padx=10, pady=(6, 10))

        # Buttons below progress bar (new dedicated row)
        frm_bottom = ttk.Frame(tab_trips)
        frm_bottom.pack(fill=tk.X, padx=10, pady=(6, 8))
        self.render_btn = ttk.Button(frm_bottom, text="Render Selected", command=self._on_render)
        self.render_btn.pack(side=tk.RIGHT)
        self.stop_btn = ttk.Button(frm_bottom, text="Stop", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # Load initial config text and package status
        try:
            self._load_config_text()
        except Exception:
            pass
        try:
            self._refresh_packages()
        except Exception:
            pass

    def _on_date_mode_change(self):
        """Enable/disable Year vs Date range widgets depending on selected mode.

        Mode is stored in self.filter_date_mode ("year" or "date"). When mode changes,
        the other widgets are disabled to make the choice mutually exclusive. We also
        trigger a reload of the trip list so the UI reflects the new filter.
        """
        mode = None
        try:
            mode = self.filter_date_mode.get()
        except Exception:
            mode = 'year'

        # Year combobox
        try:
            if mode == 'year':
                self.cmb_year.configure(state='normal')
            else:
                self.cmb_year.configure(state='disabled')
        except Exception:
            pass

        # Date widgets (calendar or simple entry)
        try:
            if HAVE_TKCALENDAR and hasattr(self, 'start_cal') and hasattr(self, 'end_cal'):
                st = 'normal' if mode == 'date' else 'disabled'
                try:
                    self.start_cal.configure(state=st)
                    self.end_cal.configure(state=st)
                except Exception:
                    # Some DateEntry implementations may use 'readonly' for normal; ignore if fails
                    pass
            else:
                st = 'normal' if mode == 'date' else 'disabled'
                try:
                    self.ent_start.configure(state=st)
                    self.ent_end.configure(state=st)
                except Exception:
                    pass
        except Exception:
            pass

        # Refresh the list so the active filter mode is applied immediately
        try:
            self.load_trips()
        except Exception:
            pass

    def _on_toggle_date_mode(self):
        """Handler for the toggle Checkbutton. Keeps `filter_date_mode` string in sync
        with the boolean toggle `filter_date_is_date` and updates widget states."""
        try:
            if self.filter_date_is_date.get():
                self.filter_date_mode.set('date')
            else:
                self.filter_date_mode.set('year')
        except Exception:
            pass
        try:
            self._on_date_mode_change()
        except Exception:
            pass

    def _on_browse(self):
        path = filedialog.askdirectory(initialdir=self.bsp_path.get() or str(DEFAULT_BSP))
        if path:
            self.bsp_path.set(path)
            self.load_trips()

    # Config editor
    def _load_config_text(self):
        cfg_file = SCRIPT_DIR / 'config.toml'
        try:
            if cfg_file.exists():
                txt = cfg_file.read_text(encoding='utf-8')
            else:
                txt = '# config.toml not found. Create settings here.'
            self.cfg_text.delete('1.0', tk.END)
            self.cfg_text.insert('1.0', txt)
        except Exception as e:
            messagebox.showerror('Error', f'Could not load config: {e}')

    def _save_config_text(self):
        cfg_file = SCRIPT_DIR / 'config.toml'
        try:
            content = self.cfg_text.get('1.0', tk.END)
            cfg_file.write_text(content, encoding='utf-8')
            messagebox.showinfo('Saved', 'Configuration saved to config.toml')
        except Exception as e:
            messagebox.showerror('Error', f'Could not save config: {e}')

    # Package log utilities
    def _clear_pkg_log(self):
        try:
            self.pkg_log_text.delete('1.0', tk.END)
        except Exception:
            pass

    def _save_pkg_log(self):
        try:
            path = filedialog.asksaveasfilename(defaultextension='.log', filetypes=[('Log files', '*.log'), ('All files', '*.*')])
            if not path:
                return
            content = self.pkg_log_text.get('1.0', tk.END)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            messagebox.showinfo('Saved', f'Log saved to {path}')
        except Exception as e:
            messagebox.showerror('Error', f'Could not save log: {e}')

    # Package manager
    def _refresh_packages(self):
        try:
            self.pkg_tree.delete(*self.pkg_tree.get_children())
            pkgs = []
            req = SCRIPT_DIR / 'requirements.txt'
            if req.exists():
                for line in req.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    name = line.split(';', 1)[0].strip()
                    for sep in ('==', '>=', '<=', '>', '<', '~='):
                        if sep in name:
                            name = name.split(sep, 1)[0]
                    if '[' in name:
                        name = name.split('[', 1)[0]
                    pkgs.append(name)
            # optional helpful packages
            for o in ('tkcalendar', 'playwright'):
                if o not in pkgs:
                    pkgs.append(o)
            for p in pkgs:
                status = 'unknown'
                try:
                    if version:
                        try:
                            v = version(p)
                            status = f'installed ({v})'
                        except PackageNotFoundError:
                            status = 'not installed'
                except Exception:
                    status = 'unknown'
                # progress empty by default (will be updated during install)
                self.pkg_tree.insert('', 'end', iid=p, values=(p, status, ''))
        except Exception:
            pass

    def _install_selected_packages(self):
        sel = self.pkg_tree.selection()
        if not sel:
            messagebox.showinfo('No selection', 'Please select one or more packages to install.')
            return
        pkgs = [self.pkg_tree.item(i)['values'][0] for i in sel]
        # notify UI to lock package controls and show spinner
        self.log_queue.put(('pkg_install_start', None))
        threading.Thread(target=self._install_packages_worker, args=(pkgs,), daemon=True).start()

    def _install_all_packages(self):
        pkgs = [self.pkg_tree.item(i)['values'][0] for i in self.pkg_tree.get_children()]
        self.log_queue.put(('pkg_install_start', None))
        threading.Thread(target=self._install_packages_worker, args=(pkgs,), daemon=True).start()

    def _install_uninstalled_packages(self):
        # install only packages that are currently marked as 'not installed' or 'unknown'
        pkgs = []
        for i in self.pkg_tree.get_children():
            vals = self.pkg_tree.item(i).get('values', [])
            if not vals:
                continue
            name = vals[0]
            status = str(vals[1]).lower() if len(vals) > 1 else ''
            if 'not installed' in status or 'unknown' in status:
                pkgs.append(name)
        if not pkgs:
            messagebox.showinfo('Nothing to install', 'No uninstalled packages found.')
            return
        self.log_queue.put(('pkg_install_start', None))
        threading.Thread(target=self._install_packages_worker, args=(pkgs,), daemon=True).start()

    def _install_packages_worker(self, pkgs):
        # run installs and collect results
        successes = []
        failures = []
        total = len(pkgs)
        # notify start with total count
        self.log_queue.put(('pkg_install_start', {'total': total}))
        self.log_queue.put(('status', f'Installing {total} package(s)...'))
        for p in pkgs:
            self.log_queue.put(('pkg_progress', {'pkg': p, 'status': 'start'}))
            rc = self._run_pip_install(p)
            if rc == 0:
                successes.append(p)
                self.log_queue.put(('pkg_progress', {'pkg': p, 'status': 'installed'}))
                self.log_queue.put(('info', f'Installed {p}'))
            else:
                failures.append((p, rc))
                self.log_queue.put(('pkg_progress', {'pkg': p, 'status': 'failed', 'rc': rc}))
                self.log_queue.put(('error', f'Failed to install {p} (exit {rc})'))

            # refresh package statuses after each install
            self.log_queue.put(('refresh_packages', None))
        # Signal completion with summary
        self.log_queue.put(('pkg_install_done', {'success': successes, 'failed': failures}))

    def _run_pip_install(self, pkg):
        """Run pip install for a single package using subprocess.communicate() with timeout."""
        import re
        cmd = [
            sys.executable,
            '-m',
            'pip',
            'install',
            '--disable-pip-version-check',
            '--no-input',
            pkg
        ]
        try:
            self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': f'Running: {" ".join(cmd)}'}))
        except Exception:
            pass

        rc = 1
        try:
            # Emit pip version info (helpful for debugging)
            try:
                pv = subprocess.run([sys.executable, '-m', 'pip', '--version'], capture_output=True, text=True, timeout=10)
                pip_version = pv.stdout.strip().splitlines()[0] if pv and pv.stdout else ''
                if pip_version:
                    self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': pip_version}))
            except Exception:
                pass

            # Use unbuffered Python env and a longer timeout to avoid spurious timeouts
            env = dict(os.environ)
            env['PYTHONUNBUFFERED'] = '1'

            start = time.time()
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            try:
                # increased timeout (10 minutes)
                stdout_data, _ = proc.communicate(timeout=600)
                rc = proc.returncode
                duration = time.time() - start

                # Log all output lines (if any)
                if stdout_data:
                    for line in stdout_data.splitlines():
                        ln = line.rstrip()
                        if ln:
                            try:
                                self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': ln}))
                            except Exception:
                                pass
                            # extract percent
                            try:
                                m = re.search(r"(\d{1,3})%", ln)
                                if m:
                                    pct = int(m.group(1))
                                    self.log_queue.put(('pkg_percent', {'pkg': pkg, 'percent': pct}))
                            except Exception:
                                pass
                else:
                    try:
                        self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': '(no pip output)'}))
                    except Exception:
                        pass

                try:
                    self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': f'Process exited with code {rc} (took {duration:.1f}s)'}))
                except Exception:
                    pass
            except subprocess.TimeoutExpired:
                # Attempt to kill and read remaining output
                try:
                    proc.kill()
                    out, _ = proc.communicate(timeout=5)
                except Exception:
                    out = ''
                if out:
                    for line in out.splitlines():
                        ln = line.rstrip()
                        if ln:
                            try:
                                self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': ln}))
                            except Exception:
                                pass
                try:
                    self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': 'Install timed out after 600s'}))
                except Exception:
                    pass
                rc = 1
        except Exception as e:
            try:
                self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': f'Install error: {e}'}))
            except Exception:
                pass
            rc = 1
        except Exception as e:
            try:
                self.log_queue.put(('pkg_output', {'pkg': pkg, 'line': f'Install error: {e}'}))
            except Exception:
                pass
            rc = 1
        return rc

    def load_trips(self):
        # Clear tree and load
        for ch in self.trips_tree.get_children():
            self.trips_tree.delete(ch)
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

        # Apply filters
        show_all = bool(self.filter_show_all.get())

        mode = self.filter_date_mode.get() if hasattr(self, 'filter_date_mode') else 'year'

        year = None
        start_date = None
        end_date = None

        # If user selected Year mode, parse year and ignore date range
        if mode == 'year':
            year_text = self.filter_year.get().strip()
            if year_text:
                try:
                    year = int(year_text)
                except ValueError:
                    messagebox.showerror("Invalid year", "Please enter a valid year (e.g., 2025) or leave empty.")
                    return

        # If user selected Date mode, parse start/end and ignore year
        elif mode == 'date':
            try:
                if HAVE_TKCALENDAR and hasattr(self, 'start_cal'):
                    sd = self.start_cal.get_date()
                    if sd:
                        start_date = datetime(sd.year, sd.month, sd.day)
                else:
                    if self.filter_start_date.get().strip():
                        sd = datetime.strptime(self.filter_start_date.get().strip(), "%d.%m.%Y")
                        start_date = sd

                if HAVE_TKCALENDAR and hasattr(self, 'end_cal'):
                    ed = self.end_cal.get_date()
                    if ed:
                        end_date = datetime(ed.year, ed.month, ed.day)
                else:
                    if self.filter_end_date.get().strip():
                        ed = datetime.strptime(self.filter_end_date.get().strip(), "%d.%m.%Y")
                        end_date = ed
            except Exception:
                messagebox.showerror("Invalid date", "Dates must be dd.mm.yyyy or selected from the calendar.")
                return

        # Unknown mode: be permissive and don't filter by date/year
        else:
            pass

        filtered = m.filter_trips_by_date(trips, year=year, start_date=start_date, end_date=end_date)
        if not show_all:
            filtered = [t for t in filtered if not cm.is_rendered(t)]

        # store filtered list for accurate selection to trips mapping
        self._filtered_trips = filtered

        for idx, t in enumerate(filtered):
            display = t.name
            # attempt nicer name from trip.json if available
            try:
                parser = m.TripParser(t)
                parser.load()
                name = parser.get_trip_name()
                display = f"{name} — {t.name}"
            except Exception:
                pass
            rendered_mark = '✅' if cm.is_rendered(t) else ''
            # Use index as iid to map selection back to filtered list
            self.trips_tree.insert('', 'end', iid=str(idx), values=(rendered_mark, display))

    def _select_all(self):
        # select all visible items in the tree
        for ch in self.trips_tree.get_children():
            self.trips_tree.selection_add(ch)

    def _deselect_all(self):
        # clear selection
        try:
            self.trips_tree.selection_set(())
        except Exception:
            pass

    def _select_unrendered(self):
        """Select only the trips that are not yet rendered (uses cache)."""
        try:
            cm = m.CacheManager(SCRIPT_DIR / 'cache' / 'rendered_trips_cache.json')
            source = getattr(self, '_filtered_trips', None) or getattr(self, '_trips', [])
            # clear selection first
            try:
                self.trips_tree.selection_set(())
            except Exception:
                pass
            for iid in self.trips_tree.get_children():
                try:
                    idx = int(iid)
                    trip = source[idx]
                    if not cm.is_rendered(trip):
                        self.trips_tree.selection_add(iid)
                except Exception:
                    # ignore malformed iids or index errors
                    pass
        except Exception:
            try:
                # fallback: select none
                self.trips_tree.selection_set(())
            except Exception:
                pass

    def _on_tree_motion(self, event):
        # Show tooltip when hovering over the rendered heading (#1)
        try:
            region = self.trips_tree.identify_region(event.x, event.y)
            col = self.trips_tree.identify_column(event.x)
            if region == 'heading' and col == '#1' and self._rendered_tooltip:
                # position tooltip slightly offset from cursor
                self._rendered_tooltip.show(event.x_root + 10, event.y_root + 10)
            else:
                if self._rendered_tooltip:
                    self._rendered_tooltip.hide()
        except Exception:
            try:
                if self._rendered_tooltip:
                    self._rendered_tooltip.hide()
            except Exception:
                pass

    def _on_render(self):
        sel = self.trips_tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Please select one or more trips to render.")
            return
        # Map selection iids (indices) to the filtered trips list (if available)
        source = getattr(self, '_filtered_trips', None) or getattr(self, '_trips', [])
        try:
            trips = [source[int(iid)] for iid in sel]
        except Exception:
            messagebox.showerror("Selection error", "Could not map selection to trips.")
            return

        # Parse config overrides from input (simple comma-separated k=v pairs)
        cfg_text = self.filter_config_overrides.get().strip()
        cfg_overrides = {}
        if cfg_text:
            import ast
            for part in cfg_text.split(','):
                if '=' not in part:
                    continue
                k, v = part.split('=', 1)
                key = k.strip()
                val_s = v.strip()
                try:
                    val = ast.literal_eval(val_s)
                except Exception:
                    # fallback to string/number/bool
                    sval = val_s.strip('"').strip("'")
                    if sval.lower() in ('true', 'false'):
                        val = True if sval.lower() == 'true' else False
                    else:
                        try:
                            if '.' in sval:
                                val = float(sval)
                            else:
                                val = int(sval)
                        except Exception:
                            val = sval
                cfg_overrides[key] = val

        # store config overrides to be merged by worker
        self._current_config_overrides = cfg_overrides

        # disable controls
        self.render_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.stop_flag.clear()
        # Track total trips for returning to trip-level progress
        self._render_total_trips = len(trips)
        self.progress['maximum'] = self._render_total_trips
        self.progress['value'] = 0
        # Ensure progress uses default (non-success) style
        try:
            self.progress['style'] = self.default_style
        except Exception:
            pass
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

            # Merge GUI-provided config overrides (if any)
            try:
                gui_overrides = getattr(self, '_current_config_overrides', {}) or {}
                if isinstance(gui_overrides, dict):
                    config.update(gui_overrides)
            except Exception:
                pass

            # Ensure language managers are initialized like in CLI so PDF strings are translated
            try:
                gui_lang = m.load_language_manager(config.get("language", "en"), SCRIPT_DIR)
                # Update module default language manager used when no explicit lang is passed
                m._DEFAULT_LANGUAGE_MANAGER = gui_lang
                config["_language_code"] = gui_lang.language_code
                pdf_lang_code = config.get("pdf_language", "").strip()
                if not pdf_lang_code:
                    pdf_lang_code = config.get("language", gui_lang.language_code)
                pdf_lang = m.load_language_manager(pdf_lang_code, SCRIPT_DIR)
                config["_pdf_language_manager"] = pdf_lang
                config["_pdf_language_code"] = pdf_lang.language_code
            except Exception:
                pass

            total = len(trips)
            done = 0
            for idx, trip in enumerate(trips, start=1):
                if self.stop_flag.is_set():
                    self.log_queue.put(("status", "Stopped by user"))
                    break
                self.log_queue.put(("status", f"Rendering {idx}/{total}: {trip.name}"))
                try:
                    # Pass a progress callback to receive per-step updates (current, total, trip_name)
                    res = m.render_trip(
                        trip,
                        SCRIPT_DIR,
                        config,
                        cm,
                        check_stop=lambda: self.stop_flag.is_set(),
                        progress_callback=lambda cur, tot, _name=trip.name: self.log_queue.put(("step_progress", (cur, tot, _name)))
                    )
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
                        # Refresh visible list so rendered marks update
                        self.log_queue.put(("refresh_list", None))
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
                    # Trip-level progress (number of trips completed)
                    try:
                        total = getattr(self, '_render_total_trips', None)
                        if total:
                            self.progress['maximum'] = total
                        self.progress['value'] = payload
                        # ensure normal style
                        try:
                            self.progress['style'] = self.default_style
                        except Exception:
                            pass
                    except Exception:
                        pass
                elif typ == "step_progress":
                    try:
                        cur, tot, name = payload
                        # Switch progress bar to per-step mode for current trip
                        self.progress['maximum'] = tot
                        self.progress['value'] = cur
                        # ensure normal style
                        try:
                            self.progress['style'] = self.default_style
                        except Exception:
                            pass
                        self.status_text.set(f"{name}: Step {cur}/{tot}")
                    except Exception:
                        pass
                elif typ == "done":
                    # All rendering finished: show success and fill progress green
                    self.status_text.set("Done")
                    try:
                        # if we know total trips, set progress to its maximum
                        total = getattr(self, '_render_total_trips', None)
                        if total:
                            self.progress['maximum'] = total
                            self.progress['value'] = total
                        # apply success style
                        try:
                            self.progress['style'] = self.success_style
                        except Exception:
                            pass
                    except Exception:
                        pass
                    self.render_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                elif typ == "refresh_list":
                    # Refresh the displayed list (e.g., after a trip finished rendering)
                    try:
                        self.load_trips()
                    except Exception:
                        pass
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
                elif typ == 'refresh_packages':
                    try:
                        self._refresh_packages()
                    except Exception:
                        pass
                elif typ == 'pkg_install_start':
                    # disable package controls and configure overall progress bar
                    try:
                        total = (payload or {}).get('total', None)
                        self.btn_pkg_refresh.config(state=tk.DISABLED)
                        self.btn_pkg_install_selected.config(state=tk.DISABLED)
                        self.btn_pkg_install_all.config(state=tk.DISABLED)
                        try:
                            self.btn_pkg_install_uninstalled.config(state=tk.DISABLED)
                        except Exception:
                            pass
                        # switch overall progress to determinate
                        try:
                            if total and isinstance(total, int) and total > 0:
                                self.pkg_progress.config(mode='determinate', maximum=total)
                                self.pkg_progress['value'] = 0
                                self.pkg_progress.pack(side=tk.RIGHT, padx=(6, 0))
                            else:
                                self.pkg_progress.config(mode='indeterminate')
                                self.pkg_progress.pack(side=tk.RIGHT, padx=(6, 0))
                                self.pkg_progress.start(10)
                        except Exception:
                            pass
                        # init counters
                        self._pkg_installed_count = 0
                        self._pkg_total = total or 0
                        self._pkg_current_percent = {}
                    except Exception:
                        pass
                elif typ == 'pkg_install_done':
                    try:
                        self.btn_pkg_refresh.config(state=tk.NORMAL)
                        self.btn_pkg_install_selected.config(state=tk.NORMAL)
                        self.btn_pkg_install_all.config(state=tk.NORMAL)
                        try:
                            self.btn_pkg_install_uninstalled.config(state=tk.NORMAL)
                        except Exception:
                            pass
                        try:
                            self.pkg_progress.stop()
                            self.pkg_progress.pack_forget()
                        except Exception:
                            pass
                        summary = payload or {}
                        succ = summary.get('success', [])
                        failed = summary.get('failed', [])
                        if failed:
                            msg = f"Installed: {len(succ)}\nFailed: {len(failed)}\n\nSee status messages for details."
                            messagebox.showwarning('Installation finished with errors', msg)
                        else:
                            msg = f"Successfully installed {len(succ)} package(s)."
                            messagebox.showinfo('Installation complete', msg)
                        # refresh package list one more time
                        try:
                            self._refresh_packages()
                        except Exception:
                            pass
                    except Exception:
                        pass
                elif typ == 'pkg_output':
                    try:
                        info = payload or {}
                        pkg = info.get('pkg')
                        line = info.get('line', '')
                        try:
                            self.pkg_log_text.insert(tk.END, f"[{pkg}] {line}\n")
                            self.pkg_log_text.see(tk.END)
                        except Exception:
                            pass
                    except Exception:
                        pass
                elif typ == 'pkg_percent':
                    try:
                        info = payload or {}
                        pkg = info.get('pkg')
                        pct = int(info.get('percent', 0))
                        try:
                            # update per-row progress
                            self.pkg_tree.item(pkg, values=(pkg, 'installing...', f"{pct}%"))
                        except Exception:
                            pass
                        # update overall progress: installed_count + current pct
                        try:
                            base = getattr(self, '_pkg_installed_count', 0)
                            total = getattr(self, '_pkg_total', 0) or 1
                            # fractional value = base + pct/100
                            val = base + (pct / 100.0)
                            # scale overall progress bar to match this fractional value
                            if getattr(self, 'pkg_progress', None) is not None and self.pkg_progress['mode'] == 'determinate':
                                self.pkg_progress['value'] = val
                        except Exception:
                            pass
                    except Exception:
                        pass
                elif typ == 'pkg_progress':
                    try:
                        info = payload or {}
                        pkg = info.get('pkg')
                        status = info.get('status')
                        if status == 'start':
                            try:
                                self.lbl_pkg_current.config(text=f"Installing: {pkg}")
                                self.cur_pkg_progress.pack(side=tk.LEFT, padx=(6, 0))
                                self.cur_pkg_progress.start(10)
                                # update status and progress columns
                                try:
                                    self.pkg_tree.item(pkg, values=(pkg, 'installing...', '0%'))
                                except Exception:
                                    pass
                                # initialize spinner state for this pkg
                                try:
                                    self._pkg_spinner_state[pkg] = 0
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        elif status == 'installed':
                            try:
                                self.cur_pkg_progress.stop()
                                self.cur_pkg_progress.pack_forget()
                                self.lbl_pkg_current.config(text=f"Installed: {pkg}")
                                try:
                                    self.pkg_tree.item(pkg, values=(pkg, 'installed', '100%'))
                                except Exception:
                                    pass
                                # increment overall installed count and update overall bar
                                try:
                                    self._pkg_installed_count = getattr(self, '_pkg_installed_count', 0) + 1
                                    if getattr(self, 'pkg_progress', None) is not None and self.pkg_progress['mode'] == 'determinate':
                                        self.pkg_progress['value'] = self._pkg_installed_count
                                except Exception:
                                    pass
                                # clear spinner state
                                try:
                                    if pkg in self._pkg_spinner_state:
                                        del self._pkg_spinner_state[pkg]
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        elif status == 'failed':
                            try:
                                self.cur_pkg_progress.stop()
                                self.cur_pkg_progress.pack_forget()
                                self.lbl_pkg_current.config(text=f"Failed: {pkg}")
                                try:
                                    self.pkg_tree.item(pkg, values=(pkg, f"failed (rc {info.get('rc')})", ''))
                                except Exception:
                                    pass
                                # count failure as completed for overall bar
                                try:
                                    self._pkg_installed_count = getattr(self, '_pkg_installed_count', 0) + 1
                                    if getattr(self, 'pkg_progress', None) is not None and self.pkg_progress['mode'] == 'determinate':
                                        self.pkg_progress['value'] = self._pkg_installed_count
                                except Exception:
                                    pass
                                try:
                                    if pkg in self._pkg_spinner_state:
                                        del self._pkg_spinner_state[pkg]
                                except Exception:
                                    pass
                            except Exception:
                                pass
                    except Exception:
                        pass
                else:
                    self.status_text.set(str(payload))
        except queue.Empty:
            pass
        finally:
            # Update simple spinner for any package still marked 'installing...' without percent
            try:
                # spinner characters
                chars = ['|', '/', '-', '\\']
                if not hasattr(self, '_pkg_spinner_state'):
                    self._pkg_spinner_state = {}
                for iid in self.pkg_tree.get_children():
                    try:
                        vals = self.pkg_tree.item(iid).get('values', [])
                        if not vals or len(vals) < 3:
                            continue
                        name = vals[0]
                        status = str(vals[1]).lower()
                        progress = str(vals[2])
                        if 'installing' in status and not progress.endswith('%'):
                            idx = self._pkg_spinner_state.get(name, 0)
                            idx = (idx + 1) % len(chars)
                            self._pkg_spinner_state[name] = idx
                            try:
                                self.pkg_tree.item(name, values=(name, vals[1], chars[idx]))
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                pass
            self.after(200, self._poll_queue)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
