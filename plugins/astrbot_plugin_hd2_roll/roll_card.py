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
import random
import sys
import time
import urllib.request
from PIL import Image, ImageDraw, ImageFont

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
def F(sz, bold=False):
    k = (sz, bold)
    if k not in _f:
        _f[k] = ImageFont.truetype(FZB if bold else FZ, sz, index=0)
    return _f[k]

SLOT_LABEL = {"primary": "主手 · PRIMARY", "secondary": "副手 · SECONDARY",
              "grenade": "手雷 · GRENADE"}
SITE_CACHE = {}

def _site_get(url, ttl=240):
    hit = SITE_CACHE.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "HD2Bot/1.0 (roll-card)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        obj = json.load(r)
    SITE_CACHE[url] = (time.time(), obj)
    return obj

def _catalog():
    """装备目录：240s 站点缓存 + 磁盘兜底（静态目录，离线可用）。"""
    cache_file = os.path.join(CACHE_DIR, "catalog.json")
    try:
        cat = _site_get(CATALOG_URL)
        if cat.get("equipment"):
            try:
                json.dump(cat, open(cache_file, "w", encoding="utf-8"))
            except Exception:
                pass
            return cat
    except Exception:
        pass
    try:
        return json.load(open(cache_file, encoding="utf-8"))
    except Exception:
        return None

def roll_seven(cat):
    """引擎 pure 模式抽取：各槽均匀抽 1 + 战备互异抽 4；七件互异、机甲 ≤1。"""
    pools = {s: [e for e in (cat.get("equipment") or []) if e.get("selection_slot") == s]
             for s in ("primary", "secondary", "grenade", "stratagem")}
    if any(len(pools[s]) < 1 for s in ("primary", "secondary", "grenade")) or len(pools["stratagem"]) < 4:
        return None
    for _ in range(32):
        lo = {"primary": random.choice(pools["primary"]),
              "secondary": random.choice(pools["secondary"]),
              "grenade": random.choice(pools["grenade"]),
              "stratagems": random.sample(pools["stratagem"], 4)}
        ids = [lo["primary"].get("id"), lo["secondary"].get("id"), lo["grenade"].get("id")] + \
              [t.get("id") for t in lo["stratagems"]]
        if len(set(ids)) != 7:
            continue
        exos = sum(1 for i in ids if str(i).startswith("vehicle_exo_"))
        if exos <= 1:
            return lo
    return lo

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

def render_roll_card(out_dir=None):
    """七格配装卡 → PNG 路径；失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        cat = _catalog()
        if not cat:
            return None
        lo = roll_seven(cat)
        if not lo:
            return None
        by_id = {e.get("id"): e for e in (cat.get("equipment") or [])}

        now = time.strftime("%Y-%m-%d %H:%M")
        img = Image.new("RGB", (W, TALL), BG)
        dr = ImageDraw.Draw(img)

        # ══ HUD 头 ══
        stripe_band(img, 0, 92, alpha=22)
        dr.rectangle([0, 0, 5, 92], fill=GOLD)
        dr.text((PAD, 14), "战术搭配器", font=F(42, True), fill=GOLD)
        dr.text((PAD + dr.textlength("战术搭配器", font=F(42, True)) + 24, 36),
                "LOADOUT ROLL · 七格纯随机", font=F(15), fill=MUTE)
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
        dr.text((PAD, y + 14), "抽取主手、副手、手雷与四个战备 · 纯随机，每格独立抽取",
                font=F(14), fill=MUTE)
        ft = "自由换装与六维评估：主站战术搭配器"
        fw = dr.textlength(ft, font=F(14))
        dr.text((W - PAD - fw, y + 14), ft, font=F(14), fill=MUTE)
        dr.text((PAD, y + 40), "真理部 · 管理式民主 · 与 Arrowhead Game Studios 无隶属关系",
                font=F(14), fill=(80, 80, 80))
        H = min(TALL, y + 88)

        img = img.crop((0, 0, W, H))
        out_path = os.path.join(out_dir, "roll_card.png")
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
