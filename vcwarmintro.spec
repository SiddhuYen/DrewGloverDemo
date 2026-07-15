# -*- mode: python ; coding: utf-8 -*-
"""One-file Windows build.

Build with `powershell -ExecutionPolicy Bypass -File build_windows.ps1`, which
prepares `build_assets/vcwarmintro.db` first — this spec only bundles it.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)

datas = [("app/static/index.html", "app/static")]
binaries = []
hiddenimports = []

# spaCy resolves pipeline components, and en_core_web_sm its weights + config,
# through `catalogue` entry points at runtime. None of that is a static import,
# so PyInstaller's module graph cannot see any of it — collect_all is what makes
# `spacy.load("en_core_web_sm")` work inside the bundle instead of degrading the
# whole app to structured-providers-only.
_PKGS = [
    "spacy", "en_core_web_sm", "thinc", "srsly", "catalogue", "cymem",
    "preshed", "murmurhash", "blis", "wasabi", "spacy_legacy", "spacy_loggers",
    "weasel", "confection", "cloudpathlib", "langcodes", "language_data",
    "marisa_trie",
]
for _pkg in _PKGS:
    try:
        _d, _b, _h = collect_all(_pkg)
    except Exception:
        continue  # optional transitive dep absent in this env; not fatal
    datas += _d
    binaries += _b
    hiddenimports += _h

# uvicorn picks its protocol/loop implementations by string name.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["anyio._backends._asyncio"]

# The prebuilt graph. `firstrun.ensure_graph_db` copies it out to the user's data
# dir on first launch; without it the app still runs and seeds on demand.
_seed_db = ROOT / "build_assets" / "vcwarmintro.db"
if _seed_db.is_file():
    datas += [(str(_seed_db), "seed")]

a = Analysis(
    ["vcwarmintro.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "matplotlib", "tkinter", "PIL", "pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VCWarmIntro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # A console is the app's only UI affordance for quitting, and it surfaces the
    # data-dir path and any provider error. Hiding it would strand the user with
    # a browser tab and an invisible process.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
