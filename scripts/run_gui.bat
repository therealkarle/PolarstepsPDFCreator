@echo off
REM Launcher for PolarstepsPDFCreator GUI (Windows)
REM Ensure we run from the repository root so the project modules are importable
pushd "%~dp0.."

REM Prefer project virtualenv Python if present (works well inside VS Code)
set "PY_EXE="
if exist "%.\%~dp0..\.venv\Scripts\python.exe" (
    REM not expected to be used; keep conservative path check
)
REM Compute project root path
set "ROOT=%~dp0.."
if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PY_EXE=%ROOT%\.venv\Scripts\python.exe"
) else if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
    set "PY_EXE=%VIRTUAL_ENV%\Scripts\python.exe"
) else (
    where py >nul 2>&1 && set "PY_EXE=py" || set "PY_EXE=python"
)

REM Use chosen Python to run the dependency checker (ensures VS Code venv is honored)
"%PY_EXE%" -m scripts.ensure_deps
popd
pause
