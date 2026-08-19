#!/usr/bin/env python3
"""
Shared Hikvision ANPR multipart-event-stream listener implementation.

entry_listener.py and exit_listener.py are both thin, role-specific
wrappers around ANPRListener defined here. Previously each camera had
its own ~1300 line, near-identical copy of this logic; that duplication
is gone, and the completion strategy now supports multiple in-flight
events at once (see EventCache below), instead of a single "current
event" slot.

============================================================
WHY THIS EXISTS (event cache design)
============================================================

Firmware V5.8.20 on the iDS-2CD7A46G0/P-LZHS no longer sends an ANPR
event's XML + 3 images back-to-back. The plate crop typically lands in
under a second, but the vehicle/full-scene images can legitimately take
seconds to minutes to follow - and a second vehicle's XML can arrive
before the first vehicle's images have finished. Arrival order must
never be trusted.

Two identifiers exist on the wire, and they are NOT the same thing:

  - <UUID> in the XML: identifies the EVENT. Never appears on any JPEG.
  - <pId> inside each <pictureInfo> in the XML's <pictureInfoList>:
    identifies one PICTURE belonging to that event. The multipart
    filename of the JPEG that later arrives for that picture IS this
    pId (confirmed against live capture - the raw multipart filename
    is the pId string, not "licensePlatePicture.jpg").

So "match by UUID, never by timing" in practice means: build a global
pId -> owning-event(UUID) index from every currently-open event's
declared picture plan, and route each incoming JPEG through that index
- not through a single "most recent event" pointer. That is what
EventCache does. It keeps every event that hasn't finished (or hasn't
aged out of its late-attach window) simultaneously addressable, so an
image for an older, still-open event is never misrouted to whatever
event happens to be newest, and is never silently dropped just because
a newer event's XML showed up in the meantime.
"""

import os
import re
import json
import time
import logging
import threading
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import urllib3
from requests.auth import HTTPDigestAuth


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LISTENER_VERSION = "2.0"


# ============================================================
# CONFIGURATION (shared tunables, overridable via .env)
#
# Defaults preserve the production-tuned behaviour already in place
# (90s idle / 180s max lifetime) rather than the shorter defaults
# floated during design discussion - real captured logs showed
# legitimate gaps well past 30s, and shortening the timeout only
# increases how often events save as "partial" before the late-attach
# window below reunites them anyway.
# ============================================================

def _int_env(name, default):
    value = os.getenv(name)

    if value is None or not value.strip():
        return default

    try:
        return int(value)
    except ValueError:
        logging.getLogger("anpr-common").warning(
            "Invalid integer for %s=%r, using default %s",
            name, value, default
        )
        return default


EVENT_IDLE_TIMEOUT = _int_env("ANPR_EVENT_IDLE_TIMEOUT", 90)
EVENT_MAX_LIFETIME = _int_env("ANPR_EVENT_MAX_LIFETIME", 180)
LATE_ATTACH_WINDOW = _int_env("ANPR_LATE_ATTACH_WINDOW", 60)
DEFAULT_EXPECTED_IMAGES = 3
WATCHDOG_INTERVAL = 1.0

ORPHAN_GRACE_SECONDS = _int_env("ANPR_ORPHAN_GRACE_SECONDS", 5)

# Orphan images that age out of the grace period above are persisted to
# xml_temp/<role>/orphans/ for manual review (see _persist_orphan).
# Nothing ever removed them, so that directory grew without bound.
# ORPHAN_FILE_MAX_AGE_SECONDS bounds how long a persisted orphan file is
# kept before the watchdog purges it; the purge itself only runs every
# ORPHAN_PURGE_INTERVAL_SECONDS since it's a directory scan, not worth
# doing on every 1s watchdog tick.
ORPHAN_FILE_MAX_AGE_SECONDS = _int_env(
    "ANPR_ORPHAN_FILE_MAX_AGE_SECONDS", 7 * 24 * 3600
)
ORPHAN_PURGE_INTERVAL_SECONDS = 3600

MAX_BUFFER_BYTES = 20 * 1024 * 1024

CONNECT_TIMEOUT = 10
STREAM_READ_TIMEOUT = 600

RECONNECT_DELAY_BASE = 5
RECONNECT_DELAY_MAX = 30


# ============================================================
# XML HELPERS
# ============================================================

def strip_namespace(root):
    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    return root


def get_text(root, path, default=""):
    element = root.find(path)

    if element is None or element.text is None:
        return default

    return element.text.strip()


INVALID_PLATE_VALUES = {
    "unknown", "unrecognized", "unrecognised", "none", "null",
    "n/a", "na", "no plate", "noplate", "unknownplate", "invalid",
}


def is_valid_plate(plate):
    if not plate:
        return False

    value = plate.strip()

    if not value:
        return False

    return value.lower() not in INVALID_PLATE_VALUES


# ============================================================
# PICTURE PLAN (firmware-declared image list)
#
# <picNum> declares how many jpeg parts to expect; <pictureInfoList>
# names each one (licensePlatePicture / vehiclePicture /
# detectionPicture) together with a <pId> that matches the multipart
# filename used later for that image. This is the authoritative way to
# know which image is which. If a firmware doesn't provide it, we fall
# back to positional order (plate, vehicle, full, extra_N...).
# ============================================================

PICTURE_TYPE_MAP = {
    "licenseplatepicture": "plate",
    "vehiclepicture": "vehicle",
    "detectionpicture": "full",
}

POSITIONAL_FALLBACK = ["plate", "vehicle", "full"]


def parse_picture_plan(root):
    plan = []

    for pic in root.findall("ANPR/pictureInfoList/pictureInfo"):
        pid = get_text(pic, "pId")
        raw_type = get_text(pic, "type").strip().lower()
        label = PICTURE_TYPE_MAP.get(raw_type, raw_type or "image")

        plan.append({
            "pid": pid or None,
            "type": label,
            "claimed": False,
        })

    return plan


def parse_expected_count(root, plan):
    pic_num = get_text(root, "picNum")

    if pic_num.isdigit():
        value = int(pic_num)
        if value >= 0:
            return value

    if plan:
        return len(plan)

    return DEFAULT_EXPECTED_IMAGES


# ============================================================
# TRIGGER / CAPTURE METADATA
#
# Not every firmware/event exposes the same fields. We opportunistically
# collect whichever of these known tags are present rather than assuming
# a fixed schema. TRIGGER_TAGS are the tags confirmed present on this
# camera's live ANPR payload; EXTRA_TRIGGER_TAGS covers other
# Hikvision event-schema tag names (access-control / IO-linked event
# types) that don't appear on this ANPR stream today but are checked
# for opportunistically in case a firmware update adds one.
# ============================================================

TRIGGER_TAGS = [
    ("ANPR/line", "line"),
    ("ANPR/direction", "direction"),
    ("ANPR/detectDir", "detect_direction"),
    ("ANPR/detectType", "detect_type"),
    ("ANPR/alarmDataType", "alarm_data_type"),
    ("ANPR/vehicleListName", "vehicle_list"),
    ("ANPR/CRIndex", "capture_record_index"),
    ("isDataRetransmission", "is_retransmission"),
    ("activePostCount", "active_post_count"),
]

EXTRA_TRIGGER_TAGS = [
    ("triggerInput", "trigger_input"),
    ("AlarmInput", "alarm_input"),
    ("IOInput", "io_input"),
    ("ioPortIndex", "io_port_index"),
    ("VehicleDetector", "vehicle_detector"),
    ("Loop", "loop"),
    ("ExternalInput", "external_input"),
    ("DigitalInput", "digital_input"),
    ("ManualCapture", "manual_capture"),
    ("CaptureMode", "capture_mode"),
]

# Tags that, if present with a truthy value, identify what triggered the
# capture. Checked in this order; first match wins.
TRIGGER_SOURCE_TAGS = [
    "ANPR/vehicleListName",
    "triggerInput",
    "AlarmInput",
    "IOInput",
    "VehicleDetector",
    "Loop",
    "ExternalInput",
    "DigitalInput",
    "ManualCapture",
    "CaptureMode",
]


def extract_trigger_info(root):
    trigger = {}

    for path, key in TRIGGER_TAGS + EXTRA_TRIGGER_TAGS:
        value = get_text(root, path, default=None)

        if value:
            trigger[key] = value

    source = None

    for path in TRIGGER_SOURCE_TAGS:
        value = get_text(root, path, default=None)

        if value:
            source = value
            break

    trigger["source"] = source or "Unknown"

    return trigger


# ============================================================
# PARSE ANPR XML
# ============================================================

def parse_anpr_xml(xml_bytes, camera_role, camera_ip_fallback):
    root = ET.fromstring(xml_bytes)
    root = strip_namespace(root)

    event_type = get_text(root, "eventType")

    if event_type.upper() != "ANPR":
        return None

    raw_plate = (
        get_text(root, "ANPR/licensePlate")
        or get_text(root, "licensePlate")
    )

    raw_category = (
        get_text(root, "ANPR/category")
        or get_text(root, "ANPR/plateCategory")
    )

    if is_valid_plate(raw_plate):
        plate_number = raw_plate
        category = raw_category
        full_plate = (
            f"{category} {plate_number}".strip()
            if category else plate_number
        )
        lpr_read = True
    else:
        plate_number = ""
        category = ""
        full_plate = "LPR not Read"
        lpr_read = False

    camera_ip = get_text(root, "ipAddress")
    channel_id = get_text(root, "channelID")
    channel_name = get_text(root, "channelName")
    event_time = get_text(root, "dateTime")

    country = get_text(root, "ANPR/country")
    province = get_text(root, "ANPR/province")
    area = get_text(root, "ANPR/area")
    region = get_text(root, "ANPR/region")
    confidence = get_text(root, "ANPR/confidenceLevel")
    plate_type = get_text(root, "ANPR/plateType")
    plate_color = get_text(root, "ANPR/plateColor")
    plate_size = get_text(root, "ANPR/plateSize")
    vehicle_type = get_text(root, "ANPR/vehicleType")
    vehicle_color = get_text(root, "ANPR/vehicleInfo/color")
    direction = get_text(root, "ANPR/direction")
    original_plate = get_text(root, "ANPR/originalLicensePlate")
    uuid = get_text(root, "UUID")

    plan = parse_picture_plan(root)
    expected_images = parse_expected_count(root, plan)
    trigger = extract_trigger_info(root)

    return {
        "event_type": event_type,
        "event_time": event_time,

        "camera": {
            "location": camera_role,
            "name": channel_name,
            "ip": camera_ip or camera_ip_fallback,
            "channel": channel_id,
        },

        "anpr": {
            "lpr_read": lpr_read,
            "plate_number": plate_number,
            "category": category,
            "full_plate": full_plate,
            "country": country,
            "province": province,
            "area": area,
            "region": region,
            "confidence": confidence,
            "plate_type": plate_type,
            "plate_color": plate_color,
            "plate_size": plate_size,
            "vehicle_type": vehicle_type,
            "vehicle_color": vehicle_color,
            "direction": direction,
            "original_license_plate": original_plate,
        },

        "uuid": uuid,
        "trigger": trigger,

        # Internal-only, popped off before this dict is ever written to
        # disk - see EventCache.handle_xml().
        "_picture_plan": plan,
        "_expected_images": expected_images,
    }


# ============================================================
# FILE NAME HELPERS
# ============================================================

def safe_filename(value):
    if not value:
        return "UNKNOWN"

    value = value.strip()

    return re.sub(r"[^A-Za-z0-9_-]", "_", value)


def create_event_basename(event):
    event_time = event.get("event_time", "")

    try:
        dt = datetime.fromisoformat(event_time)
        timestamp = dt.strftime("%Y%m%d_%H%M%S_%f")[:-3]
    except Exception:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]

    anpr = event["anpr"]

    if anpr["lpr_read"]:
        category = safe_filename(anpr.get("category"))
        plate = safe_filename(anpr.get("plate_number"))

        if category and category != "UNKNOWN":
            return f"{timestamp}_{category}_{plate}"

        return f"{timestamp}_{plate}"

    return f"{timestamp}_LPR_NOT_READ"


# ============================================================
# MULTIPART HELPERS
# ============================================================

def get_boundary(response):
    content_type = response.headers.get("Content-Type", "")

    match = re.search(r'boundary="?([^";]+)', content_type, re.IGNORECASE)

    if match:
        return match.group(1).encode()

    return b"boundary"


def parse_headers(header_bytes):
    headers = {}
    text = header_bytes.decode("latin1", errors="ignore")

    for line in text.splitlines():
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    return headers


def extract_filename(headers):
    disposition = headers.get("content-disposition", "")

    match = re.search(r'filename="([^"]+)"', disposition, re.IGNORECASE)

    if match:
        return match.group(1)

    # Some firmware only sets name=, not filename= - still useful as a
    # classification hint.
    name_match = re.search(r'name="([^"]+)"', disposition, re.IGNORECASE)

    if name_match:
        return name_match.group(1)

    return None


def is_xml_part(content_type, filename):
    if "xml" in content_type:
        return True

    if filename and filename.lower().endswith(".xml"):
        return True

    return False


def is_jpeg_part(content_type, filename):
    if "jpeg" in content_type or "jpg" in content_type:
        return True

    if filename and filename.lower().endswith((".jpg", ".jpeg")):
        return True

    return False


def filename_stem(filename):
    if not filename:
        return None

    return filename.rsplit(".", 1)[0] if "." in filename else filename


# ============================================================
# ATOMIC WRITE HELPERS
#
# write() + close() is not atomic: the dashboard reads output/*.json
# continuously, so a read that lands mid-write can see a truncated
# file, and a crash or full disk mid-write can leave a permanently
# corrupt file behind. Writing to a temp file in the same directory
# and renaming it into place is atomic on the same filesystem - a
# reader only ever sees the old file or the fully-written new one.
# ============================================================

def atomic_write_bytes(path, data):
    tmp_path = f"{path}.tmp"

    with open(tmp_path, "wb") as f:
        f.write(data)

    os.replace(tmp_path, path)


def atomic_write_json(path, obj):
    tmp_path = f"{path}.tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=4, ensure_ascii=False)

    os.replace(tmp_path, path)


# ============================================================
# PICTURE CLASSIFICATION
# ============================================================

def match_plan_by_pid(plan, stem):
    """Unclaimed plan entry whose declared pId matches this jpeg's
    filename stem. Returns the entry, or None."""

    if not stem:
        return None

    for entry in plan:
        if not entry["claimed"] and entry["pid"] == stem:
            return entry

    return None


def match_plan_next(plan):
    """Next unclaimed plan entry, in the order the camera listed them.
    Used when a pId doesn't match anything (firmware quirk / retry)."""

    for entry in plan:
        if not entry["claimed"]:
            return entry

    return None


def classify_positional(existing_image_count):
    if existing_image_count < len(POSITIONAL_FALLBACK):
        return POSITIONAL_FALLBACK[existing_image_count]

    return f"extra_{existing_image_count + 1}"


# ============================================================
# PENDING / SAVED EVENT STATE
# ============================================================

class PendingEvent:
    """An event whose XML has arrived and is still waiting on images."""

    def __init__(self, cache_key, xml_bytes, parsed, plan, expected_images):
        self.cache_key = cache_key
        self.xml_bytes = xml_bytes
        self.parsed = parsed
        self.plan = plan
        self.expected_images = expected_images
        self.images = []  # list of (type, data)
        self.created_at = time.monotonic()
        self.last_activity = self.created_at

    def add_image(self, image_type, data):
        self.images.append((image_type, data))
        self.last_activity = time.monotonic()

    def is_complete(self):
        return len(self.images) >= self.expected_images

    def idle_seconds(self, now=None):
        return (now or time.monotonic()) - self.last_activity

    def age_seconds(self, now=None):
        return (now or time.monotonic()) - self.created_at


class SavedEventInfo:
    """A just-saved event, kept alive briefly so a late image (whose
    pId was declared in this event's own XML) can still be reunited
    with it instead of becoming an orphan."""

    def __init__(self, cache_key, event, plan, expected_images,
                 images_out, json_path, output_dir, saved_at):
        self.cache_key = cache_key
        self.event = event                # full dict, as written to disk
        self.plan = plan                  # shared with the PendingEvent
        self.expected_images = expected_images
        self.images_out = images_out      # type -> filename, mutated in place
        self.json_path = json_path
        self.output_dir = output_dir
        self.saved_at = saved_at

    def age_seconds(self, now=None):
        return (now or time.monotonic()) - self.saved_at


# ============================================================
# ORPHAN IMAGE HANDLING
#
# A jpeg whose pId doesn't match any pending or recently-saved event
# is held briefly in case its owning XML is about to arrive (images
# have been observed to edge slightly ahead of their XML on the wire).
# Anything still unclaimed after the grace period is written to disk
# for manual review rather than lost.
# ============================================================

class Orphan:
    __slots__ = ("filename", "data", "arrived_at")

    def __init__(self, filename, data):
        self.filename = filename
        self.data = data
        self.arrived_at = time.monotonic()


# ============================================================
# EVENT CACHE
#
# Replaces the old single "current event" slot. Every event that has
# an open XML (in `pending`) or was saved within the last
# LATE_ATTACH_WINDOW seconds (in `recent_saved`) is independently
# addressable, keyed by its own UUID (or a synthesised key if a
# firmware event is ever missing one - never silently dropped).
# `pid_index` maps a picture's declared pId directly to the owning
# event's key, which is how an incoming jpeg is routed regardless of
# how many other events have opened or closed in the meantime.
# ============================================================

class EventCache:

    def __init__(self, camera_role, camera_ip_fallback, output_dir,
                 xml_temp_dir, orphan_dir, listener_name, logger):
        self.camera_role = camera_role
        self.camera_ip_fallback = camera_ip_fallback
        self.output_dir = output_dir
        self.xml_temp_dir = xml_temp_dir
        self.orphan_dir = orphan_dir
        self.listener_name = listener_name
        self.logger = logger

        self.lock = threading.Lock()
        self.pending = {}        # cache_key -> PendingEvent
        self.recent_saved = {}   # cache_key -> SavedEventInfo
        self.pid_index = {}      # pid -> cache_key
        self.orphans = []        # list[Orphan]
        self.stop_event = threading.Event()

        self._unnamed_counter = 0
        self._last_orphan_purge = 0.0

    # --------------------------------------------------------
    # XML arrival
    # --------------------------------------------------------

    def handle_xml(self, xml_bytes):
        try:
            event = parse_anpr_xml(
                xml_bytes, self.camera_role, self.camera_ip_fallback
            )
        except ET.ParseError as exc:
            self.logger.warning("Malformed XML part ignored: %s", exc)
            return
        except Exception:
            self.logger.exception("Unexpected error parsing XML part")
            return

        if event is None:
            # Non-ANPR event (heartbeat / other event type sharing the
            # same stream). Not an event boundary of any kind - it must
            # never affect any other event's state.
            return

        plan = event.pop("_picture_plan")
        expected_images = event.pop("_expected_images")

        uuid = event.get("uuid") or ""
        cache_key = uuid

        immediate_save = None

        with self.lock:
            if not cache_key:
                self._unnamed_counter += 1
                cache_key = f"_nouuid_{int(time.time())}_{self._unnamed_counter}"
                self.logger.warning(
                    "ANPR event has no UUID - using internal key %s "
                    "so it is never dropped.",
                    cache_key
                )

            if cache_key in self.pending:
                self.logger.warning(
                    "Duplicate XML for uuid=%s ignored (already pending) "
                    "- a retransmitted XML must never silently replace an "
                    "event whose images may already be arriving.",
                    cache_key
                )
                return

            if cache_key in self.recent_saved:
                self.logger.warning(
                    "Duplicate XML for uuid=%s ignored (already saved, "
                    "still in its late-attach window) - a retransmitted "
                    "XML must never spawn a second event for the same "
                    "UUID.",
                    cache_key
                )
                return

            pending = PendingEvent(cache_key, xml_bytes, event, plan, expected_images)
            self.pending[cache_key] = pending

            for entry in plan:
                if entry["pid"]:
                    self.pid_index[entry["pid"]] = cache_key

            self._claim_orphans_locked(pending)

            if pending.is_complete():
                # picNum == 0 (or an empty plan claimed nothing to
                # expect) - nothing to wait for.
                del self.pending[cache_key]
                immediate_save = pending

        self.logger.info(
            "EVENT CREATED | uuid=%s | trigger=%s | plate=%s | "
            "confidence=%s | expected_images=%d",
            uuid or cache_key,
            event["trigger"].get("source", "Unknown"),
            event["anpr"]["full_plate"],
            event["anpr"]["confidence"],
            expected_images
        )

        if immediate_save is not None:
            self._save_event(immediate_save, "no_images_expected")

    def _claim_orphans_locked(self, pending):
        """Must be called with self.lock held. Reunite any buffered
        orphan images whose pId is now known (this event's XML just
        declared it)."""

        if not self.orphans:
            return

        remaining = []

        for orphan in self.orphans:
            stem = filename_stem(orphan.filename)
            owner = self.pid_index.get(stem) if stem else None

            if owner == pending.cache_key:
                entry = match_plan_by_pid(pending.plan, stem) or match_plan_next(pending.plan)

                if entry is not None:
                    entry["claimed"] = True
                    image_type = entry["type"]
                else:
                    image_type = classify_positional(len(pending.images))

                pending.add_image(image_type, orphan.data)

                self.logger.info(
                    "Orphan image claimed by its event | uuid=%s | type=%s",
                    pending.parsed.get("uuid") or pending.cache_key,
                    image_type
                )
            else:
                remaining.append(orphan)

        self.orphans = remaining

    # --------------------------------------------------------
    # JPEG arrival
    # --------------------------------------------------------

    def handle_jpeg(self, filename, body):
        stem = filename_stem(filename)

        to_save = None
        to_late_attach = None
        to_orphan_buffer = False
        image_type = "unknown"

        with self.lock:
            owner_key = self.pid_index.get(stem) if stem else None

            if owner_key is not None and owner_key in self.pending:
                pending = self.pending[owner_key]
                entry = match_plan_by_pid(pending.plan, stem) or match_plan_next(pending.plan)

                if entry is not None:
                    entry["claimed"] = True
                    image_type = entry["type"]
                else:
                    image_type = classify_positional(len(pending.images))

                pending.add_image(image_type, body)

                if pending.is_complete():
                    del self.pending[owner_key]
                    to_save = pending

            elif owner_key is not None and owner_key in self.recent_saved:
                saved = self.recent_saved[owner_key]
                entry = match_plan_by_pid(saved.plan, stem) or match_plan_next(saved.plan)

                if entry is not None:
                    entry["claimed"] = True
                    image_type = entry["type"]
                else:
                    image_type = classify_positional(len(saved.images_out))

                to_late_attach = (saved, image_type, body)

            else:
                # No pId match at all. Fall back to the most recently
                # opened pending event that declared no picture plan
                # (older/other firmware without pId support) - mirrors
                # the previous single-slot behaviour for that case,
                # without disturbing any pending event that DID declare
                # a plan (and is therefore routed correctly above).
                fallback = self._pick_planless_pending_locked()

                if fallback is not None:
                    image_type = classify_positional(len(fallback.images))
                    fallback.add_image(image_type, body)

                    if fallback.is_complete():
                        del self.pending[fallback.cache_key]
                        to_save = fallback
                else:
                    to_orphan_buffer = True

        if to_orphan_buffer:
            with self.lock:
                self.orphans.append(Orphan(filename, body))

            self.logger.warning(
                "IMAGE RECEIVED | %s | no matching event (pId unknown) - "
                "buffered %ds pending grace period",
                filename or "unknown",
                ORPHAN_GRACE_SECONDS
            )
            return

        self.logger.info(
            "IMAGE RECEIVED | %s | type=%s | %d bytes",
            filename or "unknown",
            image_type,
            len(body)
        )

        if to_save is not None:
            self._save_event(to_save, "images_complete")

        if to_late_attach is not None:
            saved, img_type, data = to_late_attach
            self._late_attach(saved, img_type, data)

    def _pick_planless_pending_locked(self):
        """Must be called with self.lock held."""

        candidates = [
            p for p in self.pending.values()
            if not p.plan and not p.is_complete()
        ]

        if not candidates:
            return None

        return max(candidates, key=lambda p: p.created_at)

    # --------------------------------------------------------
    # Save / late attach
    # --------------------------------------------------------

    def _save_event(self, pending, reason):
        try:
            event = pending.parsed
            anpr = event["anpr"]
            uuid = event.get("uuid") or pending.cache_key

            base_name = create_event_basename(event)

            self.logger.info(
                "ANPR EVENT PROCESSING | uuid=%s | %s | confidence=%s | "
                "LPR=%s | reason=%s",
                uuid, anpr["full_plate"], anpr["confidence"],
                anpr["lpr_read"], reason
            )

            xml_path = os.path.join(self.xml_temp_dir, f"{base_name}.xml")
            atomic_write_bytes(xml_path, pending.xml_bytes)

            image_paths = {}

            for image_type, image_data in pending.images:
                key = image_type
                suffix = 1

                while key in image_paths:
                    suffix += 1
                    key = f"{image_type}_{suffix}"

                filename = f"{base_name}_{key}.jpg"
                filepath = os.path.join(self.output_dir, filename)

                with open(filepath, "wb") as f:
                    f.write(image_data)

                image_paths[key] = filename

                self.logger.info(
                    "Saved %s image: %s (%d bytes)",
                    key, filename, len(image_data)
                )

            images_out = {"plate": None, "vehicle": None, "full": None}
            images_out.update(image_paths)
            event["images"] = images_out

            images_complete = len(pending.images) >= pending.expected_images

            event["capture"] = {
                "expected_images": pending.expected_images,
                "received_images": len(pending.images),
                "images_complete": images_complete,
                "completion_reason": reason,
                "late_attached": False,
                "last_updated": None,
            }

            event["server"] = {
                "received_time": datetime.now().astimezone().isoformat(),
                "listener": self.listener_name,
            }

            json_path = os.path.join(self.output_dir, f"{base_name}.json")
            atomic_write_json(json_path, event)

            log_fn = self.logger.info if images_complete else self.logger.warning
            marker = "EVENT COMPLETE" if images_complete else "EVENT PARTIAL"

            log_fn(
                "%s | uuid=%s | %s | %s | confidence=%s | images=%d/%d | "
                "reason=%s | saved with %d image(s)",
                marker, uuid, self.camera_role, anpr["full_plate"],
                anpr["confidence"], len(pending.images),
                pending.expected_images, reason, len(pending.images)
            )

            if not images_complete:
                # Partial save - keep this event addressable for the
                # late-attach window. If it declared a picture plan,
                # re-affirm the still-open pId(s) in the index (a no-op
                # if handle_xml already registered them, which it did -
                # this only matters for the picNum==0-then-grew edge
                # case); if it never declared one, the planless-fallback
                # path in handle_jpeg() is still the way a late image
                # can find it.
                with self.lock:
                    for entry in pending.plan:
                        if entry["pid"]:
                            self.pid_index[entry["pid"]] = pending.cache_key

                    self.recent_saved[pending.cache_key] = SavedEventInfo(
                        cache_key=pending.cache_key,
                        event=event,
                        plan=pending.plan,
                        expected_images=pending.expected_images,
                        images_out=images_out,
                        json_path=json_path,
                        output_dir=self.output_dir,
                        saved_at=time.monotonic(),
                    )

        except Exception:
            self.logger.exception("Failed processing ANPR event")

    def _late_attach(self, saved, image_type, image_data):
        try:
            key = image_type
            suffix = 1

            while saved.images_out.get(key):
                suffix += 1
                key = f"{image_type}_{suffix}"

            filename = f"{create_event_basename(saved.event)}_{key}.jpg"
            filepath = os.path.join(saved.output_dir, filename)

            with open(filepath, "wb") as f:
                f.write(image_data)

            saved.images_out[key] = filename
            saved.event["images"] = dict(saved.images_out)

            capture = saved.event.setdefault("capture", {})
            capture["received_images"] = capture.get("received_images", 0) + 1
            capture["images_complete"] = (
                capture["received_images"] >= saved.expected_images
            )
            capture["late_attached"] = True
            capture["last_updated"] = datetime.now().astimezone().isoformat()

            atomic_write_json(saved.json_path, saved.event)

            uuid = saved.event.get("uuid") or saved.cache_key

            self.logger.info(
                "EVENT UPDATED | uuid=%s | late image attached | type=%s | "
                "now %d/%d images | %s",
                uuid, key, capture["received_images"],
                saved.expected_images, filename
            )

            if capture["images_complete"]:
                self.logger.info(
                    "EVENT COMPLETE | uuid=%s | completed via late-attached "
                    "image | %s",
                    uuid, filename
                )

        except Exception:
            self.logger.exception("Failed attaching late image")

    # --------------------------------------------------------
    # Watchdog - timeout-based completion + cache pruning
    # --------------------------------------------------------

    def watchdog_tick(self):
        now = time.monotonic()
        to_flush = []
        expired_saved_keys = []
        expired_orphans = []

        with self.lock:
            for key, pending in list(self.pending.items()):
                if pending.age_seconds(now) >= EVENT_MAX_LIFETIME:
                    to_flush.append((pending, "max_lifetime"))
                    del self.pending[key]
                elif pending.idle_seconds(now) >= EVENT_IDLE_TIMEOUT:
                    to_flush.append((pending, "idle_timeout"))
                    del self.pending[key]

            for key, saved in list(self.recent_saved.items()):
                if saved.age_seconds(now) >= LATE_ATTACH_WINDOW:
                    expired_saved_keys.append(key)
                    del self.recent_saved[key]

                    for entry in saved.plan:
                        if entry["pid"] and self.pid_index.get(entry["pid"]) == key:
                            del self.pid_index[entry["pid"]]

            remaining_orphans = []

            for orphan in self.orphans:
                if now - orphan.arrived_at > ORPHAN_GRACE_SECONDS:
                    expired_orphans.append(orphan)
                else:
                    remaining_orphans.append(orphan)

            self.orphans = remaining_orphans

        for pending, reason in to_flush:
            self._save_event(pending, reason)

        for key in expired_saved_keys:
            self.logger.debug(
                "Late-attach window expired for uuid=%s - no longer "
                "tracked for late images.",
                key
            )

        for orphan in expired_orphans:
            self._persist_orphan(orphan)

        if now - self._last_orphan_purge >= ORPHAN_PURGE_INTERVAL_SECONDS:
            self._last_orphan_purge = now
            self._purge_old_orphan_files()

    def _purge_old_orphan_files(self):
        cutoff = time.time() - ORPHAN_FILE_MAX_AGE_SECONDS
        purged = 0

        try:
            with os.scandir(self.orphan_dir) as it:
                for entry in it:
                    try:
                        if not entry.is_file():
                            continue
                        if entry.stat().st_mtime < cutoff:
                            os.remove(entry.path)
                            purged += 1
                    except OSError:
                        continue
        except FileNotFoundError:
            return
        except Exception:
            self.logger.exception("Failed purging old orphan images")
            return

        if purged:
            self.logger.info(
                "Purged %d stale orphan image(s) older than %ds from %s",
                purged, ORPHAN_FILE_MAX_AGE_SECONDS, self.orphan_dir
            )

    def _persist_orphan(self, orphan):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"{timestamp}_orphan.jpg"
        path = os.path.join(self.orphan_dir, filename)

        try:
            with open(path, "wb") as f:
                f.write(orphan.data)

            self.logger.warning(
                "ORPHAN IMAGE | no UUID/pId match found | persisted for "
                "review: %s (original filename=%s, %d bytes)",
                path, orphan.filename or "unknown", len(orphan.data)
            )
        except Exception:
            self.logger.exception("Failed to persist orphan image")

    # --------------------------------------------------------
    # Shutdown
    # --------------------------------------------------------

    def flush_all(self, reason):
        with self.lock:
            leftover = list(self.pending.values())
            self.pending.clear()
            leftover_orphans = self.orphans
            self.orphans = []

        for pending in leftover:
            self._save_event(pending, reason)

        for orphan in leftover_orphans:
            self._persist_orphan(orphan)


# ============================================================
# STREAM LISTENER
# ============================================================

class ANPRListener:

    def __init__(self, camera_role, camera_env_key, base_dir,
                 log_filename, logger_name):
        from dotenv import load_dotenv

        self.camera_role = camera_role
        self.base_dir = base_dir

        self.output_dir = os.path.join(base_dir, "output", camera_role)
        self.log_dir = os.path.join(base_dir, "logs")
        self.xml_temp_dir = os.path.join(base_dir, "xml_temp", camera_role)
        self.orphan_dir = os.path.join(self.xml_temp_dir, "orphans")

        for directory in (self.output_dir, self.log_dir, self.xml_temp_dir, self.orphan_dir):
            os.makedirs(directory, exist_ok=True)

        load_dotenv(os.path.join(base_dir, ".env"))

        self.camera_ip = os.getenv(camera_env_key)
        self.camera_user = os.getenv("CAMERA_USER")
        self.camera_pass = os.getenv("CAMERA_PASS")

        if not self.camera_ip:
            raise RuntimeError(f"{camera_env_key} is missing from .env")
        if not self.camera_user:
            raise RuntimeError("CAMERA_USER is missing from .env")
        if not self.camera_pass:
            raise RuntimeError("CAMERA_PASS is missing from .env")

        self.event_url = (
            f"https://{self.camera_ip}/ISAPI/Event/notification/alertStream"
        )
        self.auth = HTTPDigestAuth(self.camera_user, self.camera_pass)

        self.logger = self._build_logger(logger_name, log_filename)

        self.cache = EventCache(
            camera_role=camera_role,
            camera_ip_fallback=self.camera_ip,
            output_dir=self.output_dir,
            xml_temp_dir=self.xml_temp_dir,
            orphan_dir=self.orphan_dir,
            listener_name=logger_name,
            logger=self.logger,
        )

    def _build_logger(self, logger_name, log_filename):
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)

        if logger.handlers:
            return logger

        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

        file_handler = logging.FileHandler(os.path.join(self.log_dir, log_filename))
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.propagate = False

        return logger

    # --------------------------------------------------------
    # Outer reconnect loop
    # --------------------------------------------------------

    def listen(self):
        delay = RECONNECT_DELAY_BASE

        while True:
            started = time.monotonic()

            try:
                self._run_stream()
            except KeyboardInterrupt:
                self.logger.info("%s listener stopped manually.", self.camera_role)
                break

            connected_for = time.monotonic() - started

            if connected_for >= RECONNECT_DELAY_MAX:
                delay = RECONNECT_DELAY_BASE
            else:
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

            self.logger.info("Reconnecting in %ss...", delay)
            time.sleep(delay)

    def _run_stream(self):
        watchdog_stop = threading.Event()

        def watchdog_loop():
            while not watchdog_stop.wait(WATCHDOG_INTERVAL):
                try:
                    self.cache.watchdog_tick()
                except Exception:
                    # This thread is the only thing enforcing completion
                    # timeouts and late-attach expiry. If it died
                    # silently, the whole timeout/late-attach mechanism
                    # would stop working for the rest of this connection
                    # with no visible error anywhere. Catch and continue.
                    self.logger.exception("Watchdog loop error - continuing")

        watchdog = threading.Thread(
            target=watchdog_loop, daemon=True,
            name=f"{self.camera_role}-watchdog"
        )
        watchdog.start()

        response = None

        try:
            self.logger.info(
                "Listener v%s connecting to %s camera %s",
                LISTENER_VERSION, self.camera_role, self.camera_ip
            )

            response = requests.get(
                self.event_url,
                auth=self.auth,
                stream=True,
                verify=False,
                timeout=(CONNECT_TIMEOUT, STREAM_READ_TIMEOUT),
                allow_redirects=True,
            )

            self.logger.info("Camera HTTP status: %s", response.status_code)
            response.raise_for_status()

            boundary = get_boundary(response)
            self.logger.info(
                "Connected. Multipart boundary: %s",
                boundary.decode("latin1", errors="ignore")
            )

            self._parse_multipart(response, boundary)

        except requests.exceptions.ReadTimeout:
            self.logger.warning(
                "No data received from %s camera for %ss - treating "
                "connection as stalled and reconnecting.",
                self.camera_role, STREAM_READ_TIMEOUT
            )

        except KeyboardInterrupt:
            raise

        except Exception:
            self.logger.exception("Camera connection error")

        finally:
            watchdog_stop.set()
            watchdog.join(timeout=WATCHDOG_INTERVAL * 2)

            if watchdog.is_alive():
                self.logger.warning(
                    "Watchdog thread did not stop within %ss",
                    WATCHDOG_INTERVAL * 2
                )

            self.cache.flush_all("stream_ended")

            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def _parse_multipart(self, response, boundary):
        delimiter = b"--" + boundary
        buffer = b""

        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue

            buffer += chunk

            if len(buffer) > MAX_BUFFER_BYTES:
                self.logger.warning(
                    "Multipart buffer exceeded %d bytes without finding "
                    "a boundary - resetting to avoid unbounded memory "
                    "growth.",
                    MAX_BUFFER_BYTES
                )
                buffer = b""
                continue

            while True:
                first = buffer.find(delimiter)

                if first == -1:
                    break

                second = buffer.find(delimiter, first + len(delimiter))

                if second == -1:
                    break

                part = buffer[first + len(delimiter):second]
                buffer = buffer[second:]
                part = part.strip(b"\r\n")

                if not part:
                    continue

                header_end = part.find(b"\r\n\r\n")
                separator_size = 4

                if header_end == -1:
                    header_end = part.find(b"\n\n")
                    separator_size = 2

                if header_end == -1:
                    self.logger.warning(
                        "Multipart part with no header/body separator "
                        "ignored (%d bytes)",
                        len(part)
                    )
                    continue

                header_bytes = part[:header_end]
                body = part[header_end + separator_size:]
                body = body.rstrip(b"\r\n")

                headers = parse_headers(header_bytes)
                content_type = headers.get("content-type", "").lower()
                filename = extract_filename(headers)

                if is_xml_part(content_type, filename):
                    self.cache.handle_xml(body)
                elif is_jpeg_part(content_type, filename):
                    self.cache.handle_jpeg(filename, body)
                else:
                    self.logger.debug(
                        "Ignoring multipart part with unrecognised "
                        "content-type=%r filename=%r (%d bytes)",
                        content_type, filename, len(body)
                    )
