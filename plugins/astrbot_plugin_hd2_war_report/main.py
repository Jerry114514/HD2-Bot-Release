import json
import io
import re
import time
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
]

# 阵营中文名
FACTION_CN = {
    "Terminids": "终结族",
    "Automatons": "机器人",
    "Illuminate": "光能者",
    "Humans": "超级地球",
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
    name = PLANET_NAME_CN.get(info.get("name", ""), info.get("name", "未知星球"))
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

def _get_major_order(
    planet_map: dict,
    brief_translated: str | None = None,
) -> str | None:
    """抓取最新 Major Order（翻译简报 + 星球战况）

    Args:
        planet_map: 星球信息映射
        brief_translated: 可选，外部传入的简报翻译（LLM 翻译优先）；为空则用本地/MyMemory
    """
    try:
        try:
            mos = _fetch_official(f"/api/v2/Assignment/War/{OFFICIAL_WAR_ID}")
        except Exception as e:
            logger.warning(f"[HD2] 官方 MO 失败，回退社区源: {e}")
            obj = _fetch_json(LIVE_API_URL)
            mos = obj.get("majorOrders") or []
        if not mos:
            return None
        mo = mos[0]
        setting = mo.get("setting") or {}
        title = setting.get("overrideTitle") or "MAJOR ORDER"
        brief = setting.get("overrideBrief") or ""
        tasks = setting.get("tasks") or []
        rewards = setting.get("rewards") or []

        lines = ["🎖️ 重要指令"]
        # 翻译简报：优先外部传入（LLM），否则本地修正表/MyMemory
        if brief:
            translated = brief_translated or _translate_en_zh(brief)
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

# MO 任务类型 → 中文（type 语义：1=解放 2=采集样本 3=消灭敌人 9=完成行动）
MO_TASK_TYPE_CN = {
    1: "解放星球",
    2: "采集样本",
    3: "消灭敌人",
    9: "完成行动",
}


def _fmt_big_num(n: int) -> str:
    """大数格式化：1,000,000 → 100万；1,250,000,000 → 12.5亿"""
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 1_000_000_000:
        v = n / 1_000_000_000
        return (f"{v:.2f}".rstrip("0").rstrip(".")) + "亿"
    if n >= 1_000_000:
        v = n / 1_000_000
        return (f"{v:.1f}".rstrip("0").rstrip(".")) + "万"
    if n >= 10_000:
        v = n / 10_000
        return (f"{v:.1f}".rstrip("0").rstrip(".")) + "万"
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


def _get_news_items(limit: int = 1) -> list[dict]:
    """抓取最新新闻（星图网站 companion 优先，官方 NewsFeed 兜底——官方源疑似有问题待查验）"""
    try:
        # 星图网站（companion）优先
        obj = _fetch_json(LIVE_API_URL, timeout=20)
        news = obj.get("news") or []
        if not news:
            # 官方 NewsFeed 兜底
            try:
                news = _fetch_official(f"/api/NewsFeed/{OFFICIAL_WAR_ID}?maxEntries=50", timeout=20)
            except Exception as e2:
                logger.warning(f"[HD2] 官方新闻也失败: {e2}")
                return []
        if not news:
            return []
        # 按 id 降序取最新
        news_sorted = sorted(news, key=lambda n: n.get("id", 0), reverse=True)
        return news_sorted[:limit]
    except Exception as e:
        logger.warning(f"[HD2] 获取新闻失败: {e}")
        return []

def _get_warzone_distribution() -> str:
    """抓取战区玩家分布（超级地球/终结族/机器人/光能族）带仰齐浜时间"""
    import datetime

    try:
        obj = _fetch_json(EXTENDED_API_URL, timeout=20)
        data = obj.get("data") or []
        latest = data[-1] if data else {}
        total = latest.get("totalPlayerCount") or 0
        factions = [
            ("🌏 超级地球", latest.get("playerCountHumans") or 0),
            ("🕷 终结族", latest.get("playerCountTerminids") or 0),
            ("🤖 机器人", latest.get("playerCountAutomatons") or 0),
            ("👽 光能族", latest.get("playerCountIlluminate") or 0),
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


def _campaign_race_cn(c: dict, p: dict) -> str:
    """从 campaign 星球的 owner 判断敌方阵营（campaign.race 不可靠）"""
    owner = p.get("owner")
    if owner is None:
        return f"阵营?"
    return OWNER_ID_CN.get(owner, f"阵营{owner}")


async def _fetch_planet_page_text(slug: str) -> str:
    """用 Playwright 无头浏览器抓取星球页面渲染文本（采样最长+重试，供 DSS 行动识别用）"""
    from playwright.async_api import async_playwright

    url = f"https://helldiverscompanion.com/#hellpad/planets/{slug}"
    best = ""
    for attempt in range(3):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_function(
                        "() => document.body.innerText.length > 1500",
                        timeout=30000,
                    )
                except Exception:
                    pass
                samples = []
                for _ in range(5):
                    await page.wait_for_timeout(1000)
                    body_now = await page.evaluate("() => document.body.innerText")
                    samples.append(body_now)
                body = max(samples, key=len)
                if len(body) > len(best):
                    best = body
                if len(best) > 3000 and "OPERATIONAL PARAMETERS" in best.upper():
                    return best
            except Exception:
                pass
            finally:
                await browser.close()
    return best


def _find_dss_actions(body: str) -> list:
    """从星球页面文本识别实际生效的 DSS 战略行动：优先 OP 区，OP 区无命中时全文兜底匹配"""
    up = body.upper()
    op_start = up.find("OPERATIONAL PARAMETERS")
    poi_start = up.find("POINTS OF INTEREST", op_start if op_start >= 0 else 0)
    zone = up[op_start:(poi_start if poi_start > op_start else op_start + 4000)] if op_start >= 0 else up[:4000]
    hits = []
    for key in DSS_ACTION_CN:
        if key in zone or key.replace(" BLOCK", " BLOCKADE") in zone:
            hits.append(key)
    # OP 区未命中：全文兜底（排除明显的非行动区误匹配风险，行动名较独特）
    if not hits:
        for key in DSS_ACTION_CN:
            if key in up or key.replace(" BLOCK", " BLOCKADE") in up:
                hits.append(key)
    return hits


# DSS 页面抓取缓存（600 秒），避免每次查询都开 Playwright 抓停靠星球页面
_DSS_CACHE = {"ts": 0, "body": ""}


async def _fetch_page_quick(slug: str, timeout: int = 20) -> str:
    """DSS 用轻量单次页面抓取：不等全文渲染，够识别行动变量即可（替代多采样重试慢路径）
    注意：必须在事件循环内 await 调用（不能内部 asyncio.run，否则运行中事件循环报错）"""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(
                    f"https://helldiverscompanion.com/#hellpad/planets/{slug}",
                    wait_until="domcontentloaded", timeout=timeout * 1000,
                )
                try:
                    # 等待页面完整渲染：OP 区标签出现（或 8000 字符兜底），比固定字符数更可靠
                    await page.wait_for_function(
                        "() => document.body.innerText.toUpperCase().includes('OPERATIONAL PARAMETERS') || document.body.innerText.length > 8000",
                        timeout=20000,
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(800)
                return await page.evaluate("() => document.body.innerText")
            finally:
                await browser.close()
    except Exception as e:
        logger.warning(f"[HD2-DSS] 页面抓取失败({slug}): {type(e).__name__}: {e}")
        return ""


async def _get_dss_status(planet_map: dict, context: Context | None = None) -> str:
    """查询 DSS：停靠星球 + 生效效果 + 投票剩余
    主源：官方 API（spaceStations.activeEffectIds + EFFECT_ID_CN 映射）
    备选：社区 companion（tacticalActions[].status）
    context: 可选，提供时对 DSS 资讯缓存走 LLM 翻译写回（已译条目复用）"""
    try:
        try:
            _cache_dss_news()
            if context is not None:
                try:
                    await _translate_dss_news_cache(context)
                except Exception as e:
                    logger.warning(f"[HD2] DSS 资讯 LLM 翻译失败: {e}")
        except Exception:
            pass
        st = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status", timeout=20)
        ss_list = st.get("spaceStations") or []
        if not ss_list:
            return "\n".join(["🛰️ 当前无 DSS 数据（DSS 可能离线或移动中）。"] + _dss_news_supplement())
        ss = ss_list[0]
        idx = str(ss.get("planetIndex", ""))
        info = planet_map.get(idx, {})
        en_name = info.get("name", "")
        name = PLANET_NAME_CN.get(en_name, en_name) or f"星球#{idx}"
        sector = info.get("sector", "")
        # 星系显示：查本地星图表（对照表 星球英文名 -> 星区），官方 sector 为数字/Unknown 不可靠
        sector_cn = (_load_starmap_sectors().get(en_name.upper())) or SECTOR_CN.get(sector, sector)
        lines = ["🛰️ DSS 民主空间站"]
        lines.append(f"📍 当前停靠：{name}" + (f"（{sector_cn}）" if sector_cn else ""))

        # 生效效果（官方 activeEffectIds -> 中文映射，DSS 专用关联表）
        eff_ids = ss.get("activeEffectIds") or []
        eff_cn = _load_dss_effect_cn()
        if eff_ids:
            lines.append("⚡ 生效效果（官方数据）：")
            eff_desc = _load_dss_effect_desc()
            for eid in eff_ids:
                if eid in DSS_EXOSUIT_IDS:
                    continue  # 外骨骼机甲支援已含于行动支持，不单独显示
                cn = eff_cn.get(eid, f"效果{eid}")
                line = f"• {cn}"
                desc = eff_desc.get(eid, "")
                if desc:
                    line += f"\n  {desc}"
                lines.append(line)

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
        name = PLANET_NAME_CN.get(en_name, en_name) or f"星球#{idx}"
        sector = info.get("sector", "")
        # 星系显示：查本地星图表（对照表 星球英文名 -> 星区），官方 sector 为数字/Unknown 不可靠
        sector_cn = (_load_starmap_sectors().get(en_name.upper())) or SECTOR_CN.get(sector, sector)
        lines = ["🛰️ DSS 民主空间站"]
        lines.append(f"📍 当前停靠：{name}" + (f"（{sector_cn}）" if sector_cn else ""))

        if "OPERATIONAL SUPPORT" in DSS_ACTION_CN:
            cn0, desc0 = DSS_ACTION_CN["OPERATIONAL SUPPORT"]
            lines.append("⚡ 实际生效的行动：")
            lines.append(f"• {cn0}（OPERATIONAL SUPPORT）")
            lines.append(f"  {desc0}")

        STATUS_CN = {0: "离线", 1: "投票中", 2: "已激活", 3: "恢复中"}
        tac = ss.get("tacticalActions") or []
        if tac:
            lines.append("🗳️ 投票/行动状态：")
            for t in tac:
                name_en = (t.get("name") or "").upper()
                name_key = name_en.replace(" BLOCKADE", " BLOCK")
                if name_key in DSS_ACTION_CN:
                    cn_t, desc_t = DSS_ACTION_CN[name_key]
                else:
                    cn_t, desc_t = name_en, ""
                st_v = STATUS_CN.get(t.get("status"), f"状态{t.get('status')}")
                icon = {2: "🟢", 3: "❄"}.get(t.get("status"), "⏳")
                line = f"{icon} {st_v}：{cn_t}（{name_en}）"
                if t.get("status") == 2 and desc_t:
                    line += f"\n  {desc_t}"
                lines.append(line)

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
        names.append(PLANET_NAME_CN.get(en, en) or f"星球#{w}")
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
            pname = PLANET_NAME_CN.get(en, en) or f"星球#{idx}"
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

# DSS 资讯 LLM 翻译提示词（新闻/公告类，非 MO 简报）
_DSS_NEWS_LLM_PROMPT = (
    "你是专业的游戏本地化翻译。把用户给出的英文《绝地潜兵2》游戏新闻/公告翻译成简体中文。"
    "要求：1) 准确传达内容；2) 专有名词（星球名、DSS、阵营等）按术语表翻译；3) 只输出译文，不要任何解释。\n\n"
    + LLM_GLOSSARY
)


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


async def _translate_dss_news_cache(context: Context) -> None:
    """对 DSS 资讯缓存逐条走 LLM 翻译，写回缓存文件（中英双语；翻译失败保留原文）"""
    try:
        items = _load_dss_news_cache()
        if not items:
            return
        translated = []
        for it in items:
            msg = it.get("message", "")
            if it.get("cn"):
                translated.append(it)  # 已有译文，复用
                continue
            cn = await _translate_with_llm(context, msg, system_prompt=_DSS_NEWS_LLM_PROMPT)
            if cn:
                it["cn"] = cn
            translated.append(it)
        data = {"ts": int(time.time()), "items": translated}
        with open(DSS_NEWS_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[HD2] DSS 资讯 LLM 翻译缓存失败: {e}")


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
    """DSS 离线/不可用时：从本地缓存读取 DSS 相关资讯作补充说明（优先中文译文）"""
    items = _load_dss_news_cache()
    if not items:
        return []
    lines = ["", "📰 DSS 相关资讯（缓存）："]
    for it in items[:1]:  # 与战报 MO 缓存同理：只显示最近一条
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


def _get_campaigns(planet_map: dict) -> str:
    """当前全部入侵/防御战役列表"""
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
            name = PLANET_NAME_CN.get(en_name, en_name) or f"星球#{idx}"
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

NEWS_CACHE_FILE = "news_cache.json"  # 存 cache/ 目录，新闻 id 不变则输出固定

class Hd2WarReportPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.planet_map = {}
        self._last_refresh = 0.0
        self._news_cache = {}  # gid -> 固定文案
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
                    self._news_cache = json.load(f)
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
        """LLM 翻译新闻全文（按新闻 id 缓存，命中直接复用固定输出）"""
        if not items:
            return ""
        # 收集未缓存的条目
        fresh_items = [it for it in items if str(it.get("id")) not in self._news_cache]
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
                        self._news_cache[str(fresh_items[0].get("id"))] = result
                        self._save_news_cache()
            except Exception as e:
                logger.warning(f"[HD2] LLM 新闻翻译失败: {e}")
                # LLM 失败时用原文占位
                for it in fresh_items:
                    nid = str(it.get("id"))
                    if nid not in self._news_cache:
                        self._news_cache[nid] = _clean_news_message(it.get("message", ""), 500)
                self._save_news_cache()

        # 固定输出：按 items 顺序取缓存文案
        lines = ["📰 最新资讯："]
        for it in items:
            nid = str(it.get("id"))
            cached = self._news_cache.get(nid)
            if cached:
                lines.append(cached)
            else:
                lines.append(_clean_news_message(it.get("message", ""), 500))
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
        """战役简报：当前 Campaign（episodes 最新进行中）+ LLM 翻译 + 术语表"""
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
            race_cn = {1: "终结族", 2: "机器人", 4: "光能族"}.get(ep.get("race"), "")
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
                name = PLANET_NAME_CN.get(en_name, en_name) or f"星球#{idx}"
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

    async def _build_report(self) -> str:
        self._ensure_planet_map()
        # 先抓简报原文用于 LLM 翻译
        brief_original = _fetch_brief_text()
        brief_translated = None
        if brief_original:
            brief_translated = await _translate_with_llm(self.context, brief_original)
        # MO 缓存：最近 5 条新闻没有 NEW MAJOR ORDER 时直接复用缓存（MO 未更新，避免重复翻译/防 API 波动）
        mo = None
        if not _has_recent_new_major_order():
            mo = _load_mo_cache()
            if mo:
                logger.info("[HD2] 最近5条新闻无 NMO，使用 MO 缓存")
        if mo is None:
            mo = _get_major_order(self.planet_map, brief_translated=brief_translated)
            if mo:
                _save_mo_cache(mo)  # 每次实时抓取成功后录入缓存
        news_items = _get_news_items(1)
        news = await self._build_news_summary(news_items)
        warzone = _get_warzone_distribution()
        parts = []
        if mo:
            parts.append(mo)
        else:
            parts.append("⚠️ 暂时无法获取 Major Order 数据。")
        if warzone:
            parts.append(warzone)
        if news:
            parts.append(news)
        return "\n\n\n".join(parts)
