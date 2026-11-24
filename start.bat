@echo off
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is not installed. Install it from https://docs.astral.sh/uv/ and try again.
    pause
    exit /b 1
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example. Add your API keys to it, then run start.bat again.
    notepad ".env"
    pause
    exit /b 0
)

echo Starting Job-Search at http://localhost:8501  (Ctrl+C to stop)
uv run streamlit run streamlit_app.py --server.port 8501 --server.headless false
pause
