@echo off
rem First run of the web dashboard: creates the venv, installs the packages, sets up the database,
rem asks for an admin account, then starts the server. Safe to run again: the venv and the admin account are only created once.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo [Setup] Creating the virtual environment in venv\ ...
    py -3.11 -m venv venv 2>nul || python -m venv venv || goto :failed
)

echo [Setup] Installing packages from requirements.txt ...
venv\Scripts\python -m pip install --upgrade pip || goto :failed
venv\Scripts\python -m pip install -r requirements.txt || goto :failed

echo [Setup] Setting up the database ...
venv\Scripts\python web\manage.py migrate || goto :failed

venv\Scripts\python web\manage.py shell -v 0 -c "import sys; from django.contrib.auth import get_user_model; sys.exit(0 if get_user_model().objects.filter(is_staff=True, is_active=True).exists() else 1)"
if errorlevel 1 (
    echo [Setup] No admin account yet. Create one:
    venv\Scripts\python web\manage.py createsuperuser || goto :failed
)

echo [Setup] Starting the dashboard at http://127.0.0.1:8000 ...
venv\Scripts\python web\run.py
exit /b %errorlevel%

:failed
echo [Setup] Failed. Fix the error above, then run setup.bat again.
exit /b 1
