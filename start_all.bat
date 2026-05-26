@echo off
title AI Office - Launcher

echo.
echo  ========================================
echo   AI OFFICE - starting all agents
echo   Marta, Kevin, Kasper, Peter, Elina, Alex
echo  ========================================
echo.

cd /d "%~dp0"

echo [1/6] Marta   - coordinator...
start "Marta - coordinator" cmd /k "set PYTHONIOENCODING=utf-8 & python main.py --agent marta"

timeout /t 1 /nobreak >nul

echo [2/6] Kevin   - developer...
start "Kevin - developer" cmd /k "set PYTHONIOENCODING=utf-8 & python main.py --agent kevin"

timeout /t 1 /nobreak >nul

echo [3/6] Kasper  - researcher...
start "Kasper - researcher" cmd /k "set PYTHONIOENCODING=utf-8 & python main.py --agent kasper"

timeout /t 1 /nobreak >nul

echo [4/6] Peter   - analyst...
start "Peter - analyst" cmd /k "set PYTHONIOENCODING=utf-8 & python main.py --agent peter"

timeout /t 1 /nobreak >nul

echo [5/6] Elina   - copywriter...
start "Elina - copywriter" cmd /k "set PYTHONIOENCODING=utf-8 & python main.py --agent elina"

timeout /t 1 /nobreak >nul

echo [6/6] Alex    - planner...
start "Alex - planner" cmd /k "set PYTHONIOENCODING=utf-8 & python main.py --agent alex"

echo.
echo  All 6 agents started in separate windows.
echo  Press any key to close this launcher.
echo.
pause >nul
