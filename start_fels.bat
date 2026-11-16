@echo off
rem Startet den Swing-Bot Fels (Paper Trading), einmal nach US-Boersenschluss.
cd /d "%~dp0"
.venv\Scripts\python.exe bot.py fels

