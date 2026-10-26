# -*- coding: utf-8 -*-
"""HD2 统一数据源模块（对齐主站 Jerry114514.github.io）
=====================================================
所有外部数据 URL、抓取、缓存版本与回退优先级集中在本模块，
插件不再各自散落硬编码 URL。纯标准库实现，可离线单测。

数据优先级（详见 docs/数据源对齐与映射表.md）：
  1. 主站 data.json（站点 Actions 每 5 分钟更新，已合并翻译仓库中文）
  2. 主站实测帧 data/recent/（15 分钟粒度，解放速率 / MO 速率实测）
  3. 主站翻译产物（TransNews.json / campaign_zh.json，即翻译仓库推送到 Pages 的副本）
  4. 本地磁盘缓存（带版本号 ver + 时间戳 ts，按 TTL 自然过期，不做永久钉死）
  5. 官方 API、companion、helldivers2.dev（最后兜底，口径可能与主站不一致）

约定：
  - “数据缺失”与“真实数值为 0”严格区分：实测速率拿不到帧/满血顶格/易主时返回 None，
    只有真实差分为 0 才返回 0.0。
  - 所有取值函数都做类型与存在性检查，字段缺失/数组为空/上游超时不会抛到调用方。
"""
import json
import os
import re
import time
from urllib.request import Request, urlopen

# ============ URL 集中管理（可用 config.data.* 覆盖） ============
SITE_BASE = "https://jerry114514.github.io/HD2-Galatic_war-Map"
DATA_JSON_URL = SITE_BASE + "/data.json"
RECENT_BASE = SITE_BASE + "/data/recent"
TRANSNEWS_URL = SITE_BASE + "/data/translated/TransNews.json"
CAMPAIGN_ZH_URL = SITE_BASE + "/data/campaign_zh.json"
TABLES_BASE = SITE_BASE + "/tables"

# 官方 API（2026-08 抓包发现的公开后端，仅需 X-Super-Client 风控头）
OFFICIAL_API_URL = "https://api.live.prod.thehelldiversgame.com"
OFFICIAL_WAR_ID = 801
# 社区聚合（companion）
LIVE_API_URL = "https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live"
EXTENDED_API_URL = "https://cdn.helldiverscompanion.com/live/extendedApiInformation/2days.json"
# 星球名 / 机翻兜底
PLANETS_URL = "https://api.helldivers2.dev/api/v1/planets"
TRANSLATE_URL = "https://api.mymemory.translated.net/get"

try:
    import hd2_config as _cfg

    def _dcfg(*keys, default=None):
        return _cfg.get(*keys, default=default)
except Exception:  # 离线单测/独立脚本场景
    def _dcfg(*keys, default=None):
        return default


def _url(cfg_key_list, default):
    v = _dcfg(*cfg_key_list, default=default)
    return str(v or default)


SITE_BASE = _url(("data", "site_base"), SITE_BASE)
DATA_JSON_URL = _url(("data", "news_json_url"), SITE_BASE + "/data.json")
RECENT_BASE = _url(("data", "recent_base"), SITE_BASE + "/data/recent")
TRANSNEWS_URL = _url(("data", "transnews_url"), SITE_BASE + "/data/translated/TransNews.json")
CAMPAIGN_ZH_URL = _url(("data", "campaign_zh_url"), SITE_BASE + "/data/campaign_zh.json")
TABLES_BASE = _url(("data", "tables_base"), SITE_BASE + "/tables")
OFFICIAL_API_URL = _url(("data", "official_api_url"), OFFICIAL_API_URL)
LIVE_API_URL = _url(("data", "live_api_url"), LIVE_API_URL)
EXTENDED_API_URL = _url(("data", "extended_api_url"), EXTENDED_API_URL)
PLANETS_URL = _url(("data", "planets_url"), PLANETS_URL)
TRANSLATE_URL = _url(("data", "translate_url"), TRANSLATE_URL)

# 官方 API 风控头（无密钥；建议部署者把 super_client 换成自己的 QQ 号）
SUPER_CLIENT = str(_dcfg("data", "super_client", default="YOUR_QQ_NUMBER") or "YOUR_QQ_NUMBER")
OFFICIAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-HD2-Plugin/1.0",
    "Accept": "application/json",
    "X-Super-Client": SUPER_CLIENT,
    "X-Super-Contact": f"{SUPER_CLIENT}@qq.com",
    "Accept-Language": "en-US",
}

# TTL（秒）
SITE_DOC_TTL = 120          # data.json：站点每 5 分钟更新，默认 2 分钟
RECENT_TTL = 300            # 实测帧：15 分钟粒度，5 分钟检查一次足够
TRANSNEWS_TTL = 600
CAMPAIGN_ZH_TTL = 1800
TABLE_TTL = 86400
try:
    SITE_DOC_TTL = max(30, int(_dcfg("data", "news_cache_ttl", default=SITE_DOC_TTL)))
except Exception:
    pass
try:
    RECENT_FRESH_MAX_H = max(0.5, float(_dcfg("data", "recent_fresh_max_hours", default=3)))
except Exception:
    RECENT_FRESH_MAX_H = 3.0

# ============ 通用抓取 ============


def fetch_json(url: str, timeout: int = 20, extra_headers: dict | None = None):
    """HTTP GET + JSON 解析；失败抛异常由调用方决定回退"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-HD2-Plugin/1.0",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_official(path: str, timeout: int = 20, language: str | None = None):
    """官方 API（主源兜底链的最后一环）；可覆盖 Accept-Language，失败抛异常。"""
    headers = dict(OFFICIAL_HEADERS)
    if language:
        headers["Accept-Language"] = language
    return fetch_json(OFFICIAL_API_URL + path, timeout=timeout, extra_headers=headers)


# ============ 缓存（带版本号 ver，避免永久钉死旧响应） ============

_PLUGIN_CACHE_ROOT = ""  # 插件可 set_cache_root("<插件>/cache") 注入


def set_cache_root(path: str) -> None:
    global _PLUGIN_CACHE_ROOT
    _PLUGIN_CACHE_ROOT = path or ""
    if _PLUGIN_CACHE_ROOT:
        os.makedirs(_PLUGIN_CACHE_ROOT, exist_ok=True)


def _cache_root() -> str:
    if _PLUGIN_CACHE_ROOT:
        return _PLUGIN_CACHE_ROOT
    try:
        import hd2_config as _c

        return _c.project_dir()
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


def _cache_file(name: str) -> str:
    return os.path.join(_cache_root(), "cache", name)


def _read_cache(name: str):
    """读缓存文件 -> (data, ver, ts)；无/损坏返回 (None, None, 0)"""
    try:
        p = _cache_file(name)
        if not os.path.exists(p):
            return None, None, 0.0
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("data"), d.get("ver"), float(d.get("ts") or 0)
    except Exception:
        return None, None, 0.0


def _write_cache(name: str, data, ver="") -> None:
    try:
        p = _cache_file(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "ver": str(ver or ""), "data": data}, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


# ============ 类型安全取值 ============


def _num(v):
    """forecast.js number()：None/空串/布尔 -> None，其余转 float 失败 -> None"""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _ms(v):
    """ISO 时间 -> epoch ms；无效返回 None。

    注意：站点/官方时间戳是 UTC（Z 后缀）或带时区（+08:00），
    必须按带时区解析；不能用 time.mktime（会把 Z 串按本地时区解释，
    在 UTC+8 机器上整体偏移 8 小时，污染断更判定/剩余时间/预测窗口）。
    无时区的裸时间串按 UTC 处理。
    """
    if not v:
        return None
    try:
        from datetime import datetime, timezone

        s = str(v).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = datetime.fromisoformat(s[:19])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except Exception:
        return None


def _clamp100(v):
    return max(0.0, min(100.0, v))


def to_ms(v):
    """ISO 时间字符串 -> epoch 毫秒（公开包装，插件与测试共用）；无效返回 None"""
    return _ms(v)


def _norm_owner(v):
    """阵营名归一化为中文（大小写/单复数兼容）"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return {1: "超级地球", 2: "终结族", 3: "机器人", 4: "光能者"}.get(int(v))
    s = str(v).strip().lower()
    return {
        "humans": "超级地球", "human": "超级地球",
        "terminids": "终结族", "terminid": "终结族",
        "automatons": "机器人", "automaton": "机器人",
        "illuminate": "光能者",
    }.get(s, str(v))


# ============ 1) 主站 data.json ============

_SITE_DOC_MEM = {"ts": 0.0, "doc": None}


def site_doc(force: bool = False) -> dict:
    """主站 data.json（内存 + 磁盘缓存，ver=fetchedAt）；失败返回上次成功值或 {}"""
    now = time.time()
    if not force and _SITE_DOC_MEM["doc"] and now - _SITE_DOC_MEM["ts"] < SITE_DOC_TTL:
        return _SITE_DOC_MEM["doc"]
    try:
        doc = fetch_json(DATA_JSON_URL, timeout=15)
        if isinstance(doc, dict) and doc:
            _SITE_DOC_MEM.update({"ts": now, "doc": doc})
            _write_cache("site_doc_cache.json", doc, doc.get("fetchedAt") or "")
            return doc
    except Exception:
        pass
    data, _ver, ts = _read_cache("site_doc_cache.json")
    if isinstance(data, dict) and data:
        _SITE_DOC_MEM.update({"ts": ts, "doc": data})
        return data
    return _SITE_DOC_MEM["doc"] or {}


def _get(doc, key, default=None):
    if not isinstance(doc, dict):
        return default
    v = doc.get(key, default)
    return v if v is not None else default


def _doc(doc):
    """doc=None -> 现抓站点；显式传入的 dict（含空 dict）按调用方意图原样使用"""
    return site_doc() if doc is None else doc


def player_distribution(doc: dict | None = None) -> dict:
    """战区玩家分布 {total, humans, terminids, automatons, illuminate, updatedAt}；缺失返回 {}"""
    d = _get(_doc(doc), "player_distribution", {})
    if not isinstance(d, dict) or not d:
        return {}
    out = {}
    for k in ("total", "humans", "terminids", "automatons", "illuminate"):
        n = _num(d.get(k))
        out[k] = int(n) if n is not None else None
    out["updatedAt"] = d.get("updatedAt") or ""
    return out


def planets_list(doc: dict | None = None) -> list:
    p = _get(_doc(doc), "planets", [])
    return p if isinstance(p, list) else []


def planet_map(doc: dict | None = None) -> dict:
    """index(str) -> 星球信息（owner 归一化为数字枚举，保留 resistance/attacking/regions 等）"""
    out = {}
    for p in planets_list(doc):
        if not isinstance(p, dict) or p.get("index") is None:
            continue
        owner = p.get("currentOwner", p.get("owner", ""))
        if isinstance(owner, str):
            owner = {"humans": 1, "terminids": 2, "automatons": 3, "automaton": 3, "illuminate": 4}.get(
                owner.strip().lower(), owner)
        out[str(p.get("index"))] = {
            "name": p.get("name", ""),
            "sector": p.get("sector", ""),
            "owner": owner,
            "health": _num(p.get("health")),
            "maxHealth": _num(p.get("maxHealth")),
            "players": int(_num((p.get("statistics") or {}).get("playerCount")
                                if isinstance(p.get("statistics"), dict) else p.get("players")) or 0),
            "attacking": bool(p.get("attacking")),
            "resistance": _num(p.get("resistance")),
            "activeEffects": p.get("activeEffects") if isinstance(p.get("activeEffects"), list) else [],
            "event": p.get("event"),
            "regions": p.get("regions") if isinstance(p.get("regions"), list) else [],
        }
    return out


def campaigns(doc: dict | None = None) -> list:
    c = _get(_doc(doc), "campaigns", [])
    return c if isinstance(c, list) else []


def mo_assignment(doc: dict | None = None) -> dict:
    a = _get(_doc(doc), "assignments", {})
    return a if isinstance(a, dict) else {}


def mo_brief_zh(doc: dict | None = None) -> str:
    m = _get(_doc(doc), "major_order", {})
    if not isinstance(m, dict):
        return ""
    return (m.get("translated_brief") or "").strip()


def news_zh_map(doc: dict | None = None) -> dict:
    """{str(id): {"text": 译文, "ver": translated_at}}"""
    doc = _doc(doc)
    out = {}
    for n in _get(doc, "news", []):
        if not isinstance(n, dict) or n.get("id") is None:
            continue
        t = (n.get("translated") or "").strip()
        if t:
            out[str(n["id"])] = {"text": t, "ver": str(n.get("translated_at") or "")}
    return out


def dispatches(doc: dict | None = None) -> list:
    d = _get(_doc(doc), "dispatches", [])
    return d if isinstance(d, list) else []


def active_campaign(doc: dict | None = None) -> dict:
    a = _get(_doc(doc), "active_campaign", {})
    return a if isinstance(a, dict) else {}


def strategic(doc: dict | None = None) -> dict:
    s = _get(_doc(doc), "strategic", {})
    return s if isinstance(s, dict) else {}


def dss(doc: dict | None = None) -> dict:
    d = _get(_doc(doc), "dss", {})
    return d if isinstance(d, dict) else {}


def ownership_history(doc: dict | None = None) -> dict:
    o = _get(_doc(doc), "ownership_history", {})
    return o if isinstance(o, dict) else {}


# ============ 2) 主站实测帧（data/recent/） ============

_RECENT_MEM = {"ts": 0.0, "index": None, "planets": {}, "regions": None, "assignments": None}


def _recent_stale(index: dict) -> bool:
    """帧清单生成时间超过 RECENT_FRESH_MAX_H 视为断更（与主站 rate-source.js 同口径）"""
    gen = _ms((index or {}).get("generatedAt"))
    if gen is None:
        return True
    return (time.time() * 1000 - gen) > RECENT_FRESH_MAX_H * 3600000


def recent_index(force: bool = False) -> dict | None:
    """帧清单 {windowHours, planets, generatedAt}；缺失/断更返回 None（调用方回退推演，不要当 0）"""
    now = time.time()
    if not force and _RECENT_MEM["index"] and now - _RECENT_MEM["ts"] < RECENT_TTL:
        return _RECENT_MEM["index"]
    try:
        idx = fetch_json(RECENT_BASE + "/index.json", timeout=10)
        if isinstance(idx, dict) and idx.get("planets") is not None:
            if _recent_stale(idx):
                return None
            _RECENT_MEM.update({"ts": now, "index": idx})
            _write_cache("recent_index_cache.json", idx, idx.get("generatedAt") or "")
            return idx
    except Exception:
        pass
    data, _ver, ts = _read_cache("recent_index_cache.json")
    if isinstance(data, dict) and not _recent_stale(data):
        _RECENT_MEM.update({"ts": ts, "index": data})
        return data
    return None


def _recent_fetch_frames(url: str, cache_name: str):
    try:
        j = fetch_json(url, timeout=10)
        if isinstance(j, dict) and isinstance(j.get("data"), list):
            _write_cache(cache_name, j, "")
            return j["data"]
    except Exception:
        pass
    data, _ver, _ts = _read_cache(cache_name)
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    return []


def recent_planet_frames(idx: int) -> list:
    """单星球血量帧 [{health,maxHealth,regenPerSecond,players,owner,timestampUtc}]"""
    i = recent_index()
    if not i or int(idx) not in {int(x) for x in (i.get("planets") or [] if isinstance(i.get("planets"), list) else [])}:
        return []
    key = f"p{idx}"
    now = time.time()
    hit = _RECENT_MEM["planets"].get(key)
    if hit and now - hit[0] < RECENT_TTL:
        return hit[1]
    frames = _recent_fetch_frames(f"{RECENT_BASE}/planet_{int(idx)}.json", f"recent_planet_{int(idx)}_cache.json")
    _RECENT_MEM["planets"][key] = (now, frames)
    return frames


def recent_region_frames() -> list:
    """区域帧 data=[{timestampUtc, planetRegions:[...]}]"""
    now = time.time()
    if _RECENT_MEM["regions"] is not None and now - _RECENT_MEM["ts"] < RECENT_TTL:
        return _RECENT_MEM["regions"]
    frames = _recent_fetch_frames(RECENT_BASE + "/planet_regions.json", "recent_regions_cache.json")
    _RECENT_MEM["regions"] = frames
    return frames


def recent_assignment_frames() -> list:
    """MO 进度帧 data=[{timestampUtc, assignments:[{progress,expiresIn,startTime,setting,id32}]}]"""
    now = time.time()
    if _RECENT_MEM["assignments"] is not None and now - _RECENT_MEM["ts"] < RECENT_TTL:
        return _RECENT_MEM["assignments"]
    frames = _recent_fetch_frames(RECENT_BASE + "/assignments.json", "recent_assignments_cache.json")
    _RECENT_MEM["assignments"] = frames
    return frames


# ============ 3) 主站翻译产物（翻译仓库推送到 Pages 的副本） ============

_TRANS_MEM = {"ts": 0.0, "data": None}
_CZHH_MEM = {"ts": 0.0, "data": None}


def transnews(force: bool = False) -> dict:
    """data/translated/TransNews.json（mo_brief/items/strategic/global_events）"""
    now = time.time()
    if _TRANS_MEM["data"] is not None and now - _TRANS_MEM["ts"] < TRANSNEWS_TTL:
        return _TRANS_MEM["data"]
    try:
        j = fetch_json(TRANSNEWS_URL, timeout=15)
        if isinstance(j, dict) and j:
            _TRANS_MEM.update({"ts": now, "data": j})
            _write_cache("transnews_cache.json", j, "")
            return j
    except Exception:
        pass
    data, _ver, ts = _read_cache("transnews_cache.json")
    if isinstance(data, dict) and data:
        _TRANS_MEM.update({"ts": ts, "data": data})
        return data
    return _TRANS_MEM["data"] or {}


def campaign_zh(force: bool = False) -> dict:
    """data/campaign_zh.json（战役剧情中文覆盖表，按 id32）"""
    now = time.time()
    if _CZHH_MEM["data"] is not None and now - _CZHH_MEM["ts"] < CAMPAIGN_ZH_TTL:
        return _CZHH_MEM["data"]
    try:
        j = fetch_json(CAMPAIGN_ZH_URL, timeout=15)
        if isinstance(j, dict) and j:
            _CZHH_MEM.update({"ts": now, "data": j})
            _write_cache("campaign_zh_cache.json", j, "")
            return j
    except Exception:
        pass
    data, _ver, ts = _read_cache("campaign_zh_cache.json")
    if isinstance(data, dict) and data:
        _CZHH_MEM.update({"ts": ts, "data": data})
        return data
    return _CZHH_MEM["data"] or {}


# ============ 主站 tables 文件（本地优先，缺了从 Pages 拉） ============


def table_json(fname: str) -> dict | list:
    """tables/<fname>：本地候选路径优先 -> cache/tables_cache 远程副本 -> Pages 直取"""
    cands = []
    try:
        import hd2_config as _c

        cands.append(os.path.join(_c.project_dir(), "tables", fname))
    except Exception:
        pass
    root = os.environ.get("HD2_PROJECT_DIR", "")
    if root:
        cands.append(os.path.join(root, "tables", fname))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tables", fname))
    for p in cands:
        try:
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            continue
    # 远程副本 / Pages
    remote_name = f"tables_cache_{fname}"
    data, _ver, ts = _read_cache(remote_name)
    if data is not None and time.time() - ts < TABLE_TTL:
        return data
    try:
        j = fetch_json(f"{TABLES_BASE}/{fname}", timeout=10)
        if isinstance(j, (dict, list)) and j:
            _write_cache(remote_name, j, "")
            return j
    except Exception:
        pass
    return data if data is not None else {}


def effect_id_cn() -> dict:
    """效果 ID -> 中文词条（主站 tables/effect_id_cn.json，191 条）"""
    try:
        raw = table_json("effect_id_cn.json")
        if isinstance(raw, dict):
            return {int(k): v for k, v in raw.items() if str(k).lstrip("-").isdigit()}
    except Exception:
        pass
    return {}


# ============ 实测速率（rate-source.js 口径移植） ============

_CAP_TOL = 0.0005   # 满血顶格容差 0.05%
_MIN_SPAN_H = 0.24  # 窗口最短 ≈14 分钟，宁可没有实测也不要假实测


def _is_capped(frame: dict, fallback_mh: float | None = None) -> bool:
    """满血顶格判定；区域帧可能缺 maxHealth 字段，用星球/区域传入的满血兜底"""
    mh = _num((frame or {}).get("maxHealth"))
    if not (mh and mh > 0):
        mh = fallback_mh
    hp = _num((frame or {}).get("health"))
    return mh is not None and mh > 0 and hp is not None and hp >= mh * (1 - _CAP_TOL)


def net_from_frames(frames: list, max_health: float, window_h: float) -> dict | None:
    """血量帧序列 -> {net(%/h 正=推进), hours, first, last, owner_changed, capped}；帧不足返回 None"""
    pts = []
    for f in frames or []:
        if not isinstance(f, dict):
            continue
        t = _ms(f.get("timestampUtc"))
        if t is None:
            continue
        pts.append((t, f))
    pts.sort(key=lambda x: x[0])
    if len(pts) < 2 or not (max_health and max_health > 0):
        return None
    last_t, last_f = pts[-1]
    earliest = last_t - window_h * 3600000.0
    first_t, first_f = pts[0]
    for t, f in pts:
        if t >= earliest:
            first_t, first_f = t, f
            break
    hours = (last_t - first_t) / 3600000.0
    if not (hours >= _MIN_SPAN_H):
        return None
    dh = (_num(last_f.get("health")) or 0) - (_num(first_f.get("health")) or 0)
    return {
        "net": -dh / max_health * 100.0 / hours,
        "hours": hours,
        "first": first_f,
        "last": last_f,
        "owner_changed": first_f.get("owner") != last_f.get("owner"),
        "capped": _is_capped(first_f, max_health) and _is_capped(last_f, max_health),
        "last_age_h": (time.time() * 1000 - last_t) / 3600000.0,
    }


def planet_measured(planet: dict, window_h: float | None = None) -> dict | None:
    """星球实测速率：{net, gross, res, res_eq, hours, measured: True}；不可实测返回 None。

    net  = -(末帧血量-首帧血量)/满血*100/小时
    gross = net + 抵抗度（resistance 为站点预计算值，%/h）
    res_eq = resistance * 满血 / 1e6（按 100 万血基准）
    """
    p = planet or {}
    mh = _num(p.get("maxHealth"))
    if not (mh and mh > 0):
        return None
    idx = p.get("index")
    if idx is None:
        return None
    frames = recent_planet_frames(int(idx))
    if not frames:
        return None
    n = net_from_frames(frames, mh, window_h if window_h else float(
        (recent_index() or {}).get("windowHours") or 1))
    if not n:
        return None
    if n["owner_changed"] or n["capped"]:
        return None   # 满血顶格 Δ=0 不代表净速率 0，必须交推演兜底
    if n["last_age_h"] > RECENT_FRESH_MAX_H:
        return None   # 末帧超过断更阈值：旧差分不得冒充当前实测
    res = _num(p.get("resistance")) or 0.0
    return {
        "net": n["net"], "gross": n["net"] + res, "res": res,
        "res_eq": res * mh / 1e6,
        "hours": n["hours"], "measured": True,
    }


def region_measured(planet_index: int, region: dict, window_h: float | None = None) -> dict | None:
    """区域实测（区域帧 owner 为数字，只比窗口内是否易主）"""
    r = region or {}
    mh = _num(r.get("maxHealth"))
    if not (mh and mh > 0):
        return None
    ri = r.get("regionIndex")
    if ri is None:
        return None
    rows = []
    for frame in recent_region_frames():
        if not isinstance(frame, dict):
            continue
        t = _ms(frame.get("timestampUtc"))
        for rr in frame.get("planetRegions") or []:
            if isinstance(rr, dict) and rr.get("planetIndex") == planet_index and rr.get("regionIndex") == ri:
                row = dict(rr)
                row["timestampUtc"] = frame.get("timestampUtc")
                rows.append((t, row))
    if not rows:
        return None
    rows.sort(key=lambda x: (x[0] is None, x[0]))
    frames = [r0 for _t, r0 in rows]
    n = net_from_frames(frames, mh, window_h if window_h else float(
        (recent_index() or {}).get("windowHours") or 1))
    if not n:
        return None
    if n["owner_changed"] or n["capped"]:
        return None
    if n["last_age_h"] > RECENT_FRESH_MAX_H:
        return None   # 末帧超过断更阈值：旧差分不得冒充当前实测
    regen = _num(n["last"].get("regerPerSecond")) or 0.0
    return {
        "net": n["net"], "gross": n["net"] + (regen * 3600 / mh * 100), "res": regen * 3600 / mh * 100,
        "res_eq": regen * 3600 / 1e6 * 100,
        "hours": n["hours"], "measured": True,
        "players": _num(n["last"].get("players")),
        "is_available": n["last"].get("isAvailable"),
    }


def region_meta(planet_index: int, region: dict) -> dict | None:
    """区域「当前值」（不差分）：末帧抵抗度/玩家/开放状态；超过 3 小时无新帧返回 None"""
    r = region or {}
    mh = _num(r.get("maxHealth"))
    ri = r.get("regionIndex")
    if not (mh and mh > 0) or ri is None:
        return None
    rows = []
    for frame in recent_region_frames():
        if not isinstance(frame, dict):
            continue
        t = _ms(frame.get("timestampUtc"))
        if t is None:
            continue
        for rr in frame.get("planetRegions") or []:
            if isinstance(rr, dict) and rr.get("planetIndex") == planet_index and rr.get("regionIndex") == ri:
                rows.append((t, rr))
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    t, last = rows[-1]
    if (time.time() * 1000 - t) > 3 * 3600000:
        return None
    regen = _num(last.get("regerPerSecond")) or 0.0
    return {
        "res": regen * 3600 / mh * 100,
        "res_eq": regen * 3600 / 1e6 * 100,
        "regen_per_second": regen,
        "players": _num(last.get("players")),
        "is_available": last.get("isAvailable"),
        "availability_factor": _num(last.get("availabilityFactor")),
        "timestamp_utc": last.get("timestampUtc"),
    }


# ============ 区域开放状态（regions.js 口径移植） ============

REGION_BASE_HP = {"Settlement": 100000, "Town": 200000, "City": 400000, "MegaCity": 600000}


def _region_is_human(owner) -> bool:
    if owner == 1:
        return True
    return str(owner).strip().lower() in ("humans", "human", "1")


def region_names(planet_index: int) -> list:
    """区域名称表（tables/planet_regions.json + planet_regions_zh.json）
    -> [{name_en, name_cn, size, region_type, desc}]，数组顺序即 regionIndex"""
    try:
        tbl = table_json("planet_regions.json")
        planets = tbl.get("planets") if isinstance(tbl, dict) else None
        entry = (planets or {}).get(str(planet_index)) or {}
        zh_tbl = table_json("planet_regions_zh.json")
        zh_map = ((zh_tbl.get("planets") or {}).get(str(planet_index))) or {} if isinstance(zh_tbl, dict) else {}
        out = []
        for r in entry.get("regions") or []:
            if not isinstance(r, dict):
                continue
            name_en = r.get("name") or ""
            out.append({
                "name_en": name_en,
                "name_cn": zh_map.get(name_en, ""),
                "size": r.get("size") or "",
                "region_type": r.get("region_type") or "",
                "desc": r.get("desc") or "",
            })
        return out
    except Exception:
        return []


def region_model(planet: dict, region: dict | None, name_meta: dict | None = None,
                 doc: dict | None = None, meta: dict | None = None) -> dict:
    """单个区域的开放状态模型（regions.js model() 同口径）：
    available/isAvailable -> 已开放/未开放；无区域且我方无防御 -> 无区域战役；
    开放条件 = 入侵进度/主星球解放 ≥ (1-availabilityFactor)*100；
    解放贡献 = 区域满血×伤害倍率 / 母星球血池 ×100。"""
    p = planet or {}
    d = doc if isinstance(doc, dict) and doc else site_doc()
    meta = meta or {}
    pi = p.get("index")
    campaigns_list = d.get("campaigns") or []
    camp = next((c for c in campaigns_list
                 if isinstance(c, dict) and (c.get("planet") or {}).get("index") == pi), None)
    event = None
    for ev in d.get("planet_events") or []:
        if isinstance(ev, dict) and ev.get("planetIndex") == pi and ev.get("eventType") == 1:
            event = ev
            break
    defense = bool(event) or is_defense(camp)
    flags = _num((region or {}).get("flags")) or 0
    factory = str(meta.get("region_type", "")).lower() == "megafactory" or (int(flags) & 1) != 0
    available = region.get("isAvailable") if isinstance(region, dict) and isinstance(region.get("isAvailable"), bool) else None
    ours = bool(region) and _region_is_human((region or {}).get("owner"))
    inactive = not region and _region_is_human(p.get("currentOwner")) and not defense
    factor = _num((region or {}).get("availabilityFactor"))
    if factor is None:
        factor = _num(meta.get("availabilityFactor"))
    threshold = _clamp100((1 - factor) * 100) if factor is not None else None
    parent_max = None
    if defense:
        parent_max = _num((event or {}).get("maxHealth"))
        if parent_max is None:
            parent_max = _num((camp or {}).get("maxHealth"))
    if parent_max is None:
        parent_max = _num(p.get("maxHealth"))
    region_max = _num((region or {}).get("maxHealth"))
    if region_max is None and not factory:
        region_max = REGION_BASE_HP.get(meta.get("size") or "")
    multiplier = _num((region or {}).get("damageMultiplier"))
    if multiplier is None:
        multiplier = 1.5
    estimated = _num((region or {}).get("damageMultiplier")) is None or _num((region or {}).get("maxHealth")) is None
    contribution = None
    if parent_max and parent_max > 0 and region_max and region_max > 0:
        contribution = region_max * multiplier / parent_max * 100
    planet_pct = None
    hp, mh = _num(p.get("health")), _num(p.get("maxHealth"))
    if hp is not None and mh and mh > 0:
        planet_pct = _clamp100((1 - hp / mh) * 100)
    status = "已开放" if available is True else "未开放" if available is False else ("无区域战役" if inactive else "开放状态未知")
    if ours or inactive:
        status += " · 已解放"
    condition = ""
    if available is not True:
        if ours or inactive:
            condition = "暂无区域战役"
        elif defense:
            condition = "等待开放，阈值未知" if threshold is None else f"入侵进度 ≥{threshold:.2f}%"
        elif threshold is not None:
            condition = f"主星球解放 ≥{threshold:.2f}%"
            if planet_pct is not None and planet_pct >= threshold:
                condition += " · 待开放"
        else:
            condition = "条件未知"
    return {
        "available": available, "status": status, "condition": condition,
        "threshold": threshold, "contribution": contribution, "multiplier": multiplier,
        "estimated": estimated, "defense": defense,
        "target": "主星球防守进度" if defense else "主星球解放度",
        "name_en": (name_meta or {}).get("name_en", ""),
        "name_cn": (name_meta or {}).get("name_cn", ""),
        "size": (name_meta or {}).get("size", ""),
        "owner": (region or {}).get("owner"),
        "players": _num((region or {}).get("players")),
    }


# ============ MO 预测（forecast.js 口径移植） ============

MO_TASK_HOLD_TYPE = 13
MO_TASK_NUMERIC_TYPES = {2, 3, 9, 11, 15}


def order_window(assignment: dict, now_ms: float | None = None) -> dict | None:
    """指令时间窗 {start, end, now, hours, expected}；缺 deadline/startedAt 返回 None"""
    a = assignment or {}
    end = _ms(a.get("deadline") or a.get("expiration"))
    now = now_ms if now_ms is not None else _ms(a.get("serverTime"))
    if now is None:
        now = time.time() * 1000.0
    if end is None:
        return None
    start = _ms(a.get("startedAt") or a.get("startDate"))
    if start is None:
        # 兜底：开局播报 dispatch（NEW MAJOR ORDER 开头且不晚于 end-14 天内）
        now_s = now / 1000.0
        for d in dispatches():
            msg = str(d.get("message") or "")
            if not re.match(r"^\s*(?:<i=\d+>)?\s*NEW MAJOR ORDER", msg, re.IGNORECASE):
                continue
            pt = _ms(d.get("published"))
            if pt is not None and pt / 1000.0 <= now_s and (end - pt) <= 14 * 86400000:
                start = pt
                break
    if start is None or start > now or end <= start:
        return None
    return {
        "start": start, "end": end, "now": now,
        "hours": (end - start) / 3600000.0,
        "expected": _clamp100((now - start) / (end - start) * 100.0),
    }


def task_state(task: dict, current, window: dict | None, recent_rate: float | None = None) -> dict:
    """单任务预测（与 forecast.js task() 同口径）：
    rate = current/全程已历小时（含缓存开始前的时段）；type 13=驻守，11=原始进度是占领标记"""
    g = _num((task or {}).get("goal"))
    c = _num(current)
    t_type = _num((task or {}).get("type"))
    if g is None or g <= 0 or c is None:
        return {"state": "unknown"}
    if t_type == MO_TASK_HOLD_TYPE:
        return {"state": "hold", "satisfied": c >= g, "expired": bool(window) and window["now"] >= window["end"]}
    if t_type is not None and int(t_type) not in MO_TASK_NUMERIC_TYPES:
        return {"state": "satisfied" if c >= g else "stateful"}
    if c >= g:
        return {"state": "complete"}
    if not window:
        return {"state": "unknown"}
    elapsed_h = (window["now"] - window["start"]) / 3600000.0
    remaining_h = max(0.0, (window["end"] - window["now"]) / 3600000.0)
    rate = (c / elapsed_h) if elapsed_h > 0 else None
    projected = _clamp100((c + rate * remaining_h) / g * 100.0) if rate is not None else None
    eta_h = ((g - c) / rate) if (rate is not None and rate > 0) else None
    return {
        "state": "expired" if remaining_h <= 0 else "active",
        "expected": window["expected"],
        "delta": c / g * 100.0 - window["expected"],
        "rate": rate, "projected": projected, "eta_h": eta_h, "remaining_h": remaining_h,
        "win": (eta_h is not None and eta_h <= remaining_h) if eta_h is not None else None,
        "recent_rate": recent_rate,
        "recent_projected": (_clamp100((c + recent_rate * remaining_h) / g * 100.0)
                             if (recent_rate is not None and remaining_h > 0) else None),
    }


def is_defense(camp: dict) -> bool:
    c = camp or {}
    if str(c.get("campaignType", "")).lower() == "defense":
        return True
    if c.get("eventType") == 1:
        return True
    return str(c.get("type")) in ("defense", "1", "4")


def defense_model(camp: dict) -> dict:
    """防御战赛跑模型（forecast.js defense()）：实测防守速率优先，缺失用开战以来平均"""
    c = camp or {}
    dprog = _num(c.get("defendersProgress"))
    aprog = _num(c.get("attackersProgress"))
    a_rate = _num(c.get("attackersRate"))
    d_rate = _num(c.get("defendersRate"))
    secs = _num(c.get("remainingTime"))
    remaining_h = max(0.0, secs / 3600.0) if secs is not None else None
    elapsed_h = (aprog / a_rate) if (a_rate and a_rate > 0 and aprog is not None) else None
    # 0 是实测停滞，不是“用历史正值替换”的许可
    rate = max(0.0, d_rate) if d_rate is not None else ((dprog / elapsed_h) if (elapsed_h and elapsed_h > 0 and dprog is not None) else None)
    source = "实测防守速率" if d_rate is not None else "开战以来平均防守速率"
    expected = _clamp100(aprog) if aprog is not None else None
    if dprog is None or remaining_h is None or rate is None:
        projected = None
    else:
        projected = _clamp100(dprog + rate * remaining_h)
    if dprog is not None and dprog >= 100:
        eta_h = 0.0
    elif dprog is not None and rate is not None and rate > 0:
        eta_h = (100 - dprog) / rate
    else:
        eta_h = None
    win = None
    if dprog is not None:
        if dprog >= 100:
            win = True
        elif projected is not None:
            win = (eta_h is not None and eta_h <= remaining_h) if eta_h is not None else False
    required_rate = (max(0.0, 100 - dprog) / remaining_h) if (dprog is not None and remaining_h and remaining_h > 0) else None
    return {
        "def": dprog, "atk": aprog, "rate": rate, "source": source,
        "expected": expected, "projected": projected, "eta_h": eta_h,
        "remaining_h": remaining_h, "win": win,
        "delta": (dprog - expected) if (dprog is not None and expected is not None) else None,
        "required_rate": required_rate,
    }


def reinforcement(doc: dict, planet: dict, camp: dict | None = None, efficiency_k: float = 90.0) -> dict:
    """增援占比模型（forecast.js reinforcement()）：守住/持续推进需要多少在线兵力占比"""
    d = doc or {}
    p = planet or {}
    total = _num((d.get("player_distribution") or {}).get("total"))
    players = _num(p.get("players"))
    mode = "defense" if is_defense(camp) else "liberation"
    share = (players / total * 100.0) if (total and total > 0 and players is not None and 0 <= players <= total) else None
    unknown = {"state": "unknown", "mode": mode, "current_share": share}
    if mode == "defense":
        f = defense_model(camp)
        if f["def"] is not None and f["def"] >= 100:
            return {"state": "complete", "mode": mode, "current_share": share}
        if f["remaining_h"] == 0:
            return {"state": "expired", "mode": mode, "current_share": share}
        required_rate = f["required_rate"]
        max_health = None
        for ev in d.get("planet_events") or []:
            if isinstance(ev, dict) and ev.get("planetIndex") == p.get("index") and ev.get("eventType") == 1:
                max_health = _num(ev.get("maxHealth"))
                break
        measured_rate = _num((camp or {}).get("defendersRate"))
    else:
        if p.get("currentOwner") == "Humans" or (_num(p.get("health")) == 0 and (_num(p.get("maxHealth")) or 0) > 0):
            return {"state": "complete", "mode": mode, "current_share": share}
        required_rate = _num(p.get("resistance"))
        max_health = _num(p.get("maxHealth"))
        measured_rate = None
    if share is None or required_rate is None or required_rate < 0:
        return unknown
    if measured_rate is not None:
        if not (measured_rate > 0) or not (share > 0):
            return unknown
        rate_per_share = measured_rate / share
        source = "实测"
    else:
        impact = _num(d.get("impactMultiplier"))
        if not (impact and impact > 0) or not (max_health and max_health > 0):
            return unknown
        rate_per_share = total * impact * efficiency_k / max_health
        source = "推演"
    if not (rate_per_share > 0):
        return unknown
    required_share = required_rate / rate_per_share
    strict = mode == "liberation"   # 打平抵抗度只能僵持，解放必须严格超过
    return {
        "state": ("impossible" if required_share > 100 or (strict and required_share >= 100) else "active"),
        "mode": mode, "current_share": share, "required_share": required_share,
        "required_rate": required_rate, "rate_per_share": rate_per_share,
        "strict": strict, "source": source,
        "sufficient": (share > required_share) if strict else (share >= required_share),
        "gap": max(0.0, required_share - share),
    }


# ============ 星图归属变化（ownership-changes.js 口径移植） ============

_FACTION_CN = {
    "Humans": "超级地球", "Terminids": "终结族",
    "Automaton": "机器人", "Illuminate": "光能者",
}


def faction_cn(name) -> str | None:
    if isinstance(name, (int, float)) or (isinstance(name, str) and name.isdigit()):
        return _norm_owner(int(name)) if str(name).lstrip("-").isdigit() else None
    return _FACTION_CN.get(name) or _norm_owner(name)


def ownership_changes(hours: float = 24, now_ms: float | None = None) -> dict | None:
    """近 N 小时归属变化；无记录返回空 rows（非 None）。None=历史结构缺失"""
    h = ownership_history()
    if not isinstance(h, dict) or not isinstance(h.get("events"), list):
        return None
    now = now_ms if now_ms is not None else time.time() * 1000.0
    cutoff = now - hours * 3600000.0
    rows = {}
    n_changes = n_def = 0
    items = []
    for ev in h.get("events") or []:
        if not isinstance(ev, dict):
            continue
        t = _ms(ev.get("detectedAt"))
        if t is None or not (cutoff <= t <= now):
            continue
        if not (isinstance(ev.get("planetIndex"), int) and ev.get("fromOwner") in _FACTION_CN
                and ev.get("toOwner") in _FACTION_CN and ev.get("fromOwner") != ev.get("toOwner")):
            continue
        items.append((t, ev, False))
        n_changes += 1
    for ev in h.get("defenseEvents") or []:
        if not isinstance(ev, dict):
            continue
        t = _ms(ev.get("detectedAt"))
        if t is None or not (cutoff <= t <= now) or not isinstance(ev.get("planetIndex"), int):
            continue
        items.append((t, ev, True))
        n_def += 1
    items.sort(key=lambda x: x[0])
    for t, ev, is_def in items:
        pi = ev["planetIndex"]
        row = rows.setdefault(pi, {"planet_index": pi, "paths": [], "victories": [],
                                   "changes": 0, "uncertain": False, "latest": ev.get("detectedAt")})
        if is_def:
            row["victories"].append(ev)
        else:
            path = row["paths"][-1] if row["paths"] else None
            if path and path[-1] == ev["fromOwner"]:
                path.append(ev["toOwner"])
            else:
                row["paths"].append([ev["fromOwner"], ev["toOwner"]])
            row["changes"] += 1
        prev_t = _ms(ev.get("previousSeenAt"))
        if prev_t is not None and prev_t < cutoff:
            row["uncertain"] = True
    def _coverage(started):
        st = _ms(started)
        up = _ms(h.get("updatedAt"))
        if st is None or up is None:
            return 0.0
        return max(0.0, min(hours, 48, (min(now, up) - max(cutoff, st)) / 3600000.0))
    return {
        "rows": sorted(rows.values(), key=lambda r: str(r["latest"]) or "", reverse=True),
        "changes": n_changes, "defenses": n_def,
        "coverage_hours": _coverage(h.get("startedAt")),
        "defense_coverage_hours": _coverage(h.get("defenseStartedAt")),
        "stale": (not h.get("updatedAt")) or (now - (_ms(h.get("updatedAt")) or 0) > 20 * 60000),
    }


# ============ DSS 捐献（dss-donations.js 口径移植） ============

DSS_RESOURCES = {
    3992382197: "普通样本",
    2985106497: "稀有样本",
    3608481516: "申购点",
}
TACTICAL_STATUS_CN = {0: "离线", 1: "投票中", 2: "已激活", 3: "恢复中"}


def dss_tactical_actions(d: dict | None = None) -> list:
    dd = d or dss()
    t = dd.get("tacticalActions")
    return t if isinstance(t, list) else []


def dss_donation_pct(action: dict) -> float | None:
    """捐献进度百分比；cost 缺失/非法返回 None（区分“尚未开始筹集”）"""
    cost = ((action or {}).get("cost") or [None])[0]
    if not isinstance(cost, dict):
        return None
    cur = _num(cost.get("current"))
    tgt = _num(cost.get("target"))
    if cur is None or tgt is None or tgt <= 0:
        return None
    return min(100.0, max(0.0, cur / tgt * 100.0))


def dss_resource_lines(action: dict) -> list:
    """捐献资源说明（每人每周期最多捐赠 X 资源名）"""
    lines = []
    for cost in (action or {}).get("cost") or []:
        if not isinstance(cost, dict):
            continue
        res = DSS_RESOURCES.get(cost.get("itemMixId"))
        if not res:
            lines.append("捐赠资源待确认")
            continue
        amount = _num(cost.get("maxDonation"))
        period = _num(cost.get("maxDonationPeriodSec"))
        if not amount or not period or amount <= 0 or period <= 0:
            lines.append(f"捐赠资源：{res}")
            continue
        hours = period / 3600.0
        cycle = f"{int(hours)}小时" if float(hours).is_integer() else f"{int(period)}秒"
        lines.append(f"每人每{cycle}最多捐赠 {int(amount):,} {res}")
    return lines


def dss_snapshot_label(d: dict | None = None) -> str:
    """捐献数据时点标签；无有效时间返回“捐赠数据时间未知”"""
    dd = d or dss()
    ts = dd.get("donationUpdatedAtMs", dd.get("clientTimeMs"))
    t = _num(ts)
    if not t or t <= 0:
        return "捐赠数据时间未知"
    try:
        lt = time.localtime(t / 1000.0)
        label = time.strftime("%m-%d %H:%M", lt)
    except Exception:
        return "捐赠数据时间未知"
    suffix = "（上次成功快照）" if dd.get("donationSource") == "snapshot" else ""
    return f"捐赠数据截至 {label}{suffix}"


# ============ 格式化工具（插件与测试共用） ============


def fmt_big_num(n) -> str:
    """1,000,000 -> 100万；125,000,000 -> 12.5亿（亿=1e8，修正旧实现的 10 倍缩小）"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.2f}".rstrip("0").rstrip(".") + "亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}".rstrip("0").rstrip(".") + "万"
    return str(n)


def fmt_seconds(secs) -> str:
    secs = max(int(secs or 0), 0)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d > 0:
        return f"{d}天{h}小时"
    if h > 0:
        return f"{h}小时{m}分"
    return f"{m}分钟"


def fmt_utc8(iso_time: str) -> str:
    """UTC ISO -> UTC+8 的 MM-DD HH:MM；失败返回原样"""
    if not iso_time or len(str(iso_time)) < 16:
        return iso_time or ""
    try:
        from datetime import datetime, timedelta, timezone

        dt = datetime.fromisoformat(str(iso_time).replace("Z", "+00:00"))
        dt = dt.astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return str(iso_time)[11:16] or ""
