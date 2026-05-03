# PyInstaller spec for sim_bridge.exe
#
# Build:  pyinstaller sim_bridge.spec
# Output: dist/sim_bridge.exe
#
# Run on the sim PC (Windows + MSFS). No Python installation required.

from PyInstaller.utils.hooks import collect_all, collect_submodules

_datas = []
_binaries = []
_hiddenimports = []

# SimConnect Python package — includes the SimConnect SDK DLL
_sc = collect_all("SimConnect")
_datas     += _sc[0]
_binaries  += _sc[1]
_hiddenimports += _sc[2]

# uvicorn uses dynamic imports that PyInstaller misses without this
_hiddenimports += collect_submodules("uvicorn")

# h11 is uvicorn's HTTP/1.1 backend
_hiddenimports += ["h11"]

# Our own modules (static analysis may not pick them all up)
_hiddenimports += [
    "data_fetcher.sim.sim_client",
    "data_fetcher.sim.sim_models",
    "data_fetcher.sim.sim_config",
    "data_fetcher.sim.sim_normalizer",
    "data_fetcher.sim.simconnect_client",
    "data_fetcher.sim.telemetry_hub",
    "data_fetcher.sim.remaining_distance",
    "data_fetcher.sim.airport_lookup",
]

a = Analysis(
    ["sim_bridge/main.py"],
    pathex=["."],           # project root so data_fetcher.* resolves
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
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
    name="sim_bridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # keep console window — shows connection status
    icon=None,
)
