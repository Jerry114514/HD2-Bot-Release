"""GitHub Issue #60 polling, content hashing and per-session delivery state."""
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request

from .data import atomic_json


ISSUE_URL = "https://api.github.com/repos/Jerry114514/Jerry114514.github.io/issues/60"
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_CACHE_SECONDS = 55
_SESSION_RE = re.compile(r"^[^:]+:(?:GroupMessage|FriendMessage):.+$", re.I)
_INVALID_PLATFORM_IDS = {"napcat", "test", "test_adapter", "fixture"}
_SYNTHETIC_SESSION_RE = re.compile(r"^(?:GROUP|TEST|FIXTURE)-[A-Z0-9]+$", re.I)
_DETECTION_LINE_RE = re.compile(r"(?m)^-\s*检测时间（UTC）：.*(?:\n|$)")
_fetch_lock = threading.Lock()
_fetch_cache = {"etag": None, "payload": None, "ts": 0.0}


def issue_revision(issue):
    """Stable hash of meaningful issue content; detection timestamps are excluded."""
    body = str((issue or {}).get("body") or "")
    normalized = _DETECTION_LINE_RE.sub("", body).replace("\r\n", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized).strip()
    title = str((issue or {}).get("title") or "")
    return hashlib.sha256((title + "\n" + normalized).encode("utf-8")).hexdigest()


def parse_issue60(body):
    """Parse Issue #60 into change groups before the documented replay separator."""
    part = (body or "").split("\n---")[0]
    match = re.search(r"检测时间（UTC）：(\S+)", part)
    detected = match.group(1) if match else ""
    match = re.search(r"银河效果定义：(\d+) 条", part)
    effect_count = int(match.group(1)) if match else None
    no_change = "本次未检测到" in part
    groups = []
    for chunk in re.split(r"## 变更：", part)[1:]:
        name = chunk.split("\n")[0].strip()
        added_part, _, removed_part = chunk.partition("### 移除的星球效果")
        groups.append({
            "name": name,
            "added": re.findall(r"`(\d+)` — (.+)", added_part),
            "removed": re.findall(r"`(\d+)` — (.+)", removed_part),
        })
    return {
        "detected": detected,
        "effect_count": effect_count,
        "no_change": no_change,
        "groups": groups,
    }


def issue_text(issue):
    info = parse_issue60((issue or {}).get("body"))
    groups = info.get("groups") or []
    lines = [
        "📡 HD2 API 内容变更",
        "检测时间（UTC）：" + (info.get("detected") or "未知"),
    ]
    if info.get("effect_count") is not None:
        lines.append(f"银河效果定义：{info['effect_count']} 条")
    if info.get("no_change"):
        lines.append("本次未检测到内容变化。")
    for group in groups:
        lines.append("")
        lines.append("▎" + group["name"])
        lines.extend(f"+ 新增 #{eid} — {name}" for eid, name in group["added"])
        lines.extend(f"- 移除 #{eid} — {name}" for eid, name in group["removed"])
    if not groups and not info.get("no_change"):
        lines.append("检测到 API 内容更新，请查看 Issue 详情。")
    lines.append("")
    lines.append(
        (issue or {}).get("html_url")
        or "https://github.com/Jerry114514/Jerry114514.github.io/issues/60"
    )
    return "\n".join(lines)


def fetch_issue60(force=False, ttl=DEFAULT_CACHE_SECONDS, timeout=20):
    """Fetch Issue #60 with ETag revalidation and a small process cache."""
    now = time.monotonic()
    with _fetch_lock:
        cached = _fetch_cache["payload"]
        if not force and cached and now - _fetch_cache["ts"] < ttl:
            return copy.deepcopy(cached)
        headers = {
            "User-Agent": "HD2Bot/2.0 (api-watch)",
            "Accept": "application/vnd.github+json",
        }
        if _fetch_cache["etag"]:
            headers["If-None-Match"] = _fetch_cache["etag"]
        request = urllib.request.Request(ISSUE_URL, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.load(response)
                payload = {
                    "title": str(data.get("title") or "HD2 API 内容变更"),
                    "body": str(data.get("body") or ""),
                    "updated_at": str(data.get("updated_at") or ""),
                    "html_url": str(data.get("html_url") or ""),
                }
                payload["revision"] = issue_revision(payload)
                _fetch_cache.update({
                    "etag": response.headers.get("ETag"),
                    "payload": payload,
                    "ts": time.monotonic(),
                })
        except urllib.error.HTTPError as error:
            if error.code != 304 or cached is None:
                raise
            _fetch_cache["ts"] = time.monotonic()
            return copy.deepcopy(cached)
        return copy.deepcopy(payload)


class ApiWatchState:
    """Persist subscribers and one delivered revision per chat session."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._state = {
            "schema": 1,
            "last_revision": "",
            "last_checked": "",
            "sessions": {},
        }
        self._load()

    def _load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(value, dict):
            return
        sessions = value.get("sessions")
        sessions = {
            origin: row for origin, row in (sessions or {}).items()
            if self.valid_origin(origin)
        } if isinstance(sessions, dict) else {}
        self._state = {
            "schema": 1,
            "last_revision": str(value.get("last_revision") or ""),
            "last_checked": str(value.get("last_checked") or ""),
            "sessions": sessions,
        }

    def _save(self):
        atomic_json(self.path, self._state)

    @staticmethod
    def valid_origin(origin):
        origin = str(origin or "").strip()
        if not _SESSION_RE.fullmatch(origin):
            return False
        platform_id, _kind, session_id = origin.split(":", 2)
        if platform_id.casefold() in _INVALID_PLATFORM_IDS:
            return False
        return not _SYNTHETIC_SESSION_RE.fullmatch(session_id)

    def _add_session(self, origin, revision, timestamp):
        origin = str(origin or "").strip()
        if not self.valid_origin(origin):
            return False
        self._state["sessions"][origin] = {
            "revision": str(revision or self._state.get("last_revision") or ""),
            "updated_at": timestamp,
        }
        return True

    def _trim_sessions(self):
        sessions = self._state.get("sessions") or {}
        if len(sessions) <= 64:
            return
        keep = sorted(
            sessions.items(),
            key=lambda row: row[1].get("updated_at") or "",
            reverse=True,
        )[:64]
        self._state["sessions"] = dict(keep)

    def subscribe(self, origin, revision=None):
        with self._lock:
            added = self._add_session(origin, revision, time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            if added:
                self._trim_sessions()
                self._save()
            return added

    def discover_from_log(self, path, max_bytes=2 * 1024 * 1024):
        """Recover sessions that invoked /api using the existing diagnostics log."""
        try:
            log_path = Path(path)
            with log_path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - max_bytes))
                lines = stream.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return 0
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        found = 0
        with self._lock:
            for line in lines:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                text = str(row.get("text") or "").strip()
                if not re.match(r"^/api(?:t|文字)?(?:\s|$)", text):
                    continue
                if self._add_session(row.get("platform"), self._state.get("last_revision"), timestamp):
                    found += 1
            if found:
                self._trim_sessions()
                self._save()
        return found

    def observe(self, issue):
        revision = str((issue or {}).get("revision") or issue_revision(issue))
        with self._lock:
            previous = str(self._state.get("last_revision") or "")
            changed = bool(previous and revision and revision != previous)
            self._state["last_revision"] = revision
            self._state["last_checked"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._save()
        return changed

    def last_revision(self):
        with self._lock:
            return str(self._state.get("last_revision") or "")

    def pending_sessions(self, revision):
        revision = str(revision or "")
        with self._lock:
            return [
                origin for origin, row in (self._state.get("sessions") or {}).items()
                if origin and (row or {}).get("revision") != revision
            ]

    def mark_delivered(self, origin, revision):
        origin = str(origin or "").strip()
        with self._lock:
            row = (self._state.get("sessions") or {}).get(origin)
            if row is None:
                return
            row["revision"] = str(revision or "")
            row["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._save()

    def subscribers(self):
        with self._lock:
            return list((self._state.get("sessions") or {}).keys())
