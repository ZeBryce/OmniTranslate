# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for OmniTranslate (one-folder, windowed).
# Build with build_exe.bat; output goes to dist\OmniTranslate\.

import importlib.metadata

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = [], [], []

# Paddle / PaddleX / PaddleOCR load models, configs and DLLs dynamically
for pkg in ("paddle", "paddlex", "paddleocr"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# PaddleX checks installed dependencies through importlib.metadata at
# runtime, so every distribution's metadata has to ship with the build.
seen = set()
for dist in importlib.metadata.distributions():
    name = dist.metadata["Name"]
    if not name or name.lower() in seen:
        continue
    seen.add(name.lower())
    try:
        datas += copy_metadata(name)
    except Exception:
        pass

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["pynput.keyboard._win32", "pynput.mouse._win32"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OmniTranslate",
    console=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="OmniTranslate",
)
