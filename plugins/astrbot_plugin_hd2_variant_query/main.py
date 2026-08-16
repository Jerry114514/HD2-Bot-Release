import io
import re
import time
import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# ============ 变种词库（与星球信息插件一致）============
VARIANT_CN = {
    "SPORE BURST STRAIN": "孢裂变种",
    "THE INCINERATION CORPS": "炽灼部队",
    "CYBORGS": "生化人",
    "MINDLESS MASSES": "无脑群氓",
    "DRAGONROACH": "蟑龙",
    "HIVE LORD": "霸王虫",
    "PREDATOR STRAIN": "掠食变种",
    "APPROPRIATORS": "占领者",
}

# 变种扫描缓存（避免每次查询重复抓取）
_scan_cache = {"ts": 0, "data": {}}


# 星图对照表路径（配置化）：环境变量 HD2_PROJECT_DIR 指向项目根，或 HD2_TABLES_DIR 直接指定
import os as _os
import sys as _sys

_ROOT = _os.environ.get("HD2_PROJECT_DIR")
if _ROOT and _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
try:
    import hd2_config as _cfg
    _cfg_ok = True
except Exception:
    _cfg_ok = False


def _tables_dir() -> str:
    """表目录：hd2_config 优先；无配置时回退 插件目录 tables 副本 -> 项目根 tables -> 环境变量"""
    if _cfg_ok:
        try:
            p = _cfg.get("paths", "starmap", default="tables/星图对照表_修正版.md")
            return _os.path.dirname(_cfg.resolve(p))
        except Exception:
            pass
    # 无配置：优先插件目录 tables 副本（部署兜底），其次项目根 tables/（同仓发布）
    here = _os.path.dirname(_os.path.abspath(__file__))
    for cand in (os.path.join(here, "tables"), os.path.join(here, "..", "..", "tables")):
        cand = os.path.abspath(cand)
        if os.path.isdir(cand):
            return cand
    return _os.environ.get("HD2_TABLES_DIR", ".")


# 监控埋点（hd2_monitor）：记录执行状态
_MON_PARENT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _MON_PARENT not in _sys.path:
    _sys.path.insert(0, _MON_PARENT)
try:
    from astrbot_plugin_hd2_monitor.main import hd2_log as _log
except Exception:
    def _log(*a, **k):
        pass

# 公共库（hd2_common）：共享常量/抓取/表加载/译名映射
from astrbot_plugin_hd2_common.main import (
    fetch_json as _fetch_json,
    fetch_official as _fetch_official,
    load_effect_id_cn as _load_effect_id_cn,
    OFFICIAL_API_URL,
    LIVE_API_URL,
    OFFICIAL_WAR_ID,
    PLANETS_URL,
)


def _gid(event):
    try:
        return int(getattr(event.message_obj, "group_id", 0) or 0)
    except Exception:
        return 0


def _uid(event):
    try:
        return int(event.get_sender_id() or 0)
    except Exception:
        return 0


def _uname(event):
    try:
        s = event.get_sender()
        return (getattr(s, "card", "") or getattr(s, "nickname", "") or "")
    except Exception:
        return ""


STARMAP_CANDIDATES = [
    _os.path.join(_tables_dir(), "星图对照表_修正版.md"),
    _os.path.join(_tables_dir(), "星图对照表.md"),
]
_planet_cn_map = None


def _load_planet_cn_map() -> dict:
    """从星图对照表加载 英文->中文 映射"""
    global _planet_cn_map
    if _planet_cn_map is not None:
        return _planet_cn_map
    m = {}
    import os as _os
    for p in STARMAP_CANDIDATES:
        if not _os.path.exists(p):
            continue
        try:
            with io.open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("|"):
                        continue
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if len(cells) < 3:
                        continue
                    en, cn = cells[1], cells[2]
                    if en and cn and en not in ("英文", "---"):
                        m[en.upper()] = cn
            break
        except Exception:
            continue
    _planet_cn_map = m
    return m


async def _scan_all_variants() -> dict:
    """扫描战役星球行动变量（官方 planetActiveEffects 优先，Playwright 兜底）
    返回 {英文变种关键词: [星球名, ...]}，结果缓存 300 秒。"""
    now = time.time()
    if _scan_cache["data"] and now - _scan_cache["ts"] < 300:
        return _scan_cache["data"]

    result = {kw: [] for kw in VARIANT_CN}
    try:
        # ---- 官方源 ----
        st = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status")
        camps = st.get("campaigns") or []
        camp_idx = list(dict.fromkeys(c.get("planetIndex") for c in camps))
        camp_set = set(camp_idx)
        eff_cn = _load_effect_id_cn()
        # 中文词条 -> VARIANT_CN key（忽略空格差异）
        cn2key = {v.replace(" ", "").upper(): k for k, v in VARIANT_CN.items()}
        # 星球名
        try:
            planets = _fetch_json(PLANETS_URL, extra_headers={
                "X-Super-Client": "astrbot-hd2-plugin", "X-Super-Contact": "https://github.com/astrbot"})
            name_map = {p["index"]: p["name"] for p in planets}
        except Exception:
            name_map = {}
        # 官方 planetActiveEffects：战役星球上的效果 -> 变种
        for p in st.get("planetActiveEffects") or []:
            idx = p.get("index")
            if idx not in camp_set:
                continue
            eid = p.get("galacticEffectId")
            cn = eff_cn.get(eid)
            if not cn:
                continue
            key = cn2key.get(cn.replace(" ", "").upper())
            if key:
                nm = name_map.get(idx, f"星球{idx}")
                if nm not in result[key]:
                    result[key].append(nm)
        _scan_cache["ts"] = now
        _scan_cache["data"] = result
        return result
    except Exception as e:
        logger.warning(f"[HD2-变种] 官方扫描失败，回退页面抓取: {e}")

    # ---- 社区备选（Playwright 页面抓取）----
    from playwright.async_api import async_playwright

    planets = _fetch_json("https://api.helldivers2.dev/api/v1/planets", extra_headers={
        "X-Super-Client": "astrbot-hd2-plugin", "X-Super-Contact": "https://github.com/astrbot"})
    name_map = {p["index"]: p["name"] for p in planets}

    live = _fetch_json(LIVE_API_URL)
    camps = live["warStatus"].get("campaigns") or []
    camp_idx = list(dict.fromkeys(c.get("planetIndex") for c in camps))

    async def check_one(idx):
        slug = name_map.get(idx, "").lower().replace(" ", "_")
        if not slug:
            return
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(f"https://helldiverscompanion.com/#hellpad/planets/{slug}", wait_until="domcontentloaded", timeout=45000)
                    try:
                        await page.wait_for_function("() => document.body.innerText.includes('OPERATIONAL PARAMETERS')", timeout=20000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(600)
                    body = await page.evaluate("() => document.body.innerText")
                finally:
                    await browser.close()
            up = body.upper()
            op_start = up.find("OPERATIONAL PARAMETERS")
            poi_start = up.find("POINTS OF INTEREST", op_start)
            if op_start == -1:
                return
            end = poi_start if poi_start > op_start else op_start + 800
            op_zone = up[op_start:end]
            for kw in VARIANT_CN:
                if kw in op_zone:
                    result[kw].append(name_map.get(idx, slug))
        except Exception:
            pass

    sem = asyncio.Semaphore(3)

    async def limited(i):
        async with sem:
            await check_one(i)

    asyncio.run(asyncio.gather(*[limited(i) for i in camp_idx]))

    _scan_cache["ts"] = now
    _scan_cache["data"] = result
    return result

class Hd2VariantQueryPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    def _is_at_me(self, event: AstrMessageEvent) -> bool:
        """检查消息中是否 @ 了机器人自己（或引用了机器人）"""
        try:
            from astrbot.core.message.components import At, Reply

            self_id = str(event.get_self_id())
            for m in event.get_messages():
                if isinstance(m, At) and str(getattr(m, "qq", "")) == self_id:
                    return True
                if isinstance(m, Reply) and str(getattr(m, "sender_id", "")) == self_id:
                    return True
        except Exception:
            pass
        text = event.message_str or ""
        if f"at,qq={self_id}" in text or f"at,qq={self_id}]" in text:
            return True
        if re.search(r"@[\w.\-]+", text):
            return True
        return False

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event: AstrMessageEvent):
        """变种查询触发：@ + 变种名"""
        text = (event.message_str or "").strip().lower()
        if not text:
            return
        # 清洗 @ 前缀
        text_clean = re.sub(r"^@[\w.\-]+\s*", "", text).strip()
        if not text_clean:
            return
        # 仅响应 / 指令
        if not text_clean.startswith("/"):
            return
        text_clean = text_clean[1:].strip()
        if not text_clean:
            return
        # 匹配变种中文名（支持 变种名 / 变种名信息 / 查xx变种）
        matched_kw = None
        for kw, cn in VARIANT_CN.items():
            if cn in text_clean:
                matched_kw = kw
                break
        if not matched_kw:
            # 可能是查询词含「变种」但没匹配到词库 -> 未查询到
            if "变种" in text_clean or "变异" in text_clean:
                _log("done", plugin="variant_query", text=text_clean, group=_gid(event), user=_uid(event), detail="未匹配到词库")
                yield event.plain_result("未查询到该变种信息。切莫粗心大意。")
                event.stop_event()
            return
        _t0 = time.time()
        _log("matched", plugin="variant_query", text=text_clean, group=_gid(event), user=_uid(event), name=_uname(event))
        try:
            scan = await _scan_all_variants()
            planets = scan.get(matched_kw, [])
            cn_name = VARIANT_CN[matched_kw]
            if not planets:
                yield event.plain_result("未查询到该变种信息。切莫粗心大意。")
            else:
                # 星球名经星图对照表替换为中文
                cn_map = _load_planet_cn_map()
                lines = ["目前的变种信息："]
                for p in planets:
                    p_cn = cn_map.get(p.upper(), p)
                    lines.append(f"{p_cn}——{cn_name}")
                yield event.plain_result("\n".join(lines))
            _log("done", plugin="variant_query", text=text_clean, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail=f"{cn_name}: {len(planets)} 颗星球")
        except Exception as e:
            logger.warning(f"[HD2-VariantQuery] 查询失败: {e}")
            _log("failed", plugin="variant_query", text=text_clean, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail=str(e)[:200])
            yield event.plain_result(f"⚠️ 查询失败：{e}")
        event.stop_event()
