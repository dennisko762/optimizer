# PyInstaller spec — builds efb.exe
#
# Build:  pyinstaller sim_bridge.spec   (via build_bridge.bat)
# Output: dist/efb.exe
#
# Single executable: SimConnect + full API + bundled React frontend.
# Runs on the sim PC. Other devices connect via browser.

from PyInstaller.utils.hooks import collect_all, collect_submodules
import os

_datas    = []
_binaries = []
_hidden   = []

# SimConnect Python package (includes SimConnect SDK DLL)
_sc = collect_all("SimConnect")
_datas    += _sc[0]
_binaries += _sc[1]
_hidden   += _sc[2]

# uvicorn dynamic imports
_hidden += collect_submodules("uvicorn")
_hidden += ["h11"]

# Project modules
for _pkg in ("optimizer", "data_fetcher", "delay_module", "performance_engine", "strategy"):
    _hidden += collect_submodules(_pkg)

# Bundle the built React frontend
_ui_dist = os.path.join("efb-ui", "dist")
_datas += [(_ui_dist, os.path.join("efb-ui", "dist"))]

a = Analysis(
    ["sim_bridge/main.py"],
    pathex=["."],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="efb",
    debug=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
)
