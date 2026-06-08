"""Loader for Chromium WebExtensions, with a first-run auto-fetch of
uBlock Origin Lite (MV3) so the browser ships with a real adblocker.

Strategy
--------
On PySide6 6.10+ / Chromium 134+, MV2 extensions are rejected by the
engine, but MV3 works. uBlock Origin Lite is the only adblocker that has
a proper MV3 release. We:

  1. On first run, download uBOLite_*.chromium.zip from the official
     uBO Lite release page into ./extensions/uBOLite/ and unpack it.
  2. On every run, call `QWebEngineProfile.extensionManager().loadExtension`
     for each extension found in ./extensions/.
  3. Re-install when the user picks "Reload Extensions" from the menu.

The user can also drop additional MV3 extensions into ./extensions/.

Limitations
-----------
- Extensions do NOT load into off-the-record profiles. The default
  profile must be a NAMED, non-OTR profile for them to work.
- The default profile is OTR by default in QWebEngineProfile. We work
  around that by giving it a storage name in browser.py's main().
"""

import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTENSIONS_DIR = os.path.join(SCRIPT_DIR, "extensions")
UBO_DIR = os.path.join(EXTENSIONS_DIR, "uBOLite")

# Pin to a known-good release. The "latest" redirect works but a fixed
# version is more predictable.
UBO_LITE_VERSION = "2026.529.1448"
UBO_LITE_URL = (
    f"https://github.com/uBlockOrigin/uBOL-home/releases/download/"
    f"{UBO_LITE_VERSION}/uBOLite_{UBO_LITE_VERSION}.chromium.zip"
)


# ── Capability detection ─────────────────────────────────────────────
def _detect_extension_support():
    """Return (supported: bool, reason: str)."""
    try:
        from PySide6 import __version__ as pyside_version
    except Exception:
        pyside_version = "unknown"

    qt_version = "unknown"
    chromium_version = "unknown"
    try:
        from PySide6.QtCore import qVersion as _qVersion
        qt_version = _qVersion()
    except Exception:
        pass
    try:
        from PySide6.QtWebEngineCore import qWebEngineChromiumVersion
        chromium_version = qWebEngineChromiumVersion()
    except Exception:
        pass

    try:
        from PySide6.QtWebEngineCore import QWebEngineProfile
        has_manager = hasattr(QWebEngineProfile, "extensionManager")
    except Exception:
        has_manager = False

    if not has_manager:
        return False, (
            f"WebExtensions require PySide6 \u2265 6.10 (extensionManager API). "
            f"Detected: PySide6 {pyside_version}, Qt {qt_version}, "
            f"Chromium {chromium_version}."
        )

    return True, (
        f"WebExtensions supported (PySide6 {pyside_version}, Qt {qt_version}, "
        f"Chromium {chromium_version}). MV2 may be rejected; use MV3."
    )


# ── uBO Lite first-run download ─────────────────────────────────────
def _download_ubo_lite(on_done=None):
    """Download and unpack uBO Lite into ./extensions/uBOLite/.
    `on_done(success: bool, message: str)` is called on completion.
    """
    def _run():
        try:
            os.makedirs(UBO_DIR, exist_ok=True)
            req = urllib.request.Request(
                UBO_LITE_URL,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()

            with tempfile.NamedTemporaryFile(
                suffix=".zip", delete=False, dir=tempfile.gettempdir()
            ) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

            try:
                # Clear out any partial install
                for entry in os.listdir(UBO_DIR):
                    p = os.path.join(UBO_DIR, entry)
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        try:
                            os.remove(p)
                        except OSError:
                            pass

                with zipfile.ZipFile(tmp_path, "r") as z:
                    z.extractall(UBO_DIR)

                if not os.path.isfile(os.path.join(UBO_DIR, "manifest.json")):
                    raise RuntimeError("manifest.json missing after unpack")

                if on_done:
                    on_done(True, f"uBlock Origin Lite installed ({len(data)//1024} KB)")
                return True
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            if on_done:
                on_done(False, f"uBlock Origin Lite download failed: {e}")
            return False

    # Run on the GUI thread (small fetch, no need for a worker)
    _run()


# ── ExtensionLoader ──────────────────────────────────────────────────
class ExtensionLoader(QObject):
    """Scans ./extensions/ and installs each found WebExtension."""

    status_changed = Signal(str)
    count_changed = Signal(int)

    def __init__(self, settings, profile=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.profile = profile
        self._installed = {}  # id -> name
        self._supported, self._reason = _detect_extension_support()

    # ── Public API ───────────────────────────────────────────────────
    def install_all(self):
        if not self._supported:
            self.status_changed.emit(self._reason)
            return

        # First-run: ensure uBO Lite is on disk. If not, fetch it.
        if not os.path.isdir(UBO_DIR) or not os.path.isfile(
            os.path.join(UBO_DIR, "manifest.json")
        ):
            self.status_changed.emit("First run: fetching uBlock Origin Lite\u2026")
            _download_ubo_lite(on_done=lambda ok, msg: self._on_ubo_done(ok, msg))
            return  # The callback will re-trigger install_all()

        # Also accept any user-dropped extension in ./extensions/
        os.makedirs(EXTENSIONS_DIR, exist_ok=True)

        self._do_install()

    def installed(self):
        return dict(self._installed)

    def count(self):
        return len(self._installed)

    def support_reason(self):
        return self._reason

    def is_supported(self):
        return self._supported

    # ── Internals ────────────────────────────────────────────────────
    def _on_ubo_done(self, ok, message):
        self.status_changed.emit(message)
        if ok:
            # Kick the actual install once the zip is on disk
            QTimer.singleShot(0, self._do_install)
        else:
            self.status_changed.emit(
                "uBO Lite unavailable \u2014 falling back to host-based blocker only"
            )

    def _do_install(self):
        if self.profile is None:
            self.status_changed.emit("No profile \u2014 cannot install extensions")
            return

        if not self._supported:
            self.status_changed.emit(self._reason)
            return

        try:
            mgr = self.profile.extensionManager()
        except Exception as e:
            self.status_changed.emit(f"extensionManager() failed: {e}")
            return

        if mgr is None:
            self.status_changed.emit("No extension manager on this profile")
            return

        # Build the set of already-loaded extensions so we don't
        # double-install.  ``manager.extensions()`` returns a list of
        # ``ExtensionInfo`` (PySide6 6.10+) or, on older bindings,
        # ``None`` / an empty list.
        already_loaded = set()
        try:
            for ext in (mgr.extensions() or []):
                try:
                    already_loaded.add(ext.id())
                except Exception:
                    pass
        except Exception as e:
            self.status_changed.emit(f"manager.extensions() failed: {e}")

        for entry in sorted(os.listdir(EXTENSIONS_DIR)):
            ext_path = os.path.join(EXTENSIONS_DIR, entry)
            if not os.path.isdir(ext_path):
                continue
            manifest = os.path.join(ext_path, "manifest.json")
            if not os.path.isfile(manifest):
                self.status_changed.emit(f"Skipping {entry}: no manifest.json")
                continue

            if not _manifest_supports_mv3(manifest) and not _mv2_likely_works():
                self.status_changed.emit(
                    f"Skipping {entry}: MV2 not supported on Chromium \u2265 130"
                )
                continue

            try:
                if not hasattr(mgr, "loadFinished"):
                    info = mgr.installExtension(ext_path)
                    if info is None:
                        self.status_changed.emit(
                            f"installExtension returned None for {entry}"
                        )
                        continue
                    if info.error():
                        self.status_changed.emit(
                            f"Install error for {entry}: {info.error()}"
                        )
                        continue
                    self._installed[info.id()] = info.name() or entry
                    self.status_changed.emit(
                        f"Installed: {info.name() or entry}"
                    )
                else:
                    if not getattr(self, "_load_finished_connected", False):
                        mgr.loadFinished.connect(self._on_load_finished)
                        self._load_finished_connected = True
                    mgr.loadExtension(ext_path)
                    self.status_changed.emit(f"Loading: {entry}")
            except Exception as e:
                self.status_changed.emit(f"Install threw for {entry}: {e}")
                continue

        # Seed ``_installed`` with the already-loaded set so the
        # dialog shows the current population even before the async
        # ``loadFinished`` signals fire.
        for ext_id in already_loaded:
            if ext_id not in self._installed:
                self._installed[ext_id] = ext_id

        self.count_changed.emit(len(self._installed))

    def list_installed(self):
        """Return the live list of installed extensions from the manager.

        Falls back to the local cache populated by ``loadFinished`` if
        the manager query fails.  This is the function the dialog
        should call instead of poking at ``self._installed`` directly.
        """
        if not self._supported or self.profile is None:
            return list(self._installed.values())
        try:
            mgr = self.profile.extensionManager()
            if mgr is None:
                return list(self._installed.values())
            result = []
            for ext in (mgr.extensions() or []):
                try:
                    name = ext.name() or ext.id()
                except Exception:
                    name = ext.id() if hasattr(ext, "id") else "(unknown)"
                result.append(name)
            if result:
                return result
        except Exception:
            pass
        return list(self._installed.values())

    def _on_load_finished(self, info):
        try:
            err = info.error() or ""
        except Exception:
            err = ""
        try:
            name = info.name() or "(unknown)"
            ext_id = info.id()
        except Exception:
            name, ext_id = "(unreadable)", ""
        if err:
            self.status_changed.emit(f"Load error for {name}: {err}")
            return
        self._installed[ext_id] = name
        self.status_changed.emit(f"Loaded: {name}")
        self.count_changed.emit(len(self._installed))


# ── Helpers ─────────────────────────────────────────────────────────
def _manifest_supports_mv3(manifest_path):
    """Return True if the manifest declares manifest_version 3."""
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("manifest_version", 0)) >= 3
    except Exception:
        return False


def _mv2_likely_works():
    """True if the running Chromium still accepts MV2 (i.e. < 130)."""
    try:
        from PySide6.QtWebEngineCore import qWebEngineChromiumVersion
        major = int(qWebEngineChromiumVersion().split(".")[0])
        return major < 130
    except Exception:
        return False
