# PyInstaller spec — Warm-Intro Pathfinder desktop app.
# Build from the repo root:  pyinstaller build/pathfinder.spec --noconfirm
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Paths in a spec resolve relative to the spec's own dir (build/), so anchor
# everything to the repo root.
ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # noqa: F821 (PyInstaller global)

datas, binaries, hiddenimports = [], [], []

# our own code + assets
datas += [(os.path.join(ROOT, "app/static"), "app/static"),
          (os.path.join(ROOT, "resources/graph.db"), "resources")]
hiddenimports += collect_submodules("app")
hiddenimports += ["app.main"]

# the spend-capped Claude key, baked in by the CI workflow from a repo
# secret (see .github/workflows/build-desktop.yml + DESKTOP.md). Absent on
# a local dev build — that's fine, desktop/main.py just finds no file and
# the classifier no-ops, same as any other unset API key.
claude_key_file = os.path.join(ROOT, "resources", "claude_key.txt")
if os.path.exists(claude_key_file):
    datas += [(claude_key_file, "resources")]

# spaCy + the English model (+ its native-lib dependency chain)
for pkg in ("spacy", "en_core_web_sm", "thinc", "srsly", "catalogue", "cymem",
            "preshed", "blis", "wasabi", "spacy_legacy", "spacy_loggers",
            "murmurhash", "weasel", "cloudpathlib"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

# web stack that PyInstaller's static analysis can miss (uvicorn workers etc.)
hiddenimports += collect_submodules("uvicorn")
for pkg in ("fastapi", "starlette", "pydantic", "pydantic_core", "anyio",
            "sniffio", "httpx", "httpcore", "h11", "click", "sqlalchemy",
            "platformdirs", "webview", "bs4"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

a = Analysis(
    [os.path.join(ROOT, "desktop/main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["playwright", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="WarmIntroPathfinder", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="WarmIntroPathfinder")

if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="WarmIntroPathfinder.app",
        bundle_identifier="com.pantheon.warmintro",
        info_plist={"NSHighResolutionCapable": True,
                    "LSApplicationCategoryType": "public.app-category.productivity"},
    )
