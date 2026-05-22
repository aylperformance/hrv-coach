@echo off
REM Lancement HRV Coach
cd /d "%~dp0"
echo.
echo ====================================
echo   HRV Coach - demarrage du serveur
echo ====================================
echo.
echo Ouvre ton navigateur sur : http://127.0.0.1:8000
echo Pour arreter : Ctrl+C dans cette fenetre
echo.
python -m uvicorn main:app --reload --port 8000
pause
