# -*- coding: utf-8 -*-
"""/战报 图片卡渲染器（astrbot_plugin_hd2_war_report）

主站黑金 HUD 风格（tokens 同源）：零圆角 / 硬描边 / 琥珀强调 / 45° 斜纹头。
数据口径与主站一致：data.json（官方 API 落盘 + 翻译仓中文）+ player_distribution。
设计为纯函数式入口：render_report_card() → PNG 路径（失败返回 None，调用方回退文本）。
缓存：按 data.fetchedAt 记忆最近一次成品，同版本重复调用直接复用文件。
"""
import json
import re
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont
import hashlib
import tempfile
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot_plugin_hd2_common import api_watch
from astrbot_plugin_hd2_common import data as shared
from astrbot_plugin_hd2_common.commands import mo_tasks
_render_locks = {}
_render_lock_guard = threading.Lock()

DATA_URL = "https://jerry114514.github.io/HD2-Galatic_war-Map/data.json"
DIST_URL = "https://jerry114514.github.io/HD2-Galatic_war-Map/data/history/player_distribution.json"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
OUT_NAME = "report_card.png"
FETCH_TIMEOUT = 20
FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",) * 2,
]

# ── 主站 tokens（css/tokens.css 同源） ──
BG = (10, 10, 10)
PANEL = (18, 18, 18)
LINE = (36, 36, 36)
LINE2 = (42, 42, 42)
TEXT = (242, 242, 242)
DIM = (168, 168, 168)
MUTE = (110, 110, 110)
GOLD = (255, 176, 0)
GOLD2 = (255, 201, 74)
BRASS = (200, 134, 42)
BLUE = (91, 163, 208)
GREEN = (31, 168, 74)
RED = (255, 77, 46)
W = 1080
PAD = 36
TALL = 2600

_fcache = {}
def F(sz, bold=False):
    if sz <= 24:
        sz += 2
    key = (sz, bold)
    if key not in _fcache:
        reg, bold_p = next((pair for pair in FONT_CANDIDATES if all(os.path.isfile(p) for p in pair)), ("DejaVuSans.ttf", "DejaVuSans.ttf"))
        _fcache[key] = ImageFont.truetype(bold_p if bold else reg, sz, index=0)
    return _fcache[key]


def paste_contain(img, src, box):
    from PIL import ImageOps
    x, y, width, height = box
    icon = ImageOps.contain(src.convert("RGBA"), (width, height))
    img.paste(icon, (x + (width-icon.width)//2, y + (height-icon.height)//2), icon)

def _get(url):
    return shared.get_json(url, ttl=300, timeout=FETCH_TIMEOUT)


_site_cache = {}  # url -> (ts, obj)·全卡共享 240s TTL


def _site_get(url, ttl=240):
    return shared.site_doc() if url == DATA_URL else shared.get_json(url, ttl=ttl)


FACTION_CN = {"Humans": "超级地球", "Terminid": "终结族", "Terminids": "终结族",
              "Automaton": "机器人", "Automatons": "机器人", "Illuminate": "光能者"}
FACTION_COLOR = {"超级地球": BLUE, "终结族": (245, 197, 24),
                 "机器人": (214, 128, 109), "光能者": (207, 100, 248)}
DSS_ACTION_SHORT = {"EAGLE STORM": "飞鹰风暴", "ORBITAL BLOCK": "轨道封锁", "ORBITAL BLOCKADE": "轨道封锁",
                    "HEAVY ORDNANCE DISTRIBUTION": "重型军械分发", "EAGLE BLOCK": "飞鹰封锁",
                    "PLANETARY BOMBARDMENT": "星球轰炸", "OPERATIONAL SUPPORT": "行动支持"}
ITEM_MIX = {3992382197: "普通样本", 2985106497: "稀有样本"}


def _static_map_json(url, cache_name, build):
    """静态表抓取：成功写盘，网络失败读磁盘副本（静态表永不失效）。"""
    cache_file = os.path.join(CACHE_DIR, cache_name)
    try:
        obj = _site_get(url)
        m = build(obj)
        if m:
            try:
                json.dump(m, open(cache_file, "w", encoding="utf-8"))
            except Exception:
                pass
            return m
    except Exception:
        pass
    try:
        return json.load(open(cache_file, encoding="utf-8"))
    except Exception:
        return {}


def _effect_cn_map_source():
    """hd2_variables.json 原始对象（被动增益分类查找用）"""
    return _site_get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/hd2_variables.json")


def _effect_cn_map():
    """hd2_variables.json → {effect_id: 中文词条名}"""
    def build(v):
        m = {}
        for cat in v.get("categories") or []:
            for e in cat.get("entries") or []:
                cn = e.get("cn") or e.get("name") or ""
                for eid in e.get("effect_ids") or []:
                    if eid is not None and cn:
                        m[eid] = cn
        return m
    return _static_map_json(
        "https://jerry114514.github.io/HD2-Galatic_war-Map/tables/hd2_variables.json",
        "effect_cn.json", build)


def _starmap_cn_map():
    """starmap.json → {EN_NAME_UPPER: 中文名}"""
    def build(sm):
        pls = sm.get("planets") or {}
        return {k.upper(): (v.get("cn") or "") for k, v in pls.items() if isinstance(v, dict)}
    return _static_map_json(
        "https://jerry114514.github.io/HD2-Galatic_war-Map/tables/starmap.json",
        "starmap_cn.json", build)


def _sector_maps():
    """starmap.json → {"pl_sec": 星球→游戏星区中文名, "sec_cn": hd2dev英文星区→中文名}
    主站口径（planet.html secCn）：官方 API 的 sector 字段不可靠（273 颗里 152 空、
    有值的仅 8 颗与游戏分区一致），星区一律以 starmap 的 system 优先，API sector 仅兜底。"""
    def build(sm):
        pl_sec = {}
        for k, v in (sm.get("planets") or {}).items():
            if isinstance(v, dict) and v.get("system"):
                pl_sec[str(k).upper()] = str(v["system"])
        sec_cn = {}
        for s in sm.get("systems") or []:
            if isinstance(s, dict) and s.get("name") and s.get("name_en"):
                sec_cn[str(s["name_en"]).upper()] = str(s["name"])
        return {"pl_sec": pl_sec, "sec_cn": sec_cn}
    return _static_map_json(
        "https://jerry114514.github.io/HD2-Galatic_war-Map/tables/starmap.json",
        "starmap_sector.json", build)


# ── 下属区域（主站 sec-regions 同款口径） ──
_REGION_BASE_HP = {"Settlement": 100000, "Town": 200000, "City": 400000, "MegaCity": 600000}
_REGION_TYPE_ZH = {"Settlement": "定居点", "Town": "城镇", "City": "城市", "MegaCity": "大城市"}
_REGION_STARS = {"Settlement": 1, "Town": 2, "City": 3, "MegaCity": 4}
_REGION_OWNER_NUM_CN = {1: "超级地球", 2: "终结族", 3: "机器人", 4: "光能者"}


def _region_tables():
    """主站区域静态表（84 星 / 211 区域）+ 官方简中名表（各自磁盘缓存，静态表永不失效）"""
    regions = _static_map_json(
        "https://jerry114514.github.io/HD2-Galatic_war-Map/tables/planet_regions.json",
        "planet_regions_static.json", lambda o: o.get("planets") or {})
    zh = _static_map_json(
        "https://jerry114514.github.io/HD2-Galatic_war-Map/tables/planet_regions_zh.json",
        "planet_regions_zh_static.json", lambda o: o.get("planets") or {})
    return regions, zh


def _iso_ts(s):
    """ISO 时间串 → datetime（失败 None；空串/缺字段容错）"""
    try:
        return datetime.fromisoformat(str(s or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _region_recent_frames(pidx, ridx):
    return shared.region_frames(pidx, ridx)


def _region_rate(pl, lv, impact):
    return shared.region_rate(pl, lv, impact)

def fmt(n):
    return f"{int(n or 0):,}"

def pct(p, g):
    try:
        return max(0.0, min(100.0, (p / g * 100.0) if g else 0.0))
    except Exception:
        return 0.0


def _is_word_char(c):
    return c.isascii() and (c.isalnum() or c in ".,%")

def wrap(text, width_chars):
    """按空格分词贪心打包：英文单词保持完整；无空格的中文长段按宽硬切。"""
    out = []
    for para in (text or "").replace("\r", "").split("\n"):
        para = para.strip()
        if not para:
            continue
        line = ""
        for tok in re.split(r"( +)", para):
            if not tok:
                continue
            while len(tok) > width_chars:          # 单个超长 token（纯中文长段）硬切
                if line:
                    out.append(line)
                    line = ""
                cut = width_chars
                # 拉丁词保护：切点落在英文/数字词中间 → 回退词首（避免 Terminid|s 断词）
                if _is_word_char(tok[cut - 1]) and cut < len(tok) and _is_word_char(tok[cut]):
                    j = cut
                    while j > 0 and _is_word_char(tok[j - 1]):
                        j -= 1
                    if cut - j < 24:
                        cut = j
                out.append(tok[:cut])
                tok = tok[cut:]
            if len(line) + len(tok) > width_chars and line:
                out.append(line)
                line = tok.lstrip()
            else:
                line += tok
        if line:
            out.append(line)
    return out

def stripe_band(img, y0, h, alpha=22):
    dr = ImageDraw.Draw(img, "RGBA")
    x = -h
    while x < W:
        dr.polygon([(x, y0 + h), (x + h, y0), (x + h + 7, y0), (x + 7, y0 + h)],
                   fill=(255, 176, 0, alpha))
        x += 14
    dr.line([(0, y0), (W, y0)], fill=LINE2)
    dr.line([(0, y0 + h - 1), (W, y0 + h - 1)], fill=LINE2)

def progress_bar(dr, x, y, w, h, ratio, fill=GOLD):
    dr.rectangle([x, y, x + w, y + h], fill=(31, 31, 31), outline=LINE2)
    fw = int(w * max(0.0, min(1.0, ratio)))
    if fw > 0:
        dr.rectangle([x, y, x + fw, y + h], fill=fill)
    for k in (1, 2, 3):
        dr.line([x + int(w * k / 4), y, x + int(w * k / 4), y + h], fill=(10, 10, 10))

def section_label(dr, x, y, text, f_label):
    dr.rectangle([x, y + 2, x + 6, y + 2 + 28], fill=GOLD)
    dr.text((x + 16, y), text, font=f_label, fill=TEXT)


def _wrap_rich_runs(runs, max_width, font, dr):
    lines = []
    line = []
    width = 0
    for text, tone in runs or ():
        for char in str(text or ""):
            if char in "\r":
                continue
            if char == "\n":
                if line:
                    lines.append(line)
                line, width = [], 0
                continue
            char_width = dr.textlength(char, font=font)
            if line and width + char_width > max_width:
                lines.append(line)
                line, width = [], 0
            if line and line[-1][1] == tone:
                line[-1] = (line[-1][0] + char, tone)
            else:
                line.append((char, tone))
            width += char_width
    if line:
        lines.append(line)
    return lines


def _draw_rich_lines(dr, x, y, lines, font, line_height, color=DIM):
    rich_colors = {"yellow": GOLD2, "blue": BLUE}
    for line in lines:
        xx = x
        for text, tone in line:
            dr.text((xx, y), text, font=font, fill=rich_colors.get(tone, color))
            xx += dr.textlength(text, font=font)
        y += line_height
    return y


def _money_amount(reward):
    if not reward:
        return "无"
    return (
        str(reward.get("name_cn") or reward.get("name") or "奖励")
        + (f" ×{reward.get('amount')}" if reward.get("amount") else "")
    )

def render_report_card(out_dir=None):
    out_dir = out_dir or CACHE_DIR
    data = _site_get(DATA_URL)
    distribution = shared.player_distribution(data)
    dist_hist = [{"totalPlayers":distribution["total"], "timestamp":distribution["updatedAt"],
                  "factions":dict(zip(("Humans","Terminids","Automatons","Illuminate"),
                                      (distribution[k] for k in ("humans","terminids","automatons","illuminate"))))}] if distribution["total"] is not None else []
    def draw(dr, img):
        now = datetime.now(timezone.utc).astimezone()
        a = data.get("assignments") or {}
        mo = data.get("major_order") or {}
        mtask = mo.get("tasks") or {}
        tasks = mo_tasks(data)
        brief = shared.major_order_content(data).get("text") or ""
        war = data.get("war") or {}
        online = distribution["total"]
        campaigns = data.get("campaigns") or []
        news = data.get("news") or []

        f_title = F(42, True); f_h2 = F(25, True); f_h3 = F(21, True)
        f_body = F(19); f_small = F(16); f_tiny = F(14)
        f_kpi = F(32, True); f_kpi_l = F(15)


        # ══ HUD 头 ══
        stripe_band(img, 0, 92, alpha=22)
        dr.rectangle([0, 0, 5, 92], fill=GOLD)
        dr.text((PAD, 14), "银河战争速报", font=f_title, fill=GOLD)
        dr.text((PAD + 266, 36), "GALACTIC WAR REPORT", font=F(15), fill=MUTE)
        ts = str(data.get("fetchedAt") or "未知")[:16].replace("T", " ")
        tw = dr.textlength(ts, font=f_small)
        dr.text((W - PAD - tw, 16), ts, font=f_small, fill=DIM)
        sw = dr.textlength("真理部 · MOT", font=f_small)
        dr.text((W - PAD - sw, 44), "真理部 · MOT", font=f_small, fill=BRASS)
        y = 92 + 16

        # ══ KPI 行 ══
        KPI_H = 86
        kpis = [("在线指挥官", fmt(online) if online is not None else "未知"),
                ("进行中战役", str(len(campaigns)) if campaigns else "—")]
        if tasks:
            kpis.append(("指令任务", f"{len(tasks)} 项"))
            kpis.append(("已完成任务", str(sum(1 for t in tasks if shared.percent(t.get("progress"), t.get("goal")) == 100)) + " 项"))
        else:
            kpis.append(("指令", "当前无"))
            kpis.append(("状态", "待命中"))
        kw = (W - PAD * 2 - 12 * 3) // 4
        for i, (lab, val) in enumerate(kpis):
            kx = PAD + i * (kw + 12)
            dr.rectangle([kx, y, kx + kw, y + KPI_H], fill=PANEL, outline=LINE)
            dr.rectangle([kx, y, kx + 4, y + KPI_H], fill=BRASS)
            dr.text((kx + 16, y + 10), lab, font=f_kpi_l, fill=MUTE)
            dr.text((kx + 16, y + 32), val, font=f_kpi, fill=TEXT)
        y += KPI_H + 18

        # ══ 重要指令面板 ══
        if tasks:
            brief_lines = wrap(brief, 46)
            head_h = 52
            brief_h = (16 + 25 * len(brief_lines) + 10) if brief_lines else 0
            task_h = sum(100 for _ in tasks)
            panel_h = head_h + brief_h + task_h + 16
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 14, "重要指令", f_h2)
            dl = a.get("deadline") or ""
            if dl:
                try:
                    dd = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                    hrs = (dd - datetime.now(timezone.utc)).total_seconds() / 3600
                    remain = f"距截止 {int(hrs // 24)} 天 {int(hrs % 24)} 小时" if hrs > 0 else "已到期"
                    rw = dr.textlength(remain, font=f_h3)
                    dr.text((W - PAD - 18 - rw, y + 15), remain, font=f_h3, fill=GOLD2)
                except Exception:
                    pass
            yy = y + head_h
            if brief_lines:
                for ln in brief_lines:
                    dr.text((PAD + 18, yy), ln, font=f_body, fill=DIM)
                    yy += 25
                yy += 10
                dr.line([(PAD + 18, yy), (W - PAD - 18, yy)], fill=LINE)
                yy += 6
            for i, t in enumerate(tasks):
                name = f"任务 {i + 1}：{t.get('name') or t.get('action') or ''}"
                dr.text((PAD + 18, yy), name, font=f_h3, fill=TEXT)
                req = t.get("requirement") or {}
                if req.get("type") == "equipment" and (req.get("name") or req.get("en")):
                    equip = "限定：" + (req.get("name") or req.get("en"))
                    ew = dr.textlength(equip, font=f_small)
                    dr.rectangle([W - PAD - 18 - ew - 20, yy - 3, W - PAD - 18, yy + 29],
                                 fill=(26, 20, 10), outline=BRASS)
                    dr.text((W - PAD - 18 - ew - 10, yy + 1), equip, font=f_small, fill=GOLD2)
                yy += 38
                goal, prog = t.get("goal"), t.get("progress")
                percentage = shared.percent(prog, goal)
                r = (percentage or 0) / 100.0
                done = percentage == 100
                progress_bar(dr, PAD + 18, yy, W - PAD * 2 - 36, 14, r, fill=GREEN if done else GOLD)
                yy += 24
                pt = ("未知" if percentage is None else "已完成" if done else f"{r * 100:.1f}%")
                dr.text((PAD + 18, yy), pt, font=f_h3, fill=GREEN if done else GOLD)
                sub = f"进度 {shared.fmt_big_num(prog)} / 目标 {shared.fmt_big_num(goal)}"
                sw2 = dr.textlength(sub, font=f_small)
                dr.text((W - PAD - 18 - sw2, yy + 6), sub, font=f_small, fill=MUTE)
                yy += 62
            y += panel_h + 18

        # ══ 战区玩家分布 ══
        if dist_hist:
            latest = dist_hist[-1]
            total = latest.get("totalPlayers") or 0
            fac = latest.get("factions") or {}
            rows = [("超级地球", fac.get("Humans"), BLUE),
                    ("终结族", fac.get("Terminids"), (245, 197, 24)),
                    ("机器人", fac.get("Automatons"), (214, 128, 109)),
                    ("光能者", fac.get("Illuminate"), (207, 100, 248))]
            panel_h = 60 + 54 * len(rows) + 44
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 14, "战区玩家分布", f_h2)
            u = (latest.get("timestamp") or "")[:16].replace("T", " ")
            if u:
                uw = dr.textlength(u, font=f_small)
                dr.text((W - PAD - 18 - uw, y + 16), u, font=f_small, fill=MUTE)
            yy = y + 58
            for name, cnt, color in rows:
                measured_pct = shared.percent(cnt,total)
                r = measured_pct or 0
                dr.text((PAD + 18, yy), name, font=f_body, fill=TEXT)
                progress_bar(dr, PAD + 140, yy + 3, W - PAD * 2 - 330, 16, r / 100.0, fill=color)
                vt = (f"{fmt(cnt)}（{r:.1f}%）" if measured_pct is not None else "未知")
                vw = dr.textlength(vt, font=f_body)
                dr.text((W - PAD - 18 - vw, yy), vt, font=f_body, fill=TEXT)
                yy += 54
            total_t = f"总计在线：{fmt(total)} 名指挥官" + (f" · 采样 {u}" if u else "")
            tw2 = dr.textlength(total_t, font=f_small)
            dr.text((W - PAD - 18 - tw2, yy + 2), total_t, font=f_small, fill=DIM)
            y += panel_h + 18

        # ══ 最新要闻 ══
        news_show = [n for n in news if (n.get("translated") or n.get("original") or n.get("message"))][:3]
        y_after_news = None
        if news_show:
            blocks = []
            for n in news_show:
                blocks.append(wrap(n.get("translated") or n.get("original") or n.get("message") or "", 48))
            panel_h = 52 + sum(24 + 24 * len(ls) + 14 for ls in blocks) + 4
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 14, "最新要闻", f_h2)
            yy = y + 52
            for lines in blocks:
                first = True
                for ln in lines:
                    if first:
                        dr.rectangle([PAD + 18, yy + 9, PAD + 24, yy + 25], fill=BRASS)
                        dr.text((PAD + 34, yy), ln, font=f_body, fill=TEXT)
                        first = False
                    else:
                        dr.text((PAD + 34, yy), ln, font=f_body, fill=DIM)
                    yy += 24
                yy += 14
            y_after_news = y + panel_h + 18

        # ══ 页脚 ══
        y = (y_after_news if y_after_news else y) + 8
        dr.line([(PAD, y), (W - PAD, y)], fill=LINE)
        dr.text((PAD, y + 16), "数据来源：主站战况快照 · 源时间见页脚", font=f_tiny, fill=MUTE)
        ft = "发送 /帮助 /dss /战役 /查询 查看更多"
        fw = dr.textlength(ft, font=f_tiny)
        dr.text((W - PAD - fw, y + 16), ft, font=f_tiny, fill=MUTE)
        dr.text((PAD, y + 42), "真理部 · 管理式民主 · 本站与 Arrowhead Game Studios 无隶属关系",
                font=f_tiny, fill=(80, 80, 80))
        return y + 88
    return _render_cached(os.path.join(out_dir, OUT_NAME), "", str(data.get("fetchedAt")), draw)


def render_major_order_card(out_dir=None):
    """/重要指令：当期 MO 独立通知卡，不借用活动战役阶段头图。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        document = _site_get(DATA_URL)
        order = shared.major_order_content(document)
        assignment = document.get("assignments") or {}
        tasks = mo_tasks(document)
        if not order.get("text") and not tasks:
            return None
        out_path = os.path.join(out_dir, "major_order_card.png")
        revision = "mo-card-v1:" + hashlib.sha256(json.dumps({
            "revision": order.get("revision"),
            "text": order.get("text"),
            "tasks": tasks,
            "deadline": assignment.get("deadline"),
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

        def draw(dr, img):
            from PIL import ImageOps
            y = _hud_head(dr, img, "重要指令", "MAJOR ORDER")
            banner_w = W - PAD * 2
            banner_h = 286
            banner_file = next(
                (path for source in order.get("banner_sources") or ()
                 if (path := _banner_file(source))),
                None,
            )
            if banner_file:
                try:
                    source = Image.open(banner_file).convert("RGB")
                    img.paste(ImageOps.fit(source, (banner_w, banner_h)), (PAD, y))
                except Exception:
                    banner_file = None
            if not banner_file:
                for row in range(banner_h):
                    shade = 20 + int(20 * row / max(1, banner_h - 1))
                    dr.line([(PAD, y + row), (W - PAD, y + row)],
                            fill=(shade, shade + 2, shade + 5))
                for offset in range(-banner_h, banner_w, 38):
                    dr.line([(PAD + offset, y + banner_h), (PAD + offset + 150, y)],
                            fill=(49, 42, 24), width=2)
            overlay = Image.new("RGBA", (banner_w, banner_h), (0, 0, 0, 0))
            odr = ImageDraw.Draw(overlay, "RGBA")
            odr.rectangle([0, banner_h - 76, banner_w, banner_h], fill=(0, 0, 0, 188))
            odr.rectangle([0, 0, 9, banner_h], fill=(*GOLD, 235))
            img.paste(overlay, (PAD, y), overlay)
            dr.text((PAD + 26, y + banner_h - 62), "INCOMING MESSAGE FROM SUPER EARTH",
                    font=F(14), fill=GOLD2)
            dr.text((PAD + 26, y + banner_h - 40),
                    "重要指令 · " + str(order.get("revision") or "CURRENT"),
                    font=F(18, True), fill=TEXT)
            y += banner_h + 18

            body_font, body_w = F(21), W - PAD * 2 - 40
            rich_lines = _wrap_rich_runs(order.get("rich_text") or (), body_w, body_font, dr)
            deadline = assignment.get("deadline") or ""
            panel_h = 56 + 31 * len(rich_lines) + 18 + 112 * len(tasks) + 20
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 14, order.get("title") or "重要指令", F(25, True))
            if deadline:
                deadline_text = str(deadline)[:16].replace("T", " ")
                dw = dr.textlength(deadline_text, font=F(15))
                dr.text((W - PAD - 18 - dw, y + 17), "截止 " + deadline_text,
                        font=F(15), fill=GOLD2)
            yy = _draw_rich_lines(
                dr, PAD + 20, y + 54, rich_lines, body_font, 31, color=DIM
            )
            yy += 16
            dr.line([(PAD + 20, yy), (W - PAD - 20, yy)], fill=LINE)
            yy += 12
            for index, task in enumerate(tasks):
                name = task.get("name") or task.get("action") or f"任务 {index + 1}"
                dr.text((PAD + 20, yy), f"任务 {index + 1} · {name}",
                        font=F(20, True), fill=TEXT)
                yy += 34
                percentage = shared.percent(task.get("progress"), task.get("goal"))
                progress_bar(
                    dr, PAD + 20, yy, W - PAD * 2 - 40, 14,
                    (percentage or 0) / 100,
                    fill=GREEN if percentage == 100 else GOLD,
                )
                yy += 22
                progress = f"{percentage:.1f}%" if percentage is not None else "未知"
                if percentage == 100:
                    progress = "已完成"
                dr.text((PAD + 20, yy), progress, font=F(18, True),
                        fill=GREEN if percentage == 100 else GOLD)
                detail = (
                    f"{shared.fmt_big_num(task.get('progress'))} / "
                    f"{shared.fmt_big_num(task.get('goal'))}"
                )
                detail_w = dr.textlength(detail, font=F(16))
                dr.text((W - PAD - 20 - detail_w, yy + 3), detail, font=F(16), fill=MUTE)
                yy += 44
            y += panel_h + 18
            dr.text((PAD, y), "正文来源：当期 NEW MAJOR ORDER 官方中文公告；HTML 已净化。",
                    font=F(14), fill=MUTE)
            return y + 42

        return _render_cached(out_path, "", revision, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


# ═══════════════════════════════════════════════════════════════
# 其他板块图片卡（/战线 · /dss · /查询）——数据一律从主站抓取
# ═══════════════════════════════════════════════════════════════

def _hud_head(dr, img, title, sub_en):
    stripe_band(img, 0, 92, alpha=22)
    dr.rectangle([0, 0, 5, 92], fill=GOLD)
    dr.text((PAD, 14), title, font=F(42, True), fill=GOLD)
    dr.text((PAD + dr.textlength(title, font=F(42, True)) + 24, 36), sub_en, font=F(15), fill=MUTE)
    now = datetime.now(timezone.utc).astimezone()
    ts = now.strftime("%Y-%m-%d %H:%M")
    tw = dr.textlength(ts, font=F(16))
    dr.text((W - PAD - tw, 16), ts, font=F(16), fill=DIM)
    sw = dr.textlength("真理部 · MOT", font=F(16))
    dr.text((W - PAD - sw, 44), "真理部 · MOT", font=F(16), fill=BRASS)
    dr.line([(0, 92), (W, 92)], fill=LINE2)
    return 92 + 16


def _render_cached(out_path, meta_path, rev, draw_fn, page_info=None):
    meta = shared._snapshot.get() or {}
    digest = hashlib.sha256((str(rev) + json.dumps(meta.get("_hd2", {}), sort_keys=True) + "layout-v4" + (str(page_info) if page_info else "")).encode()).hexdigest()[:16]
    stem, _ = os.path.splitext(out_path)
    out_path = stem + "_" + digest + ".png"
    meta_path = out_path + ".meta"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with _render_lock_guard:
        lock = _render_locks.setdefault(out_path, threading.Lock())
    with lock:
        try:
            paths = json.load(open(meta_path, encoding="utf-8"))["pages"]
            if paths and all(os.path.isfile(p) for p in paths):
                return paths[0] if len(paths) == 1 else paths
        except (OSError, ValueError, KeyError):
            pass
        img = Image.new("RGB", (W, TALL), BG)
        y = draw_fn(ImageDraw.Draw(img), img)
        if y + 20 > TALL:
            if y > 60000:
                raise ValueError("内容过长，请使用文字查询或原文链接")
            img = Image.new("RGB", (W, y + 40), BG)
            y = draw_fn(ImageDraw.Draw(img), img)
        paths = save_pages(img, y + 20, out_path, meta, page_info=page_info)
        shared.atomic_json(meta_path, {"pages": paths})
        return paths[0] if len(paths) == 1 else paths


def render_campaigns_card(out_dir=None):
    """/战线 图片卡：全部进行中战役，按在线玩家降序。失败返回 None。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "campaigns_card.png")
        meta_path = out_path + ".meta"
        data = _site_get(DATA_URL)
        rev = "lines:" + str(data.get("fetchedAt") or "")

        def draw(dr, img):
            cs = data.get("campaigns") or []
            planets = {p.get("index"): p for p in (data.get("planets") or []) if p.get("index") is not None}
            smcn = _starmap_cn_map()
            rows = []
            for c in cs:
                cp = c.get("planet") or {}
                pl = planets.get(cp.get("index"))
                if not pl:
                    continue
                en = (pl.get("name") or "").upper()
                cn = smcn.get(en) or cp.get("name") or en
                fac_en = c.get("faction") or pl.get("currentOwner") or ""
                fac = FACTION_CN.get(fac_en, fac_en)
                state = shared.campaign_state(data, pl)
                fac = FACTION_CN.get(state["faction"], str(state["faction"] or "未知"))
                rows.append((cn, pl.get("sector") or "", fac, state["progress"],
                             pl.get("players") or 0, state))
            rows.sort(key=lambda x: -x[4])
            players_sum = sum(r[4] for r in rows)
            top_lib = max((r[3] for r in rows if r[3] is not None), default=0.0)
            y = _hud_head(dr, img, "战线总览", "CAMPAIGN LINES")
            KPI_H = 78
            kpis = [("进行中战线", str(len(rows)) + " 条"),
                    ("最高战役进度", str(round(top_lib, 1)) + "%"),
                    ("战线在线", fmt(players_sum))]
            kw = (W - PAD * 2 - 12 * 2) // 3
            for i, (lab, val) in enumerate(kpis):
                kx = PAD + i * (kw + 12)
                dr.rectangle([kx, y, kx + kw, y + KPI_H], fill=PANEL, outline=LINE)
                dr.rectangle([kx, y, kx + 4, y + KPI_H], fill=BRASS)
                dr.text((kx + 16, y + 9), lab, font=F(15), fill=MUTE)
                dr.text((kx + 16, y + 30), val, font=F(30, True), fill=TEXT)
            y += KPI_H + 18

            panel_h = 52 + 46 * len(rows) + 34
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "全部战线（按在线人数排序）", F(25, True))
            yy = y + 54
            bw_x, bw_w = PAD + 320, W - PAD * 2 - 610
            for cn, sector, fac, ratio, players, state in rows:
                color = FACTION_COLOR.get(fac, GOLD)
                dr.rectangle([PAD + 18, yy + 6, PAD + 22, yy + 30], fill=color)
                dr.text((PAD + 32, yy), cn, font=F(20, True), fill=TEXT)
                sw = dr.textlength(sector, font=F(14))
                dr.text((W - PAD - 18 - sw, yy + 8), sector, font=F(14), fill=MUTE)
                progress_bar(dr, bw_x, yy + 2, bw_w, 12, (ratio or 0) / 100.0,
                             fill=BLUE if state["defending"] else GOLD)
                pt = (str(round(ratio, 1)) + "%") if ratio is not None else "未知"
                dr.text((bw_x + bw_w + 12, yy - 4), pt, font=F(18, True),
                        fill=BLUE if state["defending"] else GOLD)
                pw = dr.textlength(fmt(players) + " 人", font=F(15))
                dr.text((W - PAD - 18 - pw, yy - 2), fmt(players) + " 人", font=F(15), fill=DIM)
                dr.text((PAD + 32, yy + 24), fac + " · " + state["status"] + (" · 截止 " + str(state["deadline"])[:16] if state["defending"] and state["deadline"] else ""), font=F(14), fill=color)
                yy += 46
            dr.text((PAD + 18, y + panel_h - 26),
                    "解放/防御使用对应战役进度 · 数据不足显示未知",
                    font=F(13), fill=(80, 80, 80))
            return y + panel_h + 12

        return _render_cached(out_path, meta_path, rev, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def render_dss_card(out_dir=None):
    """/dss 图片卡：停靠星球 + 生效词条 + 战术行动捐献进度。失败返回 None。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "dss_card.png")
        meta_path = out_path + ".meta"
        data = _site_get(DATA_URL)
        dss = data.get("dss") or {}
        rev = "dss:" + str(data.get("fetchedAt") or "")

        def draw(dr, img):
            planets = {p.get("index"): p for p in (data.get("planets") or []) if p.get("index") is not None}
            pl = planets.get(dss.get("planetIndex")) or {}
            en = (pl.get("name") or "").upper()
            smcn = _starmap_cn_map()
            cn = smcn.get(en) or pl.get("name") or "—"
            emap = _effect_cn_map()
            active_ids = set(dss.get("activeEffectIds") or [])
            cur_war = dss.get("currentWarTime") or 0
            y = _hud_head(dr, img, "民主空间站", "DEMOCRACY SPACE STATION")

            # 停靠星球
            panel_h = 100
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "当前停靠", F(25, True))
            dr.text((PAD + 18, y + 50), cn, font=F(34, True), fill=TEXT)
            sub = en + "  ·  " + (pl.get("sector") or "")
            dr.text((PAD + 18 + dr.textlength(cn, font=F(34, True)) + 18, y + 60),
                    sub, font=F(16), fill=MUTE)
            y += panel_h + 18

            # 被动增益（effect 1238 · 行动支持，口径同主站 tactical_action 分类）
            passive = None
            try:
                v = _site_get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/hd2_variables.json")
                for cat in (v.get("categories") or []):
                    if cat.get("key") != "tactical_action":
                        continue
                    for e in cat.get("entries") or []:
                        if 1238 in (e.get("effect_ids") or []):
                            passive = e.get("cn") or e.get("name") or passive
            except Exception:
                pass

            # 战术行动（照主站三态：已激活 / 冷却中 / 募捐中）
            acts = dss.get("tacticalActions") or []
            if acts:
                blocks = []
                for act in acts:
                    nm_en = act.get("name") or ""
                    nm = DSS_ACTION_SHORT.get(nm_en, nm_en)
                    cost = (act.get("cost") or [{}])[0]
                    cur, tgt = cost.get("current") or 0, cost.get("target") or 0
                    res = ITEM_MIX.get(cost.get("itemMixId"), "资源")
                    md = cost.get("maxDonation")
                    mps = cost.get("maxDonationPeriodSec")
                    quota = ""
                    if isinstance(md, (int, float)) and md > 0 and mps:
                        quota = "每人每 " + str(int(mps // 3600)) + " 小时限捐 " + fmt(md) + " " + res
                    ids = set(act.get("effectIds") or [])
                    shared_phase = shared.dss_phase(dss, act)
                    is_active = shared_phase == "已激活"
                    is_cool = shared_phase == "冷却中"
                    exp = act.get("expireAtWarTime") or 0
                    remain_ms = max(0, (exp - cur_war) * 1000) if (exp and cur_war) else None
                    if is_active:
                        phase = "已激活"
                        bar = min(100.0, max(0.0, remain_ms / (24 * 3600 * 1000) * 100)) if remain_ms is not None else 100.0
                    elif is_cool:
                        phase = "冷却中"
                        bar = min(100.0, max(0.0, remain_ms / (24 * 3600 * 1000) * 100)) if remain_ms is not None else 100.0
                    else:
                        phase = "募捐中"
                        bar = (cur / tgt * 100.0) if tgt else 0.0
                    blocks.append((nm, phase, bar, cur, tgt, res, quota, is_active, is_cool))
                panel_h = 54 + sum(124 for _ in blocks) + 10
                dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
                section_label(dr, PAD + 18, y + 13, "战术行动", F(25, True))
                yy = y + 54
                for nm, phase, bar, cur, tgt, res, quota, is_active, is_cool in blocks:
                    tone = GREEN if is_active else (BLUE if is_cool else GOLD)
                    dr.text((PAD + 18, yy), nm, font=F(23, True), fill=TEXT)
                    lw = dr.textlength(phase, font=F(19, True))
                    dr.text((W - PAD - 18 - lw, yy + 2), phase, font=F(19, True), fill=tone)
                    yy += 38
                    progress_bar(dr, PAD + 18, yy, W - PAD * 2 - 36, 14, bar / 100.0,
                                 fill=GREEN if is_active else (BLUE if is_cool else GOLD))
                    yy += 24
                    if is_active:
                        line = "效果生效中 · 进度条为剩余时间占比"
                    elif is_cool:
                        line = "冷却中 · 结束后重新进入募捐"
                    else:
                        line = "已筹集 " + fmt(cur) + " / 目标 " + fmt(tgt) + " " + res
                        if quota:
                            line += "  ·  " + quota
                    dr.text((PAD + 18, yy), line, font=F(15), fill=DIM)
                    yy += 62
                y += panel_h + 18
            else:
                dr.rectangle([PAD, y, W - PAD, y + 80], fill=PANEL, outline=LINE)
                dr.text((PAD + 18, y + 26), "当前无进行中的战术行动", font=F(19), fill=MUTE)
                y += 98

            # 被动增益
            if passive:
                dr.rectangle([PAD, y, W - PAD, y + 64], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
                dr.text((PAD + 18, y + 14), "被动增益", font=F(19, True), fill=MUTE)
                dr.text((PAD + 18, y + 38), passive, font=F(21, True), fill=TEXT)
                y += 82
            # 未收录效果 id（照实显示，口径与主站一致）
            unknown = sorted(i for i in active_ids if i not in emap and i != 1238)
            if unknown:
                note = "未收录效果 ID：" + "、".join("#" + str(i) for i in unknown) + "（照实显示）"
                nw = dr.textlength(note, font=F(13))
                dr.text((W - PAD - 18 - nw, y - 12), note, font=F(13), fill=(80, 80, 80))
            return y + 10
        return _render_cached(out_path, meta_path, rev, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def _planet_icon_file(en_name):
    """主站星球徽标 assets/planet-icons/<EN>.png → 缓存路径；失败 None。"""
    if not en_name:
        return None
    import re as _re
    fn = _re.sub(r"[^A-Z0-9_]", "", str(en_name).upper().replace(" ", "_")) + ".png"
    cache_file = os.path.join(CACHE_DIR, "planet_icons", fn)
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 200:
        return cache_file
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        url = "https://jerry114514.github.io/HD2-Galatic_war-Map/assets/planet-icons/" + fn
        req = urllib.request.Request(url, headers={"User-Agent": "HD2Bot/1.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            data = r.read()
        if len(data) < 200:
            return None
        open(cache_file, "wb").write(data)
        return cache_file
    except Exception:
        return None


def _biome_index():
    """主站 assets/biomes/index.json（生态名 → 图片文件名，如 Acidic Badlands → Sandy_acid_Landscape.png）；磁盘缓存优先。"""
    cache_file = os.path.join(CACHE_DIR, "biomes", "index.json")
    try:
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 100:
            v = json.load(open(cache_file, encoding="utf-8"))
            if isinstance(v, dict) and v:
                return v
    except Exception:
        pass
    try:
        v = _get("https://jerry114514.github.io/HD2-Galatic_war-Map/assets/biomes/index.json")
        if isinstance(v, dict) and v:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            json.dump(v, open(cache_file, "w", encoding="utf-8"), ensure_ascii=False)
            return v
    except Exception:
        pass
    return None


def _biome_landscape(biome_name):
    """主站环境图（index.json 映射为主，朴素命名兜底）→ 缓存路径；失败 None。"""
    if not biome_name:
        return None
    key = biome_name.strip()
    fn = None
    idx = _biome_index()
    if idx:
        v = idx.get(key)
        if v:
            fn = v if isinstance(v, str) else (v.get("file") or v.get("img") or v.get("image"))
    if not fn:
        fn = key.replace(" ", "_") + "_Landscape.png"
    cache_file = os.path.join(CACHE_DIR, "biomes", fn)
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 500:
        return cache_file
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        url = "https://jerry114514.github.io/HD2-Galatic_war-Map/assets/biomes/" + fn
        req = urllib.request.Request(url, headers={"User-Agent": "HD2Bot/1.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            data = r.read()
        if len(data) < 500:
            return None
        open(cache_file, "wb").write(data)
        return cache_file
    except Exception:
        return None


def _planet_biome_name(pl):
    """biome 名：tables/planet_biomes.json 按英文名查，回退星球自带字段。"""
    if not pl:
        return None
    try:
        v = _site_get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/planet_biomes.json")
        if isinstance(v, dict):
            return v.get((pl.get("name") or "").upper()) or pl.get("biome")
    except Exception:
        pass
    return pl.get("biome")


def _planet_intel():
    """词条链：hd2_variables（cn+分组）→ effect_id_cn 兜底 → {id: (cn, 组名)}"""
    cn, grp = {}, {}
    try:
        v = _site_get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/hd2_variables.json")
        for cat in v.get("categories") or []:
            nm = cat.get("name") or cat.get("key") or ""
            for e in cat.get("entries") or []:
                c = e.get("cn") or e.get("name") or ""
                for eid in e.get("effect_ids") or []:
                    if eid is None:
                        continue
                    if eid not in cn and c:
                        cn[eid] = c
                        grp[eid] = nm
                    elif eid not in grp:
                        grp[eid] = nm
    except Exception:
        pass
    try:
        fb = json.load(open(os.path.join(CACHE_DIR, "effect_id_cn.json"), encoding="utf-8"))
    except Exception:
        try:
            fb = _get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/effect_id_cn.json")
            try:
                json.dump(fb, open(os.path.join(CACHE_DIR, "effect_id_cn.json"), "w", encoding="utf-8"))
            except Exception:
                pass
        except Exception:
            fb = {}
    if isinstance(fb, dict):
        for k, val in fb.items():
            try:
                i = int(k)
            except Exception:
                continue
            if i not in cn and val:
                cn[i] = val
    return cn, grp


def _chips_lines(dr, x0, y0, texts, max_w, line_h=44):
    """chips 断行预排：返回 [(x, y, text, w)] 与结束 y。"""
    rows = []
    cx, cy = x0, y0
    for c in texts:
        cw = dr.textlength(c, font=F(18, True)) + 22
        if cx + cw > max_w:
            cx = x0
            cy += line_h
        rows.append((cx, cy, c, cw))
        cx += cw + 10
    return rows, (cy + 34 if rows else cy)


def render_planet_card(slug, title_cn=None, out_dir=None):
    """星球指挥台图片卡（照主站 planet.html：Hero 环 + Quick Intel + 态势 + 统计 + 词条分组）"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        en = shared.planet_key(slug)
        out_path = os.path.join(out_dir, "planet_" + en.replace(" ", "_") + ".png")
        meta_path = out_path + ".meta"
        data = _site_get(DATA_URL)
        pl = next((x for x in (data.get("planets") or [])
                   if shared.planet_key(x.get("name")) == en), None)
        if not pl:
            return None
        rev = "planet:" + str(data.get("fetchedAt") or "") + ":" + en

        def draw(dr, img):
            smcn = _starmap_cn_map()
            cn_map, grp = _planet_intel()
            owner_en = pl.get("currentOwner") or ""
            owner = FACTION_CN.get(owner_en, owner_en)
            ocolor = FACTION_COLOR.get(owner, GOLD)
            state = shared.campaign_state(data, pl)
            maxh = state["max_health"] or 1
            players = pl.get("players") or 0
            res = state["resistance"] or 0
            impact = data.get("impactMultiplier") or 0
            attacking = state["defending"]
            lib = state["progress"] or 0
            gross = players * impact * 90 / maxh * 100
            net = state["rate"] or 0
            rate_txt = f"{net:+.3f} %/h" if state["rate"] is not None else "未知"
            rate_source = "实测" if state["measured"] else "推演" if state["rate"] is not None else "未知"
            hp_ratio = maxh / 1000000.0
            eq_rate = net * hp_ratio
            heal_h = res / 100 * maxh
            eta_h = (100 - lib) / net if net > .0005 else None
            eta_txt = "—" if eta_h is None else (f"{int(eta_h // 24)} 天 {int(eta_h % 24)} 时" if eta_h <= 720 else "超出 30 天")
            online_all = ((data.get("war") or {}).get("statistics") or {}).get("playerCount") or 0
            share = players / online_all * 100 if online_all else 0
            status_cn = state["status"]
            progress_label = "防御进度" if attacking else "解放进度"
            updated = (data.get("fetchedAt") or "")[:16].replace("T", " ")
            y = _hud_head(dr, img, "星球指挥台", "PLANET COMMAND")

            cn = title_cn or smcn.get(en) or pl.get("name") or en
            # 星区中文名：starmap system 优先，API sector 仅兜底（主站 secCn 口径）
            secmaps = _sector_maps()
            sec_cn = ((secmaps.get("pl_sec") or {}).get((pl.get("name") or en).upper())
                      or (secmaps.get("sec_cn") or {}).get((pl.get("sector") or "").upper())
                      or (pl.get("sector") or "") or "未知星区")
            # ══ Hero：环境图背景 + 遮罩 + 身份 + 解放环 + Quick Intel ══
            hero_h = 330
            bimg = _biome_landscape(_planet_biome_name(pl))
            if bimg:
                try:
                    from PIL import ImageOps
                    img.paste(ImageOps.fit(Image.open(bimg).convert("RGB"), (W - PAD * 2, hero_h)), (PAD, y))
                except Exception:
                    bimg = None
            # 主站式暗化渐变遮罩（.hero-media::after：顶部 .62 → 55% .78 → 底部 .92）
            overlay = Image.new("RGBA", (W - PAD * 2, hero_h), (0, 0, 0, 0))
            odr = ImageDraw.Draw(overlay, "RGBA")

            def _ga(t):
                if t <= 0.55:
                    return int(158 + (199 - 158) * (t / 0.55))
                return int(199 + (235 - 199) * ((t - 0.55) / 0.45))

            for yy in range(hero_h):
                odr.line([(0, yy), (W - PAD * 2, yy)], fill=(6, 6, 6, _ga(yy / max(1, hero_h - 1))))
            odr.rectangle([0, 0, 3, hero_h], fill=GOLD + (255,))
            img.paste(overlay, (PAD, y), overlay)
            dr.rectangle([PAD, y, W - PAD, y + hero_h], outline=LINE2)
            ix = y + 14
            picon = _planet_icon_file(pl.get("name"))
            if not bimg and picon:
                # 主站 .no-bio 回退：星球图标作居中大水印（50% 不透明度）
                try:
                    ic = Image.open(picon).convert("RGBA")
                    ih = int(hero_h * 0.72)
                    iw = int(ic.width * ih / max(1, ic.height))
                    if iw > W - PAD * 2 - 40:
                        iw = W - PAD * 2 - 40
                        ih = int(ic.height * iw / max(1, ic.width))
                    ic = ic.resize((max(1, iw), max(1, ih)))
                    ic.putalpha(ic.getchannel("A").point(lambda v: v * 50 // 100))
                    img.paste(ic, (PAD + (W - PAD * 2 - ic.width) // 2, y + (hero_h - ic.height) // 2), ic)
                except Exception:
                    pass
            if picon:
                try:
                    paste_contain(img, Image.open(picon).convert("RGBA"), (PAD + 18, ix, 56, 56))
                except Exception:
                    pass
            dr.text((PAD + 86, ix + 2), "Planet Command · 星球指挥台", font=F(14), fill=GOLD)
            dr.text((PAD + 86, ix + 24), cn, font=F(42, True), fill=TEXT)
            dr.text((PAD + 88, ix + 78), (pl.get("name") or en) + " · " + sec_cn + "星区",
                    font=F(15), fill=DIM)
            cx, cyy = PAD + 18, ix + 116
            for txt, col in ((status_cn, ocolor), ("◆ " + owner, ocolor)):
                cw = dr.textlength(txt, font=F(15, True)) + 20
                dr.rectangle([cx, cyy, cx + cw, cyy + 30], fill=(16, 18, 22, 220), outline=col)
                dr.text((cx + 10, cyy + 5), txt, font=F(15, True), fill=TEXT)
                cx += cw + 10
            # 解放进度环（主站 168px 口径缩放为 150）
            import math
            ring_x, ring_y, RR, SZ = PAD + 500, y + 36, 60, 150
            sw_c = 11
            dr.arc([ring_x, ring_y, ring_x + SZ, ring_y + SZ], 0, 360, fill=(28, 28, 28), width=sw_c)
            ring_col = GREEN if lib >= 99.9 else ocolor
            if lib > 0.05:
                dr.arc([ring_x, ring_y, ring_x + SZ, ring_y + SZ], -90,
                       -90 + 360 * min(100, lib) / 100, fill=ring_col, width=sw_c)
            ltxt = f"{lib:.1f}%" if state["progress"] is not None else "未知"
            lw = dr.textlength(ltxt, font=F(28, True))
            dr.text((ring_x + (SZ - lw) / 2, ring_y + SZ / 2 - 26), ltxt, font=F(28, True), fill=TEXT)
            lbl = progress_label
            lbw = dr.textlength(lbl, font=F(13))
            dr.text((ring_x + (SZ - lbw) / 2, ring_y + SZ / 2 + 10), lbl, font=F(13), fill=DIM)
            hpv = f"{fmt(state['health'] or 0)} / {fmt(maxh)} · ×{hp_ratio:.2f}"
            hw = dr.textlength(hpv, font=F(12))
            dr.text((ring_x + (SZ - hw) / 2, ring_y + SZ + 4), hpv, font=F(12), fill=MUTE)
            # Quick Intel（右列 6 行）
            qx = PAD + 700
            qy = y + 14
            qis = [("在线玩家", f"{fmt(players)}（占全服 {share:.2f}%）"),
                   (progress_label, f"{lib:.2f}%" if state["progress"] is not None else "未知"),
                   ("血量倍数", f"×{hp_ratio:.2f}（100 万血基准）"),
                   ("净速率", rate_txt + "（" + rate_source + "）"),
                   ("当前生态", _planet_biome_name(pl) or "—"),
                   ("防御截止" if attacking else "预计解放", str(state["deadline"] or "未知")[:16] if attacking else eta_txt)]
            for k, v in qis:
                dr.text((qx, qy), k, font=F(14), fill=MUTE)
                vw = dr.textlength(v, font=F(17, True))
                dr.text((W - PAD - 18 - vw, qy - 2), v, font=F(17, True), fill=TEXT)
                qy += 32
            # 细粒度进度条
            sy = y + hero_h - 14
            progress_bar(dr, PAD + 2, sy, W - PAD * 2 - 4, 8, lib / 100.0,
                         fill=GREEN if lib >= 99.9 else ocolor)
            y += hero_h + 18

            # ══ 战略态势 ══
            panel_h = 204
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "战略态势", F(23, True))
            situation = [(progress_label, f"{lib:.2f}%" if state["progress"] is not None else "未知", f"血量 {fmt(state['health'] or 0)} / {fmt(maxh)}"),
                         ("净速率", rate_txt, rate_source),
                         ("每小时回血", f"{heal_h:,.0f} HP/h", "抵抗度% × 血量"),
                         ("等效推进（基准球）", ("+" if eq_rate >= 0 else "") + f"{eq_rate:.2f} %/h", f"血量为基准球的 {hp_ratio:.2f} 倍"),
                         ("预计防御完成" if attacking else "预计解放", eta_txt, "推演时间，不代表能在防御截止前完成" if attacking else "净速率 ≤ 0 时无法推演"),
                         ("敌方抵抗", f"{res:.2f} %/h" if state["resistance"] is not None else "未知", "解放战的回血能力；防御使用事件数据")]
            colw = (W - PAD * 2 - 36) // 2
            for i, (k, v, note) in enumerate(situation):
                kx = PAD + 18 + (i % 2) * (colw + 18)
                ky = y + 46 + (i // 2) * 46
                dr.text((kx, ky), k, font=F(15), fill=MUTE)
                vw = dr.textlength(v, font=F(18, True))
                dr.text((kx + colw - 40 - vw, ky - 3), v, font=F(18, True), fill=TEXT)
                nw = dr.textlength(note, font=F(13))
                dr.text((kx + colw - 40 - nw, ky + 20), note, font=F(13), fill=(90, 90, 90))
            y += panel_h + 18

            # ══ 星球统计（war_stats.planets_stats） ══
            stats = None
            for row in ((data.get("war_stats") or {}).get("planets_stats")) or []:
                if isinstance(row, dict) and row.get("planetIndex") == pl.get("index"):
                    stats = row
                    break
            panel_h = 216
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "星球统计（本场战争累计）", F(23, True))
            if stats:
                won, lost = stats.get("missionsWon") or 0, stats.get("missionsLost") or 0
                tot = won + lost
                rate = (won / tot * 100) if tot else 0
                shots, hits = stats.get("bulletsFired") or 0, stats.get("bulletsHit") or 0
                kills = [("终结族", stats.get("bugKills") or 0), ("机器人", stats.get("automatonKills") or 0),
                         ("光能者", stats.get("illuminateKills") or 0)]
                owner_key = str(owner_en or "").lower()
                key_map = {"terminids": "终结族", "automaton": "机器人", "illuminate": "光能者"}
                main_kill = next((k for k in kills if key_map.get(owner_key) == k[0]), None) \
                    or max(kills, key=lambda k: k[1])
                others = [k for k in kills if k is not main_kill and k[1] > 0]
                rows = [("任务完成", fmt(won), f"成功率 {rate:.1f}%" if tot else "—"),
                        ("任务失败", fmt(lost), f"占 {100 - rate:.1f}%" if tot else "—"),
                        ("开火", fmt(shots), "累计发数"),
                        ("命中", fmt(hits), (f"命中/开火 {hits / shots * 100:.1f}%" if shots else "—")),
                        (main_kill[0] + "击杀", fmt(main_kill[1]),
                         "当前控制方" if owner_key else "历史主要交战阵营"),
                        ("阵亡绝地潜兵", fmt(stats.get("deaths") or 0),
                         "其他历史 " + " · ".join(k[0] + " " + fmt(k[1]) for k in others) if others else "")]
                for i, (k, v, s2) in enumerate(rows):
                    kx = PAD + 18 + (i % 2) * (colw + 18)
                    ky = y + 48 + (i // 2) * 40
                    hero_v = "击杀" in k
                    dr.text((kx, ky), k, font=F(15), fill=MUTE)
                    vw = dr.textlength(v, font=F(21, True))
                    dr.text((kx + colw - 40 - vw, ky - 3), v, font=F(21, True),
                            fill=GOLD if hero_v else TEXT)
                    if s2:
                        sw2 = dr.textlength(s2, font=F(13))
                        dr.text((kx + colw - 44 - vw - sw2, ky + 1), s2, font=F(13),
                                fill=(90, 90, 90))
            else:
                dr.text((PAD + 18, y + 52),
                        "该星球暂无累计统计（官方 war_stats 里没有这一条；通常是从未被打过的星球）。",
                        font=F(17), fill=DIM)
            y += panel_h + 18

            # ══ 下属区域（城市 / 超大型工厂；数据与口径照主站 sec-regions） ══
            rt_map, rzh_map = _region_tables()
            rst = rt_map.get(str(pl.get("index"))) or {}
            rst_regions = rst.get("regions") or []
            if rst_regions:
                rzh = rzh_map.get(str(pl.get("index"))) or {}
                live_regions = pl.get("regions") or []

                def _live(ridx):
                    for r in live_regions:
                        if r.get("regionIndex") == ridx:
                            return r
                    if ridx < len(live_regions) and live_regions[ridx].get("regionIndex") is None:
                        return live_regions[ridx]
                    return None

                ours_pl = str(pl.get("currentOwner") or "").lower() == "humans"
                rows_r = []
                for k, rg in enumerate(rst_regions):
                    lv = _live(k) or {}
                    flags = lv.get("flags") or 0
                    factory = str(rg.get("region_type") or "").lower() == "megafactory" or (flags & 1)
                    size = rg.get("size") or ""
                    if factory:
                        type_cn, stars = "超大型工厂", 4
                    else:
                        type_cn = _REGION_TYPE_ZH.get(size, size or "区域")
                        stars = _REGION_STARS.get(size, 0)
                    p = None
                    if lv.get("maxHealth"):
                        p = max(0.0, min(100.0, (1 - (lv.get("health") or 0) / lv["maxHealth"]) * 100))
                    if p is None and ours_pl:
                        p = 100.0                      # 己方星球官方不下发 regions → 按已解放（主站口径）
                    if lv.get("owner"):
                        owner_cn = FACTION_CN.get(lv["owner"], lv["owner"])
                    elif ours_pl:
                        owner_cn = "超级地球"          # 静态表 owner 是抓取当时的阵营，己方一律记超级地球
                    else:
                        owner_cn = FACTION_CN.get(rg.get("owner") or "", rg.get("owner") or "—")
                    rate = _region_rate(pl, lv, impact)
                    if rate:
                        net, _res, _measured = rate
                        if p is None or net is None:
                            rate_txt, eta_r = "—", "—"
                        else:
                            left = max(0.0, 100.0 - p)
                            if net > 0.0005:
                                h = left / net
                                eta_r = ("%d 小时" % round(h)) if h <= 24 else (
                                    "%d 天 %d 时" % (int(h // 24), int(h % 24)) if h <= 720 else "超过 30 天")
                            else:
                                eta_r = "不会解放"
                            rate_txt = ("+" if net >= 0 else "−") + "%.2f%%/h · %s" % (abs(net), eta_r)
                    else:
                        rate_txt = "—"
                    en_name = rg.get("name") or ""
                    zh = rzh.get(en_name) or ""
                    rows_r.append((zh or en_name or "区域", en_name, owner_cn, type_cn, stars,
                                   lv.get("players"), p, rate_txt))
                if rows_r:
                    panel_h = 50 + 24 + 34 * len(rows_r) + 10
                    dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                    dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
                    section_label(dr, PAD + 18, y + 13, "下属区域（城市 / 超大型工厂）", F(23, True))
                    hy = y + 50
                    c_owner, c_plr, c_lib, c_rate = PAD + 330, PAD + 700, PAD + 830, W - PAD - 14
                    for lbl, rx, ralign in (("区域", PAD + 14, 0), ("归属 · 类型", c_owner, 0),
                                            ("玩家", c_plr, 1), ("已解放", c_lib, 1), ("推进 · 预计", c_rate, 1)):
                        lw = dr.textlength(lbl, font=F(13))
                        dr.text(((rx - lw) if ralign else rx, hy), lbl, font=F(13), fill=MUTE)
                    for i, (nm, en_nm, owner_cn, type_cn, stars, players, p, rate_txt) in enumerate(rows_r):
                        ry = hy + 24 + i * 34
                        dr.line([(PAD + 10, ry), (W - PAD - 10, ry)], fill=LINE)
                        dr.text((PAD + 14, ry + 7), nm, font=F(17, True), fill=TEXT)
                        if en_nm and en_nm != nm:
                            dr.text((PAD + 14 + dr.textlength(nm, font=F(17, True)) + 10, ry + 11),
                                    en_nm, font=F(11), fill=(80, 80, 80))
                        dr.text((c_owner, ry + 9), owner_cn + " · " + type_cn + "★" * stars,
                                font=F(14), fill=DIM)
                        pw = dr.textlength(fmt(players) if players else "—", font=F(16, True))
                        dr.text((c_plr - pw, ry + 8), fmt(players) if players else "—", font=F(16, True), fill=TEXT)
                        lib_s = "—" if p is None else "%.2f%%" % p
                        lw2 = dr.textlength(lib_s, font=F(16, True))
                        dr.text((c_lib - lw2, ry + 8), lib_s, font=F(16, True),
                                fill=GREEN if p == 100 else TEXT)
                        rw = dr.textlength(rate_txt, font=F(15))
                        dr.text((c_rate - rw, ry + 9), rate_txt, font=F(15),
                                fill=(80, 80, 80) if rate_txt == "—" else DIM)
                    y += panel_h + 18

            # ══ 环境与作战词条（分组照主站：变种类 + 作战限制 保留组，其余平铺） ══
            eids = [i for i in (pl.get("activeEffects") or []) if i is not None]
            seen, items = set(), []
            for eid in eids:
                c = cn_map.get(eid) or ("#" + str(eid))
                if c in seen:                    # 多 id 同词条 → 链上第一个命中（主站口径）
                    continue
                seen.add(c)
                items.append((c, grp.get(eid) or ""))
            var_items = [x for x in items if "变种" in x[1]]
            limit_items = [x for x in items if x[1] == "作战限制"]
            flat_items = [x for x in items if x not in var_items and x not in limit_items]
            max_w = W - PAD - 18 - (PAD + 18)
            yy = y + 54
            groups_layout = []
            if var_items:
                gy = yy; yy += 26
                rows_l, end_y = _chips_lines(dr, PAD + 18 + 64, yy, [x[0] for x in var_items], max_w - 64)
                groups_layout.append(("var", gy, rows_l))
                yy = end_y + 16
            if limit_items:
                gy = yy; yy += 26
                rows_l, end_y = _chips_lines(dr, PAD + 18, yy, [x[0] for x in limit_items], max_w)
                groups_layout.append(("limit", gy, rows_l))
                yy = end_y + 16
            if flat_items:
                rows_l, end_y = _chips_lines(dr, PAD + 18, yy, [x[0] for x in flat_items], max_w)
                groups_layout.append(("flat", gy if (var_items or limit_items) else yy, rows_l))
                yy = end_y + 8
            panel_h = max(120, (yy - y) + 6)
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "环境与作战词条", F(23, True))
            for gk, gy, rows_l in groups_layout:
                if gk == "var":
                    dr.text((PAD + 18, gy), "变种", font=F(15, True), fill=BRASS)
                if gk == "limit":
                    dr.text((PAD + 18, gy), "作战限制", font=F(15, True), fill=BRASS)
                for cx, cy2, c, cw in rows_l:
                    dr.rectangle([cx, cy2, cx + cw, cy2 + 34], fill=(26, 20, 10), outline=BRASS)
                    dr.text((cx + 11, cy2 + 4), c, font=F(18, True), fill=GOLD2)
            if not items:
                dr.text((PAD + 18, y + 54), "当前无环境词条", font=F(19), fill=MUTE)
            y += panel_h + 18
            return y

        return _render_cached(out_path, meta_path, rev, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


# ═══════════════════════════════════════════════════════════════
# /api API 变动监测卡（来源：主仓 Issue #60 · CI 自动刷新）
# ═══════════════════════════════════════════════════════════════

ISSUE_URL = api_watch.ISSUE_URL


def _fetch_issue60():
    active = shared._snapshot.get() or {}
    if "_issue_body" in active:
        return active["_issue_body"]
    return api_watch.fetch_issue60(ttl=api_watch.DEFAULT_CACHE_SECONDS)["body"]


def _parse_issue60(body):
    """解析 Issue #60 正文 → {detected, effect_count, changed, groups}。
    只解析分隔线之前的最新状态（--- 之后是手动重放的版式示例）。"""
    return api_watch.parse_issue60(body)


def render_api_card(out_dir=None):
    """/api 图片卡：主仓 Issue #60 的 API 变动监测。失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        body = _fetch_issue60()
        info = _parse_issue60(body)
        out_path = os.path.join(out_dir, "api_card.png")
        meta_path = out_path + ".meta"
        rev = hashlib.sha256(body.encode("utf-8")).hexdigest()

        def draw(dr, img):
            changed = not info["no_change"]
            tone = GOLD if changed else GREEN
            y = _hud_head(dr, img, "API 变动监测", "API CHANGE WATCH")

            # 状态横条
            panel_h = 92
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=tone)
            st = ("▲ 检测到 " + str(len(info["groups"])) + " 组变更") if changed else "✔ 基线一致 · 无新增 / 移除 / 定义变更"
            dr.text((PAD + 18, y + 16), st, font=F(28, True), fill=tone)
            dt = info.get("detected") or "—"
            dr.text((PAD + 18, y + 58), "检测时间（UTC）：" + dt, font=F(15), fill=DIM)
            try:
                du = datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                local = du.astimezone().strftime("%Y-%m-%d %H:%M")
                lw = dr.textlength("仰齐浜时间：" + local, font=F(15))
                dr.text((W - PAD - 18 - lw, y + 58), "仰齐浜时间：" + local, font=F(15), fill=DIM)
            except Exception:
                pass
            y += panel_h + 18

            # 变更分组
            groups = info["groups"]
            if groups:
                total_rows = sum(24 + 26 * (len(g["added"]) + len(g["removed"]) + 1) + 14
                                 for g in groups)
                panel_h = 52 + total_rows + 12
                dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, W - PAD, y + 3], fill=tone)
                section_label(dr, PAD + 18, y + 13, "变更明细", F(23, True))
                yy = y + 54
                for g in groups:
                    dr.text((PAD + 18, yy), "▎" + g["name"], font=F(21, True), fill=TEXT)
                    yy += 30
                    for kind, mark, col in (("added", "+ 新增", GREEN), ("removed", "- 移除", RED)):
                        for eid, cn in g[kind]:
                            dr.text((PAD + 40, yy), mark, font=F(16, True), fill=col)
                            dr.text((PAD + 128, yy), "#" + eid + " — " + cn,
                                    font=F(18, True), fill=TEXT)
                            yy += 26
                    yy += 14
            else:
                panel_h = 84
                dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                dr.text((PAD + 18, y + 26), "对照表与上一轮一致，无需行动。",
                        font=F(19), fill=DIM)
            y += panel_h + 18

            # 效果定义规模
            dr.text((PAD, y), "当前银河效果定义：" + str(info.get("effect_count") or "—") + " 条",
                    font=F(16), fill=DIM)
            y += 30

            # 页脚
            dr.line([(PAD, y), (W - PAD, y)], fill=LINE)
            dr.text((PAD, y + 14), "来源：主仓 Issue #60（机器人每 60 秒检查，CI 自动刷新）",
                    font=F(14), fill=MUTE)
            ft = "明细与历史：github.com/Jerry114514/Jerry114514.github.io/issues/60"
            fw = dr.textlength(ft, font=F(14))
            dr.text((W - PAD - fw, y + 14), ft, font=F(14), fill=MUTE)
            dr.text((PAD, y + 40), "对照表口径：官方 API 结构（字段名 + 类型）比对，基线随 CI 更新",
                    font=F(14), fill=(80, 80, 80))
            dr.text((PAD, y + 64), "本会话已登记自动监测；每 60 秒检查一次，内容变化时主动推送。",
                    font=F(14), fill=GOLD2)
            return y + 104

        return _render_cached(out_path, meta_path, rev, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def _banner_file(rel):
    """Campaign/MO image → local cache; accepts site-relative paths and full URLs."""
    if not rel:
        return None
    source = str(rel).replace("\\", "/")
    name = os.path.basename(source)
    if not name:
        return None
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "campaign", name)
    if os.path.isfile(bundled) and os.path.getsize(bundled) > 0:
        return bundled
    if source.startswith(("http://", "https://")):
        cache_name = hashlib.sha256(source.encode()).hexdigest()[:10] + "_" + name
        url = source
    else:
        cache_name = name
        url = shared.site_url(source.lstrip("./"))
    cache_file = os.path.join(CACHE_DIR, "banners", cache_name)
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
        return cache_file
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "HD2Bot/1.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            blob = r.read()
        if not blob or not blob.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
            raise ValueError("图片响应格式无效")
        with open(cache_file, "wb") as f:
            f.write(blob)
        return cache_file
    except Exception:
        return None


def campaign_grid_columns(count):
    return max(1, min(3, int(count or 0)))


def _campaign_image_file(sources):
    for source in sources or ():
        path = _banner_file(source)
        if path:
            return path
    return None


def _crop_cover(path, size):
    from PIL import ImageOps
    if not path:
        return None
    try:
        return ImageOps.fit(Image.open(path).convert("RGB"), size)
    except Exception:
        return None


def _campaign_placeholder(dr, box, faction):
    x0, y0, x1, y1 = box
    palette = {
        "Terminids": ((55, 48, 12), (24, 20, 10)),
        "Automaton": ((58, 29, 24), (22, 15, 14)),
        "Illuminate": ((45, 26, 61), (20, 15, 29)),
    }.get(faction, ((24, 39, 57), (14, 20, 28)))
    height = max(1, y1 - y0)
    for row in range(height):
        ratio = row / height
        color = tuple(int(start + (end - start) * ratio) for start, end in zip(palette[0], palette[1]))
        dr.line([(x0, y0 + row), (x1, y0 + row)], fill=color)
    for offset in range(x0 - height, x1, 42):
        dr.line([(offset, y1), (offset + height, y0)], fill=(48, 43, 31), width=2)
    dr.rectangle(box, outline=LINE2)
    dr.text((x0 + 18, y0 + 16), "ARCHIVE IMAGE / SIGNAL LOST", font=F(12), fill=MUTE)


def _phase_color(status_key):
    return {
        "success": GREEN,
        "failure": RED,
        "in_progress": GOLD,
    }.get(status_key, MUTE)


def render_briefing_card(out_dir=None):
    """/战役：档案头图 + 全阶段简报/战报墙，所有阶段一次完整展示。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "briefing_card.png")
        document = _site_get(DATA_URL)
        campaign = shared.campaign_view(document)
        title = campaign.get("title_cn") or campaign.get("title") or ""
        description = (campaign.get("description_cn") or campaign.get("description")
                       or campaign.get("briefing_cn") or campaign.get("briefing") or "")
        phases = campaign.get("phases") or []
        if not (title or description) or not phases:
            return None
        current_index = campaign.get("current_phase_index")
        current_phase = phases[current_index] if isinstance(current_index, int) and 0 <= current_index < len(phases) else {}
        revision = "brief-wall-v2:" + hashlib.sha256(
            json.dumps(campaign, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

        def draw(dr, img):
            y = _hud_head(dr, img, "战役档案", "CAMPAIGN ARCHIVE")
            content_w = W - PAD * 2
            gap = 18
            left_w = int(content_w * 0.58)
            image_x = PAD + left_w + gap
            image_w = W - PAD - image_x
            image_h = 264
            desc_font = F(18)
            desc_w = left_w - 36
            desc_lines = []
            for line in wrap(description, max(18, int(desc_w / 18))):
                desc_lines.append(line)
            overall = {
                "in_progress": "进行中 / IN PROGRESS",
                "victory": "战役获胜 / VICTORY",
                "defeat": "战役失败 / DEFEAT",
            }.get(campaign.get("overall_status"), "进行中 / IN PROGRESS")
            desc_offset = 146 if campaign.get("title_cn") and campaign.get("title") else 122
            reward_offset = desc_offset + len(desc_lines) * 27 + 14
            overview_h = max(image_h, reward_offset + 42)
            dr.rectangle([PAD, y, W - PAD, y + overview_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, PAD + 5, y + overview_h], fill=GOLD)
            dr.text((PAD + 22, y + 16), "ACTIVE CAMPAIGN / 战役档案",
                    font=F(13, True), fill=GOLD)
            title_cn = campaign.get("title_cn") or campaign.get("title") or ""
            title_en = campaign.get("title") if campaign.get("title_cn") else ""
            dr.text((PAD + 22, y + 44), title_cn, font=F(34, True), fill=TEXT)
            if title_en:
                dr.text((PAD + 22, y + 84), title_en, font=F(17, True), fill=GOLD2)
            meta_y = y + (116 if title_en else 92)
            meta = " · ".join(value for value in (
                campaign.get("race_cn") or campaign.get("race_name"),
                overall,
                f"{len(phases)} 个阶段",
            ) if value)
            dr.text((PAD + 22, meta_y), meta, font=F(15), fill=DIM)
            yy = y + desc_offset
            for line in desc_lines:
                dr.text((PAD + 22, yy), line, font=desc_font, fill=TEXT)
                yy += 27
            dr.text((PAD + 22, y + reward_offset), "战役奖励",
                    font=F(14, True), fill=MUTE)
            dr.text((PAD + 94, y + reward_offset - 2),
                    "🏅 " + _money_amount(campaign.get("reward")),
                    font=F(18, True), fill=GOLD)
            image_box = (image_x, y + 12, W - PAD - 12, y + overview_h - 12)
            image_path = _campaign_image_file(current_phase.get("image_sources"))
            image = _crop_cover(image_path, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
            if image:
                img.paste(image, (image_box[0], image_box[1]))
            else:
                _campaign_placeholder(dr, image_box, campaign.get("race_name"))
            overlay = Image.new("RGBA", (
                image_box[2] - image_box[0], image_box[3] - image_box[1]
            ), (0, 0, 0, 0))
            odr = ImageDraw.Draw(overlay, "RGBA")
            odr.rectangle([0, overlay.height - 54, overlay.width, overlay.height],
                          fill=(0, 0, 0, 196))
            img.paste(overlay, (image_box[0], image_box[1]), overlay)
            dr.text((image_box[0] + 16, image_box[3] - 44),
                    (current_phase.get("title_cn") or current_phase.get("title") or title_cn)
                    + " / CURRENT PHASE",
                    font=F(15, True), fill=TEXT)
            y += overview_h + 18

            header_h = 50
            dr.rectangle([PAD, y, W - PAD, y + header_h], fill=(25, 25, 25), outline=LINE2)
            dr.text((PAD + 18, y + 13), "战役章节", font=F(22, True), fill=TEXT)
            caption = f"{len(phases)} 个阶段全部展示"
            caption_w = dr.textlength(caption, font=F(14))
            dr.text((W - PAD - 18 - caption_w, y + 17), caption, font=F(14), fill=MUTE)
            y += header_h + 10

            columns = campaign_grid_columns(len(phases))
            card_gap = 16
            card_w = (content_w - card_gap * (columns - 1)) // columns
            phase_font = F(14)
            report_font = F(14)
            line_chars = max(11, int((card_w - 30) / 15))
            media_height = {1: 320, 2: 205, 3: 138}[columns]
            phase_text = []
            for phase in phases:
                brief_lines = wrap(phase.get("briefing_cn") or phase.get("briefing") or "（该阶段暂无简报）",
                                   line_chars)
                report_lines = wrap(phase.get("outcome") or "暂无可用战报。", line_chars)
                body_height = 36 + len(brief_lines) * 22 + 14 + 36 + len(report_lines) * 22 + 18
                phase_text.append((brief_lines, report_lines, body_height))
            body_height = max(row[2] for row in phase_text)
            card_h = 46 + media_height + body_height

            for index, (phase, text_rows) in enumerate(zip(phases, phase_text)):
                x = PAD + index * (card_w + card_gap)
                is_current = index == current_index
                status_key = phase.get("status_key") or "unknown"
                status_color = _phase_color(status_key)
                border = GOLD if is_current else LINE2
                dr.rectangle([x, y, x + card_w, y + card_h], fill=PANEL, outline=border, width=2 if is_current else 1)
                dr.rectangle([x, y, x + card_w, y + 46], fill=(24, 24, 24), outline=LINE)
                dr.text((x + 12, y + 8), f"阶段 {index + 1:02d}", font=F(16, True), fill=TEXT)
                code = f"PHASE {index + 1:02d}"
                dr.text((x + 12, y + 27), code, font=F(11), fill=MUTE)
                status = phase.get("status_cn") or "未知"
                status_w = dr.textlength(status, font=F(13, True))
                dr.text((x + card_w - 10 - status_w, y + 14), status,
                        font=F(13, True), fill=status_color)
                if is_current:
                    dr.rectangle([x + card_w - 48, y + 2, x + card_w - 2, y + 7], fill=GOLD)

                media_box = (x + 1, y + 47, x + card_w - 1, y + 46 + media_height)
                image_path = _campaign_image_file(phase.get("image_sources"))
                phase_image = _crop_cover(
                    image_path,
                    (media_box[2] - media_box[0], media_box[3] - media_box[1]),
                )
                if phase_image:
                    img.paste(phase_image, (media_box[0], media_box[1]))
                else:
                    _campaign_placeholder(dr, media_box, campaign.get("race_name"))
                overlay_h = 78
                phase_overlay = Image.new(
                    "RGBA",
                    (media_box[2] - media_box[0], media_box[3] - media_box[1]),
                    (0, 0, 0, 0),
                )
                pdr = ImageDraw.Draw(phase_overlay, "RGBA")
                pdr.rectangle([0, phase_overlay.height - overlay_h,
                               phase_overlay.width, phase_overlay.height],
                              fill=(0, 0, 0, 205))
                pdr.rectangle([0, phase_overlay.height - overlay_h,
                               5, phase_overlay.height],
                              fill=(*status_color, 232))
                img.paste(phase_overlay, (media_box[0], media_box[1]), phase_overlay)
                overlay_y = media_box[3] - overlay_h + 10
                phase_title = phase.get("title_cn") or phase.get("title") or "未命名阶段"
                dr.text((x + 13, overlay_y), phase_title,
                        font=F(20 if columns < 3 else 17, True), fill=TEXT)
                phase_en = phase.get("title") if phase.get("title_cn") else ""
                if phase_en:
                    dr.text((x + 13, overlay_y + 24), phase_en,
                            font=F(11), fill=GOLD2)
                result_y = media_box[3] - 33
                dr.text((x + 13, result_y),
                        f"结果 · {status}" + (f" / {phase.get('status_en')}" if phase.get("status_en") else ""),
                        font=F(11, True), fill=status_color)
                reward = phase.get("reward") or {}
                reward_text = _money_amount(reward)
                if not reward:
                    reward_text = "无阶段奖励"
                rw = dr.textlength(reward_text, font=F(11))
                dr.text((x + card_w - 10 - rw, result_y), reward_text,
                        font=F(11), fill=GOLD2)

                body_y = media_box[3] + 12
                dr.text((x + 12, body_y), "简报", font=F(13, True), fill=GOLD)
                body_y += 24
                for line in text_rows[0]:
                    dr.text((x + 12, body_y), line, font=phase_font, fill=TEXT)
                    body_y += 22
                body_y += 8
                dr.line([(x + 12, body_y), (x + card_w - 12, body_y)], fill=LINE)
                body_y += 10
                dr.text((x + 12, body_y), "战报", font=F(13, True), fill=status_color)
                body_y += 24
                for line in text_rows[1]:
                    dr.text((x + 12, body_y), line, font=report_font, fill=DIM)
                    body_y += 22
            y += card_h + 18
            dr.text((PAD, y), "文案：活动战役中文层优先，缺失时按战役/阶段 ID 回退 campaign_zh.json。",
                    font=F(13), fill=MUTE)
            return y + 40

        return _render_cached(out_path, "", revision, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(CACHE_DIR, OUT_NAME)
    path = render_report_card(os.path.dirname(os.path.abspath(out)) or CACHE_DIR)
    print("rendered:", path)


# ============ /帮助 作战手册卡（文案与各插件实际指令同步维护） ============

HELP_SECTIONS = [
    ("战况速报", "INTEL", [
        ("/战报", "银河战况速报卡：当期 MO、目标星球、四阵营兵力分布"),
        ("/重要指令", "独立 MO 通知卡：官方中文全文、黄色重点、任务进度与独立横幅"),
        ("/战线", "全银河战线列表：各战区解放/防御进度与交战双方"),
        ("/dss", "民主空间站：停靠星球、生效行动效果、投票与捐献进度"),
        ("/战役", "活动战役档案墙：头图、全部阶段简报/战报、结果与奖励"),
        ("/api", "每分钟监测主仓 Issue；当前会话在内容变化时自动接收，支持手动重播"),
    ]),
    ("星球情报", "PLANET INTEL", [
        ("/查询 星球名", "星球指挥台卡：解放环、兵力趋势、区域战况、环境词条"
                       "（中英文均可；/星球 名 同效）"),
        ("/银河星图", "快捷指令直达线上中文星图（浏览器打开：星球战况与补给线）"),
    ]),
    ("配装与变种", "LOADOUT & VARIANTS", [
        ("/roll", "整套七槽配装：主手/副手/手雷 + 四战备，只显示结果与装备图标"),
        ("/变种名", "该变种当前出没星球（如 /孢裂变种）"),
    ]),
    ("伤害测试", "BALLISTICS", [
        ("/btk 武器 敌人 部位", "伤害 HUD 卡：BTK/TTK、打残/致死和卡阈值对比"
                          "（如 /btk AR-23 武斗虫 头部）"),
        ("/btk 卡阈值 武器", "阈值矩阵卡：扫描哪些敌人致死部位接近少一发击杀"),
        ("/btk DOT-FIRE 敌人", "持续伤害卡：每跳伤害、触发次数、总时长与元素倍率（无需部位）"),
        ("/btk 武器 / 敌人", "查看可测武器分类、敌人阵营和部位数据"),
    ]),
    ("AI 增强", "AUGMENT", [
        ("/分析", "AI 战局分析：威胁评估与目标建议（需配置 LLM，失败时明确提示）"),
    ]),
    ("资料库", "WIKI", [
        ("/wiki 关键词", "Helldivers 中文 Wiki 词条查询长图：数据表 + 正文摘要 + 主图"
                       "（如 /wiki 战略配备、/wiki 轨道120MM高爆弹火力网）"),
    ]),
]

HELP_NOTES = [
    "官方平台需 @机器人；NapCat 可直接 /指令；名称支持中英文",
    "默认输出战术 HUD 图片卡，渲染失败自动回退文本；指令后加 t 可要纯文本",
    "BTK 伤害结果输出图片卡；可加 距离=20 角度=1 破片=3 波及=部位1,部位2",
    "进群欢迎仅 NapCat；战况以源数据时间与陈旧提示为准",
]

HELP_TAGLINE = "为超级地球而战！"


def build_help_text():
    """/帮助 纯文本版（与图片卡同源数据，保证两条输出永不脱节）"""
    lines = ["📖 绝地潜兵2 战报机器人 · 作战手册", ""]
    lines.append("直接发送指令即可（无需 @，以 / 开头），默认输出图片卡：")
    lines.append("")
    for cn, _en, rows in HELP_SECTIONS:
        lines.append(f"━━ {cn} ━━")
        for cmd, desc in rows:
            lines.append(f"▶ {cmd} —— {desc}")
        lines.append("")
    for note in HELP_NOTES:
        lines.append(f"· {note}")
    lines.append("")
    lines.append(HELP_TAGLINE)
    return "\n".join(lines)


def _help_wrap(text, font, max_w, dr):
    """按像素宽度硬折行（中文无空格，逐字累加；\n 视为强制断行）"""
    lines, buf = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(buf)
            buf = ""
            continue
        if dr.textlength(buf + ch, font=font) > max_w:
            lines.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        lines.append(buf)
    return lines or [""]


def _draw_help(dr, img):
    y = _hud_head(dr, img, "群使用说明", "HELLDIVERS 2 · FIELD MANUAL")
    dr.rectangle([PAD, y + 2, PAD + 4, y + 30], fill=GOLD)
    dr.text((PAD + 16, y + 4), "直接发送指令即可，默认输出战术 HUD 图片卡",
            font=F(21), fill=TEXT)
    y += 46
    for si, (cn, en, rows) in enumerate(HELP_SECTIONS, 1):
        y += 10
        stripe_band(img, y, 34, alpha=14)
        dr.rectangle([PAD, y + 3, PAD + 6, y + 31], fill=GOLD)
        dr.text((PAD + 18, y + 3), cn, font=F(24, True), fill=GOLD2)
        en_x = PAD + 18 + dr.textlength(cn, font=F(24, True)) + 14
        dr.text((en_x, y + 11), en, font=F(13), fill=MUTE)
        idx = "%02d" % si
        dr.text((W - PAD - dr.textlength(idx, font=F(18, True)), y + 7),
                idx, font=F(18, True), fill=BRASS)
        y += 48
        col = min(330, max(int(dr.textlength(c, font=F(21, True)))
                           for c, _ in rows) + 26)
        for cmd, desc in rows:
            bh = 34
            dr.rectangle([PAD, y, PAD + col, y + bh], fill=PANEL, outline=LINE2)
            dr.rectangle([PAD, y, PAD + 3, y + bh], fill=GOLD)
            dr.text((PAD + 14, y + 5), cmd, font=F(21, True), fill=GOLD2)
            tx = PAD + col + 16
            dlines = _help_wrap(desc, F(19), W - PAD - tx, dr)
            lh = 27
            ty = y + (bh - lh * len(dlines)) // 2 + 2
            for ln in dlines:
                dr.text((tx, ty), ln, font=F(19), fill=TEXT)
                ty += lh
            y += max(bh, lh * len(dlines) + 6) + 10
            dr.line([(PAD, y), (W - PAD, y)], fill=LINE)
            y += 6
        y += 4
    y += 8
    dr.line([(0, y), (W, y)], fill=LINE2)
    y += 16
    for note in HELP_NOTES:
        dr.text((PAD, y), "·", font=F(18, True), fill=BRASS)
        dr.text((PAD + 18, y), note, font=F(18), fill=DIM)
        y += 28
    y += 12
    stripe_band(img, y, 40, alpha=22)
    tw = dr.textlength(HELP_TAGLINE, font=F(22, True))
    dr.text(((W - tw) // 2, y + 8), HELP_TAGLINE, font=F(22, True), fill=GOLD)
    y += 54
    return y


def render_help_card(out_dir=None):
    """/帮助 图片卡：作战手册（零外部依赖，内容见 HELP_SECTIONS）。失败返回 None。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        return _render_cached(
            os.path.join(out_dir, "help_card.png"),
            os.path.join(out_dir, "help_card.meta.json"),
            "help-v8", _draw_help)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def save_pages(img, height, out_path, document=None, page_info=None):
    """Split only near quiet rows, preserving text and adding page/source labels."""
    spans, start = [], 0
    while start < height:
        end = min(height, start + 2100)
        if end < height:
            for row in range(end, max(start + 1000, end - 100), -1):
                band = img.crop((PAD + 28, row, W - PAD - 28, row + 2)).convert("L")
                if band.point(lambda p: 255 if p > 100 else 0).getbbox() is None:
                    end = row
                    break
        spans.append((start, end))
        start = end
    paths = []
    if page_info and len(spans) != 1:
        raise ValueError("预分页内容超出单页高度")
    doc = document or {}
    quality = " · 缓存/数据陈旧" if (doc.get("_hd2") or {}).get("stale") else ""
    for i, (start, end) in enumerate(spans):
        page = Image.new("RGB", (W, end - start + 64), BG)
        page.paste(img.crop((0, start, W, end)), (0, 0))
        dr = ImageDraw.Draw(page)
        caption = f"第 {i+1}/{len(spans)} 页 · 数据时间 {doc.get('fetchedAt', '见正文')}" + quality
        if page_info:
            caption = f"第 {page_info[0]}/{page_info[1]} 页 · {page_info[2]}"
        dr.text((PAD, end - start + 14), caption, font=F(18), fill=DIM)
        path = os.path.splitext(out_path)[0] + f"_{i+1}.png"
        fd, tmp = tempfile.mkstemp(prefix=".card_", dir=os.path.dirname(path))
        os.close(fd)
        try:
            page.save(tmp, format="PNG")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        paths.append(path)
    return paths
