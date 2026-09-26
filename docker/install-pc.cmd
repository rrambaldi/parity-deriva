@echo off
rem parity-deriva on this PC: double-click this. It runs pc.ps1, which is beside
rem it, and keeps the window open at the end to read what it said.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pc.ps1" %*
echo.
pause
