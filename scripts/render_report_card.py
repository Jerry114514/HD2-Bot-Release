# -*- coding: utf-8 -*-
"""/战报 图片化输出 · 样图渲染器（原型）

主站黑金 HUD 风格：零圆角 / 硬描边 / 琥珀强调 / 45° 斜纹头。
数据源：站点 data.json + player_distribution（与 bot /战报 同一数据管线口径）。
布局：高画布顺序绘制，末尾按游标裁切。
用法：python render_report_card.py [输出.png]
"""
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont

# 字体跨平台解析：优先复用插件内的共享解析器，独立拷贝运行时退回本地候选清单
_PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
if _PLUGINS_DIR not in sys.path:
    sys.path.insert(0, _PLUGINS_DIR)
try:
    from astrbot_plugin_hd2_common.fonts import resolve_font_pair as _resolve_font_pair
except ImportError:
    def _resolve_font_pair():
        for pair in (
            (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
            (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
            ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",) * 2,
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        ):
            if all(os.path.isfile(p) for p in pair):
                return pair
        return (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc")

DATA_URL = "https://jerry114514.github.io/HD2-Galatic_war-Map/data.json"
DIST_URL = "https://jerry114514.github.io/HD2-Galatic_war-Map/data/history/player_distribution.json"

# ── 主站 tokens（css/tokens.css 同源） ──
BG = (10, 10, 10)          # --bg-deep
PANEL = (18, 18, 18)       # --card #121212
LINE = (36, 36, 36)        # --line #242424
LINE2 = (42, 42, 42)
TEXT = (242, 242, 242)     # --text
DIM = (168, 168, 168)      # --text-dim
MUTE = (110, 110, 110)     # --text-mute
GOLD = (255, 176, 0)       # --yellow
GOLD2 = (255, 201, 74)
BRASS = (200, 134, 42)     # --brass
BLUE = (91, 163, 208)      # 主页层超级地球蓝
GREEN = (31, 168, 74)      # --ok
RED = (255, 77, 46)
W = 1080
PAD = 36
TALL = 2600

FZ, FZB = _resolve_font_pair()

def F(sz, bold=False):
    return ImageFont.truetype(FZB if bold else FZ, sz, index=0)

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hd2bot-sample"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

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
    """45° 琥珀斜纹带（HUD 头部质感）"""
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

def section_label(dr, x, y, text, f_label, color=GOLD):
    dr.rectangle([x, y + 2, x + 6, y + 2 + 28], fill=color)
    dr.text((x + 16, y), text, font=f_label, fill=TEXT)

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "战报卡片样图.png"
    data = fetch(DATA_URL)
    try:
        dist_hist = fetch(DIST_URL).get("history") or []
    except Exception:
        dist_hist = []
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

    # ══ HUD 头（92px） ══
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

    # ══ KPI 行（86px） ══
    KPI_H = 86
    kpis = [("在线指挥官", fmt(online)),
            ("进行中战役", str(len(campaigns)) if campaigns else "—"),
            ("指令任务", f"{len(tasks)} 项" if tasks else "—")]
    if tasks:
        kpis.append(("指令总进度", f"{pct(sum(t.get('progress') or 0 for t in tasks), sum(t.get('goal') or 1 for t in tasks)):.1f}%"))
    else:
        kpis.append(("指令", "当前无"))
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
        total_t = f"总计在线：{fmt(total)} 名指挥官 · 采样 {u}"
        tw2 = dr.textlength(total_t, font=f_small)
        dr.text((W - PAD - 18 - tw2, yy + 2), total_t, font=f_small, fill=DIM)
        y += panel_h + 18

    # ══ 最新要闻 ══
    news_show = [n for n in news if (n.get("translated") or n.get("original"))][:2]
    if news_show:
        blocks = []
        for n in news_show:
            lines = wrap(n.get("translated") or n.get("original") or "", 48)[:6]
            blocks.append(lines)
        panel_h = 52 + sum(24 + 24 * len(ls) + 14 for ls in blocks) + 4
        dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
        y_after_news = y + panel_h + 18
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

    # ══ 页脚 ══
    y = (y_after_news if news_show else y) + 8
    dr.line([(PAD, y), (W - PAD, y)], fill=LINE)
    dr.text((PAD, y + 16), "数据来源：官方战况 API · 每 5 分钟自动更新", font=f_tiny, fill=MUTE)
    ft = "发送 /查表 /dss /战役 /查询 查看更多"
    fw = dr.textlength(ft, font=f_tiny)
    dr.text((W - PAD - fw, y + 16), ft, font=f_tiny, fill=MUTE)
    dr.text((PAD, y + 42), "真理部 · 管理式民主 · 本站与 Arrowhead Game Studios 无隶属关系",
            font=f_tiny, fill=(80, 80, 80))
    H = min(TALL, y + 88)

    img = img.crop((0, 0, W, H))
    img.save(out)
    print("saved:", out, img.size)

if __name__ == "__main__":
    main()
