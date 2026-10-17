@echo off
rem Create the project-local venv and install dependencies.
rem All paths are relative to this script, so the folder can live anywhere.
setlocal
pushd "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment in venv\ ...
    python -m venv venv || goto :error
)

"venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
"venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error

if not exist ".env" (
    echo.
    echo NOTE: create a .env file here with OPENAI_API_KEY and OPENAI_BASE_URL.
)

echo.
echo Setup complete. Start the app with run.bat
popd
exit /b 0

:error
echo Setup failed.
popd
exit /b 1
