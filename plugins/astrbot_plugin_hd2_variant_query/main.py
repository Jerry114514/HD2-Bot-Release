import io
import os
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
    return str(getattr(event.message_obj, "group_id", "") or "")


def _uid(event):
    return str(event.get_sender_id() or "")


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


async def _scan_all_variants():
    now = time.time()
    if _scan_cache["data"] and now - _scan_cache["ts"] < 300:
        return _scan_cache["data"]
    result = {kw: [] for kw in VARIANT_CN}
    effects = await blocking(_load_effect_id_cn)
    if not effects:
        raise RuntimeError("变种效果映射不可用，无法判断当前变种")
    try:
        status = await blocking(_fetch_official, f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status")
        planets = await blocking(_fetch_json, PLANETS_URL)
        names = {p["index"]: p["name"] for p in planets}
        camps = {c.get("planetIndex") for c in status.get("campaigns") or []}
        known = {v.replace(" ", "").upper(): k for k,v in VARIANT_CN.items()}
        for effect in status.get("planetActiveEffects") or []:
            idx = effect.get("index")
            key = known.get(str(effects.get(effect.get("galacticEffectId"), "")).replace(" ", "").upper())
            if idx in camps and key:
                name = names.get(idx, str(idx))
                if name not in result[key]:
                    result[key].append(name)
    except (OSError, ValueError) as error:
        logger.warning(f"[HD2] 结构化变种源不可用：{error}")
        planets = await blocking(_fetch_json, PLANETS_URL)
        names = {p["index"]: p["name"] for p in planets}
        live = await blocking(_fetch_json, LIVE_API_URL)
        indices = {c.get("planetIndex") for c in (live.get("warStatus") or {}).get("campaigns") or []}
        if indices:
            try:
                from playwright.async_api import async_playwright
            except ImportError as missing:
                raise RuntimeError("变种备用查询需要可选浏览器依赖，目前数据不可用") from missing
            sem = asyncio.Semaphore(3)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    async def check(idx):
                        async with sem:
                            page = await browser.new_page()
                            try:
                                slug = names.get(idx, "").lower().replace(" ", "_")
                                await page.goto(f"https://helldiverscompanion.com/#hellpad/planets/{slug}", timeout=30000)
                                await page.wait_for_function("() => document.body.innerText.includes('OPERATIONAL PARAMETERS')",timeout=15000)
                                body = (await page.inner_text("body")).upper()
                                section = body.split("OPERATIONAL PARAMETERS",1)[1].split("POINTS OF INTEREST",1)[0]
                                for key in VARIANT_CN:
                                    if key in section:
                                        result[key].append(names.get(idx,str(idx)))
                            finally:
                                await page.close()
                    await asyncio.gather(*(check(idx) for idx in indices))
                finally:
                    await browser.close()
    _scan_cache.update({"ts":now,"data":result})
    return result

from astrbot_plugin_hd2_common import commands
from astrbot_plugin_hd2_common.runtime import blocking

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
        parsed = commands.parse(event.message_str)
        if not parsed or parsed[0] != "variant":
            return
        query = parsed[1].strip()
        key = next((key for key,cn in VARIANT_CN.items() if query.upper() == key or query == cn), None)
        if key is None:
            yield event.plain_result("请使用 /变种 名称，支持：" + "、".join(VARIANT_CN.values()))
            event.stop_event()
            return
        try:
            scan = await _scan_all_variants()
            name_map = await blocking(_load_planet_cn_map)
            rows = scan[key]
            yield event.plain_result("目前无该变种的活动战役。" if not rows else "\n".join(name_map.get(p.upper(),p) + "——" + VARIANT_CN[key] for p in rows))
        except Exception as error:
            yield event.plain_result("⚠️ 变种数据不可用：" + str(error))
        event.stop_event()
