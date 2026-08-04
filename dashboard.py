#!/usr/bin/env python3
"""
Hikvision ANPR Dashboard v2.0.

The v1 dashboard called glob.glob() + open()+json.load() on every JSON
file in output/Entry and output/Exit, on every single /api/events
request. That doesn't scale: at current volume (~240 events/day) it
gets a little slower every day, forever.

v2.0 keeps an in-memory index (EventIndex) that a single background
thread updates incrementally - each scan cycle only stats directory
entries (cheap) and only opens+parses a JSON file if it's new or its
mtime changed since the last scan. Request handlers never touch disk;
they read the already-built, already-sorted in-memory list. A small
SSE endpoint (/api/events/stream) pushes a lightweight "something
changed, go refetch" signal so the frontend updates immediately
instead of waiting for its next poll tick.

/api/events response shape is unchanged from v1 (aside from the
additive "version" field), so nothing downstream breaks.
"""

import os
import json
import threading
import time
from datetime import datetime

from flask import (
    Flask,
    render_template,
    jsonify,
    send_from_directory,
    abort,
    Response,
    stream_with_context,
)


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = "/home/rixos/Hikvision_ANPR"

ENTRY_DIR = os.path.join(BASE_DIR, "output", "Entry")
EXIT_DIR = os.path.join(BASE_DIR, "output", "Exit")

MAX_EVENTS_RETURNED = 500

# How often the background thread re-checks the output directories for
# new/modified files. This is not the browser's refresh rate - the
# browser is pushed an update immediately via SSE when something
# actually changes; this interval just bounds the worst-case delay
# between a file landing on disk and the dashboard noticing it.
INDEX_SCAN_INTERVAL = float(os.getenv("DASHBOARD_INDEX_SCAN_INTERVAL_S", "0.3"))

# SSE heartbeat so idle connections aren't silently dropped by
# intermediate proxies/load balancers.
SSE_HEARTBEAT_SECONDS = 15

# Client-side polling fallback interval (used alongside SSE as a
# robustness safety net, and as the sole refresh mechanism for any
# client where SSE is blocked). Configurable via .env; 250ms default
# per spec - deliberately not lower, since that would just add load
# for no perceptible benefit given ANPR events are discrete.
try:
    DASHBOARD_POLL_INTERVAL_MS = int(os.getenv("DASHBOARD_POLL_INTERVAL_MS", "250"))
except ValueError:
    DASHBOARD_POLL_INTERVAL_MS = 250

app = Flask(__name__)


# ============================================================
# EVENT SHAPING HELPERS
# (unchanged from v1 - the JSON-on-disk schema and the API response
# shape built from it are both preserved for backward compatibility)
# ============================================================

def safe_load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        app.logger.warning("Unable to read %s: %s", filename, exc)
        return None


def format_event_time(value):
    if not value:
        return {"display": "-", "date": "-", "time": "-"}

    try:
        dt = datetime.fromisoformat(value)
        return {
            "display": dt.strftime("%d/%m/%Y %H:%M:%S"),
            "date": dt.strftime("%d/%m/%Y"),
            "time": dt.strftime("%H:%M:%S"),
        }
    except Exception:
        return {"display": value, "date": "", "time": ""}


def normalise_location(value):
    if not value:
        return "-"

    mapping = {
        "DXB": "Dubai",
        "AUH": "Abu Dhabi",
        "SHJ": "Sharjah",
        "AJM": "Ajman",
        "UAQ": "Umm Al Quwain",
        "RAK": "Ras Al Khaimah",
        "FUJ": "Fujairah",
    }

    return mapping.get(value.upper(), value)


def build_event(data, direction, json_file):
    anpr = data.get("anpr", {})
    camera = data.get("camera", {})
    images = data.get("images", {})

    lpr_read = anpr.get("lpr_read", True)
    full_plate = anpr.get("full_plate")

    if not lpr_read or not full_plate:
        full_plate = "LPR not Read"
        lpr_read = False

    country_code = anpr.get("country") or anpr.get("area") or ""
    province = anpr.get("province") or ""
    location_code = province or country_code

    event_time = format_event_time(data.get("event_time"))

    confidence = anpr.get("confidence")

    if not lpr_read:
        confidence_display = "-"
    elif confidence:
        confidence_display = f"{confidence}%"
    else:
        confidence_display = "-"

    return {
        "id": os.path.basename(json_file).replace(".json", ""),
        "direction": direction,
        "event_time": data.get("event_time", ""),
        "display_time": event_time["display"],
        "date": event_time["date"],
        "time": event_time["time"],
        "lpr_read": lpr_read,
        "plate_number": anpr.get("plate_number", ""),
        "category": anpr.get("category", ""),
        "full_plate": full_plate,
        "country": country_code,
        "state": normalise_location(location_code),
        "confidence": confidence,
        "confidence_display": confidence_display,
        "plate_color": anpr.get("plate_color") or "-",
        "plate_type": anpr.get("plate_type") or "-",
        "vehicle_type": anpr.get("vehicle_type") or "-",
        "vehicle_color": anpr.get("vehicle_color") or "-",
        "movement": anpr.get("direction") or "-",
        "camera_name": camera.get("name") or direction,
        "plate_image": images.get("plate"),
        "vehicle_image": images.get("vehicle"),
        "full_image": images.get("full"),
    }


def build_stats(events):
    today = datetime.now().strftime("%d/%m/%Y")

    today_events = [event for event in events if event["date"] == today]

    entries = sum(1 for event in today_events if event["direction"] == "Entry")
    exits = sum(1 for event in today_events if event["direction"] == "Exit")
    read_events = sum(1 for event in today_events if event["lpr_read"])
    total = len(today_events)

    read_rate = round((read_events / total) * 100, 1) if total else 0

    return {
        "total": total,
        "entry": entries,
        "exit": exits,
        "read": read_events,
        "not_read": total - read_events,
        "read_rate": read_rate,
    }


# ============================================================
# EVENT INDEX
#
# One in-memory copy of every parsed event, keyed by its source JSON
# path, kept current by a single background thread. Request handlers
# only ever read from this - never touch disk.
# ============================================================

class EventIndex:

    def __init__(self):
        self.lock = threading.Lock()
        self._mtimes = {}   # path -> mtime last parsed
        self._events = {}   # path -> built event dict

        self.version = 0
        self._sorted_cache = None
        self._sorted_cache_version = -1

        self._subscribers = []
        self._sub_lock = threading.Lock()

    def scan_once(self):
        """Stat every file in both output directories (cheap); only
        open+parse the ones that are new or whose mtime changed since
        the last scan. Returns True if the index actually changed."""

        changed = False

        for directory, direction in ((ENTRY_DIR, "Entry"), (EXIT_DIR, "Exit")):
            try:
                with os.scandir(directory) as it:
                    entries = [e for e in it if e.name.endswith(".json")]
            except FileNotFoundError:
                continue

            seen_paths = set()

            for entry in entries:
                path = entry.path
                seen_paths.add(path)

                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue

                with self.lock:
                    known_mtime = self._mtimes.get(path)

                if known_mtime == mtime:
                    continue

                data = safe_load_json(path)

                if not data:
                    continue

                try:
                    event = build_event(data, direction, path)
                except Exception as exc:
                    app.logger.warning("Error processing %s: %s", path, exc)
                    continue

                with self.lock:
                    self._events[path] = event
                    self._mtimes[path] = mtime

                changed = True

            with self.lock:
                known_for_dir = [
                    p for p in self._mtimes
                    if os.path.dirname(p) == directory
                ]

            for path in known_for_dir:
                if path not in seen_paths:
                    with self.lock:
                        self._mtimes.pop(path, None)
                        self._events.pop(path, None)
                    changed = True

        if changed:
            with self.lock:
                self.version += 1
            self._notify_subscribers()

        return changed

    def get_sorted_events(self):
        with self.lock:
            if (
                self._sorted_cache is not None
                and self._sorted_cache_version == self.version
            ):
                return self._sorted_cache, self.version

            # Never sort by filename - always by the camera's own
            # event timestamp (ISO8601, consistent timezone offset
            # across both cameras, so lexicographic sort is correct).
            events = sorted(
                self._events.values(),
                key=lambda event: event.get("event_time", ""),
                reverse=True,
            )

            self._sorted_cache = events
            self._sorted_cache_version = self.version

            return events, self.version

    # ---- SSE subscriber management ----

    def subscribe(self):
        wake = threading.Event()

        with self._sub_lock:
            self._subscribers.append(wake)

        return wake

    def unsubscribe(self, wake):
        with self._sub_lock:
            if wake in self._subscribers:
                self._subscribers.remove(wake)

    def _notify_subscribers(self):
        with self._sub_lock:
            subscribers = list(self._subscribers)

        for wake in subscribers:
            wake.set()


index = EventIndex()


def _background_scanner():
    while True:
        try:
            index.scan_once()
        except Exception:
            app.logger.exception("Event index scan failed")

        time.sleep(INDEX_SCAN_INTERVAL)


# Build the index once, synchronously, before serving any request -
# otherwise the first requests after a restart would see an empty
# dashboard while the background thread catches up.
index.scan_once()

_scanner_thread = threading.Thread(
    target=_background_scanner,
    daemon=True,
    name="dashboard-index-scanner",
)
_scanner_thread.start()


# ============================================================
# WEB ROUTES
# ============================================================

@app.route("/")
def dashboard():
    return render_template(
        "dashboard.html",
        poll_interval_ms=DASHBOARD_POLL_INTERVAL_MS,
    )


@app.route("/api/events")
def api_events():
    events, version = index.get_sorted_events()

    latest_entry = next(
        (event for event in events if event["direction"] == "Entry"),
        None,
    )

    latest_exit = next(
        (event for event in events if event["direction"] == "Exit"),
        None,
    )

    return jsonify({
        "events": events[:MAX_EVENTS_RETURNED],
        "latest_entry": latest_entry,
        "latest_exit": latest_exit,
        "stats": build_stats(events),
        "version": version,
    })


@app.route("/api/events/stream")
def api_events_stream():
    """Server-Sent Events channel. Pushes a small "something changed"
    signal whenever the index updates - the client reacts by
    refetching /api/events, so the response payload shape/contract
    used everywhere else never has to be duplicated here."""

    def generate():
        wake = index.subscribe()

        try:
            yield "retry: 2000\n"
            yield f"event: update\ndata: {index.version}\n\n"

            while True:
                triggered = wake.wait(timeout=SSE_HEARTBEAT_SECONDS)

                if triggered:
                    wake.clear()
                    yield f"event: update\ndata: {index.version}\n\n"
                else:
                    yield ": heartbeat\n\n"

        except GeneratorExit:
            pass
        finally:
            index.unsubscribe(wake)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================
# IMAGE ROUTE
# ============================================================

@app.route("/images/<direction>/<path:filename>")
def image_file(direction, filename):
    if direction.lower() == "entry":
        directory = ENTRY_DIR
    elif direction.lower() == "exit":
        directory = EXIT_DIR
    else:
        abort(404)

    return send_from_directory(directory, filename)


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "entry_directory": os.path.isdir(ENTRY_DIR),
        "exit_directory": os.path.isdir(EXIT_DIR),
        "indexed_events": len(index._events),
        "index_version": index.version,
    })


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5020,
        debug=False,
        threaded=True,
    )
