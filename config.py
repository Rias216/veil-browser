import json
import os

SETTINGS_FILE = "settings.json"

SEARCH_ENGINES = {
    "DuckDuckGo": "https://duckduckgo.com/?q=",
    "Brave Search": "https://search.brave.com/search?q=",
    "Startpage": "https://www.startpage.com/sp/search?query=",
    "SearXNG": "https://searx.be/search?q=",
}

DEFAULT_SETTINGS = {
    "proxy_enabled": False,
    "proxy_type": "HTTP",
    "proxy_host": "127.0.0.1",
    "proxy_port": 8080,
    "proxy_user": "",
    "proxy_pass": "",
    "adblock_enabled": True,
    "https_only": True,
    "search_engine": "https://duckduckgo.com/?q=",
    "search_engine_name": "DuckDuckGo",
    "homepage": "https://duckduckgo.com"
}

class SettingsManager:
    def __init__(self):
        self.settings = DEFAULT_SETTINGS.copy()
        self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    loaded = json.load(f)
                    self.settings.update(loaded)
            except Exception as e:
                print(f"Error loading settings: {e}")

    def save(self):
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def get(self, key):
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def set(self, key, value):
        self.settings[key] = value
        self.save()
