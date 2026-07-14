# Script de sincronización automática para CueGrid (Versión RESOURCES 0ms)

Write-Host "`n[1/3] Compilando motor de Python con PyInstaller (ONEDIR)..." -ForegroundColor Cyan
cd core
$env:PYTHONPATH="src"
pyinstaller --clean --noconfirm cuegrid.spec
cd ..

Write-Host "`n[2/3] Mudando motor a la carpeta de Recursos de Tauri..." -ForegroundColor Cyan

$SourceFolder = "core\dist\cuegrid-core"
$DestFolder = "gui\src-tauri\resources\cuegrid-core"

if (Test-Path $SourceFolder) {
    if (Test-Path $DestFolder) {
        Remove-Item -Recurse -Force -Path $DestFolder
    }

    Copy-Item -Recurse -Force -Path $SourceFolder -Destination $DestFolder
    Write-Host "-> Carpeta de motor (ONEDIR) copiada a resources con éxito." -ForegroundColor Green
} else {
    Write-Host "-> ERROR: No se encontró la compilación en core\dist\cuegrid-core." -ForegroundColor Red
    Exit
}

Write-Host "`n[3/3] Forzando recarga en caliente de Tauri..." -ForegroundColor Cyan
$LibRsPath = "gui\src-tauri\src\lib.rs"
if (Test-Path $LibRsPath) {
    (Get-Item $LibRsPath).LastWriteTime = Get-Date
    Write-Host "====== ¡HECHO! App reiniciando con el motor nativo ======" -ForegroundColor Green
}