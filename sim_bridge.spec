# PyInstaller spec — builds efb.exe
#
# Build:  build_bridge.bat
# Output: dist/efb.exe

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules
import os

_datas    = []
_binaries = []
_hidden   = []


def _is_runtime_module(module_name):
    parts = module_name.split(".")
    if "bak" in parts:
        return False
    if module_name.endswith("_bak") or "._bak" in module_name:
        return False
    if module_name.endswith(".test") or module_name.endswith("_test"):
        return False
    if module_name in {"optimizer.test"}:
        return False
    if module_name.endswith(".generate_aircraft_yamls"):
        return False
    return True


# ── Project modules ───────────────────────────────────────────────────────────
for _pkg in ("optimizer", "data_fetcher", "delay_module", "performance_engine", "strategy"):
    _hidden += [
        _module
        for _module in collect_submodules(_pkg)
        if _is_runtime_module(_module)
    ]

# ── Runtime packages with non-Python data ─────────────────────────────────────
# Keep this list deliberately small. Collecting the whole Python environment
# makes the one-file exe much bigger and slows down cold start because PyInstaller
# must unpack all bundled binaries/data before Python starts.
for _pkg in ("openap",):
    _tmp = collect_all(_pkg)
    _datas    += _tmp[0]
    _binaries += _tmp[1]
    _hidden   += _tmp[2]

# ── uvicorn dynamic imports ───────────────────────────────────────────────────
_hidden += collect_submodules("uvicorn")
_hidden += ["h11"]

# Some FastAPI/Starlette responses use package data that PyInstaller hooks may
# not always infer in lean builds.
_datas += collect_data_files("fastapi")
_datas += collect_data_files("starlette")

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
    # Do not exclude matplotlib: openap imports it at package import time.
    excludes=[
        "sentence_transformers",
        "torch",
        "transformers",
        "tkinter",
    ],
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
)
