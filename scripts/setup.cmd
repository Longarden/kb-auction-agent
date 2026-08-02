@echo off
REM Run once. Creates a repo-local venv and installs dependencies.
REM Your global python / conda environments are not touched.
py -3.12 -m venv "%~dp0..\.venv"
"%~dp0..\.venv\Scripts\python.exe" -m pip install -U pip
"%~dp0..\.venv\Scripts\python.exe" -m pip install -e "%~dp0..[dev]"
echo.
echo Done. Next:
echo   scripts\test.cmd
echo   scripts\demo.cmd