import json
import threading
import urllib.request
from pathlib import Path

from fabric import Service, Signal
from fabric.utils import GLib, logger

from core.config import state_file


class EmojiEntry:
    def __init__(self, char, data):
        self.char = char
        self.name = data.get("tts", [""])[0] if "tts" in data else ""
        self.generic_name = ""
        self.keywords = data.get("default", [])

    @property
    def id(self):
        return self.char


class EmojiService(Service):
    ready = Signal("ready")

    def __init__(self):
        super().__init__()
        self.emojis = []
        self._usage = self.load_usage()
        self._data_file = state_file("emoji_data.json")
        self._loading = False
        self._init_data()

    def _init_data(self):
        if self._data_file.exists():
            self._parse_data()
        else:
            threading.Thread(target=self._download_data, daemon=True).start()

    def _download_data(self):
        if self._loading:
            return
        self._loading = True
        try:
            url = "https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json/cldr-annotations-full/annotations/en/annotations.json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_file, "wb") as f:
                f.write(data)
            GLib.idle_add(self._parse_data)
        except Exception as e:
            logger.warning(f"Failed to download emojis: {e}")
        finally:
            self._loading = False

    def _parse_data(self):
        try:
            with open(self._data_file, "r", encoding="utf-8") as f:
                raw = json.load(f)

            annotations = raw.get("annotations", {}).get("annotations", {})
            self.emojis = []
            for char, data in annotations.items():
                # Filter out ascii characters like '{' which appear in CLDR
                if len(char) == 1 and ord(char) < 128:
                    continue
                self.emojis.append(EmojiEntry(char, data))

            GLib.idle_add(self.ready.emit)
        except Exception as e:
            logger.warning(f"Failed to parse emojis: {e}")

    @staticmethod
    def usage_file() -> Path:
        return state_file("emoji-usage.json")

    @classmethod
    def load_usage(cls) -> dict:
        path = cls.usage_file()
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError, OSError:
            return {}

    @classmethod
    def save_usage(cls, usage: dict):
        path = cls.usage_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(usage, f)
        except OSError:
            pass

    def record_usage(self, entry: EmojiEntry):
        count = self._usage.get(entry.char, 0) + 1
        self._usage[entry.char] = count
        self.save_usage(self._usage)

    @property
    def usage(self):
        return self._usage


emoji_service = EmojiService()
