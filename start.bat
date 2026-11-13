@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt
echo.
echo Messenger: http://127.0.0.1:5001   (остановка: Ctrl+C)
python app.py
pause
