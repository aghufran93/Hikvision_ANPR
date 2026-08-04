# Changelog

All notable changes to this project. Reconstructed from the codebase and
project history; entries are dated where a date is known.

## [2.0] - 2026-08-04

### Added
- `anpr_common.py` — new shared module containing `EventCache`,
  `PendingEvent`/`SavedEventInfo`, XML/multipart parsing, atomic writes,
  trigger extraction, and the `ANPRListener` class.
- UUID + `pId`-keyed multi-event cache, replacing the previous
  single-in-flight-event model. Multiple vehicles' events can now be open
  simultaneously without their images being crossed.
- Late-image reattachment: an image arriving after its event was already
  saved (partial) can still be merged back in, within a configurable
  `ANPR_LATE_ATTACH_WINDOW`.
- `dashboard.py` rewritten with an in-memory `EventIndex`: a background
  thread does incremental, `stat()`-based scans of `output/Entry` and
  `output/Exit` instead of re-reading every JSON file on every request.
- `GET /api/events/stream` — Server-Sent Events endpoint pushing a
  lightweight "changed" signal so the dashboard updates instantly.
- `templates/dashboard.html` / `static/js/dashboard.js` updated to consume
  the SSE stream, with a configurable polling interval
  (`DASHBOARD_POLL_INTERVAL_MS`) as a fallback.
- New tunables: `ANPR_EVENT_IDLE_TIMEOUT`, `ANPR_EVENT_MAX_LIFETIME`,
  `ANPR_LATE_ATTACH_WINDOW`, `ANPR_ORPHAN_GRACE_SECONDS`,
  `DASHBOARD_POLL_INTERVAL_MS`.

### Changed
- `entry_listener.py` and `exit_listener.py` reduced from ~1,300 lines of
  near-duplicate logic each to ~20-line role-specific configuration
  wrappers around `anpr_common.ANPRListener`.
- `/api/events` response shape preserved for backward compatibility, with
  one additive `version` field.

### Removed
- `camera_listener.py` — confirmed dead code (unused by any systemd unit
  or import, and produced an incompatible JSON schema from the other two
  listeners).

### Fixed
- Cross-event image misattribution: a late-arriving image from one
  vehicle could previously be silently attached to a different, unrelated
  vehicle's event. Root cause was orphan-claiming logic that didn't
  verify identity before attaching. Fixed by construction in the new
  `pId`-indexed `EventCache` design.
- Images from events that finished after their listener's idle-timeout
  save were previously orphaned permanently; they now reattach
  automatically within the late-attach window.

## [1.5] - 2026-08-04

### Added
- `LISTENER_VERSION` tag logged on every camera connection, so the running
  code version is visible in the log file (`grep "Listener v" logs/*.log`).
- Hardened the watchdog thread (responsible for idle/max-lifetime
  completion) with its own exception boundary, so an unhandled error
  inside it can no longer silently disable event-completion timeouts for
  the rest of a connection.
- Firmware-declared image classification via the XML's `pId`/
  `pictureInfoList`, with positional-order fallback for firmware that
  doesn't provide one.
- Orphan image buffering and quarantine (single-event-slot version,
  superseded by v2.0's multi-event cache).
- Centralized atomic JSON writes.

### Fixed
- Image misclassification: `detectionPicture` (the full/detection scene
  shot) was being mapped to `"vehicle"` by a positional/substring
  heuristic; corrected via `pId`/type-based classification.

### Known issues (flagged, not fixed in this version)
- A race condition in the old single-event orphan-claiming logic
  (mutating a published pending event outside its lock) — superseded
  entirely by the v2.0 rewrite rather than patched in place.
- Same-second filename collision risk — not addressed.

## [Unreleased / pre-1.5]

The original implementation (two independent, ~1,300-line, near-identical
`entry_listener.py`/`exit_listener.py` files, plus an unused third
`camera_listener.py` variant) predates the history captured here in
detail. Known characteristics at the time v1.5 work began: single
in-flight-event tracking per listener, completion driven by idle timeout,
and the orphaned/misattributed-image behaviour that motivated the v1.5
and v2.0 work above.
