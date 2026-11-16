@echo off
rem Startet den Daytrading-Bot Falke (Paper Trading). DRY_RUN: vorher  set DRY_RUN=true  eingeben.
cd /d "%~dp0"
.venv\Scripts\python.exe daybot.py falke %*

