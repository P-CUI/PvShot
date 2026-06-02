@echo off
setlocal
pushd "%~dp0"
python pvshot_gui.py
set "RUN_EXIT=%ERRORLEVEL%"
popd
exit /b %RUN_EXIT%
