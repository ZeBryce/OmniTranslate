@echo off
rem Create the project-local .venv with uv and install the locked dependencies.
rem All paths are relative to this script, so the folder can live anywhere.
setlocal
pushd "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
    echo uv is not installed. Install it first, for example:
    echo     winget install --id=astral-sh.uv -e
    echo or see https://docs.astral.sh/uv/getting-started/installation/
    goto :error
)

rem Relocatable venv: activate scripts and .exe launchers use relative paths.
rem uv downloads its own Python (version in .python-version) if needed.
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv\ ...
    uv venv --relocatable --managed-python || goto :error
)

rem Install exactly the versions in uv.lock
uv sync --locked || goto :error

if not exist ".env" (
    echo.
    echo Pick a translation engine and enter its key on the app's Engine tab.
)

echo.
echo Setup complete. Start the app with run.bat
popd
exit /b 0

:error
echo Setup failed.
popd
exit /b 1
