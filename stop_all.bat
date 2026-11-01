@echo off
title Stop All VehicleGPT and CARLA Services
color 0C

echo Stopping all VehicleGPT and CARLA processes...

taskkill /IM CarlaUE4-Win64-Shipping.exe /F 2>nul
taskkill /IM CarlaUE4.exe /F 2>nul
powershell -Command "Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*vehiclegpt*' -or $_.CommandLine -like '*sim.*' -or $_.CommandLine -like '*collector*' -or $_.CommandLine -like '*ui.server*' } | Stop-Process -Force" 2>nul

echo All services stopped cleanly.
pause
