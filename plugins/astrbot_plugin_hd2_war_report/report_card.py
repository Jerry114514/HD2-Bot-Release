# -*- coding: utf-8 -*-
"""/战报 图片卡渲染器（astrbot_plugin_hd2_war_report）

主站黑金 HUD 风格（tokens 同源）：零圆角 / 硬描边 / 琥珀强调 / 45° 斜纹头。
数据口径与主站一致：data.json（官方 API 落盘 + 翻译仓中文）+ player_distribution。
设计为纯函数式入口：render_report_card() → PNG 路径（失败返回 None，调用方回退文本）。
缓存：按 data.fetchedAt 记忆最近一次成品，同版本重复调用直接复用文件。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont

DATA_URL = "https://jerry114514.github.io/HD2-Galatic_war-Map/data.json"
DIST_URL = "https://jerry114514.github.io/HD2-Galatic_war-Map/data/history/player_distribution.json"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
OUT_NAME = "report_card.png"
FETCH_TIMEOUT = 20
FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
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
W = 1080
PAD = 36
TALL = 2600

_fcache = {}
def F(sz, bold=False):
    key = (sz, bold)
    if key not in _fcache:
        reg, bold_p = FONT_CANDIDATES[0]
        _fcache[key] = ImageFont.truetype(bold_p if bold else reg, sz, index=0)
    return _fcache[key]

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HD2Bot/1.0 (report-card)"})
    last = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
                return json.load(r)
        except Exception as e:
            last = e
            time.sleep(0.6)
    raise last


_site_cache = {}  # url -> (ts, obj)·全卡共享 240s TTL


def _site_get(url, ttl=240):
    hit = _site_cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    obj = _get(url)
    _site_cache[url] = (time.time(), obj)
    return obj


FACTION_CN = {"Humans": "超级地球", "Terminid": "终结族", "Terminids": "终结族",
              "Automaton": "机器人", "Automatons": "机器人", "Illuminate": "光能者"}
FACTION_COLOR = {"超级地球": BLUE, "终结族": (245, 197, 24),
                 "机器人": (214, 128, 109), "光能者": (207, 100, 248)}
DSS_ACTION_SHORT = {"EAGLE STORM": "飞鹰风暴", "ORBITAL BLOCK": "轨道封锁",
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

def fmt(n):
    return f"{int(n or 0):,}"

def pct(p, g):
    try:
        return max(0.0, min(100.0, (p / g * 100.0) if g else 0.0))
    except Exception:
        return 0.0

def wrap(text, width_chars):
    out = []
    for para in (text or "").replace("\r", "").split("\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > width_chars:
            out.append(para[:width_chars])
            para = para[width_chars:]
        out.append(para)
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

def render_report_card(out_dir=None):
    """渲染 /战报 图片卡 → 返回 PNG 绝对路径；任何失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, OUT_NAME)

        meta_path = os.path.join(out_dir, OUT_NAME + ".meta")
        # 时间快路径：站内数据 5 分钟一更新，240s 内的成品直接复用（零网络）
        try:
            if os.path.exists(out_path) and os.path.exists(meta_path):
                meta = json.loads(open(meta_path, encoding="utf-8").read())
                if meta.get("rev") and time.time() - meta.get("ts", 0) < 240:
                    return out_path
        except Exception:
            pass

        data = _site_get(DATA_URL)
        try:
            dist_hist = _site_get(DIST_URL).get("history") or []
        except Exception:
            dist_hist = []

        rev = str(data.get("fetchedAt") or "")
        # 同版本缓存：站内数据 5 分钟一更新，重复触发直接复用成品
        try:
            if os.path.exists(out_path) and os.path.exists(meta_path):
                meta = json.loads(open(meta_path, encoding="utf-8").read())
                if meta.get("rev") == rev:
                    return out_path
        except Exception:
            pass

        now = datetime.now(timezone.utc).astimezone()
        a = data.get("assignments") or {}
        mo = data.get("major_order") or {}
        mtask = mo.get("tasks") or {}
        tasks = (mtask.get("items") if isinstance(mtask, dict) else mtask) or []
        brief = (mo.get("translated_brief") or a.get("briefing") or "").strip()
        war = data.get("war") or {}
        online = ((war.get("statistics") or {}).get("playerCount")) or 0
        campaigns = data.get("campaigns") or []
        news = data.get("news") or []

        f_title = F(42, True); f_h2 = F(25, True); f_h3 = F(21, True)
        f_body = F(19); f_small = F(16); f_tiny = F(14)
        f_kpi = F(32, True); f_kpi_l = F(15)

        img = Image.new("RGB", (W, TALL), BG)
        dr = ImageDraw.Draw(img)

        # ══ HUD 头 ══
        stripe_band(img, 0, 92, alpha=22)
        dr.rectangle([0, 0, 5, 92], fill=GOLD)
        dr.text((PAD, 14), "银河战争速报", font=f_title, fill=GOLD)
        dr.text((PAD + 266, 36), "GALACTIC WAR REPORT", font=F(15), fill=MUTE)
        ts = now.strftime("%Y-%m-%d %H:%M")
        tw = dr.textlength(ts, font=f_small)
        dr.text((W - PAD - tw, 16), ts, font=f_small, fill=DIM)
        sw = dr.textlength("真理部 · MOT", font=f_small)
        dr.text((W - PAD - sw, 44), "真理部 · MOT", font=f_small, fill=BRASS)
        y = 92 + 16

        # ══ KPI 行 ══
        KPI_H = 86
        kpis = [("在线指挥官", fmt(online)),
                ("进行中战役", str(len(campaigns)) if campaigns else "—")]
        if tasks:
            kpis.append(("指令任务", f"{len(tasks)} 项"))
            kpis.append(("指令总进度",
                         f"{pct(sum(t.get('progress') or 0 for t in tasks), sum(t.get('goal') or 1 for t in tasks)):.1f}%"))
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
            brief_lines = wrap(brief, 46)[:8]
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
                name = f"任务 {i + 1}：{t.get('action') or ''}{(' · ' + t['faction_name']) if t.get('faction_name') else ''}"
                dr.text((PAD + 18, yy), name, font=f_h3, fill=TEXT)
                req = t.get("requirement") or {}
                if req.get("type") == "equipment" and (req.get("name") or req.get("en")):
                    equip = "限定：" + (req.get("name") or req.get("en"))
                    ew = dr.textlength(equip, font=f_small)
                    dr.rectangle([W - PAD - 18 - ew - 20, yy - 3, W - PAD - 18, yy + 29],
                                 fill=(26, 20, 10), outline=BRASS)
                    dr.text((W - PAD - 18 - ew - 10, yy + 1), equip, font=f_small, fill=GOLD2)
                yy += 38
                goal, prog = t.get("goal") or 0, t.get("progress") or 0
                r = pct(prog, goal) / 100.0
                done = prog >= goal and goal > 0
                progress_bar(dr, PAD + 18, yy, W - PAD * 2 - 36, 14, r, fill=GREEN if done else GOLD)
                yy += 24
                pt = ("已完成" if done else f"{r * 100:.1f}%")
                dr.text((PAD + 18, yy), pt, font=f_h3, fill=GREEN if done else GOLD)
                sub = f"进度 {fmt(prog)} / 目标 {fmt(goal)}"
                sw2 = dr.textlength(sub, font=f_small)
                dr.text((W - PAD - 18 - sw2, yy + 6), sub, font=f_small, fill=MUTE)
                yy += 62
            y += panel_h + 18

        # ══ 战区玩家分布 ══
        if dist_hist:
            latest = dist_hist[-1]
            total = latest.get("totalPlayers") or 0
            fac = latest.get("factions") or {}
            rows = [("超级地球", fac.get("Humans") or 0, BLUE),
                    ("终结族", fac.get("Terminids") or 0, (245, 197, 24)),
                    ("机器人", fac.get("Automatons") or 0, (214, 128, 109)),
                    ("光能者", fac.get("Illuminate") or 0, (207, 100, 248))]
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
                r = (cnt / total * 100) if total else 0
                dr.text((PAD + 18, yy), name, font=f_body, fill=TEXT)
                progress_bar(dr, PAD + 140, yy + 3, W - PAD * 2 - 330, 16, r / 100.0, fill=color)
                vt = f"{fmt(cnt)}（{r:.1f}%）"
                vw = dr.textlength(vt, font=f_body)
                dr.text((W - PAD - 18 - vw, yy), vt, font=f_body, fill=TEXT)
                yy += 54
            total_t = f"总计在线：{fmt(total)} 名指挥官" + (f" · 采样 {u}" if u else "")
            tw2 = dr.textlength(total_t, font=f_small)
            dr.text((W - PAD - 18 - tw2, yy + 2), total_t, font=f_small, fill=DIM)
            y += panel_h + 18

        # ══ 最新要闻 ══
        news_show = [n for n in news if (n.get("translated") or n.get("original"))][:2]
        y_after_news = None
        if news_show:
            blocks = []
            for n in news_show:
                blocks.append(wrap(n.get("translated") or n.get("original") or "", 48)[:6])
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
        dr.text((PAD, y + 16), "数据来源：官方战况 API · 每 5 分钟自动更新", font=f_tiny, fill=MUTE)
        ft = "发送 /查表 /dss /战役 /查询 查看更多"
        fw = dr.textlength(ft, font=f_tiny)
        dr.text((W - PAD - fw, y + 16), ft, font=f_tiny, fill=MUTE)
        dr.text((PAD, y + 42), "真理部 · 管理式民主 · 本站与 Arrowhead Game Studios 无隶属关系",
                font=f_tiny, fill=(80, 80, 80))
        H = min(TALL, y + 88)

        img = img.crop((0, 0, W, H))
        tmp = out_path + ".tmp"
        img.save(tmp, format="PNG")
        os.replace(tmp, out_path)
        try:
            open(meta_path, "w", encoding="utf-8").write(
                json.dumps({"rev": rev, "ts": time.time()}))
        except Exception:
            pass
        return out_path
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


def _render_cached(out_path, meta_path, rev, draw_fn):
    """通用缓存外壳：rev 未变 → 直接复用成品"""
    try:
        if os.path.exists(out_path) and os.path.exists(meta_path):
            if json.loads(open(meta_path, encoding="utf-8").read()).get("rev") == rev:
                return out_path
    except Exception:
        pass
    try:
        img = Image.new("RGB", (W, TALL), BG)
        dr = ImageDraw.Draw(img)
        y = draw_fn(dr, img)
        H = min(TALL, y + 20)
        img = img.crop((0, 0, W, H))
        tmp = out_path + ".tmp"
        img.save(tmp, format="PNG")
        os.replace(tmp, out_path)
        try:
            open(meta_path, "w", encoding="utf-8").write(json.dumps({"rev": rev, "ts": time.time()}))
        except Exception:
            pass
        return out_path
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


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
                if pl.get("currentOwner") == "Humans":
                    ratio = 100.0
                else:
                    ratio = (1 - (pl.get("health") or 0) / (pl.get("maxHealth") or 1)) * 100.0
                rows.append((cn, pl.get("sector") or "", fac, ratio,
                             pl.get("players") or 0, True))
            rows.sort(key=lambda x: -x[4])
            players_sum = sum(r[4] for r in rows)
            top_lib = max((r[3] for r in rows), default=0.0)
            y = _hud_head(dr, img, "战线总览", "CAMPAIGN LINES")
            KPI_H = 78
            kpis = [("进行中战线", str(len(rows)) + " 条"),
                    ("最高解放度", str(round(top_lib, 1)) + "%"),
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
            for cn, sector, fac, ratio, players, attacking in rows:
                color = FACTION_COLOR.get(fac, GOLD)
                dr.rectangle([PAD + 18, yy + 6, PAD + 22, yy + 30], fill=color)
                dr.text((PAD + 32, yy), cn, font=F(20, True), fill=TEXT)
                sw = dr.textlength(sector, font=F(14))
                dr.text((W - PAD - 18 - sw, yy + 8), sector, font=F(14), fill=MUTE)
                progress_bar(dr, bw_x, yy + 2, bw_w, 12, ratio / 100.0,
                             fill=GOLD if attacking else (60, 60, 60))
                pt = str(round(ratio, 1)) + "%"
                dr.text((bw_x + bw_w + 12, yy - 4), pt, font=F(18, True),
                        fill=GOLD if attacking else MUTE)
                pw = dr.textlength(fmt(players) + " 人", font=F(15))
                dr.text((W - PAD - 18 - pw, yy - 2), fmt(players) + " 人", font=F(15), fill=DIM)
                dr.text((PAD + 32, yy + 24), fac + " · 解放战役", font=F(14), fill=color)
                yy += 46
            dr.text((PAD + 18, y + panel_h - 26),
                    "解放度 = 1 − 当前血量占比 · 己方星球按 100% 显示（官方报满血）",
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
            y = _hud_head(dr, img, "民主空间站", "DEMOCRACY SPACE STATION")

            panel_h = 100
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "当前停靠", F(25, True))
            dr.text((PAD + 18, y + 50), cn, font=F(34, True), fill=TEXT)
            sub = en + "  ·  " + (pl.get("sector") or "")
            dr.text((PAD + 18 + dr.textlength(cn, font=F(34, True)) + 18, y + 60),
                    sub, font=F(16), fill=MUTE)
            y += panel_h + 18

            eids = dss.get("activeEffectIds") or []
            act_by_eid = {}
            for act in dss.get("tacticalActions") or []:
                for eid in act.get("effectIds") or []:
                    act_by_eid[eid] = DSS_ACTION_SHORT.get(act.get("name"), act.get("name") or "")
            chips = [emap.get(i) or act_by_eid.get(i) or ("#" + str(i)) for i in eids]
            eff_panel_h = 96 + (40 if chips else 0)
            dr.rectangle([PAD, y, W - PAD, y + eff_panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "生效效果", F(25, True))
            yy = y + 52
            if not chips:
                dr.text((PAD + 18, yy), "当前无 DSS 生效效果", font=F(19), fill=MUTE)
                yy += 30
            else:
                cx = PAD + 18
                for c in chips:
                    cw = dr.textlength(c, font=F(19, True)) + 24
                    if cx + cw > W - PAD - 18:
                        cx = PAD + 18
                        yy += 40
                    dr.rectangle([cx, yy, cx + cw, yy + 34], fill=(26, 20, 10), outline=BRASS)
                    dr.text((cx + 12, yy + 4), c, font=F(19, True), fill=GOLD2)
                    cx += cw + 10
            note = "#编号 = 对照表未收录的效果 ID，照实显示"
            nw = dr.textlength(note, font=F(13))
            dr.text((W - PAD - 18 - nw, y + eff_panel_h - 24), note, font=F(13), fill=(80, 80, 80))
            y += eff_panel_h + 18

            acts = dss.get("tacticalActions") or []
            if acts:
                ah = 0
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
                    blocks.append((nm, cur, tgt, res, quota))
                    ah += 120
                panel_h = 52 + ah + 12
                dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
                section_label(dr, PAD + 18, y + 13, "战术行动 · 捐献进度", F(25, True))
                yy = y + 54
                for nm, cur, tgt, res, quota in blocks:
                    dr.text((PAD + 18, yy), nm, font=F(23, True), fill=TEXT)
                    if tgt:
                        pt = str(round(cur / tgt * 100)) + "%"
                        pw = dr.textlength(pt, font=F(21, True))
                        dr.text((W - PAD - 18 - pw, yy), pt, font=F(21, True), fill=GOLD2)
                    yy += 38
                    progress_bar(dr, PAD + 18, yy, W - PAD * 2 - 36, 14, (cur / tgt) if tgt else 0.0)
                    yy += 24
                    line = "已筹集 " + fmt(cur) + " / 目标 " + fmt(tgt) + " " + res
                    if quota:
                        line += "  ·  " + quota
                    dr.text((PAD + 18, yy), line, font=F(15), fill=DIM)
                    yy += 58
            else:
                dr.rectangle([PAD, y, W - PAD, y + 80], fill=PANEL, outline=LINE)
                dr.text((PAD + 18, y + 26), "当前无进行中的战术行动", font=F(19), fill=MUTE)
                y += 98
            return y + 10

        return _render_cached(out_path, meta_path, rev, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def render_planet_card(slug, title_cn=None, out_dir=None):
    """/查询 图片卡：slug 形如 zea_rugosia。失败返回 None。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        en = (slug or "").replace("_", " ").replace("-", " ").upper().strip()
        out_path = os.path.join(out_dir, "planet_" + en.replace(" ", "_") + ".png")
        meta_path = out_path + ".meta"
        data = _site_get(DATA_URL)
        pl = next((x for x in (data.get("planets") or [])
                   if (x.get("name") or "").upper() == en), None)
        if not pl:
            return None
        rev = "planet:" + str(data.get("fetchedAt") or "") + ":" + en

        def draw(dr, img):
            smcn = _starmap_cn_map()
            emap = _effect_cn_map()
            cn = title_cn or smcn.get(en) or pl.get("name") or en
            owner_en = pl.get("currentOwner") or ""
            owner = FACTION_CN.get(owner_en, owner_en)
            ocolor = FACTION_COLOR.get(owner, GOLD)
            maxh = pl.get("maxHealth") or 1
            lib = 100.0 if owner == "超级地球" else (1 - (pl.get("health") or 0) / maxh) * 100.0
            y = _hud_head(dr, img, "星球档案", "PLANET INTEL")

            panel_h = 122
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=ocolor)
            dr.text((PAD + 18, y + 16), cn, font=F(40, True), fill=TEXT)
            sub = (pl.get("name") or en) + "  ·  " + (pl.get("sector") or "") + " 星区"
            dr.text((PAD + 20, y + 72), sub, font=F(17), fill=MUTE)
            ob = "当前归属：" + owner
            ow = dr.textlength(ob, font=F(21, True)) + 30
            dr.rectangle([W - PAD - 18 - ow, y + 18, W - PAD - 18, y + 58],
                         fill=(20, 26, 24), outline=ocolor)
            dr.rectangle([W - PAD - 16 - ow, y + 27, W - PAD - 14 - ow, y + 49], fill=ocolor)
            dr.text((W - PAD - ow, y + 25), ob, font=F(21, True), fill=TEXT)
            st = "⚔ 交战中" if pl.get("attacking") else "无交战"
            stw = dr.textlength(st, font=F(17))
            dr.text((W - PAD - 18 - stw, y + 72), st, font=F(17),
                    fill=RED if pl.get("attacking") else MUTE)
            y += panel_h + 18

            panel_h = 172
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "解放度", F(25, True))
            ltxt = str(round(lib, 1)) + "%"
            lw = dr.textlength(ltxt, font=F(44, True))
            dr.text((W - PAD - 18 - lw, y + 6), ltxt, font=F(44, True),
                    fill=GREEN if lib >= 99.9 else GOLD)
            progress_bar(dr, PAD + 18, y + 62, W - PAD * 2 - 36, 20, lib / 100.0,
                         fill=GREEN if lib >= 99.9 else GOLD)
            ky = y + 106
            kpis = [("在线玩家", fmt(pl.get("players") or 0)),
                    ("阻力", str(pl.get("resistance") or 0) + "%/h"),
                    ("当前血量", fmt(pl.get("health") or 0)),
                    ("血池上限", fmt(maxh))]
            kw = (W - PAD * 2 - 36 - 12 * 3) // 4
            for i, (lab, val) in enumerate(kpis):
                kx = PAD + 18 + i * (kw + 12)
                dr.rectangle([kx, ky, kx + kw, ky + 54], fill=(24, 24, 24), outline=LINE)
                dr.text((kx + 10, ky + 6), lab, font=F(14), fill=MUTE)
                dr.text((kx + 10, ky + 25), val, font=F(21, True), fill=TEXT)
            y += panel_h + 18

            eids = pl.get("activeEffects") or []
            chips = [emap.get(i) or ("#" + str(i)) for i in eids]
            chip_rows = max(1, -(-len(chips) // 3))
            panel_h = 52 + 16 + 44 * (chip_rows if chips else 1)
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "环境与作战词条", F(25, True))
            yy = y + 58
            if not chips:
                dr.text((PAD + 18, yy), "当前无环境词条", font=F(19), fill=MUTE)
            else:
                cx = PAD + 18
                for c in chips:
                    cw = dr.textlength(c, font=F(19, True)) + 24
                    if cx + cw > W - PAD - 18:
                        cx = PAD + 18
                        yy += 44
                    dr.rectangle([cx, yy, cx + cw, yy + 36], fill=(26, 20, 10), outline=BRASS)
                    dr.text((cx + 12, yy + 5), c, font=F(19, True), fill=GOLD2)
                    cx += cw + 10
            y += panel_h + 18
            return y

        return _render_cached(out_path, meta_path, rev, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(CACHE_DIR, OUT_NAME)
    path = render_report_card(os.path.dirname(os.path.abspath(out)) or CACHE_DIR)
    print("rendered:", path)
