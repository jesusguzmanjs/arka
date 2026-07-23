#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
CORE_DIR="$REPO_ROOT/core"
GUI_DIR="$REPO_ROOT/gui"
RESOURCES_DIR="$GUI_DIR/src-tauri/resources/cuegrid-core"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}=== CueGrid production build (macOS) ===${NC}\n"

# ── 1/3: Frontend production build ──────────────────────────────────────
echo -e "${CYAN}[1/3] Building Vue 3 frontend (npm run build)...${NC}"
npm run --prefix "$GUI_DIR" build
echo -e "${GREEN}-> gui/dist ready.${NC}\n"

# ── 2/3: Freeze the Python core with PyInstaller (--onedir) ─────────────
echo -e "${CYAN}[2/3] Freezing Python core with PyInstaller (--onedir)...${NC}"
cd "$CORE_DIR"
export PYTHONPATH="src"
pyinstaller --clean --noconfirm --onedir --name cuegrid-core src/cuegrid/cli.py

SOURCE_DIR="$CORE_DIR/dist/cuegrid-core"
if [ ! -d "$SOURCE_DIR" ]; then
    echo -e "${RED}-> ERROR: expected PyInstaller output not found at $SOURCE_DIR${NC}"
    exit 1
fi

# Limpiar carpeta de recursos anterior y copiar la nueva
rm -rf "$RESOURCES_DIR"
mkdir -p "$(dirname "$RESOURCES_DIR")"
cp -R "$SOURCE_DIR/" "$RESOURCES_DIR"

# CRÍTICO: Dar permisos de ejecución al ejecutable principal dentro de la carpeta
chmod +x "$RESOURCES_DIR/cuegrid-core"

echo -e "${GREEN}-> Core resource folder placed at gui/src-tauri/resources/cuegrid-core${NC}\n"

# ── 3/3: Native Tauri build ─────────────────────────────────────────────
echo -e "${CYAN}[3/3] Running tauri build...${NC}"
cd "$GUI_DIR"
npx tauri build

echo -e "\n${GREEN}====== BUILD COMPLETE ======${NC}"