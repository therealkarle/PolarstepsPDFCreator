# PolarstepsPDFCreator - GUI (Windows)

Quick start for the simple Tkinter GUI included in `gui/tk_gui.py`:

1. Ensure Python 3.8+ is installed on the machine.
2. Install required packages (recommended in dev):

   ```powershell
   pip install -r requirements.txt
   pip install playwright
   playwright install
   ```

   Note: Playwright is optional but required for HTML->PDF rendering when the code uses the Playwright renderer. The GUI will also automatically download the browser if needed when you hit Render.

3. Start the GUI by double-clicking `scripts\run_gui.bat` or running from a command prompt:

   ```powershell
   python -m gui.tk_gui
   ```

4. In the app: choose one or more `Polarsteps Data` folders (append additional paths separated by semicolons), optionally set the output folder, select trips, and click `Render Selected`.

Packaging: To create a single EXE for Windows, consider `pyinstaller` and include Playwright browsers following the Playwright packaging docs.
