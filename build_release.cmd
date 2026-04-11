@echo off
setlocal
set ROOT=%~dp0
set RELEASE_DIR=%ROOT%release
set DIST_DIR=%ROOT%dist\gallery-dl-windows-gui

call "%ROOT%build_exe.cmd"
if errorlevel 1 exit /b 1

for /f "tokens=3 delims= " %%V in ('findstr /b /c:"version = " "%ROOT%pyproject.toml"') do set VERSION=%%~V

if "%VERSION%"=="" (
  echo Failed to detect version from pyproject.toml.
  exit /b 1
)

if not exist "%DIST_DIR%" (
  echo Build output folder was not found: %DIST_DIR%
  exit /b 1
)

if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"

set ARCHIVE=%RELEASE_DIR%\gallery-dl-windows-gui-portable-v%VERSION%.zip
if exist "%ARCHIVE%" del /f /q "%ARCHIVE%"

powershell -NoProfile -Command "Compress-Archive -Path '%DIST_DIR%' -DestinationPath '%ARCHIVE%' -Force"
if errorlevel 1 exit /b 1

echo.
echo Release archive created: %ARCHIVE%
