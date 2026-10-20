@echo off
rem Launch OmniTranslate with the project-local .venv (paths relative to this script).
setlocal
pushd "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo .venv not found. Run setup.bat first.
    popd
    exit /b 1
)

".venv\Scripts\python.exe" run.py %*
set "CODE=%ERRORLEVEL%"
popd
exit /b %CODE%
