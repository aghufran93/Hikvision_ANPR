# Hikvision ANPR Monitoring System

A production ANPR (Automatic Number Plate Recognition) monitoring system for
Hikvision DeepinView traffic cameras. Two always-on **listener** services
consume each camera's raw ISAPI multipart event stream, reconstruct
vehicle-pass events (plate number, confidence, vehicle/plate/full-scene
images) with zero reliance on arrival order or timing, and a **live web
dashboard** displays them in real time.

Built for a single Entry camera + single Exit camera deployment
(iDS-2CD7A46G0/P-LZHS, firmware V5.8.20), running as systemd services on
Ubuntu Linux.

---

## Features

- **Entry & Exit listeners** — two long-running processes, one per camera,
  sharing a single core implementation (`anpr_common.py`)
- **UUID + pId event matching** — every event and every picture inside it is
  identified by IDs the camera itself declares in the XML, never by arrival
  order or timing, so multiple vehicles' events can be in flight
  simultaneously without their images getting crossed
- **Late-image reattachment** — if an event is saved before all its images
  arrive (a slow "full scene" shot, for example), a straggler image arriving
  later is merged back into that event's existing JSON in place, instead of
  being lost or filed under the wrong vehicle
- **Orphan image quarantine** — an image that truly cannot be matched to any
  event is held briefly, then written to `xml_temp/<Camera>/orphans/` for
  manual review instead of silently discarded
- **`LPR_NOT_READ` handling** — events where the plate wasn't read are saved
  the same way as successful reads, just flagged accordingly
- **Atomic writes** — every JSON/image file is written to a temp path and
  `os.replace()`'d into place, so the dashboard (which reads these files
  continuously) never sees a half-written file
- **Automatic reconnect** — camera disconnects, timeouts, and malformed
  data are logged and recovered from automatically with exponential backoff;
  the listener never gives up and exits
- **Live dashboard** — an in-memory event index (no per-request disk scan),
  pushed to the browser instantly via Server-Sent Events with a polling
  fallback; search/filter by plate, direction, and LPR status; a full-size
  image viewer
- **systemd-managed** — all three processes run as systemd services with
  `Restart=always`

---

## Project Architecture

```
   Hikvision Entry Camera                Hikvision Exit Camera
   (iDS-2CD7A46G0/P-LZHS)                 (iDS-2CD7A46G0/P-LZHS)
            │                                       │
            │  HTTPS multipart ISAPI event stream    │
            │  (XML + JPEG parts, digest auth)       │
            ▼                                       ▼
     entry_listener.py                        exit_listener.py
   (role config only: camera_role="Entry")  (role config only: camera_role="Exit")
            │                                       │
            └───────────────┬───────────────────────┘
                             ▼
                   anpr_common.ANPRListener
              (shared: reconnect loop, multipart
               parser, XML parser, EventCache)
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
     output/<Camera>/*.json         output/<Camera>/*_plate.jpg
     output/<Camera>/*.json           _vehicle.jpg / _full.jpg
     xml_temp/<Camera>/*.xml
     xml_temp/<Camera>/orphans/*.jpg (unmatched images)
              │
              ▼
        dashboard.py (Flask)
   EventIndex background thread
   (incremental stat-based scan,
    never touches disk per-request)
              │
   ┌──────────┼───────────────┐
   ▼          ▼               ▼
/api/events  /api/events/stream   /images/<dir>/<file>
 (JSON)       (SSE push)           (JPEG, served from output/)
   │          │
   └────┬─────┘
        ▼
 templates/dashboard.html + static/js/dashboard.js
        (browser — live-updating table, latest
         Entry/Exit cards, image modal)

Logs: logs/entry_listener.log, logs/exit_listener.log (+ systemd journal)
```

### How an event actually flows

1. The camera opens one long-lived HTTPS connection per listener and streams
   a `multipart/form-data`-style body forever. A vehicle pass produces one
   XML part (`eventType=ANPR`, with a `<UUID>` and a `<pictureInfoList>`
   declaring how many JPEGs to expect and a `<pId>` for each) followed,
   *at some later and unpredictable time*, by 0–3 JPEG parts whose
   multipart filename **is** that picture's `pId`.
2. `anpr_common.EventCache` keeps every event that's still waiting on images
   (`pending`) addressable by its UUID, and maintains a `pid -> UUID` index
   built from each event's own declared picture plan. An incoming JPEG is
   routed through that index — never through "whatever event is newest."
3. Once an event has all its expected images (or `picNum` was 0), it's saved
   immediately: the raw XML goes to `xml_temp/<Camera>/`, each image to
   `output/<Camera>/<basename>_<type>.jpg`, and a combined JSON to
   `output/<Camera>/<basename>.json`.
4. If an event goes quiet for `ANPR_EVENT_IDLE_TIMEOUT` seconds (default 90)
   or hits `ANPR_EVENT_MAX_LIFETIME` (default 180), it's saved as **partial**
   with whatever images arrived. It stays addressable for
   `ANPR_LATE_ATTACH_WINDOW` more seconds (default 60) — if a late image
   with a recognised `pId` shows up in that window, it's attached and the
   JSON is rewritten in place (`late_attached: true`).
5. An image whose `pId` matches nothing at all is buffered for
   `ANPR_ORPHAN_GRACE_SECONDS` (default 5) in case its XML is still in
   transit, then written to `xml_temp/<Camera>/orphans/` for manual review.
6. `dashboard.py` never talks to the listeners directly — it only watches
   the `output/Entry` and `output/Exit` directories. A background thread
   re-`stat()`s both every `DASHBOARD_INDEX_SCAN_INTERVAL_S` (default 0.3s)
   and only opens/parses a file if it's new or its mtime changed, keeping an
   in-memory, pre-sorted event list. Every API request reads only that
   in-memory list — never the disk. When the index changes, every connected
   browser is pushed an `update` event over SSE and refetches
   `/api/events`; a client-side poll (default every 250ms) runs underneath
   as a safety net for proxies that block SSE.

---

## Folder Structure

```
Hikvision_ANPR/
├── anpr_common.py        Shared core: XML/multipart parsing, EventCache,
│                         ANPRListener (reconnect loop + stream handling).
│                         entry_listener.py and exit_listener.py are both
│                         thin wrappers around the ANPRListener class
│                         defined here — this file is where nearly all
│                         the real logic lives.
├── entry_listener.py     Entry-camera entry point. ~20 lines: instantiates
│                         ANPRListener(camera_role="Entry",
│                         camera_env_key="CAMERA_ENTRY", ...) and calls
│                         .listen(). No parsing logic of its own.
├── exit_listener.py      Same, for the Exit camera
│                         (camera_env_key="CAMERA_EXIT").
├── dashboard.py          Flask web app. Serves the dashboard page, the
│                         JSON API, the SSE live-update stream, and image
│                         files straight out of output/.
├── requirements.txt      Python dependencies (see Dependencies below).
├── .env                  Camera IPs/credentials + tuning constants.
│                         Never committed (gitignored) — see .env.example.
├── project_structure.txt A static tree snapshot checked into the repo.
│                         Note: it still lists camera_listener.py, which
│                         has since been confirmed as dead code and
│                         deleted — treat this file as historical, not
│                         authoritative.
├── templates/
│   └── dashboard.html    Single-page dashboard template (Jinja2). Injects
│                         DASHBOARD_POLL_INTERVAL_MS into the page for
│                         dashboard.js to read.
├── static/
│   ├── css/dashboard.css Dashboard styling.
│   ├── js/dashboard.js   All dashboard client logic: fetches /api/events,
│   │                     renders the latest-entry/exit cards and history
│   │                     table, client-side search/filter, the image
│   │                     modal, and the SSE + polling live-update logic.
│   └── logo.png          Header logo.
├── output/
│   ├── Entry/            One <timestamp>_<plate>.json + up to 3 JPEGs
│   │                     per Entry event. Gitignored (runtime data).
│   └── Exit/             Same, for Exit events.
├── xml_temp/
│   ├── Entry/            Raw XML for every saved Entry event, plus:
│   │   └── orphans/      JPEGs that couldn't be matched to any event.
│   └── Exit/              Same, for Exit. Gitignored (runtime data).
├── logs/
│   ├── entry_listener.log  Plain-text log, no rotation configured (see
│   └── exit_listener.log   Code Review notes). Gitignored.
├── data/                 Reference material, not runtime data:
│   ├── ISAPI_Network Cameras_DeepinView Series 2/   Hikvision ISAPI
│   │                     protocol docs (PDF/xlsx) for this camera family.
│   ├── iVMS-4200_*.pdf   Hikvision's iVMS-4200 client manual/datasheet/
│   │                     release notes.
│   ├── iDS-2CD7A46G0_..._.txt  A raw diagnostic/firmware log dump pulled
│   │                     from the camera at one point.
│   ├── TPP.pdf           Vendor reference document.
│   ├── Entry/, Exit/     Empty. Confirmed unreferenced by any code in
│   │                     this repo — legacy leftovers, not used.
│   └── iVMS-4200*.pkg/.zip  iVMS-4200 client installers (macOS/other) —
│                         intentionally left untracked; see .gitignore.
└── venv/                 Local virtual environment. Not committed.
```

`camera_listener.py`, mentioned in some earlier project notes, **does not
exist in this repository** — it was an unused, incompatible-schema
duplicate of the listener logic and was deleted once confirmed dead.

---

## Installation Guide

### 1. Clone the repository

```bash
git clone https://github.com/aghufran93/Hikvision_ANPR.git
cd Hikvision_ANPR
```

### 2. Create a virtual environment

**Linux / macOS**

```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell)**

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

**Windows (cmd.exe)**

```cmd
python -m venv venv
venv\Scripts\activate.bat
```

You'll know it worked when your shell prompt is prefixed with `(venv)`.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure your environment

```bash
cp .env.example .env
```

Then edit `.env` with your camera IPs and credentials — see
[Environment Variables](#environment-variables) below.

### 5. Deactivate the virtual environment (when you're done)

```bash
deactivate
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in real values. **Never commit
`.env`** — it's already listed in `.gitignore`.

| Variable | Purpose | Example | Required |
|---|---|---|---|
| `CAMERA_ENTRY` | Entry camera IP/hostname. Read via `os.getenv("CAMERA_ENTRY")` in `anpr_common.py`. | `192.168.22.164` | **Required** |
| `CAMERA_EXIT` | Exit camera IP/hostname. | `192.168.22.165` | **Required** |
| `CAMERA_USER` | Digest-auth username shared by both cameras. | `admin` | **Required** |
| `CAMERA_PASS` | Digest-auth password shared by both cameras. | `(secret)` | **Required** |
| `ANPR_EVENT_IDLE_TIMEOUT` | Seconds with no new image for an event before it's saved as-is. | `90` | Optional (default `90`) |
| `ANPR_EVENT_MAX_LIFETIME` | Hard cap in seconds from XML arrival to forced save. | `180` | Optional (default `180`) |
| `ANPR_LATE_ATTACH_WINDOW` | Seconds after a partial save that a late image is still accepted. | `60` | Optional (default `60`) |
| `ANPR_ORPHAN_GRACE_SECONDS` | Seconds an unmatched image is buffered before being quarantined. | `5` | Optional (default `5`) |
| `DASHBOARD_POLL_INTERVAL_MS` | Browser polling fallback interval, milliseconds. | `250` | Optional (default `250`) |
| `DASHBOARD_INDEX_SCAN_INTERVAL_S` | How often the dashboard's background thread re-scans `output/` for changes. | `0.3` | Optional (default `0.3`) |

### Present in some deployments' `.env` but **not read by any code**

These keys were found in a live `.env` file but do not appear anywhere in
`anpr_common.py` or `dashboard.py` — setting them currently has **no
effect**. They're listed here so you don't waste time debugging why
changing them didn't work, not because they're part of the supported
configuration surface:

`ENTRY_CAMERA_IP`, `EXIT_CAMERA_IP`, `ENTRY_OUTPUT`, `EXIT_OUTPUT`,
`OG_DIR`, `XML_TEMP_DIR`, `CAMERA_PROTOCOL`, `APP_HOST`, `APP_PORT`,
`APP_SECRET_KEY`.

Notably, the dashboard's bind address and port are **hardcoded** in
`dashboard.py` (`app.run(host="0.0.0.0", port=5020, debug=False,
threaded=True)`), not read from `APP_HOST`/`APP_PORT` — see
[Configuration Guide](#configuration-guide) for how to actually change them.

---

## Configuration Guide

**Camera IPs / credentials** — set `CAMERA_ENTRY`, `CAMERA_EXIT`,
`CAMERA_USER`, `CAMERA_PASS` in `.env`. Both cameras must share one
username/password (the code has no per-camera credential support).

**Event timing behaviour** — tune `ANPR_EVENT_IDLE_TIMEOUT`,
`ANPR_EVENT_MAX_LIFETIME`, `ANPR_LATE_ATTACH_WINDOW`,
`ANPR_ORPHAN_GRACE_SECONDS` in `.env` if your camera's real-world image
latency differs from the defaults (see Troubleshooting for how to check).

**Dashboard port** — not env-configurable today. Edit the `app.run(...)`
call at the bottom of `dashboard.py` directly, then restart the
`rixos-hik-dashboard` service.

**Storage locations** — `BASE_DIR` is hardcoded to
`/home/rixos/Hikvision_ANPR` in `entry_listener.py`, `exit_listener.py`,
and `dashboard.py`. To deploy this repo at a different path, you must edit
that constant in all three files — there is no environment variable for
it currently.

**Logging** — each listener logs at `INFO` level (hardcoded) to both
`logs/<name>.log` and stdout (captured by systemd/journald). The dashboard
uses Flask's default `app.logger`. There's no `LOG_LEVEL` environment
variable and no rotation configured — see Code Review.

**Debug mode** — the dashboard runs with `debug=False` hardcoded; there is
no env-driven way to enable Flask's debug/reloader mode. If you need it
for local development, temporarily edit `dashboard.py`.

---

## Running the Project

There's no strict startup order requirement — each listener and the
dashboard create their own `output/`, `logs/`, and `xml_temp/`
subdirectories on first run (`os.makedirs(..., exist_ok=True)`), and the
dashboard tolerates the `output/` directories being empty or missing. The
recommended order below just gets you useful data to look at fastest.

**Step 1 — Run the Entry listener**

```bash
source venv/bin/activate
python3 entry_listener.py
```

**Step 2 — Run the Exit listener** (in a separate terminal/session)

```bash
source venv/bin/activate
python3 exit_listener.py
```

**Step 3 — Run the dashboard** (in a separate terminal/session)

```bash
source venv/bin/activate
python3 dashboard.py
```

Then open `http://<server-ip>:5020/` in a browser.

### Production (systemd)

The live deployment runs all three as systemd services (unit files live in
`/etc/systemd/system/`, not in this repo):

```ini
[Unit]
Description=Rixos Hikvision ANPR Entry Listener
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rixos
Group=rixos
WorkingDirectory=/home/rixos/Hikvision_ANPR
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=/home/rixos/Hikvision_ANPR/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
ExecStart=/home/rixos/Hikvision_ANPR/venv/bin/python3 /home/rixos/Hikvision_ANPR/entry_listener.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

(`rixos-hik-exit.service` and `rixos-hik-dashboard.service` follow the same
pattern, pointing at `exit_listener.py` and `dashboard.py` respectively.)

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rixos-hik-entry rixos-hik-exit rixos-hik-dashboard
sudo systemctl restart rixos-hik-entry      # after a code change
sudo systemctl status rixos-hik-dashboard
journalctl -u rixos-hik-entry -f            # tail logs live
```

---

## Dependencies

From `requirements.txt`:

| Package | Used for |
|---|---|
| `requests` | HTTP client for the long-lived streaming connection to each camera's ISAPI endpoint, with `HTTPDigestAuth` for authentication. |
| `urllib3` | Only used to call `disable_warnings(InsecureRequestWarning)`, since the camera connection uses `verify=False` (self-signed HTTPS cert). |
| `python-dotenv` | Loads `.env` into the process environment (`load_dotenv()` in `anpr_common.py`). |
| `Flask` | The dashboard's web framework — routing, `render_template`, `jsonify`, `send_from_directory`, and the SSE response via `Response` + `stream_with_context`. |

No database, ORM, task queue, or frontend build tooling is used anywhere
in this project — the dashboard frontend is plain HTML/CSS/vanilla JS
served directly by Flask.

---

## Troubleshooting

**Camera cannot connect / listener logs `Camera connection error` or keeps reconnecting**
- Confirm the camera IP in `.env` (`CAMERA_ENTRY` / `CAMERA_EXIT`) is reachable: `curl -k -u user:pass https://<camera-ip>/ISAPI/System/deviceInfo`.
- Check `CAMERA_USER`/`CAMERA_PASS` are correct — a bad password surfaces as an HTTP error logged right after `Listener v2.0 connecting to ... camera`.
- The listener retries forever with exponential backoff (5s → 30s cap) — it will recover on its own once the camera is reachable again; no restart needed.

**`RuntimeError: CAMERA_ENTRY is missing from .env`** (or `CAMERA_EXIT`/`CAMERA_USER`/`CAMERA_PASS`)
- `.env` doesn't exist or is missing that key. Copy `.env.example` to `.env` and fill it in — this is a hard failure by design, not a bug.

**`ModuleNotFoundError: No module named 'flask'` (or `requests`, `dotenv`)**
- The virtual environment isn't activated, or dependencies weren't installed. Run `source venv/bin/activate && pip install -r requirements.txt`.

**Port already in use (dashboard won't start)**
- Something else is already bound to 5020: `lsof -i :5020` or `sudo ss -tlnp | grep 5020`. Either stop that process or change the hardcoded port in `dashboard.py` (see Configuration Guide).

**Permission denied running `systemctl restart ...`**
- The systemd units require `sudo`. If you're running this restart from an automated/non-interactive context, it will fail silently with "a password is required" rather than prompting — always run these commands from an interactive terminal.

**Dashboard shows stale data / doesn't update**
- First check the backend directly, bypassing the browser entirely: `curl -s http://localhost:5020/api/events | python3 -m json.tool` and compare its `latest_entry`/`latest_exit` timestamps against what's on screen.
- If the API is current but the page isn't, the browser tab may have been suspended in the background (browsers throttle `setInterval` timers on hidden tabs) — hard refresh (Ctrl+Shift+R) or open a fresh tab.
- If the API itself is stale, confirm the dashboard process actually restarted: `systemctl show rixos-hik-dashboard.service -p ExecMainStartTimestamp,NRestarts`. A `systemctl restart` that doesn't change the start time did not actually take effect.

**Images not saving / event stuck as `EVENT PARTIAL`**
- Check `logs/<name>_listener.log` for `EVENT PARTIAL | ... | reason=idle_timeout` — this means not all expected images arrived within `ANPR_EVENT_IDLE_TIMEOUT` seconds. If this happens often, raise `ANPR_EVENT_IDLE_TIMEOUT` and/or `ANPR_LATE_ATTACH_WINDOW` in `.env`.
- Check `xml_temp/<Camera>/orphans/` for images that arrived too late (outside the late-attach window) to be reunited with their event.

**`ORPHAN IMAGE | no UUID/pId match found` appearing repeatedly**
- This means a JPEG's declared `pId` never matched any event, even during its grace period. A low, occasional rate is expected (camera timing edge cases); a high or growing rate usually means `ANPR_LATE_ATTACH_WINDOW` is shorter than your camera's actual worst-case image latency — check the timestamps in `logs/<name>_listener.log` between an event's `EVENT PARTIAL` line and the corresponding late `IMAGE RECEIVED`/orphan line to measure the real gap.

**XML parsing failed**
- Logged as `Malformed XML part ignored: ...` and the listener continues — a single bad XML part never crashes the process or corrupts other in-flight events.

**Firewall issues**
- The listener needs outbound HTTPS access to both camera IPs; the dashboard needs inbound access on port 5020 from wherever you browse from. There is no built-in TLS on the dashboard itself — put it behind a reverse proxy if you need HTTPS to the browser.

---

## Developer Guide

**Adding another camera** — create a new thin entry point following the
exact pattern of `entry_listener.py`:

```python
from anpr_common import ANPRListener

BASE_DIR = "/home/rixos/Hikvision_ANPR"

if __name__ == "__main__":
    listener = ANPRListener(
        camera_role="Overflow",        # becomes output/Overflow, xml_temp/Overflow
        camera_env_key="CAMERA_OVERFLOW",  # new .env key you add
        base_dir=BASE_DIR,
        log_filename="overflow_listener.log",
        logger_name="hikvision-overflow",
    )
    listener.listen()
```

Add `CAMERA_OVERFLOW=<ip>` to `.env`, create a matching systemd unit, and
add `"Overflow"` alongside `"Entry"`/`"Exit"` anywhere `dashboard.py`
enumerates directions (`ENTRY_DIR`/`EXIT_DIR`, `EventIndex.scan_once()`,
the `/images/<direction>/...` route) if you want it to show up in the
dashboard too — there's currently no camera-count abstraction, direction
names are hardcoded to two.

**Changing storage** — all persistence is direct local-filesystem
read/write (`atomic_write_json`, `atomic_write_bytes` in `anpr_common.py`;
`safe_load_json`/`os.scandir` in `dashboard.py`). There is no storage
abstraction layer — swapping to S3/a database would mean modifying both
of those files directly.

**Adding a new dashboard page** — add a new `@app.route(...)` in
`dashboard.py`, reuse `index.get_sorted_events()` for data, and add a new
template under `templates/`. The existing SSE endpoint
(`/api/events/stream`) can be reused as-is for live updates on a new page
too, since it only signals "something changed" rather than carrying a
page-specific payload.

**Modifying logging** — listener logging is built once per process in
`ANPRListener._build_logger()` (a `logging.FileHandler` + a
`logging.StreamHandler`, level hardcoded to `INFO`). The dashboard uses
Flask's own `app.logger`. Neither currently supports rotation or a
configurable level — see Code Review for the specific gap.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Security

See [`SECURITY.md`](SECURITY.md) for the vulnerability reporting process
and known accepted risks.

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).

## License

All rights reserved. No open-source license is granted — this code is
public for visibility only; reuse, modification, or redistribution
requires explicit permission from the repository owner.
