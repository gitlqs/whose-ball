@echo off
rem One-click tracker: start server (if not running) + open board as standalone Edge app window.
rem External links (e.g. Salesforce) clicked inside the app window open in normal Edge tabs.
cd /d "%~dp0"
set URL=http://127.0.0.1:8765

netstat -ano | findstr ":8765 " | findstr LISTENING >nul
if not %errorlevel%==0 (
    start "tracker server" /min python tracker.py serve
    timeout /t 1 /nobreak >nul
)

set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if exist "%EDGE%" (
    start "" "%EDGE%" --app=%URL% --window-size=1150,900
    exit /b
)
if exist "%CHROME%" (
    start "" "%CHROME%" --app=%URL% --window-size=1150,900
    exit /b
)
start "" %URL%
