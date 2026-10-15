@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "BACKEND_DIR=%PROJECT_DIR%backend"
set "PYTHON=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "ENV_FILE=%PROJECT_DIR%.env"
set "PORT=%~1"

if not exist "%ENV_FILE%" (
    echo Root .env not found. Copy .env.example to .env first.
    exit /b 1
)

for /f "tokens=1,* delims==" %%A in ('findstr /R /B /C:"BACKEND_HOST=" "%ENV_FILE%"') do set "HOST=%%B"
if not defined PORT for /f "tokens=1,* delims==" %%A in ('findstr /R /B /C:"BACKEND_PORT=" "%ENV_FILE%"') do set "PORT=%%B"

if not defined HOST (
    echo BACKEND_HOST is missing from .env.
    exit /b 1
)
if not defined PORT (
    echo BACKEND_PORT is missing from .env.
    exit /b 1
)

if not exist "%PYTHON%" (
    echo Backend virtual environment not found:
    echo %PYTHON%
    exit /b 1
)

cd /d "%BACKEND_DIR%"
echo Backend: http://%HOST%:%PORT%
echo API docs: http://%HOST%:%PORT%/docs
"%PYTHON%" -m uvicorn app.main:app --reload --host %HOST% --port %PORT%

set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
