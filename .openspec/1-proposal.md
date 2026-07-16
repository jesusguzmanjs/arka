# Proposal: CueGrid

Status: implemented baseline, synchronized 2026-07-16.

CueGrid is a desktop-assisted Python application that injects grid-aligned HotCues into Native Instruments Traktor collections. The checked-out code is the product baseline; detailed implementation contracts live in [2-core-spec.md](2-core-spec.md), [3-gui-spec.md](3-gui-spec.md), [3-player-spec.md](3-player-spec.md), and [4-library-spec.md](4-library-spec.md).

## Product goal

Help DJs identify structural transitions without inventing a second beat grid. CueGrid takes Traktor's BPM and Grid marker as authoritative, evaluates phrase-boundary candidates against the master audio, and writes only safe standard HotCue changes back to the matching `collection.nml` entry.

## Current core behavior

- Grid-Guided Phrase Analysis calculates all candidate timestamps from Traktor BPM, grid anchor, duration, and the selected phrase interval.
- The detector performs one local full-track `librosa.load` per analysis run, slices each before/after window in RAM, releases the waveform, and calls garbage collection. It uses no global audio cache.
- Each candidate uses HPSS harmonic/percussive RMS and MFCC timbre evidence. Events that survive edge, silence, threshold, spatial, relative-confidence, and capacity guards all use the label `cue`.
- The active pipeline is master-track-only. FFmpeg Stem extraction and master/drum fusion are retired from the analysis path; reference-only helpers live in PyInstaller-excluded legacy modules and are not dependencies of CueGrid.
- Single-track, playlist, and title-selected batch processing are implemented. Batch execution is sequential and isolates individual track failures.
- The CLI supplies NDJSON analysis progress, Super JSON track-preview data, playlist/library queries, discovery, manual cue/grid/BPM updates, and standard HotCue deletion.
- NML writes are atomic and create retained daily backups. Flex Grid tracks are protected from automatic analysis.

## Current desktop behavior

The Vue + Tauri application invokes the packaged core as a sidecar. It uses one-shot JSON for metadata/library operations and NDJSON for analysis. The player presents Peaks.js waveforms, eight fixed HotCue pads, local manual edits, and explicit save/delete flows. The library browser uses the relational `--get-library` response as its primary source.

## Delivery constraints

- Preserve Traktor's grid and non-HotCue markers.
- Never overwrite an occupied standard HotCue slot unless the user chose Clear Existing.
- Keep the core and GUI process boundary explicit: filesystem mutation remains in the Python sidecar.
- Treat the checked-out code as authoritative whenever historical documentation disagrees with it; synchronize the specifications in the same change.
