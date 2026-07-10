# Script de sincronización automática para CueGrid

# 1. Forzar entorno limpio y compilar Python con PyInstaller
Write-Host "`n[1/3] Compilando motor de Python con PyInstaller..." -ForegroundColor Cyan
cd core
$env:PYTHONPATH="src"
pyinstaller --clean --onefile --name cuegrid src/cuegrid/cli.py
cd ..

# 2. Mover el ejecutable a la carpeta de binarios de Tauri
Write-Host "`n[2/3] Mudando nuevo binario a la estructura de Rust..." -ForegroundColor Cyan
$SourceFile = "core\dist\cuegrid.exe"
$DestFile = "gui\src-tauri\binaries\cuegrid-x86_64-pc-windows-msvc.exe"

if (Test-Path $SourceFile) {
    Move-Item -Force -Path $SourceFile -Destination $DestFile
    Write-Host "-> Binario copiado con éxito." -ForegroundColor Green
} else {
    Write-Host "-> ERROR: No se encontró el ejecutable compilado por PyInstaller." -ForegroundColor Red
    Exit
}

# 3. El truco maestro: Actualizar el timestamp de lib.rs para engañar al watcher de Cargo
Write-Host "`n[3/3] Forzando recarga en caliente de Tauri..." -ForegroundColor Cyan
$LibRsPath = "gui\src-tauri\src\lib.rs"
if (Test-Path $LibRsPath) {
    (Get-Item $LibRsPath).LastWriteTime = Get-Date
    Write-Host "====== ¡HECHO! Tu app de escritorio se está reiniciando sola con el nuevo código ======" -ForegroundColor Green
} else {
    Write-Host "-> AVISO: No se pudo tocar lib.rs, si Tauri está corriendo reinícialo a mano." -ForegroundColor Yellow
}
