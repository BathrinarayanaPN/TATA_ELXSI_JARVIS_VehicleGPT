@echo off
title Tata Elxsi SDV - VehicleGPT CARLA Simulation Launcher
color 0B

echo ===============================================================================
echo          TATA ELXSI SDV - VEHICLEGPT COCKPIT 4.0 + CARLA SIMULATION
echo                         TELIPORT Season 4 - Case Study 5
echo ===============================================================================
echo.

set PROJECT_DIR=c:\Users\J.A.R.V.I.S\Downloads\vehiclegpt\vehiclegpt
set CARLA_DIR=C:\Users\J.A.R.V.I.S\Downloads\CARLA_Latest
set PYTHON_EXE=c:\Users\J.A.R.V.I.S\Downloads\vehiclegpt\vgpt-env\Scripts\python.exe

:: 1. Check if CARLA is already running
echo [1/4] Checking CARLA 0.9.16 server...
netstat -ano | findstr ":2000" >nul
if %errorlevel% equ 0 (
    echo     CARLA server is already running on port 2000.
) else (
    echo     Starting CARLA 0.9.16 server in low-quality windowed mode...
    start "CARLA 0.9.16 Simulator" /D "%CARLA_DIR%" "%CARLA_DIR%\CarlaUE4.exe" -windowed -ResX=1280 -ResY=720 -quality-level=Low -nosound
    echo     Waiting 12 seconds for CARLA world to initialize...
    powershell -Command "Start-Sleep -Seconds 12" >nul 2>&1
)

:: 2. Start Collector (Ingests CAN 10 Hz + SOME/IP into SQLite signals_1s)
echo [2/4] Starting Vehicle Telemetry Collector (CAN 50100 + SOME/IP 30509)...
start "VehicleGPT Collector" /D "%PROJECT_DIR%" "%PYTHON_EXE%" -m collector.collector

:: 3. Start Web UI Server (FastAPI Digital Cockpit on http://localhost:8080)
echo [3/4] Starting FastAPI Digital Cockpit Server on http://localhost:8080...
start "VehicleGPT Digital Cockpit Server" /D "%PROJECT_DIR%" "%PYTHON_EXE%" -m ui.server

:: Wait 3 seconds for server to bind port 8080
powershell -Command "Start-Sleep -Seconds 3" >nul 2>&1

:: 4. Start CARLA Bridge in this window (Streams CAN, HUD, Lane Detection, and Terminal Telemetry)
echo [4/4] Launching CARLA Ego Vehicle Bridge (Town04 Highway Loop)...
echo.
echo ===============================================================================
echo  CARLA simulation is now LIVE!
echo  Open your browser at: http://localhost:8080/
echo  Live telemetry is streaming below at 2 Hz. Press Ctrl+C anytime to stop.
echo ===============================================================================
echo.

start http://localhost:8080/

cd /d "%PROJECT_DIR%"
"%PYTHON_EXE%" -m sim.carla_bridge --town Town04

pause
