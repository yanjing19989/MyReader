@echo off
cd /d "%~dp0"
uv run python -m app --host 127.0.0.1 --port 8000
pause
