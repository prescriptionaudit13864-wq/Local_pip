@echo off
rem Assign a pilot doctor from the Command Prompt (cmd). The name you give is fixed: the doctor cannot change it.
rem   pilot\add_doctor "Dr Rahman"           create the doctor and show their access code (once)
rem   pilot\add_doctor list                  list all doctors
rem   pilot\add_doctor remove "Dr Rahman"    remove the doctor: their code stops working at once
setlocal
if "%~1"=="" goto usage
if /i "%~1"=="list" goto list
if /i "%~1"=="remove" goto remove
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0add_doctor.ps1" -Name "%~1"
exit /b %errorlevel%

:list
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0add_doctor.ps1" -List
exit /b %errorlevel%

:remove
if "%~2"=="" goto usage
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0add_doctor.ps1" -Name "%~2" -Remove
exit /b %errorlevel%

:usage
echo Usage:
echo   pilot\add_doctor "Dr Name"          create a doctor and show the access code
echo   pilot\add_doctor list               list doctors
echo   pilot\add_doctor remove "Dr Name"   remove a doctor
exit /b 1
