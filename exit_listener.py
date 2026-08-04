#!/usr/bin/env python3
"""
Exit camera ANPR listener. Role-specific configuration only - the
actual multipart parsing / event-cache / persistence logic lives in
anpr_common.py and is shared with entry_listener.py.
"""

from anpr_common import ANPRListener

BASE_DIR = "/home/rixos/Hikvision_ANPR"


if __name__ == "__main__":
    listener = ANPRListener(
        camera_role="Exit",
        camera_env_key="CAMERA_EXIT",
        base_dir=BASE_DIR,
        log_filename="exit_listener.log",
        logger_name="hikvision-exit",
    )
    listener.listen()
