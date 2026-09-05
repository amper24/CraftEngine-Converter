@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден. Установите Python 3.10+ и добавьте его в PATH.
    pause
    exit /b 1
)

python -m pip show pyyaml >nul 2>nul
if errorlevel 1 (
    echo Устанавливаю PyYAML...
    python -m pip install pyyaml
)

python -m converter gui
pause