@echo off
setlocal
set ROOT=%~dp0
set PYTHON=%ROOT%\.venv\Scripts\python.exe
set DIST=%ROOT%dist
set BUILD=%ROOT%build

if not exist "%PYTHON%" (
  echo Local virtual environment was not found.
  exit /b 1
)

"%PYTHON%" -m pip install -e .[build]
"%PYTHON%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --name gallery-dl-windows-gui ^
  --windowed ^
  --distpath "%DIST%" ^
  --workpath "%BUILD%" ^
  --collect-submodules gallery_dl ^
  --collect-data gallery_dl ^
  --copy-metadata gallery-dl ^
  main.py

if errorlevel 1 exit /b 1

"%PYTHON%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --name gallery-dl-windows-gui-cli ^
  --distpath "%DIST%" ^
  --workpath "%BUILD%" ^
  --collect-submodules gallery_dl ^
  --collect-data gallery_dl ^
  --copy-metadata gallery-dl ^
  main.py

if errorlevel 1 exit /b 1

copy /Y "%DIST%\gallery-dl-windows-gui-cli\gallery-dl-windows-gui-cli.exe" "%DIST%\gallery-dl-windows-gui\gallery-dl-windows-gui-cli.exe" >nul

echo.
echo Build complete: %DIST%\gallery-dl-windows-gui\
