@echo off
cd /d "%~dp0"

:menu
cls
echo ============================================
echo  Securities QA Agent - Launcher
echo ============================================
echo.
echo  Select mode:
echo.
echo    [1] Start API server    (FastAPI backend)
echo    [2] Interactive chat    (qa chat CLI)
echo    [3] Single question     (qa ask)
echo    [4] KB status           (qa status)
echo    [5] Show config         (qa config show)
echo    [6] Web UI              (open frontend)
echo    [7] Setup / reinstall   (setup.bat)
echo.
echo    [0] Exit
echo.
set /p choice="Enter number (0-7): "

if "%choice%"=="1" goto server
if "%choice%"=="2" goto chat
if "%choice%"=="3" goto ask
if "%choice%"=="4" goto status
if "%choice%"=="5" goto config
if "%choice%"=="6" goto web
if "%choice%"=="7" goto setup
if "%choice%"=="0" goto end
goto menu

:server
cls
echo ============================================
echo  Starting API server...
echo ============================================
echo.
echo  Frontend: http://127.0.0.1:8001
echo  API:      http://127.0.0.1:8001/api/v1
echo.
echo  Press Ctrl+C to stop the server.
echo.
.venv\Scripts\python -m qa.api.server
if errorlevel 1 (
    echo Failed to start server. Run setup.bat first.
    pause
)
goto menu

:chat
cls
.venv\Scripts\qa chat
if errorlevel 1 (
    echo Run setup.bat first to initialize.
    pause
)
goto menu

:ask
cls
set /p question="Enter your question: "
if "%question%"=="" goto menu
echo.
.venv\Scripts\qa ask "%question%"
echo.
pause
goto menu

:web
cls
echo Opening Web UI...
echo.

:: Check if backend is already running
curl -s http://127.0.0.1:8001/api/v1/qa/health >nul 2>&1
if %errorlevel% neq 0 (
    echo Backend not running. Starting API server automatically...
    start "QA Server" cmd /c ".venv\Scripts\python -m qa.api.server"
    echo Waiting for server to start...
    timeout /t 3 /nobreak >nul
)

echo Opening http://127.0.0.1:8001 ...
start "" "http://127.0.0.1:8001"
echo.
echo  If the page shows offline, wait a moment and refresh.
echo  Server console window is running in background.
pause
goto menu

:status
cls
.venv\Scripts\qa status
echo.
pause
goto menu

:config
cls
.venv\Scripts\qa config show
echo.
pause
goto menu

:setup
cls
call setup.bat
goto menu

:end
echo Goodbye!
