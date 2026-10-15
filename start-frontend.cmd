@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "FRONTEND_DIR=%PROJECT_DIR%frontend"
set "ENV_FILE=%PROJECT_DIR%.env"
set "PORT=%~1"

if not exist "%ENV_FILE%" (
    echo Root .env not found. Copy .env.example to .env first.
    exit /b 1
)

for /f "tokens=1,* delims==" %%A in ('findstr /R /B /C:"FRONTEND_HOST=" "%ENV_FILE%"') do set "HOST=%%B"
if not defined PORT for /f "tokens=1,* delims==" %%A in ('findstr /R /B /C:"FRONTEND_PORT=" "%ENV_FILE%"') do set "PORT=%%B"

if not defined HOST (
    echo FRONTEND_HOST is missing from .env.
    exit /b 1
)
if not defined PORT (
    echo FRONTEND_PORT is missing from .env.
    exit /b 1
)

if not exist "%FRONTEND_DIR%\node_modules" (
    echo Frontend dependencies not found.
    echo Run npm.cmd install in the frontend directory first.
    exit /b 1
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
    echo npm.cmd not found. Install Node.js 20 or newer first.
    exit /b 1
)

cd /d "%FRONTEND_DIR%"
echo Frontend: http://%HOST%:%PORT%
call npm.cmd run dev -- --hostname %HOST% --port %PORT%

set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
