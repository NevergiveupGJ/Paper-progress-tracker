@echo off
rem Paper Progress Tracker for Windows. Double-click this file to start it.
rem It finds Python 3.9 or newer, builds the page, starts the local server in
rem the background if it is not running yet and opens the tracker in your
rem browser. Double-click stop-windows.bat to stop it.
rem The same as running in a terminal: py -3 app\launcher.py start
setlocal EnableExtensions DisableDelayedExpansion
set "ACTION=%~1"
if not defined ACTION set "ACTION=start"
pushd "%~dp0" || goto nofolder
if not exist "app\launcher.py" goto notunzipped

rem The Python launcher first, then python and python3. The Microsoft Store
rem placeholder for python fails this check, so it is skipped.
set "PY="
call :trypython py -3
if not defined PY call :trypython python
if not defined PY call :trypython python3
if not defined PY goto nopython

%PY% "app\launcher.py" %ACTION%
if errorlevel 1 goto failed
rem Close by itself after a few seconds, so the messages can be read first.
timeout /t 5 /nobreak >nul 2>&1
popd
exit /b 0

:trypython
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%*"
exit /b 0

:failed
echo.
echo Something went wrong. The lines above say what happened.
echo.
pause
popd
exit /b 1

:nopython
echo Paper Progress Tracker needs Python 3.9 or newer, and it was not found.
echo.
echo 1. Download Python from https://www.python.org/downloads/windows/
echo 2. In the installer, tick "Add python.exe to PATH".
echo 3. Double-click start-windows.bat again.
echo.
choice /c DC /n /m "Press D to open the download page, or C to close."
if errorlevel 2 goto closepython
start "" "https://www.python.org/downloads/windows/"
:closepython
popd
exit /b 1

:notunzipped
echo The app folder is not next to this file.
echo Unzip the whole download first. Right-click the zip file, choose Extract All,
echo then double-click start-windows.bat in the unzipped folder.
echo.
pause
popd
exit /b 1

:nofolder
echo Could not open the folder that holds this file.
echo.
pause
exit /b 1
