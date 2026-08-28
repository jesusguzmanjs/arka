$ErrorActionPreference = "Stop"

$RepoRoot    = $PSScriptRoot
$CoreDir     = Join-Path $RepoRoot "core"
$GuiDir      = Join-Path $RepoRoot "gui"
$ResourcesDir = Join-Path $GuiDir "src-tauri\resources\cuegrid-core"

Write-Host "=== Arka production build (Windows) ===" -ForegroundColor Cyan
Write-Host ""

# ── 1/3: Frontend production build ──────────────────────────────────────
Write-Host "[1/3] Building Vue 3 frontend (npm run build)..." -ForegroundColor Cyan
npm run --prefix $GuiDir build
if ($LASTEXITCODE -ne 0) {
    Write-Host "-> ERROR: frontend build failed." -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "-> gui/dist ready." -ForegroundColor Green
Write-Host ""

# ── 2/3: Freeze the Python core with PyInstaller (--onedir) ─────────────
Write-Host "[2/3] Freezing Python core with PyInstaller (--onedir)..." -ForegroundColor Cyan
Push-Location $CoreDir
try {
    # Al estar dentro de 'core', el PYTHONPATH es el directorio actual
    $env:PYTHONPATH = "."

    # La ruta parte directamente de 'cuegrid'
    pyinstaller --clean --noconfirm --onedir --name cuegrid-core cuegrid/cli.py

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    Pop-Location
}

$SourceDir = Join-Path $CoreDir "dist\cuegrid-core"
if (-not (Test-Path $SourceDir)) {
    Write-Host "-> ERROR: expected PyInstaller output not found at $SourceDir" -ForegroundColor Red
    exit 1
}

# Limpiar carpeta de recursos anterior y copiar la nueva
if (Test-Path $ResourcesDir) {
    Remove-Item -Recurse -Force $ResourcesDir
}
New-Item -ItemType Directory -Path (Split-Path $ResourcesDir) -Force | Out-Null
Copy-Item -Recurse -Force -Path $SourceDir -Destination $ResourcesDir
Write-Host "-> Core resource folder placed at gui\src-tauri\resources\cuegrid-core" -ForegroundColor Green
Write-Host ""

# ── 3/3: Native Tauri build ─────────────────────────────────────────────
Write-Host "[3/3] Running tauri build..." -ForegroundColor Cyan
Push-Location $GuiDir
try {
    npx tauri build
    if ($LASTEXITCODE -ne 0) {
        throw "tauri build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "====== BUILD COMPLETE ======" -ForegroundColor Green
