import json
import io
import re
import time
import asyncio
import random
from urllib.parse import quote
from urllib.request import Request, urlopen

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

# 监控埋点（hd2_monitor）：记录执行状态
import os as _os, sys as _sys
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
    load_starmap_sectors as _load_starmap_sectors,
    load_planet_map as _load_planet_map,
    load_planet_name_map as _load_planet_name_map,
    PLANET_NAME_CN,
    SECTOR_CN,
    OFFICIAL_API_URL,
    LIVE_API_URL,
    OFFICIAL_WAR_ID,
    PLANETS_URL,
    TRANSLATE_URL,
    ASSIGNMENTS_URL,
    EXTENDED_API_URL,
)

# 统一数据源（hd2_datasource，项目根）：主站 data.json / 实测帧 / 翻译产物 / 预测口径
_ROOT_DIR = _os.path.dirname(_MON_PARENT)
if _ROOT_DIR not in _sys.path:
    _sys.path.insert(0, _ROOT_DIR)
try:
    import hd2_datasource as _ds
except Exception:  # pragma: no cover - 部署异常时退化为纯 API 模式
    _ds = None


# 星球译名合并映射（对照表优先 + 硬编码兜底），懒加载缓存
_PLANET_CN_MERGED = None


def _planet_cn(en: str) -> str:
    """英文名 -> 中文名：星图对照表（随主站更新）优先，硬编码表兜底；查不到原样返回"""
    global _PLANET_CN_MERGED
    if _PLANET_CN_MERGED is None:
        try:
            _PLANET_CN_MERGED = _load_planet_name_map()
        except Exception:
            _PLANET_CN_MERGED = dict(PLANET_NAME_CN)
    en = en or ""
    return _PLANET_CN_MERGED.get(en.upper(), en)


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


# 触发关键词（消息里包含任一即触发）
TRIGGER_KEYWORDS = [
    "目前战况如何",
    "现在在打哪里",
    "重要指令是什么",
    "战况如何",
    "重要指令",
    "打哪里",
    "major order",
    "majororder",
    "战报",
    "dss",
    "战役",
    "战线",
    "分析",
    "归属变化",
    "星图变化",
    "归属",
]

# 阵营中文名
FACTION_CN = {
    "Terminids": "终结族",
    "Automatons": "机器人",
    "Illuminate": "光能者",
    "Humans": "超级地球",
}

# 阵营名 -> owner 数字枚举（与 OWNER_ID_CN 对应；站点 planets[].currentOwner 用的是这套字符串）
OWNER_STR_ID = {
    "humans": 1,
    "terminids": 2,
    "automatons": 3,
    "illuminate": 4,
}

# 防御战截止时间换算偏移（UTC -> UTC+8）
TZ_OFFSET_HOURS = 8

# 已知 Major Order 简报的本地精确翻译（模板化文本，优先命中）
# 原文（小写） -> 中文
BRIEF_TRANSLATION_OVERRIDES = {
    "hold the designated planets to support the completion of the tcs+":
        "在指定时间内守住目标星球，以保证TCS+系统的完工",
    "hold the designated planets": "守住目标星球",
}

# 常见游戏术语英文->中文替换（机翻后修正）
TERM_FIXES = [
    ("星耀", "星球"),
    ("恒星", "星球"),
    ("行星", "星球"),
    ("指定的行星", "目标星球"),
    ("指定行星", "目标星球"),
    ("tcs +", "TCS+"),
    ("tcs+", "TCS+"),
    ("终端虫", "虫族"),
    ("虫子", "虫族"),
    ("自动机", "机器人"),
    ("巨型工厂", "超大型工厂"),
    ("超级工厂", "超大型工厂"),
    ("阿尔库比尔", "阿库别瑞"),
    ("阿尔库别雷", "阿库别瑞"),
    ("曲速泡", "曲率泡"),
    ("曲速中继站", "曲率中继站"),
    ("继电器", "中继器"),
]

# 翻译术语表：注入 LLM 翻译 prompt，保证译名统一（后续可扩充）
LLM_GLOSSARY = """
游戏术语对照表（翻译时必须使用这些译名）：
- DARIUS II = 大流士 II
- ACHERNAR SECUNDUS = 水委一次星
- GRAND ERRANT = 大艾伦特
- PHERKAD SECUNDUS = 北极一次星
- MEISSA = 梅莎
- Terminids = 终结族（虫族）
- Automatons = 机器人
- Illuminate = 光能者
- Cyborgs = 生化人
- cyborg = 生化人
- destryers = 超级驱逐舰（前面带 super 时译文不变，如 super destryers 仍译为超级驱逐舰）
- Alcubiere = 阿库别瑞
- MegaFactory = 超大型工厂
- Megafactories = 超大型工厂
- TCS+ = TCS+
- Major Order = 重要指令
- STRATEGIC IMPERATIVE = 战略机遇
- Super Earth = 超级地球
- Helldivers = 绝地潜兵
- Barrier planets = 屏障星球
- Stratagem = 战略/战略配备
- The Void = 寂域
- Void-impacted molecules = 受寂域影响的分子
- Voteless = 无票者
- Wretch = 悲怜体
- Crusher = 粉碎者
- Spearhead = 教团先锋
- Gloom = 阴霾
- (The) Gloom = 阴霾
- Ministry of Science = 科学部
- y-shapes = 音叉形状
- Socialism = （不要翻译，遇到直接略过该单词，不要输出任何中文对应词）
"""





def _translate_en_zh(text: str) -> str:
    """英译中：本地修正表优先，然后 MyMemory，失败返回原文"""
    if not text or not text.strip():
        return text or ""
    key = " ".join(text.lower().split()).rstrip(".。!！?？")
    if key in BRIEF_TRANSLATION_OVERRIDES:
        return BRIEF_TRANSLATION_OVERRIDES[key]
    try:
        q = quote(text[:500])
        obj = _fetch_json(f"{TRANSLATE_URL}?q={q}&langpair=en%7Czh-CN", timeout=10)
        translated = ((obj.get("responseData") or {}).get("translatedText") or "").strip()
        if translated and obj.get("responseStatus") == 200:
            # 术语修正
            fixed = translated
            for old, new in TERM_FIXES:
                fixed = fixed.replace(old, new)
            return fixed
    except Exception as e:
        logger.warning(f"[HD2] 翻译失败: {e}")
    return text

async def _translate_with_llm(
    context: Context, text: str, provider_id: str | None = None, system_prompt: str | None = None
) -> str | None:
    """用 AstrBot 接入的 LLM 翻译文本，失败返回 None

    system_prompt: 可选自定义翻译提示词；默认是 Major Order 简报翻译提示词
    """
    if not text or not text.strip():
        return text or ""
    try:
        if not provider_id:
            # 取默认 provider
            conf = context.astrbot_config_mgr.default_conf
            provider_id = (
                (conf.get("provider_settings") or {}).get("default_provider_id")
                or ""
            )
        if not provider_id:
            return None
        if system_prompt is None:
            sys_prompt = (
                "你是专业的游戏本地化翻译。把用户给出的英文《绝地潜兵2》Major Order 任务简报翻译成简体中文。"
                "要求：1) 准确传达任务目标；2) 星球名等专有名词按术语表翻译；3) 只输出译文，不要任何解释。\n\n"
                + LLM_GLOSSARY
            )
        else:
            sys_prompt = system_prompt
        resp = await context.llm_generate(
            chat_provider_id=provider_id,
            prompt=text,
            system_prompt=sys_prompt,
        )
        result = (resp.completion_text or "").strip()
        if result:
            # LLM 输出兜底：本地术语修正（防 LLM 不遵循术语表）
            for old, new in TERM_FIXES:
                result = result.replace(old, new)
            return result
    except Exception as e:
        logger.warning(f"[HD2] LLM 翻译失败: {e}")
    return None

def _fmt_planet_status(info: dict) -> str:
    """生成单个星球战况文本（中文星球名/星区，时间 UTC+8）"""
    name = _planet_cn(info.get("name", "")) or "未知星球"
    # 星系显示：查本地星图表（对照表 英文名->星区），sector 字段 Unknown/数字不可靠
    sector = (_load_starmap_sectors().get((info.get("name") or "").upper())) or SECTOR_CN.get(info.get("sector", ""), info.get("sector", ""))
    players = info.get("players", 0)
    event = info.get("event")
    owner = info.get("owner", "")

    if event:
        # 防御战/进攻战：event.health 下降 = 已完成的进度
        e_health = event.get("health") or 0
        e_max = event.get("maxHealth") or 1
        progress = (1 - e_health / e_max) * 100
        faction = FACTION_CN.get(event.get("faction"), event.get("faction") or "?")
        end_time = event.get("endTime", "")
        time_str = _fmt_utc8(end_time)
        if progress >= 99.9:
            status = f"✅ 防御成功（vs {faction}）"
        else:
            status = f"⚔️ 防御战（vs {faction}）{progress:.1f}%，截止 {time_str}"
    else:
        # 解放战：health 下降 = 解放进度
        health = info.get("health") or 0
        max_health = info.get("maxHealth") or 1
        progress = (1 - health / max_health) * 100
        if progress >= 99.9:
            status = "🟢 已解放"
        elif progress <= 0.01 and owner == "Humans":
            status = "🔵 超级地球控制中（无战斗）"
        else:
            status = f"🟠 解放战 {progress:.1f}%"

    sector_str = f"（{sector}）" if sector else ""
    return f"• {name}{sector_str}｜{status}｜玩家 {players}"

def _fmt_utc8(iso_time: str) -> str:
    """把 UTC ISO 时间转为 UTC+8 的 MM-DD HH:MM 文本，失败返回原样"""
    if not iso_time or len(iso_time) < 16:
        return iso_time or ""
    try:
        import datetime

        # 解析末尾 Z 的 ISO 时间
        dt = datetime.datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        dt = dt + datetime.timedelta(hours=TZ_OFFSET_HOURS)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso_time[11:16] or ""

def _fmt_seconds(secs: int) -> str:
    secs = max(int(secs), 0)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d > 0:
        return f"{d}天{h}小时"
    if h > 0:
        return f"{h}小时{m}分"
    return f"{m}分钟"

def _fetch_brief_text() -> str:
    """只抓取当前 Major Order 简报原文（官方 API 优先，社区备选）"""
    try:
        ass = _fetch_official(f"/api/v2/Assignment/War/{OFFICIAL_WAR_ID}")
        if ass:
            return (ass[0].get("setting") or {}).get("overrideBrief") or ""
    except Exception as e:
        logger.warning(f"[HD2] 官方简报原文失败，回退社区源: {e}")
    try:
        obj = _fetch_json(LIVE_API_URL)
        mos = obj.get("majorOrders") or []
        if not mos:
            return ""
        return (mos[0].get("setting") or {}).get("overrideBrief") or ""
    except Exception as e:
        logger.warning(f"[HD2] 获取简报原文失败: {e}")
        return ""

def _mo_measured_rates(assignment: dict) -> dict:
    """近 1 小时 MO 各任务实测完成速率（主站 data/recent/assignments.json 帧差分）。

    口径与主站 rate-source.js moWindows(h1) 一致：同指令、同 startTime、窗口 ≥15 分钟；
    type 11 的原始进度是占领标记，不计速率。拿不到帧返回 {}（绝不冒充 0）。
    """
    if not _ds:
        return {}
    try:
        a_id = str(assignment.get("id") or assignment.get("id32") or "")
        tasks = assignment.get("tasks") or []
        tkey = json.dumps([[t.get("type"), t.get("values"), t.get("valueTypes")] for t in tasks])
        now_ms = time.time() * 1000
        rows = []
        for f in _ds.recent_assignment_frames():
            if not isinstance(f, dict):
                continue
            t = _ds.to_ms(f.get("timestampUtc"))
            if t is None or t > now_ms:
                continue
            for a in f.get("assignments") or []:
                if not isinstance(a, dict):
                    continue
                if a_id and str(a.get("id32") or a.get("id") or "") != a_id:
                    continue
                st = a.get("setting") or {}
                tk = json.dumps([[x.get("type"), x.get("values"), x.get("valueTypes")]
                                 for x in (st.get("tasks") or [])])
                if tk != tkey:
                    continue
                rows.append((t, a.get("startTime"), a.get("progress") or []))
                break
        if len(rows) < 2:
            return {}
        rows.sort(key=lambda r: r[0])
        last_t, last_start, last_prog = rows[-1]
        if now_ms - last_t > 3600000:
            return {}
        same = [r for r in rows if r[1] is not None and r[1] == last_start]
        base = next((r for r in same if r[0] >= last_t - 3600000), None)
        if base is None or base[0] == last_t:
            return {}
        hours = (last_t - base[0]) / 3600000
        if hours < 0.24:
            return {}
        rates = {}
        for i, t in enumerate(tasks):
            if _ds._num(t.get("type")) == 11:
                continue
            try:
                va, vb = base[2][i], last_prog[i]
            except Exception:
                continue
            fa, fb = _ds._num(va), _ds._num(vb)
            if fa is None or fb is None or (fb < fa and _ds._num(t.get("type")) != 15):
                continue
            rates[i] = (fb - fa) / hours
        return rates
    except Exception as e:
        logger.debug(f"[HD2] MO 实测速率不可用: {e}")
        return {}


def _mo_task_forecast_line(task: dict, cur, target, window, recent_rate) -> str:
    """单任务预测行（主站 forecast.js 口径）；无窗口/无速率返回空串

    type 11 的原始进度是"星球是否解放"的占领标记（0/1），真实进度已在
    「目标星球战况」逐星展示，这里不再输出误导性的时间基准对比。
    """
    if not _ds:
        return ""
    try:
        ttype = _ds._num(task.get("type"))
        if ttype == 11:
            return ""
        st = _ds.task_state({"type": task.get("type"), "goal": target}, cur, window, recent_rate)
        state = st.get("state")
        if state == "hold":
            return "驻守目标即可，保持进度到截止" if st.get("satisfied") else "需驻守到截止时间"
        if state in ("complete", "unknown", "satisfied", "stateful"):
            return ""
        parts = []
        d = st.get("delta")
        if d is not None:
            parts.append(f"{'领先' if d >= 0 else '落后'}时间基准 {abs(d):.1f}pt")
        proj = st.get("projected")
        if proj is not None:
            parts.append(f"按均速预计 {proj:.0f}%")
        if recent_rate is not None:
            parts.append(f"近1h实测 {recent_rate:.0f}/h")
        win = st.get("win")
        tail = "✅ 预计按时完成" if win else ("⚠️ 恐难按时完成" if win is False else "")
        s = " · ".join(parts)
        if tail:
            s = (s + " · " if s else "") + tail
        return s
    except Exception:
        return ""


def _mo_reward_amount() -> int | None:
    """MO 奖励奖章数：主站 active_campaign.phases[].reward.amount；缺失返回 None"""
    if not _ds:
        return None
    try:
        ac = _ds.active_campaign()
        for ph in ac.get("phases") or []:
            if not isinstance(ph, dict):
                continue
            amt = (ph.get("reward") or {}).get("amount")
            if amt:
                return int(amt)
    except Exception:
        pass
    return None


def _format_mo_site(a: dict, planet_map: dict, brief_translated: str | None, brief_cn: str | None) -> str:
    """用主站 data.json 的 assignments 组装 MO 报告（预测/实测可选，零 LLM）"""
    brief = a.get("briefing") or ""
    tasks = a.get("tasks") or []
    progress_vals = a.get("progress") or []
    translated = (brief_cn or "").strip() or brief_translated or (_site_zh().get("brief") or "").strip()
    if not translated and brief:
        translated = _translate_en_zh(brief)

    window = _ds.order_window(a) if _ds else None
    measured = _mo_measured_rates(a)

    lines = ["🎖️ 重要指令"]
    if translated:
        lines.append(f"📋 {translated}")

    task_lines = []
    for i, t in enumerate(tasks):
        values = t.get("values") or []
        vtypes = t.get("valueTypes") or []
        target = None
        for j, vt in enumerate(vtypes):
            if vt == 3 and j < len(values) and isinstance(values[j], (int, float)):
                target = values[j]
                break
        if target is None:
            continue
        tname = MO_TASK_TYPE_CN.get(t.get("type"), f"任务{i+1}")
        cur = progress_vals[i] if i < len(progress_vals) else None
        if cur is None:
            task_lines.append(f"• {tname}：进度暂缺（目标 {_fmt_big_num(target)}）")
            continue
        pct = (cur / target * 100) if target else 0
        line = f"• {tname}：{pct:.1f}%（{_fmt_big_num(cur)}/{_fmt_big_num(target)}）"
        extra = _mo_task_forecast_line(t, cur, target, window, measured.get(i))
        if extra:
            line += f"\n  {extra}"
        task_lines.append(line)
    if task_lines:
        lines.append("")
        lines.append("📊 任务进度")
        lines.extend(task_lines)

    # 目标星球（valueTypes 12 = 星球索引）
    planet_idxs = []
    for t in tasks:
        values = t.get("values") or []
        vtypes = t.get("valueTypes") or []
        for i, vt in enumerate(vtypes):
            if vt == 12 and i < len(values) and isinstance(values[i], int):
                planet_idxs.append(str(values[i]))
    if not planet_idxs:
        for t in tasks:
            for x in (t.get("values") or []):
                if isinstance(x, int) and 10 <= x <= 274:
                    planet_idxs.append(str(x))
    planet_idxs = list(dict.fromkeys(planet_idxs))
    if planet_idxs:
        lines.append("")
        lines.append("🎯 目标星球战况")
        for idx in planet_idxs:
            info = planet_map.get(idx)
            if info:
                lines.append(_fmt_planet_status(info))
            else:
                lines.append(f"• 星球#{idx}（数据暂缺）")
        try:
            st = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status", timeout=20)
            ps = {p.get("index"): p for p in st.get("planetStatus") or []}
            camp_idx = set(c.get("planetIndex") for c in (st.get("campaigns") or []))
            defend_idx = set(ev.get("planetIndex") for ev in (st.get("planetEvents") or []))
            routes = _load_warp_routes()
            if routes:
                block = _compute_warp_blockades(routes, ps, camp_idx, defend_idx)
                for idx in planet_idxs:
                    if int(idx) in block:
                        lines.append(f"🚀 {_fmt_warp_blockade(int(idx), block[int(idx)], planet_map)}")
        except Exception:
            pass

    tail = []
    reward_amt = _mo_reward_amount()
    if reward_amt:
        tail.append(f"🏆 {reward_amt} 奖章")
    d_ms = _ds.to_ms(a.get("deadline")) if _ds else None
    s_ms = _ds.to_ms(a.get("serverTime")) if _ds else None
    remaining = None
    if d_ms:
        base_ms = s_ms if s_ms else time.time() * 1000
        remaining = (d_ms - base_ms) / 1000
    if remaining is not None and remaining > 0:
        tail.append(f"⏳ 剩余 {_fmt_seconds(remaining)}")
    if tail:
        lines.append("")
        lines.append("  ·  ".join(tail))
    return "\n".join(lines)


def _get_major_order(
    planet_map: dict,
    brief_translated: str | None = None,
    brief_cn: str | None = None,
) -> str | None:
    """抓取最新 Major Order（主站 data.json 优先，官方 API 兜底）

    返回 None 表示"取不到数据（源故障）"；返回含"当前没有进行中的重要指令"的文案
    表示"源正常但确实没有 MO"（MO 间隙期属正常状态，不应报错）。

    Args:
        planet_map: 星球信息映射
        brief_translated: 可选，外部传入的简报翻译；为空则用站点中文/本地修正表
        brief_cn: 可选，站点 data.json 里现成的 MO 简报中文（最优先，零 LLM）
    """
    if _ds:
        try:
            a = _ds.mo_assignment(_site_doc())
            tasks = a.get("tasks") if isinstance(a, dict) else None
            if isinstance(tasks, list) and tasks:
                return _format_mo_site(a, planet_map, brief_translated, brief_cn)
        except Exception as e:
            logger.warning(f"[HD2] 站点 MO 组装失败，回退官方 API: {e}")
    return _get_major_order_api(planet_map, brief_translated, brief_cn)


def _get_major_order_api(
    planet_map: dict,
    brief_translated: str | None = None,
    brief_cn: str | None = None,
) -> str | None:
    """官方 API 路径的 MO 报告（主站不可用时的兜底，逻辑与旧版一致）"""
    try:
        try:
            mos = _fetch_official(f"/api/v2/Assignment/War/{OFFICIAL_WAR_ID}")
        except Exception as e:
            logger.warning(f"[HD2] 官方 MO 失败，回退社区源: {e}")
            obj = _fetch_json(LIVE_API_URL)
            mos = obj.get("majorOrders") or []
        if not mos:
            # 源正常但没有进行中的 MO（两次 MO 之间的间隙）——这是正常状态，如实说明
            logger.info("[HD2] 当前没有进行中的 Major Order（源返回空）")
            return "🎖️ 重要指令\n📋 当前没有进行中的重要指令（等待真理部下一道命令）。"
        mo = mos[0]
        setting = mo.get("setting") or {}
        title = setting.get("overrideTitle") or "MAJOR ORDER"
        brief = setting.get("overrideBrief") or ""
        tasks = setting.get("tasks") or []
        rewards = setting.get("rewards") or []

        lines = ["🎖️ 重要指令"]
        # 翻译简报优先级：站点现成中文 > 外部传入(LLM) > 本地修正表/MyMemory
        if brief or brief_cn:
            translated = (brief_cn or "").strip() or brief_translated or _translate_en_zh(brief)
            lines.append(f"📋 {translated}")

        # 任务进度：progress 数组与 tasks 一一对应，目标量在 valueTypes==3
        progress_vals = mo.get("progress") or []
        task_lines = []
        for i, t in enumerate(tasks):
            values = t.get("values") or []
            vtypes = t.get("valueTypes") or []
            target = None
            for j, vt in enumerate(vtypes):
                if vt == 3 and j < len(values) and isinstance(values[j], (int, float)):
                    target = values[j]
                    break
            if target is None:
                continue
            cur = progress_vals[i] if i < len(progress_vals) else 0
            pct = (cur / target * 100) if target else 0
            tname = MO_TASK_TYPE_CN.get(t.get("type"), f"任务{i+1}")
            task_lines.append(f"• {tname}：{pct:.1f}%（{_fmt_big_num(cur)}/{_fmt_big_num(target)}）")
        if task_lines:
            lines.append("")
            lines.append("📊 任务进度")
            lines.extend(task_lines)

        # 任务：valueTypes 中 12 = 星球索引，逐星报告战况（兼容新旧结构）
        planet_idxs = []
        for t in tasks:
            values = t.get("values") or []
            vtypes = t.get("valueTypes") or []
            for i, vt in enumerate(vtypes):
                if vt == 12 and i < len(values) and isinstance(values[i], int):
                    planet_idxs.append(str(values[i]))
        # 兜底：valueTypes 缺失时扫描 values 中 10-274 的值
        if not planet_idxs:
            for t in tasks:
                for x in (t.get("values") or []):
                    if isinstance(x, int) and 10 <= x <= 274:
                        planet_idxs.append(str(x))
        planet_idxs = list(dict.fromkeys(planet_idxs))

        if planet_idxs:
            lines.append("")
            lines.append("🎯 目标星球战况")
            for idx in planet_idxs:
                info = planet_map.get(idx)
                if info:
                    lines.append(_fmt_planet_status(info))
                else:
                    lines.append(f"• 星球#{idx}（数据暂缺）")
            # 跃迁航道封锁提示（MO 目标被封锁则无法解放）
            try:
                st = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status", timeout=20)
                ps = {p.get("index"): p for p in st.get("planetStatus") or []}
                camp_idx = set(c.get("planetIndex") for c in (st.get("campaigns") or []))
                defend_idx = set(ev.get("planetIndex") for ev in (st.get("planetEvents") or []))
                routes = _load_warp_routes()
                if routes:
                    block = _compute_warp_blockades(routes, ps, camp_idx, defend_idx)
                    for idx in planet_idxs:
                        if int(idx) in block:
                            lines.append(f"🚀 {_fmt_warp_blockade(int(idx), block[int(idx)], planet_map)}")
            except Exception:
                pass

        # 奖励
        reward_str = ""
        if rewards:
            for r in rewards:
                amt = r.get("amount")
                reward_str = f"🏆 {amt} 奖章"

        # 剩余时间
        expires_in = mo.get("expiresIn")
        tail = []
        if reward_str:
            tail.append(reward_str)
        if expires_in is not None:
            tail.append(f"⏳ 剩余 {_fmt_seconds(expires_in)}")
        if tail:
            lines.append("")
            lines.append("  ·  ".join(tail))
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[HD2] 获取 Major Order 失败: {e}")
        return None

# 缓存目录：插件目录下统一 cache/ 文件夹（MO / DSS 资讯 / 新闻缓存）
CACHE_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "cache")
_os.makedirs(CACHE_DIR, exist_ok=True)

# Major Order 缓存文件（最近 5 条新闻无 NEW MAJOR ORDER 时复用，避免重复翻译/防 API 波动）
MO_CACHE_FILE = _os.path.join(CACHE_DIR, "mo_cache.json")
MO_CACHE_WINDOW = 5  # 检查最近 N 条新闻是否出现新 NMO

# ============ 信息流数据源 ============
# 主源：主站 data.json（由主仓库 Actions 每 5 分钟更新，news 段带翻译仓库的官方中文译文）。
# 兜底：data/translated/TransNews.json（翻译仓库推送到 Pages 的副本）→ 实时 API → 本地缓存。
# URL/TTL 全部集中在 hd2_datasource，可用 config 的 data.* 覆盖（见 docs/数据源对齐与映射表.md）。
NEWS_FEED_JSON_URL = _ds.DATA_JSON_URL if _ds else "https://jerry114514.github.io/HD2-Galatic_war-Map/data.json"
NEWS_FEED_SOURCE = "hd2map"      # hd2map=站点 JSON 优先(回退 API)；api=只用 API
NEWS_FEED_CACHE_TTL = 120        # 站点 JSON 本地缓存秒数（站点每 5 分钟更新一次）
NEWS_FEED_CACHE_FILE = _os.path.join(CACHE_DIR, "news_feed_cache.json")
_NEWS_FEED_MEM = {"ts": 0.0, "items": None, "source": ""}

if _ds:
    _ds.set_cache_root(CACHE_DIR)

try:
    import hd2_config as _hd2cfg

    def _dcfg(*keys, default=None):
        return _hd2cfg.get(*keys, default=default)
except Exception:
    def _dcfg(*keys, default=None):
        return default

NEWS_FEED_JSON_URL = _dcfg("data", "news_json_url", default=NEWS_FEED_JSON_URL)
NEWS_FEED_SOURCE = str(_dcfg("data", "news_source", default=NEWS_FEED_SOURCE) or "hd2map")
try:
    NEWS_FEED_CACHE_TTL = int(_dcfg("data", "news_cache_ttl", default=NEWS_FEED_CACHE_TTL))
except Exception:
    NEWS_FEED_CACHE_TTL = 120

# ============ /战报 后台静默预热（前台只读缓存） ============
# 思路与网站一致：后台定时把数据抓好、译文备好，存成快照；用户发 /战报 时直接格式化快照返回。
# 站点 data.json 已含 major_order.translated_brief 与 news[].translated，
# 因此前台路径**完全不需要 LLM**，冷启动后响应通常在 10ms 级。
try:
    REPORT_WARM_ENABLE = str(_dcfg("data", "report_warm_enable", default="1")).lower() not in ("0", "false", "no")
except Exception:
    REPORT_WARM_ENABLE = True
try:
    REPORT_WARM_INTERVAL = max(60, int(_dcfg("data", "report_warm_interval", default=240)))
except Exception:
    REPORT_WARM_INTERVAL = 240
try:
    REPORT_SNAPSHOT_TTL = max(120, int(_dcfg("data", "report_snapshot_ttl", default=900)))
except Exception:
    REPORT_SNAPSHOT_TTL = 900
REPORT_SNAPSHOT_FILE = _os.path.join(CACHE_DIR, "report_snapshot.json")

# ============ 星球表（index -> 星球信息）多源加载 ============
# 原来只依赖 helldivers2.dev：该源超时（默认 20s）时每条指令都要白等满超时，
# 且返回空 dict 会让 MO 目标星球、战线等全部缺名。改为：
#   站点 data.json 的 planets[]（字段与 helldivers2.dev 完全一致，273 个，随站点每 5 分钟更新）
#   → 本地磁盘缓存 → 最后才回退 helldivers2.dev（且显式短超时）
PLANET_TABLE_CACHE = _os.path.join(CACHE_DIR, "planet_table_cache.json")
PLANET_TABLE_TTL = 3600      # 磁盘缓存最长可用时长（秒）
PLANET_TABLE_REFRESH = 300   # 超过该时长则后台刷新（前台仍用缓存，不等待）
_PLANET_TABLE_MEM = {"ts": 0.0, "map": {}}


def _read_planet_disk_cache() -> tuple:
    """读星球表磁盘缓存：(map, age)；无则 ({}, None)"""
    try:
        if _os.path.exists(PLANET_TABLE_CACHE):
            with open(PLANET_TABLE_CACHE, encoding="utf-8") as f:
                d = json.load(f)
            mp = d.get("map") or {}
            if mp:
                return mp, time.time() - float(d.get("ts") or 0)
    except Exception as e:
        logger.warning(f"[HD2] 星球表缓存读取失败: {e}")
    return {}, None


def _save_planet_disk_cache(mp: dict) -> None:
    try:
        tmp = PLANET_TABLE_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "source": "site", "map": mp}, f, ensure_ascii=False)
        _os.replace(tmp, PLANET_TABLE_CACHE)
    except Exception as e:
        logger.warning(f"[HD2] 星球表缓存写入失败: {e}")


async def _refresh_planet_table_bg() -> None:
    """后台刷新星球表（不阻塞任何指令）"""
    try:
        mp, src = await asyncio.to_thread(_load_planet_table, True)
        logger.info(f"[HD2] 星球表后台刷新完成（来源 {src}，{len(mp or {})} 个）")
    except Exception as e:
        logger.warning(f"[HD2] 星球表后台刷新失败: {e}")


def _planet_info_from_item(p: dict) -> dict:
    """把星球条目归一化为插件内部结构（owner 可能是字符串"Terminids"或数字 2）"""
    owner = p.get("currentOwner", p.get("owner", ""))
    if isinstance(owner, str):
        owner = OWNER_STR_ID.get(owner.strip().lower(), owner)
    return {
        "name": p.get("name", "未知星球"),
        "sector": p.get("sector", ""),
        "owner": owner,
        "health": p.get("health"),
        "maxHealth": p.get("maxHealth"),
        "event": p.get("event"),
        "players": (p.get("statistics") or {}).get("playerCount", p.get("players", 0)),
    }


def _load_planet_table(force: bool = False) -> tuple:
    """加载星球表：返回 (map, source)；source ∈ mem/disk/site/api/"" 。

    策略（前台永不因网络抖动等待）：
      1) 内存/磁盘已有缓存 -> 立刻返回；缓存超过 PLANET_TABLE_REFRESH 时顺手起个后台刷新
      2) 都没有 -> 才走网络（站点 JSON 优先，最后 helldivers2.dev 短超时）
    这样机器人重启后第一条指令也是毫秒级，星球表的更新交给后台。
    """
    global _planet_refresh_task
    now = time.time()
    if not force:
        if _PLANET_TABLE_MEM["map"] and now - _PLANET_TABLE_MEM["ts"] < PLANET_TABLE_TTL:
            return _PLANET_TABLE_MEM["map"], "mem"
        mp, age = _read_planet_disk_cache()
        if mp and age is not None and age < PLANET_TABLE_TTL:
            _PLANET_TABLE_MEM.update({"ts": now - age, "map": mp})
            logger.info(f"[HD2] 星球表来源：本地缓存（{len(mp)} 个，{int(age)}s 前）")
            if age > PLANET_TABLE_REFRESH:
                try:
                    _planet_refresh_task = asyncio.create_task(_refresh_planet_table_bg())
                except Exception:
                    pass
            return mp, "disk"
    # 1) 站点 data.json（与信息流/中文译文共用一次抓取）
    try:
        doc = _site_doc(force=force)
        plist = (doc or {}).get("planets") or []
        if plist:
            mp = {str(p.get("index")): _planet_info_from_item(p)
                  for p in plist if isinstance(p, dict) and p.get("index") is not None}
            if mp:
                _PLANET_TABLE_MEM.update({"ts": now, "map": mp})
                _save_planet_disk_cache(mp)
                logger.info(f"[HD2] 星球表来源：站点 data.json（{len(mp)} 个）")
                return mp, "site"
    except Exception as e:
        logger.warning(f"[HD2] 站点星球表失败: {e}")
    # 2) 过期的本地磁盘缓存（总比没有强）
    mp, age = _read_planet_disk_cache()
    if mp:
        _PLANET_TABLE_MEM.update({"ts": now - (age or 0), "map": mp})
        logger.warning(f"[HD2] 星球表来源：过期本地缓存（已 {int(age or 0)}s 未更新）")
        return mp, "disk-stale"
    # 3) 最后才回退 helldivers2.dev（显式短超时，避免默认 20s 拖死指令）
    try:
        planets = _fetch_json(
            PLANETS_URL, timeout=6,
            extra_headers={
                "X-Super-Client": "astrbot-hd2-plugin",
                "X-Super-Contact": "https://github.com/astrbot",
            },
        )
        mp = {str(p.get("index")): _planet_info_from_item(p)
              for p in planets if isinstance(p, dict) and p.get("index") is not None}
        if mp:
            _PLANET_TABLE_MEM.update({"ts": now, "map": mp})
            logger.info(f"[HD2] 星球表来源：helldivers2.dev（{len(mp)} 个）")
            return mp, "api"
    except Exception as e:
        logger.warning(f"[HD2] helldivers2.dev 星球表失败（已用短超时）: {e}")
    return {}, ""


def _load_planet_map() -> dict:
    """index -> 星球信息 dict：直接用多源版（站点 JSON → 磁盘缓存 → API 短超时）

    注意：本文件同名函数会遮蔽 hd2_common 的导入版，故这里显式走多源实现。
    """
    return _load_planet_table()[0]

# 内存快照（避免每条指令读盘）
_REPORT_WARM = {"ts": 0.0, "text": "", "source": "", "building": False}
_warm_task = None
_planet_refresh_task = None

# MO 任务类型 → 中文（与官方 task type 语义对齐）
MO_TASK_TYPE_CN = {
    2: "撤离",
    3: "击杀",
    7: "完成任务",
    9: "完成行动",
    11: "解放",
    12: "防守",
    13: "坚守",
    15: "扩张",
}


def _fmt_big_num(n: int) -> str:
    """大数格式化：委托 hd2_datasource（修正了旧实现"亿"位 10 倍缩小的错误）"""
    if _ds:
        return _ds.fmt_big_num(n)
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 100_000_000:
        return (f"{n / 100_000_000:.2f}".rstrip("0").rstrip(".")) + "亿"
    if n >= 10_000:
        return (f"{n / 10_000:.1f}".rstrip("0").rstrip(".")) + "万"
    return str(n)


def _has_recent_new_major_order(limit: int = MO_CACHE_WINDOW) -> bool:
    """最近 limit 条新闻里是否出现 NEW MAJOR ORDER（新闻 message 含该字样）"""
    try:
        for it in _get_news_items(limit):
            if "NEW MAJOR ORDER" in str(it.get("message", "")).upper():
                return True
    except Exception as e:
        logger.warning(f"[HD2] 检查 NMO 新闻失败: {e}")
    return False


def _save_mo_cache(text: str) -> None:
    """每次实时抓取 MO 成功后写入缓存（含时间戳）"""
    try:
        data = {"ts": int(time.time()), "text": text}
        with open(MO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[HD2] MO 缓存写入失败: {e}")


def _load_mo_cache() -> str | None:
    """读取 MO 缓存文本（无缓存/损坏返回 None）"""
    try:
        if _os.path.exists(MO_CACHE_FILE):
            with open(MO_CACHE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("text") or None
    except Exception as e:
        logger.warning(f"[HD2] MO 缓存读取失败: {e}")
    return None


def _site_feed_items(doc: dict) -> list[dict]:
    """把站点 data.json 转成插件内部新闻条目格式。

    站点结构：dispatches[*] = {id, published, type, message}（原始英文，30 条）
              news[*]       = {id, original, translated, published_at, ...}（最新 10 条，带中文译文）
    转换后：{id, message, published, type, _zh}；_zh 为站点已给的中文译文（有则免翻译）。
    """
    if not isinstance(doc, dict):
        return []
    zh_by_id = {}
    for n in (doc.get("news") or []):
        if isinstance(n, dict) and n.get("id") is not None:
            t = (n.get("translated") or "").strip()
            if t:
                zh_by_id[str(n["id"])] = t
    out = []
    for d in (doc.get("dispatches") or []):
        if not isinstance(d, dict) or d.get("id") is None:
            continue
        out.append({
            "id": d.get("id"),
            "message": d.get("message") or "",
            "published": d.get("published") or "",
            "type": d.get("type"),
            "_zh": zh_by_id.get(str(d.get("id"))),
        })
    return out


def _fetch_site_feed() -> list[dict]:
    """读站点 data.json（信息流主源），带内存 + 文件缓存；失败抛异常由调用方回退"""
    now = time.time()
    if _NEWS_FEED_MEM["items"] and now - _NEWS_FEED_MEM["ts"] < NEWS_FEED_CACHE_TTL:
        return list(_NEWS_FEED_MEM["items"])
    doc = _site_doc()          # 与星球表/中文译文共用同一次抓取
    items = _site_feed_items(doc)
    if not items:
        raise ValueError("站点 data.json 无 dispatches 数据")
    _NEWS_FEED_MEM.update({"ts": now, "items": items, "source": "hd2map"})
    try:
        with open(NEWS_FEED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"ts": now, "url": NEWS_FEED_JSON_URL,
                       "fetchedAt": doc.get("fetchedAt"), "items": items}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[HD2] 站点信息流缓存写入失败: {e}")
    return items


def _load_site_feed_cache() -> list[dict]:
    """站点不可达时的本地兜底（缓存文件）"""
    try:
        if _os.path.exists(NEWS_FEED_CACHE_FILE):
            with open(NEWS_FEED_CACHE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            items = d.get("items") or []
            if items:
                logger.warning(f"[HD2] 站点信息流不可达，改用本地缓存（{len(items)} 条）")
                return items
    except Exception as e:
        logger.warning(f"[HD2] 站点信息流缓存读取失败: {e}")
    return []


def _api_feed_items(limit: int) -> list[dict]:
    """原来的实时抓取路径（兜底）：companion live API → 官方 NewsFeed"""
    obj = _fetch_json(LIVE_API_URL, timeout=20)
    news = obj.get("news") or []
    if not news:
        news = _fetch_official(f"/api/NewsFeed/{OFFICIAL_WAR_ID}?maxEntries=50", timeout=20)
    if not news:
        return []
    news = sorted(news, key=lambda n: n.get("id", 0), reverse=True)
    return news[:limit]


def _transnews_feed_items(limit: int) -> list[dict]:
    """翻译回退层：主站 data/translated/TransNews.json（翻译仓库推送到 Pages 的副本）

    结构：{items: [{id, original, translated, published_at, translated_at, text_source, mt}]}，
    转成与站点信息流相同的内部格式（带 _zh，免 LLM）。
    """
    if not _ds:
        return []
    try:
        t = _ds.transnews()
        items_out = []
        for n in t.get("items") or []:
            if not isinstance(n, dict) or n.get("id") is None:
                continue
            items_out.append({
                "id": n.get("id"),
                "message": n.get("original") or "",
                "published": n.get("published_at") or "",
                "type": None,
                "_zh": (n.get("translated") or "").strip() or None,
            })
        return items_out[:limit]
    except Exception as e:
        logger.debug(f"[HD2] TransNews 回退层不可用: {e}")
        return []


def _get_news_items(limit: int = 1) -> list[dict]:
    """取最新新闻（信息流）。

    优先级：站点 data.json（每 5 分钟更新，news[] 带官方中文）
          → data/translated/TransNews.json（站点合并滞后时的译文回退）
          → 实时 API（最后兜底）；全部失败时用本地缓存。
    返回条目含 `_zh`（站点已翻译的中文）时，后续无需再走 LLM 翻译。
    """
    if NEWS_FEED_SOURCE != "api":
        try:
            items = _fetch_site_feed()
            if items:
                logger.info(f"[HD2] 信息流来源：站点 data.json（{len(items)} 条）")
                return items[:limit]
        except Exception as e:
            logger.warning(f"[HD2] 站点信息流失败，回退 TransNews/实时 API: {e}")
            cached = _load_site_feed_cache()
            if cached:
                return cached[:limit]
        items = _transnews_feed_items(limit)
        if items:
            logger.info(f"[HD2] 信息流来源：TransNews 回退层（{len(items)} 条）")
            return items
    try:
        items = _api_feed_items(limit)
        if items:
            logger.info(f"[HD2] 信息流来源：实时 API（{len(items)} 条）")
        return items
    except Exception as e:
        logger.warning(f"[HD2] 获取新闻失败: {e}")
        return _load_site_feed_cache()[:limit]


# ---------------- 站点中文（MO 简报 / 资讯译文）现成复用 ----------------
_SITE_ZH = {"ts": 0.0, "brief": "", "brief_title": "", "news": {}}


# ---------------- 站点 data.json：一次抓取，多处复用 ----------------
# 信息流、MO 简报中文、新闻中文、星球表都来自同一个 JSON；
# 共用一份带 TTL 的内存缓存，避免同一份文件被重复请求 3 次（也少受网络抖动影响）。
_SITE_DOC = {"ts": 0.0, "doc": None}


def _site_doc(force: bool = False) -> dict:
    """取主站 data.json（hd2_datasource 统一缓存：内存 + 磁盘，ver=fetchedAt）；失败返回 {}"""
    if _ds:
        return _ds.site_doc(force=force)
    now = time.time()
    if not force and _SITE_DOC["doc"] and now - _SITE_DOC["ts"] < NEWS_FEED_CACHE_TTL:
        return _SITE_DOC["doc"]
    try:
        doc = _fetch_json(NEWS_FEED_JSON_URL, timeout=15)
        if isinstance(doc, dict) and doc:
            _SITE_DOC.update({"ts": now, "doc": doc})
            return doc
    except Exception as e:
        logger.warning(f"[HD2] 站点 data.json 抓取失败: {e}")
    return _SITE_DOC["doc"] or {}


def _site_zh(force: bool = False) -> dict:
    """取站点 data.json 里的现成中文：MO 简报译文 + 各条新闻译文（按 id）。

    主站已合并翻译仓库的官方中文（major_order.translated_brief 与 news[].translated），
    直接复用可让 /战报 完全不调 LLM。站点不可用时保留上一次取值，调用方按需回退。
    """
    if _ds:
        doc = _site_doc(force=force)
        if doc:
            brief = _ds.mo_brief_zh(doc)
            if brief:
                _SITE_ZH["brief"] = brief
            mo = doc.get("major_order") or {}
            if isinstance(mo, dict):
                _SITE_ZH["brief_title"] = (mo.get("title") or "").strip()
            news = _ds.news_zh_map(doc)
            if news:
                _SITE_ZH["news"] = news
            _SITE_ZH["ts"] = time.time()
        return _SITE_ZH
    doc = _site_doc(force=force)
    if isinstance(doc, dict) and doc:
        mo = doc.get("major_order") or {}
        brief = (mo.get("translated_brief") or "").strip()
        if brief:
            _SITE_ZH["brief"] = brief
        _SITE_ZH["brief_title"] = (mo.get("title") or "").strip()
        news = {}
        for n in (doc.get("news") or []):
            if not isinstance(n, dict) or n.get("id") is None:
                continue
            t = (n.get("translated") or "").strip()
            if t:
                news[str(n["id"])] = {"text": t, "ver": str(n.get("translated_at") or "")}
        if news:
            _SITE_ZH["news"] = news
        _SITE_ZH["ts"] = time.time()
    return _SITE_ZH


def _seed_site_news_cache(cache: dict) -> int:
    """把站点已有的中文译文**全部**播种进新闻缓存（不止当前要显示的那条）。

    为什么全播：否则同一份资讯列表里，可能"这一条用站点译文、那一条用本机 LLM 译文"，
    术语与文风不一致。全播之后，站点覆盖到的条目都统一用权威译文。
    返回新写入条数。
    """
    n = 0
    try:
        for nid, rec in (_SITE_ZH.get("news") or {}).items():
            if _news_should_update(cache.get(nid), rec.get("text"), "site", rec.get("ver") or ""):
                cache[nid] = _news_rec_make(rec["text"], "site", rec.get("ver") or "")
                n += 1
    except Exception as e:
        logger.warning(f"[HD2] 播种站点译文失败: {e}")
    return n



# ---------------- /战报 快照：后台预热写入，前台只读 ----------------
def _snapshot_ttl_for(quality: str) -> float:
    """快照有效期：间隙快照（MO 间隙）用较短值，内容本来就少，不必频繁现抓"""
    return float(min(REPORT_SNAPSHOT_TTL, 300)) if quality == "gap" else float(REPORT_SNAPSHOT_TTL)


def _load_report_snapshot() -> tuple:
    """读快照：(text, ts, quality)；无/损坏返回 ("", 0, "")"""
    try:
        if _os.path.exists(REPORT_SNAPSHOT_FILE):
            with open(REPORT_SNAPSHOT_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return (d.get("text") or ""), float(d.get("ts") or 0), str(d.get("quality") or "")
    except Exception as e:
        logger.warning(f"[HD2] 读取战报快照失败: {e}")
    return "", 0.0, ""


def _save_report_snapshot(text: str, source: str = "", quality: str = "ok") -> None:
    """原子写快照（先临时文件再 replace，避免前端读到半截）"""
    try:
        tmp = REPORT_SNAPSHOT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "source": source, "quality": quality, "text": text},
                      f, ensure_ascii=False)
        _os.replace(tmp, REPORT_SNAPSHOT_FILE)
    except Exception as e:
        logger.warning(f"[HD2] 保存战报快照失败: {e}")


def _report_snapshot_text() -> str:
    """取快照文本（优先内存，其次磁盘）；按快照自身的 quality 决定有效期"""
    now = time.time()
    if _REPORT_WARM["text"]:
        ttl = _snapshot_ttl_for(_REPORT_WARM.get("quality") or "ok")
        if now - _REPORT_WARM["ts"] <= ttl:
            return _REPORT_WARM["text"]
    text, ts, quality = _load_report_snapshot()
    if text and now - ts <= _snapshot_ttl_for(quality):
        _REPORT_WARM.update({"ts": ts, "text": text, "source": "disk", "quality": quality})
        return text
    return ""


def _build_report_parts(planet_map: dict, brief_cn: str | None, news_text: str) -> tuple:
    """组装 /战报 正文（纯格式化：抓 MO + 战区分布，不调 LLM）

    返回 (正文, quality)：
      quality = "ok"    数据完整可缓存
              = "gap"   当前处于 MO 间隙（正常状态，但没新数据，无需反复刷新快照）
              = "broken" MO 取数失败（保留旧快照更好）
    """
    mo = None
    # MO 缓存：最近 5 条新闻没有 NEW MAJOR ORDER 时复用缓存（MO 未更新，省一次抓取）
    if not _has_recent_new_major_order():
        mo = _load_mo_cache()
    if mo is None:
        mo = _get_major_order(planet_map, brief_cn=brief_cn)
        if mo:
            _save_mo_cache(mo)
    warzone = _get_warzone_distribution()
    parts = []
    quality = "ok"
    if mo:
        parts.append(mo)
        if "当前没有进行中的重要指令" in mo:
            quality = "gap"
    else:
        parts.append("⚠️ 暂时无法获取 Major Order 数据。")
        quality = "broken"
    if warzone:
        parts.append(warzone)
    if news_text:
        parts.append(news_text)
    return "\n\n\n".join(parts), quality

def _get_warzone_distribution() -> str:
    """战区玩家分布（超级地球/终结族/机器人/光能者）带仰齐浜时间

    主源：主站 data.json 的 player_distribution（与主站首页同口径）；
    兜底：companion extendedApiInformation（旧实现）。
    """
    import datetime

    if _ds:
        try:
            pd = _ds.player_distribution()
            total = pd.get("total")
            if total is not None:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
                time_str = now.strftime("%H:%M:%S")
                factions = [
                    ("🌏 超级地球", pd.get("humans") or 0),
                    ("🕷 终结族", pd.get("terminids") or 0),
                    ("🤖 机器人", pd.get("automatons") or 0),
                    ("👽 光能者", pd.get("illuminate") or 0),
                ]
                lines = [f"🌍 战区玩家分布（仰齐浜时间 {time_str}）"]
                for name, cnt in factions:
                    pct = (cnt / total * 100) if total else 0
                    lines.append(f"• {name}：{cnt:,} 人（{pct:.1f}%）")
                lines.append(f"总计：{total:,} 名玩家在线")
                return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[HD2] 站点玩家分布失败，回退 companion: {e}")
    try:
        obj = _fetch_json(EXTENDED_API_URL, timeout=20)
        data = obj.get("data") or []
        latest = data[-1] if data else {}
        total = latest.get("totalPlayerCount") or 0
        factions = [
            ("🌏 超级地球", latest.get("playerCountHumans") or 0),
            ("🕷 终结族", latest.get("playerCountTerminids") or 0),
            ("🤖 机器人", latest.get("playerCountAutomatons") or 0),
            ("👽 光能者", latest.get("playerCountIlluminate") or 0),
        ]
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        time_str = now.strftime("%H:%M:%S")
        lines = [f"🌍 战区玩家分布（仰齐浜时间 {time_str}）"]
        for name, cnt in factions:
            pct = (cnt / total * 100) if total else 0
            lines.append(f"• {name}：{cnt:,} 人（{pct:.1f}%）")
        lines.append(f"总计：{total:,} 名玩家在线")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[HD2] 获取战区分布失败: {e}")
        return ""

def _clean_news_message(text: str, limit: int = 600) -> str:
    """清理新闻富文本标签（<i=1> 等）并截断"""
    import re

    text = text or ""
    text = re.sub(r"<[^>]+>", "", text)  # 去 <i=1> 等标签
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]

# DSS 战术行动中英对照（与星球信息插件一致）
DSS_ACTION_CN = {
    "EAGLE STORM": ("✈ 飞鹰风暴", "DSS的工作人员在大型三层结构附近集结了一支“飞鹰”编队，采取24小时轮班制，为星球上的绝地潜兵提供火力压制支援。在此期间，“飞鹰风暴” 将作为行动变量周期性在任务中部署。若该星球遭到入侵，DSS将会守住防线，阻止敌方入侵进度；在地面执行任务的绝地潜兵小队队长可通过 “标记目标” 功能为 “飞鹰” 标记敌人。"),
    "ORBITAL BLOCK": ("🚫 轨道封锁", "DSS启用零重力反舰导弹系统，将会瞄准该星球上所有试图离开的敌方目标。同时将多余战力投入到超级驱逐舰后勤支援。在此期间，在该星球执行任务的小队将自动激活 “强化资源：绝地喷射仓空间优化” ；且该星球上的敌军无法对其他星球发动入侵战役。"),
    "HEAVY ORDNANCE DISTRIBUTION": ("💣 重型军械分发", "DSS后勤舰队将为环绕星球的超级驱逐舰提供富余的380MM高爆弹，SEAF部队的行动也将获得装备支援，加速解放进程。在此期间，在该星球执行任务的小队将获得额外的战略配备：轨道380 MM高爆弹火力网；地面上的SEAF部队将获得更高级的武器支援；且DSS将会额外推进该星球的解放进程。"),
    "EAGLE BLOCK": ("🚫✈🚫 飞鹰封锁", "在该效果激活期间，将同时激活DSS战术行动：“飞鹰风暴”与“轨道封锁”。"),
    "PLANETARY BOMBARDMENT": ("💣🌏💣 星球轰炸", "DSS上搭载的76门“总统级”舰炮同时开火，轰炸整个星球。在此期间，所有小队 +1 可用增援； 且DSS将会额外推进该星球的解放进程。注意了：炮弹可不会区分正义与邪恶！"),
    "OPERATIONAL SUPPORT": ("✊ 行动支持", "DSS停靠在这颗星球附近，一定程度上有助于该星球的解放战争进程。在该星球执行任务的小队所携带的所有“外骨骼机甲”战略配备冷却时间减少35%。"),
}

# owner 枚举（companion）：1=超级地球 2=终结族 3=机器人 4=光能族（campaign.race 字段不可靠，勿用）
# campaign.type：0=解放战（进攻） 1=防御战
OWNER_ID_CN = {1: "超级地球", 2: "终结族", 3: "机器人", 4: "光能族"}


# activeEffectIds 包含同一个战术行动的实现、地图标记和子效果 ID。
# 先按战术行动归一化，避免“飞鹰风暴”等效果被重复输出。
DSS_EFFECT_ACTION_KEYS = {
    1208: "PLANETARY BOMBARDMENT",
    1209: "EAGLE STORM",
    1210: "ORBITAL BLOCK",
    1211: "PLANETARY BOMBARDMENT",
    1212: "EAGLE STORM",
    1213: "ORBITAL BLOCK",
    1214: "HEAVY ORDNANCE DISTRIBUTION",
    1215: "ORBITAL BLOCK",
    1216: "EAGLE STORM",
    1237: "HEAVY ORDNANCE DISTRIBUTION",
    1238: "OPERATIONAL SUPPORT",
    1425: "EAGLE STORM",
}


def _normalize_dss_action_key(name: str) -> str:
    """把 API 行动名归一化为稳定键，兼容 BLOCKADE 与带后缀的标记效果。"""
    key = re.sub(r"<[^>]+>", "", str(name or "")).upper()
    key = key.replace("BLOCKADE", "BLOCK")
    key = re.sub(r"\s*\([^)]*\)\s*$", "", key)
    return re.sub(r"\s+", " ", key).strip()


def _collect_active_dss_effects(
    space_station: dict,
    effect_cn: dict | None = None,
    effect_desc: dict | None = None,
) -> list[dict]:
    """收集并归并真正生效的 DSS 效果。

    1. tacticalActions 只在 status=2 或 activeEffectIds 非空时视为生效；
    2. 顶层 activeEffectIds 按战术行动族归并，保留首次出现的顺序；
    3. 外骨骼机甲支援等子效果不单独输出。
    """
    effect_cn = effect_cn or {}
    effect_desc = effect_desc or {}
    result = []
    seen = set()

    def add_effect(eid=None, action_name: str = "", action_desc: str = "") -> None:
        try:
            eid_int = int(eid) if eid is not None else None
        except (TypeError, ValueError):
            eid_int = None
        if eid_int in DSS_EXOSUIT_IDS:
            return

        key = DSS_EFFECT_ACTION_KEYS.get(eid_int) or _normalize_dss_action_key(action_name)
        if not key or key in seen:
            return

        if key in DSS_ACTION_CN:
            cn, desc = DSS_ACTION_CN[key]
        else:
            cn = effect_cn.get(eid_int) or action_name or f"效果{eid_int}"
            desc = effect_desc.get(eid_int, "") or _clean_news_message(action_desc, limit=600)

        seen.add(key)
        result.append({"key": key, "cn": cn, "desc": desc})

    for action in space_station.get("tacticalActions") or []:
        active_ids = action.get("activeEffectIds") or []
        try:
            status = int(action.get("status", -1))
        except (TypeError, ValueError):
            status = -1
        if status != 2 and not active_ids:
            continue

        name = action.get("name") or ""
        if not name and active_ids:
            try:
                name = effect_cn.get(int(active_ids[0]), "")
            except (TypeError, ValueError):
                name = ""
        add_effect(
            action_name=name,
            action_desc=action.get("description") or action.get("strategicDescription") or "",
        )

    for eid in space_station.get("activeEffectIds") or []:
        add_effect(eid=eid)

    return result


def _format_dss_effect_lines(effects: list[dict]) -> list[str]:
    """格式化已归并的 DSS 生效效果。"""
    lines = []
    for effect in effects:
        line = f"• {effect['cn']}"
        if effect.get("desc"):
            line += f"\n  {effect['desc']}"
        lines.append(line)
    return lines


def _campaign_race_cn(c: dict, p: dict) -> str:
    """从 campaign 星球的 owner 判断敌方阵营（campaign.race 不可靠）"""
    owner = p.get("owner")
    if owner is None:
        return f"阵营?"
    return OWNER_ID_CN.get(owner, f"阵营{owner}")


def _dss_tac_name_cn(name_en: str) -> str:
    """战术行动英文名 -> 中文（兼容站点 ORBITAL BLOCKADE 与旧表 ORBITAL BLOCK 两种拼写）"""
    key = (name_en or "").upper()
    key = key.replace(" BLOCKADE", " BLOCK")
    if key in DSS_ACTION_CN:
        return DSS_ACTION_CN[key][0]
    return name_en or ""


def _dss_tac_desc_cn(name_en: str) -> str:
    key = (name_en or "").upper().replace(" BLOCKADE", " BLOCK")
    if key in DSS_ACTION_CN:
        return DSS_ACTION_CN[key][1]
    return ""


def _strategic_line() -> str:
    """战略机遇（STRATEGIC IMPERATIVE）状态行（主站 strategic，含官方中文）"""
    if not _ds:
        return ""
    try:
        s = _ds.strategic()
        if not s:
            return ""
        status_cn = (s.get("status_cn") or "").strip()
        active = bool(s.get("active"))
        if not status_cn and not active:
            return ""
        planet = ""
        pi = s.get("planet_index")
        if pi is not None:
            info = _load_planet_table()[0].get(str(pi)) or {}
            en = info.get("name", "")
            planet = _planet_cn(en)
        head = "🎯 战略机遇：" + ("进行中" if active and not status_cn else status_cn)
        if planet:
            head += f"（{planet}）"
        return head
    except Exception:
        return ""


async def _get_dss_status(planet_map: dict, context: Context | None = None) -> str:
    """查询 DSS：停靠星球 + 生效效果 + 战术行动捐献 + 投票剩余

    主源：主站 data.json 的 dss（含 tacticalActions 捐献进度与资源限额，与主站 dss-donations.js 同口径）；
    备选：官方 API（spaceStations.activeEffectIds）；最后 community companion。
    context 参数保留兼容（站点已提供官方中文，不再对 DSS 资讯做 LLM 翻译）。
    """
    if _ds:
        try:
            d = _ds.dss(_site_doc())
            if d and d.get("planetIndex") is not None:
                return _format_dss_site(d, planet_map)
        except Exception as e:
            logger.warning(f"[HD2] 站点 DSS 失败，回退官方 API: {e}")
    return await _get_dss_status_official(planet_map)


def _format_dss_site(d: dict, planet_map: dict) -> str:
    """主站 dss 结构 -> 文本（dss-donations.js 口径）"""
    idx = str(d.get("planetIndex", ""))
    info = planet_map.get(idx, {})
    en_name = info.get("name", "")
    name = _planet_cn(en_name) or f"星球#{idx}"
    sector = info.get("sector", "")
    sector_cn = (_load_starmap_sectors().get(en_name.upper())) or SECTOR_CN.get(sector, sector)
    lines = ["🛰️ DSS 民主空间站"]
    lines.append(f"📍 当前停靠：{name}" + (f"（{sector_cn}）" if sector_cn else ""))
    strat = _strategic_line()
    if strat:
        lines.append(strat)

    eff_ids = d.get("activeEffectIds") or []
    tac = _ds.dss_tactical_actions(d) if _ds else (d.get("tacticalActions") or [])
    # 战术行动已覆盖的效果 ID 不再单独列原始效果（避免"效果1237"与"重型军械分发"重复）
    covered = set()
    for t in tac if isinstance(tac, list) else []:
        if isinstance(t, dict):
            for eid in (t.get("effectIds") or []) + (t.get("activeEffectIds") or []):
                covered.add(eid)
    shown = []
    for eid in eff_ids if isinstance(eff_ids, list) else []:
        if eid in DSS_EXOSUIT_IDS or eid in covered:
            continue
        if eid not in shown:
            shown.append(eid)
    if shown:
        lines.append("⚡ 生效效果（官方数据）：")
        eff_cn = _load_dss_effect_cn()
        eff_desc = _load_dss_effect_desc()
        for eid in shown:
            line = f"• {eff_cn.get(eid, f'效果{eid}')}"
            desc = eff_desc.get(eid, "")
            if desc:
                line += f"\n  {desc}"
            lines.append(line)

    if tac:
        lines.append("🗳️ 战术行动：")
        for t in tac:
            if not isinstance(t, dict):
                continue
            name_en = (t.get("name") or "").upper()
            cn_t = _dss_tac_name_cn(name_en) or name_en
            status_v = _ds.TACTICAL_STATUS_CN.get(t.get("status"), f"状态{t.get('status')}") if _ds \
                else {0: "离线", 1: "投票中", 2: "已激活", 3: "恢复中"}.get(t.get("status"), f"状态{t.get('status')}")
            icon = {2: "🟢", 3: "❄"}.get(t.get("status"), "⏳")
            # 捐献进度只在筹集期（投票中）展示；已激活行动的 cost 归零不代表"筹集 0%"
            pct = _ds.dss_donation_pct(t) if (_ds and t.get("status") == 1) else None
            line = f"{icon} {status_v}：{cn_t}（{name_en}）"
            if pct is not None:
                line += f" · 已筹集 {pct:.1f}%"
            lines.append(line)
            desc = _dss_tac_desc_cn(name_en)
            if t.get("status") == 2 and desc:
                lines.append(f"  {desc}")
            for rl in (_ds.dss_resource_lines(t) if _ds else []):
                lines.append(f"  · {rl}")
    snap = _ds.dss_snapshot_label(d) if _ds else ""
    if snap and "未知" not in snap:
        lines.append(f"🎁 {snap}")

    end = d.get("electionEndWarTime")
    now = d.get("currentWarTime")
    if isinstance(end, (int, float)) and isinstance(now, (int, float)):
        lines.append(f"🗳️ 下一轮投票剩余 {_fmt_seconds(end - now)}")
    supp = _dss_news_supplement()
    if supp:
        lines.extend(supp)
    return "\n".join(lines)


async def _get_dss_status_official(planet_map: dict) -> str:
    """官方/社区 API 路径的 DSS 查询（主站不可用时的兜底，逻辑与旧版一致）"""
    try:
        _cache_dss_news()
        st = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status", timeout=20)
        ss_list = st.get("spaceStations") or []
        if not ss_list:
            return "\n".join(["🛰️ 当前无 DSS 数据（DSS 可能离线或移动中）。"] + _dss_news_supplement())
        ss = ss_list[0]
        idx = str(ss.get("planetIndex", ""))
        info = planet_map.get(idx, {})
        en_name = info.get("name", "")
        name = _planet_cn(en_name) or f"星球#{idx}"
        sector = info.get("sector", "")
        # 星系显示：查本地星图表（对照表 星球英文名 -> 星区），官方 sector 为数字/Unknown 不可靠
        sector_cn = (_load_starmap_sectors().get(en_name.upper())) or SECTOR_CN.get(sector, sector)
        lines = ["🛰️ DSS 民主空间站"]
        lines.append(f"📍 当前停靠：{name}" + (f"（{sector_cn}）" if sector_cn else ""))
        strat = _strategic_line()
        if strat:
            lines.append(strat)

        # 生效效果：按战术行动归并 activeEffectIds，避免同一行动的多个子效果重复输出
        effects = _collect_active_dss_effects(ss, _load_dss_effect_cn(), _load_dss_effect_desc())
        if effects:
            lines.append("⚡ 生效效果（官方数据）：")
            lines.extend(_format_dss_effect_lines(effects))

        # 投票剩余
        end = ss.get("currentElectionEndWarTime")
        now = st.get("time")
        if end and now:
            lines.append(f"🗳️ 下一轮投票剩余 {_fmt_seconds(end - now)}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[HD2] 官方 DSS 失败，回退社区源: {e}")

    # ---- 社区备选（companion tacticalActions）----
    try:
        obj = _fetch_json(LIVE_API_URL, timeout=20)
        ws = obj.get("warStatus") or {}
        ss_list = obj.get("spaceStations") or ws.get("spaceStations") or []
        if not ss_list:
            return "\n".join(["🛰️ 当前无 DSS 数据（DSS 可能离线或移动中）。"] + _dss_news_supplement())
        ss = ss_list[0]
        idx = str(ss.get("planetIndex", ""))
        info = planet_map.get(idx, {})
        en_name = info.get("name", "")
        name = _planet_cn(en_name) or f"星球#{idx}"
        sector = info.get("sector", "")
        # 星系显示：查本地星图表（对照表 星球英文名 -> 星区），官方 sector 为数字/Unknown 不可靠
        sector_cn = (_load_starmap_sectors().get(en_name.upper())) or SECTOR_CN.get(sector, sector)
        lines = ["🛰️ DSS 民主空间站"]
        lines.append(f"📍 当前停靠：{name}" + (f"（{sector_cn}）" if sector_cn else ""))
        strat = _strategic_line()
        if strat:
            lines.append(strat)

        # 社区源也混有“候选/恢复中”行动，只渲染已生效项，不展示整张战术行动表
        effects = _collect_active_dss_effects(ss, _load_dss_effect_cn(), _load_dss_effect_desc())
        if effects:
            lines.append("⚡ 生效效果（社区数据）：")
            lines.extend(_format_dss_effect_lines(effects))

        end = ss.get("currentElectionEndWarTime")
        now = ws.get("time")
        if end and now:
            lines.append(f"🗳️ 下一轮投票剩余 {_fmt_seconds(end - now)}")
        return "\n".join(lines)
    except Exception as e2:
        logger.warning(f"[HD2] DSS 查询失败: {e2}")
        supp = _dss_news_supplement()
        if supp:
            return f"⚠️ DSS 数据不可用（{e2}）\n" + "\n".join(supp[1:])
        return f"⚠️ DSS 查询失败：{e2}"


def _get_ownership_changes(hours: int = 24) -> str:
    """近 N 小时星图归属变化（主站 ownership_history，含防守成功，官方事件观测口径）"""
    if not _ds:
        return "⚠️ 星图变化数据源不可用（hd2_datasource 未加载）。"
    try:
        view = _ds.ownership_changes(hours=hours)
    except Exception as e:
        logger.warning(f"[HD2] 归属变化计算失败: {e}")
        view = None
    if view is None:
        return "📊 星图归属变化\n暂无归属历史记录（主站数据缺失）。"
    planet_map = _load_planet_map()
    lines = [f"📊 近 {hours} 小时星图归属变化"]
    meta = [f"{len(view['rows'])} 颗星球", f"{view['changes']} 次易主", f"{view['defenses']} 次防守成功"]
    if view.get("coverage_hours") and view["coverage_hours"] < hours - 0.1:
        meta.append(f"记录覆盖约 {view['coverage_hours']:.1f} 小时")
    if view.get("stale"):
        meta.append("数据更新延迟")
    lines.append(" · ".join(meta))
    if not view["rows"]:
        lines.append("已记录时段内无阵营变化或防守成功。")
        return "\n".join(lines)
    for row in view["rows"][:12]:   # QQ 消息紧凑：最多列 12 颗
        pi = row["planet_index"]
        info = planet_map.get(str(pi)) or {}
        en = info.get("name", "")
        name = _planet_cn(en) or f"星球#{pi}"
        segs = []
        for path in row["paths"]:
            names = [_ds.faction_cn(f) or str(f) for f in path]
            segs.append("→".join(names))
        for ev in row["victories"]:
            attacker = _ds.faction_cn(ev.get("attacker")) or ""
            segs.append("🛡 防守成功" + (f" · 抵御{attacker}" if attacker else ""))
        line = f"• {name}：{'；'.join(segs)}"
        if row.get("uncertain"):
            line += "（范围待确认）"
        lines.append(line)
    if len(view["rows"]) > 12:
        lines.append(f"…另有 {len(view['rows']) - 12} 颗星球变化未列出")
    return "\n".join(lines)


def _load_sector_cn_map(planet_map: dict) -> dict:
    """构建 sector id -> 星区显示名（通过星球英文名关联 planet_map 的 sector 字段）"""
    try:
        starmap = _load_starmap_sectors()
        if not starmap:
            return {}
        sec_map = {}
        for info in planet_map.values():
            sid = info.get("sector") or ""
            disp = starmap.get((info.get("name") or "").upper())
            if sid and disp:
                sec_map[sid] = disp
        return sec_map
    except Exception:
        return {}


def _load_warp_routes() -> dict:
    """官方源：WarInfo.waypoints -> {index: [跃迁连接星球index, ...]}"""
    try:
        wi = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/WarInfo", timeout=20)
        return {p.get("index"): (p.get("waypoints") or []) for p in wi.get("planetInfos") or []}
    except Exception as e:
        logger.warning(f"[HD2] 官方 waypoints 获取失败: {e}")
        return {}


def _compute_warp_blockades(routes: dict, ps: dict, camp_idx: set, defend_idx: set) -> dict:
    """计算跃迁航道封锁（Warp Link Blockade）：
    光能族占领(owner=4) + 无进行中战役 + 存在我方(owner=1)未被防御的跃迁连接点 -> 无法解放
    返回 {planet_idx: [我方连接星球idx, ...]}"""
    block = {}
    for idx in routes:
        p = ps.get(idx, {})
        if p.get("owner") != 4:
            continue
        if idx in camp_idx:
            continue
        # 双向连接：出站 waypoints + 入站（其他星球 waypoints 含 idx）
        conn = set(routes.get(idx, []))
        for w, wps in routes.items():
            if idx in wps:
                conn.add(w)
        links = [w for w in conn if ps.get(w, {}).get("owner") == 1 and w not in defend_idx]
        if links:
            block[idx] = links
    return block


def _fmt_warp_blockade(idx: int, links: list, planet_map: dict) -> str:
    """格式化跃迁封锁描述：通往{我方连接星球}的跃迁航道为单向航道"""
    names = []
    for w in links:
        info = planet_map.get(str(w), {})
        en = info.get("name", "")
        names.append(_planet_cn(en) or f"星球#{w}")
    nm = "、".join(names)
    return f"由于此地通往[{nm}]的跃迁航道为单向航道，目前无法对该地的光能族发动解放战争。"


def _warp_blockade_lines(planet_map: dict) -> list:
    """汇总当前全部跃迁封锁星球（官方优先，companion 兜底；供战线/战役附加显示）"""
    routes = {}
    try:
        # 官方源优先
        st = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status", timeout=20)
        wi = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/WarInfo", timeout=20)
        routes = {p.get("index"): (p.get("waypoints") or []) for p in wi.get("planetInfos") or []}
    except Exception as e:
        logger.warning(f"[HD2] 官方封锁数据失败，回退社区源: {e}")
        try:
            obj = _fetch_json(LIVE_API_URL, timeout=20)
            st = obj.get("warStatus") or {}
            wi = obj.get("warInfo") or {}
            routes = {p.get("index"): (p.get("waypoints") or []) for p in wi.get("planetInfos") or []}
        except Exception as e2:
            logger.warning(f"[HD2] 社区封锁数据失败: {e2}")
            return []
    try:
        ps = {p.get("index"): p for p in st.get("planetStatus") or []}
        camp_idx = set(c.get("planetIndex") for c in (st.get("campaigns") or []))
        defend_idx = set(ev.get("planetIndex") for ev in (st.get("planetEvents") or []))
        if not routes:
            return []
        block = _compute_warp_blockades(routes, ps, camp_idx, defend_idx)
        lines = []
        for idx in sorted(block):
            links = block[idx]
            info = planet_map.get(str(idx), {})
            en = info.get("name", "")
            pname = _planet_cn(en) or f"星球#{idx}"
            lines.append(f"• {pname}：{_fmt_warp_blockade(idx, links, planet_map)}")
        return lines
    except Exception as e:
        logger.warning(f"[HD2] 跃迁封锁计算失败: {e}")
        return []


def _load_dss_effect_cn() -> dict:
    """DSS 效果关联：对照表 TACTICAL ACTION [效果ID] 标注优先 + dss_effects.json 补充 + effect_id_cn 兜底。
    路径链：HD2_PROJECT_DIR -> 插件目录"""
    def _table_candidates(fname: str) -> list:
        cands = []
        root = _os.environ.get("HD2_PROJECT_DIR", "")
        if root:
            cands.append(_os.path.join(root, "tables", fname))
        here = _os.path.dirname(_os.path.abspath(__file__))
        cands.append(_os.path.join(here, fname))
        cands.append(_os.path.join(here, "tables", fname))
        return cands

    out = {}

    # 1) 对照表 TACTICAL ACTION 解析（真源，优先）
    for p in _table_candidates("HD2行动变量对照表.md"):
        if not _os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("|") or "[" not in line:
                        continue
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if len(cells) < 3:
                        continue
                    mm = re.search(r"\[(\d+(?:/\d+)*)\]", cells[0])
                    if mm and cells[1]:
                        for eid in mm.group(1).split("/"):
                            out[int(eid)] = cells[1]
            if out:
                break
        except Exception:
            pass

    # 2) dss_effects.json 补充（对照表未覆盖的：1217/1260/1261/1262/1384/1385 等）
    for p in _table_candidates("dss_effects.json"):
        if not _os.path.exists(p):
            continue
        try:
            import json as _json2
            with open(p, encoding="utf-8") as f:
                d = _json2.load(f)
            for k, v in d.items():
                eid = int(k)
                if eid not in out:
                    out[eid] = v.get("cn_full") or v.get("cn") or ""
        except Exception:
            pass

    # 3) 通用效果表兜底
    if not out:
        out = _load_effect_id_cn()
    # 过滤外骨骼机甲支援效果（已含于行动支持，不单独显示）
    for _eid in DSS_EXOSUIT_IDS:
        out.pop(_eid, None)
    return out


# 外骨骼机甲支援效果（已含于行动支持描述，抓到也过滤不显示）
DSS_EXOSUIT_IDS = {1260, 1261, 1262, 1384, 1385}


# DSS 相关资讯缓存文件（涉及 DSS 的最新资讯，DSS 离线/不可用时作补充说明）
DSS_NEWS_CACHE = _os.path.join(CACHE_DIR, "dss_news_cache.json")
_DSS_NEWS_KEYWORDS = ("dss", "democracy space station", "space station", "station", "convocation bay")


def _cache_dss_news() -> list:
    """抓最新资讯，缓存涉及 DSS 的条目到本地文件（纯原文，供异步翻译前落盘）

    返回本次命中的条目列表（供调用方异步 LLM 翻译后再写回）
    """
    try:
        obj = _fetch_json(LIVE_API_URL, timeout=20)
        news = obj.get("news") or []
        hits = []
        for n in sorted(news, key=lambda x: x.get("id", 0), reverse=True):
            msg = str(n.get("message", ""))
            low = msg.lower()
            if any(k in low for k in _DSS_NEWS_KEYWORDS):
                hits.append({"id": n.get("id"), "message": _clean_news_message(msg, limit=400)})
                if len(hits) >= 1:  # 与战报 MO 缓存同理：只保留最近一条
                    break
        if hits:
            data = {"ts": int(time.time()), "items": hits}
            with open(DSS_NEWS_CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        return hits
    except Exception as e:
        logger.warning(f"[HD2] DSS 资讯缓存失败: {e}")
        return []


def _load_dss_news_cache() -> list:
    """读取 DSS 资讯缓存文件（返回条目列表）"""
    try:
        if _os.path.exists(DSS_NEWS_CACHE):
            with open(DSS_NEWS_CACHE, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("items", []) or []
    except Exception:
        pass
    return []


def _dss_news_supplement() -> list:
    """DSS 离线/不可用时：从缓存读取 DSS 相关资讯作补充说明。

    译文优先级：主站 data.json 官方中文（news_zh_map）> 本地缓存已译 > 英文原文。
    不再调用 LLM 翻译（站点中文已覆盖 DSS 相关资讯）。
    """
    items = _load_dss_news_cache()
    if not items:
        return []
    site_news = _ds.news_zh_map(_site_doc()) if _ds else {}
    lines = ["", "📰 DSS 相关资讯（缓存）："]
    for it in items[:1]:  # 与战报 MO 缓存同理：只显示最近一条
        nid = str(it.get("id"))
        msg = ""
        if nid in site_news:
            msg = site_news[nid].get("text") or ""
        if not msg:
            msg = it.get("cn") or it.get("message", "")
        lines.append(f"• [{it.get('id')}] {msg[:120]}")
    return lines


def _load_dss_effect_desc() -> dict:
    """DSS 效果描述：对照表 TACTICAL ACTION [效果ID] -> 描述列（行动支持等有完整描述）"""
    def _table_candidates(fname: str) -> list:
        cands = []
        root = _os.environ.get("HD2_PROJECT_DIR", "")
        if root:
            cands.append(_os.path.join(root, "tables", fname))
        here = _os.path.dirname(_os.path.abspath(__file__))
        cands.append(_os.path.join(here, fname))
        cands.append(_os.path.join(here, "tables", fname))
        return cands

    out = {}
    for p in _table_candidates("HD2行动变量对照表.md"):
        if not _os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("|") or "[" not in line:
                        continue
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if len(cells) < 3:
                        continue
                    mm = re.search(r"\[(\d+(?:/\d+)*)\]", cells[0])
                    if mm and cells[2]:
                        for eid in mm.group(1).split("/"):
                            out[int(eid)] = cells[2]
            if out:
                break
        except Exception:
            pass
    return out


def _load_maxhealth_map() -> dict:
    """maxHealth 数据源：优先实时 live API 的 warInfo.planetInfos（275 星球，不受 helldivers2.dev 宕机影响），回退本地缓存文件"""
    try:
        try:
            wi = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/WarInfo", timeout=20)
            pinfos = wi.get("planetInfos") or []
        except Exception as e:
            logger.warning(f"[HD2] 官方 WarInfo 失败，回退社区源: {e}")
            obj = _fetch_json(LIVE_API_URL, timeout=20)
            pinfos = obj.get("warInfo", {}).get("planetInfos") or []
        if pinfos:
            return {str(p.get("index")): {"maxHealth": p.get("maxHealth")} for p in pinfos}
    except Exception:
        pass
    try:
        import os as _os2, json as _json2
        root = _os2.environ.get("HD2_PROJECT_DIR", "")
        path = _os2.path.join(root, "temp", "campaign_maxhealth.json")
        if not _os2.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            d = _json2.load(f)
        return d.get("planets", {})
    except Exception:
        return {}


def _camp_display_name(camp: dict, planet_map: dict) -> str:
    p = camp.get("planet") or {}
    idx = str(p.get("index", ""))
    en = p.get("name") or (planet_map.get(idx) or {}).get("name", "")
    name = _planet_cn(en) if en else ""
    return name or f"星球#{idx}"


def _camp_line_site(camp: dict, planet_map: dict, doc: dict) -> str | None:
    """单条战线（主站口径）：解放战百分比 / 防御战赛跑预测 / 侦察战"""
    if not _ds:
        return None
    try:
        name = _camp_display_name(camp, planet_map)
        ctype = str(camp.get("campaignType") or ("defense" if _ds.is_defense(camp) else "liberation")).lower()
        if ctype == "recon":
            return f"• {name}｜🔍 侦察战"
        if ctype == "defense" or _ds.is_defense(camp):
            f = _ds.defense_model(camp)
            inv = camp.get("invasionLevel")
            inv_s = f" · 入侵等级{int(inv)}" if isinstance(inv, (int, float)) else ""
            if f["def"] is None:
                return f"• {name}｜🛡 防御战（进度未知）{inv_s}"
            race_cn = _ds.faction_cn(camp.get("faction")) or "敌方"
            remain = f["remaining_h"]
            remain_s = _ds.fmt_seconds(remain * 3600) if remain is not None else "?"
            result = "预计守住" if f["win"] else ("预计失守" if f["win"] is False else "数据不足")
            rate_s = f" · 防守{f['rate']:.2f}%/h" if f["rate"] is not None else ""
            src_s = "实测" if f["source"] == "实测防守速率" else "推演"
            return (f"• {name}｜🛡 防御战 vs {race_cn}｜我方 {f['def']:.1f}%"
                    + (f"（敌方 {f['atk']:.1f}%）" if f["atk"] is not None else "")
                    + f"｜截止 {remain_s}{inv_s}\n  预测：{result}{rate_s}（{src_s}）")
        # 解放战：星球血量即进度（主站 planets[]，camp.planet 不带血量）
        idx = str((camp.get("planet") or {}).get("index", ""))
        info = planet_map.get(idx) or {}
        hp, maxh = info.get("health"), info.get("maxHealth")
        if isinstance(hp, (int, float)) and isinstance(maxh, (int, float)) and maxh > 0:
            pct = (maxh - hp) / maxh * 100
            race_cn = _ds.faction_cn(camp.get("faction")) or _ds.faction_cn(info.get("owner")) or "敌方"
            meas = None
            try:
                meas = _ds.planet_measured({"index": int(idx) if str(idx).isdigit() else idx,
                                            "maxHealth": maxh, "resistance": info.get("resistance")})
            except Exception:
                meas = None
            rate_s = f"｜实测 {meas['net']:+.3f}%/h" if meas else ""
            return f"• {name}｜⚔️ vs {race_cn}｜解放 {pct:.2f}%{rate_s}"
        return f"• {name}｜⚔️ vs {_ds.faction_cn(camp.get('faction')) or '敌方'}"
    except Exception as e:
        logger.debug(f"[HD2] 站点战线条目失败: {e}")
        return None


def _get_campaigns(planet_map: dict) -> str:
    """当前全部入侵/防御战役列表：主站 data.json 优先（含防御赛跑预测），官方 API 兜底"""
    if _ds:
        try:
            doc = _site_doc()
            camps = _ds.campaigns(doc)
            if camps:
                lines = [f"⚔️ 当前战线（{len(camps)} 个）："]
                skipped = 0
                for c in camps:
                    line = _camp_line_site(c, planet_map, doc)
                    if line:
                        lines.append(line)
                    else:
                        skipped += 1
                if skipped:
                    lines.append(f"（另有 {skipped} 条战线数据不完整）")
                blines = _warp_blockade_lines(planet_map)
                if blines:
                    lines.append("")
                    lines.append("🚀 跃迁航道封锁（无法发动解放）：")
                    lines.extend(blines)
                return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[HD2] 站点战线失败，回退官方 API: {e}")
    return _get_campaigns_official(planet_map)


def _get_campaigns_official(planet_map: dict) -> str:
    """官方 API 路径的战线列表（主站不可用时的兜底，逻辑与旧版一致）"""
    try:
        try:
            ws = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status", timeout=20)
        except Exception as e:
            logger.warning(f"[HD2] 官方战线失败，回退社区源: {e}")
            obj = _fetch_json(LIVE_API_URL, timeout=20)
            ws = obj.get("warStatus") or {}
        camps = ws.get("campaigns") or []
        if not camps:
            return "⚔️ 当前无进行中的战役。"
        ps_list = ws.get("planetStatus") or []
        ps = {str(p.get("index")): p for p in ps_list}
        max_map = _load_maxhealth_map()
        lines = [f"⚔️ 当前战线（{len(camps)} 个）："]
        for c in camps:
            idx = str(c.get("planetIndex", ""))
            p = ps.get(idx, {})
            info = planet_map.get(idx, {})
            en_name = info.get("name", "")
            name = _planet_cn(en_name) or f"星球#{idx}"
            race_cn = _campaign_race_cn(c, p)
            ctype = "解放战" if c.get("type") == 0 else "防御战"
            # 解放百分比 = (总血量 - 现有血量) / 总血量；maxHealth 优先取缓存，其次 planet_map，HP 取 planetStatus 实时值
            if ctype == "解放战":
                maxh = (max_map.get(idx) or {}).get("maxHealth")
                hp = p.get("health")
                if maxh is None:
                    maxh = info.get("maxHealth")
                    if maxh is None:
                        hp = info.get("health")
                if isinstance(maxh, (int, float)) and isinstance(hp, (int, float)) and maxh > 0:
                    pct = (maxh - hp) / maxh * 100
                    val_s = f"｜解放 {pct:.2f}%"
                else:
                    hp2 = p.get("health")
                    val_s = f"｜HP {hp2:,}" if isinstance(hp2, (int, float)) else ""
            else:
                hp2 = p.get("health")
                val_s = f"｜HP {hp2:,}" if isinstance(hp2, (int, float)) else ""
            lines.append(f"• {name}｜{race_cn} {ctype}{val_s}")
        # 跃迁航道封锁（无法发动解放的星球）
        blines = _warp_blockade_lines(planet_map)
        if blines:
            lines.append("")
            lines.append("🚀 跃迁航道封锁（无法发动解放）：")
            lines.extend(blines)
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"[HD2] 战役查询失败: {e}")
        return f"⚠️ 战役查询失败：{e}"

# 新闻缓存文件（存 cache/ 目录）：按新闻 id 缓存固定文案
NEWS_CACHE_FILE = "news_cache.json"

# 新闻缓存记录结构：{id: {"text": ..., "source": ..., "ts": ..., "ver": ..., "h": ...}}
#   source: "site"（站点/私密仓译文，权威） | "llm"（本机 LLM 翻译） | "raw"（原文占位）
#   ver   : 站点 translated_at（有则用来判断新旧）
#   h     : text 的指纹，用于识别"同 id 但内容变了"
# 兼容旧的纯字符串格式（读取时自动归一化）。
NEWS_SRC_RANK = {"site": 2, "llm": 1, "raw": 0}


def _news_rec(val) -> dict:
    """把缓存条目归一化为记录 dict（兼容旧的纯字符串格式）"""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        return {"text": val, "source": "llm", "ts": 0, "ver": "", "h": ""}
    return {}


def _news_text(val) -> str:
    """取缓存条目的正文"""
    if isinstance(val, dict):
        return val.get("text") or ""
    return val if isinstance(val, str) else ""


def _news_fp(text: str) -> str:
    import hashlib

    return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:12]


def _site_item_ver(it: dict) -> str:
    """站点条目的版本号（translated_at），用于判断译文是否更新"""
    for k in ("translated_at", "published_at", "published"):
        v = it.get(k)
        if v:
            return str(v)
    return ""


def _news_rec_make(text: str, source: str, ver: str = "") -> dict:
    return {"text": text, "source": source, "ts": time.time(), "ver": ver, "h": _news_fp(text)}


def _news_should_update(old_val, new_text: str, new_source: str, new_ver: str = "") -> bool:
    """是否需要更新缓存条目。

    规则：
      - 无内容 -> 更新；同 id 内容指纹变了 -> 更新
      - 站点权威译文(source=site)：缓存不是 site（旧 LLM 译文）或来源版本更新 -> 更新
        （这就是"本地缓存挡住私密仓新译文"的修复点）
      - LLM 译文：只在没有 site 译文可放时才补位
    """
    if not new_text:
        return False
    old = _news_rec(old_val)
    old_text = old.get("text") or ""
    if not old_text:
        return True
    if _news_fp(old_text) == _news_fp(new_text):
        return False            # 内容一致，无需写盘
    if new_source == "site":
        if old.get("source") != "site":
            return True         # 旧的是 LLM/原文 -> 站点权威译文覆盖
        return bool(new_ver) and str(new_ver) != str(old.get("ver") or "")
    # 新来源是 LLM：只有当缓存里没有 site 权威译文时才补位
    return old.get("source") != "site"


class Hd2WarReportPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.planet_map = {}
        self._last_refresh = 0.0
        self._news_cache = {}  # 新闻 id -> 缓存记录 {text, source, ts, ver, h}
        self._load_news_cache()
    def _ensure_planet_map(self):
        if time.time() - self._last_refresh > 3600 or not self.planet_map:
            self.planet_map = _load_planet_map()
            self._last_refresh = time.time()

    def _news_cache_path(self) -> str:
        import os

        return os.path.join(CACHE_DIR, NEWS_CACHE_FILE)

    def _load_news_cache(self):
        try:
            import os

            path = self._news_cache_path()
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                # 归一化：旧格式是 {id: "文案"}，新格式是 {id: {text, source, ...}}
                self._news_cache = {str(k): _news_rec(v) for k, v in (raw or {}).items()}
        except Exception as e:
            logger.warning(f"[HD2] 加载新闻缓存失败: {e}")
            self._news_cache = {}

    def _save_news_cache(self):
        try:
            with open(self._news_cache_path(), "w", encoding="utf-8") as f:
                json.dump(self._news_cache, f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.warning(f"[HD2] 保存新闻缓存失败: {e}")

    async def _build_news_summary(self, items: list[dict]) -> str:
        """新闻正文：**站点/私密仓译文优先**（权威，且会覆盖旧的 LLM 译文），其余走 LLM

        缓存策略（按 id 存固定文案，避免同一新闻每次输出不同）：
          - 站点给出译文 → 若缓存不是站点译文，或站点版本(translated_at)更新 → 覆盖
          - 站点没给译文 → 缓存已有内容就沿用；没有才调 LLM（LLM 结果不会顶掉站点译文）
        """
        if not items:
            return ""
        dirty = False
        fresh_items = []
        for it in items:
            nid = str(it.get("id"))
            zh = (it.get("_zh") or "").strip()
            old = self._news_cache.get(nid)
            if zh:
                if _news_should_update(old, zh, "site", _site_item_ver(it)):
                    self._news_cache[nid] = _news_rec_make(zh, "site", _site_item_ver(it))
                    dirty = True
            elif not _news_text(old):
                fresh_items.append(it)
        if dirty:
            self._save_news_cache()
        if fresh_items:
            payload = []
            for it in fresh_items:
                payload.append(_clean_news_message(it.get("message", ""), 1500))
            text = "\n\n---\n\n".join(payload)
            try:
                conf = self.context.astrbot_config_mgr.default_conf
                provider_id = (
                    (conf.get("provider_settings") or {}).get("default_provider_id") or ""
                )
                if provider_id:
                    sys_prompt = (
                        "你是《绝地潜兵2》官方新闻中文编译。把下面每条英文新闻完整翻译成简体中文，"
                        "保留所有段落和细节，不要省略、不要总结、不要压缩。"
                        "游戏术语（TCS、Major Order、行星名等专有名词）按术语表翻译。"
                        "多条新闻之间用空行分隔。只输出译文，不要解释。\n\n"
                        + LLM_GLOSSARY
                    )
                    resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=text,
                        system_prompt=sys_prompt,
                    )
                    # LLM 可能返回多条合并，直接整体缓存到第一条，其余条目按原文截断
                    result = (resp.completion_text or "").strip()
                    if result:
                        # LLM 输出兜底：本地术语修正（防 LLM 不遵循术语表）
                        for old, new in TERM_FIXES:
                            result = result.replace(old, new)
                        first_id = str(fresh_items[0].get("id"))
                        # 双保险：绝不顶掉站点权威译文
                        if _news_rec(self._news_cache.get(first_id)).get("source") != "site":
                            self._news_cache[first_id] = _news_rec_make(result, "llm")
                            self._save_news_cache()
            except Exception as e:
                logger.warning(f"[HD2] LLM 新闻翻译失败: {e}")
                # LLM 失败时用原文占位（同样不顶掉站点译文）
                for it in fresh_items:
                    nid = str(it.get("id"))
                    if _news_rec(self._news_cache.get(nid)).get("source") != "site":
                        self._news_cache[nid] = _news_rec_make(
                            _clean_news_message(it.get("message", ""), 500), "raw")
                self._save_news_cache()

        # 固定输出：按 items 顺序取缓存文案（兼容旧的纯字符串缓存）
        lines = ["📰 最新资讯："]
        for it in items:
            cached = _news_text(self._news_cache.get(str(it.get("id"))))
            lines.append(cached or _clean_news_message(it.get("message", ""), 500))
        return "\n".join(lines)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event: AstrMessageEvent):
        """关键词触发"""
        text = (event.message_str or "").strip().lower()
        if not text:
            return
        # 过滤机器人自己发出的消息（同号多端避免自我触发）
        try:
            if str(event.get_sender_id()) == str(event.get_self_id()):
                return
        except Exception:
            pass
        # 仅响应 / 指令（无需 @）
        if not text.startswith("/"):
            return
        text = text[1:].strip()
        if any(kw in text for kw in TRIGGER_KEYWORDS):
            # 监控埋点
            _t0 = time.time()
            _log("matched", plugin="war_report", text=text, group=_gid(event), user=_uid(event), name=_uname(event))
            self._ensure_planet_map()
            if "dss" in text:
                report = await _get_dss_status(self.planet_map, self.context)
            elif "变化" in text or "归属" in text:
                # 支持自定义窗口：/归属变化48
                m_h = re.search(r"(?:变化|归属)\s*(\d{1,3})", text)
                hours = min(max(int(m_h.group(1)), 1), 168) if m_h else 24
                report = _get_ownership_changes(hours)
            elif "战线" in text:
                report = _get_campaigns(self.planet_map)
            elif "战役" in text:
                report = await self._get_campaign_brief()
            elif "分析" in text:
                report = await self._get_analysis()
            else:
                report = await self._build_report()
            yield event.plain_result(report)
            _log("done", plugin="war_report", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail=report[:60])
            event.stop_event()

    async def _get_campaign_brief(self) -> str:
        """战役简报：主站 active_campaign（官方中文，零 LLM）优先，官方 Episode API 兜底"""
        if _ds:
            try:
                ac = _ds.active_campaign(_site_doc())
                if ac and ac.get("available") and (ac.get("title") or ac.get("description_cn")):
                    return self._format_campaign_site(ac)
            except Exception as e:
                logger.warning(f"[HD2] 站点战役简报失败，回退官方 API: {e}")
        return await self._get_campaign_brief_official()

    def _format_campaign_site(self, ac: dict) -> str:
        """主站 active_campaign -> 战役简报文本（全部用站点现成中文）"""
        title = ac.get("title_cn") or ac.get("title") or "当前战役"
        status_cn = ac.get("status_cn") or ("进行中" if ac.get("status") == 0 else "")
        race_cn = ac.get("race_cn") or ""
        head = f"🎬 战役：{title}"
        if status_cn or race_cn:
            head += f"（{status_cn}" + (f" · {race_cn}" if race_cn else "") + "）"
        label = ac.get("label_cn") or ""
        lines = [head]
        if label and label != status_cn:
            pass  # label_cn 与 status_cn 语义重复，不重复输出
        desc = (ac.get("description_cn") or ac.get("description") or "").strip()
        if desc:
            lines.append(desc)
        # 当前阶段（phases[].status==0 进行中，取最后一个；结构与 forecast 同源）
        phase = None
        for ph in ac.get("phases") or []:
            if isinstance(ph, dict) and (ph.get("status") == 0 or not ph.get("status")):
                phase = ph
        if phase is None and ac.get("phases"):
            phase = ac["phases"][-1] if isinstance(ac["phases"][-1], dict) else None
        if phase:
            p_title = phase.get("title_cn") or phase.get("title") or ""
            p_brief = (phase.get("briefing_cn") or phase.get("briefing") or "").strip()
            p_status = phase.get("status_cn") or ""
            if p_title:
                lines.append("")
                lines.append(f"📌 当前阶段：{p_title}" + (f"（{p_status}）" if p_status else ""))
            if p_brief:
                lines.append(p_brief)
        reward = (phase or {}).get("reward") or {}
        amt = reward.get("amount")
        rname = reward.get("name_cn") or reward.get("name") or "奖章"
        if amt:
            lines.append("")
            lines.append(f"🏅 奖励：{amt} {rname}")
        ep_r, ep_t = ac.get("episodes_running"), ac.get("episodes_total")
        if ep_r is not None and ep_t:
            lines.append(f"📺 章节进度：{ep_r}/{ep_t}")
        # 跃迁航道封锁（被封锁的光能族星球）
        blines = _warp_blockade_lines(getattr(self, "planet_map", None) or {})
        if blines:
            lines.append("")
            lines.append("🚀 跃迁航道封锁：")
            lines.extend(blines)
        return "\n".join(lines)

    async def _get_campaign_brief_official(self) -> str:
        """官方 Episode API 路径的战役简报（主站不可用时的兜底，逻辑与旧版一致）"""
        try:
            try:
                obj = _fetch_official(f"/api/Episode/{OFFICIAL_WAR_ID}/", timeout=20)
                eps = obj.get("episodes") or []
            except Exception as e:
                logger.warning(f"[HD2] 官方战役简报失败，回退社区源: {e}")
                obj = _fetch_json(LIVE_API_URL, timeout=20)
                eps = obj.get("episodes") or []
            active = [e for e in eps if e.get("status") == 0]
            ep = active[-1] if active else (eps[-1] if eps else None)
            if not ep:
                return "🎬 当前无进行中的战役。"
            phase = None
            for ph in (ep.get("phases") or []):
                if ph.get("status") == 0 or not ph.get("status"):
                    phase = ph
            title_en = (ep.get("title") or "").strip()
            status_cn = "进行中" if ep.get("status") == 0 else "已完成"
            race_cn = {1: "终结族", 2: "机器人", 4: "光能者"}.get(ep.get("race"), "")
            desc_en = (ep.get("description") or "").strip()
            msg_en = ((phase or {}).get("introMessage") or desc_en).strip()
            lines = [f"🎬 战役：{title_en}（{status_cn}" + (f" · {race_cn}" if race_cn else "") + "）"]
            conf = self.context.astrbot_config_mgr.default_conf
            provider_id = (conf.get("provider_settings") or {}).get("default_provider_id") or ""
            translated = ""
            if provider_id and msg_en:
                sys_prompt = (
                    "你是《绝地潜兵2》超级地球真理部宣传官。将英文战役简报翻译成简体中文，"
                    "保留语气与细节，使用术语表译名，不要添加解释。\n\n" + LLM_GLOSSARY
                )
                to_t = f"【战役概述】\n{desc_en}\n\n【当前阶段简报】\n{msg_en}"
                resp = await self.context.llm_generate(
                    chat_provider_id=provider_id, prompt=to_t, system_prompt=sys_prompt,
                )
                translated = (resp.completion_text or "").strip()
                # 本地术语修正（如 继电器 -> 中继器）
                for _ot, _nt in TERM_FIXES:
                    translated = translated.replace(_ot, _nt)
            if translated:
                lines.append(translated)
            elif msg_en:
                lines.append(_translate_en_zh(msg_en))
            # 奖励
            rewards = ep.get("rewards") or []
            if rewards:
                amt = rewards[0].get("amount")
                if amt:
                    lines.append("")
                    lines.append(f"🏅 奖励：{amt} 奖章")
            # 跃迁航道封锁（被封锁的光能族星球）
            blines = _warp_blockade_lines(getattr(self, "planet_map", None) or {})
            if blines:
                lines.append("")
                lines.append("🚀 跃迁航道封锁：")
                lines.extend(blines)
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[HD2] 战役简报失败: {e}")
            return f"⚠️ 战役简报失败：{e}"

    async def _get_analysis(self) -> str:
        """LLM 战局分析：基于实时战役+重要指令生成行动建议"""
        try:
            try:
                ws = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status", timeout=20)
            except Exception as e:
                logger.warning(f"[HD2] 官方战局数据失败，回退社区源: {e}")
                obj = _fetch_json(LIVE_API_URL, timeout=20)
                ws = obj.get("warStatus") or {}
            camps = ws.get("campaigns") or []
            ps_list = ws.get("planetStatus") or []
            ps = {str(p.get("index")): p for p in ps_list}
            max_map = _load_maxhealth_map()
            camp_lines = []
            for c in camps[:20]:
                idx = str(c.get("planetIndex", ""))
                info = self.planet_map.get(idx, {})
                en_name = info.get("name", "")
                name = _planet_cn(en_name) or f"星球#{idx}"
                race_cn = _campaign_race_cn(c, ps.get(idx, {}))
                ctype = "解放" if c.get("type") == 0 else "防御"
                hp = (ps.get(idx) or {}).get("health")
                maxh = (max_map.get(idx) or {}).get("maxHealth")
                if ctype == "解放" and isinstance(maxh, (int, float)) and isinstance(hp, (int, float)) and maxh > 0:
                    pct = (maxh - hp) / maxh * 100
                    hp_s = f" 解放{pct:.2f}%"
                else:
                    hp_s = f" HP{hp:,}" if isinstance(hp, (int, float)) else ""
                camp_lines.append(f"- {name}：{race_cn}{ctype}{hp_s}")
            data_text = "\n".join([
                "【当前战役】",
                "\n".join(camp_lines) if camp_lines else "（无）",
                "",
                "【重要指令】",
                _fetch_brief_text() or "（无）",
            ])
            conf = self.context.astrbot_config_mgr.default_conf
            provider_id = (conf.get("provider_settings") or {}).get("default_provider_id") or ""
            if not provider_id:
                return "⚠️ 未配置 LLM provider，无法生成分析。"
            sys_prompt = (
                "你是《绝地潜兵2》超级地球真理部战略分析官。根据给定的实时战局数据，用简体中文输出战局分析：\n"
                "1. 当前威胁概述（敌方种族、战役数量、主要压力方向）\n"
                "2. 关键目标（重要指令要求什么，优先级如何）\n"
                "3. 行动建议（绝地潜兵应优先防守/进攻哪些星球，为什么）。评估条件：解放度越高 = 推荐优先级越高（优先集中兵力拿下即将解放完成的星球，避免功亏一篑；再评估其他目标）\n"
                "4. 风险提示\n"
                "要求：星球名用中文；语气符合超级地球宣传风格（民主、使命感）但内容专业务实；"
                "控制在 350 字以内，用分点列表输出。\n\n"
                + LLM_GLOSSARY
            )
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=data_text,
                system_prompt=sys_prompt,
            )
            result = (resp.completion_text or "").strip()
            if not result:
                return "⚠️ LLM 分析无返回。"
            return "📊 战局分析（真理部战略评估）\n\n" + result
        except Exception as e:
            logger.warning(f"[HD2] 战局分析失败: {e}")
            return f"⚠️ 战局分析失败：{e}"

    async def _warm_report_snapshot(self, reason: str = "定时") -> str:
        """后台预热：抓数 + 取站点中文，组装成快照存盘（不面向用户，失败不影响前台）"""
        if _REPORT_WARM["building"]:
            return ""
        _REPORT_WARM["building"] = True
        t0 = time.time()
        try:
            zh = _site_zh()
            seeded = _seed_site_news_cache(self._news_cache)
            if seeded:
                self._save_news_cache()
                logger.info(f"[HD2] 已播种 {seeded} 条站点译文到新闻缓存")
            # 预热时顺带把星球表准备好，用户第一条指令就不必等网络
            try:
                mp, psrc = await asyncio.to_thread(_load_planet_table, True)
                if mp:
                    self.planet_map = mp
                    self._last_refresh = time.time()
                    logger.info(f"[HD2] 星球表已预热（来源 {psrc}，{len(mp)} 个）")
            except Exception as e:
                logger.warning(f"[HD2] 星球表预热失败: {e}")
            news_items = _get_news_items(1)
            news_text = await self._build_news_summary(news_items)
            text, quality = _build_report_parts(self.planet_map, zh.get("brief") or None, news_text)
            if text and text.strip():
                # 间隙快照也写，但带 quality 标记、用更短有效期（避免每条指令都重新组装）
                _save_report_snapshot(text, source="warm", quality=quality)
                _REPORT_WARM.update({"ts": time.time(), "text": text, "source": "warm", "quality": quality})
                logger.info(f"[HD2] 战报快照已更新（{reason}，耗时 {time.time() - t0:.1f}s，"
                            f"MO 中文={'站点' if zh.get('brief') else '回退'}，quality={quality}）")
            else:
                logger.warning(f"[HD2] 战报预热未取到内容（{reason}），保留旧快照")
            return text
        except Exception as e:
            logger.warning(f"[HD2] 战报预热失败（{reason}）: {e}")
            return ""
        finally:
            _REPORT_WARM["building"] = False

    async def _warm_loop(self) -> None:
        """后台定时预热循环：启动后很快先预热一次，之后每 interval 秒一次"""
        try:
            await asyncio.sleep(3)   # 只等这一小会儿，尽快让快照就绪（不依赖网络就绪）
            while True:
                await self._warm_report_snapshot("启动预热" if _REPORT_WARM["ts"] == 0 else "定时")
                # 抖动，避免整点与其他任务撞在一起
                await asyncio.sleep(REPORT_WARM_INTERVAL + random.uniform(0, 20))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[HD2] 战报预热循环退出: {e}")

    async def initialize(self) -> None:
        """插件激活：把上次的快照读进内存，并启动后台预热任务"""
        global _warm_task
        text, ts, quality = _load_report_snapshot()
        if text:
            _REPORT_WARM.update({"ts": ts, "text": text, "source": "disk", "quality": quality})
            logger.info(f"[HD2] 已载入战报快照（{int(time.time() - ts)} 秒前生成，{len(text)} 字）")
        if REPORT_WARM_ENABLE:
            try:
                _warm_task = asyncio.create_task(self._warm_loop())
                logger.info(f"[HD2] 战报后台预热已启动（每 {REPORT_WARM_INTERVAL}s，"
                            f"快照有效期 {REPORT_SNAPSHOT_TTL}s）")
            except Exception as e:
                logger.warning(f"[HD2] 启动战报预热任务失败: {e}")
        else:
            logger.info("[HD2] 战报后台预热已关闭（data.report_warm_enable=0）")

    async def terminate(self) -> None:
        """插件卸载/重载：停掉后台任务"""
        global _warm_task
        if _warm_task is not None:
            try:
                _warm_task.cancel()
            except Exception:
                pass
            _warm_task = None
            logger.info("[HD2] 战报后台预热已停止")

    async def _build_report(self) -> str:
        """/战报：优先读后台预热好的快照（毫秒级返回），过快照有效期才现抓"""
        _t0 = time.time()
        self._ensure_planet_map()
        snap = _report_snapshot_text()
        if snap:
            age = int(time.time() - _REPORT_WARM["ts"]) if _REPORT_WARM["ts"] else -1
            logger.info(f"[HD2] /战报 命中快照（{age}s 前，来源 {_REPORT_WARM.get('source')}，"
                        f"本次取用 {time.time() - _t0:.3f}s）")
            return snap
        # 没有可用快照（刚启动/长时间离线）：走一次完整组装，并顺手补一个快照
        logger.info("[HD2] /战报 无快照，实时组装一次（本次可能较慢）")
        zh = _site_zh()
        brief_cn = zh.get("brief") or None
        if not brief_cn:
            brief_original = _fetch_brief_text()
            if brief_original:
                brief_cn = await _translate_with_llm(self.context, brief_original)
        news_items = _get_news_items(1)
        # 站点译文已进缓存时 _build_news_summary 不会调 LLM（只对真正缺译文的条目才翻）
        news = await self._build_news_summary(news_items)
        text, quality = _build_report_parts(self.planet_map, brief_cn, news)
        if text and text.strip():
            _save_report_snapshot(text, source="live", quality=quality)
            _REPORT_WARM.update({"ts": time.time(), "text": text, "source": "live", "quality": quality})
        logger.info(f"[HD2] /战报 实时组装完成（{time.time() - _t0:.1f}s，quality={quality}）")
        return text
