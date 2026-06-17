@echo off
cd /d "%~dp0"

echo ============================================
echo  Securities QA Agent - Setup
echo ============================================
echo.

:: 1) Create virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    uv venv .venv --python 3.11
    if errorlevel 1 (
        echo ERROR: Failed to create venv. Install uv: https://docs.astral.sh/uv/
        pause
        exit /b 1
    )
) else (
    echo [1/3] Virtual environment already exists.
)

:: 2) Install dependencies (all extras: core + dev + ocr + pdf)
echo [2/3] Installing Python dependencies (core + dev + ocr + pdf + local)...
uv pip install -e ".[dev,ocr,pdf,local]"
if errorlevel 1 (
    echo WARNING: Full install failed, trying core only...
    uv pip install -e "."
    if errorlevel 1 (
        echo ERROR: Dependency installation failed.
        pause
        exit /b 1
    )
)

:: 3) Create config file
if not exist "%USERPROFILE%\.qa\config.yaml" (
    echo [3/3] Creating default config...
    if not exist "%USERPROFILE%\.qa" mkdir "%USERPROFILE%\.qa"
    copy /Y config.example.yaml "%USERPROFILE%\.qa\config.yaml" >nul
    echo Config created: %%USERPROFILE%%\.qa\config.yaml
    echo Edit it to set your internal API address.
) else (
    echo [3/3] Config already exists.
)

echo.
echo ============================================
echo  Setup complete!
echo.
echo  Commands:
echo    qa ask "your question"    Ask a question
echo    qa chat                   Interactive chat
echo    qa index ./docs           Import documents
echo    qa status                 Check KB status
echo    qa config show            Show config
echo.
echo  Quick start: start.bat
echo ============================================
pause
