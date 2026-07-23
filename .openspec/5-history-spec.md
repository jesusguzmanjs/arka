# Session History View Specification

Status: Proposed architecture. Sections 9–14 define the approved design for
timeline gap compression, public-playback metadata, and importing a history
session as a persistent collection playlist.

## 1. Purpose and scope

This specification defines the architecture for CueGrid's **Session History**
tab. The tab presents a saved Traktor session-history NML file as a four-deck
timeline and can stage one selected session as a regular collection playlist.

History discovery and parsing remain local, read-only Vue/Tauri work. Timeline
rendering never modifies a history file. The explicit **Import as Playlist**
operation is the sole exception: it stages a new regular playlist in the
in-memory collection and persists it through the existing unified Core batch
save boundary. It never modifies the selected history NML.

`SessionHistoryView.vue` is the view selected by the existing `"history"` tab state described in [3-gui-spec.md](3-gui-spec.md). This document supersedes that file's empty-state-only limitation for this view.

## 2. Definitions

| Term | Definition |
|---|---|
| History file | A Traktor NML file named `history_YYYYyMMmDDd_HHhMMmSSs.nml` in the `History` directory next to `collection.nml`. |
| Collection entry | An `NML > COLLECTION > ENTRY` element embedded in the selected history file. It supplies track metadata. |
| History event | A `PLAYLIST` entry within `PLAYLISTS` whose containing playlist has `NAME="HISTORY"`. It represents one playback occurrence. |
| Primary key | The `PRIMARYKEY` element under a history event. Its `KEY` is the serialized Traktor location used to join an event to a collection entry. |
| Session origin | The smallest valid event `STARTTIME` in the selected history file. It maps to timeline time zero. |
| Deck lane | One of exactly four horizontal timeline rows. Traktor deck IDs map as `0 -> A`, `1 -> B`, `2 -> C`, and `3 -> D`. |

All event times and durations in this specification are seconds unless a field explicitly states another unit.

## 3. Requirements and constraints

### 3.1 File discovery and read boundary

- **HIS-REQ-001:** The frontend shall derive the history directory from the currently resolved `collection.nml` path: `dirname(collectionNmlPath) / "History"`. It shall not hard-code a user profile, Traktor version, or platform-specific Documents path.
- **HIS-REQ-002:** The frontend shall use `@tauri-apps/plugin-fs` for directory listing and UTF-8 text reads. Adding this dependency and its least-privilege Tauri filesystem capability is an implementation prerequisite.
- **HIS-REQ-003:** Discovery shall include only regular files whose basename matches the case-insensitive pattern `^history_(\d{4})y(\d{2})m(\d{2})d_(\d{2})h(\d{2})m(\d{2})s\.nml$`.
- **HIS-REQ-004:** The sidebar shall display discovered history files newest first by their timestamp encoded in the filename. File-system modified time is not the ordering source.
- **HIS-REQ-050:** The sidebar header shall place the Sessions label and a compact native HTML5 `input[type="date"]` control on the same row. No external calendar dependency is permitted. A visible clear control appears only while a date is selected and resets the filter to `null`.
- **HIS-REQ-051:** `useSessionHistory()` shall expose `filterDate: Ref<string | null>` and `filteredFiles: ComputedRef<HistoryFileDescriptor[]>`. `filterDate` is either `null`/empty or a native date value formatted as `YYYY-MM-DD`. `filteredFiles` returns all discovered files when no date is selected; otherwise it compares each descriptor's local `startedAt` year, month, and day with the selected value. It must not use UTC serialization or timezone-dependent string conversion.
- **HIS-REQ-052:** The sidebar list shall render `filteredFiles`. When discovery found one or more files but the selected date yields none, it shall render the distinct empty state `No sessions found for this date.` without replacing the selected timeline.
- **HIS-REQ-053:** `useSessionHistory()` shall keep its selected file descriptor, active timeline model, and `filterDate` in module-scoped singleton state. This state persists in RAM while the application is running, including when the Session History view unmounts and remounts during Collection/Session History tab navigation. Re-discovering history files shall clear the selected file and timeline only when the active `collection.nml` path changes to a different path.
- **HIS-CON-001:** Discovery, parsing, selection, zooming, gap compression,
  and public/cue styling are read-only. They shall not edit the history file.
  Only the confirmed Import as Playlist workflow may mutate `collection.nml`,
  through `useSaveStore.saveAll()` and the Core batch-save contract in section
  12 / `2-core-spec.md` section 5.3; it must never write a history file.
- **HIS-CON-002:** Missing/unreadable History directories, malformed files, and unmatched entries are recoverable UI states. They must not crash the view or prevent a user from selecting another file.

### 3.2 Filename date contract

`history_2025y11m11d_02h14m00s.nml` represents local wall-clock time 2025-11-11 02:14:00. The parser shall build that local date with:

```ts
new Date(year, month - 1, day, hour, minute, second)
```

It shall reject invalid calendar values by verifying each reconstructed `Date` component matches the captured value. A valid timestamp is formatted for the sidebar with `Intl.DateTimeFormat("en-US", ...)` using a date-and-time style appropriate to the available space. The raw filename remains available as an accessible label/tooltip but shall not be rendered below the date. The filename timestamp labels the saved session file; it does not replace event timing.

### 3.3 NML parsing and data join

- **HIS-REQ-005:** The frontend shall read a selected history file as text and parse it with the browser-native `DOMParser` using `application/xml`.
- **HIS-REQ-006:** The parser shall check for a `parsererror` element before extracting data. A parse failure yields a file-specific error state and no timeline model.
- **HIS-REQ-007:** The parser shall build a collection index before processing history events. For each `COLLECTION > ENTRY`, it shall capture `TITLE`, `ARTIST` (empty string when absent), the first direct child `LOCATION` attributes `VOLUME`, `DIR`, and `FILE`, the direct child `TEMPO.BPM`, and the direct child `MUSICAL_KEY.VALUE`.
- **HIS-REQ-028:** A finite `TEMPO.BPM` value shall be rounded to two decimal places and exposed as `bpm`; absent or invalid values are represented as `null`. An integer `MUSICAL_KEY.VALUE` from 0 through 23 shall be mapped to Open Key notation (`1m`–`12m` or `1d`–`12d`) and exposed as `key`; absent or invalid values are represented as an empty string. The mapping shall include `0 -> 8d`, `1 -> 8m`, `2 -> 3d`, and `3 -> 3m`.
- **HIS-REQ-008:** The collection-index key shall be exactly the Traktor serialized location `VOLUME + DIR + FILE`. Given `VOLUME="C:"`, `DIR="/:Users/:dj/:Music/:"`, and `FILE="song.flac"`, the key is `C:/:Users/:dj/:Music/:song.flac`. No host-filesystem path resolution is used for this join.
- **HIS-REQ-009:** The parser shall locate the Traktor history container as `NODE[TYPE="PLAYLIST"][NAME="HISTORY"]`, then process each `ENTRY` under that node's direct child `PLAYLIST` element (`NODE[NAME="HISTORY"] > PLAYLIST > ENTRY`). Each history entry must contain both a `PRIMARYKEY` and an `EXTENDEDDATA` element. The parser shall retain the primary-key `TYPE` as source metadata but shall join by `PRIMARYKEY.KEY`.
- **HIS-REQ-010:** A history event is renderable only when `DECK` is an integer 0 through 3, `STARTTIME` is finite and non-negative, and `DURATION` is finite and greater than zero. Invalid events are omitted and recorded as parse warnings.
- **HIS-REQ-011:** An event without a matching collection entry is omitted and recorded as a warning. Duplicate collection keys are treated as ambiguous: the event is omitted rather than selecting an arbitrary entry.
- **HIS-REQ-012:** Event parsing shall preserve source order as a deterministic tie-breaker. Render order within a lane is ascending `startTimeSeconds`, then source order.

The required TypeScript model is:

```ts
type DeckId = 0 | 1 | 2 | 3

interface HistoryFileDescriptor {
  path: string
  filename: string
  startedAt: Date
  displayLabel: string
}

interface HistoryTrack {
  primaryKey: string
  primaryKeyType: string
  title: string
  artist: string
  bpm: number | null
  key: string
}

interface HistoryEvent extends HistoryTrack {
  deck: DeckId
  startTimeSeconds: number
  durationSeconds: number
  sourceOrder: number
}

interface SessionTimeline {
  events: HistoryEvent[]
  originSeconds: number
  endSeconds: number
  durationSeconds: number
  warnings: string[]
}
```

`originSeconds` is the minimum valid `startTimeSeconds`. `endSeconds` is the maximum of `startTimeSeconds + durationSeconds`; `durationSeconds` is `endSeconds - originSeconds`. Empty files have no timeline model rather than a synthetic zero-duration timeline.

## 4. UI layout: `SessionHistoryView.vue`

The view occupies the full workspace area beneath the existing header and two-tab navigation. It has two regions:

```text
┌─ History file sidebar ──────┬─ Session timeline canvas ─────────────┐
│ scrollable history list      │ session label / duration                  │
│ newest timestamp first       ├── timeline viewport (horizontal scroll) ──┤
│ selected file is distinct    │ Deck A │ blocks                        │
│ loading / empty / error      │ Deck B │ blocks                        │
│                              │ Deck C │ blocks                        │
│                              │ Deck D │ blocks                        │
└──────────────────────────┴─────────────────────────────────┘
```

- **HIS-REQ-013:** The left sidebar is independently scrollable and lists `filteredFiles` beneath its Sessions/date-filter header. Selecting a file loads and replaces only the timeline model for that selection.
- **HIS-REQ-014:** The main canvas has exactly four horizontal lanes at all times, including when a lane has no events. Their fixed order and visible labels are Deck A, Deck B, Deck C, and Deck D.
- **HIS-REQ-015:** The timeline viewport is horizontally scrollable when the computed canvas width exceeds its available width. Lane labels remain visible while the event canvas scrolls.
- **HIS-REQ-016:** Track blocks shall expose artist and title in visible text where space permits, with an accessible full label that also includes deck, start offset, and duration.
- **HIS-REQ-017:** The view must provide distinct loading, no-history-files, invalid-file, and valid-but-no-renderable-events states. Parse warnings may be summarized without rendering invalid events.
- **HIS-REQ-023:** The event canvas shall include a horizontal Time Ruler lane above Deck A. It renders adaptive time ticks that share the event surface's horizontal coordinate system and scrolling. The implementation shall select the smallest supported interval (for example, 30 seconds, 60 seconds, or 5 minutes) that preserves a minimum readable pixel distance between labels at the current `pixelsPerSecond` scale, preventing text collisions. Labels use `MM:SS` before one hour and `HH:MM:SS` at or beyond one hour.
- **HIS-REQ-026:** The timeline header shall identify the selected session and reserve its top-right area for the compact total-duration badge defined below; the adaptive Time Ruler remains the canonical detailed session-time reference.
- **HIS-REQ-029:** The selected session's total duration shall be displayed as a compact badge in the top-right of the timeline header, above the ruler and deck lanes. It shall be formatted as `HH:MM:SS` when at least one hour, otherwise `MM:SS`.
- **HIS-REQ-030:** Each track block shall render its BPM and Open Key below artist and title using smaller, visually muted metadata text when that metadata is available.

### 4.1 Timeline interaction contract

- **HIS-REQ-024:** The timeline viewport shall handle native mouse-wheel events. `Ctrl + Wheel` or `Meta + Wheel` adjusts the reactive `pixelsPerSecond` zoom scale within deterministic minimum and maximum clamps, preserving a readable horizontal timeline.
- **HIS-REQ-025:** `Shift + Wheel`, or a plain wheel event without modifiers, scrolls the timeline container horizontally by updating its `scrollLeft`. The browser's default vertical-scroll and browser-zoom behavior is prevented while the pointer is over the timeline viewport.
- **HIS-REQ-027:** Pointer drag navigation shall be supported on the timeline canvas. Pressing the primary mouse button records the pointer x-coordinate and starting `scrollLeft`; moving while pressed updates `scrollLeft` by the inverse horizontal pointer delta. Releasing the button or leaving the canvas ends the drag, and native text or element selection is prevented while dragging.

No playback controls, editing, drag/drop, track loading, filtering, or cross-file aggregation are in scope.

## 5. Timeline mathematics and rendering

For a selected non-empty timeline, let:

```text
O = min(event.startTimeSeconds)
E = max(event.startTimeSeconds + event.durationSeconds)
S = E - O
```

The timeline uses a reactive zoom scale `P = pixelsPerSecond`, expressed as pixels per second. `P` is positive and clamped to an implementation-defined readable range. Each track block uses the following unrounded values:

```text
offsetSeconds = event.startTimeSeconds - O
```

Rendering must use absolute pixel values, never percentage widths or offsets:

```text
leftPx          = offsetSeconds * P
widthPx         = event.durationSeconds * P
timelineWidthPx = S * P
```

- **HIS-REQ-018:** The earliest valid `STARTTIME` is the x-axis origin. Its block begins at `left = 0`; absolute `STARTTIME` values are never used directly as canvas positions.
- **HIS-REQ-019:** A block's width is proportional only to its `DURATION`; it does not extend to a track's collection playtime.
- **HIS-REQ-020:** Each block is an absolutely positioned child of its matching deck-lane event surface. Lanes are relatively positioned containers. Events on different decks may overlap in time without visual collision because they are in different lanes.
- **HIS-REQ-021:** The event surface shall have a positive, deterministic width of `timelineWidthPx` and use horizontal overflow when it exceeds its viewport. A minimum canvas width may be applied only as a viewport/layout safeguard; it must not rescale individual events independently or replace the pixel-scale calculations above.
- **HIS-REQ-022:** A zero or non-finite session span is not rendered as positioned blocks. It is treated as the valid-but-no-renderable-events state, avoiding division by zero.

For the supplied sample, valid events begin at 7468, 7792, and 7906 seconds. The origin is 7468 seconds. The first Deck A event begins at 0 seconds; the Deck B event begins at 324 seconds; the later Deck A event begins at 438 seconds. The session end is `max(7468 + 360.11853, 7792 + 146.116791, 7906 + 95.6381302) = 8001.6381302`, so `S = 533.6381302` seconds.

## 6. Implementation boundaries and dependencies

- **HIS-DEP-001:** Vue 3 and TypeScript, following the composition/API and type conventions in the GUI specifications.
- **HIS-DEP-002:** `@tauri-apps/plugin-fs` for file discovery and reads, plus a Tauri capability permitting the app only to list/read the discovered `History` directory and selected NML files. Exact capability syntax is resolved against the installed plugin version when implementation is authorized.
- **HIS-DEP-003:** Browser-native `DOMParser`; no XML parsing package is required or permitted for this feature.
- **HIS-DEP-004:** An already-resolved absolute path to the active `collection.nml`, including a clear UI error if it is unavailable. The feature must use the same active/override collection selection that the GUI uses elsewhere.

Recommended implementation separation, not an additional feature requirement:

```text
composables/useSessionHistory.ts  discovery, selection, DOM parsing, model state
types/history.ts                  HistoryFileDescriptor and timeline contracts
components/SessionHistoryView.vue presentation and timeline rendering only
```

## 7. Acceptance criteria

- **HIS-AC-001:** Given a resolved `collection.nml` path whose adjacent `History` directory contains matching and non-matching files, when discovery runs, then only matching history NML files appear in the sidebar in descending filename timestamp order.
- **HIS-AC-002:** Given `history_2025y11m11d_02h14m00s.nml`, when listed, then its UI label represents local 2025-11-11 02:14:00 and its raw filename remains accessible.
- **HIS-AC-003:** Given a well-formed history NML with a matching collection entry and valid `EXTENDEDDATA`, when selected, then the rendered block uses that collection entry's artist/title and the event's deck, start time, and duration.
- **HIS-AC-004:** Given the supplied sample NML, when parsed, then it produces three renderable events, two on Deck A and one on Deck B; Deck C and Deck D still render as empty lanes.
- **HIS-AC-005:** Given the supplied sample NML, when rendered, then event offsets are 0, 324, and 438 seconds from the left edge, and block placement/width follows the formulas in section 5.
- **HIS-AC-006:** Given a history event with an unknown, duplicate, or malformed primary key, when parsed, then no block is rendered for it and the valid events remain visible.
- **HIS-AC-007:** Given invalid XML, when a history file is selected, then the view shows a file-specific error and remains usable to select a different file.
- **HIS-AC-008:** Given a valid history event with deck 4, negative start time, non-positive duration, or a non-finite numeric attribute, when parsed, then it is omitted and does not create an additional lane.
- **HIS-AC-015:** Given history files started on multiple local calendar dates, when `filterDate` is `YYYY-MM-DD`, then the sidebar shows only files whose local `startedAt` year/month/day match; clearing the control restores every discovered file.
- **HIS-AC-016:** Given at least one discovered history file but no file on the selected date, when the filter is applied, then the sidebar displays `No sessions found for this date.` and the already loaded timeline remains unchanged.
- **HIS-AC-017:** Given a selected session with a loaded timeline and date filter, when the user switches to Collection and returns to Session History during the same application session, then the exact selected descriptor, timeline model, and date filter remain available without re-selection.

## 8. Test strategy for the eventual implementation

- Unit-test filename recognition, local date reconstruction, invalid-date rejection, and newest-first sorting.
- Unit-test XML parsing with the supplied fixture, unmatched/duplicate collection keys, missing metadata attributes, invalid XML, invalid numeric attributes, and all four deck values.
- Unit-test timeline origin, end, span, percentage, and pixel calculations, including invalid/zero spans.
- Component-test the four fixed lanes, sidebar selection, empty/error states, and horizontal overflow behavior.
- Unit-test local-date filtering, including leading-zero month/day formatting, clearing, and no-match derivation; component-test the clearable native date input and filtered empty state.
- Unit-test that repeated `useSessionHistory()` callers share the selected descriptor, timeline model, and date filter, and that rediscovery preserves them unless the resolved `collection.nml` path changes.
- Run the complete GUI test/type-check suite before declaring any implementation complete, in accordance with the repository test-integrity rule.

## 9. Revision: compressed inactivity gaps

This section supersedes the continuous real-time x-axis formulas in section 5 where they determine a rendered horizontal coordinate or canvas width. Event ordering, source times, duration validation, and the displayed session-duration badge remain based on real elapsed time.

### 9.1 Constants and terms

| Constant | Required value | Meaning |
|---|---:|---|
| `INACTIVITY_THRESHOLD_SECONDS` | `900` | A pause must be strictly longer than 15 minutes to be compressed. A gap of exactly 900 seconds remains uncompressed. |
| `COMPRESSED_GAP_SECONDS` | `150` | Every qualifying real gap occupies the same horizontal space as 2 minutes 30 seconds at the current zoom. |

An **activity interval** is `[event.startTimeSeconds, event.startTimeSeconds + event.durationSeconds]`. It is global across all four decks: simultaneous and overlapping playback extends the current activity interval. A **compressible gap** is the positive interval from the end of the global activity interval to the next event's start. It is not calculated from adjacent events in a single deck.

### 9.2 Data contracts and coordinate mapping

`useSessionHistory.ts` shall export these additions. `SessionTimeline.durationSeconds` remains the uncompressed real elapsed span; `renderedDurationSeconds` is used only for layout.

```ts
export interface TimelineGap {
  realStartOffsetSeconds: number
  realEndOffsetSeconds: number
  realDurationSeconds: number
  renderedDurationSeconds: number
  renderedStartOffsetSeconds: number
  renderedEndOffsetSeconds: number
}

export interface SessionTimeline {
  events: HistoryEvent[]
  originSeconds: number
  endSeconds: number
  durationSeconds: number
  renderedDurationSeconds: number
  gaps: TimelineGap[]
  warnings: string[]
}
```

After the existing valid-event sort, initialize `activityEndSeconds` to the first event's end time. For each later event in ascending `(startTimeSeconds, sourceOrder)` order:

1. If `event.startTimeSeconds - activityEndSeconds > 900`, append one gap with real offsets relative to `originSeconds`; its rendered start is the real start minus all earlier `(realDurationSeconds - 150)` reductions.
2. Set `activityEndSeconds = max(activityEndSeconds, event end)` after the comparison. Thus an event that starts before prior playback ends creates no gap even when it is on another deck.

Define `removedBefore(t)` as the sum of `gap.realDurationSeconds - gap.renderedDurationSeconds` for gaps whose `realEndOffsetSeconds <= t`. A real session offset maps to `t - removedBefore(t)`. No event may occur inside a detected gap.

```text
realOffsetSeconds(e) = e.startTimeSeconds - originSeconds
leftPx(e)            = (realOffsetSeconds(e) - removedBefore(realOffsetSeconds(e))) * P
widthPx(e)           = e.durationSeconds * P
timelineWidthPx      = renderedDurationSeconds * P
renderedDurationSeconds = durationSeconds - Sum(realGap - 150)
```

`timelineBlockStyle` shall use this mapping. It shall not alter block widths, event source times, tooltips, total duration, or import order.

### 9.3 Ruler and lane rendering

- **HIS-REQ-031:** The ruler must use the same piecewise coordinate mapping as event blocks. Normal tick labels represent real session offsets and resume after a gap at their mapped x-coordinate. It must not render ordinary tick labels inside an omitted real-time interval.
- **HIS-REQ-032:** Each `TimelineGap` renders one non-interactive full-height `.timeline-gap-break` at the compressed interval. It spans the ruler and all four deck lanes, has a non-text alternative, and is layered above lane backgrounds but below blocks and labels.
- **HIS-REQ-033:** The gap marker contains paired diagonal break lines (`//`) on the ruler and every lane, plus a visible and accessible ruler label `Gap: {formatDuration(realDurationSeconds)}`. Its accessible name must state that elapsed time was compressed.
- **HIS-REQ-034:** Changing zoom recalculates only pixel coordinates. A gap is always `150 * pixelsPerSecond` pixels wide and retains its real-duration label; no zoom operation may reclassify or merge gaps.

For example, if all decks are idle from `00:05:00` to `18:40:00`, a real gap of `18:35:00` is rendered as 150 seconds. A track beginning at `18:40:00` is laid out immediately after the 2:30 break instead of after 18:35 of blank canvas.

## 10. Revision: public playback metadata and visual state

### 10.1 Parsing contract

- **HIS-REQ-035:** `HistoryEvent` shall include the required property `playedPublic: boolean`.
- **HIS-REQ-036:** While parsing a valid history `ENTRY`, read the direct `EXTENDEDDATA@PLAYEDPUBLIC` attribute. Set `playedPublic` to `true` only when its exact value is `"1"`; set it to `false` when it is `"0"`, absent, empty, malformed, or any other value. A missing/malformed value is not a reason to omit an otherwise valid event.

```ts
export interface HistoryEvent extends HistoryTrack {
  deck: DeckId
  startTimeSeconds: number
  durationSeconds: number
  sourceOrder: number
  playedPublic: boolean
}
```

### 10.2 Component styling and accessibility

- **HIS-REQ-037:** A public block (`playedPublic === true`) retains the current highlighted amber/gold style.
- **HIS-REQ-038:** A cue/monitor-only block (`playedPublic === false`) is visually distinct at every zoom: neutral/zinc surface, dashed border, reduced opacity (target `0.60`), and no gold fill. Colour alone must not convey this state.
- **HIS-REQ-039:** The block's `title`, `aria-label`, and generated text label shall append `Public: Yes` or `Public: No (Cue)`. The label must continue to include title, artist where available, deck, real start offset, and duration.

## 11. Revision: import session as a collection playlist

### 11.1 Scope and modal state

- **HIS-REQ-040:** The timeline header renders an explicit **Import as Playlist** action next to the total-duration badge. It is enabled only when the active loaded session has one or more valid timeline events. `.history-file` items have no context-menu handler, floating menu, or backdrop; primary click continues to select/load a session.
- **HIS-REQ-041:** Clicking the header action opens a component-local confirmation modal for the currently selected session. The modal has a labelled required text input named **Playlist name**, primary **Import** and secondary **Cancel** actions, Escape-close behavior, focus trapping, and focus restoration to the header action.
- **HIS-REQ-042:** The name defaults to `Session - {selectedFile.displayLabel}`; for example, `Session - Jul 17, 2026, 1:50 AM`. It is reset from that default each time the modal opens. Submission trims the value and rejects an empty result without closing the modal.
- **HIS-REQ-043:** The modal includes an **Only public playback** checkbox, default checked. When checked, events with `playedPublic === false` are excluded. When unchecked, public and cue events are included. The modal reports the resulting event count before confirmation and disables Import when it is zero.

The import target must be parsed from the context-target file at import time; it must not rely on whichever file happens to be selected in the visible timeline. Loading/parsing failures leave the modal open, show the file-specific error, and make no collection-state mutation.

### 11.2 Chronological entry construction

- **HIS-REQ-044:** Build entries from renderable `HistoryEvent`s, filtered as above and sorted by `(startTimeSeconds, sourceOrder)`. Resolve every event to one active `useLibraryState().collection` track before persistence with this ordered two-step algorithm: (1) when the event title is non-empty, match exact `artist` and `title`; (2) if no primary match exists, take the segment after the final `/:` in `primaryKey` and match a collection track whose `location_path` ends with that filename. The resulting payload uses only the matched track's `location_path`.
- **HIS-REQ-045:** Preserve repeated playback occurrences. If a track was played twice, its resolved collection path appears twice at its two chronological positions. An event that fails both matching steps is excluded, counted as unresolved, and shown in the modal warning: `X track(s) could not be found in your current collection and will be skipped.` The import is disabled if no resolved entries remain.
- **HIS-REQ-046:** The import creates a regular, top-level playlist node, not a Smart Playlist and not a history-file mutation. The Core generates its UUID using the same contract as Smart Playlist creation and returns it in the successful response; the frontend neither generates a UUID nor inserts a provisional playlist leaf.

### 11.3 Direct persistence workflow

- **HIS-REQ-047:** On confirmation, `SessionHistoryView.vue` shall call a dedicated `useCueGridSidecar().createStaticPlaylist({ name, entries })` method. It must not call `useSaveStore`, add a provisional leaf to `useLibraryState`, mark a playlist dirty, or wait for **Save Changes**.
- **HIS-REQ-048:** The sidecar method invokes the Core directly with `--create-static-playlist`, the JSON payload, and the active `--nml` override when present. It follows the existing Smart Playlist interaction pattern: one immediate NML mutation, then a `useLibraryState().loadLibrary()` refresh only after the sidecar reports success.
- **HIS-REQ-049:** While the direct request is pending, the modal disables duplicate submit/cancel controls and displays an importing state. On success it closes, reloads the collection, and may select the returned playlist by UUID. On failure it leaves the modal open with its name/filter/draft intact, shows the Core error, and leaves global dirty state unchanged.

## 12. Direct Core playlist-creation contract

The import payload is:

```json
{
  "name": "Session - Jul 17, 2026, 1:50 AM",
  "entries": ["C:\\Music\\First.flac", "C:\\Music\\Preview.flac", "C:\\Music\\First.flac"]
}
```

Core validation must accept repeated entries and retain their submitted order. The Core generates a new UUID and creates the same regular `NODE TYPE="PLAYLIST"` / `PLAYLIST` structure and `PRIMARYKEY` encoding used by a Smart Playlist. It sets the name, `PLAYLIST@ENTRIES` to the submitted array length, and inserts the node at the same top-level playlist location as Smart Playlists. Any validation failure makes no NML mutation.

## 13. Acceptance criteria

- **HIS-AC-009:** Given an 18:35 global all-deck inactivity interval, when the session renders at `P=2`, then the gap is 300 px wide, has one `Gap: 18:35:00` indicator across ruler and lanes, and later events are shifted left by `(18:35:00 - 2:30) * 2` pixels.
- **HIS-AC-010:** Given overlapping Deck A and Deck B events, when one deck ends but the other continues, then no gap begins until all active events have ended; a 900-second pause is not compressed and a 901-second pause is.
- **HIS-AC-011:** Given `PLAYEDPUBLIC="1"`, `"0"`, and a missing attribute, parsing yields `true`, `false`, and `false` respectively. The final two blocks are visibly cue-styled and expose `Public: No (Cue)`.
- **HIS-AC-012:** Given an active session with valid events, when the user clicks the timeline-header Import as Playlist action, then the modal default name is based on the selected session's display label; no context menu is rendered and no state changes until valid confirmation.
- **HIS-AC-013:** Given events at 01:00, 02:00, and 03:00 where the first and third resolve to the same collection path, when importing with the public filter enabled, then the direct payload has entries `[first, second, first]` in that order. Given an unmatched artist/title whose primary key filename matches a collection `location_path`, then that fallback path is included; events failing both steps are excluded and counted in the warning.
- **HIS-AC-014:** Given a confirmed valid import, when the Core reports success, then the NML has already been atomically written and the collection reloads. Given a rejected create, then no NML or global dirty state changes and the modal preserves its draft.

## 14. Test strategy additions

- Unit-test global interval merging, exactly-threshold behavior, multiple gaps, coordinate mapping before/inside/after gaps, compressed canvas width, and ruler tick mapping.
- Unit-test `PLAYEDPUBLIC` extraction for `"1"`, `"0"`, missing, and malformed values.
- Component-test public/cue classes and accessible labels; header-action enabled state; modal defaults, validation, zero-entry state, filter changes, unresolved-track warning, direct importing state, and post-success library reload.
- Unit/integration-test exact artist/title resolution, filename-suffix fallback resolution, unresolved-event exclusion, duplicate playback preservation, direct sidecar payload serialization, Core creation validation, atomic rollback, generated NML shape, and duplicate playlist entries.
- Run the complete GUI type-check/test suite and `pytest` from `core/`; both must pass before implementation is complete.

## 15. Out of scope

- Audio analysis, playback, waveform rendering, and automated cue injection. The narrowly scoped direct Core playlist-creation mutation in section 12 is in scope.
- Writing, renaming, deleting, or exporting history files.
- Combining multiple history files into a single timeline.
- Support for deck values outside 0–3 or a variable number of deck lanes.
