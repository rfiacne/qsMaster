@echo off
set "ROOT=%~dp0"

if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo Error: .venv not found. Run setup.bat first.
    exit /b 1
)

set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
"%ROOT%\.venv\Scripts\python.exe" -m qa.cli.main %*
