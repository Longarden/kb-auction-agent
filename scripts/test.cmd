@echo off
REM SafeBid tests. Always use the repo-local .venv interpreter.
REM Using global python / conda pytest gives different deps and different results.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%~dp0..\.venv\Scripts\python.exe" -m pytest %*