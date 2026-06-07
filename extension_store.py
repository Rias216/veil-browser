"""Chrome Web Store integration: search and direct-install public
extensions, without using the CWS web UI and without a Google login.

Why this exists
---------------
The CWS web UI at ``chromewebstore.google.com/detail/<id>`` is gated on
the browser being a real Chrome — non-Chrome user-agents get
"Item currently unavailable. Please check the troubleshooting guide."
The search results page and the CRX download API are not gated the
same way:

  * The search page is server-rendered HTML, so it works from any
    browser. We harvest the (extension_id, name, author, rating,
    user count, description) pairs out of the result cards.
  * The CRX download endpoint
    ``https://clients2.google.com/service/update2/crx?...`` serves
    the binary for any *public* extension as long as the request
    carries a recognizable Chrome User-Agent. No login required.

Privacy-focused browsers (ungoogled-chromium forks, Brave, etc.) use
the same trick. We do the same.

Threading
---------
CRX downloads happen on a :class:`QThread` worker so the dialog
stays responsive. Progress is reported via Qt signals; the GUI never
blocks on a download.
"""

import json
import os
import re
import shutil
import struct
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO

from PySide6.QtCore import QObject, QThread, Signal

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXTENSIONS_DIR = os.path.join(SCRIPT_DIR, "extensions")

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CRX_DOWNLOAD_URL = (
    "https://clients2.google.com/service/update2/crx"
    "?acceptformat=crx3"
    "&prodversion=999.0.9999.0"
    "&x=id%3D{ext_id}%26v%3D0.0.0.0%26installsource%3Dondemand%26uc"
)
CRX_SEARCH_URL = "https://chromewebstore.google.com/search/{query}"
CRX_DETAIL_URL = "https://chromewebstore.google.com/detail/{ext_id}"


@dataclass
class ExtensionInfo:
    """Metadata for a single extension, aggregated from whatever
    CWS surfaces we can actually access.

    The fields break down into two groups by source:

    * **Populated by :class:`SearchResultParser`** from the
      server-rendered search-results HTML:
      ``extension_id``, ``name``, ``rating``, ``description``.
    * **Populated only by :meth:`ChromeWebStore.fetch_details`** when
      the full CWS detail page is accessible (real Chrome UA, or any
      future CWS redesign that stops gating it): ``author``,
      ``users``. In the googleless build the dialog never calls
      ``fetch_details`` (the page is gated), so these stay empty for
      search results. We keep them on the dataclass for callers that
      can read the detail page, and to avoid a future API break if
      the gating ever changes.
    """

    extension_id: str
    name: str = ""
    author: str = ""
    rating: float = 0.0
    users: str = ""
    description: str = ""
    store_url: str = ""

    def is_valid(self) -> bool:
        return bool(self.extension_id and self.name)


class SearchResultParser(HTMLParser):
    """Harvest ``(id, name, rating, description)`` tuples from a CWS
    search-results page.

    The CWS server-renders each result card as a ``<div>`` carrying
    ``data-item-id="<32-char-id>"``.  The parser uses several
    fallbacks because Google frequently changes class names:

    Strategy 1 — primary (works against the most recent CWS markup)
        ``<div data-item-id="<32-char-id>">`` opens a card.
        ``<a class="...a-no-hover-decoration" href=".../<id>">`` holds
        the name.  Rating is in a ``<span class="...yNns">`` and the
        description in a ``<div class="...GTRhUb">`` (or, in older
        markup, ``<p class="g3IrHd">``).

    Strategy 2 — generic fallback
        The 32-char id can also be harvested from any ``href``
        containing ``/detail/<id>``.  The name comes from the
        ``aria-label`` of the same ``<a>`` (CWS renders
        ``aria-label="<name> by <author>"``) or, failing that, from
        the link's text content.

    Strategy 3 — last-resort
        If the card structure is completely unrecognised, we scan the
        whole page for ``href="/detail/<32-id>"`` anchors and pair
        them with their ``aria-label`` / inner text.  The id regex
        is strict (lowercase 32 hex-ish letters), so false positives
        are very unlikely.
    """

    ID_RE = re.compile(r"^[a-z]{32}$")
    ID_IN_HREF_RE = re.compile(r"/detail/([a-z]{32})")
    RATING_RE = re.compile(r"\b([0-9](?:\.[0-9])?)\b")
    RATING_STAR_RE = re.compile(r"aria-label=\"([0-9](?:\.[0-9])?) out of 5\"")

    def __init__(self):
        super().__init__()
        self.results: list = []
        self._seen: set = set()
        # Current-card extraction state.
        self._current_id: str | None = None
        self._card_name: list = []
        self._card_rating: str = ""
        self._card_description: list = []
        self._card_aria: str = ""
        self._in_name = False
        self._in_rating = False
        self._in_desc = False
        self._depth = 0
        # Strategy-3 fallback scan.
        self._anchors: list = []

    # ── HTMLParser overrides ──
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)

        # ── Strategy 1: data-item-id on a div ──
        if self._current_id is None and tag == "div":
            ext_id = d.get("data-item-id", "")
            if self.ID_RE.match(ext_id) and ext_id not in self._seen:
                self._start_card(ext_id)
                # The opening <div data-item-id="..."> IS the card
                # boundary.  We set _depth to 0 so the FIRST </div>
                # we see (the one that closes this card) finalizes
                # the extraction.  Inner <div>s increment the depth.
                self._depth = 0
                return

        if self._current_id is not None:
            if tag == "div":
                self._depth += 1
            cls = d.get("class", "")
            aria = d.get("aria-label", "")

            # Name anchor
            if tag == "a" and (
                ("a-no-hover-decoration" in cls)
                or (d.get("href", "").endswith(self._current_id))
            ):
                if aria:
                    self._card_aria = aria
                self._in_name = True
            # Rating span
            elif tag == "span" and ("yNns" in cls or "Vq0ZA" in cls):
                self._in_rating = True
            # Description
            elif tag in ("div", "p") and (
                "GTRhUb" in cls or "g3IrHd" in cls or "pj4dy" in cls
            ):
                self._in_desc = True

        # ── Strategy 3: collect every detail link for the fallback pass ──
        href = d.get("href", "")
        if "/detail/" in href:
            m = self.ID_IN_HREF_RE.search(href)
            if m:
                self._anchors.append((m.group(1), d.get("aria-label", ""), ""))

    def handle_endtag(self, tag):
        if self._current_id is None:
            return
        if tag == "div":
            # Card boundary: the FIRST </div> after the opening
            # one finalizes the card.  Any </div> beyond that
            # pops a nested level.
            if self._depth == 0:
                self._finalize_card()
            else:
                self._depth -= 1
        elif tag == "p":
            if self._in_desc:
                self._in_desc = False
        elif tag == "a" and self._in_name:
            self._in_name = False
        elif tag == "span" and self._in_rating:
            self._in_rating = False

    def handle_data(self, data):
        if self._current_id is None:
            return
        if self._in_name:
            self._card_name.append(data)
        elif self._in_rating:
            self._card_rating += data
        elif self._in_desc:
            self._card_description.append(data)

    def _start_card(self, ext_id: str):
        self._current_id = ext_id
        self._card_name = []
        self._card_rating = ""
        self._card_description = []
        self._card_aria = ""
        self._in_name = False
        self._in_rating = False
        self._in_desc = False
        self._depth = 0

    def _finalize_card(self):
        ext_id = self._current_id
        if ext_id and ext_id not in self._seen:
            name = self._extract_name()
            if name:
                self._seen.add(ext_id)
                rating = self._parse_rating()
                description = self._extract_description()
                self.results.append((ext_id, name, rating, description))
        self._current_id = None
        self._card_name = []
        self._card_rating = ""
        self._card_description = []
        self._card_aria = ""
        self._in_name = False
        self._in_rating = False
        self._in_desc = False
        self._depth = 0

    def _extract_name(self) -> str:
        # Prefer aria-label "Name by Author", then collected link text.
        if self._card_aria:
            return self._card_aria.split(" by ", 1)[0].strip()
        joined = "".join(self._card_name).strip()
        return joined

    def _extract_description(self) -> str:
        raw = "".join(self._card_description)
        return " ".join(raw.split()).strip()

    def _parse_rating(self) -> float:
        raw = self._card_rating.strip()
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
        m = self.RATING_STAR_RE.search(self._card_aria)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        # Numeric anywhere in the rating block.
        m = self.RATING_RE.search(self._card_rating)
        if m:
            try:
                v = float(m.group(1))
                if 0.0 <= v <= 5.0:
                    return v
            except ValueError:
                pass
        return 0.0

    # ── Public: post-pass that applies Strategy 3 ──
    def close(self):
        super().close()
        if not self.results:
            self._fallback_from_anchors()

    def _fallback_from_anchors(self):
        """Strategy 3 — assemble results from collected ``/detail/<id>``
        links if no cards were parsed.

        The CWS occasionally obfuscates the card markup; the per-link
        ``aria-label`` is more stable.  We synthesise empty ratings
        and descriptions so the user at least gets clickable results.
        """
        for ext_id, aria, _text in self._anchors:
            if ext_id in self._seen:
                continue
            name = aria.split(" by ", 1)[0].strip() if aria else ""
            if not name:
                # Last-ditch: extract from the rest of the link HTML —
                # too complex to do correctly here, so skip.
                continue
            self._seen.add(ext_id)
            self.results.append((ext_id, name, 0.0, ""))


class CrxUnpacker:
    """Extract a normal ZIP out of a CRX2 or CRX3 archive.

    CRX3 layout::

        magic           4 bytes  "Cr24"
        version         4 bytes  0x03000000
        header_length   4 bytes  little-endian
        header          header_length bytes
        zip_payload     rest of file

    CRX2 layout::

        magic           4 bytes  "Cr24"
        version         4 bytes  0x02000000
        pubkey_length   4 bytes
        pubkey          pubkey_length bytes
        signature_length 4 bytes
        signature       signature_length bytes
        zip_payload     rest of file
    """

    MAGIC = b"Cr24"
    ZIP_SIG = b"PK\x03\x04"

    @classmethod
    def extract(cls, crx_bytes: bytes, dest_dir: str) -> int:
        if crx_bytes.startswith(cls.MAGIC):
            try:
                offset = cls._zip_start(crx_bytes)
            except (ValueError, IndexError, struct.error):
                offset = cls._find_zip_sig(crx_bytes)
        elif cls.ZIP_SIG in crx_bytes[:64]:
            offset = cls._find_zip_sig(crx_bytes)
        else:
            raise RuntimeError("Not a CRX or ZIP file")
        cls._write_zip(crx_bytes[offset:], dest_dir)
        return len(os.listdir(dest_dir))

    @classmethod
    def _zip_start(cls, crx_bytes: bytes) -> int:
        version = int.from_bytes(crx_bytes[4:8], "little")
        if version >= 3:
            header_length = int.from_bytes(crx_bytes[8:12], "little")
            return 12 + header_length
        if version == 2:
            pub_len = int.from_bytes(crx_bytes[8:12], "little")
            sig_len = int.from_bytes(
                crx_bytes[12 + pub_len: 16 + pub_len], "little"
            )
            return 16 + pub_len + sig_len
        return cls._find_zip_sig(crx_bytes)

    @classmethod
    def _find_zip_sig(cls, crx_bytes: bytes) -> int:
        i = crx_bytes.find(cls.ZIP_SIG, 0)
        if i < 0:
            raise RuntimeError("No ZIP signature found inside CRX payload")
        return i

    @staticmethod
    def _write_zip(zip_bytes: bytes, dest_dir: str) -> None:
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as z:
            dest_real = os.path.realpath(dest_dir)
            for member in z.namelist():
                target = os.path.realpath(os.path.join(dest_dir, member))
                if target != dest_real and not target.startswith(dest_real + os.sep):
                    raise RuntimeError(f"Unsafe zip entry: {member}")
            z.extractall(dest_dir)


def mv2_blocked() -> bool:
    """True if MV2 extensions will be rejected by the running engine."""
    try:
        from PySide6.QtWebEngineCore import qWebEngineChromiumVersion
        major = int(qWebEngineChromiumVersion().split(".")[0])
        return major >= 130
    except Exception:
        return False


def _read_extension_id(unpacked_dir: str) -> str:
    """Stable id used to name the install folder. Prefers the CWS
    public key (the 64-hex ``key`` field) over a name-derived slug.
    """
    try:
        with open(os.path.join(unpacked_dir, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    key = manifest.get("key", "")
    if isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key):
        return key
    name = manifest.get("name", "")
    slug = re.sub(r"[^a-z0-9]+", "", name.lower())[:32]
    if slug:
        return slug
    return os.path.basename(unpacked_dir)


def install_crx_from_path(crx_path: str,
                          extensions_dir: str = EXTENSIONS_DIR) -> str:
    """Unpack a local ``.crx`` into ``extensions_dir`` and return its id.

    Raises:
        FileNotFoundError: if ``crx_path`` does not exist.
        RuntimeError: on a malformed CRX, a path-traversal attempt inside
            the embedded ZIP, or an MV2 manifest on Chromium >= 130.
    """
    if not os.path.isfile(crx_path):
        raise FileNotFoundError(crx_path)
    with open(crx_path, "rb") as f:
        crx_bytes = f.read()
    with tempfile.TemporaryDirectory(prefix="crx_install_") as tmp:
        CrxUnpacker.extract(crx_bytes, tmp)
        manifest_path = os.path.join(tmp, "manifest.json")
        if not os.path.isfile(manifest_path):
            raise RuntimeError("manifest.json missing after unpack")
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            mv = int(manifest.get("manifest_version", 0))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Could not read manifest.json: {e}") from e
        if mv < 3 and mv2_blocked():
            raise RuntimeError(
                "This extension is MV2, which Chromium \u2265 130 rejects"
            )
        ext_id = _read_extension_id(tmp)
        if not ext_id:
            raise RuntimeError("Could not determine extension id from manifest")
        os.makedirs(extensions_dir, exist_ok=True)
        dest = os.path.join(extensions_dir, ext_id)
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        shutil.move(tmp, dest)
    return ext_id


class ChromeWebStore:
    """Direct CWS API client: search + CRX download, no login, no web UI."""

    def __init__(self, user_agent: str = CHROME_UA, timeout: float = 30.0):
        self.user_agent = user_agent
        self.timeout = timeout

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    def search(self, query: str, limit: int = 20) -> list:
        """Return a list of ``(extension_id, name, rating, description)``
        tuples harvested from the CWS search page.
        """
        if not query.strip():
            return []
        url = CRX_SEARCH_URL.format(query=urllib.parse.quote(query.strip()))
        html = self._get(url).decode("utf-8", errors="ignore")
        parser = SearchResultParser()
        parser.feed(html)
        parser.close()
        return parser.results[:limit]

    def fetch_details(self, extension_id: str) -> ExtensionInfo:
        """Fetch the CWS detail page and extract rich metadata
        (description, author, full user count).

        The CWS gates this page on the User-Agent and refuses with
        "Item currently unavailable" for non-Chrome clients. The
        googleless dialog never calls this method (it would just
        raise); it is kept public for callers running under a real
        Chrome UA, or for future CWS redesigns that stop gating.
        """
        if not re.fullmatch(r"[a-z]{32}", extension_id):
            raise ValueError(f"Invalid extension id: {extension_id!r}")
        html = self._get(CRX_DETAIL_URL.format(ext_id=extension_id)).decode(
            "utf-8", errors="ignore"
        )
        if "Item currently unavailable" in html or "troubleshooting guide" in html:
            raise RuntimeError(
                "Chrome Web Store detail page is unavailable for this browser"
            )
        return _parse_detail_html(html, extension_id)

    def download_crx(self, extension_id: str) -> bytes:
        url = CRX_DOWNLOAD_URL.format(ext_id=extension_id)
        data = self._get(url)
        if data.startswith(b"<?xml") or data.startswith(b"<gupdate"):
            m = re.search(rb'codebase="([^"]+)"', data)
            if not m:
                raise RuntimeError(
                    f"Chrome Web Store returned no download URL for {extension_id}"
                )
            crx_url = m.group(1).decode("ascii", errors="ignore")
            data = self._get(crx_url)
        if data.startswith(b"<") or data.startswith(b"<!DOCTYPE"):
            raise RuntimeError(
                "Chrome Web Store refused the download "
                "(Google may be blocking this client. Try again later.)"
            )
        if not data.startswith(CrxUnpacker.MAGIC) and CrxUnpacker.ZIP_SIG not in data[:64]:
            raise RuntimeError(
                f"CRX download for {extension_id} is not a CRX or ZIP"
            )
        return data


def _parse_detail_html(html: str, extension_id: str) -> ExtensionInfo:
    """Best-effort metadata scrape. Used when the CWS detail page is
    actually accessible (real Chrome, or when Google stops gating it)."""
    name = ""
    description = ""
    author = ""
    rating = 0.0
    users = ""
    for m in re.finditer(r'<meta\s+(?:property|name|itemprop)="([^"]+)"\s+content="([^"]*)"', html):
        key, val = m.group(1), m.group(2)
        if key == "og:title":
            name = re.sub(r"\s*-\s*Chrome Web Store.*$", "", val).strip()
        elif key == "og:description":
            description = val
        elif key == "ratingValue":
            try:
                rating = float(val)
            except (TypeError, ValueError):
                pass
        elif key == "author":
            author = val
    if not name:
        m = re.search(r"<title>([^<]+)</title>", html)
        if m:
            name = re.sub(r"\s*-\s*Chrome Web Store.*$", "", m.group(1)).strip()
    return ExtensionInfo(
        extension_id=extension_id,
        name=name,
        author=author,
        rating=rating,
        users=users,
        description=description,
        store_url=CRX_DETAIL_URL.format(ext_id=extension_id),
    )


class _InstallWorker(QObject):
    """Background worker: download CRX → save to temp → install → cleanup."""

    progress = Signal(str, int)
    finished = Signal(str, bool, str)

    def __init__(self, store: ChromeWebStore, extensions_dir: str,
                 extension_id: str):
        super().__init__()
        self.store = store
        self.extensions_dir = extensions_dir
        self.extension_id = extension_id
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        tmp_path = None
        try:
            if self._stop:
                return
            self.progress.emit(f"Downloading {self.extension_id}\u2026", 10)
            crx_bytes = self.store.download_crx(self.extension_id)
            if self._stop:
                return
            with tempfile.NamedTemporaryFile(
                suffix=".crx", delete=False, dir=tempfile.gettempdir()
            ) as tmp:
                tmp.write(crx_bytes)
                tmp_path = tmp.name
            self.progress.emit("Installing\u2026", 60)
            ext_id = install_crx_from_path(tmp_path, extensions_dir=self.extensions_dir)
            self.progress.emit("Installed", 100)
            self.finished.emit(self.extension_id, True, f"Installed {ext_id}")
        except Exception as e:
            self.finished.emit(self.extension_id, False, str(e))
        finally:
            if tmp_path is not None:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


class ExtensionStore(QObject):
    """High-level orchestrator. Owns a worker thread per install."""

    download_progress = Signal(str, int)
    install_complete = Signal(str, bool, str)

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.store = ChromeWebStore()
        self.extensions_dir = EXTENSIONS_DIR
        os.makedirs(self.extensions_dir, exist_ok=True)
        self._inflight: dict = {}

    def install_extension(self, extension_id: str) -> None:
        if extension_id in self._inflight:
            return
        thread = QThread()
        worker = _InstallWorker(
            self.store, self.extensions_dir, extension_id,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        # The worker does NOT run an event loop, so thread.quit() is a
        # no-op.  We rely on the worker's run() returning naturally,
        # which then triggers the thread's `finished` signal.  We
        # connect deleteLater via that finished signal to avoid the
        # "QThread destroyed while still running" race.
        worker.finished.connect(self._on_finished)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._inflight[extension_id] = (thread, worker)
        thread.start()

    def search(self, query: str, limit: int = 20) -> list:
        """Search the CWS for ``query`` and return up to ``limit``
        ``(extension_id, name, rating, description)`` tuples.

        Convenience wrapper so callers (e.g. the dialog) do not
        have to reach into ``self.store`` to access the underlying
        :class:`ChromeWebStore` directly.
        """
        return self.store.search(query, limit=limit)

    def cancel_all(self) -> None:
        for thread, worker in list(self._inflight.values()):
            worker.stop()
            if thread.isRunning():
                # Give the worker up to 2 s to notice _stop and exit.
                thread.wait(2000)

    def _on_progress(self, message: str, percent: int) -> None:
        self.download_progress.emit(message, percent)

    def _on_finished(self, extension_id: str, ok: bool, message: str) -> None:
        self.install_complete.emit(extension_id, ok, message)
        info = self._inflight.pop(extension_id, None)
        if info is None:
            return
        thread, _worker = info
        # The thread's `finished` signal will fire shortly, which
        # triggers deleteLater on both worker and thread.  We don't
        # need to call wait/deleteLater here — doing so caused the
        # "QThread destroyed while still running" diagnostic on
        # shutdown.

