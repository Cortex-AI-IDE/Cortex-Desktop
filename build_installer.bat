@echo off
chcp 65001 >nul
echo ==========================================
echo   CORTEX AI IDE - Build Installer
echo ==========================================
echo.

REM Check if running from correct directory
if not exist "src\main.py" (
    echo ERROR: Please run this script from the Cortex project root directory.
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist "venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found. Please create it first:
    echo   python -m venv venv
    pause
    exit /b 1
)

echo [1/5] Cleaning previous builds...
if exist "build" rmdir /s /q build
if exist "dist" rmdir /s /q dist
echo      Done.
echo.

echo [2/5] Building EXE with PyInstaller...
echo      This may take 2-5 minutes...
venv\Scripts\python.exe -m PyInstaller cortex.spec --clean
if errorlevel 1 (
    echo ERROR: PyInstaller build failed!
    pause
    exit /b 1
)
echo      Done.
echo.

echo [3/5] Checking Inno Setup...
set "INNO_PATH="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set "INNO_PATH=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set "INNO_PATH=C:\Program Files\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files (x86)\Inno Setup 7\ISCC.exe" (
    set "INNO_PATH=C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 7\ISCC.exe" (
    set "INNO_PATH=C:\Program Files\Inno Setup 7\ISCC.exe"
)

if not defined INNO_PATH (
    echo WARNING: Inno Setup not found!
    echo Please install Inno Setup 6 or 7 from: https://jrsoftware.org/isdl.php
    echo.
    echo Skipping installer creation. EXE is available in dist\Cortex\
    pause
    exit /b 0
)

echo      Found Inno Setup at: %INNO_PATH%
echo.

echo [4/5] Building Windows Installer...
echo      This may take 1-2 minutes...
"%INNO_PATH%" cortex_setup.iss
if errorlevel 1 (
    echo ERROR: Inno Setup build failed!
    pause
    exit /b 1
)
echo      Done.
echo.

echo [5/5] Build complete!
echo.
echo ==========================================
echo   OUTPUT FILES:
echo ==========================================
echo.
echo 1. Portable Folder: dist\Cortex\
echo    - Run: dist\Cortex\Cortex.exe
echo.
if exist "Output\CortexSetup.exe" (
    echo 2. Windows Installer: Output\CortexSetup.exe
    echo    - Run: Output\CortexSetup.exe (Next-Next-Install)
    echo.
)
echo ==========================================
echo.
pause