@echo off
REM Start script for Mini-CRM (robusto)
SETLOCAL ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

REM 1) Detect Python
where py >nul 2>nul && (set "PY=py") || (set "PY=python")

REM 2) Create venv if needed
if not exist .venv (
  echo [1/3] Criando ambiente virtual...
  %PY% -3 -m venv .venv
)

REM 3) Activate venv
call .venv\Scripts\activate.bat

REM 4) Install deps
echo [2/3] Instalando dependencias...
python -m pip install --upgrade pip
python -m pip install flask flask-login pandas openpyxl werkzeug

REM 5) Run
echo [3/3] Iniciando Mini-CRM em http://127.0.0.1:5000/
python run_fixed.py
ENDLOCAL
