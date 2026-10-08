@echo off
rem Jarvis mode launcher for Windows: jarvis-mode install | uninstall [--yes] [--dry-run]
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
exit /b %ERRORLEVEL%
