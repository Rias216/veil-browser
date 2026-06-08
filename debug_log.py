"""Lightweight debug logger for the extension store / loader.

Why this exists
---------------
The user reported "installing and enabling doesn't really work and
crashes" and asked: *"make sure before each change we have something
to debug each feature new or old."*  This module is that something.

Public API
----------
    from debug_log import log
    log("install", "downloading", ext_id="abc...")
    log("enable",  "toggling",    ext_id="abc...", enabled=False)

Channels
--------
    install   — CRX download + unpack + filesystem move
    enable    — engine setExtensionEnabled()
    uninstall — engine uninstallExtension() + folder cleanup
    search    — CWS / DDG search calls
    dock      — popup dock/undock transitions

Disable with the ``DEBUG_LOG=0`` environment variable.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

_ENABLED: bool = os.environ.get("DEBUG_LOG", "1") != "0"
_COUNTER: int = 0
_LOCK = threading.Lock()


def _format(channel: str, msg: str, **fields: Any) -> str:
    """Format one log line.  Fields are appended as ``key=value``."""
    global _COUNTER
    with _LOCK:
        _COUNTER += 1
        seq = _COUNTER
    ts = time.strftime("%H:%M:%S", time.localtime()) + f".{int((time.time() % 1) * 1000):03d}"
    field_str = " ".join(f"{k}={v!r}" for k, v in fields.items())
    return f"[{ts}][{seq:04d}][{channel}] {msg}" + (f" {field_str}" if field_str else "")


def log(channel: str, msg: str, **fields: Any) -> None:
    """Print one timestamped, sequenced log line to stderr.

    Cheap enough to call on every code path entry.  No-op when
    ``DEBUG_LOG=0`` is set.
    """
    if not _ENABLED:
        return
    try:
        line = _format(channel, msg, **fields)
    except Exception as e:
        line = f"[debug_log.py: error formatting: {e}]"
    print(line, file=sys.stderr, flush=True)


def enabled() -> bool:
    return _ENABLED


def set_enabled(value: bool) -> None:
    global _ENABLED
    _ENABLED = bool(value)
