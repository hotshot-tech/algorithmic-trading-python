@echo off
rem One-click setup for the MT5 news reaction script. Double-click this file.
cd /d "%~dp0"
set "HERE=%CD%"

python --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found.
    echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
    pause
    exit /b 1
)

echo [1/4] Installing libraries...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Library install failed. See the message above.
    pause
    exit /b 1
)

if exist config.ini goto test
echo.
echo [2/4] Discord settings
set /p WEBHOOK=Paste your Discord webhook URL and press Enter: 
set /p SUFFIX=Broker symbol suffix (e.g. .a), or just press Enter if your symbols are plain like EURUSD: 
(
    echo [discord]
    echo webhook_url = %WEBHOOK%
    echo.
    echo [mt5]
    echo symbol_suffix = %SUFFIX%
    echo terminal_path =
) > config.ini
echo Saved config.ini

:test
echo.
echo [3/4] Test run. Make sure MetaTrader 5 is OPEN and LOGGED IN, then
pause
python news_reaction.py --dry-run
echo.
choice /M "Did that look right (a UTC offset line and pip moves, no errors)"
if errorlevel 2 (
    echo OK - copy the output above and send it to Claude.
    pause
    exit /b 1
)

echo.
echo [4/4] Scheduling every Saturday at 10:00...
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)"') do set "PY=%%i"
schtasks /Create /F /SC WEEKLY /D SAT /ST 10:00 /TN "MT5 News Reaction" /TR "cmd /c cd /d \"%HERE%\" && \"%PY%\" news_reaction.py >> run.log 2>&1"

echo.
choice /M "Post this week's MT5 reactions to Discord now"
if errorlevel 2 goto done
python news_reaction.py

:done
echo.
echo All done. It will run every Saturday at 10:00 while your PC is on and MT5 is logged in.
pause
