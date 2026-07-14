# Proposal: CueGrid (formerly Traktor Auto-Cue)

## Current implementation baseline (2026-07-13)

The checked-out implementation is the source of truth for the current
architecture. The Python package lives under `core/src/cuegrid/` and the
Vue/Tauri application lives under `gui/`. The GUI is a dark, single-window
application with a strictly bounded `h-screen w-screen overflow-hidden` root;
scrolling is delegated to bounded internal panels.

The visual language uses the semantic Amber/Ochre roles declared in
`gui/tailwind.config.js`: `primary`, `secondary`, `accent`, and `warning`.
The player is implemented with Peaks.js and supports eight fixed virtual pad
slots, momentary pad preview, guarded global keyboard shortcuts, custom wheel
zoom/pan, and cue context-menu deletion. Manual cue changes are held locally
until an explicit save request crosses the Tauri sidecar boundary.

The packaged Python core is invoked as `binaries/cuegrid-core`. It can discover
and retain the selected `collection.nml` path, serve read-only metadata and
playlist queries, stream analysis results as NDJSON, update manual cue
positions through `--update-cues`, and delete a standard HotCue through
`--delete-cue`. The parser owns the loaded XML tree and path; the pipeline and
writer reuse that in-memory document and atomically persist mutations.

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
- **Configuration and Library UI:** Playlist selection, Stems inclusion, Sensitivity (`soft`/`medium`/`hard`), Max Cues, Clear Existing, and playlist/track analysis actions. The old free-text Target Selector and GUI Verify Mode control are no longer the current UI contract.
- **Action Area:** A prominent "Analyze & Inject" button with clear loading/processing states.
- **Telemetry Console:** A real-time log viewer displaying the core's output.
- **Architecture:** The Python engine runs as a Tauri Sidecar, communicating with the frontend through one-shot JSON queries, NDJSON analysis output, and explicit cue mutation commands over the sidecar process boundary.
