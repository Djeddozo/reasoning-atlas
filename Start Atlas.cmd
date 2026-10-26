@echo off
rem Reasoning Atlas launcher. Keep this window open while you use the app.
rem The app's Restart button stops the service with exit code 3; this window
rem then rebuilds the screen and starts the service again with the new code.
cd /d "%~dp0"
set ATLAS_SUPERVISED=1
call npm run build
if errorlevel 1 goto failed
start "Atlas browser" http://127.0.0.1:4310
:serve
python -m services.atlas.server
if errorlevel 4 goto stopped
if errorlevel 3 goto restart
goto stopped
:restart
echo Updating Atlas...
rem A failed build keeps the previous release running.
call npm run build
goto serve
:failed
echo Atlas could not start. See the messages above.
:stopped
pause
