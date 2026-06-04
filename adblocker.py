import os
import urllib.request
import threading
import random
import string
from urllib.parse import urlparse
from PySide6.QtCore import QDateTime
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

HOSTS_URL = "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
CACHE_FILE = "adblock_hosts.txt"

# Curated small built-in list to block ads immediately even without internet or before downloading
BUILTIN_BLOCKED = {
    "doubleclick.net", "googlesyndication.com", "googleads.g.doubleclick.net",
    "googleadservices.com", "googletagservices.com", "adnxs.com",
    "adsystem.com", "adroll.com", "taboola.com", "outbrain.com",
    "rubiconproject.com", "pubmatic.com", "openx.net", "adform.net",
    "scorecardresearch.com", "quantserve.com", "advertising.com",
    "yieldmanager.com", "criteo.com", "casalemedia.com",
    "bluekai.com", "flurry.com", "chartbeat.com", "mixpanel.com",
    "hotjar.com", "optimizely.com", "disqus.com", "coinhive.com",
    "adservice.google.com", "adservice.google.nl", "pagead2.googlesyndication.com",
    "analytics.google.com", "google-analytics.com", "stats.g.doubleclick.net",
    "adserver.com", "ads.youtube.com", "ads.facebook.com", "telemetry",
    "trackers", "analytics"
}

class AdBlocker:
    def __init__(self, settings_manager):
        self.settings = settings_manager
        self.blocked_hosts = set(BUILTIN_BLOCKED)
        self.lock = threading.Lock()
        self.blocked_count = 0
        self.load_cache()
        # Start async download of full hosts list
        threading.Thread(target=self.download_hosts, daemon=True).start()

    def load_cache(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            self.blocked_hosts.add(line.lower())
                print(f"Loaded {len(self.blocked_hosts)} adblock domains from cache.")
            except Exception as e:
                print(f"Error loading adblock cache: {e}")

    def download_hosts(self):
        if not self.settings.get("adblock_enabled"):
            return
        try:
            print("Downloading latest adblock hosts list...")
            req = urllib.request.Request(
                HOSTS_URL, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode('utf-8')
                new_hosts = set(BUILTIN_BLOCKED)
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        parts = line.split()
                        if len(parts) >= 2:
                            # Usually "0.0.0.0 domain.com" or "127.0.0.1 domain.com"
                            host = parts[1].lower()
                            if host not in ("localhost", "broadcasthost"):
                                new_hosts.add(host)
                
                # Update in-memory set
                self.blocked_hosts = new_hosts
                
                # Write cache
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    for host in sorted(new_hosts):
                        f.write(host + "\n")
                print(f"Successfully updated adblock lists. Total hosts: {len(self.blocked_hosts)}")
        except Exception as e:
            print(f"Failed to update adblock hosts: {e}")

    def should_block(self, url_str):
        if not self.settings.get("adblock_enabled"):
            return False
        
        parsed = urlparse(url_str)
        host = parsed.netloc.lower()
        if not host:
            return False
            
        # Clean host (remove port if any)
        if ":" in host:
            host = host.split(":")[0]
            
        # Check domain and all its parent domains
        parts = host.split(".")
        for i in range(len(parts) - 1):
            subdomain = ".".join(parts[i:])
            if subdomain in self.blocked_hosts:
                with self.lock:
                    self.blocked_count += 1
                return True
                
        # Substring fallback for common ad networks
        for pattern in BUILTIN_BLOCKED:
            if pattern in host:
                with self.lock:
                    self.blocked_count += 1
                return True
                
        return False

    def get_blocked_count(self):
        with self.lock:
            return self.blocked_count

class PrivacyRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, adblocker, settings_manager):
        super().__init__()
        self.adblocker = adblocker
        self.settings = settings_manager

    def interceptRequest(self, info):
        url = info.requestUrl()
        url_str = url.toString()
        scheme = url.scheme()
        
        # 1. HTTPS-Only Mode Upgrader
        if self.settings.get("https_only") and scheme == "http":
            host = url.host().lower()
            if host not in ("localhost", "127.0.0.1") and not host.endswith(".local"):
                new_url = url
                new_url.setScheme("https")
                info.redirect(new_url)
                return

        # 2. AdBlocker
        if self.adblocker.should_block(url_str):
            info.block(True)

def generate_random_string(length=16):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def inject_fake_cookies(profile):
    cookie_store = profile.cookieStore()
    
    fake_trackers = [
        (".google-analytics.com", "_ga", "GA1.2."),
        (".google-analytics.com", "_gid", "GA1.2."),
        (".doubleclick.net", "id", "22"),
        (".facebook.com", "_fbp", "fb.1."),
        (".facebook.com", "_fbc", "fb.1.164b."),
        (".adnxs.com", "uuid2", ""),
        (".quantserve.com", "__qca", "P0-"),
        (".scorecardresearch.com", "UID", ""),
    ]
    
    for domain, name, prefix in fake_trackers:
        val = prefix + generate_random_string(16)
        cookie = QNetworkCookie(name.encode(), val.encode())
        cookie.setDomain(domain)
        cookie.setPath("/")
        cookie.setExpirationDate(QDateTime.currentDateTime().addYears(1))
        cookie_store.setCookie(cookie)

def block_third_party_cookies(profile):
    cookie_store = profile.cookieStore()
    # Install third-party cookie blocking filter
    cookie_store.setCookieFilter(lambda request: not request.thirdParty)
