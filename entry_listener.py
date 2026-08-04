#!/usr/bin/env python3
"""
Entry camera ANPR listener. Role-specific configuration only - the
actual multipart parsing / event-cache / persistence logic lives in
anpr_common.py and is shared with exit_listener.py.
"""

from anpr_common import ANPRListener

BASE_DIR = "/home/rixos/Hikvision_ANPR"


if __name__ == "__main__":
    listener = ANPRListener(
        camera_role="Entry",
        camera_env_key="CAMERA_ENTRY",
        base_dir=BASE_DIR,
        log_filename="entry_listener.log",
        logger_name="hikvision-entry",
    )
    listener.listen()
