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

from debug_log import log as _dl_log

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
        _dl_log("install", "_do_install:enter",
                supported=self._supported, has_profile=self.profile is not None)
        if self.profile is None:
            self.status_changed.emit("No profile \u2014 cannot install extensions")
            return

        if not self._supported:
            self.status_changed.emit(self._reason)
            return

        try:
            mgr = self.profile.extensionManager()
        except Exception as e:
            _dl_log("install", "_do_install:extensionManager-failed",
                    err=f"{type(e).__name__}: {e}")
            self.status_changed.emit(f"extensionManager() failed: {e}")
            return
        _dl_log("install", "_do_install:got-manager",
                mgr_is_none=mgr is None,
                has_loadFinished=hasattr(mgr, "loadFinished") if mgr else None)

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
        _dl_log("install", "_do_install:already-loaded",
                count=len(already_loaded))

        for entry in sorted(os.listdir(EXTENSIONS_DIR)):
            ext_path = os.path.join(EXTENSIONS_DIR, entry)
            if not os.path.isdir(ext_path):
                continue
            manifest = os.path.join(ext_path, "manifest.json")
            if not os.path.isfile(manifest):
                _dl_log("install", "_do_install:skip-no-manifest", entry=entry)
                self.status_changed.emit(f"Skipping {entry}: no manifest.json")
                continue

            if not _manifest_supports_mv3(manifest) and not _mv2_likely_works():
                _dl_log("install", "_do_install:skip-mv2", entry=entry)
                self.status_changed.emit(
                    f"Skipping {entry}: MV2 not supported on Chromium \u2265 130"
                )
                continue

            # Dedup: if any live extension already has this path,
            # skip the load.  The engine in this build creates a
            # fresh ``isInstalled: True`` copy of every loaded
            # extension on top of the ``isInstalled: False`` one
            # that points at the source path, so a single
            # ``install_all`` call can triple the extension count.
            # Path-based dedup keeps the population stable.
            ext_path_norm = os.path.normcase(os.path.normpath(ext_path))
            already_loaded_paths = set()
            for ext in (mgr.extensions() or []):
                try:
                    p = ext.path()
                except Exception:
                    p = ""
                if p:
                    already_loaded_paths.add(
                        os.path.normcase(os.path.normpath(p))
                    )
            if ext_path_norm in already_loaded_paths:
                _dl_log("install", "_do_install:skip-already-loaded",
                        entry=entry, path=ext_path)
                continue

            try:
                # In PySide6 6.10+, ``installExtension`` returns None
                # on every path we tried (C++ side returns an empty
                # ``QWebEngineExtensionInfo`` which the binding maps
                # to ``None``).  Even the pre-bundled extensions
                # (chromium-pdf, Google Hangouts) show
                # ``isInstalled: False`` in this build, confirming
                # that ``installExtension`` is effectively a no-op
                # here.  We therefore use the working async path
                # ``loadExtension`` and track the loaded extensions
                # for later ``unloadExtension`` removal.
                if hasattr(mgr, "loadExtension"):
                    if not getattr(self, "_load_finished_connected", False):
                        mgr.loadFinished.connect(self._on_load_finished)
                        self._load_finished_connected = True
                    _dl_log("install", "_do_install:loadExtension",
                            entry=entry, path=ext_path)
                    mgr.loadExtension(ext_path)
                    self.status_changed.emit(f"Loading: {entry}")
                elif hasattr(mgr, "installExtension"):
                    # Older PySide6: fall back to sync installExtension.
                    _dl_log("install", "_do_install:installExtension-fallback",
                            entry=entry, path=ext_path)
                    try:
                        info = mgr.installExtension(ext_path)
                    except Exception as e:
                        _dl_log("install",
                                "_do_install:installExtension-threw",
                                entry=entry,
                                err=f"{type(e).__name__}: {e}")
                        info = None
                    if info is not None:
                        try:
                            self._installed[info.id()] = info.name() or entry
                        except Exception:
                            pass
                        self.status_changed.emit(f"Installed: {entry}")
                else:
                    _dl_log("install", "_do_install:no-load-api", entry=entry)
                    self.status_changed.emit(
                        f"Engine has no load/install API for {entry}"
                    )
            except Exception as e:
                _dl_log("install", "_do_install:install-threw",
                        entry=entry, err=f"{type(e).__name__}: {e}")
                self.status_changed.emit(f"Install threw for {entry}: {e}")
                continue

        # Seed ``_installed`` with the already-loaded set so the
        # dialog shows the current population even before the async
        # ``loadFinished`` signals fire.
        for ext_id in already_loaded:
            if ext_id not in self._installed:
                self._installed[ext_id] = ext_id

        _dl_log("install", "_do_install:done", count=len(self._installed))
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

    def get_options_url(self, extension_id: str) -> str:
        """Return the ``chrome-extension://<id>/<page>`` URL for the
        extension's options page, or ``""`` if it has none.

        Reads the manifest from the extension's on-disk path (the
        same one ``loadExtension`` was given).  Safe to call from
        UI handlers — never raises.

        In some PySide6 builds the engine reports a cache path
        (under ``%APPDATA%/python/QtWebEngine/.../Extensions/``)
        that does NOT contain a ``manifest.json``.  In that case
        we fall back to ``./extensions/<name>/`` and look up the
        folder whose manifest declares a matching ``name``.
        """
        from extensions_popup import (
            read_extension_options_page,
            build_options_url,
        )
        if not extension_id or not isinstance(extension_id, str):
            return ""
        ext_path: str | None = None
        ext_name: str = ""
        for ext in self.live_extensions():
            try:
                if ext.id() == extension_id:
                    try:
                        ext_path = ext.path() or ""
                    except Exception:
                        ext_path = None
                    try:
                        ext_name = ext.name() or ""
                    except Exception:
                        ext_name = ""
                    break
            except Exception:
                continue
        if not ext_path:
            return ""
        # Try the engine-reported path first.
        try:
            options_path = read_extension_options_page(ext_path)
        except Exception:
            options_path = ""
        if not options_path and ext_name:
            # Engine cache path is manifest-less; fall back to the
            # source folder under ``./extensions/`` whose manifest
            # declares the same ``name`` as the live extension.
            options_path = self._find_options_path_by_name(ext_name)
        if not options_path:
            return ""
        return build_options_url(extension_id, options_path)

    def _find_options_path_by_name(self, ext_name: str) -> str:
        """Scan ``./extensions/`` for a manifest whose ``name`` field
        matches ``ext_name``.  Returns the manifest's options page
        path, or ``""`` if no match is found.  Never raises.
        """
        from extensions_popup import read_extension_options_page
        if not ext_name or not os.path.isdir(EXTENSIONS_DIR):
            return ""
        try:
            for entry in os.listdir(EXTENSIONS_DIR):
                folder = os.path.join(EXTENSIONS_DIR, entry)
                if not os.path.isdir(folder):
                    continue
                manifest = os.path.join(folder, "manifest.json")
                if not os.path.isfile(manifest):
                    continue
                try:
                    with open(manifest, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("name") == ext_name:
                    try:
                        return read_extension_options_page(folder) or ""
                    except Exception:
                        return ""
        except Exception:
            return ""
        return ""

    def live_extensions(self):
        """Return the raw ``QWebEngineExtensionInfo`` list from the
        manager (or ``[]`` if unsupported).  Callers that need
        ``id()`` / ``isEnabled()`` / ``path()`` use this directly.
        """
        if not self._supported or self.profile is None:
            return []
        try:
            mgr = self.profile.extensionManager()
            if mgr is None:
                _dl_log("enable", "live_extensions:no-manager")
                return []
            exts = list(mgr.extensions() or [])
            _dl_log("enable", "live_extensions:ok", count=len(exts))
            return exts
        except Exception as e:
            _dl_log("enable", "live_extensions:exception",
                    err=f"{type(e).__name__}: {e}")
            return []

    def uninstall(self, extension_id: str) -> bool:
        """Uninstall an extension by id and remove its folder.

        Steps:

        1. Ask the engine's extension manager to uninstall the
           extension (this is the only call Chromium actually
           respects — the on-disk folder stays otherwise).
        2. After the manager accepts, delete the folder on disk
           so a future "Reload Extensions" doesn't pick it back up.
        3. Drop the extension from the in-memory cache and notify
           listeners via :attr:`count_changed` /
           :attr:`status_changed`.

        Returns ``True`` on success, ``False`` if the id is unknown
        or the engine rejected the uninstall.  Never raises — the
        caller is a UI handler that would rather show a friendly
        error than crash the window.

        Note on the PySide6 6.10+ binding:
        ``mgr.uninstallExtension(...)`` accepts a
        :class:`QWebEngineExtensionInfo` object, **not** a string id.
        Passing a string raises ``TypeError`` (the binding has no
        overload for it).  We always look up the info object by id
        first.
        """
        _dl_log("uninstall", "uninstall:enter", ext_id=extension_id)
        if not extension_id or not isinstance(extension_id, str):
            _dl_log("uninstall", "uninstall:bad-id", ext_id=extension_id)
            self.status_changed.emit("Uninstall failed: invalid extension id")
            return False
        if not self._supported or self.profile is None:
            _dl_log("uninstall", "uninstall:unsupported",
                    supported=self._supported, has_profile=self.profile is not None)
            self.status_changed.emit(
                "Uninstall failed: extensions are not supported on this build"
            )
            return False

        try:
            mgr = self.profile.extensionManager()
        except Exception as e:
            _dl_log("uninstall", "uninstall:extensionManager-failed",
                    err=f"{type(e).__name__}: {e}")
            self.status_changed.emit(
                f"Uninstall failed: extensionManager() threw: {e}"
            )
            return False
        if mgr is None:
            _dl_log("uninstall", "uninstall:no-manager")
            self.status_changed.emit(
                "Uninstall failed: no extension manager on this profile"
            )
            return False

        # Look up the extension's info object and on-disk path
        # BEFORE we ask the engine to uninstall.  After
        # uninstallExtension() the manager may have already removed
        # it from its list, in which case ``id()`` / ``path()`` are
        # no longer reachable.
        ext_info = None
        ext_path: str | None = None
        ext_name: str = extension_id
        for ext in self.live_extensions():
            try:
                if ext.id() == extension_id:
                    ext_info = ext
                    ext_name = ext.name() or extension_id
                    try:
                        ext_path = ext.path()
                    except Exception:
                        ext_path = None
                    break
            except Exception:
                continue
        # Inspect the info object's flags so we can see WHY the
        # engine might reject the uninstall.
        info_state = {}
        if ext_info is not None:
            for attr in ("isLoaded", "isEnabled", "isInstalled"):
                try:
                    info_state[attr] = getattr(ext_info, attr)()
                except Exception as e:
                    info_state[attr] = f"err: {type(e).__name__}: {e}"
        _dl_log("uninstall", "uninstall:lookup-done",
                ext_id=extension_id, name=ext_name, path=ext_path,
                found_info=ext_info is not None, info_state=info_state)
        if ext_info is None:
            self.status_changed.emit(
                f"Uninstall failed: {ext_name} is not loaded in the engine"
            )
            return False

        # Ask the engine to remove the extension.  In this build,
        # extensions are typically ``loadExtension``-loaded, in
        # which case ``isInstalled()`` is False and
        # ``uninstallExtension`` returns False.  We therefore try
        # ``unloadExtension`` first (it accepts the same
        # ``QWebEngineExtensionInfo`` object) and fall back to
        # ``uninstallExtension`` for the rare case of a permanent
        # install.
        unloaded = False
        if hasattr(mgr, "unloadExtension"):
            try:
                mgr.unloadExtension(ext_info)
                unloaded = True
                _dl_log("uninstall", "uninstall:unloadExtension-ok",
                        ext_id=extension_id)
            except Exception as e:
                _dl_log("uninstall", "uninstall:unloadExtension-threw",
                        err=f"{type(e).__name__}: {e}")
        if not unloaded and hasattr(mgr, "uninstallExtension"):
            try:
                ok = bool(mgr.uninstallExtension(ext_info))
                _dl_log("uninstall", "uninstall:uninstallExtension-result",
                        ok=ok)
                if not ok:
                    self.status_changed.emit(
                        f"Uninstall rejected by engine for {ext_name}"
                    )
                    return False
                unloaded = True
            except Exception as e:
                _dl_log("uninstall", "uninstall:engine-threw",
                        err=f"{type(e).__name__}: {e}")
                self.status_changed.emit(
                    f"Uninstall failed for {ext_name}: {type(e).__name__}: {e}"
                )
                return False
        if not unloaded:
            self.status_changed.emit(
                f"Uninstall failed: engine has no unload/uninstall API"
            )
            return False

        # Then: best-effort folder cleanup.  This is what makes the
        # uninstall stick across restarts — without it, "Reload
        # Extensions" would re-install from the same folder.
        if ext_path and os.path.isdir(ext_path):
            try:
                shutil.rmtree(ext_path, ignore_errors=False)
                _dl_log("uninstall", "uninstall:folder-removed", path=ext_path)
            except Exception as e:
                _dl_log("uninstall", "uninstall:folder-remove-failed",
                        err=f"{type(e).__name__}: {e}")
                # The engine uninstall succeeded; the folder cleanup
                # is best-effort.  Surface the warning so the user
                # can manually remove the folder if they care.
                self.status_changed.emit(
                    f"Uninstalled {ext_name} from engine, but could not "
                    f"remove folder: {e}"
                )
        else:
            # No path known — try the standard ``extensions/<id>``
            # location as a final fallback.
            candidate = os.path.join(EXTENSIONS_DIR, extension_id)
            if os.path.isdir(candidate):
                try:
                    shutil.rmtree(candidate, ignore_errors=False)
                    _dl_log("uninstall", "uninstall:folder-removed-fallback",
                            path=candidate)
                except Exception as e:
                    _dl_log("uninstall", "uninstall:folder-remove-failed-fallback",
                            err=f"{type(e).__name__}: {e}")
                    self.status_changed.emit(
                        f"Uninstalled {ext_name} from engine, but could "
                        f"not remove folder: {e}"
                    )

        # Drop from the in-memory cache and notify.
        self._installed.pop(extension_id, None)
        self.status_changed.emit(f"Uninstalled {ext_name}")
        self.count_changed.emit(len(self._installed))
        return True

    def set_enabled(self, extension_id: str, enabled: bool) -> bool:
        """Enable or disable an extension at runtime.

        Returns ``True`` on success, ``False`` if the engine rejected
        the change.  No-op (returns ``True``) if the extension is
        already in the requested state.

        Note on the PySide6 6.10+ binding:
        ``mgr.setExtensionEnabled(...)`` accepts a
        :class:`QWebEngineExtensionInfo` object, **not** a string id.
        We always look up the info object by id first.
        """
        _dl_log("enable", "set_enabled:enter",
                ext_id=extension_id, enabled=enabled)
        if not self._supported or self.profile is None:
            _dl_log("enable", "set_enabled:unsupported",
                    supported=self._supported,
                    has_profile=self.profile is not None)
            return False
        try:
            mgr = self.profile.extensionManager()
        except Exception as e:
            _dl_log("enable", "set_enabled:extensionManager-failed",
                    err=f"{type(e).__name__}: {e}")
            return False
        if mgr is None:
            _dl_log("enable", "set_enabled:no-manager")
            return False
        # Find the info object.  ``setExtensionEnabled`` requires it
        # on PySide6 6.10+; passing a string id raises TypeError.
        ext_info = None
        try:
            for ext in (mgr.extensions() or []):
                try:
                    if ext.id() == extension_id:
                        ext_info = ext
                        break
                except Exception:
                    continue
        except Exception as e:
            _dl_log("enable", "set_enabled:list-failed",
                    err=f"{type(e).__name__}: {e}")
        if ext_info is None:
            _dl_log("enable", "set_enabled:id-not-loaded",
                    ext_id=extension_id)
            return False
        try:
            mgr.setExtensionEnabled(ext_info, bool(enabled))
            _dl_log("enable", "set_enabled:ok",
                    ext_id=extension_id, enabled=enabled)
            return True
        except Exception as e:
            _dl_log("enable", "set_enabled:engine-threw",
                    ext_id=extension_id,
                    err=f"{type(e).__name__}: {e}")
            return False

    def _on_load_finished(self, info):
        _dl_log("install", "_on_load_finished:enter", info_is_none=info is None)
        # ``info`` may be ``None`` on certain PySide6 builds when
        # the engine cannot even produce a stub.  We must never
        # let an exception out of this slot — it would propagate
        # up into the Qt event loop and crash the window.
        if info is None:
            _dl_log("install", "_on_load_finished:info-is-none")
            self.status_changed.emit("Load finished with no info object")
            return
        try:
            err = info.error() or ""
        except Exception as e:
            _dl_log("install", "_on_load_finished:error-threw",
                    err=f"{type(e).__name__}: {e}")
            err = ""
        try:
            name = info.name() or "(unknown)"
            ext_id = info.id()
        except Exception as e:
            _dl_log("install", "_on_load_finished:info-attrs-threw",
                    err=f"{type(e).__name__}: {e}")
            name, ext_id = "(unreadable)", ""
        _dl_log("install", "_on_load_finished:parsed",
                ext_id=ext_id, name=name, err=err)
        if err:
            self.status_changed.emit(f"Load error for {name}: {err}")
            return
        if not ext_id:
            self.status_changed.emit("Load finished with empty extension id")
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
