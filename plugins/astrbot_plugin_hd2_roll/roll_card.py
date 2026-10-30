# -*- coding: utf-8 -*-
"""/roll 图片卡渲染器 · 七格战术搭配（与主站 shared/hd2-loadout 对齐）

七格：主手 / 副手 / 手雷 / 战备×4（不含主站网页上的交互按钮）。
抽取规则照引擎 pure 模式（engine/sampling.mjs + common.mjs）：
  · 各槽从自己的 selection_slot 池均匀抽 1，战备池抽 4 个互异
  · 七件 id 互异；机甲（vehicle_exo_*）最多 1 台
数据：主站 shared/hd2-loadout/data/catalog.json（201 件规范装备，中文名齐备）。
图标：icons/<id>.png（预转换本地 PNG，运行时零网络）。
入口：render_roll_card(out_dir=None) → PNG 路径（失败 None，回退文本）。
"""
import json
import os
from uuid import uuid4
import sys
import time
import urllib.request
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot_plugin_hd2_roll.loadout import draw, catalog

CATALOG_URL = "https://jerry114514.github.io/shared/hd2-loadout/data/catalog.json"
HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")
CACHE_DIR = os.path.join(HERE, "cache")

# ── 黑金 HUD tokens ──
BG = (10, 10, 10); PANEL = (18, 18, 18); LINE = (36, 36, 36); LINE2 = (42, 42, 42)
TEXT = (242, 242, 242); DIM = (168, 168, 168); MUTE = (110, 110, 110)
GOLD = (255, 176, 0); GOLD2 = (255, 201, 74); BRASS = (200, 134, 42)
W = 1080; PAD = 36; TALL = 1800

FZ = r"C:\Windows\Fonts\msyh.ttc"
FZB = r"C:\Windows\Fonts\msyhbd.ttc"
_f = {}
from astrbot_plugin_hd2_war_report.report_card import F

SLOT_LABEL = {"primary": "主手 · PRIMARY", "secondary": "副手 · SECONDARY",
              "grenade": "手雷 · GRENADE"}
SITE_CACHE = {}

def _catalog():
    return catalog()

def roll_seven(cat=None):
    return draw("pure")

def paste_contain(img, src, box):
    """icon contain 缩放居中贴入 box=(x,y,w,h)"""
    x, y, w, h = box
    if src.width > src.height:
        nw, nh = w, max(1, int(src.height * w / src.width))
    else:
        nh, nw = h, max(1, int(src.width * h / src.height))
    icon = src.resize((nw, nh))
    img.paste(icon, (x + (w - nw) // 2, y + (h - nh) // 2), icon)

def icon_path(eid):
    return os.path.join(ICON_DIR, (eid or "") + ".png")

def wrap_name(name, width):
    out = []
    cur = name or ""
    while len(cur) > width:
        out.append(cur[:width]); cur = cur[width:]
    out.append(cur)
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

def section_label(dr, x, y, text, f_label):
    dr.rectangle([x, y + 2, x + 6, y + 2 + 28], fill=GOLD)
    dr.text((x + 16, y), text, font=f_label, fill=TEXT)

def render_roll_card(out_dir=None, result=None):
    """七格配装卡 → PNG 路径；失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        cat = _catalog()
        if not cat:
            return None
        lo = result if result is not None else draw()
        if not lo:
            return None

        now = time.strftime("%Y-%m-%d %H:%M")
        img = Image.new("RGB", (W, TALL), BG)
        dr = ImageDraw.Draw(img)

        # ══ HUD 头 ══
        stripe_band(img, 0, 92, alpha=22)
        dr.rectangle([0, 0, 5, 92], fill=GOLD)
        dr.text((PAD, 14), "战术搭配器", font=F(42, True), fill=GOLD)
        dr.text((PAD + dr.textlength("战术搭配器", font=F(42, True)) + 24, 36),
                "LOADOUT · 七格配装", font=F(15), fill=MUTE)
        sw = dr.textlength(now, font=F(16))
        dr.text((W - PAD - sw, 16), now, font=F(16), fill=DIM)
        sw2 = dr.textlength("真理部 · MOT", font=F(16))
        dr.text((W - PAD - sw2, 44), "真理部 · MOT", font=F(16), fill=BRASS)
        y = 92 + 16

        # ══ 武器装备（主手 / 副手 / 手雷） ══
        sec_h = 54 + 3 * 92 + 14
        dr.rectangle([PAD, y, W - PAD, y + sec_h], fill=PANEL, outline=LINE)
        dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
        section_label(dr, PAD + 18, y + 13, "武器装备", F(23, True))
        dr.text((W - PAD - 18 - dr.textlength("PRIMARY · SECONDARY · GRENADE", font=F(13)), y + 20),
                "PRIMARY · SECONDARY · GRENADE", font=F(13), fill=MUTE)
        yy = y + 54
        for slot in ("primary", "secondary", "grenade"):
            e = lo[slot]
            eid = e.get("id") if e else slot
            name = (e.get("name") if e else None) or eid
            name_en = (e.get("name_en") if e else None) or ""
            # 图标盒（武器渲染图 contain 到 92×60）
            dr.rectangle([PAD + 18, yy + 4, PAD + 122, yy + 72], fill=(26, 26, 26), outline=LINE)
            ip = icon_path(eid)
            if os.path.exists(ip):
                paste_contain(img, Image.open(ip).convert("RGBA"), (PAD + 21, yy + 7, 100, 62))
            # 槽位标签 + 名称
            dr.text((PAD + 140, yy + 2), SLOT_LABEL[slot], font=F(14, True), fill=BRASS)
            dr.text((PAD + 140, yy + 26), name, font=F(24, True), fill=TEXT)
            if name_en and name_en != name:
                dr.text((PAD + 140, yy + 58), name_en, font=F(14), fill=MUTE)
            yy += 92
        y += sec_h + 18

        # ══ 战略配备（×4） ══
        sec_h = 54 + 4 * 76 + 14
        dr.rectangle([PAD, y, W - PAD, y + sec_h], fill=PANEL, outline=LINE)
        dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
        section_label(dr, PAD + 18, y + 13, "战略配备", F(23, True))
        dr.text((W - PAD - 18 - dr.textlength("STRATAGEMS · ×4", font=F(13)), y + 20),
                "STRATAGEMS · ×4", font=F(13), fill=MUTE)
        yy = y + 54
        for i, t in enumerate(lo["stratagems"]):
            e = t
            eid = e.get("id") if e else ("stratagem-" + str(i))
            name = (e.get("name") if e else None) or eid
            name_en = (e.get("name_en") if e else None) or ""
            dr.text((PAD + 18, yy + 6), "战备 " + str(i + 1), font=F(14, True), fill=BRASS)
            ip = icon_path(eid)
            icon_ok = False
            if os.path.exists(ip):
                try:
                    icon = Image.open(ip).convert("RGBA")
                    paste_contain(img, icon, (PAD + 110, yy + 4, 64, 64))
                    icon_ok = True
                except Exception:
                    icon_ok = False
            ix = PAD + 186 if icon_ok else PAD + 18
            dr.text((ix, yy + 4), name, font=F(23, True), fill=TEXT)
            if name_en and name_en != name:
                dr.text((ix, yy + 40), name_en, font=F(14), fill=MUTE)
            yy += 76
        y += sec_h + 16

        # ══ 页脚 ══
        dr.line([(PAD, y), (W - PAD, y)], fill=LINE)
        dr.text((PAD, y + 14), "主手 · 副手 · 手雷 · 战略配备 ×4",
                font=F(14), fill=MUTE)
        ft = "为超级地球而战"
        fw = dr.textlength(ft, font=F(14))
        dr.text((W - PAD - fw, y + 14), ft, font=F(14), fill=MUTE)
        dr.text((PAD, y + 40), "真理部 · 管理式民主 · 与 Arrowhead Game Studios 无隶属关系",
                font=F(14), fill=(80, 80, 80))
        H = min(TALL, y + 88)

        img = img.crop((0, 0, W, H))
        out_path = os.path.join(out_dir, "roll_" + uuid4().hex + ".png")
        tmp = out_path + ".tmp"
        img.save(tmp, format="PNG")
        os.replace(tmp, out_path)
        return out_path
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(CACHE_DIR, "roll_card.png")
    path = render_roll_card(os.path.dirname(os.path.abspath(out)) or CACHE_DIR)
    print("rendered:", path)
