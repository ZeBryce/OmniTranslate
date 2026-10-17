@echo off
rem Build dist\OmniTranslate\OmniTranslate.exe with PyInstaller.
rem All paths are relative to this script. Run setup.bat first.
setlocal
pushd "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo venv not found. Run setup.bat first.
    goto :error
)

"venv\Scripts\python.exe" -m pip install "pyinstaller>=6.10" || goto :error
"venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean OmniTranslate.spec || goto :error

set "OUT=dist\OmniTranslate"

rem Ship the already-downloaded OCR models so the exe works offline
if exist ".paddleocr\official_models" (
    robocopy ".paddleocr\official_models" "%OUT%\.paddleocr\official_models" /E /NFL /NDL /NJH /NJS /NP >nul
    if errorlevel 8 goto :error
)

copy /Y ".env.example" "%OUT%\.env.example" >nul

echo.
echo Build complete: %OUT%\OmniTranslate.exe
echo Pick a translation engine on the app's Engine tab.
popd
exit /b 0

:error
echo Build failed.
popd
exit /b 1
