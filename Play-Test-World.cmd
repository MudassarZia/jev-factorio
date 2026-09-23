@echo off
setlocal
cd /d "%~dp0"
set "JEV_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%JEV_PYTHON%" goto bundled
where py >nul 2>nul
if not errorlevel 1 goto pylauncher
where python >nul 2>nul
if not errorlevel 1 goto pythonpath
echo Python 3.10 or newer with tkinter is required. See START-HERE.html.
pause
exit /b 1
:bundled
"%JEV_PYTHON%" -m jev_factorio.test_world
goto finished
:pylauncher
py -3 -m jev_factorio.test_world
goto finished
:pythonpath
python -m jev_factorio.test_world
:finished
if errorlevel 1 pause
