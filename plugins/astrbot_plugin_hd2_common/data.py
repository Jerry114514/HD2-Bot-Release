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
import html
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from .paths import plugin_cache_dir

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
# 快照缓存落在插件数据目录（data/plugin_data/...），不写插件安装目录。
CACHE = plugin_cache_dir("astrbot_plugin_hd2_common", create=False)
MAJOR_ORDER_BANNER = "./assets/campaign/banner_eagle3.webp"
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


DSS_RESOURCE_NAMES = {
    3992382197: "普通样本",
    2985106497: "稀有样本",
    3608481516: "申购点",
}
DSS_HERO_IMAGES = {
    "飞鹰风暴": "eagle_storm.webp",
    "飞鹰毒气风暴": "eagle_storm.webp",
    "飞鹰封锁": "eagle_storm.webp",
    "轨道封锁": "orbital_blockade.webp",
    "重型军械分发": "heavy_ordnance.webp",
    "星球轰炸": "heavy_ordnance.webp",
}
DSS_HERO_FALLBACK = "eagle_storm.webp"


def _dss_catalog_entries(variables=None):
    if variables is None:
        from .sources import table_candidates
        for path in table_candidates("hd2_variables.json"):
            try:
                with open(path, encoding="utf-8") as stream:
                    variables = json.load(stream)
                break
            except (OSError, ValueError, TypeError):
                continue
    if not isinstance(variables, dict):
        return []
    for category in variables.get("categories") or []:
        if isinstance(category, dict) and category.get("key") == "tactical_action":
            return [
                entry for entry in category.get("entries") or []
                if isinstance(entry, dict) and not (1238 in (entry.get("effect_ids") or []))
            ]
    return []


def _dss_matching_entry(action, entries):
    effect_ids = {
        number(value) for value in action.get("effectIds") or []
        if number(value) is not None
    }
    candidates = []
    for entry in entries or ():
        wanted = {
            number(value) for value in entry.get("effect_ids") or []
            if number(value) is not None
        }
        if wanted and wanted.issubset(effect_ids):
            candidates.append(entry)
    if candidates:
        candidates.sort(key=lambda entry: len(entry.get("effect_ids") or []), reverse=True)
        return candidates[0]
    action_id = number(action.get("id32"))
    if action_id is not None:
        for entry in entries or ():
            if number(entry.get("action_id32")) == action_id:
                return entry
    return {
        "cn": action.get("name") or "未知战术行动",
        "desc": "暂无本地中文说明。",
        "effect_ids": sorted(effect_ids),
    }


def _dss_display_name(value):
    text = str(value or "未知战术行动")
    return re.sub(
        r"^[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\s]+",
        "",
        text,
    ).strip() or text


def _dss_resource_lines(action):
    lines = []
    for cost in action.get("cost") or []:
        if not isinstance(cost, dict):
            continue
        resource = DSS_RESOURCE_NAMES.get(number(cost.get("itemMixId")))
        if not resource:
            lines.append("捐赠资源待确认")
            continue
        amount = number(cost.get("maxDonation"))
        period = number(cost.get("maxDonationPeriodSec"))
        if amount is None or amount <= 0 or period is None or period <= 0:
            lines.append("捐赠资源：" + resource)
            continue
        hours = period / 3600
        cycle = f"{hours:g} 小时" if hours.is_integer() else f"{period:g} 秒"
        lines.append(
            f"每人每 {cycle}最多捐赠 {fmt_big_num(amount)} {resource}"
        )
    return lines


def dss_action_models(dss, variables=None):
    """Map runtime DSS actions to the site's catalog, images and donation fields."""
    actions = [
        action for action in (dss or {}).get("tacticalActions") or []
        if isinstance(action, dict) and number(action.get("status")) in (1, 2, 3)
    ]
    entries = _dss_catalog_entries(variables)
    models = []
    for action in actions:
        entry = _dss_matching_entry(action, entries)
        name = _dss_display_name(entry.get("cn") or action.get("name"))
        status = int(number(action.get("status")) or 1)
        phase = "已激活" if status == 2 else "冷却中" if status == 3 else "募捐中"
        cost = next((row for row in action.get("cost") or [] if isinstance(row, dict)), {})
        current = number(cost.get("current"))
        target = number(cost.get("target"))
        progress = percent(current, target)
        models.append({
            "action": action,
            "entry": entry,
            "id32": action.get("id32"),
            "name": name,
            "description": entry.get("desc") or "暂无本地中文说明。",
            "status": status,
            "phase": phase,
            "is_active": status == 2,
            "is_cooldown": status == 3,
            "current": current,
            "target": target,
            "progress": progress,
            "resource": DSS_RESOURCE_NAMES.get(number(cost.get("itemMixId"))) or "资源",
            "resource_lines": _dss_resource_lines(action),
            "image": DSS_HERO_IMAGES.get(name, DSS_HERO_FALLBACK),
            "effect_ids": [number(value) for value in action.get("effectIds") or []
                           if number(value) is not None],
        })
    return models


def player_distribution(doc):
    dist = doc.get("player_distribution") or {}
    return {"total": number(dist.get("total", ((doc.get("war") or {}).get("statistics") or {}).get("playerCount"))),
            **{key:number(dist.get(key)) for key in ("humans","terminids","automatons","illuminate")},
            "updatedAt": dist.get("updatedAt") or doc.get("fetchedAt")}


def current_phase(campaign):
    phases = campaign.get("phases") or []
    return next((p for p in phases if p.get("id") is not None and p.get("id")==campaign.get("current_phase_id")), None) or next((p for p in phases if p.get("status")==0), None) or (phases[0] if phases else {})


# Arrowhead dispatch markup: <i=1>/<ci=3>; a bare <i>/<ci> means "1" (site parity).
_RICH_TAG_RE = re.compile(r"<(i|ci)(?:=([0-9]+))?>([\s\S]*?)</\1>", re.I)
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_SCRIPT_STYLE_RE = re.compile(
    r"<\s*(?:script|style)\b[^>]*>[\s\S]*?</\s*(?:script|style)\s*>", re.I
)
_BREAK_RE = re.compile(r"<br\s*/?>", re.I)
_RICH_TONES = {"1": "yellow", "3": "blue"}
_TONE_MARK = {"yellow": "\x01Y\x01", "blue": "\x01B\x01", None: "\x01E\x01"}
_TONE_MARK_RE = re.compile("\x01[YBE]\x01")


def _rich_text(value, tones=False):
    """Strip dispatch markup; optionally leave tone markers for ``rich_text_runs``.

    Safety mirrors the site: script/style blocks are removed, only the whitelisted
    ``<i>/<ci>`` pair is interpreted, every other tag loses its shell while its text
    survives, and HTML entities are decoded once at the very end.  Nothing is ever
    re-emitted as HTML.
    """
    text = str(value or "")
    for marker in _TONE_MARK.values():
        text = text.replace(marker, "")
    text = text.replace("\x01", "").replace("\x02", "")
    text = _SCRIPT_STYLE_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")

    def replace(match):
        tone = _RICH_TONES.get(match.group(2) or "1")
        inner = match.group(3)
        if not tones or tone is None:
            return inner
        return _TONE_MARK[tone] + inner + _TONE_MARK[None]

    text = _RICH_TAG_RE.sub(replace, text)
    text = _BREAK_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub("", text)
    return html.unescape(text)


def _plain_rich_text(value):
    return _rich_text(value).strip()


def rich_text_runs(value):
    """Return ``[(text, "yellow"|"blue"|None), ...]`` with adjacent runs merged."""
    text = _rich_text(value, tones=True)
    runs = []
    stack = []
    position = 0
    for match in _TONE_MARK_RE.finditer(text):
        chunk = text[position:match.start()]
        if chunk:
            runs.append((chunk, stack[-1] if stack else None))
        marker = match.group(0)
        if marker == _TONE_MARK[None]:
            if stack:
                stack.pop()
        else:
            stack.append("yellow" if marker == _TONE_MARK["yellow"] else "blue")
        position = match.end()
    tail = text[position:]
    if tail:
        runs.append((tail, stack[-1] if stack else None))
    combined = []
    for part, tone in runs:
        if combined and combined[-1][1] == tone:
            combined[-1] = (combined[-1][0] + part, tone)
        elif part:
            combined.append((part, tone))
    return combined


def split_rich_text(value):
    """Split a dispatch headline from its body, keeping the body's emphasis runs."""
    title, body = _split_text(value)
    raw = _BREAK_RE.sub("\n", str(value or ""))
    kept, dropped = [], False
    for line in raw.splitlines():
        if not dropped and _plain_rich_text(line):
            dropped = True
            continue
        if dropped:
            kept.append(line)
    return title, body, rich_text_runs("\n".join(kept).strip())


def _split_text(value):
    plain = _plain_rich_text(value)
    lines = [line.strip() for line in plain.splitlines()]
    first = next((line for line in lines if line), "")
    body = plain
    if first:
        body = re.sub(r"^\s*" + re.escape(first) + r"\s*", "", plain, count=1).strip()
    return first, body


def _stable_major_order_revision(doc, assignment):
    assignment_id = assignment.get("id")
    if assignment_id not in (None, "", 0, -1, "0", "-1"):
        return str(assignment_id)
    tasks = []
    for task in assignment.get("tasks") or []:
        if isinstance(task, dict):
            tasks.append({
                "index": task.get("index"),
                "type": task.get("type"),
                "values": task.get("values"),
                "goal": task.get("goal"),
            })
    war = doc.get("war") or {}
    key = "|".join(str(value or "") for value in (
        war.get("warId", war.get("id")),
        assignment.get("startTime", assignment.get("startedAt")),
        assignment.get("deadline"),
        json.dumps(tasks, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    ))
    return "h:" + hashlib.sha256(key.encode()).hexdigest()[:12] if key.strip("|") else ""


def major_order_content(doc):
    """Resolve the current MO without borrowing old dispatches or campaign text."""
    assignment = doc.get("assignments") or {}
    major_order = doc.get("major_order") or {}
    started = timestamp(assignment.get("startTime", assignment.get("startedAt")))
    frontier = started - 48 * 3600 if started is not None else None
    dispatches = []
    for dispatch in doc.get("dispatches") or []:
        if not isinstance(dispatch, dict):
            continue
        title = str(dispatch.get("message") or dispatch.get("title") or "")
        if not re.search(r"NEW\s+MAJOR\s+ORDER", title, re.I):
            continue
        published = timestamp(dispatch.get("published") or dispatch.get("publishedAt"))
        if frontier is not None and (published is None or published < frontier):
            continue
        dispatches.append((published or 0, number(dispatch.get("id")) or 0, dispatch))
    dispatches.sort(key=lambda row: (row[0], row[1]), reverse=True)

    dispatch = dispatches[0][2] if dispatches else None
    dispatch_id = dispatch.get("id") if dispatch else None
    news_item = {}
    if dispatch_id is not None:
        news_item = next(
            (
                item for item in doc.get("news") or []
                if isinstance(item, dict) and str(item.get("id")) == str(dispatch_id)
            ),
            {},
        )
    candidates = [
        # Tagged dispatch sources first, by field kind (official > raw > original):
        # only these carry <i=N>; a lower-priority field on one object must not
        # outrank a higher-priority field on another.
        ("official_message", news_item.get("official_message")),
        ("major_order.official_message", major_order.get("official_message")),
        ("raw_message", news_item.get("raw_message")),
        ("major_order.raw_message", major_order.get("raw_message")),
        ("message", news_item.get("message")),
        # Translation-chain fields are plain text; keep them as fallback only.
        ("translated", news_item.get("translated")),
        ("message_zh", news_item.get("message_zh")),
        ("major_order.translated", major_order.get("translated")),
    ]
    raw_text, text_source = "", ""
    for source, value in candidates:
        if _plain_rich_text(value):
            raw_text, text_source = str(value), source
            break

    assignment_id = assignment.get("id")
    translated_id = major_order.get("id32")
    translation_matches = (
        assignment_id in (None, "")
        or translated_id in (None, "")
        or str(assignment_id) == str(translated_id)
    )
    if not raw_text and translation_matches and _plain_rich_text(major_order.get("translated_brief")):
        raw_text, text_source = str(major_order["translated_brief"]), "major_order.translated_brief"
    if not raw_text and assignment.get("briefing"):
        raw_text, text_source = str(assignment["briefing"]), "assignment.briefing"
    if not raw_text and dispatch:
        raw_text, text_source = str(dispatch.get("message") or ""), "dispatch.message"

    banners = []
    for source in (
        major_order.get("banner_image_local"),
        major_order.get("banner_image"),
        MAJOR_ORDER_BANNER,
    ):
        if source and source not in banners:
            banners.append(source)
    return {
        "title": _plain_rich_text(major_order.get("title")) or "重要指令",
        "text": _plain_rich_text(raw_text),
        "rich_text": rich_text_runs(raw_text),
        "source": text_source,
        "dispatch_id": dispatch_id,
        "published": dispatch.get("published") if dispatch else None,
        "banner_sources": banners,
        "revision": _stable_major_order_revision(doc, assignment),
    }


PHASE_STATUS = {
    0: ("in_progress", "进行中", "IN PROGRESS"),
    2: ("success", "成功", "SUCCESS"),
    3: ("failure", "失败", "FAILURE"),
}


def campaign_zh_document(force=False):
    return get_json(
        site_url("data/campaign_zh.json"),
        ttl=setting("data", "campaign_zh_cache_ttl", default=300),
        force=force,
    )


def official_texts_document(force=False):
    return get_json(
        site_url("data/translated/official_texts.json"),
        ttl=setting("data", "official_texts_cache_ttl", default=300),
        force=force,
    )


def enrich_official_texts(doc, force=False):
    if isinstance(doc.get("official_texts"), dict):
        return doc
    try:
        doc["official_texts"] = official_texts_document(force=force)
    except (OSError, ValueError, TypeError):
        doc["official_texts"] = {}
    return doc


def campaign_image_sources(campaign, phase=None):
    phase = phase or {}
    sources = []
    for source in (
        phase.get("intro_image_local"),
        phase.get("intro_image"),
        campaign.get("banner_image_local"),
        campaign.get("banner_image"),
    ):
        if source and source not in sources:
            sources.append(source)
    return sources


def campaign_view(doc, translations=None):
    """Normalize every active campaign phase and apply campaign_zh by stable IDs."""
    campaign = copy.deepcopy(doc.get("active_campaign") or {})
    if campaign.get("available") is False or not campaign:
        return campaign
    phases = [dict(phase) for phase in campaign.get("phases") or [] if isinstance(phase, dict)]
    needs_translation = (
        not _plain_rich_text(campaign.get("title_cn"))
        or not _plain_rich_text(campaign.get("description_cn"))
        or any(
            not _plain_rich_text(phase.get("title_cn"))
            or not _plain_rich_text(phase.get("briefing_cn"))
            for phase in phases
        )
    )
    if needs_translation and translations is None:
        try:
            translations = campaign_zh_document()
        except (OSError, ValueError, TypeError):
            translations = {}
    if not isinstance(translations, dict):
        translations = {}
    episodes = (translations or {}).get("episodes") or {}
    episode = episodes.get(str(campaign.get("id"))) or episodes.get(
        "title:" + str(campaign.get("title") or "").strip().upper()
    ) or {}
    translated_phases = episode.get("phases") or {}
    campaign["title_cn"] = (
        _plain_rich_text(campaign.get("title_cn"))
        or _plain_rich_text(episode.get("title"))
    )
    campaign["description_cn"] = (
        _plain_rich_text(campaign.get("description_cn"))
        or _plain_rich_text(episode.get("description"))
    )

    normalized = []
    for index, phase in enumerate(phases):
        phase_zh = translated_phases.get(str(phase.get("id"))) or {}
        status_key, status_cn, status_en = PHASE_STATUS.get(
            phase.get("status"),
            (
                str(phase.get("status_key") or "unknown"),
                str(phase.get("status_cn") or "未知"),
                "",
            ),
        )
        if phase.get("status_key"):
            status_key = str(phase["status_key"])
        if phase.get("status_cn"):
            status_cn = str(phase["status_cn"])
        status_en = {
            "in_progress": "IN PROGRESS",
            "success": "SUCCESS",
            "failure": "FAILURE",
        }.get(status_key, status_en)
        outcome = _plain_rich_text(phase.get("outcome"))
        if not outcome:
            outcome = {
                "in_progress": "进行中，尚未发布最终战报",
                "success": "该阶段已完成，战术目标达成。",
                "failure": "该阶段未能达成全部目标。",
            }.get(status_key, "暂无可用战报。")
        reward = phase.get("reward")
        if isinstance(reward, list):
            reward = reward[0] if reward else None
        normalized.append({
            **phase,
            "index": index,
            "title": _plain_rich_text(phase.get("title")),
            "title_cn": (
                _plain_rich_text(phase.get("title_cn"))
                or _plain_rich_text(phase_zh.get("title"))
            ),
            "briefing": _plain_rich_text(phase.get("briefing")),
            "briefing_cn": (
                _plain_rich_text(phase.get("briefing_cn"))
                or _plain_rich_text(phase_zh.get("briefing"))
            ),
            "outcome": outcome,
            "status_key": status_key,
            "status_cn": status_cn,
            "status_en": status_en,
            "reward": reward,
            "image_sources": campaign_image_sources(campaign, phase),
        })
    campaign["phases"] = normalized
    current_index = next(
        (index for index, phase in enumerate(normalized)
         if phase.get("id") is not None
         and str(phase.get("id")) == str(campaign.get("current_phase_id"))),
        None,
    )
    if current_index is None and isinstance(campaign.get("current_phase_index"), int):
        candidate = campaign["current_phase_index"]
        if 0 <= candidate < len(normalized):
            current_index = candidate
    if current_index is None:
        current_index = next(
            (index for index, phase in enumerate(normalized) if phase.get("status") == 0),
            len(normalized) - 1 if normalized else None,
        )
    campaign["current_phase_index"] = current_index
    campaign["phase_count"] = len(normalized)
    wins = sum(1 for phase in normalized if phase.get("status_key") == "success")
    losses = sum(1 for phase in normalized if phase.get("status_key") == "failure")
    running = any(phase.get("status_key") == "in_progress" for phase in normalized)
    episode_running = number(campaign.get("episodes_running")) or 1
    campaign["overall_status"] = (
        "in_progress" if episode_running <= 3 and running
        else "victory" if wins > losses
        else "defeat" if losses > wins
        else "in_progress"
    )
    return campaign


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
