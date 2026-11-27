@echo off
rem Render semaphore wrapper for cmd.exe / PowerShell on Windows.
if defined SPACESTUDIO_PYTHON (set "PY=%SPACESTUDIO_PYTHON%") else (set "PY=python")
"%PY%" "%~dp0spacestudio_slot.py" %*
exit /b %ERRORLEVEL%
