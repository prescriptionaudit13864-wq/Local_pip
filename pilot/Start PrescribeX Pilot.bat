@echo off
rem Double-click to start the doctor pilot (vLLM + audit server + app + public link).
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0start_pilot.ps1"
