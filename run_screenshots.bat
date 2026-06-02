@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "CONFIG_FILE=%SCRIPT_DIR%pvshot_config.json"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%prepare_pvshot_environment.ps1" %*
if errorlevel 1 (
  echo Failed to prepare pvshot environment.
  exit /b 1
)

for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-Content -Raw -LiteralPath $env:CONFIG_FILE | ConvertFrom-Json; $c.pvpython_path"`) do (
  set "PVPYTHON=%%P"
)

if not exist "%PVPYTHON%" (
  echo Cannot find pvpython.exe from pvshot_config.json:
  echo %PVPYTHON%
  exit /b 1
)

echo Using "%PVPYTHON%"
pushd "%SCRIPT_DIR%"
"%PVPYTHON%" screenshot_openfoam_slices.py
set "RUN_EXIT=%ERRORLEVEL%"
popd
exit /b %RUN_EXIT%
