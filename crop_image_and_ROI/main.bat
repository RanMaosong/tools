@echo off
setlocal
cd /d "%~dp0"
python crop_image.py
if errorlevel 1 (
    echo.
    echo Could not start. Install dependencies with: python -m pip install -r requirements.txt
    pause
)
