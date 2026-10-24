@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing_env
".venv\Scripts\python.exe" -c "import numpy, reportlab" >nul 2>nul
if errorlevel 1 goto missing_packages
".venv\Scripts\python.exe" server.py
if errorlevel 1 pause
exit /b
:missing_env
echo The local Python environment has not been installed.
echo Run Setup.ps1 first using Python 3.12 or newer.
echo See README.md for Windows installation instructions.
pause
exit /b 1
:missing_packages
echo Installing missing application dependencies...
if exist "wheelhouse\" goto offline_install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
goto check_install
:offline_install
".venv\Scripts\python.exe" -m pip install --no-index --find-links wheelhouse -r requirements.txt
:check_install
if errorlevel 1 goto install_failed
".venv\Scripts\python.exe" server.py
if errorlevel 1 pause
exit /b
:install_failed
echo Dependency installation failed. Check the message above and README.md.
pause
exit /b 1
