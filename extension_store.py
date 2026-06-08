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

from debug_log import log as _dl_log

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

# DuckDuckGo HTML endpoint — used as a fallback when the CWS search
# page returns nothing parseable.  DDG is JS-free and serves
# ``site:chromewebstore.google.com`` queries as plain HTML with
# ``uddg=https%3A%2F%2Fchromewebstore.google.com%2Fdetail%2F...``
# redirects, from which we can recover extension IDs and names.
DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_RESULT_LINK_RE = re.compile(
    r'class="result__a"\s+href="[^"]*?uddg=([^"&]+)'
)
DDG_RESULT_TITLE_RE = re.compile(
    r'class="result__a"[^>]*>(.*?)</a>', re.DOTALL
)
DDG_STRIP_TAGS_RE = re.compile(r"<[^>]+>")


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

    Modern CWS markup (verified 2026) looks like this per result card::

        <div data-item-id="<32-char-id>">
          <a class="q6LNgd" href="./detail/<slug>/<id>" aria-labelledby="i6" ...>
          <div class="R6nElb">
            <img ...>
            <div class="feN2Qe">
              <div id="i6"><h2 class="CiI2if">Extension Name</h2></div>
              <p class="rQHEi" id="i7">description text…</p>
              <p ...><span aria-label="Average rating 4.5 out of 5 stars.">
                <span class="Vq0ZA">4.5</span>…
              </span></p>
            </div>
          </div>
        </div>

    The parser walks the page once and uses three independent
    strategies; whichever lands first wins per card.

    Strategy 1 — modern markup (the only one that works on the
    current CWS):
        * Card boundary: ``<div data-item-id="<32 [a-p] chars>">``.
        * Name: the first ``<h2>`` inside the card.
        * Rating: an ``aria-label`` of the form
          ``"Average rating X.Y out of 5 stars."`` (or any element
          whose text matches the decimal-numeric pattern for a value
          between 0.0 and 5.0).
        * Description: the first ``<p>`` after the ``<h2>``.

    Strategy 2 — generic anchor harvest:
        The 32-char id can also be harvested from any ``href``
        containing ``/detail/<id>`` (with or without a slug).  We
        collect every such anchor, then post-process to (id, slug,
        name) by reading the link's text and its parent ``<h2>``
        when present.  The id regex is strict (32 chars from
        ``[a-p]`` — the actual Chrome extension id alphabet), so
        false positives are extremely unlikely.

    Strategy 3 — last-resort, blank name:
        If the page has ``data-item-id`` divs but no extractable
        names, still emit a placeholder result so the user at least
        gets a clickable install target.

    Notes on the ID alphabet
    -----------------------
    Chrome extension IDs are 32 characters from the set ``[a-p]``
    (16 letters — the first 16 of the alphabet).  Earlier
    implementations used ``[a-z]`` which is a superset and was used
    in the original code; we tighten to ``[a-p]`` so we don't accept
    garbage from the surrounding HTML (e.g. base64 padding chars).
    """

    ID_RE = re.compile(r"^[a-p]{32}$")
    ID_IN_HREF_RE = re.compile(r"/detail/(?:[a-z0-9-]+/)?([a-p]{32})")
    RATING_LIKE_RE = re.compile(r"\b([0-5](?:\.[0-9])?)\b")
    RATING_STAR_RE = re.compile(
        r'aria-label="[^"]*?([0-5](?:\.[0-9])?)\s*out\s*of\s*5[^"]*"'
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list = []
        self._seen: set = set()
        # Card extraction state.
        self._current_id: str | None = None
        self._card_name: str = ""
        self._card_rating: float = 0.0
        self._card_description: str = ""
        self._saw_h2: bool = False
        self._saw_p: bool = False
        # Strategy-2 anchor collection.
        self._anchors: list = []
        # Active capture target inside the current card.
        # One of: "name" (inside <h2>), "desc" (inside <p>),
        # "rating" (any element with a rating-ish aria-label),
        # or None.
        self._capture: str | None = None
        self._capture_buf: list = []
        # Stack of still-open <div> tags *inside* the current card
        # so we know when the card itself closes.  Plain ints suffice
        # — we only care about the depth counter relative to the
        # opening div.
        self._div_depth: int = 0

    # ── HTMLParser overrides ──
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = d.get("class", "")
        aria = d.get("aria-label", "")
        href = d.get("href", "")
        ext_id_attr = d.get("data-item-id", "")

        # ── Card boundary ──
        if self._current_id is None and ext_id_attr and self.ID_RE.match(ext_id_attr):
            self._start_card(ext_id_attr)
            # We are now "inside" the card; subsequent <div>s add to
            # _div_depth, and the first </div> at depth 0 finalises.
            return

        if self._current_id is None:
            # Strategy 2: collect detail-link anchors outside any card.
            if "/detail/" in href:
                m = self.ID_IN_HREF_RE.search(href)
                if m:
                    self._anchors.append((m.group(1), "", ""))
            return

        # ── Inside a card ──
        if tag == "div":
            self._div_depth += 1
            return

        # Name: first <h2> inside the card.
        if tag == "h2" and not self._saw_h2:
            self._saw_h2 = True
            self._begin_capture("name")
            return

        # Description: a <p> after the <h2> that carries a known
        # description class (``g3IrHd``, ``GTRhUb``, ``pj4dy``).
        # The CWS renders the description as the only <p> with text
        # content; preceding <p> elements (e.g. ``rQHEi``) are empty
        # placeholders or icon holders, so we explicitly check the
        # class instead of blindly grabbing the first <p>.
        if (
            tag == "p"
            and self._saw_h2
            and not self._saw_p
            and self._is_description_class(cls)
        ):
            self._saw_p = True
            self._begin_capture("desc")
            return

        # Rating: any element whose aria-label carries "X.Y out of 5".
        if aria and not self._card_rating:
            m = self.RATING_STAR_RE.search('aria-label="' + aria + '"')
            if m:
                try:
                    self._card_rating = float(m.group(1))
                    return
                except ValueError:
                    pass

        # Detail-link href inside the card → record for fallback.
        if "/detail/" in href:
            m = self.ID_IN_HREF_RE.search(href)
            if m:
                self._anchors.append((m.group(1), "", ""))

    def handle_endtag(self, tag):
        if self._current_id is None:
            return
        if tag == "div":
            # The opening <div data-item-id> sits at depth 0.  Any
            # nested <div> bumped us up; closing it pops back down.
            # When we get back to depth 0, the card itself is closing.
            if self._div_depth == 0:
                self._finalize_card()
            else:
                self._div_depth -= 1
            return
        if self._capture is not None and tag in ("h2", "p", "span", "div"):
            # Whichever tag opened the capture region is what closes it.
            self._end_capture()

    def handle_data(self, data):
        if self._current_id is None:
            return
        if self._capture is not None:
            self._capture_buf.append(data)

    def handle_entityref(self, name):
        # Convert &amp; / &quot; / etc. into the actual character
        # so the name and description strings are clean.
        ch = {
            "amp": "&", "lt": "<", "gt": ">", "quot": '"',
            "apos": "'", "nbsp": " ", "mdash": "—", "ndash": "–",
            "hellip": "…", "copy": "©", "reg": "®", "trade": "™",
        }.get(name)
        if ch is not None and self._capture is not None:
            self._capture_buf.append(ch)

    def handle_charref(self, name):
        # Numeric character references (e.g. &#x2605; for ★).
        try:
            if name.startswith(("x", "X")):
                ch = chr(int(name[1:], 16))
            else:
                ch = chr(int(name))
        except (ValueError, OverflowError):
            return
        if self._capture is not None:
            self._capture_buf.append(ch)

    # ── Internal capture helpers ──
    @staticmethod
    def _is_description_class(cls: str) -> bool:
        """True if ``cls`` is one of the CWS description-bearing
        classes (current: ``g3IrHd``; older markup used ``GTRhUb``
        or ``pj4dy``).
        """
        return any(marker in cls for marker in ("g3IrHd", "GTRhUb", "pj4dy"))

    def _begin_capture(self, target: str):
        # If a previous capture of a different kind is still open,
        # end it first so we don't lose data.
        if self._capture is not None and self._capture != target:
            self._end_capture()
        self._capture = target
        self._capture_buf = []

    def _end_capture(self):
        if self._capture is None:
            return
        text = "".join(self._capture_buf)
        text = " ".join(text.split())
        if self._capture == "name" and text and not self._card_name:
            self._card_name = text
        elif self._capture == "desc" and text and not self._card_description:
            self._card_description = text
        elif self._capture == "rating" and text and not self._card_rating:
            m = self.RATING_LIKE_RE.search(text)
            if m:
                try:
                    v = float(m.group(1))
                    if 0.0 <= v <= 5.0:
                        self._card_rating = v
                except ValueError:
                    pass
        self._capture = None
        self._capture_buf = []

    def _start_card(self, ext_id: str):
        self._current_id = ext_id
        self._card_name = ""
        self._card_rating = 0.0
        self._card_description = ""
        self._saw_h2 = False
        self._saw_p = False
        self._capture = None
        self._capture_buf = []
        self._div_depth = 0

    def _finalize_card(self):
        # Close any in-flight capture before deciding.
        self._end_capture()
        ext_id = self._current_id
        if ext_id and ext_id not in self._seen and self._card_name:
            self._seen.add(ext_id)
            self.results.append((
                ext_id,
                self._card_name,
                self._card_rating,
                self._card_description,
            ))
        self._current_id = None
        self._card_name = ""
        self._card_rating = 0.0
        self._card_description = ""
        self._saw_h2 = False
        self._saw_p = False
        self._capture = None
        self._capture_buf = []
        self._div_depth = 0

    # ── Post-pass: anchor harvest (Strategy 2) + last-resort (Strategy 3) ──
    def close(self):
        # If the HTML was truncated mid-card, finalize whatever we have.
        if self._current_id is not None:
            self._finalize_card()
        super().close()
        if not self.results:
            self._fallback_from_anchors()

    def _fallback_from_anchors(self):
        """Strategy 2/3 — emit at least the IDs when the card markup
        is unrecognised, so the user gets clickable install targets.
        Names are blank in this path; the dialog can still show the
        32-char id and "Install" works without a name.
        """
        for ext_id, _name, _text in self._anchors:
            if ext_id in self._seen:
                continue
            self._seen.add(ext_id)
            self.results.append((ext_id, "", 0.0, ""))


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
        _dl_log("install", "install_crx_from_path:missing-file",
                crx_path=crx_path)
        raise FileNotFoundError(crx_path)
    with open(crx_path, "rb") as f:
        crx_bytes = f.read()
    _dl_log("install", "install_crx_from_path:read-crx",
            crx_path=crx_path, bytes=len(crx_bytes))
    with tempfile.TemporaryDirectory(prefix="crx_install_") as tmp:
        CrxUnpacker.extract(crx_bytes, tmp)
        _dl_log("install", "install_crx_from_path:unpacked",
                tmp=tmp, files=os.listdir(tmp)[:8])
        manifest_path = os.path.join(tmp, "manifest.json")
        if not os.path.isfile(manifest_path):
            _dl_log("install", "install_crx_from_path:no-manifest")
            raise RuntimeError("manifest.json missing after unpack")
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            mv = int(manifest.get("manifest_version", 0))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            _dl_log("install", "install_crx_from_path:bad-manifest",
                    err=f"{type(e).__name__}: {e}")
            raise RuntimeError(f"Could not read manifest.json: {e}") from e
        if mv < 3 and mv2_blocked():
            _dl_log("install", "install_crx_from_path:mv2-blocked", mv=mv)
            raise RuntimeError(
                "This extension is MV2, which Chromium \u2265 130 rejects"
            )
        ext_id = _read_extension_id(tmp)
        if not ext_id:
            _dl_log("install", "install_crx_from_path:no-id")
            raise RuntimeError("Could not determine extension id from manifest")
        os.makedirs(extensions_dir, exist_ok=True)
        dest = os.path.join(extensions_dir, ext_id)
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        shutil.move(tmp, dest)
        _dl_log("install", "install_crx_from_path:moved",
                src=tmp, dest=dest, ext_id=ext_id, mv=mv)
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
        tuples for ``query``.

        Strategy:

        1. **CWS direct.**  Hit ``chromewebstore.google.com/search/...``
           with a real Chrome UA.  The page is server-rendered, so the
           :class:`SearchResultParser` can usually harvest full
           metadata (name, rating, description) per card.
        2. **DDG HTML fallback.**  If CWS returned nothing parseable
           (e.g. Google just redesigned the page again, or the CWS
           server is unreachable), query DuckDuckGo's no-JS
           ``/html/`` endpoint with ``site:chromewebstore.google.com``
           and recover extension IDs from the result links.  Names
           come from the result ``<a>`` text.  Ratings and
           descriptions are not available from DDG, so the dialog
           shows them as 0 / blank.
        """
        if not query.strip():
            return []
        results = self._search_cws(query.strip(), limit)
        if results:
            return results
        return self._search_ddg(query.strip(), limit)

    def _search_cws(self, query: str, limit: int) -> list:
        try:
            url = CRX_SEARCH_URL.format(query=urllib.parse.quote(query))
            html = self._get(url).decode("utf-8", errors="ignore")
        except Exception:
            return []
        parser = SearchResultParser()
        try:
            parser.feed(html)
        except Exception:
            return []
        finally:
            try:
                parser.close()
            except Exception:
                pass
        return parser.results[:limit]

    def _search_ddg(self, query: str, limit: int) -> list:
        """Fallback: query DDG HTML and recover CWS extension IDs.

        The DDG HTML page uses 302-style ``uddg=`` redirects encoded
        in each result anchor.  Decoding those gives us the real
        CWS ``/detail/<slug>/<id>`` URL, from which we can pull the
        32-char extension id and a human-readable name.
        """
        try:
            body = (
                f"q=site%3Achromewebstore.google.com+"
                f"{urllib.parse.quote(query)}"
            )
            req = urllib.request.Request(
                DDG_HTML_URL,
                data=body.encode("ascii"),
                headers={
                    "User-Agent": self.user_agent,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            html = self._get_url(req).decode("utf-8", errors="ignore")
        except Exception:
            return []

        results: list = []
        seen: set = set()
        # Walk the HTML in order: pair each redirect href with the
        # following <a> title text.
        title_iter = DDG_RESULT_TITLE_RE.finditer(html)
        for href_match, title_match in zip(
            DDG_RESULT_LINK_RE.finditer(html), title_iter
        ):
            try:
                decoded = urllib.parse.unquote(href_match.group(1))
            except Exception:
                continue
            id_match = SearchResultParser.ID_IN_HREF_RE.search(decoded)
            if not id_match:
                continue
            ext_id = id_match.group(1)
            if ext_id in seen:
                continue
            seen.add(ext_id)
            title_html = title_match.group(1)
            name = DDG_STRIP_TAGS_RE.sub("", title_html).strip()
            # DDG titles look like "uBlock Origin Lite - Chrome Web Store";
            # strip the trailing site name for a cleaner display.
            for suffix in (" - Chrome Web Store", " – Chrome Web Store"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)].strip()
                    break
            results.append((ext_id, name, 0.0, ""))
            if len(results) >= limit:
                break
        return results

    def _get_url(self, req: urllib.request.Request) -> bytes:
        """Variant of ``_get`` that accepts a pre-built Request (so the
        caller can control method / headers / body)."""
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

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
        _dl_log("install", "ChromeWebStore.download_crx:enter",
                ext_id=extension_id)
        url = CRX_DOWNLOAD_URL.format(ext_id=extension_id)
        data = self._get(url)
        _dl_log("install", "ChromeWebStore.download_crx:first-bytes",
                ext_id=extension_id, bytes=len(data), head=data[:8])
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
                _dl_log("install", "_InstallWorker.run:stopped-before-start",
                        ext_id=self.extension_id)
                return
            _dl_log("install", "_InstallWorker.run:start",
                    ext_id=self.extension_id)
            self.progress.emit(f"Downloading {self.extension_id}\u2026", 10)
            try:
                crx_bytes = self.store.download_crx(self.extension_id)
            except Exception as e:
                _dl_log("install", "_InstallWorker.run:download-failed",
                        ext_id=self.extension_id, err=f"{type(e).__name__}: {e}")
                raise
            _dl_log("install", "_InstallWorker.run:download-ok",
                    ext_id=self.extension_id, bytes=len(crx_bytes))
            if self._stop:
                return
            with tempfile.NamedTemporaryFile(
                suffix=".crx", delete=False, dir=tempfile.gettempdir()
            ) as tmp:
                tmp.write(crx_bytes)
                tmp_path = tmp.name
            self.progress.emit("Installing\u2026", 60)
            ext_id = install_crx_from_path(tmp_path, extensions_dir=self.extensions_dir)
            _dl_log("install", "_InstallWorker.run:unpack-ok",
                    ext_id=self.extension_id, installed_id=ext_id)
            self.progress.emit("Installed", 100)
            self.finished.emit(self.extension_id, True, f"Installed {ext_id}")
        except Exception as e:
            _dl_log("install", "_InstallWorker.run:exception",
                    ext_id=self.extension_id, err=f"{type(e).__name__}: {e}")
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
        _dl_log("install", "install_extension:enter", ext_id=extension_id)
        if extension_id in self._inflight:
            _dl_log("install", "install_extension:already-inflight",
                    ext_id=extension_id)
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
        _dl_log("install", "install_extension:thread-start", ext_id=extension_id)
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
        _dl_log("install", "ExtensionStore._on_finished",
                ext_id=extension_id, ok=ok, message=message)
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

