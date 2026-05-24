@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Установка зависимостей...
python -m pip install -r requirements.txt

echo.
echo Сборка MemoryGame.exe ...
python -m PyInstaller --noconfirm MemoryGame.spec

if exist "dist\MemoryGame.exe" (
  echo.
  echo Готово: dist\MemoryGame.exe
  echo Скопируйте scores.json и theme_config.txt рядом с exe при первом запуске — они создадутся автоматически.
) else (
  echo Ошибка сборки.
  exit /b 1
)

pause
