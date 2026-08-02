@echo off
REM SafeBid demo. Opens http://localhost:8501 in your browser.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%~dp0..\.venv\Scripts\python.exe" -m streamlit run "%~dp0..\app\main.py" --server.port 8501 --browser.gatherUsageStats false