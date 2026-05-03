# PyInstaller spec — builds efb.exe
#
# Build:  build_bridge.bat
# Output: dist/efb.exe

from PyInstaller.utils.hooks import collect_all, collect_submodules
import importlib.metadata
import os

_datas    = []
_binaries = []
_hidden   = []

# ── Auto-collect all installed packages ──────────────────────────────────────
# Applies collect_all() to every package in the environment so that data files
# (CSVs, fonts, JSONs, etc.) are never missing — no manual whack-a-mole.
_skip = {
    "pip", "setuptools", "wheel",
    "pyinstaller", "pyinstaller-hooks-contrib",
    "altgraph", "packaging", "pefile", "pywin32-ctypes",
}

for _dist in importlib.metadata.distributions():
    _name = _dist.metadata["Name"]
    if not _name or _name.lower() in _skip:
        continue
    _pkg = _name.replace("-", "_")
    try:
        _tmp = collect_all(_pkg)
        _datas    += _tmp[0]
        _binaries += _tmp[1]
        _hidden   += _tmp[2]
    except Exception:
        pass

# ── Project modules ───────────────────────────────────────────────────────────
for _pkg in ("optimizer", "data_fetcher", "delay_module", "performance_engine", "strategy"):
    _hidden += collect_submodules(_pkg)

# ── uvicorn dynamic imports ───────────────────────────────────────────────────
_hidden += collect_submodules("uvicorn")
_hidden += ["h11"]

# ── Bundle project data files (YAML configs, etc.) ───────────────────────────
# collect_all() only handles installed packages — our own files must be explicit.
_datas += [
    (os.path.join("optimizer", "configs"), os.path.join("optimizer", "configs")),
]

# ── Bundle the built React frontend ──────────────────────────────────────────
_ui_dist = os.path.join("efb-ui", "dist")
_datas += [(_ui_dist, os.path.join("efb-ui", "dist"))]

# ─────────────────────────────────────────────────────────────────────────────

a = Analysis(
    ["sim_bridge/main.py"],
    pathex=["."],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
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
