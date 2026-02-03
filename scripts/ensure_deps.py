"""Check and offer to install Python dependencies required to run the GUI.

Usage (from repo root):
    python -m scripts.ensure_deps

The script will:
 - check for a small set of required packages (Pillow etc.)
 - if any are missing, prompt the user to install using pip
 - attempt to install from `requirements.txt` if present, otherwise install missing packages individually
 - on success, invoke `python -m gui.tk_gui`
"""
from pathlib import Path
import sys
import subprocess
import importlib

REQUIRED = {
    'PIL': 'Pillow',
    'reportlab': 'reportlab',
    'requests': 'requests'
}

SCRIPT_DIR = Path(__file__).resolve().parent.parent


def check_missing():
    missing = {}
    for mod, pkg in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except Exception:
            missing[mod] = pkg
    return missing


def _write_install_log(rc: int, output: str) -> Path:
    """Append pip install output to debug/install_deps.log and return the path."""
    try:
        from datetime import datetime
        log_dir = SCRIPT_DIR / 'debug'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / 'install_deps.log'
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write('\n--- {} rc={} ---\n'.format(datetime.now().isoformat(), rc))
            f.write(output or '(no output)')
        return log_file
    except Exception:
        return SCRIPT_DIR


def _run_pip_install(args):
    """Run pip install with given args and return (rc, output)."""
    try:
        proc = subprocess.run([sys.executable, '-m', 'pip', 'install'] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return proc.returncode, proc.stdout
    except Exception as e:
        return 1, str(e)


def install_requirements():
    """Try installing missing packages directly, fallback to requirements.txt installation.

    Returns: (rc:int, log_file:Path|None)
    """
    missing = check_missing()
    pkg_list = list({pkg for pkg in missing.values()}) if missing else list(set(REQUIRED.values()))

    last_log = None

    # Try to install missing packages directly first (faster and more targeted)
    if pkg_list:
        print(f"Installing packages: {', '.join(pkg_list)}")
        rc, out = _run_pip_install(pkg_list)
        last_log = _write_install_log(rc, out)
        if rc == 0:
            return 0, last_log
        # If direct install failed, fall back to requirements.txt if present
    req_file = SCRIPT_DIR / 'requirements.txt'
    if req_file.exists():
        print(f"Direct install failed, trying from {req_file} ...")
        rc, out = _run_pip_install(['-r', str(req_file)])
        last_log = _write_install_log(rc, out)
        if rc == 0:
            return 0, last_log
        return rc, last_log

    # As a very last resort, try installing the known set
    packages = list(set(REQUIRED.values()))
    print(f"Fallback installing packages: {', '.join(packages)}")
    rc, out = _run_pip_install(packages)
    last_log = _write_install_log(rc, out)
    return rc, last_log


def main_entry():
    """Main entrypoint that can be called programmatically. Returns an exit code integer."""
    missing = check_missing()
    if not missing:
        print("All required packages are present. Launching GUI...")
        return subprocess.call([sys.executable, '-m', 'gui.tk_gui'])

    # Build a human-friendly message listing missing packages
    missing_lines = [f" - {pkg} (import {mod})" for mod, pkg in missing.items()]
    message = "The following required Python packages appear to be missing:\n\n" + "\n".join(missing_lines)

    # If we're running interactively in a terminal, ask in console; otherwise, try a GUI prompt
    try:
        interactive = sys.stdin.isatty()
    except Exception:
        interactive = False

    user_wants_install = False

    if interactive:
        print(message)
        print()
        try:
            choice = input("Install missing packages now? [Y/n]: ").strip().lower()
        except Exception:
            choice = 'y'
        if not choice or choice[0] != 'n':
            user_wants_install = True
        else:
            print("Aborting. Please run: pip install -r requirements.txt and then try again.")
            return 1
    else:
        # No console available (likely double-click). Try to ask via a simple Tkinter dialog.
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            user_wants_install = messagebox.askyesno("Install dependencies?", message + "\n\nInstall now?")
            root.destroy()
        except Exception:
            # If Tkinter isn't available, assume user wants install and proceed quietly
            user_wants_install = True

    if not user_wants_install:
        print("User declined installation. Aborting.")
        return 1

    rc, log_path = install_requirements()

    if rc == 0:
        # Re-check and either launch GUI or show error
        missing2 = check_missing()
        if not missing2:
            try:
                if not interactive:
                    import tkinter as tk
                    from tkinter import messagebox
                    root = tk.Tk()
                    root.withdraw()
                    messagebox.showinfo("Installation complete", "Dependencies installed successfully. The app will now start the GUI.")
                    root.destroy()
            except Exception:
                pass
            return subprocess.call([sys.executable, '-m', 'gui.tk_gui'])
        else:
            err_msg = "Some packages are still missing:\n" + "\n".join([f" - {pkg} (import {mod})" for mod, pkg in missing2.items()])
            if log_path:
                err_msg += f"\n\nSee log: {str(log_path)}"
            try:
                if not interactive:
                    import tkinter as tk
                    from tkinter import messagebox
                    root = tk.Tk()
                    root.withdraw()
                    messagebox.showerror("Installation incomplete", err_msg)
                    root.destroy()
            except Exception:
                print(err_msg)
            return 2
    else:
        err = f"`pip install` failed (exit code {rc}). Please install dependencies manually."
        if log_path:
            err += f"\n\nSee install log: {str(log_path)}"
        try:
            if not interactive:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror("Installation failed", err)
                root.destroy()
        except Exception:
            print(err)
        return rc


if __name__ == '__main__':
    sys.exit(main_entry())
