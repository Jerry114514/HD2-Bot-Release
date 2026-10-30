"""Shared, read-only site snapshots and pure war-state calculations.

The shape follows the site's forecast.js and the earlier HD2 datasource work.
Missing observations remain unknown; source time is never replaced with fetch time.
"""
import contextlib
import contextvars
from concurrent.futures import Future
from collections import OrderedDict
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

_root = os.environ.get("HD2_PROJECT_DIR")
if _root:
    import sys
    if _root not in sys.path:
        sys.path.insert(0, _root)
try:
    import hd2_config as config
except ImportError:
    config = None


def setting(*keys, default=None):
    return config.get(*keys, default=default) if config else default


DEFAULT_SITE = "https://jerry114514.github.io/HD2-Galatic_war-Map"
CACHE = Path(__file__).parent / "cache"
_memory = OrderedDict()
_lock = threading.RLock()
_inflight = {}
_snapshot = contextvars.ContextVar("hd2_snapshot", default=None)


def site_url(path="data.json"):
    base = setting("data", "site_base", default=DEFAULT_SITE).rstrip("/")
    if path == "data.json":
        configured = setting("data", "news_json_url", default=None)
        return configured if configured and configured != DEFAULT_SITE + "/data.json" else base + "/data.json"
    return base + "/" + path.lstrip("/")


def resource_url(url):
    if url.startswith(DEFAULT_SITE + "/"):
        return site_url(url[len(DEFAULT_SITE) + 1:])
    return url


def get_json(url, ttl=120, timeout=15, force=False, validate=None, stats=None):
    """A bounded in-memory cache; callers explicitly decide whether stale data is usable."""
    url = resource_url(url)
    with _lock:
        hit = _memory.get(url)
        if not force and hit and time.monotonic() - hit[0] < ttl:
            try:
                if validate: validate(hit[1])
            except ValueError:
                _memory.pop(url, None)
            else:
                if stats is not None:
                    stats["cache_hits"] = stats.get("cache_hits", 0) + 1
                _memory.move_to_end(url)
                return copy.deepcopy(hit[1])
        pending = _inflight.get(url)
        owner = pending is None
        if owner:
            pending = _inflight[url] = Future()
    if not owner:
        if stats is not None:
            stats["coalesced"] = stats.get("coalesced", 0) + 1
        value = copy.deepcopy(pending.result(timeout=timeout))
        if validate:
            validate(value)
        return value
    try:
        if stats is not None:
            stats["requests"] = stats.get("requests", 0) + 1
        req = Request(url, headers={"User-Agent": "HD2-Bot/2.0", "Accept": "application/json"})
        with urlopen(req, timeout=timeout) as response:
            value = json.load(response)
        if validate:
            validate(value)
        if not isinstance(value, dict) or not value.get("error"):
            with _lock:
                _memory[url] = (time.monotonic(), copy.deepcopy(value))
                _memory.move_to_end(url)
                while len(_memory) > 256:
                    _memory.popitem(last=False)
        pending.set_result(value)
        return value
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _lock:
            _inflight.pop(url, None)


def number(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, TypeError):
        return None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write_", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def site_doc(force=False):
    active = _snapshot.get()
    if active is not None:
        return active
    primary = site_url()
    mirror = setting("data","backup_json_url",default="")
    sources = [primary] + ([mirror] if mirror and mirror != primary else [])
    cache = CACHE / ("site_" + hashlib.sha256(primary.encode()).hexdigest()[:16] + ".json")
    cached, doc, source, last_error = False, None, primary, None
    for url in sources:
        try:
            candidate = get_json(url,ttl=setting("data","news_cache_ttl",default=120),force=force)
            if not isinstance(candidate,dict) or not isinstance(candidate.get("planets"),list) or timestamp(candidate.get("fetchedAt")) is None:
                raise ValueError("战况快照格式不完整")
            doc, source = candidate, url
            try:
                atomic_json(cache,{"document":doc,"source":source})
            except OSError:
                pass
            break
        except (OSError,ValueError) as error:
            last_error = error
    if doc is None:
        try:
            record = json.loads(cache.read_text(encoding="utf-8"))
            doc = record.get("document",record)
            source = record.get("source") if "document" in record else primary
            cached = True
        except (OSError,ValueError,AttributeError):
            raise RuntimeError("暂时无法获取战况数据") from last_error
    if not isinstance(doc,dict):
        raise RuntimeError("战况缓存格式错误")
    observed = timestamp(doc.get("fetchedAt"))
    age = time.time()-observed if observed is not None else math.inf
    if age < -300 or age > setting("data","max_stale_seconds",default=3600):
        raise RuntimeError("战况数据已过期，暂不提供实时结论")
    doc = copy.deepcopy(doc)
    doc["_hd2"] = {"source":source,"observed_at":doc["fetchedAt"],"stale":cached or age>600,
                   "fallback_source":source != primary,"version":doc["fetchedAt"]}
    return doc


@contextlib.contextmanager
def snapshot_scope(doc):
    token = _snapshot.set(doc)
    try:
        yield
    finally:
        _snapshot.reset(token)


def fmt_big_num(value):
    n = number(value)
    if n is None:
        return "未知"
    if abs(n) >= 100_000_000:
        return f"{n / 100_000_000:.2f}".rstrip("0").rstrip(".") + "亿"
    if abs(n) >= 10_000:
        return f"{n / 10_000:.1f}".rstrip("0").rstrip(".") + "万"
    return f"{n:g}"


def percent(current, goal):
    current, goal = number(current), number(goal)
    return max(0., min(100., current / goal * 100)) if current is not None and goal and goal > 0 else None


def planet_key(value):
    from .search import planet_key as key
    return key(str(value or "").replace("_", " "))


def campaign_state(doc, planet):
    idx = planet.get("index")
    camp = next((c for c in doc.get("campaigns", [])
                 if str((c.get("planet") or {}).get("index", c.get("planetIndex"))) == str(idx)), {})
    event = next((e for e in doc.get("planet_events", [])
                  if str(e.get("planetIndex")) == str(idx) and e.get("eventType") == 1), None)
    event = event or planet.get("event") or {}
    defending = bool(event) or str(camp.get("campaignType", "")).lower() == "defense" or str(camp.get("type")) in ("1", "2", "4", "defense")
    hp = number(event.get("health") if defending else planet.get("health"))
    maxhp = number(event.get("maxHealth") if defending else planet.get("maxHealth"))
    progress = number(camp.get("defendersProgress")) if defending else None
    if progress is None and hp is not None and maxhp and maxhp > 0:
        progress = max(0., min(100., (1 - hp / maxhp) * 100))
    if not defending and planet.get("currentOwner") in ("Humans", 1):
        progress = 100.
    deadline = event.get("endTime") or camp.get("endTime")
    remaining = number(camp.get("remainingTime"))
    if not deadline and remaining is not None and timestamp(doc.get("fetchedAt")) is not None:
        deadline = datetime.fromtimestamp(timestamp(doc["fetchedAt"]) + remaining, timezone.utc).isoformat()
    rate = number(camp.get("defendersRate")) if defending else None
    measured = rate is not None
    players, impact = number(planet.get("players")), number(doc.get("impactMultiplier"))
    resistance = 0. if defending else number(planet.get("resistance"))
    if rate is None and None not in (players, impact, resistance) and maxhp and maxhp > 0:
        rate = players * impact * 90 / maxhp * 100 - resistance
    owner = planet.get("currentOwner")
    return {"defending": defending, "progress": progress, "health": hp, "max_health": maxhp,
            "deadline": deadline, "rate": rate, "measured": measured, "resistance": resistance,
            "faction": camp.get("faction") or event.get("faction") or owner,
            "status": "防御战役中" if defending else "已解放" if owner in ("Humans", 1) else "解放战役中" if camp else "敌方控制"}


def dss_phase(dss, action):
    active = bool(set(action.get("effectIds") or []) & set(dss.get("activeEffectIds") or []))
    return "已激活" if active else "冷却中" if action.get("status") == 3 else "募捐中"


def player_distribution(doc):
    dist = doc.get("player_distribution") or {}
    return {"total": number(dist.get("total", ((doc.get("war") or {}).get("statistics") or {}).get("playerCount"))),
            **{key:number(dist.get(key)) for key in ("humans","terminids","automatons","illuminate")},
            "updatedAt": dist.get("updatedAt") or doc.get("fetchedAt")}


def current_phase(campaign):
    phases = campaign.get("phases") or []
    return next((p for p in phases if p.get("id") is not None and p.get("id")==campaign.get("current_phase_id")), None) or next((p for p in phases if p.get("status")==0), None) or (phases[0] if phases else {})


def region_frames(planet_index, region_index):
    doc = get_json(site_url("data/recent/planet_regions.json"), ttl=300)
    hits = []
    for frame in doc.get("data") or []:
        active = _snapshot.get()
        observed = timestamp(active.get("fetchedAt")) if active else None
        frame_time = timestamp(frame.get("timestampUtc"))
        if observed is not None and (frame_time is None or frame_time > observed):
            continue
        for region in frame.get("planetRegions") or []:
            if region.get("planetIndex") == planet_index and region.get("regionIndex") == region_index and timestamp(frame.get("timestampUtc")) is not None:
                hits.append({**region, "timestampUtc": frame["timestampUtc"]})
    hits.sort(key=lambda row: timestamp(row["timestampUtc"]))
    if not hits:
        return None
    regen = next((r.get("regerPerSecond", r.get("regenPerSecond")) for r in reversed(hits)
                  if r.get("regerPerSecond", r.get("regenPerSecond")) is not None), None)
    return hits[0], hits[-1], number(regen)


def region_rate(planet, region, impact):
    maxhp = number(region.get("maxHealth"))
    if not maxhp or maxhp <= 0 or str(region.get("owner")).lower() in ("humans", "1"):
        return None
    try:
        frames = region_frames(planet.get("index"), region.get("regionIndex"))
    except (OSError, ValueError):
        frames = None
    regen, net, measured = frames[2] if frames else None, None, False
    if frames:
        first, last, _ = frames
        t0, t1 = timestamp(first["timestampUtc"]), timestamp(last["timestampUtc"])
        age = time.time() - t1
        if not -300 <= age <= 10800:
            frames, regen = None, None
        elif (t1 - t0 >= 1800 and first.get("owner") == last.get("owner")
              and all(number(f.get("health")) is not None for f in (first, last))
              and not all(f["health"] >= maxhp * .9995 for f in (first, last))):
            net = -(last["health"] - first["health"]) / maxhp * 100 / ((t1 - t0) / 3600)
            measured = True
    resistance = regen * 3600 / maxhp * 100 if regen is not None else None
    players, impact = number(region.get("players")), number(impact)
    if net is None and None not in (players, impact, resistance):
        net = players * impact * 90 / maxhp * 100 - resistance
    return net, resistance, measured
