@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден. Установите Python 3.10+ и добавьте его в PATH.
    pause
    exit /b 1
)

python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo Устанавливаю PyInstaller...
    python -m pip install pyinstaller
)

echo Сборка единого EXE в папку build\ ...
python -m PyInstaller --noconfirm ^
  --distpath build ^
  --workpath build\.tmp ^
  --clean ^
  CraftEngineConverter.spec

if errorlevel 1 (
    echo Ошибка сборки.
    pause
    exit /b 1
)

echo.
echo Готово! EXE: build\CraftEngineConverter.exe
pause