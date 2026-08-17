# Arka

**Arka is an open-source preparation workspace for Traktor DJs.** It helps you analyse tracks, place and refine HotCues, correct beatgrids, organise metadata, and create rule-based playlists—while keeping Traktor's collection.nml as the source of truth.

It is built for DJs who want useful automation with clear review and control before changes are saved.

## Core features

### AutoCue

- Analyses the master track against its existing Traktor beatgrid to find musically useful phrase transitions.
- Uses harmonic/percussive source separation (HPSS), structural energy contrast, and timbral change detection.
- Adds up to eight standard Traktor HotCues while preserving occupied slots and non-HotCue markers.
- Leaves Flex Grid tracks out of automated analysis and never replaces your beatgrid.
- Provides an interactive waveform player for auditioning and editing cues before saving.

### Smart Playlists

- Builds reusable rule sets with **match all** or **match any** logic, then compiles the results into normal Traktor playlists.
- Filters by BPM, rating, play count, import date, last-played date, genre, label, comment, and musical key.
- Includes a **Track Format** rule for **Stems**, covering Traktor 4 dynamic Stems as well as classic native .stem.mp4 files.
- Shows a dedicated Stems icon in the library when Traktor reports native Stem availability.

### Remix Studio

- A dedicated workspace for building, editing, and auditioning Traktor Remix Sets.
- Loads tracks and loops ultra-fast in memory, avoiding redundant disk reads while you work.
- Provides a DJ-style bipolar filter for every column, with LPF/HPF control, smooth audio ramps, and double-click reset.
- Maximises vertical space for Remix Pads with compact headers and controls, alongside clean Traktor-style waveforms with extended zoom levels for visual headroom.
- Uses a stable WebAudio/Tone.js engine for flawless stem isolation through Mute and Solo controls, plus reliable transport synchronisation.
- Clearly identifies Stem-ready tracks with visual indicators in the Remix Studio sidebar.

### Grid Fixer and library tools

- Edit the beatgrid directly in the player: nudge its phase, set the anchor at the playhead, shift cues with the grid when needed, and halve or double BPM.
- Browse playlists and tracks in a Traktor-style library, then batch-edit common metadata fields.
- Writes collection changes atomically and creates rotating daily backups in the adjacent CueGrid Backups folder.

### Session History

- Visualises past Traktor sessions in a comprehensive four-deck timeline of played tracks.
- Lets you review historic mixes and export any past session directly as a regular Traktor playlist.

## Designed for Traktor 4

Arka recognises current **Traktor Pro 4** process naming conventions across Windows, macOS, and Linux. It watches for Traktor while it is running and blocks collection changes until it closes, helping prevent conflicting writes to collection.nml.

The background monitor reuses its system state to minimise CPU use, including on macOS.

## Installation and usage

### Run from source

Prerequisites:

- Python 3.10 or newer
- Node.js and npm
- Rust toolchain (including the MSVC toolchain on Windows)
- A Traktor collection.nml and audio files for real-world use

Install the Core and desktop dependencies:

~~~
# Python Core
cd core
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pip install pyinstaller

# Vue + Tauri desktop app
cd ..\gui
npm install
~~~

Start Arka in development mode:

~~~
cd gui
npm run tauri dev
~~~

Choose your Traktor collection.nml when prompted (or use the collection selector in the app), load a track, review AutoCue or manual edits in the player, and select **Save Changes** only when you are ready to write them.

### Verify and build

~~~
# From the repository root
pytest core

# From gui
npm run build
~~~

The Windows production build is available from the repository root:

~~~
.\build-win.ps1
~~~

It packages the Python Core as a Tauri sidecar and places installer artifacts under gui\src-tauri\target\release\bundle\.

## Architecture

| Layer | Technology | Role |
| --- | --- | --- |
| Desktop interface | Vue 3, Tailwind CSS, Peaks.js | Library, waveform player, editing, playlist, and history workflows |
| Desktop shell | Tauri / Rust | Native application, process safety monitoring, and Core bridge |
| Analysis and collection engine | Python, Librosa, NumPy, Mutagen | Audio analysis, beatgrid logic, metadata, NML parsing, validation, and atomic writes |

~~~
core/                   Python analysis and collection engine
  src/cuegrid/          CLI, analysis, NML parsing/writing, playlist logic
  tests/                Core test suite
gui/                    Vue 3 + Tauri desktop application
  src/                  UI components, composables, stores, and types
  src-tauri/            Rust shell and sidecar configuration
.openspec/              Current implementation specifications
build-win.ps1           Windows production build pipeline
~~~

## Privacy and telemetry

On launch, Arka sends one anonymous background ping for basic usage statistics. It does not store personal data, IP addresses, or information from your Traktor library. Local analysis telemetry can be viewed and exported from the app; it is separate from this anonymous usage signal.

## Safety

Arka validates writes and saves them atomically, but a separate backup of your Traktor collection is still recommended before production use. Never edit the same collection in Arka while Traktor is open.

## Specifications

The implementation contracts live in [.openspec/](.openspec/):

- [Core engine](.openspec/2-core-spec.md)
- [Desktop GUI](.openspec/3-gui-spec.md)
- [Waveform player](.openspec/3-player-spec.md)
- [Library and metadata workflows](.openspec/4-library-spec.md)
- [Session history](.openspec/5-history-spec.md)

## License

Arka is released under the [GNU General Public License v3.0](LICENSE) (GPLv3).
