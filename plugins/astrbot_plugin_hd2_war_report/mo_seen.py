"""Local per-user seen state for automatic Major Order announcements."""
import json
from pathlib import Path
import threading

from astrbot_plugin_hd2_common.data import atomic_json


class MoSeenStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._users = {}
        self._load()

    def _load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        users = value.get("users") if isinstance(value, dict) else None
        self._users = users if isinstance(users, dict) else {}

    def _save(self):
        atomic_json(self.path, {"schema": 1, "users": self._users})

    @staticmethod
    def key_for(event):
        platform = str(getattr(event, "unified_msg_origin", "") or "").split(":", 1)[0]
        user = str(event.get_sender_id() or "")
        return f"{platform}|{user}" if platform and user else ""

    def needs_announcement(self, key, revision):
        revision = str(revision or "")
        if not key or revision in ("", "0", "-1"):
            return False
        with self._lock:
            return self._users.get(key) != revision

    def mark_seen(self, key, revision):
        revision = str(revision or "")
        if not key or revision in ("", "0", "-1"):
            return
        with self._lock:
            self._users[key] = revision
            if len(self._users) > 2000:
                self._users = dict(list(self._users.items())[-2000:])
            self._save()
