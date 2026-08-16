# -*- coding: utf-8 -*-
"""HD2 公共库：共享常量 / HTTP 抓取 / 表加载 / 译名映射

供 hd2_planet_info / hd2_variant_query / hd2_war_report 复用，
消除跨插件重复代码。各插件通过父目录 sys.path 注入后 import：
    from astrbot_plugin_hd2_common.main import fetch_json, fetch_official, ...
"""
import json
import os as _os
import sys as _sys
from urllib.request import Request, urlopen

# ============ 数据源常量 ============
# 官方 API（主源）：2026-08 从游戏抓包发现的新官方后端，公开可访问（仅需 X-Super-Client 头）
OFFICIAL_API_URL = "https://api.live.prod.thehelldiversgame.com"
OFFICIAL_WAR_ID = 801
# 社区聚合（companion）
LIVE_API_URL = "https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live"
ASSIGNMENTS_URL = "https://cdn.helldiverscompanion.com/live/assignments/recent.json"
EXTENDED_API_URL = "https://cdn.helldiverscompanion.com/live/extendedApiInformation/2days.json"
# 星球名（helldivers2.dev，浏览器可直连，支持 zh-Hans）
PLANETS_URL = "https://api.helldivers2.dev/api/v1/planets"
# 机翻兜底
TRANSLATE_URL = "https://api.mymemory.translated.net/get"

# 官方 API 请求头（X-Super-Client 为风控识别头，无密钥）
_OFFICIAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-HD2-Plugin/1.0",
    "Accept": "application/json",
    "X-Super-Client": "YOUR_QQ_NUMBER",
    "X-Super-Contact": "YOUR_QQ_NUMBER@qq.com",
    "Accept-Language": "en-US",
}


def fetch_json(url: str, timeout: int = 20, extra_headers: dict | None = None):
    """通用 HTTP GET + JSON 解析；带 UA/Accept 头，可附加额外头"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBot-HD2-Plugin/1.0",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_official(path: str, timeout: int = 20):
    """官方 API 主源（优先）；失败抛异常由调用方回退社区源"""
    return fetch_json(OFFICIAL_API_URL + path, timeout=timeout, extra_headers=_OFFICIAL_HEADERS)


def table_candidates(fname: str) -> list:
    """表文件候选路径链：HD2_PROJECT_DIR -> 项目根(插件父父目录) -> 插件目录副本 -> 公共库目录"""
    cands = []
    root = _os.environ.get("HD2_PROJECT_DIR", "")
    if root:
        cands.append(_os.path.join(root, "tables", fname))
    # 插件与项目同仓发布时：<项目根>/tables/（公共库位于 <项目根>/plugins/astrbot_plugin_hd2_common/）
    here = _os.path.dirname(_os.path.abspath(__file__))
    proj_root = _os.path.dirname(_os.path.dirname(here))
    cands.append(_os.path.join(proj_root, "tables", fname))
    cands.append(_os.path.join(here, "tables", fname))
    cands.append(_os.path.join(here, fname))
    return cands


def load_effect_id_cn() -> dict:
    """加载效果 ID -> 中文词条映射（tables/effect_id_cn.json，官方 planetActiveEffects 用）"""
    try:
        path = next((c for c in table_candidates("effect_id_cn.json") if _os.path.exists(c)), "")
        if not path:
            return {}
        with open(path, encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except Exception:
        return {}


def load_starmap_sectors() -> dict:
    """读星图对照表：星球英文名(大写) -> 星区显示名（对照表第一列）"""
    try:
        cands = []
        root = _os.environ.get("HD2_PROJECT_DIR", "")
        if root:
            cands.append(_os.path.join(root, "tables", "星图对照表_修正版.md"))
            cands.append(_os.path.join(root, "tables", "星图对照表.md"))
        here = _os.path.dirname(_os.path.abspath(__file__))
        proj_root = _os.path.dirname(_os.path.dirname(here))
        cands.append(_os.path.join(proj_root, "tables", "星图对照表_修正版.md"))
        cands.append(_os.path.join(proj_root, "tables", "星图对照表.md"))
        cands.append(_os.path.join(here, "星图对照表_修正版.md"))
        cands.append(_os.path.join(here, "tables", "星图对照表_修正版.md"))
        path = next((c for c in cands if _os.path.exists(c)), "")
        if not path:
            return {}
        m = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("|"):
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) < 3:
                    continue
                sec, en, cn = cells[0], cells[1], cells[2]
                if en in ("英文", "---", "") or sec in ("星区", "---"):
                    continue
                m[en.upper()] = sec
        return m
    except Exception:
        return {}


def load_planet_map() -> dict:
    """index -> 星球信息 dict：name/sector/owner/health/maxHealth/event/players（helldivers2.dev）"""
    try:
        planets = fetch_json(
            PLANETS_URL,
            extra_headers={
                "X-Super-Client": "astrbot-hd2-plugin",
                "X-Super-Contact": "https://github.com/astrbot",
            },
        )
        result = {}
        for p in planets:
            info = {
                "name": p.get("name", "未知星球"),
                "sector": p.get("sector", ""),
                "owner": p.get("currentOwner", ""),
                "health": p.get("health"),
                "maxHealth": p.get("maxHealth"),
                "event": p.get("event"),
                "players": (p.get("statistics") or {}).get("playerCount", 0),
            }
            result[str(p.get("index"))] = info
        return result
    except Exception:
        return {}


# ============ 星球译名表（用户维护，两插件共用；与 星图对照表_修正版.md 保持一致） ============

PLANET_NAME_CN = {
    "ACAMAR IV": "天园六 IV",
    "ACHERNAR SECUNDUS": "水委一次星",
    "ACHIRD III": "王良三 III",
    "ACRAB XI": "房宿四 XI",
    "ACRUX IX": "十字架二 IX",
    "ACUBENS PRIME": "柳宿增三主星",
    "ADHARA": "弧矢七",
    "AESIR PASS": "亚萨关隘",
    "AFOYAY BAY": "艾福亚湾",
    "AIN-5": "毕宿一 5",
    "ALAIRT III": "艾列尔特 III",
    "ALAMAK VII": "天大将军一 VII",
    "ALARAPH": "右执法",
    "ALATHFAR XI": "织女增三 XI",
    "ALDERIDGE COVE": "阿尔德里奇湾",
    "ALTA V": "奥尔塔V",
    "ANDAR": "安达尔",
    "ANGEL'S VENTURE": "天使投资",
    "ARKTURUS": "阿克图勒斯",
    "ASPEROTH PRIME": "阿斯佩洛斯主星",
    "ATRAMA": "阿特拉玛",
    "AURORA BAY": "极光湾",
    "AZTERRA": "艾孜泰拉",
    "AZUR SECUNDUS": "艾热尔次星",
    "BALDRICK PRIME": "巴尔德里克主星",
    "BARABOS": "巴勒博斯",
    "BASHYR": "巴希尔",
    "BASQUINE VIII": "巴斯金 VIII",
    "BEKVAM III": "贝克温",
    "BELLATRIX": "参宿五",
    "BIG ROCK": "巨岩",
    "BLISTICA": "布里斯提卡",
    "BORE ROCK": "博尔岩",
    "BOREA": "博瑞亚",
    "BOTEIN": "天阴四",
    "BRILLIANCE": "璨光",
    "BRINK-2": "布林克2",
    "BUNDA SECUNDUS": "天垒城一次星",
    "CALYPSO": "卡利普索",
    "CANOPUS": "老人",
    "CAPH": "王良一",
    "CARAMOOR": "开勒莫尔",
    "CASTOR": "北河二",
    "CERBERUS IIIC": "刻耳柏洛斯IIIc",
    "CHARBAL-VII": "查巴尔 VII",
    "CHARON PRIME": "卡戎主星",
    "CHOEPESSA IV": "科埃佩萨 IV",
    "CHOOHE": "丘伊",
    "CHORT BAY": "雀特湾",
    "CIRRUS": "希勒斯",
    "CLAORELL": "可洛威尔",
    "CLASA": "克拉萨",
    "CRIMSICA": "克里姆西卡",
    "CRUCIBLE": "熔炉",
    "CURIA": "居里亚",
    "CYBERSTAN": "生化斯坦",
    "DARIUS II": "大流士 II",
    "DARROWSPORT": "达罗斯波特",
    "DEMIURG": "戴米尔基",
    "DENEB SECUNDUS": "天津四次星",
    "DILUVIA": "迪卢维亚",
    "DOLPH": "多尔夫",
    "DRAUPNIR": "德罗普尼尔",
    "DUMA TYR": "杜马提尔",
    "DURGEN": "德尔根",
    "EAST IRIDIUM TRADING BAY": "东铱贸易湾",
    "EFFLUVIA": "艾芙鲁维亚",
    "ELECTRA BAY": "昂宿一湾",
    "ELYSIAN MEADOWS": "埃律西昂草原",
    "EMERIA": "埃梅里亚",
    "EMORATH": "艾莫拉斯",
    "EPSILON PHOENCIS VI": "厄普西隆VI",
    "ERATA PRIME": "艾拉塔主星",
    "ERSON SANDS": "厄尔森桑兹",
    "ESKER": "艾斯科尔",
    "ESTANU": "伊斯塔努",
    "EUKORIA": "欧科里亚",
    "EUPHORIA III": "欧福利亚 III",
    "FENMIRE": "范迈尔",
    "FENRIR III": "芬里尔 III",
    "FORI PRIME": "佛里主星",
    "FORNSKOGUR II": "福恩斯科古尔II",
    "FORT JUSTICE": "正义堡",
    "FORT SANCTUARY": "庇护堡",
    "FORT UNION": "联合堡",
    "FREEDOM PEAK": "弗里敦峰",
    "FRONTERIA": "边陲",
    "FURY": "费里",
    "GACRUX": "十字架一",
    "GAELLIVARE": "耶利瓦勒",
    "GAR HAREN": "轧尔哈伦",
    "GATRIA": "盖尔崔亚",
    "GEMMA": "贯索四",
    "GENESIS PRIME": "创世主星",
    "GRAFMERE": "格拉夫米尔",
    "GRAND ERRANT": "大艾伦特",
    "GUNVALD": "古恩瓦尔德",
    "HADAR": "马腹一",
    "HAKA": "哈卡",
    "HALDUS": "海德斯",
    "HALIES PORT": "海利斯港",
    "HEETH": "希斯",
    "HELLMIRE": "海尔迈尔",
    "HERTHON SECUNDUS": "赫尔松次星",
    "HESOE PRIME": "海索主星",
    "HORT": "哈尔特",
    "HYDROBIUS": "哈卓毕亚斯",
    "HYDROFALL PRIME": "水瀑主星",
    "IGLA": "依格勒",
    "ILDUNA PRIME": "伊尔都纳主星",
    "IMBER": "因博尔",
    "INARI": "伊纳里",
    "INGMAR": "英格玛",
    "IRIDICA": "艾利迪卡",
    "IRO": "伊罗",
    "IRULTA": "艾鲁尔塔",
    "IVIS": "艾维斯",
    "JULHEIM": "尤尔海姆",
    "K": "K",
    "KARLIA": "可利亚",
    "KEID": "角宿二湾",
    "KELVINOR": "开尔文奥尔",
    "KERTH SECUNDUS": "克斯次星",
    "KHANDARK": "勘达尔克",
    "KHARST": "喀斯特",
    "KIRRIK": "基里克",
    "KLAKA 5": "克拉卡5",
    "KLEN DAHTH II": "克伦达斯 II",
    "KNETH PORT": "克奈斯港",
    "KRAKABOS": "克拉克博斯",
    "KRAKATWO": "克拉克图",
    "KRAZ": "轸宿四",
    "KUMA": "天棓二",
    "KUPER": "库珀",
    "LASTOFE": "赖斯斗夫",
    "LENG SECUNDUS": "蓝恩次星",
    "LESATH": "尾宿八",
    "LIBERTY RIDGE": "解放岭",
    "LUXURIANT": "富源",
    "MAIA": "昂宿四",
    "MALEVELON CREEK": "麦拉芬蒙河",
    "MANTES": "蒙特斯",
    "MARFARK": "玛尔法克",
    "MARRE IV": "马尔IV",
    "MARS": "火星",
    "MARTALE": "玛尔特",
    "MARTYR'S BAY": "烈士湾",
    "MASTIA": "玛斯蒂娅",
    "MATAR BAY": "玛塔",
    "MAW": "深渊",
    "MEISSA": "觜宿一",
    "MEKBUDA": "井宿七",
    "MENKENT": "库楼三",
    "MERAK": "北斗二",
    "MERGA IV": "玄戈增二 IV",
    "MERIDIA": "默里迪亚",
    "MIDASBURG": "弥达斯堡",
    "MINTORIA": "敏托瑞亚",
    "MOG": "莫格",
    "MORADESH": "莫拉戴什",
    "MORDIA 9": "摩帝亚9",
    "MORT": "莫特",
    "MORTAX PRIME": "摩尔塔克斯主星",
    "MOX": "莫克斯",
    "MYRADESH": "米拉戴什",
    "MYRIUM": "梅里翁",
    "NEW HAVEN": "纽黑文",
    "NEW INSIGHT": "NEW INSIGHT",
    "NEW KIRUNA": "新基鲁纳",
    "NEW STOCKHOLM": "新斯德哥尔摩",
    "NIVEL 43": "尼维尔43",
    "NUBLARIA I": "努布拉里亚I",
    "OASIS": "绿洲",
    "OBARI": "欧巴里",
    "OKUL VI": "欧库VI",
    "OMICRON": "奥密克戎",
    "OSHAUNE": "欧绍恩",
    "OSLO STATION": "奥斯陆站",
    "OSUPSAM": "欧苏普森",
    "OUTPOST 32": "32号哨站",
    "OVERGOE PRIME": "欧维果主星",
    "PANDION-XXIV": "帕迪恩 XXIV",
    "PARSH": "帕尔什",
    "PARTION": "帕尔晨",
    "PATHFINDER V": "开拓者 V",
    "PEACOCK": "孔雀十一",
    "PENTA": "彭塔",
    "PHACT BAY": "丈人一湾",
    "PHERKAD SECUNDUS": "北极一次星",
    "PILEN V": "皮伦 V",
    "PIONEER II": "先驱 II",
    "POLARIS PRIME": "北极星主星",
    "POLLUX 31": "北河三 31",
    "PRASA": "普拉萨",
    "PRIMORDIA": "普莱默迪亚",
    "PROPUS": "五诸侯三",
    "PROSPERITY FALLS": "繁荣瀑布",
    "PROVIDENCE": "普罗维登斯",
    "PÖPLI IX": "珀普利 IX",
    "RAS ALGETHI": "帝座",
    "RASP": "拉斯普",
    "RATCH": "拉奇",
    "RD-4": "RD-4",
    "REAF": "利夫",
    "REGNUS": "雷格努斯",
    "RIRGA BAY": "里尔加湾",
    "ROGUE 5": "罗格5",
    "SANGIS": "赤血",
    "SEASSE": "西斯",
    "SENGE 23": "SENGE 23",
    "SETIA": "塞提亚",
    "SEYSHEL BEACH": "塞舌尔海滩",
    "SHALLUS": "沙勒斯",
    "SHELT": "谢尔特",
    "SHETE": "赛特",
    "SIEMNOT": "西姆诺特",
    "SIRIUS": "天狼星",
    "SKAASH": "斯卡什",
    "SKAT BAY": "斯卡特湾",
    "SKITTER": "斯基特",
    "SLIF": "斯利夫",
    "SOCORRO III": "索科罗III",
    "SOLGHAST": "索尔加斯特",
    "SPHERION": "斯飞利昂",
    "STOR THA PRIME": "斯特萨主星",
    "STOUT": "斯图尔特",
    "SULFURA": "萨尔弗拉",
    "SUPER EARTH": "超级地球",
    "TARSH": "塔尔什",
    "TERMADON": "特尔玛登",
    "TERREK": "泰雷克",
    "THE WEIR": "维尔",
    "TIBIT": "提比特",
    "TIEN KWAN": "天关",
    "TRANDOR": "特兰道尔",
    "TROOST": "特鲁斯特",
    "TURING": "图灵",
    "UBANEA": "乌巴尼亚",
    "URSICA XI": "厄西卡 XI",
    "USTOTU": "伍斯特图",
    "UVP ALPHA": "UVP阿尔法",
    "UVP BETA": "UVP贝塔",
    "UVP DELTA": "UVP德尔塔",
    "UVP GAMMA": "UVP伽马",
    "VALGAARD": "瓦尔加德",
    "VALMOX": "瓦尔莫克斯",
    "VANDALON IV": "万达隆 IV",
    "VARYLIA 5": "瓦拉瑞亚 5",
    "VEGA BAY": "织女一湾",
    "VEIL": "帷幕",
    "VELD": "维尔德",
    "VERNEN WELLS": "佛农井",
    "VINDEMITARIX PRIME": "文德米塔里克斯主星",
    "VIRIDIA PRIME": "维尔伊迪亚主星",
    "VOG-SOJOTH": "佛戈索约斯",
    "VOLTERRA": "沃尔泰拉",
    "WASAT": "天樽二",
    "WAYWARD": "WAYWARD",
    "WEZEN": "弧矢一",
    "WIDOW'S HARBOR": "寡妇港",
    "WILFORD STATION": "威尔福德站",
    "WRAITH": "幽灵",
    "X-45": "X-45",
    "YED PRIOR": "天市右垣六",
    "ZAGON PRIME": "扎贡主星",
    "ZEA RUGOSIA": "泽亚鲁戈西亚",
    "ZEFIA": "塞飞亚",
    "ZEGEMA PARADISE": "泽格玛乐土",
    "ZOSMA": "太徽右垣五",
    "ZYGOS": "齐戈斯",
    "ZZANIAH PRIME": "藏尼亚主星",
}


# 星区译名（sector id -> 中文，官方/社区 sector 字段用）
SECTOR_CN = {
    "Akira": "阿基拉",
    "Alstrad": "阿尔斯特拉德",
    "Altus": "阿尔特斯",
    "Andromeda": "仙女座",
    "Arkturus": "阿图里昂",
    "Borgus": "博格斯",
    "Farsight": "法尔赛特",
    "Hanzo": "半藏",
    "Hawking": "霍金",
    "Jin Xi": "锦栖",
    "L'estrade": "莱斯特拉德",
    "Meridia": "默里迪亚",
    "Mirin": "米琳",
    "Omega": "欧米茄",
    "Orion": "猎户座",
    "Rigel": "参宿七",
    "Sol": "太阳系",
    "Tanis": "塔尼斯",
    "Trigon": "特里贡",
    "Umlaut": "乌姆劳特",
    "Valdis": "瓦尔迪斯",
    "Xi Tauri": "金牛座",
    "Xzar": "兹亚尔",
    "Ymir": "土卫十九",
}


# ============ 占位插件类（公共库不注册指令，仅作为模块被其他插件 import） ============
from astrbot.api.star import Star

class Hd2CommonPlugin(Star):
    def __init__(self, context) -> None:
        super().__init__(context)
