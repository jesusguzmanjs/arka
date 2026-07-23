# CueGrid

**CueGrid is an advanced preparation and library-management workspace for Traktor DJs.** It pairs intelligent, beatgrid-aware phrase analysis with a visual waveform player, native collection browsing, batch metadata editing, rule-based playlists, and session-history tools—so the work around a set happens in one focused desktop application.

CueGrid works with Traktor's `collection.nml` as the source of truth. It helps you prepare more deliberately while keeping your grids, library structure, and DJ workflow familiar.

## Why CueGrid

Preparing a library should not mean choosing between automation and control. CueGrid uses Traktor's existing beatgrid to identify musically useful cue candidates, then gives you a full visual workspace to review, audition, edit, organize, and save those changes safely.

- Find high-value phrase transitions without replacing the beatgrid you already trust.
- Review and refine cues directly against an interactive waveform before committing changes.
- Manage tracks, playlists, metadata, and past sessions without leaving the application.
- Save related changes together through one validated, atomic update to your Traktor collection.

## Highlights

### Intelligent Auto Cue

CueGrid's analysis is deliberately **grid-guided**. It generates candidates from the existing Traktor grid, then analyzes the master track with harmonic/percussive source separation (HPSS), structural energy contrast, and timbral change detection.

- Respects the existing BPM and grid anchor; it does not estimate or replace your beatgrid.
- Scores phrase-boundary candidates with Librosa HPSS and MFCC-based timbral contrast.
- Uses a spatial plateau weighting that favors the central area of a track without flattening the whole arrangement.
- Applies dynamic proximity suppression: cues too close together are rejected and nearby candidates progressively recover with distance, producing a more natural distribution.
- Supports up to eight standard Traktor HotCues while preserving occupied slots and non-HotCue markers.
- Protects Flex Grid tracks from automated analysis.

### Integrated Waveform Player

The built-in player is made for making confident decisions quickly, not just looking at audio.

- **Peaks.js** zoom and overview waveforms with a custom CSS-mask HPSS color layer.
- Visible beat and bar lines derived from the active grid, plus on-grid snapping for manual cue edits.
- Eight fixed, Traktor-style virtual HotCue pads—use the mouse or keys `1`–`8` for momentary auditioning; use an empty pad to create a cue and `Shift` + number to remove one.
- Relative `±8`-beat jumps for fast phrase navigation.
- A dedicated **Grid Edit Mode** for nudging the phase/anchor, setting the grid to the playhead, optionally shifting cues with it, and halving or doubling BPM.
- Manual edits remain local until you choose **Save Changes**, so you can review the complete result before anything touches the collection.

### Native Library Browser and Metadata Workflow

CueGrid provides a Traktor-style, two-column browser for working across your collection.

- Playlist tree and tracklist in one native browser, with resizable and sortable columns.
- Select one track or many, then apply batch metadata edits in a single operation.
- Edit core library fields such as title, artist, release, remixer, producer, genre, label, comments, lyrics, mix, key, and rating.
- Persist changes atomically to `collection.nml`; supported audio files can also receive matching ID3, Vorbis, or MP4 tag updates via Mutagen.
- Keep edits staged until an explicit save, avoiding accidental partial updates.

### Smart Playlists

Build reusable selection logic, then compile it into regular static Traktor playlists.

- Combine rules with **all** or **any** matching.
- Filter by BPM, rating, import date, last played date, play count, genre, label, comment, and musical key.
- BPM matching accounts for common half- and double-tempo library representations.
- Re-running a Smart Playlist refreshes its generated Traktor playlist using the current collection.

### Session History Timeline

Turn past Traktor sessions into useful library context.

- Browse a visual, four-deck timeline of saved Traktor history sessions.
- Compress global inactivity gaps longer than 15 minutes into clear timeline breaks, keeping the view readable while retaining real elapsed-time context.
- Distinguish public playback from cue/monitor activity with separate visual treatment.
- Import a session directly as a normal saved Traktor playlist, preserving chronological order and repeated plays.

## Built for Traktor, with Careful Writes

CueGrid does not treat `collection.nml` casually. GUI-originated track and playlist changes are assembled into one complete payload, validated before mutation, then written as one atomic operation. Each successful mutation creates a rotating daily backup alongside the collection in `CueGrid Backups`.

Automated cue writes preserve Grid, Load, and other non-standard cue markers. Use an independent backup of your Traktor collection as part of any production workflow.

## Architecture

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Desktop interface | Vue 3, Tailwind CSS, Peaks.js | Library workspace, waveform player, metadata and playlist workflows, session timeline |
| Desktop shell | Tauri / Rust | Native desktop window, file/resource access, and the bridge to the Core sidecar |
| Analysis and collection engine | Python, Librosa, NumPy, Mutagen | HPSS analysis, beatgrid math, metadata handling, NML parsing, validation, and atomic persistence |

For packaged builds, the Python engine is frozen with PyInstaller and shipped as a Tauri sidecar resource. The UI invokes it through the Tauri bridge rather than requiring a user-managed Python installation.

## Project Layout

```text
core/                   Python analysis and collection engine
  src/cuegrid/          CLI, audio analysis, NML parser/writer, playlist logic
  tests/                Core test suite
gui/                    Vue 3 + Tauri desktop application
  src/                  UI components, composables, stores, and types
  src-tauri/            Rust shell, capabilities, and sidecar configuration
.openspec/              Current implementation specifications
build-win.ps1           Windows production build pipeline
```

## Run Locally

### Prerequisites

- Windows (the packaged build pipeline is currently PowerShell/Windows-oriented)
- Python 3.10 or newer
- Node.js and npm
- Rust with the MSVC toolchain
- A Traktor `collection.nml` and audio files for real-world testing

For production packaging, install PyInstaller in the same Python environment as the Core dependencies.

### Install development dependencies

```powershell
# Python Core
cd core
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pip install pyinstaller

# Vue / Tauri application
cd ..\gui
npm install
```

### Start the desktop app

From the `gui` directory:

```powershell
npm run tauri dev
```

This starts the Vue development server and launches CueGrid through Tauri. For a UI-only browser development server, use `npm run dev` instead.

### Verify the project

```powershell
# From the repository root
pytest core

# From gui
npm run build
```

`npm run build` performs Vue type-checking before producing the frontend bundle.

## Build a Windows Release

From the repository root, with the Core virtual environment activated:

```powershell
.\build-win.ps1
```

The script:

1. Builds and type-checks the Vue frontend.
2. Freezes the Python Core with PyInstaller and places it in Tauri's target-specific sidecar location.
3. Runs the Tauri production build.

The installer artifacts are produced beneath `gui\src-tauri\target\release\bundle\`.

## Specifications

The implementation contracts live in [`.openspec/`](.openspec/):

- [Core engine](.openspec/2-core-spec.md)
- [Waveform player](.openspec/3-player-spec.md)
- [Library and metadata workflows](.openspec/4-library-spec.md)
- [Session history](.openspec/5-history-spec.md)

## License

No license is currently declared for this repository.
