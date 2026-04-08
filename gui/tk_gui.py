"""Simple Tkinter GUI for PolarstepsPDFCreator

Features:
- Choose Polarsteps Data folder
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

# Optional: matplotlib for GUI charts (Agg backend for headless export)
# Matplotlib is optional for the GUI; Pylance may warn when it's not installed.
# Use type-ignore comments to suppress unresolved import diagnostics in editor
try:
    import matplotlib  # type: ignore[reportMissingModuleSource]
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt  # type: ignore[reportMissingModuleSource]
    HAVE_MATPLOTLIB = True
except Exception:
    # Graceful fallback when matplotlib is not present at runtime
    plt = None
    HAVE_MATPLOTLIB = False

import io
import json
import re

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception:
    raise RuntimeError("Tkinter is required to run the GUI on this platform.")

# Optional calendar widget for improved date input in the GUI
try:
    from tkcalendar import DateEntry
    HAVE_TKCALENDAR = True

    class UpwardDateEntry(DateEntry):
        """DateEntry that prefers opening the popup above the entry."""
        def drop_down(self):
            """Display or withdraw the drop-down calendar with upward placement."""
            if self._calendar.winfo_ismapped():
                self._top_cal.withdraw()
            else:
                self._validate_date()
                date = self.parse_date(self.get())
                x = self.winfo_rootx()
                y_below = self.winfo_rooty() + self.winfo_height()

                # So that it stays in the window, prefer above position.
                self._top_cal.update_idletasks()
                cal_height = self._top_cal.winfo_height() or self._top_cal.winfo_reqheight() or self._calendar.winfo_reqheight()
                y_above = self.winfo_rooty() - cal_height
                y = y_above if y_above >= 0 else y_below

                if self.winfo_toplevel().attributes('-topmost'):
                    self._top_cal.attributes('-topmost', True)
                else:
                    self._top_cal.attributes('-topmost', False)
                self._top_cal.geometry('+%i+%i' % (x, y))
                self._top_cal.deiconify()
                self._calendar.focus_set()
                self._calendar.selection_set(date)

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
# default path used when no folder has been chosen (folder name remains BSPData for compatibility)
DEFAULT_BSP = SCRIPT_DIR / "BSPData"


class ScrollableFrame(ttk.Frame):
    """A simple scrollable frame that keeps its inner frame width in sync
    with the canvas and supports mousewheel scrolling when the pointer is over it."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._canvas = tk.Canvas(self, highlightthickness=0)
        self._vscroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vscroll.set)
        self._vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = ttk.Frame(self._canvas)
        self._window_id = self._canvas.create_window((0, 0), window=self.inner, anchor='nw')

        def _on_inner_config(e):
            try:
                self._canvas.configure(scrollregion=self._canvas.bbox('all'))
            except Exception:
                pass
        self.inner.bind('<Configure>', _on_inner_config)

        def _on_canvas_config(e):
            try:
                w = e.width
                if w > 10:
                    try:
                        self._canvas.itemconfig(self._window_id, width=w)
                    except Exception:
                        pass
            except Exception:
                pass
        self._canvas.bind('<Configure>', _on_canvas_config)

        # mouse wheel handling bound while mouse is over inner area
        def _on_mousewheel(e):
            delta = 0
            if getattr(e, 'delta', 0):
                delta = int(-1 * (e.delta / 120))
            else:
                num = getattr(e, 'num', None)
                if num == 4:
                    delta = -1
                elif num == 5:
                    delta = 1
            try:
                self._canvas.yview_scroll(delta, 'units')
            except Exception:
                pass

        def _bind_mousewheel():
            try:
                self.bind_all('<MouseWheel>', _on_mousewheel)
                self.bind_all('<Button-4>', _on_mousewheel)
                self.bind_all('<Button-5>', _on_mousewheel)
            except Exception:
                pass

        def _unbind_mousewheel():
            try:
                self.unbind_all('<MouseWheel>')
                self.unbind_all('<Button-4>')
                self.unbind_all('<Button-5>')
            except Exception:
                pass

        self.inner.bind('<Enter>', lambda e: _bind_mousewheel())
        self.inner.bind('<Leave>', lambda e: _unbind_mousewheel())


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        # load language manager early so UI strings come from language packs
        try:
            cfg = {}
            config_file = SCRIPT_DIR / 'config.toml'
            if config_file.exists():
                content = config_file.read_text(encoding='utf-8')
                if hasattr(m, '_tomllib') and m._tomllib:
                    cfg = m._tomllib.loads(content)
                else:
                    cfg = m._parse_simple_toml(content)
            lang_code = str(cfg.get('language', 'en') or 'en')
        except Exception:
            lang_code = 'en'
        try:
            self.lang = m.load_language_manager(lang_code, SCRIPT_DIR)
        except Exception:
            self.lang = m.load_language_manager('en', SCRIPT_DIR)

        self.title(self.lang.t('gui.app_title'))
        self.geometry("800x600")

        # initial paths can come from config file
        # support new key polarsteps_data_folder, fall back to bsp_folder for compatibility
        bsp_initial = cfg.get('polarsteps_data_folder') or cfg.get('bsp_folder') or str(DEFAULT_BSP)
        output_initial = cfg.get('output_folder') or str(SCRIPT_DIR / 'TripPdfs')
        self.bsp_path = tk.StringVar(value=str(bsp_initial))
        self.output_path = tk.StringVar(value=str(output_initial))
        self.output_path = tk.StringVar(value=str(SCRIPT_DIR / 'TripPdfs'))
        self.status_text = tk.StringVar(value=self.lang.t('gui.status_ready'))
        
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
        # ensure the GUI uses any path overrides from config
        try:
            # update bsp and output path if config changed
            cfg_bsp = cfg.get('bsp_folder')
            if cfg_bsp:
                self.bsp_path.set(str(cfg_bsp))
            cfg_out = cfg.get('output_folder')
            if cfg_out:
                self.output_path.set(str(cfg_out))
        except Exception:
            pass

        # Resize and place window inside visible desktop work area (taskbar-safe)
        try:
            self.update_idletasks()
            req_w = self.winfo_reqwidth()
            req_h = self.winfo_reqheight()

            # Defaults for non-Windows platforms or API failures
            work_x = 0
            work_y = 0
            work_w = self.winfo_screenwidth()
            work_h = self.winfo_screenheight()

            # On Windows, use desktop work area so taskbar is respected.
            if sys.platform == 'win32':
                try:
                    class _RECT(ctypes.Structure):
                        _fields_ = [
                            ('left', ctypes.c_long),
                            ('top', ctypes.c_long),
                            ('right', ctypes.c_long),
                            ('bottom', ctypes.c_long),
                        ]

                    rect = _RECT()
                    SPI_GETWORKAREA = 0x0030
                    ok = ctypes.windll.user32.SystemParametersInfoW(
                        SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
                    )
                    if ok:
                        work_x = int(rect.left)
                        work_y = int(rect.top)
                        work_w = max(1, int(rect.right - rect.left))
                        work_h = max(1, int(rect.bottom - rect.top))
                except Exception:
                    pass

            # Keep a small margin so shadows/title bar are always visible.
            max_w = max(500, work_w - 24)
            max_h = max(400, work_h - 24)
            w = min(req_w + 40, max_w)
            # Start at half of the desktop work area height so the full window stays visible.
            h = min(max_h, max(400, work_h // 2))

            x = work_x + max(0, (work_w - w) // 2)
            y = work_y + max(0, (work_h - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
            try:
                # Allow shrinking below start size while keeping UI usable.
                self.minsize(min(700, max_w), min(500, max_h))
            except Exception:
                pass

            # Start maximized/fullscreen for best visibility by default.
            try:
                # Windows and many Tk themes support zoomed state
                self.state('zoomed')
            except Exception:
                try:
                    self.attributes('-zoomed', True)
                except Exception:
                    try:
                        self.attributes('-fullscreen', True)
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
            messagebox.showwarning(self.lang.t('gui.playwright_missing_title'), self.lang.t('render.playwright_missing'))

        # automatic update check
        if cfg.get('auto_update', False):
            # schedule a short delay so UI finishes initialization
            self.after(1000, self._on_check_update)

        self.load_trips()

    def _create_widgets(self):
        # Notebook with two tabs: Trips and Settings
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tab_trips = ttk.Frame(self.notebook)
        tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(tab_trips, text=self.lang.t('gui.tab_trips'))
        self.notebook.add(tab_settings, text=self.lang.t('gui.tab_settings'))
        self.tab_trips = tab_trips

        frm_top = ttk.Frame(tab_trips)
        frm_top.pack(fill=tk.X, padx=10, pady=(10, 6))
        self.frm_top = frm_top

        ttk.Label(frm_top, text=self.lang.t('gui.bsp_folder')).pack(side=tk.LEFT)
        ttk.Entry(frm_top, textvariable=self.bsp_path, width=45).pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(frm_top, text=self.lang.t('gui.browse'), command=self._on_browse).pack(side=tk.LEFT)
        # note: multiple folders may be entered separated by semicolons or spaces
        # output folder chooser next to input (new)
        ttk.Label(frm_top, text=self.lang.t('gui.output_folder')).pack(side=tk.LEFT, padx=(20,0))
        ttk.Entry(frm_top, textvariable=self.output_path, width=45).pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(frm_top, text=self.lang.t('gui.browse'), command=self._on_browse_output).pack(side=tk.LEFT)
        # If Playwright is missing, show quick-install button
        if m.sync_playwright is None:
            self.playwright_btn = ttk.Button(frm_top, text=self.lang.t('gui.install_playwright'), command=self._on_install_playwright)
            self.playwright_btn.pack(side=tk.LEFT, padx=(6, 0))

        frm_mid = ttk.Frame(tab_trips)
        frm_mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self.frm_mid = frm_mid

        lbl = ttk.Label(frm_mid, text=self.lang.t('gui.available_trips_label'))
        lbl.pack(anchor=tk.W)
        self.lbl_available_trips = lbl

        # Treeview with multiple columns: rendered flag, trip name, date range, travel days, folder name
        # Keep list compact so control rows remain visible at half-screen window height.
        self.trips_tree = ttk.Treeview(frm_mid, columns=('rendered', 'trip', 'dates', 'days', 'folder'),
                          show='headings', selectmode=tk.EXTENDED, height=10)
        # prepare sort-state dict and current sort column
        self._sort_reverse = {}
        self._current_sort_col = None
        # remember base heading texts for arrows
        self._heading_texts = {
            'rendered': '✔',
            'trip': self.lang.t('gui.trip_name', default=self.lang.t('gui.trip')),
            'dates': self.lang.t('gui.column_dates', default='Dates'),
            'days': self.lang.t('gui.column_days', default='Days'),
            'folder': self.lang.t('gui.column_folder', default='Folder'),
        }
        # setup a highlight style for sorted column headings
        try:
            self.highlight_style = 'Sort.Selected.Treeview.Heading'
            self.style.configure(self.highlight_style, background='#ddeeff')
        except Exception:
            self.highlight_style = ''
        # Use a check symbol as header; make column narrow and non-stretching
        self.trips_tree.heading('rendered', text=self._heading_texts['rendered'], command=lambda c='rendered': self._on_heading_click(c))
        self.trips_tree.column('rendered', width=28, minwidth=24, anchor='center', stretch=False)
        # trip name column
        self.trips_tree.heading('trip', text=self._heading_texts['trip'], command=lambda c='trip': self._on_heading_click(c))
        self.trips_tree.column('trip', anchor='w')
        # date range column (start - end)
        self.trips_tree.heading('dates', text=self._heading_texts['dates'], command=lambda c='dates': self._on_heading_click(c))
        self.trips_tree.column('dates', anchor='w', width=140)
        # days column
        self.trips_tree.heading('days', text=self._heading_texts['days'], command=lambda c='days': self._on_heading_click(c))
        self.trips_tree.column('days', width=60, anchor='center', stretch=False)
        # folder name column
        self.trips_tree.heading('folder', text=self._heading_texts['folder'], command=lambda c='folder': self._on_heading_click(c))
        self.trips_tree.column('folder', anchor='w', width=120)
        self.trips_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scr = ttk.Scrollbar(frm_mid, orient=tk.VERTICAL, command=self.trips_tree.yview)
        scr.pack(side=tk.LEFT, fill=tk.Y)
        self.trips_tree.config(yscrollcommand=scr.set)

        # Tooltip for the rendered header
        try:
            self._rendered_tooltip = _Tooltip(self, self.lang.t('gui.rendered'))
            self.trips_tree.bind('<Motion>', self._on_tree_motion)
            self.trips_tree.bind('<Leave>', lambda e: self._rendered_tooltip.hide())
        except Exception:
            self._rendered_tooltip = None

        frm_controls = ttk.Frame(tab_trips)
        frm_controls.pack(fill=tk.X, padx=10, pady=(6, 10))
        self.frm_controls = frm_controls

        # Filters
        ttk.Label(frm_controls, text=self.lang.t('gui.filters')).pack(side=tk.LEFT)
        self.chk_show_all = ttk.Checkbutton(frm_controls, text=self.lang.t('gui.include_rendered'), variable=self.filter_show_all, command=self.load_trips)
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
            self._toggle_tooltip = _Tooltip(self, self.lang.t('gui.toggle_date_tooltip'))
            self.chk_date_toggle.bind('<Enter>', lambda e: self._toggle_tooltip.show(e.x_root + 10, e.y_root + 10))
            self.chk_date_toggle.bind('<Motion>', lambda e: self._toggle_tooltip.show(e.x_root + 10, e.y_root + 10))
            self.chk_date_toggle.bind('<Leave>', lambda e: self._toggle_tooltip.hide())
        except Exception:
            self._toggle_tooltip = None

        ttk.Label(frm_date, text=self.lang.t('gui.year')).grid(row=1, column=0, sticky='w')
        years = [''] + [str(y) for y in range(datetime.now().year, datetime.now().year - 30, -1)]
        self.cmb_year = ttk.Combobox(frm_date, width=6, values=years, textvariable=self.filter_year)
        self.cmb_year.grid(row=1, column=1, padx=(6, 4))

        if HAVE_TKCALENDAR:
            ttk.Label(frm_date, text=self.lang.t('gui.from')).grid(row=1, column=3, sticky='w', padx=(8, 0))
            self.start_cal = UpwardDateEntry(frm_date, width=12, date_pattern='dd.mm.yyyy')
            self.start_cal.grid(row=1, column=4, padx=(6, 4))
            ttk.Label(frm_date, text=self.lang.t('gui.to')).grid(row=1, column=5, sticky='w', padx=(8, 0))
            self.end_cal = UpwardDateEntry(frm_date, width=12, date_pattern='dd.mm.yyyy')
            self.end_cal.grid(row=1, column=6, padx=(6, 0))
        else:
            ttk.Label(frm_date, text=self.lang.t('gui.from')).grid(row=1, column=3, sticky='w', padx=(8, 0))
            self.ent_start = ttk.Entry(frm_date, width=10, textvariable=self.filter_start_date)
            self.ent_start.grid(row=1, column=4, padx=(6, 4))
            ttk.Label(frm_date, text=self.lang.t('gui.to')).grid(row=1, column=5, sticky='w', padx=(8, 0))
            self.ent_end = ttk.Entry(frm_date, width=10, textvariable=self.filter_end_date)
            self.ent_end.grid(row=1, column=6, padx=(6, 0))
            self.lbl_cal_hint = ttk.Label(frm_date, text=self.lang.t('gui.install_tkcalendar_hint'), foreground='gray')
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

        ttk.Label(frm_controls, text=self.lang.t('gui.config')).pack(side=tk.LEFT, padx=(8, 0))
        self.ent_config = ttk.Entry(frm_controls, width=20, textvariable=self.filter_config_overrides)
        self.ent_config.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(frm_controls, text=self.lang.t('gui.apply'), command=self.load_trips).pack(side=tk.LEFT, padx=(6, 0))

        # Selection buttons in a grid for multiline visibility in smaller windows
        frm_select_buttons = ttk.Frame(frm_controls)
        frm_select_buttons.pack(fill=tk.X, side=tk.LEFT, padx=(12, 0), pady=(4, 0))
        self.btn_refresh = ttk.Button(frm_select_buttons, text=self.lang.t('gui.refresh'), command=self.load_trips)
        self.btn_refresh.grid(row=0, column=0, padx=(0, 6), pady=(0, 2), sticky='w')
        self.btn_select_all = ttk.Button(frm_select_buttons, text=self.lang.t('gui.select_all'), command=self._select_all)
        self.btn_select_all.grid(row=0, column=1, padx=(0, 6), pady=(0, 2), sticky='w')
        self.btn_deselect_all = ttk.Button(frm_select_buttons, text=self.lang.t('gui.deselect_all'), command=self._deselect_all)
        self.btn_deselect_all.grid(row=1, column=0, padx=(0, 6), pady=(0, 2), sticky='w')
        self.btn_select_unrendered = ttk.Button(frm_select_buttons, text=self.lang.t('gui.select_unrendered'), command=self._select_unrendered)
        self.btn_select_unrendered.grid(row=1, column=1, padx=(0, 6), pady=(0, 2), sticky='w')

        # Arrange in two rows so buttons remain visible even with narrow window width.
        frm_select_buttons.columnconfigure(0, weight=1)
        frm_select_buttons.columnconfigure(1, weight=1)

        # NOTE: Render / Stop buttons moved below the progress bar to a dedicated row

        # Settings tab: Config editor and package manager
        # Update controls (auto-check and manual check button)
        frm_update = ttk.LabelFrame(tab_settings, text=self.lang.t('gui.update_frame'))
        frm_update.pack(fill=tk.X, expand=False, padx=10, pady=(10, 6))
        # display auto-update setting (editable in main config form below)
        ttk.Label(frm_update, text=self.lang.t('gui.form.auto_update')).pack(side=tk.LEFT)
        ttk.Button(frm_update, text=self.lang.t('gui.check_update'), command=self._on_check_update).pack(side=tk.LEFT, padx=(6, 0))

        # Config editor (Graphical form + raw editor)
        frm_cfg = ttk.LabelFrame(tab_settings, text=self.lang.t('gui.config_frame'))
        frm_cfg.pack(fill=tk.BOTH, expand=False, padx=10, pady=(10, 6))

        # Container frame: use a simple frame so the form can occupy full width
        container = ttk.Frame(frm_cfg)
        container.pack(fill=tk.BOTH, expand=True)

        # Left: scrollable form (fills the container) using ScrollableFrame
        self.scrollable_form = ScrollableFrame(container)
        self.scrollable_form.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        # expose inner for the existing form builder
        self.form_inner = self.scrollable_form.inner
# mousewheel binding handled by the ScrollableFrame implementation

        # Build form widgets
        self._build_config_form()

        # Small helper label
        ttk.Label(frm_cfg, text=self.lang.t('gui.config_tip'), foreground='gray').pack(anchor='w', padx=8, pady=(4, 0))

        # Package manager
        frm_pkg = ttk.LabelFrame(tab_settings, text=self.lang.t('gui.packages'))
        frm_pkg.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 10))
        # Add a Progress column to show per-package progress / percent
        self.pkg_tree = ttk.Treeview(frm_pkg, columns=('pkg', 'status', 'progress'), show='headings', height=8)
        self.pkg_tree.heading('pkg', text=self.lang.t('gui.package'))
        self.pkg_tree.column('pkg', anchor='w')
        self.pkg_tree.heading('status', text=self.lang.t('gui.status'))
        self.pkg_tree.column('status', width=140, anchor='center')
        self.pkg_tree.heading('progress', text=self.lang.t('gui.progress'))
        self.pkg_tree.column('progress', width=100, anchor='center')
        self.pkg_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        pkg_scr = ttk.Scrollbar(frm_pkg, orient=tk.VERTICAL, command=self.pkg_tree.yview)
        pkg_scr.pack(side=tk.LEFT, fill=tk.Y)
        self.pkg_tree.config(yscrollcommand=pkg_scr.set)
        frm_pkg_btn = ttk.Frame(frm_pkg)
        frm_pkg_btn.pack(fill=tk.X, padx=6, pady=6)
        self.btn_pkg_refresh = ttk.Button(frm_pkg_btn, text=self.lang.t('gui.refresh'), command=self._refresh_packages)
        self.btn_pkg_refresh.pack(side=tk.LEFT)
        self.btn_pkg_install_selected = ttk.Button(frm_pkg_btn, text=self.lang.t('gui.install_selected'), command=self._install_selected_packages)
        self.btn_pkg_install_selected.pack(side=tk.LEFT, padx=(6, 0))
        self.btn_pkg_install_all = ttk.Button(frm_pkg_btn, text=self.lang.t('gui.install_all'), command=self._install_all_packages)
        self.btn_pkg_install_all.pack(side=tk.LEFT, padx=(6, 0))
        self.btn_pkg_install_uninstalled = ttk.Button(frm_pkg_btn, text=self.lang.t('gui.install_uninstalled'), command=self._install_uninstalled_packages)
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
        ttk.Button(frm_log_btn, text=self.lang.t('gui.clear_log'), command=self._clear_pkg_log).pack(side=tk.LEFT)
        ttk.Button(frm_log_btn, text=self.lang.t('gui.save_log'), command=self._save_pkg_log).pack(side=tk.LEFT, padx=(6, 0))

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
        self.frm_bottom = frm_bottom
        self.stats_btn = ttk.Button(frm_bottom, text=self.lang.t('gui.statistics'), command=self._on_show_statistics)
        self.stats_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self.combined_html_btn = ttk.Button(frm_bottom, text="Combined HTML", command=self._on_combined_html)
        self.combined_html_btn.pack(side=tk.RIGHT, padx=(6, 0))
        # add tooltip explaining button action
        try:
            # determine language for tooltip (read config if possible)
            tt_text = "Zeige Statistiken für ausgewählte oder gefilterte Reisen"
            try:
                cfg = {}
                config_file = SCRIPT_DIR / 'config.toml'
                if config_file.exists():
                    content = config_file.read_text(encoding='utf-8')
                    if hasattr(m, '_tomllib') and m._tomllib:
                        cfg = m._tomllib.loads(content)
                    else:
                        cfg = m._parse_simple_toml(content)
                lang_code = str(cfg.get('language', 'en') or 'en').strip() or 'en'
                try:
                    lang_mgr = m.load_language_manager(lang_code, SCRIPT_DIR)
                    tt_text = lang_mgr.t('gui.stats_button_tooltip', default=tt_text)
                except Exception:
                    pass
            except Exception:
                pass
            self._stats_tooltip = _Tooltip(self.stats_btn, tt_text)
            self.stats_btn.bind('<Enter>', lambda e: self._stats_tooltip.show(e.x_root + 10, e.y_root + 10))
            self.stats_btn.bind('<Leave>', lambda e: self._stats_tooltip.hide())
        except Exception:
            self._stats_tooltip = None
        self.render_btn = ttk.Button(frm_bottom, text=self.lang.t('gui.render_selected'), command=self._on_render)
        self.render_btn.pack(side=tk.RIGHT)
        self.stop_btn = ttk.Button(frm_bottom, text=self.lang.t('gui.stop'), command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # Load initial config text, form values and package status
        try:
            self._load_config_text()
        except Exception:
            pass
        try:
            self._load_config_form()
        except Exception:
            pass
        try:
            self._refresh_packages()
        except Exception:
            pass

        # Intercept combobox mousewheel at class level to prevent Combobox values from changing
        try:
            self.bind_class('TCombobox', '<MouseWheel>', self._combobox_mousewheel)
            self.bind_class('TCombobox', '<Button-4>', self._combobox_mousewheel)
            self.bind_class('TCombobox', '<Button-5>', self._combobox_mousewheel)
        except Exception:
            pass

        # Keep bottom controls visible: reduce visible trip rows when window is short.
        try:
            self._trip_rows_current = 10
            self._trip_resize_job = None
            self.bind('<Configure>', self._on_window_configure)
            self.after(100, self._adjust_trip_rows)
        except Exception:
            pass

    def _on_window_configure(self, event=None):
        try:
            if self._trip_resize_job is not None:
                self.after_cancel(self._trip_resize_job)
        except Exception:
            pass
        try:
            self._trip_resize_job = self.after(80, self._adjust_trip_rows)
        except Exception:
            pass

    def _adjust_trip_rows(self):
        """Dynamically reduce tree rows so lower control area remains visible."""
        try:
            self._trip_resize_job = None
            if not hasattr(self, 'trips_tree'):
                return

            try:
                self.update_idletasks()
            except Exception:
                pass

            content_h = 0
            try:
                content_h = int(self.tab_trips.winfo_height())
            except Exception:
                content_h = 0
            if content_h <= 1:
                total_h = int(self.winfo_height())
                if total_h <= 1:
                    return
                content_h = max(100, total_h - 90)

            # Reserve measured heights for all non-list controls in Trips tab.
            reserved_h = 0
            for name in ('frm_top', 'frm_controls', 'progress', 'status_label', 'frm_bottom'):
                try:
                    widget = getattr(self, name, None)
                    if widget is not None:
                        reserved_h += int(widget.winfo_reqheight())
                except Exception:
                    pass

            # Keep some room for tab/frame paddings and section spacing.
            reserved_h += 56

            # The list frame itself includes label + tree heading + paddings.
            list_overhead = 0
            try:
                list_overhead += int(self.lbl_available_trips.winfo_reqheight())
            except Exception:
                list_overhead += 24
            list_overhead += 36

            available_tree_h = content_h - reserved_h - list_overhead
            row_px = 24
            rows = max(1, min(18, int(available_tree_h // row_px)))

            # Safety fallback for very tight layouts.
            if available_tree_h < 90:
                rows = 1

            if rows != getattr(self, '_trip_rows_current', None):
                self.trips_tree.configure(height=rows)
                self._trip_rows_current = rows
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
        path = filedialog.askdirectory(initialdir=self.bsp_path.get().split(';')[0] or str(DEFAULT_BSP))
        if path:
            existing = self.bsp_path.get().strip()
            if existing:
                # append with semicolon separator
                self.bsp_path.set(existing + ";" + path)
            else:
                self.bsp_path.set(path)
            self.load_trips()

    def _on_browse_output(self):
        """Handler to browse for an output directory."""
        path = filedialog.askdirectory(initialdir=self.output_path.get() or str(SCRIPT_DIR / 'TripPdfs'))
        if path:
            self.output_path.set(path)

    # Config editor
    def _load_config_text(self):
        cfg_file = SCRIPT_DIR / 'config.toml'
        try:
            if cfg_file.exists():
                txt = cfg_file.read_text(encoding='utf-8')
            else:
                txt = '# config.toml not found. Create settings here.'
            # Cache raw content for comment-preserving edits (raw editor removed)
            self._original_config_text = txt
            self._original_config_lines = txt.splitlines()
            # quietly cache raw config on load (no popup) and update status text
            try:
                self.status_text.set(self.lang.t('gui.configuration_cached'))
            except Exception:
                pass
        except Exception as e:
            messagebox.showerror(self.lang.t('gui.error'), self.lang.t('gui.error_load_config').format(error=e))

    def _save_config_text(self):
        # Raw editor has been removed. Saving raw text is not available.
        messagebox.showinfo(self.lang.t('gui.not_available'), self.lang.t('gui.raw_editor_removed'))

    # --- Mousewheel helpers to ensure scrolling works when hovering controls ---
    def _hook_widget_for_mouse_scroll(self, widget):
        """Bind enter/leave on a widget so the mousewheel scrolls the config form while hovering.

        We bind wheel events directly on the hovered widget to scroll the form and
        return 'break' to prevent the widget from changing its own value (e.g. Spinbox/Combobox).
        """
        try:
            widget.bind('<Enter>', lambda e, w=widget: self._bind_scroll_for(w))
            widget.bind('<Leave>', lambda e, w=widget: self._unbind_scroll_for(w))
        except Exception:
            pass

    def _bind_scroll_for(self, widget):
        """Bind mousewheel for the widget and all its descendants to prevent widgets
        from changing their value and to scroll the form instead."""
        try:
            if not hasattr(self, '_mousebound_map'):
                self._mousebound_map = {}
            if widget in self._mousebound_map:
                return
            # collect widget and all descendants
            stack = [widget]
            bound = []
            while stack:
                w = stack.pop()
                bound.append(w)
                try:
                    children = w.winfo_children()
                    if children:
                        stack.extend(children)
                except Exception:
                    pass
            for w in bound:
                try:
                    w.bind('<MouseWheel>', self._widget_mousewheel)
                    w.bind('<Button-4>', self._widget_mousewheel)
                    w.bind('<Button-5>', self._widget_mousewheel)
                except Exception:
                    pass
            self._mousebound_map[widget] = bound
        except Exception:
            pass

    def _unbind_scroll_for(self, widget):
        """Unbind mousewheel handlers for the widget and its descendants."""
        try:
            if not hasattr(self, '_mousebound_map'):
                return
            bound = self._mousebound_map.pop(widget, None)
            if not bound:
                return
            for w in bound:
                try:
                    w.unbind('<MouseWheel>')
                    w.unbind('<Button-4>')
                    w.unbind('<Button-5>')
                except Exception:
                    pass
        except Exception:
            pass

    def _widget_mousewheel(self, event):
        """Scroll the form canvas and return 'break' to prevent widgets handling the wheel."""
        try:
            # If scrolling happens on a Listbox that's part of a Combobox popdown, treat it like a combobox
            try:
                w = event.widget
                cls = w.winfo_class()
                if cls == 'Listbox':
                    cur = w
                    while cur is not None:
                        try:
                            cname = cur.winfo_class()
                            if 'Combobox' in cname or 'Popdown' in cname:
                                # treat as combobox popdown
                                delta = 0
                                if getattr(event, 'delta', 0):
                                    delta = int(-1 * (event.delta / 120))
                                else:
                                    num = getattr(event, 'num', None)
                                    if num == 4:
                                        delta = -1
                                    elif num == 5:
                                        delta = 1
                                try:
                                    self.scrollable_form._canvas.yview_scroll(delta, 'units')
                                except Exception:
                                    pass
                                return 'break'
                        except Exception:
                            pass
                        cur = getattr(cur, 'master', None)
            except Exception:
                pass

            delta = 0
            if getattr(event, 'delta', 0):
                delta = int(-1 * (event.delta / 120))
            else:
                num = getattr(event, 'num', None)
                if num == 4:
                    delta = -1
                elif num == 5:
                    delta = 1
            try:
                self.scrollable_form._canvas.yview_scroll(delta, 'units')
            except Exception:
                pass
        except Exception:
            pass
        return 'break'

    def _combobox_mousewheel(self, event):
        """Class-level handler for Combobox mouse wheel that prevents changing the
        Combobox value and scrolls the form instead. Returns 'break' to stop default handling.
        """
        try:
            # determine widget under pointer
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            widget = self.winfo_containing(x, y)
            if widget is None:
                return 'break'
            # check if widget or any ancestor is a TCombobox
            cur = widget
            is_comb = False
            while cur is not None:
                try:
                    if cur.winfo_class() == 'TCombobox':
                        is_comb = True
                        break
                except Exception:
                    pass
                cur = getattr(cur, 'master', None)
            if not is_comb:
                return
            delta = 0
            if getattr(event, 'delta', 0):
                delta = int(-1 * (event.delta / 120))
            else:
                num = getattr(event, 'num', None)
                if num == 4:
                    delta = -1
                elif num == 5:
                    delta = 1
            try:
                self.scrollable_form._canvas.yview_scroll(delta, 'units')
            except Exception:
                pass
        except Exception:
            pass
        return 'break'

    # --- Graphical form helpers for config ---
    def _build_config_form(self):
        """Creates form widgets and stores variables in self.config_vars."""
        self.config_vars = {}
        # label widgets used for per-field validation feedback
        self.config_labels = {}
        # validation status label and save button will be created in the actions section

        def add_entry(parent, label, key, var_type='str', width=20, help_text=None):
            frm = ttk.Frame(parent)
            frm.pack(fill=tk.X, pady=2)
            lbl = ttk.Label(frm, text=label, width=28)
            lbl.pack(side=tk.LEFT)
            # keep reference to label for validation feedback
            self.config_labels[key] = lbl
            if var_type == 'bool':
                var = tk.BooleanVar()
                chk = ttk.Checkbutton(frm, variable=var)
                chk.pack(side=tk.LEFT)
            elif var_type == 'int':
                var = tk.IntVar()
                sp = ttk.Spinbox(frm, from_=-100000, to=100000, textvariable=var, width=8)
                sp.pack(side=tk.LEFT)
            elif var_type == 'float':
                var = tk.DoubleVar()
                ent = ttk.Entry(frm, textvariable=var, width=10)
                ent.pack(side=tk.LEFT)
            elif var_type == 'path':
                var = tk.StringVar()
                ent = ttk.Entry(frm, textvariable=var, width=36)
                ent.pack(side=tk.LEFT)
                def _browse_path():
                    p = filedialog.askopenfilename(initialdir=str(SCRIPT_DIR))
                    if p:
                        var.set(p)
                ttk.Button(frm, text=self.lang.t('gui.browse'), command=_browse_path).pack(side=tk.LEFT, padx=(6,0))
            elif var_type == 'combobox':
                var = tk.StringVar()
                cb = ttk.Combobox(frm, textvariable=var, width=14)
                cb.pack(side=tk.LEFT)
                # store and hook combobox for validation and mousewheel behavior
                self.config_vars[key] = var
                try:
                    self._hook_widget_for_mouse_scroll(cb)
                except Exception:
                    pass
                return var, cb
            else:
                var = tk.StringVar()
                ent = ttk.Entry(frm, textvariable=var, width=20)
                ent.pack(side=tk.LEFT)
            if help_text:
                ttk.Label(frm, text=help_text, foreground='gray').pack(side=tk.LEFT, padx=(6,0))
            self.config_vars[key] = var
            # reactively validate when field changes
            try:
                var.trace_add('write', lambda *a, k=key: self._on_var_change(k))
            except Exception:
                try:
                    var.trace('w', lambda *a, k=key: self._on_var_change(k))
                except Exception:
                    pass
            # Hook widgets for mousewheel scrolling when the pointer is over them
            try:
                self._hook_widget_for_mouse_scroll(frm)
                if 'ent' in locals():
                    self._hook_widget_for_mouse_scroll(ent)
                if 'sp' in locals():
                    self._hook_widget_for_mouse_scroll(sp)
                if 'cb' in locals():
                    self._hook_widget_for_mouse_scroll(cb)
                if 'chk' in locals():
                    self._hook_widget_for_mouse_scroll(chk)
            except Exception:
                pass
            return var

        # Groups
        grp_general = ttk.LabelFrame(self.form_inner, text=self.lang.t('gui.group_general'))
        grp_general.pack(fill=tk.X, padx=6, pady=(6,4))
        add_entry(grp_general, self.lang.t('gui.form.language'), 'language')
        add_entry(grp_general, self.lang.t('gui.form.pdf_language'), 'pdf_language')
        # new path settings for Polarsteps Data and output
        add_entry(grp_general, self.lang.t('gui.form.bsp_folder'), 'polarsteps_data_folder', var_type='path')
        add_entry(grp_general, self.lang.t('gui.form.output_folder'), 'output_folder', var_type='path')
        add_entry(grp_general, self.lang.t('gui.form.output_folder_pdf'), 'output_folder_pdf', var_type='path')
        add_entry(grp_general, self.lang.t('gui.form.output_folder_html'), 'output_folder_html', var_type='path')
        var, cb = add_entry(grp_general, self.lang.t('gui.form.renderer_mode'), 'renderer_mode', var_type='combobox')
        try:
            cb['values'] = ['pdf', 'html', 'both']
            var.set('both')
        except Exception:
            pass
        add_entry(grp_general, self.lang.t('gui.form.show_rendered_trips'), 'show_rendered_trips', var_type='bool')
        add_entry(grp_general, self.lang.t('gui.form.open_pdf_after_render'), 'open_pdf_after_render', var_type='bool')
        add_entry(grp_general, self.lang.t('gui.form.auto_update'), 'auto_update', var_type='bool')

        grp_fonts = ttk.LabelFrame(self.form_inner, text=self.lang.t('gui.group_fonts'))
        grp_fonts.pack(fill=tk.X, padx=6, pady=(6,4))
        add_entry(grp_fonts, self.lang.t('gui.form.step_title_font_size'), 'step_title_font_size', var_type='int')
        add_entry(grp_fonts, self.lang.t('gui.form.step_text_font_size'), 'step_text_font_size', var_type='int')
        add_entry(grp_fonts, self.lang.t('gui.form.text_font_path'), 'text_font_path', var_type='path')
        add_entry(grp_fonts, self.lang.t('gui.form.emoji_font_path'), 'emoji_font_path', var_type='path')
        add_entry(grp_fonts, self.lang.t('gui.form.emoji_scale'), 'emoji_scale', var_type='float')

        grp_layout = ttk.LabelFrame(self.form_inner, text=self.lang.t('gui.group_layout'))
        grp_layout.pack(fill=tk.X, padx=6, pady=(6,4))
        add_entry(grp_layout, self.lang.t('gui.form.safety_margin_mm'), 'safety_margin_mm', var_type='int')
        add_entry(grp_layout, self.lang.t('gui.form.photos_before_page_break'), 'photos_before_page_break', var_type='int', help_text=self.lang.t('gui.help.photos_before_page_break'))
        add_entry(grp_layout, self.lang.t('gui.form.fill_page_with_photos'), 'fill_page_with_photos', var_type='bool', help_text=self.lang.t('gui.help.fill_page_with_photos'))
        add_entry(grp_layout, self.lang.t('gui.appendix_include_undisplayed_media'), 'appendix_show_undisplayed_media', var_type='bool')
        add_entry(grp_layout, self.lang.t('gui.form.photo_wall_columns'), 'photo_wall_columns', var_type='int')
        add_entry(grp_layout, self.lang.t('gui.form.photo_wall_gap'), 'photo_wall_gap', var_type='int')

        grp_map = ttk.LabelFrame(self.form_inner, text=self.lang.t('gui.group_map_general'))
        grp_map.pack(fill=tk.X, padx=6, pady=(6,4))
        var_map_style, cb = add_entry(grp_map, self.lang.t('gui.form.map_style'), 'map_style', var_type='combobox')
        cb['values'] = ('hybrid', 'satellite', 'road')
        add_entry(grp_map, self.lang.t('gui.form.hybrid_labels_opacity'), 'hybrid_labels_opacity', var_type='float')
        add_entry(grp_map, self.lang.t('gui.form.marker_thumb_size'), 'marker_thumb_size', var_type='int')

        grp_overview = ttk.LabelFrame(self.form_inner, text=self.lang.t('gui.group_map_overview'))
        grp_overview.pack(fill=tk.X, padx=6, pady=(6,4))
        add_entry(grp_overview, self.lang.t('gui.form.overview_vertical_resolution_px'), 'maps.overview.vertical_resolution_px', var_type='int')
        add_entry(grp_overview, self.lang.t('gui.form.overview_aspect_ratio'), 'maps.overview.aspect_ratio')
        add_entry(grp_overview, self.lang.t('gui.form.overview_padding_factor'), 'maps.overview.padding_factor', var_type='float')
        add_entry(grp_overview, self.lang.t('gui.form.overview_algorithm'), 'maps.overview.algorithm')
        add_entry(grp_overview, self.lang.t('gui.form.overview_min_width_km'), 'maps.overview.min_width_km', var_type='float')

        grp_step = ttk.LabelFrame(self.form_inner, text=self.lang.t('gui.group_map_step'))
        grp_step.pack(fill=tk.X, padx=6, pady=(6,4))
        add_entry(grp_step, self.lang.t('gui.form.step_vertical_resolution_px'), 'maps.step.vertical_resolution_px', var_type='int')
        add_entry(grp_step, self.lang.t('gui.form.step_aspect_ratio'), 'maps.step.aspect_ratio')
        add_entry(grp_step, self.lang.t('gui.form.step_padding_factor'), 'maps.step.padding_factor', var_type='float')
        add_entry(grp_step, self.lang.t('gui.form.step_min_width_km'), 'maps.step.min_width_km', var_type='float')
        add_entry(grp_step, self.lang.t('gui.form.step_max_distance_farthest_steps_km'), 'maps.step.max_distance_farthest_steps_km', var_type='float')
        add_entry(grp_step, self.lang.t('gui.form.step_cluster_distance_km'), 'maps.step.cluster_distance_km', var_type='float')
        add_entry(grp_step, self.lang.t('gui.form.step_render_scale'), 'maps.step.render_scale', var_type='float')
        # Hook group frame for mouse scroll enter/leave to keep mousewheel active
        try:
            self._hook_widget_for_mouse_scroll(grp_general)
        except Exception:
            pass
        try:
            self._hook_widget_for_mouse_scroll(grp_fonts)
        except Exception:
            pass
        try:
            self._hook_widget_for_mouse_scroll(grp_layout)
        except Exception:
            pass
        try:
            self._hook_widget_for_mouse_scroll(grp_map)
        except Exception:
            pass
        try:
            self._hook_widget_for_mouse_scroll(grp_overview)
        except Exception:
            pass
        try:
            self._hook_widget_for_mouse_scroll(grp_step)
        except Exception:
            pass

        # Actions - pinned outside the scrollable form so they remain clickable
        frm_actions = ttk.Frame(self.scrollable_form.master)
        frm_actions.pack(side=tk.RIGHT, fill=tk.Y, padx=(6,8), pady=(6,8))
        # Buttons stacked vertically for persistent access (not affected by scrolling)
        ttk.Button(frm_actions, text=self.lang.t('gui.load_from_file'), command=self._load_config_form).pack(anchor='n', pady=4)
        self.btn_save_form = ttk.Button(frm_actions, text=self.lang.t('gui.save_to_file'), command=self._save_config_form)
        self.btn_save_form.pack(anchor='n', pady=4)
        ttk.Button(frm_actions, text=self.lang.t('gui.toml_preview'), command=self._apply_form_to_raw).pack(anchor='n', pady=4)
        ttk.Button(frm_actions, text=self.lang.t('gui.apply'), command=self._apply_config).pack(anchor='n', pady=4)
        # Validation status label pinned in the actions column
        self.lbl_validation_status = ttk.Label(frm_actions, text='', foreground='gray')
        self.lbl_validation_status.pack(side=tk.BOTTOM, pady=(6,0))

    def _load_config_form(self):
        """Load values from config.toml into the form widgets.

        Also cache the original file text/lines in memory so we can preserve
        comments when writing back changes.
        """
        cfg_file = SCRIPT_DIR / 'config.toml'
        # cache original content to allow comment-preserving edits
        self._original_config_text = None
        self._original_config_lines = None
        if not cfg_file.exists():
            messagebox.showinfo(self.lang.t('gui.info'), self.lang.t('gui.no_config_found'))
            cfg = {}
        else:
            try:
                txt = cfg_file.read_text(encoding='utf-8')
                # store raw content for later patching (preserve comments)
                self._original_config_text = txt
                self._original_config_lines = txt.splitlines()

                # prefer tomllib/toml if available for accurate parsing
                parsed = None
                try:
                    if hasattr(m, '_tomllib') and m._tomllib:
                        if hasattr(m._tomllib, 'loads'):
                            parsed = m._tomllib.loads(txt)
                    # fallback to simple parser
                except Exception:
                    parsed = None
                if parsed is None:
                    parsed = m._parse_simple_toml(txt)
                cfg = parsed or {}
            except Exception as e:
                messagebox.showerror(self.lang.t('gui.error'), self.lang.t('gui.error_load_config').format(error=e))
                return

        def _get(cfg, path, default=None):
            parts = path.split('.')
            cur = cfg
            for p in parts:
                if not isinstance(cur, dict):
                    return default
                if p not in cur:
                    return default
                cur = cur[p]
            return cur

        for path, var in self.config_vars.items():
            val = _get(cfg, path, None)
            try:
                if isinstance(var, tk.BooleanVar):
                    var.set(bool(val) if val is not None else False)
                elif isinstance(var, tk.IntVar):
                    var.set(int(val) if val is not None else 0)
                elif isinstance(var, tk.DoubleVar):
                    var.set(float(val) if val is not None else 0.0)
                else:
                    # StringVar or others
                    if val is None:
                        var.set('')
                    else:
                        var.set(str(val))
            except Exception:
                try:
                    var.set(val)
                except Exception:
                    pass
        # validate after loading to update UI feedback (no popup)
        try:
            ok, errs = self._validate_config_form()
            if ok:
                try:
                    self.lbl_validation_status.configure(text=self.lang.t('gui.validation_all_valid'), foreground='green')
                except Exception:
                    self.lbl_validation_status.configure(text='Alle Felder gültig', foreground='green')
            else:
                self.lbl_validation_status.configure(text=f'{len(errs)} Problem(e) — siehe Meldungen', foreground='red')
            # update per-field label states
            for k, _ in self.config_vars.items():
                self._on_var_change(k)
            try:
                self.status_text.set(self.lang.t('gui.configuration_loaded_into_form'))
            except Exception:
                try:
                    self.status_text.set('Konfiguration in Formular geladen')
                except Exception:
                    pass
        except Exception:
            pass

    def _save_config_form(self):
        """Write form values back to config.toml while preserving comments where possible.

        Strategy:
        - Build the desired key/value map from the form (same as before)
        - Validate fields; show errors and allow user to abort or proceed
        - If we have the original file lines cached (loaded via _load_config_form),
          try to patch only the value assignments in-place and keep all comments/formatting.
        - If patching fails for any reason, fall back to a full TOML dump.
        """
        try:
            import toml
        except Exception:
            toml = None

        # Validate first
        ok, errs = self._validate_config_form()
        if not ok:
            # show errors and ask user whether to continue
            msg = 'Configuration has the following issues:\n' + '\n'.join(f'- {e}' for e in errs)
            msg += '\n\nSave anyway?'
            if not messagebox.askyesno('Validierungswarnungen', msg):
                return

        # Build nested dict from form widget values
        cfg = {}
        def _set(cfg, path, value):
            parts = path.split('.')
            cur = cfg
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value

        for path, var in self.config_vars.items():
            try:
                if isinstance(var, tk.BooleanVar):
                    v = bool(var.get())
                elif isinstance(var, tk.IntVar):
                    v = int(var.get())
                elif isinstance(var, tk.DoubleVar):
                    v = float(var.get())
                else:
                    raw = var.get()
                    if raw is None:
                        v = ''
                    else:
                        s = str(raw).strip()
                        try:
                            if s == '':
                                v = ''
                            elif s.isdigit() or (s.startswith('-') and s[1:].isdigit()):
                                v = int(s)
                            else:
                                v = float(s) if '.' in s else s
                        except Exception:
                            v = s
                _set(cfg, path, v)
            except Exception:
                pass

        cfg_file = SCRIPT_DIR / 'config.toml'

        # Helper utilities for smart line editing
        def _split_unquoted_hash(s: str):
            """Split s into (code, comment) at first unquoted '#'."""
            in_s = False
            in_d = False
            escaped = False
            for i, ch in enumerate(s):
                if ch == '\\' and not escaped:
                    escaped = True
                    continue
                if ch == '"' and not escaped and not in_s:
                    in_d = not in_d
                elif ch == "'" and not escaped and not in_d:
                    in_s = not in_s
                if ch == '#' and not in_s and not in_d:
                    return s[:i].rstrip(), s[i:].rstrip()
                escaped = False
            return s.rstrip(), ''

        def _fmt_val(v):
            # Use simple formatting compatible with TOML for basic types
            import json
            if isinstance(v, bool):
                return 'true' if v else 'false'
            if isinstance(v, int):
                return str(v)
            if isinstance(v, float):
                # ensure dot decimal
                return str(v)
            # fallback to a quoted string with basic escaping
            return json.dumps(str(v), ensure_ascii=False)

        def _find_section_range(lines, section_name):
            """Return (start_idx, end_idx) for the section; for top-level (None) returns (0, first_header-1)."""
            headers = []
            for idx, line in enumerate(lines):
                s = line.strip()
                if s.startswith('[') and s.endswith(']'):
                    headers.append((idx, s[1:-1].strip()))
            if section_name is None:
                if headers:
                    return 0, headers[0][0] - 1
                return 0, len(lines) - 1
            for h_i, (idx, name) in enumerate(headers):
                if name == section_name:
                    end = headers[h_i + 1][0] - 1 if h_i + 1 < len(headers) else len(lines) - 1
                    return idx, end
            return None

        def _find_key_in_range(lines, key, start, end):
            for i in range(start + 1 if start is not None and lines[start].strip().startswith('[') else start, end + 1):
                if i < 0 or i >= len(lines):
                    continue
                line = lines[i]
                stripped = line.lstrip()
                if not stripped or stripped.startswith('#'):
                    continue
                code, _ = _split_unquoted_hash(line)
                if '=' not in code:
                    continue
                left = code.split('=', 1)[0].strip()
                if left == key:
                    return i
            return None

        # Attempt in-place patching if we have original lines cached
        if getattr(self, '_original_config_lines', None):
            try:
                lines = list(self._original_config_lines)
                # process keys
                for path, var in self.config_vars.items():
                    parts = path.split('.')
                    key = parts[-1]
                    section = '.'.join(parts[:-1]) if len(parts) > 1 else None
                    rng = _find_section_range(lines, section)
                    if rng is None:
                        # section does not exist; append it
                        if section is not None:
                            if lines and lines[-1].strip() != '':
                                lines.append('')
                            lines.append(f'[{section}]')
                            rng = (len(lines) - 1, len(lines) - 1)
                        else:
                            rng = (0, len(lines) - 1)
                    start, end = rng

                    # format value
                    try:
                        if isinstance(var, tk.BooleanVar):
                            val = bool(var.get())
                        elif isinstance(var, tk.IntVar):
                            val = int(var.get())
                        elif isinstance(var, tk.DoubleVar):
                            val = float(var.get())
                        else:
                            raw = var.get()
                            val = '' if raw is None else raw
                    except Exception:
                        val = ''
                    new_val = _fmt_val(val)

                    idx = _find_key_in_range(lines, key, start, end)
                    if idx is not None:
                        orig = lines[idx]
                        indent = orig[:len(orig) - len(orig.lstrip())]
                        code, comment = _split_unquoted_hash(orig)
                        # preserve any inline comment
                        comment_suffix = (' ' + comment) if comment else ''
                        lines[idx] = f"{indent}{key} = {new_val}{comment_suffix}"
                    else:
                        # append the key=value just before end (or after header)
                        insert_at = end + 1
                        # place after header if header exists
                        if section is not None and lines[start].strip().startswith('['):
                            insert_at = start + 1
                        # insert a blank line for readability if needed
                        if insert_at > 0 and insert_at <= len(lines) and lines[insert_at - 1].strip() != '':
                            lines.insert(insert_at, '')
                            insert_at += 1
                        lines.insert(insert_at, f"{key} = {new_val}")
                # write back file
                content = '\n'.join(lines) + '\n'
                cfg_file.write_text(content, encoding='utf-8')
                # update cached original
                self._original_config_lines = content.splitlines()
                self._original_config_text = content
                # refresh raw editor + form (keep form values as-is)
                self._load_config_text()
                messagebox.showinfo(self.lang.t('gui.saved'), self.lang.t('gui.saved_comments_preserved'))
                return
            except Exception as e:
                # fall through to full dump on failure
                print('Comment-preserving save failed, falling back to full dump:', e)

    def _set_label_state(self, key, ok):
        """Set label color to red on error, default otherwise."""
        try:
            lbl = self.config_labels.get(key)
            if lbl:
                if ok:
                    lbl.configure(foreground='black')
                else:
                    lbl.configure(foreground='red')
        except Exception:
            pass

    def _on_var_change(self, key):
        """Callback when a form variable changes; updates per-field validation state and global status."""
        try:
            ok, errs = self._validate_config_form(single_key=key)
            self._set_label_state(key, ok)
            # update global status label
            all_ok, all_errs = self._validate_config_form()
            if all_ok:
                try:
                    self.lbl_validation_status.configure(text=self.lang.t('gui.validation_all_valid'), foreground='green')
                except Exception:
                    self.lbl_validation_status.configure(text='Alle Felder gültig', foreground='green')
                try:
                    self.btn_save_form.state(['!disabled'])
                except Exception:
                    pass
            else:
                self.lbl_validation_status.configure(text=f'{len(all_errs)} Problem(e) — siehe Meldungen', foreground='red')
                try:
                    self.btn_save_form.state(['!disabled'])
                except Exception:
                    pass
        except Exception:
            pass

    def _validate_config_form(self, single_key: str = None):
        """Validate form values. If `single_key` is given, only validate that key and return its state.

        Returns (ok: bool, errors: List[str])."""
        errs = []
        import os
        def _maybe_check_numeric(var, k, kind, minv=None, maxv=None):
            try:
                v = None
                if kind == 'int':
                    v = int(var.get())
                else:
                    v = float(var.get())
                if minv is not None and v < minv:
                    return f"{k}: value {v} < min {minv}"
                if maxv is not None and v > maxv:
                    return f"{k}: value {v} > max {maxv}"
            except Exception:
                return f"{k}: not a valid {kind}"
            return None

        # check specific fields
        try:
            # fonts
            tp = self.config_vars.get('text_font_path')
            if tp is not None:
                p = str(tp.get()).strip()
                if p:
                    if not os.path.exists(p):
                        errs.append('Text font path does not exist')
            ep = self.config_vars.get('emoji_font_path')
            if ep is not None:
                p = str(ep.get()).strip()
                if p:
                    if not os.path.exists(p):
                        errs.append('Emoji font path does not exist')

            # numeric ranges
            e = _maybe_check_numeric(self.config_vars.get('step_title_font_size'), 'Step title font size', 'int', 6, 200)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('step_text_font_size'), 'Step text font size', 'int', 6, 200)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('emoji_scale'), 'Emoji scale', 'float', 0.1, 10.0)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('safety_margin_mm'), 'Page margin (mm)', 'int', 0, 100)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('photos_before_page_break'), 'Photos before page break', 'int', 0, 100)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('photo_wall_columns'), 'Photo wall columns', 'int', 1, 10)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('photo_wall_gap'), 'Photo wall gap', 'int', 0, 100)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('maps.overview.vertical_resolution_px'), 'Maps overview vertical resolution', 'int', 100, 4000)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('maps.step.vertical_resolution_px'), 'Maps step vertical resolution', 'int', 100, 4000)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('maps.step.render_scale'), 'Maps step render scale', 'float', 1.0, 4.0)
            if e: errs.append(e)
            e = _maybe_check_numeric(self.config_vars.get('hybrid_labels_opacity'), 'Hybrid labels opacity', 'float', 0.0, 1.0)
            if e: errs.append(e)

            # map style
            ms = self.config_vars.get('map_style')
            if ms is not None:
                if ms.get() not in ('hybrid', 'satellite', 'road'):
                    errs.append('Map style must be one of hybrid/satellite/road')
        except Exception:
            pass

        if single_key is not None:
            # return only status for that key
            field_errs = [e for e in errs if e.lower().startswith(single_key.replace('_',' ').lower()) or single_key in e]
            ok = len(field_errs) == 0
            return ok, field_errs
        ok = len(errs) == 0
        return ok, errs

    # ScrollableFrame helper (defined below) now handles mouse wheel and pointer detection.

        # Fallback: full dump (same as previous behavior)
        out = None
        if toml:
            try:
                out = toml.dumps(cfg)
            except Exception:
                out = None
        if out is None:
            lines_out = []
            for k, v in cfg.items():
                if isinstance(v, dict):
                    lines_out.append(f"[{k}]")
                    for sk, sv in v.items():
                        lines_out.append(f"{sk} = {repr(sv)}")
                else:
                    lines_out.append(f"{k} = {repr(v)}")
            out = '\n'.join(lines_out) + '\n'

        try:
            cfg_file.write_text(out, encoding='utf-8')
            self._load_config_text()
            messagebox.showinfo(self.lang.t('gui.saved'), self.lang.t('gui.saved_full_rewrite'))
        except Exception as e:
            messagebox.showerror(self.lang.t('gui.error'), self.lang.t('gui.could_not_save_config').format(error=e))

    def _apply_form_to_raw(self):
        """Apply current form values to a TOML preview dialog (raw editor removed)."""
        try:
            import toml
        except Exception:
            toml = None
        # build nested dict similar to _save_config_form
        cfg = {}
        def _set(cfg, path, value):
            parts = path.split('.')
            cur = cfg
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value
        for path, var in self.config_vars.items():
            try:
                if isinstance(var, tk.BooleanVar):
                    v = bool(var.get())
                elif isinstance(var, tk.IntVar):
                    v = int(var.get())
                elif isinstance(var, tk.DoubleVar):
                    v = float(var.get())
                else:
                    raw = var.get()
                    v = raw if raw is not None else ''
                _set(cfg, path, v)
            except Exception:
                pass
        if toml:
            try:
                out = toml.dumps(cfg)
            except Exception:
                out = None
        else:
            out = None
        if out is None:
            # naive fallback
            lines = []
            for k, v in cfg.items():
                if isinstance(v, dict):
                    lines.append(f"[{k}]")
                    for sk, sv in v.items():
                        lines.append(f"{sk} = {repr(sv)}")
                else:
                    lines.append(f"{k} = {repr(v)}")
            out = '\n'.join(lines)
        # Show preview dialog with TOML content (raw editor removed)
        win = tk.Toplevel(self)
        win.title(self.lang.t('gui.toml_preview'))
        win.geometry('800x480')
        txtw = tk.Text(win, height=20, width=80, wrap='none')
        txtw.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8,0))
        txtw.insert('1.0', out)
        txtw.configure(state='disabled')
        # buttons
        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, pady=8)
        def _copy():
            try:
                self.clipboard_clear()
                self.clipboard_append(out)
                messagebox.showinfo(self.lang.t('gui.copied_title') if self.lang and self.lang.t('gui.copied_title') else self.lang.t('gui.copied'), self.lang.t('gui.copied'))
            except Exception:
                pass
        ttk.Button(btn_frame, text=self.lang.t('gui.copy_to_clipboard'), command=_copy).pack(side=tk.LEFT, padx=6)
        def _save_preview():
            path = filedialog.asksaveasfilename(defaultextension='.toml', filetypes=[('TOML files', '*.toml'), ('All files','*.*')])
            if path:
                open(path, 'w', encoding='utf-8').write(out)
                messagebox.showinfo(self.lang.t('gui.saved'), self.lang.t('gui.saved_to_path').format(path=path))
                win.destroy()
            ttk.Button(btn_frame, text=self.lang.t('gui.save_to_file'), command=_save_preview).pack(side=tk.LEFT, padx=6)
        # quick validation button inside preview
        def _validate_and_show():
            ok, errs = self._validate_config_form()
            if ok:
                messagebox.showinfo(self.lang.t('gui.validation_ok_title') if self.lang and self.lang.t('gui.validation_ok_title') else self.lang.t('gui.validation_all_valid'), self.lang.t('gui.validation_all_valid'))
            else:
                messagebox.showwarning(self.lang.t('gui.validation_issues'), '\n'.join(errs))
        ttk.Button(btn_frame, text=self.lang.t('gui.validate'), command=_validate_and_show).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_frame, text=self.lang.t('gui.close'), command=win.destroy).pack(side=tk.RIGHT, padx=6)

    def _apply_config(self):
        """Validate and apply current form values to the running GUI/session.

        This does not write to disk; it validates the form and stores the
        resulting configuration in-memory as `self._applied_config` so other
        parts of the GUI can read applied settings without saving the file.
        """
        ok, errs = self._validate_config_form()
        if not ok:
            try:
                messagebox.showwarning(self.lang.t('gui.validation_issues'), '\n'.join(errs))
            except Exception:
                pass
            return

        # build nested dict from form widget values
        cfg = {}
        def _set(cfg, path, value):
            parts = path.split('.')
            cur = cfg
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value

        for path, var in self.config_vars.items():
            try:
                if isinstance(var, tk.BooleanVar):
                    v = bool(var.get())
                elif isinstance(var, tk.IntVar):
                    v = int(var.get())
                elif isinstance(var, tk.DoubleVar):
                    v = float(var.get())
                else:
                    raw = var.get()
                    v = raw if raw is not None else ''
                _set(cfg, path, v)
            except Exception:
                pass

        # store applied config in-memory
        try:
            self._applied_config = cfg
        except Exception:
            self._applied_config = None
        # if the user changed paths via the form, sync them to the main UI
        try:
            # sync config value(s) to UI - prefer new key but accept old for backward compatibility
            if 'polarsteps_data_folder' in cfg or 'bsp_folder' in cfg:
                self.bsp_path.set(str(cfg.get('polarsteps_data_folder') or cfg.get('bsp_folder') or ''))
            if 'output_folder' in cfg:
                self.output_path.set(str(cfg.get('output_folder') or ''))
        except Exception:
            pass

        # Persist settings to disk using existing save routine (will preserve comments when possible)
        try:
            # _save_config_form performs validation again; since we've validated, it should save silently
            self._save_config_form()
        except Exception:
            # fallback: write a simple TOML dump
            try:
                import toml as _toml
                with open(m.SCRIPT_DIR / 'config.toml', 'w', encoding='utf-8') as f:
                    f.write(_toml.dumps(cfg))
            except Exception:
                try:
                    # naive fallback
                    lines = []
                    for k, v in cfg.items():
                        if isinstance(v, dict):
                            lines.append(f"[{k}]")
                            for sk, sv in v.items():
                                lines.append(f"{sk} = {repr(sv)}")
                        else:
                            lines.append(f"{k} = {repr(v)}")
                    (m.SCRIPT_DIR / 'config.toml').write_text('\n'.join(lines) + '\n', encoding='utf-8')
                except Exception:
                    pass

        # Reload config into form and in-memory text
        try:
            self._load_config_text()
        except Exception:
            pass
        try:
            self._load_config_form()
        except Exception:
            pass

        # Update language-driven UI (best-effort): refresh stats tooltip
        try:
            lang_code = cfg.get('language', '') if isinstance(cfg, dict) else ''
            if not lang_code:
                lang_code = 'en'
            try:
                lang_mgr = m.load_language_manager(lang_code, m.SCRIPT_DIR)
                tt_text = lang_mgr.t('gui.stats_button_tooltip', default="Zeige Statistiken für ausgewählte oder gefilterte Reisen")
            except Exception:
                tt_text = "Zeige Statistiken für ausgewählte oder gefilterte Reisen"
            try:
                # replace existing tooltip
                if getattr(self, '_stats_tooltip', None):
                    try:
                        self._stats_tooltip.hide()
                    except Exception:
                        pass
                self._stats_tooltip = _Tooltip(self.stats_btn, tt_text)
                self.stats_btn.bind('<Enter>', lambda e: self._stats_tooltip.show(e.x_root + 10, e.y_root + 10))
                self.stats_btn.bind('<Leave>', lambda e: self._stats_tooltip.hide())
            except Exception:
                pass
        except Exception:
            pass

        try:
            self.status_text.set('Einstellungen gespeichert')
        except Exception:
            pass
        try:
            do_restart = messagebox.askyesno('Neustart erforderlich', 'Einstellungen wurden gespeichert. Jetzt die GUI neu starten, um Änderungen anzuwenden?')
            if do_restart:
                python = sys.executable
                run_path = str(SCRIPT_DIR / 'run_gui.py')
                os.execv(python, [python, run_path])
            else:
                try:
                    self.status_text.set('Neustart ausstehend; Änderungen aktiv nach Neustart')
                except Exception:
                    pass
        except Exception as e:
            try:
                messagebox.showerror(self.lang.t('gui.restart_failed_title'), self.lang.t('gui.restart_failed').format(error=e))
            except Exception:
                pass

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
            messagebox.showinfo(self.lang.t('gui.saved'), self.lang.t('gui.saved_log').format(path=path))
        except Exception as e:
            messagebox.showerror(self.lang.t('gui.error'), self.lang.t('gui.could_not_save_log').format(error=e))

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
            messagebox.showinfo(self.lang.t('gui.no_package_selected_title'), self.lang.t('gui.no_package_selected_body'))
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
            messagebox.showinfo(self.lang.t('gui.nothing_to_install_title'), self.lang.t('gui.nothing_to_install_body'))
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
        # reset sort state so new data uses default ordering
        try:
            self._sort_reverse.clear()
        except Exception:
            pass
        raw = self.bsp_path.get().strip()
        # split on semicolon or space and remove empties
        parts = [p for p in re.split(r'[;\s]+', raw) if p]
        if not parts:
            messagebox.showerror(self.lang.t('gui.error'), self.lang.t('cli.error_bsp_not_found').format(path=''))
            return
        trips = []
        for p in parts:
            bp = Path(p)
            if not bp.exists():
                messagebox.showerror(self.lang.t('gui.error'), self.lang.t('cli.error_bsp_not_found').format(path=bp))
                return
            try:
                trips.extend(m.find_trips(bp))
            except Exception as e:
                messagebox.showerror(self.lang.t('gui.error'), self.lang.t('gui.error_list_trips').format(error=e))
                return
        # deduplicate by path
        seen = set()
        unique = []
        for t in trips:
            s = str(t)
            if s not in seen:
                seen.add(s)
                unique.append(t)
        self._trips = unique
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
                    messagebox.showerror(self.lang.t('gui.invalid_year_title'), self.lang.t('gui.invalid_year_msg'))
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
                messagebox.showerror(self.lang.t('gui.invalid_date_title'), self.lang.t('gui.invalid_date_msg'))
                return

        # Unknown mode: be permissive and don't filter by date/year
        else:
            pass

        filtered = m.filter_trips_by_date(trips, year=year, start_date=start_date, end_date=end_date)
        if not show_all:
            filtered = [t for t in filtered if not cm.is_rendered(t)]

        # store filtered list for accurate selection to trips mapping
        self._filtered_trips = filtered
        # prepare metadata used for sorting
        self._trip_meta = {}

        for idx, t in enumerate(filtered):
            display = t.name
            date_str = ''
            days_str = ''
            start_dt = None
            end_dt = None
            days_int = 0
            # attempt nicer name and date info from trip.json if available
            try:
                parser = m.TripParser(t)
                parser.load()
                name = parser.get_trip_name()
                # show only prettified name if available, otherwise fall back to folder
                if name:
                    display = name
                # compute dates
                s_dt, e_dt = parser.get_trip_dates()
                start_dt = s_dt
                end_dt = e_dt
                if s_dt or e_dt:
                    if s_dt and e_dt:
                        date_str = f"{s_dt.strftime('%d.%m.%Y')} – {e_dt.strftime('%d.%m.%Y')}"
                        try:
                            delta = (e_dt.date() - s_dt.date()).days
                            # inclusive count
                            days_int = delta + 1
                            days_str = str(days_int)
                        except Exception:
                            days_str = ''
                    elif s_dt:
                        date_str = s_dt.strftime('%d.%m.%Y')
                    elif e_dt:
                        date_str = e_dt.strftime('%d.%m.%Y')
            except Exception:
                pass
            rendered_mark = '✅' if cm.is_rendered(t) else ''
            # record meta for sorting
            self._trip_meta[str(idx)] = {
                'rendered': bool(rendered_mark),
                'trip': display,
                'start_date': start_dt,
                'end_date': end_dt,
                'days': days_int,
                'folder': t.name,
            }
            # Use index as iid to map selection back to filtered list
            self.trips_tree.insert('', 'end', iid=str(idx),
                                   values=(rendered_mark, display, date_str, days_str, t.name))
        # after populating, apply default sorting (start date descending)
        try:
            self._apply_default_sort()
        except Exception:
            pass

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

    # --- Sorting helpers for trips_tree headings ---
    def _on_heading_click(self, column):
        """Handler when a column heading is clicked.

        Toggles ascending/descending order for that column and
        calls the generic sort routine.  Also updates the arrow
        indicator and highlights the active column.
        """
        reverse = self._sort_reverse.get(column, False)
        self._sort_by(column, not reverse)
        # remember current column and update headings
        self._current_sort_col = column
        self._update_heading_styles(column, not reverse)

    def _sort_by(self, column, reverse=False):
        """Sort visible tree items by a given column."""
        items = list(self.trips_tree.get_children())
        def key(iid):
            meta = getattr(self, '_trip_meta', {}).get(iid, {})
            if column == 'rendered':
                return meta.get('rendered', False)
            elif column == 'trip':
                return (meta.get('trip') or "").lower()
            elif column == 'dates':
                # sort by start_date (None < any date)
                dt = meta.get('start_date')
                return dt or datetime.min
            elif column == 'days':
                return meta.get('days', 0)
            elif column == 'folder':
                return (meta.get('folder') or "").lower()
            else:
                return ""
        items.sort(key=key, reverse=reverse)
        for iid in items:
            try:
                self.trips_tree.move(iid, '', 'end')
            except Exception:
                pass
        # update heading arrow & highlight also
        self._sort_reverse[column] = reverse
        self._update_heading_styles(column, reverse)

    def _apply_default_sort(self):
        """Apply default sort after loading trips (start date desc)."""
        try:
            self._sort_by('dates', True)
            self._current_sort_col = 'dates'
            self._update_heading_styles('dates', True)
        except Exception:
            pass

    def _update_heading_styles(self, active_col, reverse):
        """Update heading text and style for sort indicators.

        * An arrow (▲/▼) is appended to the active column.
        * The active heading gets a highlight style (if available).
        """
        arrow = ' ▲' if not reverse else ' ▼'
        for col, base in self._heading_texts.items():
            text = base
            style = ''
            if col == active_col:
                text = base + arrow
                style = self.highlight_style or ''
            try:
                self.trips_tree.heading(col, text=text, style=style)
            except Exception:
                # some themes may not support style change
                try:
                    self.trips_tree.heading(col, text=text)
                except Exception:
                    pass


    def _on_render(self):
        sel = self.trips_tree.selection()
        if not sel:
            messagebox.showinfo(self.lang.t('gui.no_trips_selected_title'), self.lang.t('gui.no_trips_selected_body'))
            return
        # Map selection iids (indices) to the filtered trips list (if available)
        source = getattr(self, '_filtered_trips', None) or getattr(self, '_trips', [])
        try:
            trips = [source[int(iid)] for iid in sel]
        except Exception:
            messagebox.showerror(self.lang.t('gui.selection_error_title'), self.lang.t('gui.selection_error_body'))
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

        # Prefer current form values for renderer/open behavior to avoid stale config confusion.
        try:
            mode_var = self.config_vars.get('renderer_mode')
            if mode_var is not None:
                mode = str(mode_var.get() or '').strip().lower()
                if mode in ('pdf', 'html', 'both'):
                    cfg_overrides['renderer_mode'] = mode
        except Exception:
            pass
        try:
            pdf_open_var = self.config_vars.get('open_pdf_after_render')
            if pdf_open_var is not None:
                cfg_overrides['open_pdf_after_render'] = bool(pdf_open_var.get())
        except Exception:
            pass

        # Hard safety: HTML-only mode must never auto-open PDF.
        if str(cfg_overrides.get('renderer_mode', '')).strip().lower() == 'html':
            cfg_overrides['open_pdf_after_render'] = False

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

    def _open_path(self, path: Path):
        try:
            if os.name == 'nt':
                os.startfile(str(path))
            elif sys.platform == 'darwin':
                subprocess.run(['open', str(path)], check=False)
            else:
                subprocess.run(['xdg-open', str(path)], check=False)
        except Exception:
            pass

    def _on_combined_html(self):
        try:
            sel = self.trips_tree.selection()
            if sel:
                source = getattr(self, '_filtered_trips', None) or getattr(self, '_trips', [])
                trips = [source[int(iid)] for iid in sel]
            else:
                trips = self._get_filtered_trips(include_rendered=True)
        except ValueError:
            messagebox.showerror(self.lang.t('gui.invalid_date_title'), self.lang.t('gui.invalid_date_msg'))
            return
        except Exception:
            messagebox.showerror(self.lang.t('gui.selection_error_title'), self.lang.t('gui.selection_error_body'))
            return

        if not trips:
            messagebox.showinfo(self.lang.t('gui.no_trips_for_stats_title'), self.lang.t('gui.no_trips_for_stats_body'))
            return

        self.combined_html_btn.config(state=tk.DISABLED)
        self.status_text.set(f"Building combined HTML for {len(trips)} trip(s)...")
        threading.Thread(target=self._combined_html_worker, args=(trips,), daemon=True).start()

    def _combined_html_worker(self, trips):
        try:
            config = {}
            try:
                config_file = SCRIPT_DIR / 'config.toml'
                if config_file.exists():
                    content = config_file.read_text(encoding='utf-8')
                    if hasattr(m, '_tomllib') and m._tomllib:
                        config = m._tomllib.loads(content)
                    else:
                        config = m._parse_simple_toml(content)
            except Exception:
                config = {}

            try:
                gui_overrides = getattr(self, '_current_config_overrides', {}) or {}
                if isinstance(gui_overrides, dict):
                    config.update(gui_overrides)
            except Exception:
                pass

            try:
                gui_lang = m.load_language_manager(config.get('language', 'en'), SCRIPT_DIR)
                m._DEFAULT_LANGUAGE_MANAGER = gui_lang
                config['_language_code'] = gui_lang.language_code
                pdf_lang_code = config.get('pdf_language', '').strip()
                if not pdf_lang_code:
                    pdf_lang_code = config.get('language', gui_lang.language_code)
                pdf_lang = m.load_language_manager(pdf_lang_code, SCRIPT_DIR)
                config['_pdf_language_manager'] = pdf_lang
                config['_pdf_language_code'] = pdf_lang.language_code
            except Exception:
                pass

            output_folder_html = Path(config.get('output_folder_html') or config.get('output_folder') or SCRIPT_DIR / 'TripPdfs')
            try:
                output_folder_html.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            html_name = 'combined_trips'
            if not self.trips_tree.selection():
                year = getattr(self, '_stats_filter_year', None)
                sd = getattr(self, '_stats_filter_start', None)
                ed = getattr(self, '_stats_filter_end', None)
                if year:
                    html_name += f"_{year}"
                elif sd and ed:
                    try:
                        html_name += f"_{sd.date()}_{ed.date()}"
                    except Exception:
                        html_name += '_range'
            html_path = output_folder_html / f"{html_name}.html"

            builder = m.CombinedHtmlBuilder(html_path, trips, config=config, language_manager=gui_lang, cli_language_manager=gui_lang)
            ok = builder.build()
            if ok:
                open_html = bool(config.get('open_html_after_render', True))
                if open_html and html_path.exists():
                    self._open_path(html_path)
                self.log_queue.put(('combined_html_done', {'path': str(html_path)}))
            else:
                self.log_queue.put(('combined_html_error', f'Failed to build combined HTML: {html_path}'))
        except Exception as exc:
            self.log_queue.put(('combined_html_error', str(exc)))

    def _get_filtered_trips(self, include_rendered: bool = True):
        """Return list of trips filtered by the current date/year and render settings.

        This is used by statistics so the dialog reflects the current time selection
        even if the tree has not been refreshed with the Apply button.
        """
        trips = getattr(self, '_trips', []) or []
        cm = m.CacheManager(SCRIPT_DIR / 'cache' / 'rendered_trips_cache.json')

        show_all = bool(self.filter_show_all.get())
        mode = self.filter_date_mode.get() if hasattr(self, 'filter_date_mode') else 'year'
        year = None
        start_date = None
        end_date = None

        if mode == 'year':
            year_text = self.filter_year.get().strip()
            if year_text:
                try:
                    year = int(year_text)
                except ValueError:
                    # invalid year ignored here
                    year = None
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
            except Exception as e:
                # propagate invalid date so caller can notify user
                raise ValueError("Invalid date") from e
        # otherwise leave year,start_date,end_date None for no filter

        filtered = m.filter_trips_by_date(trips, year=year, start_date=start_date, end_date=end_date)
        # For statistics we usually want the complete period view independent of
        # render cache; include_rendered=False keeps legacy behaviour when needed.
        if not include_rendered and not show_all:
            filtered = [t for t in filtered if not cm.is_rendered(t)]
        # remember the parameters so _stats_worker can pass them to the
        # statistics generator; explicit selection bypasses these later.
        self._stats_filter_year = year
        self._stats_filter_start = start_date
        self._stats_filter_end = end_date
        return filtered

    def _on_show_statistics(self):
        """Launch background job to compute statistics for selected or filtered trips."""
        sel = self.trips_tree.selection()
        try:
            if sel:
                # explicit selection takes precedence; ignore time filter
                source = getattr(self, '_filtered_trips', None) or getattr(self, '_trips', [])
                trips = [source[int(iid)] for iid in sel]
            else:
                # no selection: use trips matching current time filter
                trips = self._get_filtered_trips(include_rendered=True)
        except ValueError:
            messagebox.showerror(self.lang.t('gui.invalid_date_title'), self.lang.t('gui.invalid_date_msg'))
            return
        except Exception:
            messagebox.showerror(self.lang.t('gui.selection_error_title'), self.lang.t('gui.selection_error_body'))
            return
        if not trips:
            messagebox.showinfo(self.lang.t('gui.no_trips_for_stats_title'), self.lang.t('gui.no_trips_for_stats_body'))
            return
        # disable button while computing
        try:
            self.stats_btn.config(state=tk.DISABLED)
        except Exception:
            pass
        t = threading.Thread(target=self._stats_worker, args=(trips,), daemon=True)
        t.start()

    def _stats_worker(self, trips):
        try:
            cfg = {}
            language_code = 'en'
            try:
                config_file = SCRIPT_DIR / 'config.toml'
                if config_file.exists():
                    content = config_file.read_text(encoding='utf-8')
                    if hasattr(m, '_tomllib') and m._tomllib:
                        cfg = m._tomllib.loads(content)
                    else:
                        cfg = m._parse_simple_toml(content)
                    language_code = str(cfg.get('language', 'en') or 'en').strip() or 'en'
            except Exception:
                cfg = {}
                language_code = 'en'

            try:
                if hasattr(m, 'create_map_generator_from_config'):
                    mg = m.create_map_generator_from_config(config=cfg, purpose='stats')
                else:
                    mg = m.MapGenerator()
            except Exception:
                mg = m.MapGenerator()

            sg = m.StatisticsGenerator(map_generator=mg, config=cfg)
            # pass along any active filters so the returned period covers the
            # full requested interval instead of just the travel dates
            year = getattr(self, '_stats_filter_year', None)
            start_date = getattr(self, '_stats_filter_start', None)
            end_date = getattr(self, '_stats_filter_end', None)
            # if trips were explicitly selected, ignore the prior filter
            if self.trips_tree.selection():
                year = None
                start_date = None
                end_date = None
            agg = sg.compute_aggregate_stats(trips, year=year, start_date=start_date, end_date=end_date)

            display_agg = dict(agg or {})
            display_agg['countries'] = sg.localize_country_counts(agg.get('countries', {}), language_code=language_code)
            display_agg['continents'] = sg.localize_continent_counts(agg.get('continents', {}), language_code=language_code)

            map_bytes = b''
            try:
                map_bytes = sg.generate_overview_map(trips)
            except Exception:
                map_bytes = b''
            charts = {}
            if HAVE_MATPLOTLIB and display_agg.get('countries'):
                try:
                    labels = list(display_agg['countries'].keys())
                    sizes = list(display_agg['countries'].values())
                    fig1, ax1 = plt.subplots(figsize=(4,3))
                    ax1.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=90)
                    ax1.axis('equal')
                    buf = io.BytesIO()
                    fig1.savefig(buf, format='png', bbox_inches='tight')
                    plt.close(fig1)
                    charts['country_pie'] = buf.getvalue()
                except Exception:
                    charts = {}
            # send result to main thread queue; include language for later localization
            self.log_queue.put(('stats_ready', {
                'agg': display_agg,
                'map': map_bytes,
                'charts': charts,
                'language_code': language_code
            }))
        except Exception as e:
            self.log_queue.put(('stats_error', str(e)))
        finally:
            try:
                self.stats_btn.config(state=tk.NORMAL)
            except Exception:
                pass

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
                        # Open only artifacts that were actually rendered.
                        try:
                            parser = m.TripParser(trip)
                            parser.load()
                            trip_name_safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in parser.get_trip_name())
                        except Exception:
                            trip_name_safe = trip.name

                        renderer_mode = str(config.get('renderer_mode', config.get('renderer', 'both'))).strip().lower()
                        if renderer_mode not in ('pdf', 'html', 'both'):
                            renderer_mode = 'both'

                        try:
                            pdf_open_after = bool(config.get('open_pdf_after_render', True))
                        except Exception:
                            pdf_open_after = True
                        try:
                            html_open_after = bool(config.get('open_html_after_render', True))
                        except Exception:
                            html_open_after = True

                        self.log_queue.put(("info", f"Render mode resolved: {renderer_mode} (open_html={html_open_after}, open_pdf={pdf_open_after})"))

                        pdf_dir, html_dir = m._resolve_output_dirs(config, SCRIPT_DIR)
                        pdf_path = pdf_dir / f"{trip_name_safe}.pdf"
                        html_path = html_dir / f"{trip_name_safe}.html"

                        def _open_path(path: Path):
                            try:
                                if os.name == 'nt':
                                    os.startfile(str(path))
                                elif sys.platform == 'darwin':
                                    subprocess.run(['open', str(path)], check=False)
                                else:
                                    subprocess.run(['xdg-open', str(path)], check=False)
                            except Exception:
                                pass

                        if renderer_mode in ('html', 'both') and html_open_after and html_path.exists():
                            _open_path(html_path)
                        if renderer_mode in ('pdf', 'both') and pdf_open_after and pdf_path.exists():
                            _open_path(pdf_path)
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

    def _on_check_update(self):
        """User requested an update check from the GUI."""
        def worker():
            try:
                self.log_queue.put(('status', self.lang.t('cli.update_checking')))
                avail, msg = m.check_for_update(SCRIPT_DIR)
                if not avail:
                    messagebox.showinfo(self.lang.t('gui.info'), self.lang.t('cli.update_not_available'))
                    self.log_queue.put(('status', self.lang.t('gui.status_ready')))
                    return
                # ask user whether to apply update
                do_it = messagebox.askyesno(self.lang.t('gui.update'), self.lang.t('cli.update_available', msg=msg) + "\n" + self.lang.t('cli.update_prompt'))
                if do_it:
                    self._start_update_thread()
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _start_update_thread(self):
        def upd():
            ok = m.do_update(SCRIPT_DIR)
            if ok:
                messagebox.showinfo(self.lang.t('gui.info'), self.lang.t('cli.update_success'))
                try:
                    self.quit()
                except Exception:
                    pass
            else:
                messagebox.showerror(self.lang.t('gui.error'), self.lang.t('cli.update_failed', error=''))
        threading.Thread(target=upd, daemon=True).start()

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
                    messagebox.showerror(self.lang.t('gui.error'), payload)
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
                        try:
                            self.status_text.set(self.lang.t('gui.step_status').format(name=name, cur=cur, tot=tot))
                        except Exception:
                            self.status_text.set(f"{name}: Step {cur}/{tot}")
                    except Exception:
                        pass
                elif typ == "done":
                    # All rendering finished: show success and fill progress green
                    try:
                        self.status_text.set(self.lang.t('gui.status_done'))
                    except Exception:
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
                        messagebox.showinfo(self.lang.t('gui.playwright'), self.lang.t('gui.playwright_install_success'))
                    else:
                        messagebox.showerror(self.lang.t('gui.playwright'), self.lang.t('gui.playwright_install_failed'))
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
                elif typ == 'stats_ready':
                    try:
                        agg = payload.get('agg')
                        map_bytes = payload.get('map')
                        charts = payload.get('charts', {}) or {}
                        lang_code = payload.get('language_code', 'en') or 'en'
                        try:
                            StatsDialog(self, agg, map_bytes, charts, lang_code)
                        except Exception as e:
                            messagebox.showinfo(self.lang.t('gui.statistics'), self.lang.t('gui.stats_ready_msg').format(agg=agg))
                    except Exception as e:
                        messagebox.showerror(self.lang.t('gui.statistics'), self.lang.t('gui.stats_error').format(error=e))
                elif typ == 'stats_error':
                    messagebox.showerror(self.lang.t('gui.statistics'), self.lang.t('gui.stats_error_payload').format(payload=payload))
                elif typ == 'combined_html_done':
                    try:
                        self.status_text.set(self.lang.t('gui.status_done'))
                    except Exception:
                        self.status_text.set('Combined HTML generation complete')
                    try:
                        self.combined_html_btn.config(state=tk.NORMAL)
                    except Exception:
                        pass
                elif typ == 'combined_html_error':
                    try:
                        self.status_text.set(self.lang.t('gui.status_ready'))
                    except Exception:
                        self.status_text.set('Ready')
                    try:
                        self.combined_html_btn.config(state=tk.NORMAL)
                    except Exception:
                        pass
                    messagebox.showerror('Combined HTML', payload)
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
                            messagebox.showwarning(self.lang.t('gui.install_finished_with_errors_title'), msg)
                        else:
                            msg = f"Successfully installed {len(succ)} package(s)."
                            messagebox.showinfo(self.lang.t('gui.install_finished_title'), msg)
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


class StatsDialog(tk.Toplevel):
    def __init__(self, parent, agg: dict, map_bytes: bytes = None, charts: dict = None, language_code: str = 'en'):
        super().__init__(parent)
        # load language manager for translations
        try:
            self.lang = m.load_language_manager(language_code, SCRIPT_DIR)
        except Exception:
            self.lang = m.load_language_manager('en', SCRIPT_DIR)

        self.title(self.lang.t("gui.statistics"))
        self.transient(parent)
        self.grab_set()
        self.geometry('900x600')
        frm = ttk.Frame(self)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left = ttk.Frame(frm)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = ttk.Frame(frm, width=300)
        right.pack(side=tk.RIGHT, fill=tk.Y)

        # Summary text
        txt = tk.Text(left, height=10, wrap='word')
        txt.pack(fill=tk.X)
        summary_lines = []
        try:
            summary_lines.append(self.lang.t("gui.stats_trips", count=agg.get('trip_count', 0)))
            summary_lines.append(self.lang.t("gui.stats_total_steps", count=agg.get('total_steps', 0)))
            summary_lines.append(self.lang.t("gui.stats_travel_days", count=agg.get('total_travel_days', 0)))
            # compute period if available and show travel / non-travel ratio
            try:
                ps = agg.get('period_start')
                pe = agg.get('period_end')
                if ps and pe:
                    from datetime import date as _date
                    psd = _date.fromisoformat(ps)
                    ped = _date.fromisoformat(pe)
                    period_days = agg.get('period_total_days') or ((ped - psd).days + 1)
                    travel_days = agg.get('total_travel_days', 0)
                    non_travel = agg.get('period_non_travel_days') if 'period_non_travel_days' in agg else max(0, period_days - travel_days)
                    pct = (travel_days / period_days * 100) if period_days else 0
                    summary_lines.append(self.lang.t(
                        "gui.stats_period_ratio",
                        start=ps,
                        end=pe,
                        travel=travel_days,
                        non_travel=non_travel,
                        pct=pct,
                    ))
            except Exception:
                pass
            summary_lines.append(self.lang.t("gui.stats_total_km", km=agg.get('total_km', 0)))
            summary_lines.append(self.lang.t(
                "gui.stats_photos_videos",
                photos=agg.get('total_photos', 0),
                videos=agg.get('total_videos', 0),
            ))
            summary_lines.append(self.lang.t(
                "gui.stats_countries",
                count=agg.get('visited_countries_count', 0),
                percent=agg.get('visited_countries_percent', 0.0),
            ))
            summary_lines.append('')
            summary_lines.append(self.lang.t("gui.stats_countries_days"))
            for c, cnt in sorted(agg.get('countries', {}).items(), key=lambda x: -x[1]):
                pct = (cnt / max(1, agg.get('total_travel_days', 1))) * 100 if agg.get('total_travel_days') else 0
                summary_lines.append(f"  {c}: {cnt} Tage ({pct:.1f}%)")
            # Continents
            summary_lines.append('')
            summary_lines.append(self.lang.t(
                "gui.stats_continents",
                count=agg.get('visited_continents_count', 0),
                percent=agg.get('visited_continents_percent', 0.0),
            ))
            summary_lines.append(self.lang.t("gui.stats_continents_days"))
            for c, cnt in sorted(agg.get('continents', {}).items(), key=lambda x: -x[1]):
                pct = (cnt / max(1, agg.get('total_travel_days', 1))) * 100 if agg.get('total_travel_days') else 0
                summary_lines.append(f"  {c}: {cnt} Tage ({pct:.1f}%)")
        except Exception:
            summary_lines = [str(agg)]
        txt.insert(tk.END, "\n".join(summary_lines))
        txt.config(state=tk.DISABLED)

        # Map preview
        if map_bytes and HAVE_PIL:
            try:
                im = Image.open(io.BytesIO(map_bytes))
                im.thumbnail((560, 400))
                self.map_img = ImageTk.PhotoImage(im)
                lbl_map = ttk.Label(left, image=self.map_img)
                lbl_map.pack(fill=tk.BOTH, pady=(6,0))
            except Exception:
                pass

        # Charts on right
        if charts:
            if charts.get('country_pie') and HAVE_PIL:
                try:
                    im = Image.open(io.BytesIO(charts.get('country_pie')))
                    im.thumbnail((260, 200))
                    self.chart_img = ImageTk.PhotoImage(im)
                    lbl_chart = ttk.Label(right, image=self.chart_img)
                    lbl_chart.pack(pady=(6, 8))
                except Exception:
                    pass
        # Buttons
        btn_frm = ttk.Frame(right)
        btn_frm.pack(side=tk.BOTTOM, fill=tk.X, pady=(8,0))
        def _export_json():
            path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON','*.json')])
            if path:
                try:
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(agg, f, indent=2, ensure_ascii=False)
                    messagebox.showinfo(self.lang.t('gui.export'), self.lang.t('gui.export_json_saved').format(path=path))
                except Exception as e:
                        messagebox.showerror(self.lang.t('gui.export'), self.lang.t('gui.export_json_failed').format(error=e))
        def _save_map():
            if not map_bytes:
                messagebox.showinfo(self.lang.t('gui.no_map'), self.lang.t('gui.no_map'))
                return
            path = filedialog.asksaveasfilename(defaultextension='.png', filetypes=[('PNG','*.png')])
            if path:
                try:
                    with open(path, 'wb') as f:
                        f.write(map_bytes)
                    messagebox.showinfo(self.lang.t('gui.export'), self.lang.t('gui.map_saved').format(path=path))
                except Exception as e:
                    messagebox.showerror(self.lang.t('gui.export'), self.lang.t('gui.map_save_failed').format(error=e))
        ttk.Button(btn_frm, text=self.lang.t('gui.stats_export_json'), command=_export_json).pack(fill=tk.X, pady=(0,6))
        ttk.Button(btn_frm, text=self.lang.t('gui.stats_save_map'), command=_save_map).pack(fill=tk.X, pady=(0,6))
        ttk.Button(btn_frm, text=self.lang.t('gui.stats_close'), command=self.destroy).pack(fill=tk.X)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
