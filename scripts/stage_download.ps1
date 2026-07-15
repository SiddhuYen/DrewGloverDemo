<#
Stages the built .exe next to the download page, then deploys to Netlify.

    powershell -ExecutionPolicy Bypass -File scripts\stage_download.ps1
    powershell -ExecutionPolicy Bypass -File scripts\stage_download.ps1 -Deploy

Without -Deploy it only copies the binary into docs\ so you can preview the page
locally. With -Deploy it runs the Netlify CLI, which needs YOUR login: run
`netlify login` once first.

The .exe is deliberately never committed. A 72MB binary in git history is
permanent and would be pushed to every clone forever; Netlify uploads it from the
publish directory instead, so git stays clean.

ASCII only + saved with a BOM: Windows PowerShell 5.1 reads a .ps1 as
Windows-1252 without one, and a stray UTF-8 dash decodes into a smart quote that
silently terminates a string.
#>
[CmdletBinding()]
param(
    # Run `netlify deploy --prod` after staging.
    [switch]$Deploy,
    # Preview the page locally instead (http://localhost:8888).
    [switch]$Serve
)

$ErrorActionPreference = "Stop"
$Root  = Split-Path $PSScriptRoot -Parent
$Exe   = Join-Path $Root "dist\VCWarmIntro.exe"
$Docs  = Join-Path $Root "docs"
$Staged = Join-Path $Docs "VCWarmIntro.exe"

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

if (-not (Test-Path $Exe)) {
    throw "No build found at $Exe. Run build_windows.ps1 first."
}
if (-not (Test-Path $Docs)) { New-Item -ItemType Directory $Docs | Out-Null }

Step "Staging the binary into docs\"
Copy-Item $Exe $Staged -Force
$mb = [math]::Round((Get-Item $Staged).Length / 1MB, 1)
Write-Host "    VCWarmIntro.exe ($mb MB)"

# The page prints the size; a stale figure there is a small lie worth avoiding.
$page = Join-Path $Docs "index.html"
if (Test-Path $page) {
    $html = Get-Content $page -Raw
    $updated = [regex]::Replace($html,
        'VCWarmIntro\.exe &middot; [\d.]+ MB',
        "VCWarmIntro.exe &middot; $mb MB")
    if ($updated -ne $html) {
        [System.IO.File]::WriteAllText($page, $updated, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "    updated the size shown on the page"
    }
}

if ($Serve) {
    Step "Serving locally at http://localhost:8888 (ctrl-c to stop)"
    & netlify dev --dir docs
    return
}

if ($Deploy) {
    Step "Deploying to Netlify"
    & netlify --version 2>$null
    if (-not $?) {
        throw "Netlify CLI not found. Install it with: npm install -g netlify-cli"
    }
    # --prod publishes to the live URL; drop it for a preview deploy first.
    & netlify deploy --prod --dir docs
    if ($LASTEXITCODE -ne 0) { throw "netlify deploy failed ($LASTEXITCODE)" }
    Write-Host "`nDeployed." -ForegroundColor Green
} else {
    Step "Staged, not deployed"
    Write-Host "    preview : powershell -File scripts\stage_download.ps1 -Serve"
    Write-Host "    publish : powershell -File scripts\stage_download.ps1 -Deploy"
    Write-Host "    (deploy needs 'netlify login' once, in your own terminal)"
}
