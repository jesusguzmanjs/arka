# Core Specification: CueGrid

Status: Current implementation contract, synchronized 2026-07-16. Smart Playlist architecture added 2026-07-21.

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
| `key` | optional `<INFO KEY>` musical key; `null` when absent or empty |
| `duration_ms` | `INFO.PLAYTIME_FLOAT * 1000`, falling back to `INFO.PLAYTIME * 1000` |
| `cues` | parsed `CUE_V2` elements |
| `peak_db`, `perceived_db` | optional `<LOUDNESS>` attributes |
| `audio_id`, `flags` | retained NML metadata for callers/UI; `flags` is a non-negative integer parsed from `INFO@FLAGS` and defaults to `0` when absent or malformed; neither is an active detector input |

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

Non-finite HPSS/MFCC values reject that candidate as unscorable. The detector does not calculate or apply a sampled track-average percussive-energy floor.

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

After the strict edge bounds and significance test, a candidate with missing after-window percussive RMS, or with `percussive_after <= 1e-4`, is rejected as `REJECTED_SILENCE`. Remaining significant candidates receive a soft spatial weighting with a central plateau:

```text
x = clamp(time_ms / duration_ms, 0, 1)
distance_from_center = abs(x - 0.5)
plateau_radius = 0.10
falloff_range = 0.40
d_norm = max(0, distance_from_center - plateau_radius) / falloff_range
spatial_weight = 1 - spatial_penalty_alpha * d_norm^2
base_confidence = confidence * spatial_weight
```

The central 20% of the track (`0.40 <= x <= 0.60`) is the plateau: `d_norm = 0` and `spatial_weight = 1.0`, so it receives no spatial penalty. Outside the plateau, `d_norm` increases linearly from the plateau edge toward the track boundary, and the spatial deduction increases quadratically; at either boundary `d_norm = 1`. `spatial_penalty_alpha` defaults to 0.6.

Candidates below `relative_confidence_threshold` times the strongest `base_confidence` are discarded as `DISCARDED_LIMIT`. The survivors are selected dynamically, up to `max_cues` (defaults: 0.30 and 8). At each iteration, the detector recomputes every remaining candidate's proximity factor against the nearest timestamp already accepted:

```text
delta_beats = min(abs(candidate_time_ms - accepted_time_ms)) / beat_length_ms(bpm)

proximity_weight(delta_beats) =
    0.0,                                      delta_beats < 4
    ((delta_beats - 4) / (32 - 4))^2,         4 <= delta_beats < 32
    1.0,                                      delta_beats >= 32

final_confidence = base_confidence * proximity_weight
```

The highest recomputed `final_confidence` is accepted on each iteration. A candidate within four beats of an accepted cue has factor `0.0`; it is removed from the pool, and if the best remaining candidate has zero proximity weight, every remaining candidate is marked `DISCARDED_TOO_CLOSE`. Candidates 4–32 beats away recover quadratically, and candidates at least 32 beats away receive no proximity penalty. Any remaining candidates after reaching `max_cues` are marked `DISCARDED_LIMIT`. Returned events are sorted chronologically.

Every returned `DetectedEvent` has `label == "cue"`. `is_major_phrase` is retained only as event metadata. There are no role-derived names or role-based quotas.

## 5. Mapping and safe persistence

### Batch-saving transaction boundary

CueGrid uses a batch-saving persistence model for GUI-originated **track and
playlist** edits. The backend MUST NOT receive or write an individual
persistence request for each track's metadata, cue, grid, BPM, or playlist
edit. Instead, the GUI keeps those edits in memory and submits one batch
payload only when the user explicitly invokes **Save Changes**. Closing the
application without saving discards the in-memory batch.

The batch payload is the sole persistence boundary for a track-editing session.
It identifies every modified track and contains its final in-memory changes.
The core validates the complete payload before mutation and performs one atomic
NML write using the existing backup, temporary-file, replace, and rollback
guarantees. It must not expose per-edit track-save endpoints as an alternative
GUI write path. Any invalid track entry rejects the entire transaction.

The current UI save-store implementation simulates this batch request while
the concrete payload serialization is introduced. Its one-second delay is UI
behavior only and is not a backend persistence contract.

`core.mapping.map_events_to_cues` assigns the lowest available HotCue slot from 0 through 7. It writes `CueType.CUE` / NML `TYPE="0"` with the display name `Cue`. Existing slots are preserved unless `clear_existing=True` was requested.

When BPM/grid information is available (the normal pipeline path), the mapper rejects an event fewer than eight beats from a retained existing cue or from a previously accepted new cue. If all slots are occupied, later events are skipped rather than overwriting a cue.

`NmlWriter`:

- appends automated standard `CUE_V2` nodes and never changes Grid (`TYPE="4"`) or Load (`TYPE="3"`) markers;
- removes only standard HotCues when `clear_existing=True`;
- formats numeric cue values to six decimal places;
- creates at most one daily backup at `CueGrid Backups/<collection.nml>.YYYYMMDD.bak` alongside the NML, retaining the five most recent such backups;
- writes to `<collection.nml>.tmp` then atomically replaces the target;
- validates the complete `--batch-save` payload before XML mutation and restores the in-memory tree if the write fails.

AutoCue batch analysis also uses one retained parser tree: it creates one
backup before processing the resolved batch, appends each successful track's
new standard HotCue nodes in memory, and performs one atomic NML write after
the complete processing loop only when at least one track produced cues.

Flex Grid tracks are protected: single-track and batch analysis return a `flex_grid` skip before decoding. A batch-saved grid-anchor update requires exactly one Grid marker.

### 5.1 Smart Playlist compilation

A **Smart Playlist** is a CueGrid rule definition evaluated against the current
`<COLLECTION>` entries and compiled into a normal, static Traktor playlist.
CueGrid does not write a proprietary dynamic-playlist representation to the
NML. Re-running a Smart Playlist operation evaluates its rules again and
replaces the compiled contents of the named CueGrid-managed playlist.

The evaluator must read XML attributes defensively. Missing child elements,
missing attributes, empty strings, and malformed numeric/date values must not
raise an exception or abort evaluation of later entries. A missing or invalid
numeric value is treated as `0`; a missing or invalid text/key value as the
empty string; and a missing or invalid date as no date. Therefore, a
never-played track, for which Traktor commonly omits `INFO@PLAYCOUNT` and
`INFO@LAST_PLAYED`, has `playcount = 0` and does not match a positive
last-played date predicate. Invalid rule input is rejected before the NML tree
is mutated.

The supported field-to-NML mapping and operator set is fixed for this feature:

| Rule field | NML source | Supported operators | Value contract |
|---|---|---|---|
| `bpm` | `ENTRY > TEMPO@BPM` | `equals`, `greater_than`, `less_than`, `between` | finite number; `between` has inclusive `min` and `max` |
| `playcount` | `ENTRY > INFO@PLAYCOUNT` | `equals`, `greater_than`, `less_than`, `between` | non-negative integer; absent/invalid is `0` |
| `genre` | `ENTRY > INFO@GENRE` | `contains`, `is_exactly`, `does_not_contain` | string |
| `label` | `ENTRY > INFO@LABEL` | `contains`, `is_exactly`, `does_not_contain` | string |
| `comment` | `ENTRY > INFO@COMMENT` | `contains`, `is_exactly`, `does_not_contain` | string |
| `key` | `ENTRY > INFO@KEY` | `is_exactly`, `equals`, `is_harmonically_compatible`, `is_harmonically_compatible_fuzzy` | one or more comma-separated Open Key values, for example `9m, 2m` |
| `import_date` | `ENTRY > INFO@IMPORT_DATE` | `in_last_days`, `before`, `after` | `YYYY/M/D` or `YYYY/MM/DD` NML date; comparison is calendar-date based |
| `last_played` | `ENTRY > INFO@LAST_PLAYED` | `in_last_days`, `before`, `after` | same date format; missing means no date |
| `track_format` | `ENTRY > INFO@FLAGS` | `is_exactly` | the fixed value `Stem` |
| `rating` | `ENTRY > INFO@RANKING` | `greater_than_or_equal`, `less_than_or_equal`, `equals` | UI integer stars `1`–`5` |

String comparison is case-insensitive Unicode text comparison. `contains` and
`does_not_contain` operate on the normalized text value. Numeric comparison,
rating comparison, and both bounds of `between` are inclusive only where the
operator name explicitly states it; `greater_than` and `less_than` are strict.
`in_last_days` includes today and the preceding X calendar days, where X is a
positive integer. `before` and `after` are strict calendar-date comparisons.

Key matching is case-insensitive and ignores surrounding whitespace. A Key
rule may contain one or more comma-separated targets. Exact operators match
when the track's normalized key equals any normalized target. The harmonic
operators build one union set across every target before evaluating the track:
`is_harmonically_compatible` adds each target and its direct Open Key matches
(same key, one step clockwise or counter-clockwise, and relative major/minor);
`is_harmonically_compatible_fuzzy` additionally adds every match reachable by
one semitone adjustment. The track matches when its normalized key belongs to
that unified set. Invalid targets reject the rule and an absent or invalid
track key matches neither operator. For example, a rule value of `"9m, 2m"`
uses the union of the two targets' compatibility sets, not an intersection.

`track_format` supports only `is_exactly` with the case-insensitive value
`Stem`. It evaluates true when the `0x40` bit is present in `INFO@FLAGS`, the
same native-Stems availability signal exposed by the Library Browser. Missing,
malformed, or unset flags evaluate as not Stem.

#### BPM comparison rules

Traktor persists BPM with floating-point noise, so Smart Playlist BPM matching
MUST NEVER use strict floating-point equality. BPM comparisons use a `0.5` BPM
tolerance. For `equals`, a track matches when its stored BPM, half of its
stored BPM, or double its stored BPM is within `0.5` BPM of the requested
value. This supports equivalent half/double tempo representations used in DJ
libraries. The half/double alternatives apply only to `equals`.

For BPM `greater_than` and `less_than`, the stored value must be at least
`0.5` BPM above or below the requested bound, respectively. For BPM `between`,
the inclusive range is expanded by `0.5` BPM at each edge. These adjustments
ensure values such as `119.996376` are treated as `120` for rule evaluation.

For rating predicates, the engine must translate the UI star value to the
Traktor scale before comparison: `ranking = stars * 51`; hence one through
five stars map to `51`, `102`, `153`, `204`, and `255`. A missing or invalid
`RANKING` evaluates as `0`.

Rules are evaluated in request order against every collection entry. With
`match = "all"`, every rule must match; with `match = "any"`, at least one
rule must match. A Smart Playlist request must contain at least one rule.
If evaluation yields zero matching collection entries, the compiler MUST NOT
mutate the in-memory NML tree, create a backup, or write the NML file. The CLI
must return the JSON error payload
`{"ok": false, "error": "No tracks match these rules. Adjust your filters and try again."}`
with a non-zero exit status so the GUI retains the user's draft for correction.

The compiler must write a standard playlist under the `$ROOT` playlist folder:

```xml
<NODE TYPE="PLAYLIST" NAME="Warmup">
  <PLAYLIST ENTRIES="1" TYPE="LIST" UUID="0123456789abcdef0123456789abcdef">
    <ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Music/:Example.flac" /></ENTRY>
  </PLAYLIST>
</NODE>
```

`UUID` must be a newly generated, lowercase 32-character hexadecimal value
(`^[0-9a-f]{32}$`). `ENTRIES` must equal the number of emitted `ENTRY` nodes.
Each emitted `PRIMARYKEY@KEY` must be derived from the matching collection
entry's original Traktor `LOCATION` representation using the existing
Mac/Windows NML path formatter; it must not use the normalized filesystem path.
The operation uses the existing writer backup, temporary-file, atomic replace,
and in-memory rollback guarantees. A conflicting existing playlist name must
be handled deterministically by replacing only its generated playlist contents
rather than creating a duplicate playlist name.

### 5.2 Unified batch track persistence

`--batch-save` is the sole persistence API for GUI-originated track edits.
`collection.nml` remains the primary source of truth: the command combines
final cue, grid, BPM, and metadata state for every changed track into one
retained-parser-tree mutation, one backup decision, and one atomic replace.
The former per-track cue/delete and metadata mutation endpoints are retired and
MUST NOT be used by the GUI.

The supported editable field keys are:

| JSON field | Traktor field | NML location |
|---|---|---|
| `title` | Title | `ENTRY@TITLE` |
| `release` | Release (Album) | `ENTRY > ALBUM@TITLE` |
| `artist` | Artist | `ENTRY@ARTIST` |
| `remixer` | Remixer | `ENTRY > INFO@REMIXER` |
| `producer` | Producer | `ENTRY > INFO@PRODUCER` |
| `genre` | Genre | `ENTRY > INFO@GENRE` |
| `label` | Label | `ENTRY > INFO@LABEL` |
| `comment` | Comment | `ENTRY > INFO@COMMENT` |
| `comment2` | Comment 2 | `ENTRY > INFO@RATING` |
| `lyrics` | Lyrics | `ENTRY > INFO@KEY_LYRICS` |
| `mix` | Mix | `ENTRY > INFO@MIX` |
| `rating` | Rating | `ENTRY > INFO@RANKING` |

`ALBUM` and `INFO` elements must be created when an update requires them and they are absent. Existing unrelated attributes and child elements must be preserved.

Each track object's optional `metadata` object is a partial patch:

- An omitted field is left unchanged.
- A string value replaces the field value.
- `null` clears the field value by removing its NML attribute.
- `rating` is either `null` or an integer from `0` through `5`, inclusive.
- In NML, `rating` is stored as `RANKING = rating * 51`, producing the Traktor-compatible values `0`, `51`, `102`, `153`, `204`, and `255`.
- Empty strings are valid string values and must not be converted to `null`.

The complete payload is validated before any NML mutation. Every path must
resolve through the existing normalized location matching and duplicate-location
rules; an unresolved or ambiguous path, invalid cue/grid/BPM value, or invalid
metadata patch rejects the complete batch. The core MUST then leave the
in-memory parser tree and physical NML unmodified.

After validation, the core applies every track object's final state to the
retained parser tree and calls `NmlWriter` exactly once. A write failure
restores the in-memory tree and prevents all physical-file writes. A successful
NML write is all-or-nothing for the submitted track batch.

### 5.3 Playlist mutations in a unified batch

The optional `playlists` array in `--batch-save` persists final playlist state
alongside `tracks` in the same validation, retained-parser-tree mutation,
backup decision, and atomic replace transaction:

```json
{
  "tracks": [],
  "playlists": [
    {
      "uuid": "0123456789abcdef0123456789abcdef",
      "action": "update",
      "name": "Peak Time",
      "entries": ["C:\\Music\\One.flac", "C:\\Music\\Two.flac"]
    },
    { "uuid": "fedcba9876543210fedcba9876543210", "action": "delete" }
  ]
}
```

Every playlist mutation has a distinct non-empty `uuid`. `action` is exactly
`update` or `delete`. `delete` removes the matching playlist `NODE` entirely.
`update` requires a non-empty `name` and an `entries` array of distinct,
non-empty normalized collection paths; it updates the playlist node `NAME` and
completely replaces the nested `PLAYLIST > ENTRY` nodes in the exact submitted
order. `PLAYLIST@ENTRIES` must be updated to the submitted count while all
unrelated playlist attributes remain intact. Every path must resolve to one
unique collection entry before any mutation occurs; the entry's original
Traktor `LOCATION` representation becomes `PRIMARYKEY@KEY`.

An unknown or duplicate playlist UUID, invalid playlist mutation, or unresolved
playlist entry rejects the complete track-and-playlist transaction. The
`--get-library` playlist leaf representation includes the source playlist's
`uuid` so the GUI can identify mutations without relying on a mutable name.

### 5.4 Optional physical audio-file metadata persistence

`--write-to-files` enables a secondary physical-file metadata write after the
corresponding `--batch-save` NML mutation has atomically committed. This action
uses `mutagen` and applies only to batch entries that include `metadata`.

Physical-file writing is best-effort and never replaces, rolls back, delays, or invalidates the committed NML update. A locked file, permissions failure, unsupported format, malformed tag container, or any `mutagen`/filesystem exception must be caught, logged with the track path and error detail, and reported as an individual `physical_write_failed` track error. Processing continues with later tracks.

The supported file formats and mandatory tag behavior are:

| Format | Required behavior |
|---|---|
| MP3 | Use ID3 tags and save with `v2_version=4` (ID3v2.4). |
| FLAC | Use Vorbis comments. Every key written by CueGrid must be uppercase. |
| M4A/AAC | Use QuickTime atoms; use standard atoms where available. |
| AIFF | Use the file's native ID3 tag support and the same logical ID3 mapping as MP3. |
| WAV | Use native ID3 support. If no ID3v2 chunk exists, create one before writing metadata. Save as ID3v2.4. |

The logical physical-tag mapping is:

| Field | ID3 / AIFF / WAV | FLAC Vorbis key | M4A/AAC QuickTime atom |
|---|---|---|---|
| `title` | `TIT2` | `TITLE` | `©nam` |
| `release` | `TALB` | `ALBUM` | `©alb` |
| `artist` | `TPE1` | `ARTIST` | `©ART` |
| `remixer` | `TPE4` | `MIXARTIST` | NML only |
| `producer` | `IPLS` role `producer` | `PRODUCER` | NML only |
| `genre` | `TCON` | `GENRE` | `©gen` |
| `label` | `TPUB` | `ORGANIZATION` | `©pub` |
| `comment` | `COMM` | `COMMENT` | `©cmt` |
| `comment2` | NML only | `COMMENT2` | NML only |
| `lyrics` | `USLT` | `LYRICS` | `©lyr` |
| `mix` | NML only | `VERSION` | NML only |
| `rating` | `POPM` | `RATING` | `----:com.apple.iTunes:RATING` |

For ID3 `POPM`, CueGrid must use the owner identifier `traktor@native-instruments.de` and map ratings as `0 → 0`, `1 → 1`, `2 → 64`, `3 → 128`, `4 → 196`, and `5 → 255`. For FLAC and M4A/AAC, rating is stored as its decimal `0`–`5` value.

A `null` field value must remove the corresponding physical tag or atom when present. Unsupported physical-file extensions must produce an individual `unsupported_audio_format` error when `--write-to-files` is enabled.

### 5.5 Traktor Physical Metadata Quirks & Rules

The following rules were established by Traktor Pro A/B testing. They are mandatory for all physical-file metadata writes and take precedence over general mappings and Mutagen defaults in this section.

#### ID3 tags (MP3, WAV, and AIFF)

- CueGrid MUST save ID3 tags as ID3v2.4 (`v2_version=4`) to preserve UTF-8. This is required for Traktor to read the `TCON` Genre frame correctly.
- For `COMM`, the ID3 comment description MUST be strictly empty (`desc=""`); Traktor hides comments with a non-empty description.
- For `POPM`, the owner email MUST be spoofed as `traktor@native-instruments.de`; Traktor does not read ratings under another owner identifier.
- `comment2` and `mix` MUST NOT be written through Mutagen. Traktor protects them in its proprietary `PRIV:TRAKTOR4` binary block, so they are written exclusively to `collection.nml`.

#### Vorbis comments (FLAC)

- `label` maps to `ORGANIZATION`, `remixer` maps to `MIXARTIST`, and `mix` maps to `VERSION`.
- Before writing a Vorbis key, the writer MUST aggressively delete every pre-existing case variation of that key, including lowercase, uppercase, and mixed-case forms. This prevents duplicate tags from confusing Traktor.

#### MP4/M4A atoms

- Standard atoms work reliably for supported standard fields. `label` maps to `©pub` (Publisher).
- Traktor ignores iTunes freeform fields for `mix`, `comment2`, `producer`, and `remixer`. CueGrid MUST NOT write those fields physically and MUST write them exclusively to `collection.nml`.

#### Cues and beatgrids

CueGrid MUST NEVER write cue points or beatgrids physically to audio files, avoiding changes to Traktor's `TRAKTOR4` base64/binary blocks. CueGrid injects these values exclusively into `collection.nml`. Users who need them synchronized to physical files must use Traktor's native **Write File Tags** feature.

### 5.6 Remix Set persistence

`--save-remix-set JSON` is a standalone NML mutation. It parses one Remix Set
payload, resolves the collection NML, and invokes `NmlWriter.write_remix_set`.
It cannot be combined with track selection, other mutations or queries, or
audio-analysis options; `--nml`, `--json`, and `--verbose` remain valid.

The read-only Remix Set commands also bypass the analysis selector and audio
pipeline. `--list-remix-sets` prints the root `<SETS>` element's direct `<SET>`
titles in document order, or `[]` when `<SETS>` is absent. `--get-remix-set
TITLE` matches `SET@TITLE` exactly and returns the set title, tempo BPM,
quantize value/state, four-slot column settings, and pads. It accepts the
native `QUANT_VAlUE` spelling and falls back to `QUANT_VALUE`. Cell paths are
normalized using `nml_location_to_path` and cross-referenced against the main
collection for each resolved track's duration and musical key. `START_MARKER`
and `END_MARKER` are converted from seconds to milliseconds; an `END_MARKER`
of `0` is replaced with the matched collection duration when available,
because Traktor uses it to represent the full source file. A missing title returns
`{"error":"not_found","message":"..."}` with a nonzero exit code;
duplicate titles are ambiguous.

`NmlWriter.write_remix_set(payload)` checks each active pad's normalized source
path against the current collection before changing the NML. A pad whose source
path already has a collection entry is referenced in place: its source file is
not copied and its existing collection entry is left untouched, preserving
Traktor-managed stripe and transient data. A pad whose source path is absent
from the collection is copied to
`~/Music/Traktor/Samples/Arka/<sanitized set title>/`; its destination filename
is deterministic: `<pad id>_<original filename>`.
The title-derived directory name replaces filesystem-reserved characters,
slashes, and control characters, and is never empty. A Windows `\\?\` path
prefix is stripped before source paths are resolved or serialized.

The writer locates or creates a root-level `<SETS>` element and writes one
native `<SET>` child. A same-title `<SET>` is replaced at its existing document
index; a new title is appended. `SETS@ENTRIES` always equals the total number
of direct `<SET>` children. The set has `TITLE`, `QUANT_VAlUE`, and
`QUANT_STATE` attributes. Its first children, in order, are a virtual `LOCATION`,
`MODIFICATION_INFO AUTHOR_TYPE="importer"`, `INFO` with an un-padded
`IMPORT_DATE`, and `TEMPO`. Its timestamped `LOCATION@FILE` follows
`YYYYyMMmDDd_HHhMMmSSs000000.set`, and its `DIR` and
`VOLUME` are derived from the collection's directory. The `.set` file is
never created or touched on disk. The set also has a six-decimal `TEMPO@BPM`.
The virtual `.set` is never registered in `<COLLECTION>`; Traktor reads it
from `<SETS>`. The writer appends one root `<COLLECTION><ENTRY>` for every
active-pad sample whose destination is not already in the collection. Existing
matching entries are never modified or duplicated. Each new entry has
`MODIFIED_DATE`, `MODIFIED_TIME`, `LOCK="1"`,
and an ISO-8601 `LOCK_MODIFICATION_TIME`, plus a
`MODIFICATION_INFO AUTHOR_TYPE="importer"` child, and an `INFO` child with its
`IMPORT_DATE`, `FLAGS="28"`, the copied file's physical `FILESIZE`, and—when
the submitted pad range is positive—integer and six-decimal duration fields.
`INFO@COMMENT` is `Arka: <set title>` for browser filtering. A valid submitted
Open Key is serialized as `MUSICAL_KEY@VALUE`; invalid or absent keys are
omitted. Its `TEMPO` includes `BPM_QUALITY="100.000000"`, followed by an AutoGrid
`CUE_V2 TYPE="4"` at `0.000000` containing `GRID@BPM`. It updates
`COLLECTION@ENTRIES` to the total number of direct `ENTRY` children. Every new
`LOCATION` inherits the first non-empty `COLLECTION//LOCATION@VOLUMEID`, when
available.
It has exactly four `SLOT` elements (A through D), with `KEYLOCK`,
`FXENABLE="1"`, `PUNCHMODE`, and `ACTIVE_CELL_INDEX`: slots containing one or
more cells use `0`, while empty slots use `-1`. Each active pad produces a
`CELL` under its slot whose `INDEX` is its zero-based row index and whose audio
location is written directly as `VOLUME`, `DIR`, and `FILE` attributes on the
cell.
`DIR` uses Traktor `/:` separators; Windows uses the drive volume; macOS uses
the mounted volume for `/Volumes/<volume>/...` and `Macintosh HD` otherwise.

The daily NML backup is created immediately before XML mutation, then the
complete document is atomically replaced through the existing writer path. An
exception restores the retained parser tree to its original in-memory state.

## 6. Pipeline behavior

`run_pipeline` performs one path-selected analysis:

```text
parse NML -> resolve entry -> Flex Grid guard -> generate/detect events
-> map free cues -> mutate and atomically write when cues exist
```

`run_batch_pipeline` requires exactly one of `playlist`, `track_title`, or an explicit `track_paths` list. Playlist and title selection resolve references once; explicit paths are resolved individually with `NmlParser.find_entry`. All resolved entries are processed sequentially. An explicit-path resolution failure is logged and does not abort the remaining paths. Each eligible successful entry may be written immediately; failures and invalid BPM/Flex Grid entries become an individual `BatchTrackResult` and do not abort the remaining batch. An optional callback is invoked after each result, enabling live NDJSON lifecycle output.

Both pipelines reset the telemetry file at the start of a run.

### 6.1 Unified batch-save pipeline behavior

The batch-save pipeline does not run audio decoding, beat-grid processing,
cue mapping, or telemetry export:

```text
parse NML -> validate complete payload -> resolve every path
-> apply cue/grid/BPM/metadata mutations to retained tree
-> atomically write NML once -> optionally write physical metadata files
-> emit per-track results and one batch summary
```

For every track committed to NML, the NML change is successful even when its
optional physical-file write fails. Such a track result must report both
outcomes:

```json
{
  "path": "C:\\Music\\Example.flac",
  "nml_updated": true,
  "physical_file_updated": false,
  "error": {
    "code": "physical_write_failed",
    "message": "..."
  }
}
```

The NML transaction is atomic: a validation or NML-write error returns a
nonzero exit status and reports no committed tracks. After a successful NML
transaction, individual physical-file errors are reported without rolling back
the NML write; the command returns a nonzero exit status when any occurs.

## 7. CLI contract

### 7.1 Analysis selectors

Exactly one selector is required for an analysis run:

```text
cuegrid [TRACK_PATHS...]
cuegrid --track-title TITLE [--artist ARTIST]
cuegrid --playlist NAME
```

One `TRACK_PATH` uses the single-track pipeline; multiple positional `TRACK_PATHS` are resolved and processed sequentially as a batch. `--title` is valid only with one `TRACK_PATH`. `--artist` narrows a single track or title batch and is invalid with `--playlist`. `--nml PATH` overrides discovery; otherwise the CLI chooses the most recently modified `collection.nml` in standard Native Instruments document locations.

`--clear-existing`, all tuning flags, `--export-csv`, `--json`, and `-v/--verbose` apply to analysis. `--export-gui` is a single-track-only output mode and cannot be combined with `--json`.

### 7.2 Normalization function

`nml_location_to_path(volume, dir_, file_)` splits Traktor's `DIR` value on
`/:`, discarding empty segments, then constructs a comparable path before
case-folding it and replacing any backslashes with forward slashes:

- a Windows drive-letter `VOLUME` produces `PureWindowsPath(volume + "\\",
  *segments, file_)`;
- the macOS boot-drive volume exactly named `Macintosh HD` produces
  `PurePosixPath("/", *segments, file_)`, because it is mounted at the root
  filesystem rather than below `/Volumes`;
- every other macOS volume produces `PurePosixPath("/Volumes", volume,
  *segments, file_)`.

For example, `VOLUME="Macintosh HD"`, `DIR="/:Users/:dj/:Music/:"`, and
`FILE="track.mp3"` normalize to `/users/dj/music/track.mp3`.

### 7.3 One-shot read-only commands

These commands bypass audio analysis and the normal selector requirement:

| Command | Success output |
|---|---|
| `--discover-nml` | `{ "path": "..." }`, or an error object |
| `--list-playlists` | JSON array of non-system playlist names in document order |
| `--get-playlist-tracks NAME` | JSON array of `{artist,title,location_path,flags,is_flex_grid}` |
| `--get-track-metadata TRACK_PATH` | one Super JSON metadata/preview object |
| `--get-library` | relational `{collection, playlists}` JSON object |

`--get-library` indexes collection entries by normalized location path. Each collection value includes `location_path`, `audio_id`, `bpm`, `grid_anchor_ms`, `key`, `duration_ms`, `is_flex_grid`, `existing_cues`, and `collection_index`, plus the complete metadata dictionary below. `audio_id` is the nullable `ENTRY@AUDIO_ID` value and enables the GUI's read-only native Stem lookup; it is not an analysis input. Playlist/folder nodes retain hierarchy using `kind`, `name`, `children` (folders), or `track_paths` (playlists); they do not duplicate track metadata. Duplicate normalized collection locations return `{"error":"duplicate_location",...}` with a nonzero exit code.

Every collection entry in a successful `--get-library` response must include every field in this metadata dictionary. String fields are always present and use `""` when the corresponding NML attribute or element is absent. `rating` is always present as a number in the inclusive range `0` through `5`; a missing or invalid NML `INFO@RANKING` value is reported as `0`.

| JSON field | JSON type | Traktor NML source |
|---|---|---|
| `title` | string | `ENTRY@TITLE` |
| `artist` | string | `ENTRY@ARTIST` |
| `album` | string | `ENTRY > ALBUM@TITLE` |
| `remixer` | string | `ENTRY > INFO@REMIXER` |
| `producer` | string | `ENTRY > INFO@PRODUCER` |
| `genre` | string | `ENTRY > INFO@GENRE` |
| `label` | string | `ENTRY > INFO@LABEL` |
| `comment` | string | `ENTRY > INFO@COMMENT` |
| `comment2` | string | `ENTRY > INFO@RATING` |
| `lyrics` | string | `ENTRY > INFO@KEY_LYRICS` |
| `mix` | string | `ENTRY > INFO@MIX` |
| `audio_id` | string \| null | `ENTRY@AUDIO_ID`; `null` when absent or empty |
| `flags` | number | `ENTRY > INFO@FLAGS`, parsed as a non-negative integer; missing or malformed values are `0` |
| `rating` | number | `ENTRY > INFO@RANKING`, converted from Traktor's `0`–`255` representation to the nearest integer `0`–`5` rating |

The payload is a read-only representation of the active `collection.nml` state at query time. It must not infer values from physical audio-file tags, cached UI state, or an audio decode.

The Super JSON returned by `--get-track-metadata` contains `artist`, `title`, `bpm`, `grid_anchor_ms`, `is_flex_grid`, non-grid `existing_cues`, `waveform_peaks`, and `color_map`. Preview generation is independent of detection: it makes a complete mono decode at 11,025 Hz, uses 64-sample waveform extrema converted to signed `int8` values, and produces normalized low/mid/high colour buckets every 500 ms.

### 7.4 Unified batch-save mutation

```text
cuegrid --batch-save JSON [--write-to-files] [--nml PATH] [--json] [-v|--verbose]
```

`--batch-save` accepts exactly one object with optional `tracks` and
`playlists` arrays; at least one array must be non-empty. Each track entry has
a distinct non-empty `path` and supplies final state for one or more edited
track domains. Playlist mutation entries follow section 5.3 and have distinct
UUIDs:

```json
{
  "tracks": [
    {
      "path": "C:\\Music\\Example One.flac",
      "cues": [
        { "hotcue": 0, "start_ms": 32000.0 },
        { "hotcue": 3, "start_ms": 96000.0 }
      ],
      "grid_anchor_ms": 0.0,
      "bpm": 128.0,
      "metadata": {
        "genre": "Techno",
        "label": "Example Records",
        "rating": 4
      }
    },
    {
      "path": "C:\\Music\\Example Two.mp3",
      "metadata": { "comment2": "Peak-time" }
    }
  ],
  "playlists": [
    {
      "uuid": "0123456789abcdef0123456789abcdef",
      "action": "update",
      "name": "Peak Time",
      "entries": ["C:\\Music\\Example One.flac"]
    }
  ]
}
```

Every track entry MUST include at least one of `cues`, `grid_anchor_ms`,
`bpm`, or `metadata`. `cues` is the complete desired standard-HotCue set, not
an incremental patch: it is an array of unique numeric `hotcue` indexes from 0
through 7 with finite, non-negative `start_ms` values. The core updates or
creates listed HotCues and removes standard HotCues not listed; it never
changes Grid (`TYPE="4"`) or Load (`TYPE="3"`) markers. `grid_anchor_ms` is
finite and non-negative and requires exactly one Grid marker. `bpm` is finite
and inclusively between 50 and 200. `metadata` uses the supported partial-field
mapping in section 5.2 and must contain at least one supported key when
present.

`--write-to-files` is a boolean presence flag, defaults to `false`, and is
valid only with `--batch-save`. The endpoint is mutually exclusive with
positional track paths, analysis selectors, Smart Playlist mutation, analysis
tuning flags, `--export-csv`, and `--export-gui`; it may use `--nml`, `--json`,
and `--verbose`. The former `--update-cues`, `--delete-cue`, and
`--update-metadata` endpoints are rejected, not translated.

With `--json`, `--batch-save` emits compact, line-flushed NDJSON in this order:

```text
nml_resolved
batch_save_validated
batch_save_nml_committed
batch_save_track_complete*
batch_save_physical_status* (only for metadata entries when --write-to-files is enabled)
batch_save_summary
```

`batch_save_validated` includes the requested track and playlist mutation
counts. Playlist updates and deletes are committed by the same
`batch_save_nml_committed` event as tracks.
`batch_save_nml_committed` is emitted only after the one atomic NML write.
Each `batch_save_track_complete` includes `path` and `nml_updated: true`.
`batch_save_physical_status` includes `path`, `success`, and an `error` string
or `null`; physical-file errors preserve their caught exception details.
`batch_save_summary` includes `requested`, `nml_updated`,
`physical_file_updated`, and `errors`.

### 7.5 Smart Playlist mutation

```text
cuegrid --compile-smart-playlist JSON [--nml PATH] [--json] [-v|--verbose]
```

`--compile-smart-playlist` accepts exactly one JSON object:

```json
{
  "name": "Recent 8A",
  "match": "all",
  "rules": [
    { "field": "key", "operator": "is_exactly", "value": "8A" },
    { "field": "import_date", "operator": "in_last_days", "value": 30 }
  ]
}
```

`name` is a trimmed, non-empty playlist name. `match` is either `all` or
`any`. `rules` is a non-empty array. Every rule must use one of the exact
field/operator combinations in section 5.1 and must satisfy that operator's
value contract. `between` uses `{ "min": number, "max": number }` with
`min <= max`. This mutation is mutually exclusive with analysis selectors,
positional track paths, cue mutations, metadata mutation, and analysis tuning
flags; it may use `--nml`, `--json`, and `--verbose`.

With `--json`, successful compilation emits one line-flushed object:

```json
{ "type": "smart_playlist_compiled", "name": "Recent 8A", "matched": 12, "uuid": "0123456789abcdef0123456789abcdef" }
```

Validation or write failure emits the existing `fatal_error` contract and
makes no persistent NML change.

### 7.6 Static playlist mutation

```text
cuegrid --create-static-playlist JSON [--nml PATH] [--json] [-v|--verbose]
```

`--create-static-playlist` accepts exactly one object with a trimmed non-empty
`name` and an ordered `entries` array of normalized collection paths. `entries`
may be empty when creating a standard empty playlist. Entries may repeat and
their submitted order must be preserved. Every submitted path must resolve
uniquely in the current collection before mutation. The command creates
a new regular top-level playlist using the same node structure, UUID generation,
primary-key serialization, writer backup, atomic replace, and rollback behavior
as `--compile-smart-playlist`; it does not create a Smart Playlist rule set.

This mutation is mutually exclusive with analysis selectors, positional paths,
batch-save, Smart Playlist compilation, and analysis-tuning flags. It may use
`--nml`, `--json`, and `--verbose`. With `--json`, success emits exactly:

```json
{ "type": "static_playlist_created", "name": "Session - Jul 17, 2026, 1:50 AM", "entries": 3, "uuid": "0123456789abcdef0123456789abcdef" }
```

Validation or write failure emits the existing `fatal_error` contract and
makes no persistent NML change.

### 7.7 NDJSON analysis output

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

### 7.8 Current compatibility flags

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
