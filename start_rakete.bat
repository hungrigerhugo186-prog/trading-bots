@echo off
rem Startet den Daytrading-Bot Rakete (Paper Trading). DRY_RUN: vorher  set DRY_RUN=true  eingeben.
cd /d "%~dp0"
.venv\Scripts\python.exe daybot.py rakete %*

