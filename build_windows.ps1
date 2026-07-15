<#
Builds dist\VCWarmIntro.exe - a single downloadable file with the graph inside.

    powershell -ExecutionPolicy Bypass -File build_windows.ps1

Steps: venv (Python 3.12) -> deps + spaCy model -> prebuilt graph -> PyInstaller.
The graph step needs network and takes several minutes; it is skipped when
build_assets\vcwarmintro.db already exists. Pass -RefreshGraph to rebuild it.

ASCII only, on purpose. Windows PowerShell 5.1 decodes a .ps1 as Windows-1252
unless it starts with a BOM, so a UTF-8 em-dash arrives as 'a,"' - and that
trailing 0x94 is a smart closing quote, which PowerShell honours as a string
delimiter. The file is saved WITH a BOM and kept ASCII regardless.
#>
[CmdletBinding()]
param(
    # Rebuild the bundled graph even if one is already present.
    [switch]$RefreshGraph,
    # Crawl ~25 VC firm rosters too. Needs SERPER_API_KEY; without it the graph
    # is the warm seed + podcast fleet only (~214 reachable instead of ~495).
    [switch]$Precrawl
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$Assets = Join-Path $Root "build_assets"
$SeedDb = Join-Path $Assets "vcwarmintro.db"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function SizeMB($path) { [math]::Round((Get-Item $path).Length / 1MB, 1) }

# --- 1. interpreter --------------------------------------------------------
# spaCy 3.7 has no wheels for 3.13+, so the version is a hard requirement, not
# a preference: on 3.14 the install fails trying to compile blis from source.
Step "Locating Python 3.12"
$Py = $null
foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                 "$env:ProgramFiles\Python312\python.exe")) {
    if (Test-Path $c) { $Py = $c; break }
}
if (-not $Py) {
    try {
        $probe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($probe) { $Py = $probe.Trim() }
    } catch { }
}
if (-not $Py) {
    throw "Python 3.12 not found. Install it ('winget install Python.Python.3.12') and re-run."
}
Write-Host "    $Py"

# --- 2. environment --------------------------------------------------------
if (-not (Test-Path $VenvPy)) {
    Step "Creating .venv"
    & $Py -m venv $Venv
}
Step "Installing dependencies"
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r (Join-Path $Root "requirements.txt") pyinstaller --quiet

Step "Ensuring the spaCy model is present"
& $VenvPy -c "import en_core_web_sm" 2>$null
if (-not $?) { & $VenvPy -m spacy download en_core_web_sm }

# --- 3. the shipped graph --------------------------------------------------
if ($RefreshGraph -and (Test-Path $SeedDb)) { Remove-Item $SeedDb -Force }
if (-not (Test-Path $Assets)) { New-Item -ItemType Directory $Assets | Out-Null }

if (Test-Path $SeedDb) {
    Step ("Reusing existing graph (" + (SizeMB $SeedDb) + " MB); -RefreshGraph rebuilds it")
} else {
    Step "Building the graph (network; several minutes)"
    $Staging = Join-Path $Assets "staging"
    if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
    New-Item -ItemType Directory $Staging | Out-Null
    $StagedDb = Join-Path $Staging "vcwarmintro.db"

    # Build in a staging dir so a half-finished crawl never lands in the bundle.
    $env:VCWI_DB_URL = "sqlite:///" + ($StagedDb -replace '\\', '/')
    $env:VCWI_CACHE_DB = Join-Path $Staging "vcwarmintro_cache.db"

    & $VenvPy -m app.cli seed --discover
    if ($LASTEXITCODE -ne 0) { throw "seed failed with exit code $LASTEXITCODE" }

    if ($Precrawl) {
        if (-not $env:SERPER_API_KEY) {
            Write-Warning "SERPER_API_KEY not set; skipping precrawl (firm rosters)."
        } else {
            & $VenvPy (Join-Path $Root "scripts\precrawl.py")
        }
    }

    # Fold the WAL back in: only the .db is copied into the bundle, so anything
    # still sitting in vcwarmintro.db-wal would be missing from the shipped graph.
    Step "Checkpointing WAL"
    & $VenvPy (Join-Path $Root "scripts\checkpoint_db.py") $StagedDb
    if ($LASTEXITCODE -ne 0) { throw "checkpoint failed with exit code $LASTEXITCODE" }

    Move-Item $StagedDb $SeedDb -Force
    Remove-Item $Staging -Recurse -Force
    Remove-Item Env:\VCWI_DB_URL, Env:\VCWI_CACHE_DB -ErrorAction SilentlyContinue
    Step ("Graph built: " + (SizeMB $SeedDb) + " MB")
}

# --- 4. the exe ------------------------------------------------------------
# A running app holds an exclusive lock on its own image, so PyInstaller dies on
# the very last step with a bare "PermissionError: [WinError 5] Access is denied"
# that names the path but not the cause. Closing it here costs nothing: the
# whole point of the run is to replace that binary.
$running = Get-Process -Name "VCWarmIntro" -ErrorAction SilentlyContinue
if ($running) {
    Step ("Closing " + @($running).Count + " running VCWarmIntro instance(s) - they lock dist\VCWarmIntro.exe")
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

Step "Running PyInstaller (a few minutes)"
& $VenvPy -m PyInstaller (Join-Path $Root "vcwarmintro.spec") --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$Exe = Join-Path $Root "dist\VCWarmIntro.exe"
if (-not (Test-Path $Exe)) { throw "Build finished but $Exe is missing." }
Write-Host ("`nBuilt " + $Exe + " (" + (SizeMB $Exe) + " MB)") -ForegroundColor Green
Write-Host "Send that one file to Drew. Double-click to run.`n"
