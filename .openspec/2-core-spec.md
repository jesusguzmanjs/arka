# Core Specification: CueGrid

Status: Current implementation contract, synchronized 2026-07-16.

The checked-out Python source under `core/src/cuegrid/` is the source of truth. This specification records the active behavior; it does not preserve retired designs as future work.

## 1. Scope and package layout

CueGrid reads a Traktor `collection.nml`, derives phrase-boundary candidates from a fixed beat grid, confirms selected candidates by audio analysis, and writes standard HotCues into the matching collection entry.

```text
core/src/cuegrid/
  cli.py                 argparse, one-shot queries, NDJSON framing
  config.py              AppConfig defaults and sensitivity presets
  audio/beatgrid.py      pure grid/candidate arithmetic
  audio/detector.py      full-track decode, RAM slicing, HPSS detection
  audio/features.py      structural contrast and MFCC scoring
  audio/loader.py        active preview payload generation and seek utility
  audio/legacy_stems.py  excluded FFmpeg/Stem reference code
  core/mapping.py        event-to-free-HotCue mapping
  core/pipeline.py       single-track and sequential batch orchestration
  nml/parser.py          read-only XML/path/library parsing
  nml/writer.py          atomic XML mutation and backup management
```

The core deliberately does not:

- perform whole-track novelty-curve peak-picking;
- estimate or snap a replacement beat grid;
- label events as intro, drop, breakdown, or outro;
- use a Stem sidecar, FFmpeg extraction, or master/drum fusion in the active analysis pipeline;
- process batches concurrently.

The checkout retains legacy Stem reference helpers in `audio/legacy_stems.py` and `nml/stems.py`, plus legacy AppConfig fields. Neither legacy module is imported by `cli.py`, `detector.py`, or either pipeline path. The PyInstaller recipe explicitly excludes both modules and `ffmpeg`; `ffmpeg-python` is not a project dependency.

## 2. Data model and NML parsing

`NmlParser` loads an NML once and retains its `ElementTree` and normalized `nml_path`. `NmlWriter` receives that same parser and mutates the same tree; analysis does not reparse the file before writing.

`TrackEntry` contains:

| Field | Source |
|---|---|
| `title`, `artist` | `<ENTRY TITLE>` and `<ENTRY ARTIST>` |
| `location_path` | normalized `<LOCATION>` path |
| `tempo.bpm` | `<TEMPO BPM>` |
| `grid_anchor_ms` | last `CUE_V2 TYPE="4"` `START` |
| `is_flex_grid` | true when more than one `TYPE="4"` marker exists |
| `duration_ms` | `INFO.PLAYTIME_FLOAT * 1000`, falling back to `INFO.PLAYTIME * 1000` |
| `cues` | parsed `CUE_V2` elements |
| `peak_db`, `perceived_db` | optional `<LOUDNESS>` attributes |
| `audio_id`, `flags` | retained NML metadata for callers/UI; not active detector inputs |

Path matching normalizes Traktor `VOLUME`/`DIR`/`FILE` values and a supplied filesystem path. A duplicate location requires `--title` and/or `--artist` to disambiguate; unmatched and still-ambiguous lookups are errors.

Playlist selection uses an exact, case-sensitive playlist `NAME`. Duplicate names raise `AmbiguousPlaylistError`; stale, malformed, unresolved, or per-track ambiguous playlist references are logged and skipped. Title batch selection uses a case-insensitive exact title comparison and may be narrowed with a case-insensitive artist match.

## 3. Grid candidate arithmetic

`audio.beatgrid` is pure arithmetic. For BPM `B`, grid anchor `G` in milliseconds, and phrase interval `P` beats:

```text
beat_length_ms = 60000 / B
time_ms(n) = G + n * P * beat_length_ms, n = 0, 1, ...
```

Candidates continue while `time_ms <= duration_ms`. A candidate has `beat_index = n * P` and a traceability-only `is_major_phrase` flag when `n % major_phrase_multiple == 0`.

Defaults in `AppConfig` are `phrase_beats=4` and `major_phrase_multiple=1`. Invalid/nonpositive BPM or duration produces no candidates. A candidate is already grid-exact; no downstream snapping occurs.

## 4. Detection implementation

### 4.1 Memory and decode contract

For a call to `detect_events` that has candidates, `_score_candidates` makes one local master-track decode:

```python
full_y, full_sr = librosa.load(str(audio_path), sr=config.sample_rate, mono=True)
```

It does not use a module-level or process-wide audio cache. For each candidate, before and after windows are bounded NumPy slices of `full_y`. In a `finally` block the local waveform is deleted and `gc.collect()` is called. `sample_rate=None` preserves the source sample rate; a supplied value requests librosa resampling for this one decode.

The detector computes candidates before decoding. It rejects any candidate before beat 8 and any candidate at or beyond `duration_ms - 8 * beat_length_ms` before slicing or DSP.

### 4.2 Window features

Each valid before/after slice is processed independently:

1. `librosa.stft(..., n_fft=1024, hop_length=config.hop_length)` creates a magnitude spectrogram.
2. `librosa.decompose.hpss(..., kernel_size=15)` produces harmonic and percussive spectrograms.
3. Mean RMS is measured for each component with a 1024-sample frame length.
4. Mean MFCC values are measured from the original slice (`config.mfcc_count`, default 13).

Non-finite HPSS/MFCC values reject that candidate as unscorable. A candidate whose after percussive RMS is at or below `1e-4`, or below 10% of the sampled track-average after percussive RMS, cannot remain significant.

### 4.3 Structural scoring and selection

For harmonic and percussive RMS values, the code uses signed decibel changes:

```text
delta_db(before, after) = 20 * log10(max(after, eps) / max(before, eps))
```

The structural contrast is the stronger of:

- a percussive rise not mirrored by harmonic movement: `max(0, percussive_delta - abs(harmonic_delta))`;
- a percussive loss while harmonic energy holds/rises: `max(0, -percussive_delta + harmonic_delta)` when harmonic delta is nonnegative, returned with negative sign.

The confidence is:

```text
abs(structural_delta) / energy_threshold
+ MFCC_distance / timbre_threshold
```

A candidate is significant when either absolute structural contrast reaches `energy_change_threshold_db` or MFCC distance reaches `timbre_change_distance_threshold`. Defaults are 4.0 dB and 18.0 respectively.

Significant candidates receive a soft spatial weighting after the strict edge bounds:

```text
x = clamp(time_ms / duration_ms, 0, 1)
W(x) = 1 - spatial_penalty_alpha * (2x - 1)^2
final_confidence = confidence * W(x)
```

`spatial_penalty_alpha` defaults to 0.6. Candidates below `relative_confidence_threshold` times the strongest weighted candidate are discarded; remaining candidates are sorted by weighted confidence, capped at `max_cues`, and returned chronologically. Defaults are 0.30 and 8.

Every returned `DetectedEvent` has `label == "cue"`. `is_major_phrase` is retained only as event metadata. There are no role-derived names or role-based quotas.

## 5. Mapping and safe persistence

`core.mapping.map_events_to_cues` assigns the lowest available HotCue slot from 0 through 7. It writes `CueType.CUE` / NML `TYPE="0"` with the display name `Cue`. Existing slots are preserved unless `clear_existing=True` was requested.

When BPM/grid information is available (the normal pipeline path), the mapper rejects an event fewer than eight beats from a retained existing cue or from a previously accepted new cue. If all slots are occupied, later events are skipped rather than overwriting a cue.

`NmlWriter`:

- appends automated standard `CUE_V2` nodes and never changes Grid (`TYPE="4"`) or Load (`TYPE="3"`) markers;
- removes only standard HotCues when `clear_existing=True`;
- formats numeric cue values to six decimal places;
- creates at most one daily backup at `CueGrid Backups/<collection.nml>.YYYYMMDD.bak` alongside the NML, retaining the five most recent such backups;
- writes to `<collection.nml>.tmp` then atomically replaces the target;
- validates manual-update payloads before XML mutation and restores the in-memory tree if the write fails.

Flex Grid tracks are protected: single-track and batch analysis return a `flex_grid` skip before decoding. A manual grid-anchor update requires exactly one Grid marker.

## 6. Pipeline behavior

`run_pipeline` performs one path-selected analysis:

```text
parse NML -> resolve entry -> Flex Grid guard -> generate/detect events
-> map free cues -> mutate and atomically write when cues exist
```

`run_batch_pipeline` requires exactly one of `playlist` or `track_title`. It resolves references once, then processes entries sequentially. Each eligible successful entry may be written immediately; failures and invalid BPM/Flex Grid entries become an individual `BatchTrackResult` and do not abort the remaining batch. An optional callback is invoked after each result, enabling live NDJSON lifecycle output.

Both pipelines reset the telemetry file at the start of a run.

## 7. CLI contract

### 7.1 Analysis selectors

Exactly one selector is required for an analysis run:

```text
cuegrid TRACK_PATH
cuegrid --track-title TITLE [--artist ARTIST]
cuegrid --playlist NAME
```

`--title` is valid only with `TRACK_PATH`. `--artist` narrows a single track or title batch and is invalid with `--playlist`. `--nml PATH` overrides discovery; otherwise the CLI chooses the most recently modified `collection.nml` in standard Native Instruments document locations.

`--clear-existing`, all tuning flags, `--export-csv`, `--json`, and `-v/--verbose` apply to analysis. `--export-gui` is a single-track-only output mode and cannot be combined with `--json`.

### 7.2 One-shot read-only commands

These commands bypass audio analysis and the normal selector requirement:

| Command | Success output |
|---|---|
| `--discover-nml` | `{ "path": "..." }`, or an error object |
| `--list-playlists` | JSON array of non-system playlist names in document order |
| `--get-playlist-tracks NAME` | JSON array of `{artist,title,location_path,flags,is_flex_grid}` |
| `--get-track-metadata TRACK_PATH` | one Super JSON metadata/preview object |
| `--get-library` | relational `{collection, playlists}` JSON object |

`--get-library` indexes collection entries by normalized location path. Each collection value includes the normal metadata payload plus `location_path`, `duration_ms`, and `collection_index`. Playlist/folder nodes retain hierarchy using `kind`, `name`, `children` (folders), or `track_paths` (playlists); they do not duplicate track metadata. Duplicate normalized collection locations return `{"error":"duplicate_location",...}` with a nonzero exit code.

The Super JSON returned by `--get-track-metadata` contains `artist`, `title`, `bpm`, `grid_anchor_ms`, `is_flex_grid`, non-grid `existing_cues`, `waveform_peaks`, and `color_map`. Preview generation is independent of detection: it makes a complete mono decode at 11,025 Hz, uses 64-sample waveform extrema converted to signed `int8` values, and produces normalized low/mid/high colour buckets every 500 ms.

### 7.3 Standalone mutations

```text
cuegrid TRACK_PATH --delete-cue HOTCUE_INDEX [--nml PATH] [--title TITLE] [--artist ARTIST]
cuegrid TRACK_PATH --update-cues JSON [--grid-anchor MS] [--bpm BPM] [--nml PATH]
```

`--delete-cue` removes only one existing standard `TYPE="0"` HotCue with zero-based index 0–7. `--update-cues` accepts a JSON array of objects containing numeric `hotcue` and `start_ms`; it updates existing standard HotCues in place or creates missing ones. `--grid-anchor` and `--bpm` are accepted only with `--update-cues`; BPM must be finite and between 50 and 200 inclusive. Neither mutation runs the audio pipeline.

### 7.4 NDJSON analysis output

`--json` writes compact, line-flushed JSON objects to stdout. Normal logging remains on stderr. A successful analysis emits:

```text
nml_resolved
track_start
event_detected*
cue_written*
track_complete
summary
```

The exact message fields are implemented by `_json_*` helpers in `cli.py`. `event_detected` includes the unified `cue` label, time, confidence, and `is_major_phrase`. A Flex Grid skip also emits `skipped` with reason `flex_grid`. Failed top-level resolution/selection operations emit `log` with `level="error"` and exit nonzero.

### 7.5 Current compatibility flags

The parser still exposes `--stems-dir`, `--verify {fast,smart}`, and `--no-stems`. They remain accepted and their values can reach `AppConfig`, but they do not change the active detector or pipeline source. `--verify` is consulted only by a human-readable cue-printing branch. New functionality must not depend on these legacy flags without an explicit specification change.

## 8. Configuration defaults

| Field / CLI | Default |
|---|---:|
| `--phrase-beats` | 4 |
| `--major-phrase-multiple` | 1 |
| `--sample-rate` | native rate (`None`) |
| `--hop-length` | 512 |
| `--window-beats` | 2.0 |
| `--mfcc-count` | 13 |
| `--energy-threshold` | 4.0 |
| `--timbre-threshold` | 18.0 |
| `--relative-confidence-threshold` | 0.30 |
| `--max-cues` | 8 |
| `spatial_penalty_alpha` | 0.6 (no CLI flag) |

`--mode soft`, `medium`, and `hard` override the energy/timbre/relative triple with `(2.0, 8.0, .15)`, `(4.0, 18.0, .30)`, and `(7.0, 30.0, .50)` respectively.

## 9. Telemetry

Each pipeline run replaces the application-owned telemetry file in the operating-system temporary directory at `cuegrid/last_run_telemetry.csv`; candidate rows are appended as tracks are scored. `--export-csv PATH` appends the same rows to the requested file.

The schema is:

```text
track_title,Formatted_Time,beat,time_ms,energy_delta_db,harmonic_delta_db,
percussive_delta_db,timbre_dist,original_confidence,spatial_weight,confidence,
status,track_peak_db,track_perceived_db
```

There are no drum-score, drum-weight, or Smart-Mode telemetry columns in the active detector.

## 10. Change-control rule

Before changing Python behavior, update this specification when the intended behavior is not already described here. When this document conflicts with checked-out code, the code is authoritative until the documentation is synchronized.
