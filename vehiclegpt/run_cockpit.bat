@echo off
REM Tata Elxsi SDV VehicleGPT - Futuristic Web Cockpit & CARLA Station
cd /d %~dp0

echo ========================================================
echo   TATA ELXSI SDV · VEHICLEGPT NEXT-GEN WEB COCKPIT
echo ========================================================
echo.

set PYEXE=..\vgpt-env\Scripts\python.exe
if not exist %PYEXE% set PYEXE=python

if not exist data\vehicle.db (
  echo Seeding initial 4 weeks of vehicle history...
  %PYEXE% -m sim.seed_history
)

REM Check if collector is already running on port 50100
netstat -ano | findstr 50100 >nul
if errorlevel 1 (
  echo Starting Collector (listen-only)...
  start "VehicleGPT - Collector (listen-only)" cmd /k %PYEXE% -m collector.collector
  timeout /t 2 /nobreak >nul
) else (
  echo Collector is already active.
)

REM Check if CARLA bridge is running on port 50020
netstat -ano | findstr 50020 >nul
if errorlevel 1 (
  echo Starting CARLA Bridge with Real-Time HUD...
  start "VehicleGPT - CARLA Bridge HUD" cmd /k %PYEXE% -m sim.carla_bridge --town Town04
  timeout /t 2 /nobreak >nul
) else (
  echo CARLA Bridge or Simulator is already active.
)

echo Starting Futuristic Web Cockpit Server at http://localhost:8080...
start "VehicleGPT - Web Cockpit Server" cmd /k %PYEXE% -m ui.server

timeout /t 2 /nobreak >nul
start http://localhost:8080
echo.
echo ========================================================
echo   Cockpit UI open at: http://localhost:8080
echo   Streamlit UI also available at: http://localhost:8501
echo ========================================================
