@echo off
rem Start Tiny DAW with its files up to date (start.py). Uses Python 3.12 (the plugin host is built for it).
cd /d "%~dp0"
set PYTHON=python
where py >nul 2>nul && set PYTHON=py -3.12
%PYTHON% start.py %*
if errorlevel 1 pause
