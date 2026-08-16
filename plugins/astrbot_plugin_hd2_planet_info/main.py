import io
import re
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
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
    fetch_official as _fetch_official,
    load_effect_id_cn as _load_effect_id_cn,
    PLANET_NAME_CN,
    OFFICIAL_API_URL,
    OFFICIAL_WAR_ID,
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

# ============ 星球信息功能 ============
PLANET_INFO_MARKER = True

# 抵抗度等级翻译
RESIST_LEVEL_CN = {
    "UNBREAKABLE": "牢不可破",
    "CRITICAL": "严重",
    "DIRE": "危急",
    "HIGH": "高",
    "AVERAGE": "中等",
    "LOW": "低",
    "NONE": "无",
    "BROKEN": "崩溃",
}

# 部队（阵营）映射
ENEMY_RACE_CN = {
    "AUTOMATON": "机器人",
    "TERMINID": "终结族",
    "ILLUMINATE": "光能族",
    "HUMAN": "超级地球",
}

# 环境（ENVIRONMENTAL CONDITION）中英翻译
ENV_COND_CN = {
    "RAINSTORMS": ("暴雨", "暴雨将降低能见度。"),
    "EXTREME COLD": ("夜间极寒", "夜间气温大幅降低，同时使需要散热的武器冷却加快。"),
    "INTENSE HEAT": ("昼间极暑", "昼间气温大幅升高，加快体力消耗速度，同时使需要散热的武器冷却变慢。"),
    "FIRE TORNADOES": ("火龙卷", "火龙卷将随机出现在地图上。"),
    "ION STORMS": ("离子风暴", "风暴期间，战略配备不可用（除超级地球大炮）"),
    "BLIZZARDS": ("暴风雪", "暴风雪将降低能见度，并使需要散热的武器冷却加快。"),
    "（其他未捕获环境）": ("—", "插件内置表已含：沙暴/地震/流星雨/火山活动/酸雨/浓雾/强风/暴雪/冰雹/昏暗天空/血雨/昼间/夜间变体"),
}

# POI 特殊文案（按用户提供）
POI_SPECIAL_CN = {
    "INTERSTELLAR VOID": ("已堕入寂域", "由于其上的异界尖塔过多，该星球周边数百光年的星域已被异常重力撕裂出现实场域，落入了光能族的魔爪。"),
    "HIVE WORLD": ("虫窝世界", "这颗星球由于“阴霾”的侵蚀和终结族的肆意扩张，已经千疮百孔，在该星球的部分任务区域无法呼叫轨道支援。"),
    "AUTOMATON HOMEWORLD": ("机器人母星", "生化人的丑陋作品，由仇恨驱动的造物。他们甘愿俯首当为生化人的爪牙。我们距离肃清它们仅有一步之遥。"),
    "CYBORG HOMEWORLD": ("生化人母星", "第一次银河战争的遗毒，他们回来了！并且霸占了这颗曾被民主光辉照耀的星球。我们距离肃清他们仅有一步之遥。"),
    "HUMAN HOMEWORLD": ("人类的故乡", "“民主在等待你，公民。成为英雄，成为传奇，成为...绝地潜兵！”"),
    "TERMINID CONTROL SYSTEM +": ("终结族控制系统 +", "TCS + 是一项旨在针对“阴霾”威胁的天体包围圈工程。在建成该设施的星球上，其将会不断地向大气中喷洒对终结族极度有害的熏香味气体。该工程将会部署到复数个星球以完成对“阴霾”的包围。"),
    "DEMOCRACY SPACE STATION": ("DSS 民主空间站", "DSS空间站是由数十亿超级货币打造的超巨轨道武器构造体。其以4个小时为投票周期，投票结束后DSS将前往得票最高的星球，为该地的绝地潜兵与SEAF部队提供助力。"),
    "PANDORA BASE": ("潘多拉基地", "这里是搭建DSS及其强化设施的绝密地点。你应该不会想泄密的，对吧？"),
    "CENTER FOR THE CONTAINMENT OF DISSIDENCE": ("异端收容中心", "别问，问就是异端！上面那个泄密的刚进去。"),
}

# ============ 行动变量词库（与 HD2行动变量对照表.md 一致） ============
# 参数标题 -> 中文标题
VARIANT_GROUP_CN = {
    "TERMINID BROOD": "终结族变种",
    "EXOTIC LIFEFORM": "终结族特殊个体",
    "AUTOMATON ASSEMBLAGE": "机器人军团",
    "SPECIAL UNIT": "机器人特殊单位",
    "AUXILIARY ASSEMBLAGE": "机器人辅助军团",
    "FRIENDLY HELLFIRE": "友军火力",
    "ILLUMINATE SECT": "光能族宗派",
    "COSMIC HORROR": "深域惧像",
}

# 分组图标
VARIANT_GROUP_ICON = {
    "TERMINID BROOD": "🧬",
    "EXOTIC LIFEFORM": "🦎",
    "AUTOMATON ASSEMBLAGE": "🤖",
    "SPECIAL UNIT": "🎖️",
    "AUXILIARY ASSEMBLAGE": "🪖",
    "FRIENDLY HELLFIRE": "🔥",
    "ILLUMINATE SECT": "👽",
    "COSMIC HORROR": "🌌",
}

# 参数 -> 所属分组（分组名未显示在页面上时按此归组兜底）
VARIANT_GROUP_OF = {
    "SPORE BURST STRAIN": "TERMINID BROOD",
    "PREDATOR STRAIN": "TERMINID BROOD",
    "RUPTURE STRAIN": "TERMINID BROOD",
    "DRAGONROACH": "EXOTIC LIFEFORM",
    "HIVE LORD": "EXOTIC LIFEFORM",
    "THE INCINERATION CORPS": "AUTOMATON ASSEMBLAGE",
    "CYBORGS": "AUTOMATON ASSEMBLAGE",
    "THE JET BRIGADE": "SPECIAL UNIT",
    "DEVASTATOR SURGE": "AUXILIARY ASSEMBLAGE",
    "HEAVY ARMOR SURGE": "AUXILIARY ASSEMBLAGE",
    "HULK SURGE": "AUXILIARY ASSEMBLAGE",
    "EAGLE AIR RAIDS": "FRIENDLY HELLFIRE",
    "ORDNANCE AIR RAIDS": "FRIENDLY HELLFIRE",
    "MINDLESS MASSES": "ILLUMINATE SECT",
    "APPROPRIATORS": "ILLUMINATE SECT",
    "SPEARHEAD": "ILLUMINATE SECT",
    "THE GREAT HOST": "COSMIC HORROR",
    "EXOSTORM": "COSMIC HORROR",
    "POLYVOID": "COSMIC HORROR",
}

# 参数名 -> (译文, 说明)（与对照表完全一致）
VARIANT_CN = {
    "SPORE BURST STRAIN": ("孢裂变种", "这颗星球上的虫子发生了变异，会释放特殊的信息素，使得其他同变种个体分泌大量肾上腺素。做好准备，以非暴力手段应对这些为彼此提速的敌人。"),
    "PREDATOR STRAIN": ("掠食变种", "根据报道，这颗星球上的终结族体现出极强的攻击性，且学会了高速移动的能力与表层伪装色，做好准备，以迅雷之势清扫这些变种。"),
    "RUPTURE STRAIN": ("爆裂变种", "情报显示，这颗星球上的终结族体现出极强的腐蚀性，且学会了掘地等手段免疫常规攻击，做好准备，应对这些精通于地下突袭的变种。"),
    "DRAGONROACH": ("蟑龙", "该星球上的吐酸泰坦选择进化出了飞行能力，不过原始本能使得它们不会成群出现，做好夺取制空权的准备。"),
    "HIVE LORD": ("霸王虫", "该星球上的终结族演变出了复数个皮糙肉厚的庞然大物，做好面对虫群与广域动量攻击的准备。"),
    "THE INCINERATION CORPS": ("炽灼部队", "在此处的机器人配备了大量火焰装备。来吧，去给他们浇点油。"),
    "CYBORGS": ("生化人", "做好准备，面对经过精英化训练，涵盖全射程打击的生化人部队与邪恶机器组成的部队。"),
    "THE JET BRIGADE": ("喷气旅", "在此处的机器人选择抛弃防御力，转而使用低空协同突进战术。做好准备，把他们炸上天。"),
    "DEVASTATOR SURGE": ("歼灭者狂潮", "机器人在该星球部署了大量歼灭者。"),
    "HEAVY ARMOR SURGE": ("巨型者狂潮", "做好反坦克准备，面对重甲机器人大军。"),
    "HULK SURGE": ("蹂躏者狂潮", "地图扫描显示，该地部署了大量的蹂躏者。"),
    "EAGLE AIR RAIDS": ("“飞鹰风暴”空袭", "任务期间将周期性部署“飞鹰”机枪扫射"),
    "ORDNANCE AIR RAIDS": ("轨道轰炸支援", "轨道火力网轰炸在任务期间周期性部署。"),
    "MINDLESS MASSES": ("无脑群氓", "可恶的光能族，不仅剥夺了这颗星球上的公民投票权，还要把他们变成丧尸来攻击我们。做好准备，应对堪比终结族的丧尸大军——不要有心理负担。"),
    "APPROPRIATORS": ("占领者", "这颗星球上的光能族只有他们的同类，这或许表现出光能族的极端排外，也可能表明他们的用心极其险恶。做好准备，应对可能出现的异形构造体。"),
    "SPEARHEAD": ("教团先锋", "【未知的势力，仅存在于游戏文件与参数中。】"),
    "THE GREAT HOST": ("大军", "在默里迪亚虫洞帷幕后秘密建造的光能族入侵舰队，正全速驶向超级地球。"),
    "EXOSTORM LEVEL 1": ("异界风暴 1 级", "由异界尖塔引发的非自然气候现象。概率引发局部地区的沙尘暴。"),
    "EXOSTORM LEVEL 2": ("异界风暴 2 级", "这是第二级的异界风暴。会生成出扭曲现实场的闪电。"),
    "EXOSTORM LEVEL 3": ("异界风暴 3 级", "最高级别的异界风暴。会生成出扭曲现实场的闪电的同时，会不断刮起龙卷风，撕裂被吸入其中的一切。星球表面的颜色，也昭示出不祥之兆。"),
    "POLYVOID": ("寂域环境", "由于过多的异界尖塔导致的重力场域异常现象，已使整颗星球堕入寂域。在任何探测器都找不到的地方...不敢想象发生了什么..."),
}
# ============ 行动变量词库 END ============

# ============ DSS 战略行动（TACTICAL ACTION）词库 ============
TACTICAL_ACTION_CN = {
    "EAGLE STORM": ("✈ 飞鹰风暴", "DSS的工作人员在大型三层结构附近集结了一支“飞鹰”编队，采取24小时轮班制，为星球上的绝地潜兵提供火力压制支援。在此期间，“飞鹰风暴” 将作为行动变量周期性在任务中部署。若该星球遭到入侵，DSS将会守住防线，阻止敌方入侵进度；在地面执行任务的绝地潜兵小队队长可通过 “标记目标” 功能为 “飞鹰” 标记敌人。"),
    "ORBITAL BLOCK": ("🚫 轨道封锁", "DSS启用零重力反舰导弹系统，将会瞄准该星球上所有试图离开的敌方目标。同时将多余战力投入到超级驱逐舰后勤支援。在此期间，在该星球执行任务的小队将自动激活 “强化资源：绝地喷射仓空间优化” ；且该星球上的敌军无法对其他星球发动入侵战役。"),
    "HEAVY ORDNANCE DISTRIBUTION": ("💣 重型军械分发", "DSS后勤舰队将为环绕星球的超级驱逐舰提供富余的380MM高爆弹，SEAF部队的行动也将获得装备支援，加速解放进程。在此期间，在该星球执行任务的小队将获得额外的战略配备：轨道380 MM高爆弹火力网；地面上的SEAF部队将获得更高级的武器支援；且DSS将会额外推进该星球的解放进程。"),
    "EAGLE BLOCK": ("🚫✈🚫 飞鹰封锁", "在该效果激活期间，将同时激活DSS战术行动：“飞鹰风暴”与“轨道封锁”。"),
    "PLANETARY BOMBARDMENT": ("💣🌏💣 星球轰炸", "DSS上搭载的76门“总统级”舰炮同时开火，轰炸整个星球。在此期间，所有小队 +1 可用增援； 且DSS将会额外推进该星球的解放进程。注意了：炮弹可不会区分正义与邪恶！"),
    "OPERATIONAL SUPPORT": ("✊ 行动支持", "DSS停靠在这颗星球附近，一定程度上有助于该星球的解放战争进程。在该星球执行任务的小队所携带的所有“外骨骼机甲”战略配备冷却时间减少35%。"),
}
# ============ DSS 战略行动词库 END ============

# ============ 战略目标（OBJECTIVES）词库 ============
OBJECTIVE_CN = {
    "CIRCUMVALLATION": ("天体包围圈", "解放该星球将完成对目标星球上的敌军的战略围攻。拿下这颗星球后，将会形成对包围圈内星球的“围攻解放”攻势，此行为将会降低围攻星球的敌人抵抗度。"),
    "SIEGE LIBERATION": ("围攻解放", "目标星球上的敌军已被包围。无法继续阻挡我们的解放大业。"),
    "SIEGE VALLATION": ("围攻战线", "这是对目标星球上的敌军形成围攻的星球之一。包括其在内的几颗星球已经形成围攻。持续掌控这颗星球会削弱目标星球上的敌方抵抗度。"),
    "TOTAL SECTOR LIBERATION": ("星区解放", "解放该星球后，超级地球将实现对所在星区的全面掌控。"),
}

# 重要指令目标状态
MO_TARGET_CN = {
    "未完成": "该星球是本次重要指令的目标之一，绝地潜兵，解放它。",
    "已完成": "该星球作为本次重要指令的目标之一，已在我们的掌控之中。",
}
# ============ 战略目标词库 END ============

async def _translate_objective_desc(context, desc: str) -> str:
    """用 DeepSeek 翻译战略目标英文描述为中文（失败返回原文）"""
    if not desc or not desc.strip():
        return desc
    # 已含中文则跳过
    if re.search(r"[\u4e00-\u9fff]", desc):
        return desc
    try:
        import json as _json
        import urllib.request as _urllib

        conf = _json.load(
            open(_os.environ.get("HD2_CMD_CONFIG", r"C:\Users\<USERNAME>\.astrbot\data\cmd_config.json"), encoding="utf-8-sig")
        )
        providers = conf.get("provider_sources") or []
        key, base = "", "https://api.deepseek.com/v1/chat/completions"
        for p in providers:
            if isinstance(p, dict) and "deepseek" in str(p.get("id", "")).lower():
                keys = p.get("key") or []
                key = keys[0] if isinstance(keys, list) and keys else str(keys or "")
                if p.get("api_base"):
                    base = p["api_base"].rstrip("/") + "/chat/completions"
                break
        if not key:
            return desc
        sys_prompt = (
            "你是《绝地潜兵2》中文本地化翻译。把用户给出的英文战略目标描述翻译成简体中文，"
            "星球名等专有名词保留英文或按常识译名，只输出译文不要解释。"
        )
        req = _urllib.Request(
            base,
            data=_json.dumps({
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": desc},
                ],
                "temperature": 0.3,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with _urllib.urlopen(req, timeout=120) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        result = (data["choices"][0]["message"]["content"] or "").strip()
        return result or desc
    except Exception:
        return desc


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

# 星图对照表路径：优先配置/环境变量，回退旧路径
STARMAP_CANDIDATES = [
    _os.path.join(_tables_dir(), "星图对照表_修正版.md"),
    _os.path.join(_tables_dir(), "星图对照表.md"),
]
STARMAP_FILE = next((p for p in STARMAP_CANDIDATES if _os.path.exists(p)), STARMAP_CANDIDATES[0])

# 从对照表加载的映射缓存（避免重复读文件）
_starmap_cache = {"planets": None, "sectors": None}


def _load_starmap_table() -> dict:
    """从星图对照表_修正版.md 动态加载 星球名映射（英文->中文）和 星区映射（英文->中文）

    文件格式：| 星区 | 英文 | 中文 |
    """
    global _starmap_cache
    if _starmap_cache["planets"] is not None:
        return _starmap_cache
    planets = {}
    sectors = {}
    try:
        with io.open(STARMAP_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("|"):
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) < 3:
                    continue
                sector_en, planet_en, planet_cn = cells[0], cells[1], cells[2]
                if planet_en in ("英文", "---", "") or planet_en == "英文":
                    continue
                if not planet_en:
                    continue
                planets[planet_en.upper()] = planet_cn
                if sector_en and sector_en != "星区" and sector_en != "---":
                    sectors[sector_en] = sector_en  # 星区中文暂未填则用英文
    except Exception as e:
        logger.warning(f"[HD2] 加载星图对照表失败: {e}")
    _starmap_cache["planets"] = planets
    _starmap_cache["sectors"] = sectors
    return _starmap_cache


def _get_planet_name_map() -> dict:
    """合并映射：对照表优先，硬编码表补充"""
    merged = dict(PLANET_NAME_CN)
    try:
        table = _load_starmap_table()
        for en, cn in table["planets"].items():
            if cn:  # 只覆盖有中文名的
                merged[en] = cn
    except Exception:
        pass
    return merged


def _planet_slug_from_query(text: str) -> str | None:
    """从查询文本解析星球英文 slug：先匹配对照表中文名->英文，再直接匹配英文名"""
    t = text.strip().lower()
    # 去掉常见后缀
    for suf in ["信息", "情报", "资料", "详情", "info", "查"]:
        if t.endswith(suf):
            t = t[: -len(suf)].strip()
    if not t:
        return None
    name_map = _get_planet_name_map()
    # 硬编码补充星球（强制覆盖，不受对照表影响）
    EXTRA_PLANET_CN = {
        "艾福亚湾": "AFOYAY BAY",
    }
    for cn, en in EXTRA_PLANET_CN.items():
        name_map[cn] = en
        name_map[en] = cn

    # slug 规则：空格/连字符都转下划线（如 PHERKAD_SECUNDUS -> pherkad_secundus、RD-4 -> rd_4）
    def _to_slug(en: str) -> str:
        return en.lower().replace(" ", "_").replace("-", "_")

    # 1) 中文名 -> 英文（对照表 + 硬编码表）
    rev = {}
    for en, cn in name_map.items():
        if cn:
            rev[cn.lower()] = en
    if t in rev:
        return _to_slug(rev[t])
    # 2) 直接英文名（支持空格/下划线/连字符变体）
    t_norm = t.replace(" ", "_").replace("-", "_")
    for en, cn in name_map.items():
        if _to_slug(en) == t_norm:
            return _to_slug(en)
        if en.lower() == t:
            return _to_slug(en)
    # 3) 任意纯英文名直接转 slug（不在对照表也支持）
    if re.fullmatch(r"[a-z][a-z0-9_\- ]*", t):
        return t_norm
    return None


# slug -> index 缓存（hd2dev 星球名，3600s）
_INDEX_CACHE = {"ts": 0, "map": {}}


def _slug_to_index(slug: str):
    """星球 slug -> 官方 index（helldivers2.dev 名字，带缓存）"""
    import time as _time
    now = _time.time()
    if not _INDEX_CACHE["map"] or now - _INDEX_CACHE["ts"] > 3600:
        try:
            planets = _fetch_json(
                "https://api.helldivers2.dev/api/v1/planets",
                {"X-Super-Client": "astrbot-hd2-plugin", "X-Super-Contact": "https://github.com/astrbot"},
            )
            _INDEX_CACHE["map"] = {p["name"].lower().replace(" ", "_").replace("-", "_"): p["index"] for p in planets}
            _INDEX_CACHE["ts"] = now
        except Exception:
            pass
    return _INDEX_CACHE["map"].get(slug)


def _fetch_planet_official(idx: int) -> dict:
    """官方源星球详情：抵抗度/防御战/行动变量/占领方 -> info dict"""
    st = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status")
    wi = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/WarInfo")
    ps = {p.get("index"): p for p in st.get("planetStatus") or []}
    pi = {p.get("index"): p for p in wi.get("planetInfos") or []}
    p = ps.get(idx, {})
    info = {}

    # 占领方/解放状态
    owner = p.get("owner")
    if owner == 1:
        info["secured"] = True

    # 跃迁航道封锁（Warp Link Blockade）：光能族占领 + 无战役 + 连接点有我方未被防御
    try:
        wi2 = _fetch_official(f"/api/WarSeason/{OFFICIAL_WAR_ID}/WarInfo")
        routes = {q.get("index"): (q.get("waypoints") or []) for q in wi2.get("planetInfos") or []}
        camp_idx = set(c.get("planetIndex") for c in st.get("campaigns") or [])
        defend_idx = set(ev.get("planetIndex") for ev in st.get("planetEvents") or [])
        if owner == 4 and idx not in camp_idx:
            # 双向连接：出站 + 入站
            conn = set(routes.get(idx, []))
            for w, wps in routes.items():
                if idx in wps:
                    conn.add(w)
            links = [w for w in conn if ps.get(w, {}).get("owner") == 1 and w not in defend_idx]
            if links:
                info["warp_blockade"] = links
    except Exception:
        pass

    # 抵抗度（官方 regenPerSecond / maxHealth）
    mh = pi.get(idx, {}).get("maxHealth")
    regen = p.get("regenPerSecond")
    if mh and regen is not None and mh > 0 and regen > 0:
        rpct = regen / mh * 100
        info["resistance_pct"] = f"{rpct:.2f}%"
        lvl = "NONE"
        if rpct >= 5:
            lvl = "UNBREAKABLE"
        elif rpct >= 3:
            lvl = "CRITICAL"
        elif rpct >= 2:
            lvl = "DIRE"
        elif rpct >= 1:
            lvl = "HIGH"
        elif rpct >= 0.5:
            lvl = "AVERAGE"
        elif rpct > 0:
            lvl = "LOW"
        info["resistance_level"] = lvl
    else:
        info["resistance_level"] = "未知"

    # 防御战（planetEvents）
    for ev in st.get("planetEvents") or []:
        if ev.get("planetIndex") == idx:
            h, m = ev.get("health"), ev.get("maxHealth")
            if h and m:
                info["defense_hp"] = (f"{h:,}", f"{m:,}")
            race_cn = {2: "终结族", 3: "机器人", 4: "光能族"}.get(ev.get("race"), "")
            if race_cn:
                info["defense_desc"] = f"{race_cn}入侵战役" + (f"（失守 {(m-h)/m*100:.1f}%）" if h and m else "")

    # 行动变量（planetActiveEffects -> 中文词条）
    eff_cn = _load_effect_id_cn()
    effs = []
    for pe in st.get("planetActiveEffects") or []:
        if pe.get("index") == idx:
            cn = eff_cn.get(pe.get("galacticEffectId"))
            if cn and cn not in effs:
                effs.append(cn)
    if effs:
        info["official_effects"] = effs

    # MO 目标（官方 Assignment）
    try:
        ass = _fetch_official(f"/api/v2/Assignment/War/{OFFICIAL_WAR_ID}")
        if ass:
            for t in (ass[0].get("setting") or {}).get("tasks") or []:
                vtypes = t.get("valueTypes") or []
                vals = t.get("values") or []
                for i, vt in enumerate(vtypes):
                    if vt == 12 and i < len(vals) and vals[i] == idx:
                        info["mo_target"] = True
    except Exception:
        pass

    if owner:
        info["owner"] = owner
    return info


async def _fetch_planet_page_text(slug: str) -> str:
    """用 Playwright 无头浏览器抓取星球页面渲染文本（带完整数据等待 + 重试）"""
    from playwright.async_api import async_playwright

    url = f"https://helldiverscompanion.com/#hellpad/planets/{slug}"
    best = ""
    for attempt in range(3):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                # 等待页面渲染（最多 30 秒），条件宽松：只要内容超过 1500 字符
                try:
                    await page.wait_for_function(
                        "() => document.body.innerText.length > 1500",
                        timeout=30000,
                    )
                except Exception:
                    pass
                # 多采样几次，取最长结果（数据完整度优先）
                samples = []
                for _ in range(5):
                    await page.wait_for_timeout(1000)
                    body_now = await page.evaluate("() => document.body.innerText")
                    samples.append(body_now)
                body = max(samples, key=len)
                if len(body) > len(best):
                    best = body
                # 已拿到完整数据则返回
                if len(best) > 3000 and ("OPERATIONAL PARAMETERS" in best.upper() or "POINTS OF INTEREST" in best.upper()):
                    return best
            except Exception:
                pass
            finally:
                await browser.close()
    return best


def _parse_planet_info(body: str) -> dict:
    """从页面文本解析星球信息"""
    info = {}


    def grab(label):
        i = body.find(label)
        return body[i + len(label):] if i >= 0 else ""

    # ENEMY RESISTANCE：等级 + 百分比
    seg = grab("ENEMY RESISTANCE")
    m = re.search(r"([A-Z]+)\n(\d+\.\d+%)", seg)
    if m:
        info["resistance_level"] = m.group(1)
        info["resistance_pct"] = m.group(2)
    else:
        m2 = re.search(r"(\d+\.\d+%)", seg)
        info["resistance_pct"] = m2.group(1) if m2 else "未知"
        info["resistance_level"] = "未知"

    # 解放状态（无抵抗时显示）
    if "LIBERTY SECURED" in body:
        info["secured"] = True
    if "UNDEMOCRATIC PUSHBACK" in body or "ENEMY RESISTANCE" in body:
        info["fighting"] = True

    # 入侵战役（DEFENSE CAMPAIGN + HP）
    if "DEFENSE CAMPAIGN" in body.upper():
        sa_start = body.upper().find("STRATEGIC ANALYSIS")
        sa_zone = body[sa_start:] if sa_start >= 0 else body
        dc = sa_zone.upper().find("DEFENSE CAMPAIGN")
        if dc >= 0:
            after = sa_zone[dc + len("DEFENSE CAMPAIGN"):]
            # 找 HP 数值对（如 240,092 / 700,000）
            hp_matches = re.findall(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", after)
            if hp_matches:
                cur, maxh = hp_matches[0]
                info["defense_hp"] = (cur, maxh)
            # 找战役描述：以战役典型开头（The ... swarm/incursion/invasion 等）
            desc_m = re.search(
                r"(?:The|A|An)\s+[A-Za-z][A-Za-z ,.'\-]{20,200}?\.",
                after,
            )
            if desc_m:
                info["defense_desc"] = desc_m.group(0).strip()

    # 入侵等级（STRATEGIC ANALYSIS 区域：ENEMY INVASION LEVEL）
    if "ENEMY INVASION LEVEL" in body.upper():
        sa_start = body.upper().find("STRATEGIC ANALYSIS")
        sa_zone = body[sa_start:] if sa_start >= 0 else body
        # 取 ENEMY INVASION LEVEL 标签前的数字作为等级
        up_sa = sa_zone.upper()
        iil = up_sa.find("ENEMY INVASION LEVEL")
        if iil > 0:
            before = sa_zone[:iil]
            nums = re.findall(r"(\d{1,3})\s*\|?\s*$", before.strip())
            if nums:
                info["invasion_level"] = nums[-1]
            else:
                # 兜底：找标签前最后一个独立数字
                m = re.findall(r"(\d{1,3})", before)
                if m:
                    info["invasion_level"] = m[-1]
        info["invasion_level_found"] = True

    # OPERATIONAL PARAMETERS：部队 + 环境 + 行动变量（截到 POINTS OF INTEREST 之前，最多 3000 字符）
    seg2 = grab("OPERATIONAL PARAMETERS")
    poi_i = seg2.upper().find("POINTS OF INTEREST")
    if poi_i >= 0:
        seg2 = seg2[:poi_i]
    info["raw_params"] = seg2[:3000]

    # OBJECTIVES：战略目标区域（位于 OPERATIONAL PARAMETERS 之前）
    seg_obj = grab("OBJECTIVES")
    info["raw_objectives"] = seg_obj[:900]

    # 重要指令目标标记
    if "MAJOR ORDER TARGET" in body.upper():
        info["mo_target"] = True

    # POINTS OF INTEREST（完整区域，含 HISTORIC POINT OF INTEREST）
    seg3 = grab("POINTS OF INTEREST")
    # 截断到历史时间线（月份词如 JANUARY/FEBRUARY 出现）之前，避免历史记录里的 Hive World 等词误匹配
    hist_match = re.search(r"\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\b", seg3.upper())
    if hist_match:
        seg3 = seg3[: hist_match.start()]
    info["raw_poi"] = seg3[:1500]
    return info


def _translate_planet_info(info: dict) -> str:
    """翻译星球信息为中文输出"""
    lines = []

    # 超级地球：固定输出（页面为星系总览，无星球战况数据）
    if info.get("is_super_earth"):
        lines.append("🌍 超级地球（Super Earth）—— 人类的家园")
        lines.append("📌 特殊状态：")
        lines.append("·[人类的故乡]")
        lines.append("【“民主在等待你，公民。成为英雄，成为传奇，成为...绝地潜兵！”】")
        lines.append("")
        lines.append("⚔️ 特殊事件：超级地球保卫战 | Battle of Super Earth")
        lines.append("事件状态：胜利")
        lines.append("特殊描述：棒约克的潜兵决定放弃他们的家园，进行远征并修复了DSS。历经为期约2周的保卫战后，两座特大城市：孤军坚守的仰齐浜、被民主加护的荣都，依旧屹立不倒。")
        lines.append("我们要让入侵我们家园的凶手：光能族，付出代价！")
        return "\n".join(lines)

    # 抵抗度 / 解放状态
    if info.get("secured"):
        lines.append("🟢 该星球已被超级地球解放（LIBERTY SECURED）")
    elif info.get("resistance_level") != "未知":
        lvl = info.get("resistance_level", "未知")
        pct = info.get("resistance_pct", "未知")
        lvl_cn = RESIST_LEVEL_CN.get(lvl, lvl)
        lines.append(f"⚔️ 敌方抵抗度：{pct}（{lvl_cn}）")
    else:
        lines.append("⚔️ 敌方抵抗度：暂无数据（该星球未处于活跃战事）")

    # 入侵战役（DEFENSE CAMPAIGN）
    if info.get("defense_hp"):
        cur, maxh = info["defense_hp"]
        try:
            pct = int(cur.replace(",", "")) / int(maxh.replace(",", "")) * 100
            pct_s = f"{pct:.1f}%"
        except Exception:
            pct_s = "?"
        lines.append(f"⚔️ 入侵战役：敌方 HP {cur} / {maxh}（{pct_s}）")
        if info.get("defense_desc"):
            lines.append(f"📋 战役描述：{info['defense_desc']}")

    # 入侵等级（ENEMY INVASION LEVEL）
    if info.get("invasion_level_found"):
        level = info.get("invasion_level", "?")
        lines.append(f"⚠️ 入侵等级：{level}（越高的入侵等级，则意味着该星球需要通过完成更多的任务来阻挡攻势。）")

    # 行动变量（全部列出，列表形式）
    raw = info.get("raw_params", "")
    param_lines = []
    # 部队
    for en, cn in ENEMY_RACE_CN.items():
        if en in raw.upper():
            param_lines.append(f"• 敌方部队：{cn}")
            break
    # 所有环境（可能多个 ENVIRONMENTAL CONDITION）
    env_matches = re.findall(r"ENVIRONMENTAL CONDITION\n([A-Z][A-Z ]*[A-Z]|[A-Z]+)", raw)
    for env_raw in env_matches:
        env_raw = env_raw.strip()
        env_cn = ENV_COND_CN.get(env_raw)
        if not env_cn:
            base = re.sub(r"^(DIURNAL|NOCTURNAL) ", "", env_raw)
            env_cn = ENV_COND_CN.get(base)
        if not env_cn:
            env_cn = env_raw
        param_lines.append(f"• 环境：{env_cn}")
    if param_lines:
        lines.append("📋 行动变量：")
        lines.extend(param_lines)

    # 跃迁航道封锁（Warp Link Blockade）
    if info.get("warp_blockade"):
        links = info["warp_blockade"]
        names = []
        for w in links:
            nm = _get_planet_name_map().get(str(w), _to_slug(str(w)))
            names.append(nm)
        nm_s = "、".join(names)
        lines.append("🚀 跃迁航道封锁：")
        lines.append(f"由于此地通往[{nm_s}]的跃迁航道为单向航道，目前无法对该地的光能族发动解放战争。")

    # 行动变量（官方效果词条，官方源优先输出）
    if info.get("official_effects"):
        lines.append("📋 行动变量（官方数据）：")
        for e in info["official_effects"]:
            lines.append(f"• {e}")

    # 重要指令目标
    if info.get("mo_target"):
        lines.append("🎯 重要指令目标：该星球是本次重要指令的目标之一，绝地潜兵，解放它。")

    # 战略目标（OBJECTIVES）：命中参数名后直接输出对照表固定描述
    raw_obj = info.get("raw_objectives", "")
    obj_found = []
    # 按页面出现顺序找第一个 OBJECTIVE 参数名
    first_idx = -1
    first_en = None
    for obj_en in OBJECTIVE_CN:
        idx = raw_obj.upper().find(obj_en)
        if idx >= 0 and (first_idx == -1 or idx < first_idx):
            first_idx = idx
            first_en = obj_en
    if first_en:
        obj_cn, obj_desc = OBJECTIVE_CN[first_en]
        obj_found.append((obj_cn, obj_desc))
    if obj_found:
        lines.append("🎯 战略目标：")
        for obj_cn, obj_desc in obj_found:
            lines.append(f"• {obj_cn}：{obj_desc}")

    # 行动变量识别：遍历所有分组，命中输出该组全部参数（分组名未显示时按参数归组兜底）
    raw_upper = raw.upper()
    hit_groups = set()
    hit_vars = set()
    for var_en in VARIANT_CN:
        if var_en in raw_upper:
            hit_vars.add(var_en)
            grp = VARIANT_GROUP_OF.get(var_en)
            if grp:
                hit_groups.add(grp)
    for grp_en in VARIANT_GROUP_CN:
        if grp_en in raw_upper:
            hit_groups.add(grp_en)
    for grp_en in VARIANT_GROUP_CN:  # 保持对照表顺序输出
        if grp_en not in hit_groups:
            continue
        variant_lines = []
        for var_en, (var_cn, var_desc) in VARIANT_CN.items():
            if VARIANT_GROUP_OF.get(var_en) == grp_en and var_en in hit_vars:
                variant_lines.append(f"• {var_cn}：{var_desc}")
        if variant_lines:
            icon = VARIANT_GROUP_ICON.get(grp_en, "•")
            lines.append(f"{icon} {VARIANT_GROUP_CN[grp_en]}：")
            lines.extend(variant_lines)

    # DSS 战略行动（TACTICAL ACTION）识别
    tac_found = []
    for tac_en, (tac_cn, tac_desc) in TACTICAL_ACTION_CN.items():
        if tac_en in raw_upper:
            tac_found.append((tac_cn, tac_desc))
    if tac_found:
        lines.append("🛰️ DSS 战略行动：")
        for tac_cn, tac_desc in tac_found:
            lines.append(f"• {tac_cn}：{tac_desc}")

    # POI（特殊状态）：匹配到对照表中所有参数全部输出，格式 ·[参数] / 【内容】
    poi_raw = info.get("raw_poi", "")
    poi_cn = []
    for key in ["PANDORA BASE", "CENTER FOR THE CONTAINMENT OF DISSIDENCE",
                "TERMINID CONTROL SYSTEM +", "DEMOCRACY SPACE STATION",
                "CYBORG HOMEWORLD", "AUTOMATON HOMEWORLD", "INTERSTELLAR VOID", "HIVE WORLD", "HUMAN HOMEWORLD"]:
        if key in poi_raw.upper():
            poi_cn.append((key, POI_SPECIAL_CN[key]))
    if poi_cn:
        lines.append("📌 特殊状态：")
        for key, (title, desc) in poi_cn:
            lines.append(f"·[{title}]")
            lines.append(f"【{desc}】")
    else:
        # 通用 POI：ENEMY INFRASTRUCTURE 类别后的名称行
        poi_title = ""
        lines2 = poi_raw.strip().split("\n")
        for i, ln in enumerate(lines2):
            ln = ln.strip()
            if ln in ("ENEMY INFRASTRUCTURE", "ESSENTIAL INFRASTRUCTURE", "MAJOR POINT OF INTEREST", "POINT OF INTEREST", "HISTORIC POINT OF INTEREST"):
                if i + 1 < len(lines2):
                    nxt = lines2[i + 1].strip()
                    if nxt and "POINTS OF INTEREST" not in nxt:
                        poi_title = nxt
                break
        if not poi_title and lines2:
            for ln in lines2:
                ln = ln.strip()
                if ln and "POINTS OF INTEREST" not in ln and not re.fullmatch(r"[\d./: -]+", ln):
                    poi_title = ln
                    break
        if poi_title:
            lines.append(f"📌 兴趣点：{poi_title}")

    return "\n".join(lines)


# ============ 星球信息功能 END ============



class Hd2PlanetInfoPlugin(Star):
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
        # 兜底：文本里包含 @自己 的 CQ 码 或 @昵称
        text = event.message_str or ""
        if f"at,qq={self_id}" in text or f"at,qq={self_id}]" in text:
            return True
        # aiocqhttp 可能把 @ 渲染成文本（如 @retune.）
        if re.search(r"@[\w.\-]+", text):
            return True
        return False
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event: AstrMessageEvent):
        """星球信息触发：@ + 星球名 + 信息/情报/详情"""
        text = (event.message_str or "").strip().lower()
        logger.info(f"[HD2-PlanetInfo] HANDLER CALLED text={text!r} self_id={event.get_self_id()!r} wake={event.is_wake_up()}")
        if not text:
            return
        # 清洗：去掉开头的 @昵称 前缀（如 @retune. 奥密克戎信息 -> 奥密克戎信息）
        text_clean = re.sub(r"^@[\w.\-]+\s*", "", text).strip()
        # 仅响应 / 指令
        if not text_clean.startswith("/"):
            return
        text_clean = text_clean[1:].strip()
        # 支持 /查询 星球名 格式（等价于 /星球名信息）
        if text_clean.startswith("星球"):
            text_clean = re.sub(r"^星球\s*", "", text_clean).strip()
            # /星球 后直接是星球名（无需 信息 后缀）
            slug = _planet_slug_from_query(text_clean)
            has_kw = slug is not None
        elif text_clean.startswith("查询") or text_clean.startswith("查"):
            text_clean = re.sub(r"^(查询|查)\s*", "", text_clean).strip()
            # 查询后直接是星球名（无需 信息 后缀）
            slug = _planet_slug_from_query(text_clean)
            has_kw = slug is not None
        else:
            has_kw = ("信息" in text_clean or "情报" in text_clean or "详情" in text_clean or text_clean.endswith("info"))
            slug = _planet_slug_from_query(text_clean) if has_kw else None
        is_at = self._is_at_me(event)
        logger.info(f"[HD2-PlanetInfo] has_kw={has_kw} is_at={is_at} slug={slug} text_clean={text_clean!r}")
        if not has_kw:
            return
        if not slug:
            return
        _t0 = time.time()
        _log("matched", plugin="planet_info", text=text_clean, group=_gid(event), user=_uid(event), name=_uname(event))
        try:
            # 星球中文名标题（经星图对照表查验替换）
            title_cn = None
            for en, cn in _get_planet_name_map().items():
                if cn and en.lower().replace(" ", "_") == slug:
                    title_cn = cn
                    break
            if not title_cn and slug in ("super-earth", "superearth", "super_earth"):
                title_cn = "超级地球"
            # 超级地球：直接硬编码输出（页面是星系总览，无星球战况数据）
            if slug in ("super-earth", "superearth", "super_earth"):
                result = _translate_planet_info({"is_super_earth": True})
            else:
                # 官方源优先（planetStatus/planetActiveEffects/planetEvents），页面抓取兜底
                info = None
                idx = _slug_to_index(slug)
                if idx is not None:
                    try:
                        info = _fetch_planet_official(idx)
                    except Exception as e:
                        logger.warning(f"[HD2-PlanetInfo] 官方详情失败({slug}): {type(e).__name__}: {e}")
                        info = None
                if info:
                    result = _translate_planet_info(info)
                else:
                    body = await _fetch_planet_page_text(slug)
                    info = _parse_planet_info(body)
                    result = _translate_planet_info(info)
            # 输出开头加中文星球名
            if title_cn:
                result = f"🪐 {title_cn}\n\n" + result
            yield event.plain_result(result)
            _log("done", plugin="planet_info", text=text_clean, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail=result[:60])
        except Exception as e:
            logger.warning(f"[HD2] 星球信息抓取失败: {e}")
            _log("failed", plugin="planet_info", text=text_clean, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail=str(e)[:200])
            yield event.plain_result(f"⚠️ 暂时无法获取该星球信息：{e}")
        event.stop_event()
