# Remix Deck Architecture

## Phase: Pad data and presentation foundation

Each Remix Deck pad is represented by `RemixPadData`, which deliberately
separates playback behavior from source-audio physics.

- `PadSettings` contains the mutable performance behavior: identity, label,
  color, play type, trigger mode, sync, reverse, key lock, volume, and filter.
  Changing these values must not modify audio boundaries, tempo facts, or the
  source file.
- `PadAudioData` contains source-audio and timing facts: file path, duration,
  original BPM, grid anchor, start/end offsets, and pitch shift. A pad without
  audio stores `null` for this field.

`RemixPad.vue` is a presentational component only. It owns no Tone.js objects,
audio transport, persistence, or Remix Deck state. It receives a pad and
optional playback indicator as props, then relays `press`, `release`, and
`context-menu` events to a higher-level Audio Engine. It also emits immutable `update:settings`
copies for its inline play-type, trigger-mode, and sync controls; the parent is
responsible for accepting and persisting those changes.

## Deck and column composition

`useWorkspaceStore` owns the shared 64-pad matrix so both the deck and Pad
Edit Mode operate on the same pad data. `RemixDeck.vue` is the UI-state
orchestrator for the visible 4×4 matrix, owns the four per-column volume
values, and projects the audio engine's `activePads` state onto
the visible page. The engine holds one active pad ID or `null` per column,
which enforces the core rule: **only one active pad per column is allowed**.

Above the columns, the deck renders a Master header with an editable Master BPM
control. It defaults to **120.00 BPM**, accepts values from 40 through 220 in
0.01 BPM increments, and provides coarse (1.0 BPM) and Shift-click fine (0.1
BPM) adjustments. Changing the control calls `setGlobalBpm`, making the Master
BPM the single tempo reference for the Remix Deck.

`RemixColumn.vue` represents one independent vertical slot. It renders its
volume fader with an amber filled-track visual, then forwards each nested
`RemixPad.vue` press, release, and edit interaction with the pad index added.
The deck receives those events and applies the state change; an empty pad never
becomes active.

Tone.js players, routing, and playback state are encapsulated in
`useRemixAudio.ts`; the Remix Deck components do not own Tone nodes directly.

## Fixed hardware-style pad geometry

The Remix Deck is a non-scrolling, bounded flex surface. Each `RemixColumn`
uses a fixed-height control strip and a remaining-space pad stack with
`grid-template-rows: repeat(4, minmax(0, 1fr))`. This strict fractional sizing
keeps all 16 pads visible at once, with no layout changes when a pad becomes
active, matching the predictable geometry of a hardware controller.

## 64-pad pagination

The Remix Deck owns 64 pads: four columns with 16 pads each (`A1`–`A16`,
`B1`–`B16`, `C1`–`C16`, and `D1`–`D16`). The fixed 4×4 display renders one
four-pad slice from every column at a time. A left-side four-button page
selector changes `currentPage` between pages 0 through 3 without introducing
scrollbars.

The audio engine's `activePads` state stores one pad ID or `null` per column.
`RemixDeck` derives the corresponding absolute index, and `RemixColumn` uses
the active page offset to map visual state and pad events across every page.
This preserves the one-active-pad-per-column rule across the full 64-pad deck.

## Loop import context menu

Right-clicking any Remix Pad opens a floating context menu at the pointer
location. `RemixPad.vue` emits the native `context-menu` event; `RemixColumn.vue`
forwards it with the column index, absolute pad index, and pad identifier; and
`RemixDeck.vue` owns the menu state and closes it on the next window click.

The menu's **Import Loop** action is enabled only when the workspace has both
an `activeLoopRange` and an active Studio track. When selected, the deck derives
the audible source paths before invoking Tauri's `extract_pad_audio` command.
For a standard track it sends its `location_path` as a one-item array. For a
four-lane Stem track, it sends only the Stem WAV paths that are audible: a solo
selects only its lane; otherwise every unmuted lane is selected. If no path is
active, the deck shows **No audio active to extract** and does not invoke the
backend.

The backend seeks every input with `-ss <start_sec> -i <source_path>` before
applying `-t <duration>`. One input is encoded without mixing;
multiple inputs use `-filter_complex amix=inputs=<count>:duration=longest` to
downmix the active lanes before encoding the 44.1 kHz PCM WAV. On success, the
targeted pad receives the returned WAV path and duration. Imported pads use
their pad ID as the default name and receive a random color from the Traktor
palette exposed by the color picker.
The context menu also provides **Rename Pad**. Rename Pad
sets `RemixDeck`'s `editingPadId` to the selected pad ID, which travels through
`RemixColumn` to `RemixPad`; the pad replaces its label with a focused inline
text input. Enter or blur commits the non-empty name, while Escape cancels it;
either path emits `end-rename` so the deck clears `editingPadId`. An inline
palette row at the bottom of the context menu is separated from the actions
above it. Its `#FF004D`, `#FF7700`, `#FFEA00`, `#00FF00`, `#00D4FF`, `#B300FF`,
and `#FFFFFF` swatches update the pad color through the normal settings-update
flow and then close the menu.
The command result is mapped as follows:

| Rust result | Pad audio field |
| --- | --- |
| `file_path` | `filePath` |
| `duration_ms` | `durationMs` and `endMs` |

## Tone.js playback and settings binding

`useRemixAudio.ts` owns the Remix Deck audio engine. It creates four independent
channels, one per column, with this routing chain:

```text
Tone.GrainPlayer → Tone.Volume → Tone.Destination
```

The former low-pass Column Filter stage has been removed; each column routes
directly from `Tone.GrainPlayer` through its `Tone.Volume` control to the
destination. `Tone.GrainPlayer` provides keylock behavior: tempo changes alter
the playback rate without shifting the sample's pitch.

The granular engine uses a `grainSize` of `0.05` seconds and an `overlap` of
`0.05` seconds. These tighter 50 ms grains and crossfades preserve rhythmic
transients during extreme down-tempo stretching and avoid doubled kick-drum
artifacts.

Each loaded pad ID has one `Tone.GrainPlayer`. Loading replacement audio disposes
the previous player for that pad, configures its looping state from `playType`,
and connects it to the target column channel. `useRemixAudio.ts` owns a reactive
global BPM initialized to 120 and initializes `Tone.Transport` with that value.
`setGlobalBpm(newBpm)` updates the transport, then refreshes every loaded,
sync-enabled pad whose `originalBpm` is positive using
`newBpm / originalBpm`; unsynced pads retain a playback rate of `1`.

When a pad is pressed while the Remix Deck is idle (no active pads in any
column), the engine resets `Tone.Transport.position` to `0` and starts that
first pad immediately, regardless of its sync setting. This establishes a new
global downbeat without an initial quantization delay. While any pad is active,
synced loop pads are scheduled at the next measure (`@1m`) and synced one-shots
at the next quarter note (`@4n`); unsynced pads continue to start immediately.
The incoming pad and any active pad in the same column use the same transition
time. Trigger pads toggle on repeated presses, while Gate pads stop 50 ms after
release to prevent clicks.

## Audio mutex

The Stem Editor and Remix Deck use a shared audio mutex, `activeAudioEngine`,
whose value is `"stems"`, `"remix"`, or `null`. Starting a Remix pad claims the
`"remix"` engine, which pauses and silences Stem playback. Starting Stem Editor
playback claims the `"stems"` engine, which stops every active Remix pad and
pauses the shared Tone transport. This guarantees that only one engine produces
audible output at a time. Remix Pad mouse, touch, and context-menu events stop
propagation so a pad press cannot reach unrelated player controls or global
listeners.

`Tone.Transport` is a shared singleton, so the Stem Editor treats it as an
untrusted clock unless `activeAudioEngine === "stems"`. On any other engine,
the Stem Editor explicitly pauses its Peaks player, unsyncs and stops every
Stem `Tone.Player`, and exits transport-driven time-update loops before they
can move the playhead. When the user presses Stem Play, the editor first claims
the `"stems"` mutex, re-syncs/restarts the stopped Stem players at the current
transport time, and only then starts the transport.

Column volume values use a linear 0–1 control mapped through `Tone.gainToDb`.
The unimplemented Column Filter control is not rendered. Each loaded pad
instead exposes a click-toggled gain popover with a normalized range of -1 to 1.
Its value is displayed with a signed two-decimal label and is mapped to gain as
`value * 12 dB` for non-negative values and `value * 24 dB` for negative
values; neutral `0` is 0 dB, `1` is +12 dB, and `-1` is -24 dB.

`RemixPad.vue` emits immutable `update:settings` payloads. `RemixColumn.vue`
forwards each payload with its column index and absolute pad index, and
`RemixDeck.vue` replaces that pad's settings in the 64-pad matrix. The deck then
immediately calls `updatePlayerLoop` so a loaded Tone player reflects changes to
the Loop/One-Shot control without reloading its source audio.

Gain-setting updates call `updatePlayerVolume` immediately, so they apply to a
loaded player without reloading its source audio.

## External files and Pad Edit Mode

The pad context menu offers **Load File...** for WAV, MP3, AIFF, FLAC, M4A, and
Ogg sources. It initializes the source path with a 120 BPM default, a zero grid
anchor, no transpose, and a full-source loop (`loopStart` 0 and `loopEnd`
`null`). The same menu offers **Edit Audio** for loaded pads.

`useWorkspaceStore` owns `editorMode` (`library` or `pad`) and `editingPadId`.
In Pad Edit Mode, `StemEditor.vue` loads only the selected pad's source into a
single waveform; Stem lanes, Hotcues, and library loop feedback are not
rendered. Entering the mode copies the pad's BPM, grid anchor, and transpose to
local edit state and initializes `activeLoopRange` from its saved crop (or the
full source). The existing `custom-selection-overlay` remains visible for
trimming: its move and resize handles use `snapToGrid`, and its `{ start, end }`
selection becomes the saved crop boundary. Cancel clears the edit mode and its
temporary selection; Save updates the shared pad, reloads its GrainPlayer, and
then exits the mode.

While Pad Edit Mode is active, Mini Library is strictly locked to prevent its
track state from mixing with the pad source. Its interaction surface is dimmed,
grayscaled, and pointer-disabled; `selectTrack()` independently ignores any
keyboard or programmatic selection attempt until the editor returns to Library
Mode.

Pad trim feedback uses a grayscale mask rather than a highlighted selection
box. The retained selection stays in the original waveform color. Two
non-interactive CSS mask layers cover the discarded left and right regions,
using the same viewport-relative percentages as the selection overlay with a
dim dark surface and `backdrop-filter: grayscale(100%)`. In this mode the
selection overlay itself is transparent so only its resize handles remain.

The single-waveform container is observed with `ResizeObserver`. Every resize
calls `zoomview.fitToContainer()` and refreshes the selection geometry, keeping
Peaks.js and the trim masks aligned after panel or window layout changes. The
Pad Edit transport wraps its controls. Save and Cancel are deliberately outside
that transport: they sit in a non-shrinking action group at the top-right of
the editor header, immediately above the BPM/key details, so a narrow split
workspace cannot clip them.

Pad Edit Mode has mutually exclusive local sub-modes through
`isPadGridEditMode`, defaulting to **Trim Mode** for every opened pad. Trim
Mode shows only the crop handles; its selection center passes clicks through to
Peaks.js for normal playhead seeking, and the grid anchor is locked. **Grid
Edit Mode** hides the trim overlay, renders the editable grid anchor, and shows
the **Edit Grid** controls. Its `PlayerGridControls.vue` nudge by one
millisecond and omits the BPM /2 and x2 modifiers. The anchor updates local
grid state only while Grid Edit Mode is active.

The Trim Mode overlay enforces `pointer-events: none` and a default cursor at
the overlay level, while its resize handles explicitly restore pointer events
and the horizontal-resize cursor. This prevents the transparent selection body
from blocking native Peaks.js playhead seeking. Single-waveform zoom supports
fine Pad Edit precision down to `MIN_VISIBLE_SECONDS = 0.1`; its expanded
Peaks zoom levels include 8, 16, and 32 samples-per-pixel before the existing
coarser levels. Wheel and toolbar zoom always clamp the visible range between
this 100 ms minimum and the full source duration.

The Grid Edit toolbar provides an exact BPM number input (20–300, 0.01 BPM
steps); transpose in whole semitones from -12 through +12 remains available in
both sub-modes. Both inputs update the local state read by `gridTrack`, so
changes redraw the Peaks grid immediately without changing preview playback
rate.

Both trim handles are pointer-accessible in Pad Edit Mode. The end handle has
an enlarged visible hit target and writes `activeLoopRange.end` directly while
dragging, clamped to the waveform duration and at least one snapped beat after
the current start point. This preserves the one-beat minimum without blocking
crop-end edits.

For Pad Edit preview, the single-track path uses `Tone.GrainPlayer` with the
same 50 ms grain size and overlap as Remix Deck playback. Its `detune` is set
from transpose in cents (semitones × 100) and updates immediately when the
transpose input changes, avoiding the latency of a separate PitchShift node.
The GrainPlayer is disposed when the editor reloads or unmounts.

Loaded Remix Pads with a non-zero transpose show a compact top-left pitch badge
(`+2 st`, `-1 st`). The badge uses the saved setting when available and falls
back to the source-audio pitch metadata for legacy pad data.

Saving writes the selected range, BPM, grid anchor, and transpose back to the
shared pad state, then reloads its Tone player. `PadSettings` carries optional
`transpose`, `loopStart`, and `loopEnd`; `useRemixAudio.ts` applies them to
`Tone.GrainPlayer.detune` (semitones × 100 cents), `loopStart`, and `loopEnd`.

## Pad deletion and audio cleanup

The right-click menu renders **Clear Pad** only for a pad with loaded audio.
Selecting it calls `removePadAudio(padId, colIndex)` before clearing the pad's
audio reference, name, and color. `removePadAudio` clears the active-pad ID for
that column, stops the matching `Tone.Player`, disposes it, and removes it from
the player map. This order keeps the UI and Tone.js memory state synchronized.
