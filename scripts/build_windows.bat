@echo off
REM Build a single-file Windows exe. Run from the repository root:
REM   scripts\build_windows.bat
REM
REM Output: dist\fnlrc.exe
REM
REM Note: PyInstaller output is frequently flagged by antivirus (a known PyInstaller
REM issue, not this project's). Ship a zip + SHA256, and code-sign if you can.
REM
REM Output is ASCII on purpose: this file is UTF-8 without BOM and cmd.exe reads
REM batch files as the OEM codepage, so Chinese text here turned into mojibake.

setlocal
cd /d "%~dp0.."

if not exist .venv\Scripts\python.exe (
    echo [1/3] Creating venv...
    uv venv .venv --python 3.12 || goto :err
)

echo [2/3] Installing dependencies...
REM pywebview must be installed explicitly: it is commented out in requirements.txt
REM (servers do not need it), but without it the desktop build only opens a browser.
uv pip install --python .venv\Scripts\python.exe -r requirements.txt pyinstaller "pywebview>=5.0,<6" || goto :err

echo [3/3] Building...
REM Use `python -m PyInstaller` instead of pyinstaller.exe on purpose:
REM uv writes launcher shims that embed an absolute python path, so after the
REM venv is moved/copied the .exe shims fail with
REM "uv trampoline failed to canonicalize script path". `-m` always works.
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm scripts\fnlrc.spec || goto :err

echo.
echo Done: dist\fnlrc.exe
echo Please double-click it once on a machine without Python to verify.
endlocal
pause
exit /b 0

:err
echo.
echo Build failed, exit code %errorlevel%
endlocal
pause
exit /b 1
