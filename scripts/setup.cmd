@echo off
REM Run once. Creates a repo-local venv and installs dependencies.
REM Your global python / conda environments are not touched.
pushd "%~dp0.."
py -3.12 -m venv .venv
if errorlevel 1 goto :nopy
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -U pip
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -e ".[dev]"
popd
echo.
echo Setup complete. Next:
echo   scripts\test.cmd     ^(expect: 80 passed^)
echo   scripts\demo.cmd     ^(opens http://localhost:8501^)
goto :eof
:nopy
popd
echo.
echo ERROR: Python 3.12 not found. Install it, then run this script again.
echo   https://www.python.org/downloads/release/python-3129/
echo   Verify with:  py -0p