@echo off
setlocal enabledelayedexpansion

cd /d %~dp0

if not exist app_version.txt (
  echo app_version.txt not found.
  exit /b 1
)

set /p APP_VERSION=<app_version.txt
if "%APP_VERSION%"=="" (
  echo app_version.txt is empty.
  exit /b 1
)

echo Building Windows release for version %APP_VERSION%
echo.

echo [1/6] Prepare venv...
if not exist .venv (
  py -3 -m venv .venv
)

echo [2/6] Install dependencies...
call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :err

echo [3/6] Clean old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
del /q "AI招聘工作台.spec" 2>nul

echo [4/6] Build exe/app folder...
call .venv\Scripts\pyinstaller.exe --noconfirm --windowed --name "AI招聘工作台" --add-data "app_version.txt;." main.py
if errorlevel 1 goto :err

echo [5/6] Zip distribution...
if not exist release mkdir release
set ZIP_PATH=release\AI招聘工作台-Windows-v%APP_VERSION%.zip
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Force -Path 'dist\AI招聘工作台\*' -DestinationPath '%ZIP_PATH%'"
if errorlevel 1 goto :err

echo [6/6] Build installer if Inno Setup is available...
set ISCC_EXE=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe
if defined ISCC_EXE (
  call "%ISCC_EXE%" windows_installer.iss
  if errorlevel 1 goto :err
  echo Installer built successfully.
) else (
  echo Inno Setup 6 not found. Skipping installer build.
  echo Install Inno Setup 6 and rerun this script if you need the installer.
)

echo.
echo Build done:
echo   - Version      : %APP_VERSION%
echo   - EXE folder   : dist\AI招聘工作台\
echo   - ZIP file     : %ZIP_PATH%
if defined ISCC_EXE (
  echo   - Installer    : release\AI招聘工作台-Windows-Installer-v%APP_VERSION%.exe
  echo.
  echo Next step:
  echo   python scripts\generate_windows_update_manifest.py --base-url https://your-domain/releases/%APP_VERSION% --installer release\AI招聘工作台-Windows-Installer-v%APP_VERSION%.exe --out release\latest.json
) else (
  echo.
  echo Install Inno Setup 6 first if you want to generate the installer used by online update.
)
exit /b 0

:err
echo.
echo Build failed. Please check output above.
exit /b 1
