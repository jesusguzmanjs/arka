# Proposal: CueGrid (formerly Traktor Auto-Cue)

## Phase 1: Core Engine & CLI
**Objective:** Automatically detect structural changes in audio tracks and map them onto Traktor Pro 4's grid as HotCues by safely modifying `collection.nml`, utilizing grid-guided phrase analysis and automated stem extraction.

**Requirements:**
- Read Traktor's `collection.nml` to fetch the BPM, Grid Marker, and evaluate STEM flags.
- Resolve and isolate drum stems using FFmpeg for surgical audio precision.
- Analyze the track using `librosa` (RMS/MFCC) and cross-reference stems with the master track (Smart Validation).
- Snap the detected points perfectly to the mathematical beatgrid.
- Safely append `<CUE_V2>` tags without overwriting existing grids or load markers.

## Phase 2: Graphical User Interface (GUI)
**Objective:** Build a lightweight, high-performance desktop interface for CueGrid using Tauri and Vue 3, completely decoupled from the Python core.

**Requirements:**
- A single-page, dark-mode UI with a professional DJ software aesthetic.
- **Configuration Panel:** Inputs for Target (Playlist/Track), Verify Mode (fast/smart), Sensitivity (soft/medium/hard), and a Clear Existing toggle.
- **Action Area:** A prominent "Analyze & Inject" button with clear loading/processing states.
- **Telemetry Console:** A real-time log viewer displaying the core's output.
- **Architecture:** The Python engine will run as a Tauri Sidecar, communicating with the frontend via structured JSON over `stdout`.
