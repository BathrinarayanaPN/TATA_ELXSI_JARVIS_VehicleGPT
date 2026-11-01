@echo off
REM VehicleGPT one-click demo for Windows.
REM   run_demo.bat          -> built-in driver model
REM   run_demo.bat carla    -> CARLA drives the car (start CarlaUE4.exe first)
REM Activate your virtual environment before running this file.
cd /d %~dp0

if not exist data\vehicle.db (
  echo Seeding 4 weeks of vehicle history...
  python -m sim.seed_history
)

start "VehicleGPT - Collector (listen-only)" cmd /k python -m collector.collector
timeout /t 3 /nobreak >nul

if /I "%1"=="carla" (
  start "VehicleGPT - CARLA bridge" cmd /k python -m sim.carla_bridge --town Town04
) else (
  start "VehicleGPT - Vehicle simulator" cmd /k python -m sim.live_simulator
)

start "VehicleGPT - UI" cmd /k python -m streamlit run ui\app.py
echo.
echo VehicleGPT is starting. The UI opens at http://localhost:8501
echo Make sure Ollama is running with: ollama pull qwen2.5:1.5b
