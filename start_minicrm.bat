@echo off
SETLOCAL
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
py -m pip install --upgrade pip
py -m pip install flask pandas openpyxl
py app.py
ENDLOCAL
