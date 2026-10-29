# -*- coding: utf-8 -*-
"""/roll 图片卡渲染器（astrbot_plugin_hd2_roll）

主站战术搭配器（HD2_Wiki/loadout.html）同款算法：
  · 纯随机（Fisher–Yates 抽 4）/ 平衡随机（三号位/背包/次抛槽位规则）
  · 总评 = T×5 + T² − M²×0.5 + ΣBonus；评级 S≥44 A≥32 B≥20 C≥10 D<10
  · 四维：对单/对群/穿甲/专项（第 4 维名随主导类型）
数据：主站 HD2_Wiki loadout.json（社区评分表）；图标：icons/<id>.png（预转换 PNG）。
入口：render_roll_card(mode="pure"|"balanced") → PNG 路径（失败 None，回退文本）。
"""
import json
import sys
import os
import random
import time
import urllib.request
from PIL import Image, ImageDraw, ImageFont

LOADOUT_URLS = [
    "https://jerry114514.github.io/HD2_Wiki/data/wiki/zh/loadout.json",
    "http://127.0.0.1:8770/HD2_Wiki/data/wiki/zh/loadout.json",
]
HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")
CACHE_DIR = os.path.join(HERE, "cache")

# ── 主站常量（loadout.html 逐项照搬） ──
DIM4_CN = {"轨道": "冷却", "飞鹰": "冷却", "支援武器": "人机工效", "重型支援武器": "压制力",
           "载具": "战术性", "可部署物": "战术灵活性", "背包": "战术价值"}
TYPE_COLOR = {"轨道": (245, 197, 24), "飞鹰": (91, 163, 208), "支援武器": (46, 204, 113),
              "重型支援武器": (231, 76, 60), "载具": (207, 100, 248),
              "可部署物": (214, 128, 109), "背包": (107, 114, 128)}
DIM_COLOR = [(255, 107, 74), (245, 197, 24), (207, 100, 248), (91, 163, 208)]
DISPOSABLE_WEAPONS = [
    "support_eat_17_expendable_anti_tank", "support_eat_411_leveller",
    "support_eat_700_incendiary", "support_mls_4x_commando", "support_ms_11_missile_silo",
]
BACKPACK_WEAPONS = [
    "heavy_support_gr_8_recoilless_rifle", "heavy_support_ac_8_autocannon",
    "heavy_support_faf_14_spear", "heavy_support_rl_77_airburst_rocket_launcher",
    "heavy_support_gl_28_belt_fed_grenade_launcher", "heavy_support_b_flam_80_flamer",
    "heavy_support_m_1000_hmg", "heavy_support_sta_x3_wasp_launcher",
]

# ── 黑金 HUD tokens ──
BG = (10, 10, 10); PANEL = (18, 18, 18); LINE = (36, 36, 36); LINE2 = (42, 42, 42)
TEXT = (242, 242, 242); DIM = (168, 168, 168); MUTE = (110, 110, 110)
GOLD = (255, 176, 0); GOLD2 = (255, 201, 74); BRASS = (200, 134, 42)
GREEN = (31, 168, 74)
RED = (255, 77, 46)
W = 1080; PAD = 36; TALL = 2000

FZ = r"C:\Windows\Fonts\msyh.ttc"
FZB = r"C:\Windows\Fonts\msyhbd.ttc"
_f = {}
def F(sz, bold=False):
    k = (sz, bold)
    if k not in _f:
        _f[k] = ImageFont.truetype(FZB if bold else FZ, sz, index=0)
    return _f[k]

def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "HD2Bot/1.0 (roll-card)"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return json.load(r)

FETCH_TIMEOUT = 20
_site_cache = {}
def _site_get(url, ttl=240):
    hit = _site_cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    obj = _get_json(url)
    _site_cache[url] = (time.time(), obj)
    return obj

def grade(score):
    if score >= 44: return "S"
    if score >= 32: return "A"
    if score >= 20: return "B"
    if score >= 10: return "C"
    return "D"

GRADE_COLOR = {"S": GOLD, "A": GREEN, "B": (91, 163, 208), "C": DIM, "D": (255, 77, 46)}

def slot_kind(x):
    if not x: return "other"
    if x.get("type") not in ("支援武器", "重型支援武器"):
        return "backpack" if x.get("type") == "背包" else "other"
    if x.get("id") in DISPOSABLE_WEAPONS: return "disposable"
    if x.get("id") in BACKPACK_WEAPONS: return "bpWeapon"
    return "weapon"

def can_take(x, st):
    k = slot_kind(x)
    if k == "disposable": return True
    if k == "weapon": return not st["sw"] and not st["bpw"]
    if k == "backpack": return not st["bp"] and not st["bpw"]
    if k == "bpWeapon": return not st["sw"] and not st["bp"] and not st["bpw"]
    return True

def mark_taken(x, st):
    k = slot_kind(x)
    if k == "weapon": st["sw"] = True
    elif k == "backpack": st["bp"] = True
    elif k == "bpWeapon": st["sw"] = st["bp"] = st["bpw"] = True

def shuffle(p):
    p = p[:]
    for i in range(len(p) - 1, 0, -1):
        j = random.randint(0, i)
        p[i], p[j] = p[j], p[i]
    return p

def draw_pure(pool):
    return shuffle(pool)[:4]

def draw_balanced(pool):
    p = shuffle(pool)
    st = {"sw": False, "bp": False, "bpw": False}
    out = []
    for x in p:
        if len(out) >= 4: break
        if not can_take(x, st): continue
        out.append(x)
        mark_taken(x, st)
    return out if len(out) == 4 else draw_pure(pool)

def progress_bar(dr, x, y, w, h, ratio, fill):
    dr.rectangle([x, y, x + w, y + h], fill=(31, 31, 31), outline=LINE2)
    fw = int(w * max(0.0, min(1.0, ratio)))
    if fw > 0:
        dr.rectangle([x, y, x + fw, y + h], fill=fill)

def stripe_band(img, y0, h, alpha=22):
    dr = ImageDraw.Draw(img, "RGBA")
    x = -h
    while x < W:
        dr.polygon([(x, y0 + h), (x + h, y0), (x + h + 7, y0), (x + 7, y0 + h)],
                   fill=(255, 176, 0, alpha))
        x += 14
    dr.line([(0, y0), (W, y0)], fill=LINE2)
    dr.line([(0, y0 + h - 1), (W, y0 + h - 1)], fill=LINE2)

def render_roll_card(mode="pure", out_dir=None):
    """渲染配装卡 → PNG 路径；失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        pool = _site_get("https://jerry114514.github.io/HD2_Wiki/data/wiki/zh/loadout.json").get("stratagems") or []
        if not pool:
            return None
        picked = draw_balanced(pool) if mode == "balanced" else draw_pure(pool)

        types = {}
        for x in picked: types[x.get("type")] = types.get(x.get("type"), 0) + 1
        T = len(types)
        M = sum(1 for x in picked if x.get("subtag"))
        bonus_sum = sum(x.get("bonus") or 0 for x in picked)
        total = T * 5 + T * T - M * M * 0.5 + bonus_sum
        g = grade(total)
        dom_type = sorted(types, key=lambda t: -types[t])[0] if types else ""
        d4_name = DIM4_CN.get(dom_type, "专项")

        now = time.strftime("%Y-%m-%d %H:%M")
        out_path = os.path.join(out_dir, "roll_card.png")
        img = Image.new("RGB", (W, TALL), BG)
        dr = ImageDraw.Draw(img)

        # ══ HUD 头 ══
        stripe_band(img, 0, 92, alpha=22)
        dr.rectangle([0, 0, 5, 92], fill=GOLD)
        dr.text((PAD, 14), "战术搭配器", font=F(42, True), fill=GOLD)
        dr.text((PAD + dr.textlength("战术搭配器", font=F(42, True)) + 24, 36),
                "LOADOUT ROLL", font=F(15), fill=MUTE)
        mode_t = "MODE · 平衡随机" if mode == "balanced" else "MODE · 纯随机"
        mw = dr.textlength(mode_t, font=F(19, True))
        dr.text((W - PAD - mw, 16), mode_t, font=F(19, True), fill=GOLD2)
        sw = dr.textlength(now, font=F(16))
        dr.text((W - PAD - sw, 48), now, font=F(16), fill=DIM)
        dr.line([(0, 92), (W, 92)], fill=LINE2)
        y = 92 + 16

        # ══ 四张战备卡（2×2） ══
        cw, ch = (W - PAD * 2 - 14) // 2, 300
        for i, x in enumerate(picked):
            cx = PAD + (i % 2) * (cw + 14)
            cy = y + (i // 2) * (ch + 14)
            tcolor = TYPE_COLOR.get(x.get("type"), (107, 114, 128))
            dr.rectangle([cx, cy, cx + cw, cy + ch], fill=PANEL, outline=LINE)
            dr.rectangle([cx, cy, cx + cw, cy + 3], fill=tcolor)
            # 图标
            icon_p = os.path.join(ICON_DIR, (x.get("id") or "") + ".png")
            icon = Image.open(icon_p).convert("RGBA") if os.path.exists(icon_p) else None
            if icon:
                icon = icon.resize((72, 72))
                img.paste(icon, (cx + 14, cy + 14), icon)
            # 名字 + 类型
            nm = x.get("name") or ""
            nx = cx + 98
            ny = cy + 16
            for ln in wrap_name(nm, 11)[:2]:
                dr.text((nx, ny), ln, font=F(21, True), fill=TEXT)
                ny += 27
            tw = dr.textlength(x.get("type") or "", font=F(14, True)) + 16
            dr.rectangle([cx + 14, cy + 96, cx + 14 + tw, cy + 120],
                         fill=(tcolor[0] // 8 + 16, tcolor[1] // 8 + 16, tcolor[2] // 8 + 16),
                         outline=tcolor)
            dr.text((cx + 22, cy + 99), x.get("type") or "", font=F(14, True), fill=tcolor)
            # Bonus
            bonus = x.get("bonus") or 0
            bt = "Bonus " + (("+" + str(bonus)) if bonus > 0 else str(bonus))
            bc = GOLD if bonus > 0 else (MUTE if bonus == 0 else RED)
            bw = dr.textlength(bt, font=F(15, True))
            dr.text((cx + cw - 14 - bw, cy + 99), bt, font=F(15, True), fill=bc)
            # 四维条
            d4 = DIM4_CN.get(x.get("type"), "专项")
            dims = [("对单", x.get("stats", [0, 0, 0, 0])[0], DIM_COLOR[0]),
                    ("对群", x.get("stats", [0, 0, 0, 0])[1], DIM_COLOR[1]),
                    ("穿甲", x.get("stats", [0, 0, 0, 0])[2], DIM_COLOR[2]),
                    (d4, x.get("stats", [0, 0, 0, 0])[3], DIM_COLOR[3])]
            yy = cy + 134
            for lab, val, color in dims:
                dr.text((cx + 14, yy - 1), lab, font=F(14), fill=DIM)
                progress_bar(dr, cx + 92, yy + 2, cw - 164, 10, (val or 0) / 10.0, fill=color)
                vt = str(val or 0)
                dr.text((cx + cw - 56, yy - 1), vt, font=F(15, True), fill=TEXT)
                yy += 26
        y += ch * 2 + 14 + 18

        # ══ 评分明细 + 四维轮廓（左右双栏） ══
        dim_avg = [sum(x.get("stats", [0, 0, 0, 0])[i] or 0 for x in picked) / max(1, len(picked)) for i in range(4)]
        chart_labels = ["对单", "对群", "穿甲", d4_name]
        panel_h = 292
        pw = (W - PAD * 2 - 14) // 2
        # 左：评分明细（评级大字独立右栏）
        dr.rectangle([PAD, y, PAD + pw, y + panel_h], fill=PANEL, outline=LINE)
        dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
        section_label(dr, PAD + 18, y + 13, "评分明细", F(23, True))
        rows = [("类型数 T", str(T)), ("多功能 M", str(M)),
                ("Bonus 合计", ("+" + str(bonus_sum)) if bonus_sum > 0 else str(bonus_sum)),
                ("总评", f"{total:.1f}")]
        ry = y + 52
        for lab, val in rows:
            dr.text((PAD + 18, ry), lab, font=F(18), fill=DIM)
            vw = dr.textlength(val, font=F(22, True))
            strong = lab == "总评"
            dr.text((PAD + pw - 148 - vw, ry - (2 if strong else 0)), val,
                    font=F(26, True) if strong else F(22, True),
                    fill=GOLD if strong else TEXT)
            ry += 44
        gtxt = g + " 级"
        gw = dr.textlength(gtxt, font=F(64, True))
        dr.text((PAD + pw - 18 - gw, y + 60), gtxt, font=F(64, True), fill=GRADE_COLOR[g])
        hint = ("🏆 " if g == "S" else "") + ("顶级配装，真理部为你骄傲！" if g == "S" else "搭配越多样，战术评级越高")
        dr.text((PAD + 18, y + panel_h - 36), hint, font=F(15), fill=DIM)
        # 右：四维轮廓
        x2 = PAD + pw + 14
        dr.rectangle([x2, y, x2 + pw, y + panel_h], fill=PANEL, outline=LINE)
        dr.rectangle([x2, y, x2 + pw, y + 3], fill=GOLD)
        section_label(dr, x2 + 18, y + 13, "配装能力轮廓", F(23, True))
        yy = y + 56
        for i, lb in enumerate(chart_labels):
            dr.text((x2 + 18, yy), lb, font=F(16), fill=DIM)
            progress_bar(dr, x2 + 96, yy + 2, pw - 186, 12, dim_avg[i] / 12.0, fill=DIM_COLOR[i])
            vt = f"{dim_avg[i]:.1f}"
            vw = dr.textlength(vt, font=F(16, True))
            dr.text((x2 + pw - 18 - vw, yy), vt, font=F(16, True), fill=TEXT)
            yy += 50
        dr.text((x2 + 18, y + panel_h - 36), "轮廓 = 四维均值 · 满值 12", font=F(14), fill=MUTE)
        y += panel_h + 18

        # ══ 评级标准 + 公式 ══
        dr.text((PAD, y + 6), "评级标准：S≥44 · A≥32 · B≥20 · C≥10 · D<10    "
               "评分公式：总评 = T×5 + T² − M²×0.5 + ΣBonus", font=F(15), fill=MUTE)
        y += 44
        ft = "再来一次：发 /roll · 平衡随机：发 /roll 平衡"
        fw = dr.textlength(ft, font=F(15, True))
        dr.text(((W - fw) // 2, y), ft, font=F(15, True), fill=BRASS)
        H = min(TALL, y + 56)

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

def wrap_name(name, width):
    out = []
    cur = name or ""
    while len(cur) > width:
        out.append(cur[:width]); cur = cur[width:]
    out.append(cur)
    return out

def section_label(dr, x, y, text, f_label):
    dr.rectangle([x, y + 2, x + 6, y + 2 + 28], fill=GOLD)
    dr.text((x + 16, y), text, font=f_label, fill=TEXT)

if __name__ == "__main__":
    mode = "balanced" if (len(sys.argv) > 2 and sys.argv[2] == "balanced") else "pure"
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(CACHE_DIR, "roll_card.png")
    path = render_roll_card(mode, os.path.dirname(os.path.abspath(out)) or CACHE_DIR)
    print("rendered:", path)
