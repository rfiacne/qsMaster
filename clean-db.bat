@echo off
cd /d "%~dp0"

echo ============================================
echo  Securities QA Agent - Clean Database
echo ============================================
echo.
echo  This will DELETE all indexed documents and
echo  reset the vector database.
echo.
set /p confirm="Are you sure? (y/N): "

if /i "%confirm%" neq "y" (
    echo Cancelled.
    exit /b 0
)

:: Delete index data
if exist ".\data\index" (
    echo Deleting index data...
    rmdir /s /q ".\data\index"
)
if exist ".\data\test_index" (
    rmdir /s /q ".\data\test_index"
)

echo.
echo ============================================
echo  Database cleaned.
echo  Run 'qa index ./docs' to rebuild from scratch.
echo ============================================
pause
