@echo off
cd /d "%~dp0\.."
echo Starting NHL Analytics Platform on http://localhost:8513 ...
.\.venv\Scripts\streamlit.exe run dashboard\app.py --server.port 8513
pause
