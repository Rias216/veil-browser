"""Lightweight, Python-side ad/tracker blocker.

- Ships with a built-in list (~150 entries) so the browser is useful
  with no network access at all.
- The user can extend it from the menu (Update AdBlock list) to pull a
  small hosts file from HaGeZi's mirror.
- No startup network call. No background threads.
- `should_block` is O(host length) via a Trie, not O(list size).
"""

import os
import urllib.request
from urllib.parse import urlparse
from PySide6.QtCore import QDateTime, QObject, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

# A focused, hand-curated list of the highest-impact ad/tracker domains.
# Covers the major ad/tracker networks with zero network cost. The user
# can extend this from the menu (Update AdBlock list) to pull a small
# HaGeZi-style blocklist.
BUILTIN_BLOCKED = {
    # Google ad / tracking surface
    "doubleclick.net", "googlesyndication.com", "googleads.g.doubleclick.net",
    "googleadservices.com", "googletagservices.com", "googletagmanager.com",
    "adservice.google.com", "adservice.google.nl", "pagead2.googlesyndication.com",
    "ads.youtube.com", "ads.google.com", "ad.doubleclick.net",
    # YouTube ad domains (comprehensive)
    "googleads.g.doubleclick.net", "pagead2.googlesyndication.com",
    "googleads4.g.doubleclick.net", "googleads5.g.doubleclick.net",
    "googleads6.g.doubleclick.net", "googleads7.g.doubleclick.net",
    "googleads8.g.doubleclick.net", "googleads9.g.doubleclick.net",
    "pubads.g.doubleclick.net", "securepubads.g.doubleclick.net",
    "static.doubleclick.net", "cm.g.doubleclick.net",
    "ad.doubleclick.net", "ad-g.doubleclick.net",
    "video.google.com", "video.googleusercontent.com",
    "youtube.com.edgesuite.net", "youtube-i.edgesuite.net",
    "yt3.ggpht.com", "yt4.ggpht.com", "yt5.ggpht.com",
    "i.ytimg.com", "i1.ytimg.com", "i2.ytimg.com", "i3.ytimg.com", "i4.ytimg.com",
    "s.ytimg.com", "s.youtube.com", "s.ytimg.com",
    "manifest.googlevideo.com", "redirector.googlevideo.com",
    "r1---sn-", "r2---sn-", "r3---sn-", "r4---sn-", "r5---sn-",
    "r6---sn-", "r7---sn-", "r8---sn-", "r9---sn-", "r10---sn-",
    "r11---sn-", "r12---sn-", "r13---sn-", "r14---sn-", "r15---sn-",
    "r16---sn-", "r17---sn-", "r18---sn-", "r19---sn-", "r20---sn-",
    "googlevideo.com", "rr1---sn-", "rr2---sn-", "rr3---sn-", "rr4---sn-",
    "rr5---sn-", "rr6---sn-", "rr7---sn-", "rr8---sn-", "rr9---sn-",
    "rr10---sn-", "rr11---sn-", "rr12---sn-", "rr13---sn-", "rr14---sn-",
    "rr15---sn-", "rr16---sn-", "rr17---sn-", "rr18---sn-", "rr19---sn-",
    "rr20---sn-", "yt3.ggpht.com", "yt4.ggpht.com", "yt5.ggpht.com",
    "youtube-nocookie.com", "youtubei.googleapis.com",
    "youtube-ui.l.google.com", "youtube-ads.l.google.com",
    "googleads.g.doubleclick.net", "partnerad.l.google.com",
    "pagead.l.google.com", "pubads.l.google.com",
    "static.doubleclick.net", "cm.l.google.com",
    "securepubads.l.google.com", "ads.youtube.com",
    "video-ads.l.google.com", "video-ads-dashboard.l.google.com",
    "static.l.google.com", "clients6.google.com",
    # Major ad networks
    "adnxs.com", "adsystem.com", "adroll.com", "taboola.com", "outbrain.com",
    "rubiconproject.com", "pubmatic.com", "openx.net", "adform.net",
    "scorecardresearch.com", "quantserve.com", "advertising.com",
    "yieldmanager.com", "criteo.com", "casalemedia.com", "adcolony.com",
    "adsterra.com", "propellerads.com", "popads.net", "popcash.net",
    "revcontent.com", "zedo.com", "yieldlab.net", "smartadserver.com",
    "sharethrough.com", "connatix.com", "media.net", "buysellads.com",
    "carbonads.com", "adskeeper.com", "hilltopads.com", "trafficjunky.net",
    # Analytics & tracking
    "google-analytics.com", "analytics.google.com", "stats.g.doubleclick.net",
    "chartbeat.com", "mixpanel.com", "hotjar.com", "optimizely.com",
    "amplitude.com", "segment.io", "segment.com", "heapanalytics.com",
    "mouseflow.com", "fullstory.com", "logrocket.com", "bugsnag.com",
    "sentry.io", "newrelic.com", "datadoghq.com", "nr-data.net",
    "pingdom.net", "clicky.com", "statcounter.com", "alexa.com",
    "quantcast.com", "comscore.com", "nielsen.com", "permutive.com",
    "bluekai.com", "lotame.com", "oracleinfinity.com", "demdex.net",
    "adsrvr.org", "everesttech.net", "mathtag.com", "rlcdn.com",
    "crwdcntrl.net", "agkn.com", "exelator.com",
    "turn.com", "amgdgt.com", "adsymptotic.com", "moatads.com",
    # Social trackers
    "ads.facebook.com", "ads.linkedin.com", "ads.pinterest.com",
    "ads.reddit.com", "ads.tiktok.com", "ads.x.com",
    "connect.facebook.net", "platform.twitter.com",
    "px.ads.linkedin.com", "snap.licdn.com", "ct.pinterest.com",
    # Tag managers / beacon spam
    "tags.tiqcdn.com", "tealiumiq.com", "ensighten.com",
    "munchkin.marketo.net", "marketo.net", "adobedtm.com",
    "omtrdc.net", "everestads.net",
    # Misc
    "disqus.com", "coinhive.com", "cryptoloot.com", "coinhive-manager.com",
    "minero.cc", "coin-hive.com", "jsecoin.com", "deepminer.com",
    "flixgratis.com", "popunderjs.com", "exitbee.com",
    "bidswitch.net", "smartclip.net", "adocean.pl", "ad6media.fr",
    "adf.ly", "linkbucks.com", "shorte.st", "bc.vc",
    "clickbank.com", "ero-advertising.com",
    "exoclick.com", "trafficfactory.biz", "juicyads.com", "plugrush.com",
}

# A small, focused mirror used by the optional "Update AdBlock list" menu
# item. The user must explicitly trigger it; no automatic downloads.
BLOCKLIST_URL = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/hosts/hosts.txt"


# ── Trie helpers ─────────────────────────────────────────────────────
def build_trie(hosts):
    """Build a domain-suffix trie from an iterable of host strings.

    Walks labels right-to-left, so a trie entry for `doubleclick.net` is
    stored under `root["net"]["doubleclick"]`. This makes `match_trie` a
    simple O(host length) right-to-left walk.
    """
    root = {}
    for host in hosts:
        if not host:
            continue
        labels = host.lower().split(".")
        node = root
        for label in reversed(labels):
            if label not in node:
                node[label] = {}
            node = node[label]
        node[None] = True  # terminator
    return root


def match_trie(trie, host):
    """Return True if `host` (or any parent domain) is in the trie.

    Walks labels right-to-left so a trie entry for `doubleclick.net` will
    match `ad.doubleclick.net` and `pagead2.doubleclick.net`.
    """
    if not trie or not host:
        return False
    labels = host.lower().split(".")
    node = trie
    for label in reversed(labels):
        if label not in node:
            return False
        node = node[label]
        if node.get(None):
            return True
    return False


# ── AdBlocker ────────────────────────────────────────────────────────
class AdBlocker(QObject):
    """Trie-backed ad/tracker blocker.

    Emits `count_changed(int)` whenever a block is recorded, so the UI can
    update an indicator without polling. No heartbeat timer.
    """

    count_changed = Signal(int)

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self.settings = settings_manager
        self._trie = build_trie(BUILTIN_BLOCKED)
        # Counter is read by a single UI slot; the only writer is the
        # network interceptor thread. A plain int is fine — Python's GIL
        # makes single-int append-or-overwrite effectively atomic for our
        # purposes (a missed update only delays the next UI refresh by one
        # block), and we avoid the cost of a Lock on every hit.
        self._count = 0

    # ── Read API ─────────────────────────────────────────────────────
    def get_blocked_count(self):
        return self._count

    def should_block(self, url_str):
        host = urlparse(url_str).netloc.lower()
        if not host:
            return False
        if ":" in host:
            host = host.split(":", 1)[0]
        if match_trie(self._trie, host):
            self._count += 1
            self.count_changed.emit(self._count)
            return True
        return False

    def get_blocked_size(self):
        """Return the number of entries currently in the trie."""
        return _count_trie_nodes(self._trie)

    # ── Update from remote list ──────────────────────────────────────
    def update_from_url(self, url=BLOCKLIST_URL, on_done=None):
        """Synchronously pull a hosts list and rebuild the trie from the
        merged set. Runs on the caller thread (it's a tiny fetch).
        """
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                content = r.read().decode("utf-8", errors="ignore")
            hosts = set(BUILTIN_BLOCKED)
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                parts = line.split()
                host = None
                if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1"):
                    host = parts[1].lower()
                elif line.startswith("||") and "^" in line:
                    host = line[2:line.index("^")].lower()
                if not host or host in ("localhost", "broadcasthost", "0.0.0.0"):
                    continue
                hosts.add(host)
            self._trie = build_trie(hosts)
            total = len(hosts)
            if on_done:
                on_done(True, total)
            return True, total
        except Exception as e:
            if on_done:
                on_done(False, 0, error=str(e))
            return False, 0


def _count_trie_nodes(node):
    """Count the number of host entries in a trie (one per `None` key)."""
    n = 0
    for k, v in node.items():
        if k is None:
            n += 1
        elif isinstance(v, dict):
            n += _count_trie_nodes(v)
    return n


# ── HTTPS-only + ad-block on a single request hook ───────────────────
class PrivacyRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """HTTPS-only upgrade + ad/tracker blocking on a single request hook."""

    def __init__(self, adblocker, settings_manager):
        super().__init__()
        self.adblocker = adblocker
        self.settings = settings_manager

    def interceptRequest(self, info):
        url = info.requestUrl()
        url_str = url.toString()
        scheme = url.scheme()

        # HTTPS-Only
        if self.settings.get("https_only") and scheme == "http":
            host = url.host().lower()
            if host not in ("localhost", "127.0.0.1") and not host.endswith(".local"):
                new_url = url
                new_url.setScheme("https")
                info.redirect(new_url)
                return

        # AdBlock
        if self.adblocker.should_block(url_str):
            info.block(True)


# ── Cookie hardening helpers ─────────────────────────────────────────
def _generate_random_string(length=16):
    import random
    import string
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def inject_fake_cookies(profile):
    cookie_store = profile.cookieStore()
    fake_trackers = [
        (".google-analytics.com", "_ga", "GA1.2."),
        (".doubleclick.net", "id", "22"),
        (".facebook.com", "_fbp", "fb.1."),
        (".facebook.com", "_fbc", "fb.1.164b."),
        (".adnxs.com", "uuid2", ""),
        (".quantserve.com", "__qca", "P0-"),
        (".scorecardresearch.com", "UID", ""),
    ]
    for domain, name, prefix in fake_trackers:
        val = prefix + _generate_random_string(16)
        cookie = QNetworkCookie(name.encode(), val.encode())
        cookie.setDomain(domain)
        cookie.setPath("/")
        cookie.setExpirationDate(QDateTime.currentDateTime().addYears(1))
        cookie_store.setCookie(cookie)


def block_third_party_cookies(profile):
    cookie_store = profile.cookieStore()
    cookie_store.setCookieFilter(lambda request: not request.thirdParty)
