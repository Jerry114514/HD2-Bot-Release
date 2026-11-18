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
from astrbot_plugin_hd2_common import api_watch, orders, tactical
from astrbot_plugin_hd2_common import data as shared
from astrbot_plugin_hd2_common.commands import mo_tasks, names as _bundled_planet_names
from astrbot_plugin_hd2_common.sources import table_candidates as _table_candidates
from astrbot_plugin_hd2_common.paths import plugin_cache_dir
_render_locks = {}
_render_lock_guard = threading.Lock()

DATA_URL = "https://jerry114514.github.io/HD2-Galatic_war-Map/data.json"
DIST_URL = "https://jerry114514.github.io/HD2-Galatic_war-Map/data/history/player_distribution.json"
# 渲染缓存/静态图落插件数据目录，不写插件安装目录。
CACHE_DIR = str(plugin_cache_dir("astrbot_plugin_hd2_war_report", create=False))
OUT_NAME = "report_card.png"
FETCH_TIMEOUT = 20
# 字体解析跨平台化：Windows / Linux / macOS 候选清单与 fc-match 动态发现
# 收敛到共享解析器（astrbot_plugin_hd2_common/fonts.py），可用 HD2_FONT / HD2_FONT_BOLD 覆盖
from astrbot_plugin_hd2_common.fonts import resolve_font_pair

# ── Steam 风 tokens：深蓝底 + 亮黄主强调（favicon #f8e800）+ 冷白文字 ──
# 阵营色独立于本表（FACTION_COLOR 与玩家分布行），不随模板变化
BG = (14, 22, 33)      # Steam 深蓝 #0e1621
PANEL = (27, 40, 56)   # Steam 面板蓝 #1b2838
PANEL2 = (34, 49, 66)  # 斑马纹偶数行
LINE = (44, 61, 79)    # #2c3d4f
LINE2 = (56, 76, 97)   # #384c61
TEXT = (240, 245, 250) # 冷白
DIM = (176, 192, 208)  # 蓝灰次级文字
MUTE = (128, 148, 168) # 蓝灰辅助标签
GOLD = (248, 232, 0)   # favicon 亮黄 #f8e800
GOLD2 = (255, 243, 118)# 高亮黄
RICH_YELLOW = (245, 197, 24)  # 主站 --mo-yellow #F5C518（dispatch <i=1>）
BRASS = (199, 176, 31) # 暗黄（次级强调）
BLUE = (91, 163, 208)  # 主站 --mo-place #5BA3D0（dispatch <i=3> / 超级地球）
RICH_COLORS = {"yellow": RICH_YELLOW, "blue": BLUE}
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
        reg, bold_p = resolve_font_pair()
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
def _static_map_json(url, cache_name, build):
    """静态表抓取：成功写盘，网络失败读磁盘副本（静态表永不失效）。"""
    cache_file = os.path.join(CACHE_DIR, cache_name)
    try:
        obj = _site_get(url)
        m = build(obj)
        if m:
            try:
                with open(cache_file, "w", encoding="utf-8") as stream:
                    json.dump(m, stream, ensure_ascii=False)
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
    """starmap.json → {shared.planet_key(EN_NAME): 中文名}"""
    def build(sm):
        pls = sm.get("planets") or {}
        return {
            shared.planet_key(k): str(v.get("cn") or "").strip()
            for k, v in pls.items()
            if isinstance(v, dict) and str(v.get("cn") or "").strip()
        }
    remote = _static_map_json(
        "https://jerry114514.github.io/HD2-Galatic_war-Map/tables/starmap.json",
        "starmap_cn_v2.json", build)
    local = {
        shared.planet_key(k): str(v or "").strip()
        for k, v in _bundled_planet_names().items()
        if str(v or "").strip()
    }
    return {**local, **remote}


def _planet_display_name(slug, title_cn="", starmap_names=None, fallback=""):
    """Resolve a planet title with the same key normalization used by search."""
    return ((starmap_names or {}).get(shared.planet_key(slug)) or title_cn
            or fallback or str(slug or "未知星球"))


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


def _text_w(draw, text, font):
    """``textlength`` 统一入口（空串安全，返回值恒为 float）。"""
    return float(draw.textlength(str(text or ""), font=font))


def ellipsize(draw, text, font, max_width, ellipsis="…"):
    """单行省略：文本超宽时二分截断并补省略号；切断拉丁词时回退到词首。"""
    text = str(text or "")
    if max_width <= 0:
        return ""
    if _text_w(draw, text, font) <= max_width:
        return text
    ell_w = _text_w(draw, ellipsis, font)
    if ell_w >= max_width:
        return ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_w(draw, text[:mid], font) + ell_w <= max_width:
            lo = mid
        else:
            hi = mid - 1
    head = text[:lo].rstrip()
    if 0 < lo < len(text) and _is_word_char(text[lo - 1]) and _is_word_char(text[lo]):
        cut = len(head)
        while cut > 0 and _is_word_char(head[cut - 1]):
            cut -= 1
        if cut > 0:
            head = head[:cut].rstrip()
    return head + ellipsis


def wrap_text(draw, text, font, max_width, max_lines=None):
    """按像素宽度折行（中文逐字、拉丁词按词、``\\n`` 硬断）。

    与 :func:`wrap` 的差别：这里量的是真实像素宽度，可直接喂给固定宽度的面板，
    不会因为字号或中英混排而溢出右边界。``max_lines`` 限制行数时末行用省略号收尾。
    """
    if max_width <= 0:
        return []
    lines = []
    for para in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        para = para.strip()
        if not para:
            continue
        i, n = 0, len(para)
        while i < n:
            lo, hi = i + 1, n
            while lo < hi:                       # 二分求能放下的最长前缀
                mid = (lo + hi + 1) // 2
                if _text_w(draw, para[i:mid], font) <= max_width:
                    lo = mid
                else:
                    hi = mid - 1
            j = lo
            if j < n and _is_word_char(para[j - 1]) and _is_word_char(para[j]):
                k = j
                while k > i and _is_word_char(para[k - 1]):
                    k -= 1
                if k > i:
                    j = k
            segment = para[i:j].strip()
            if not segment:                      # 单字就超宽：硬切一个字符，避免死循环
                segment, j = para[i:i + 1], i + 1
            lines.append(segment)
            i = j
            while i < n and para[i] == " ":
                i += 1
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max(0, max_lines)]
        if lines:
            last = lines[-1]
            while last and _text_w(draw, last + "…", font) > max_width:
                last = last[:-1]
            lines[-1] = (last.rstrip() or "") + "…"
    return lines


def label_value_row(draw, x, y, width, label, value, label_font=None, value_font=None,
                    note=None, note_font=None, gap=16, label_reserve_ratio=0.45,
                    label_min=48, label_fill=MUTE, value_fill=TEXT,
                    note_fill=(90, 90, 90)):
    """「标签 ＋（副标签）… 数值（右对齐）」单行，先量后画，永不重叠/越界。

    降级顺序：副标签换小号 → 截断标签给副标签让位 → 丢弃副标签 → 截断数值。
    返回 ``(行高, boxes)``；``boxes`` 是 label/note/value 的真实包围盒，供离线溢出检查断言。
    """
    label, value = str(label or ""), str(value or "")
    label_font = label_font or F(15)
    value_font = value_font or F(18, True)
    note_font = note_font or F(13)
    right = x + width
    label_w = _text_w(draw, label, label_font)
    max_value = width - gap - max(12.0, min(label_w, width * label_reserve_ratio))
    if _text_w(draw, value, value_font) > max_value:
        value = ellipsize(draw, value, value_font, max_value)
    value_w = _text_w(draw, value, value_font)
    avail = width - value_w - gap

    note_txt = str(note or "")
    note_w = _text_w(draw, note_txt, note_font) if note_txt else 0.0
    if note_txt and label_w + 10 + note_w > avail:
        for _ in range(2):                       # 副标签优先缩号，最多两档
            smaller = F(max(9, int(getattr(note_font, "size", 13)) - 4))
            if _text_w(draw, note_txt, smaller) >= note_w:
                break
            note_font, note_w = smaller, _text_w(draw, note_txt, smaller)
            if label_w + 10 + note_w <= avail:
                break
    if note_txt and label_w + 10 + note_w > avail:
        room = avail - note_w - 10
        if room >= label_min:                    # 截断标签给副标签让位（保住副标签信息）
            label = ellipsize(draw, label, label_font, room)
            label_w = _text_w(draw, label, label_font)
        else:
            note_txt, note_w = "", 0.0
    avail_label = avail - ((note_w + 10.0) if note_txt else 0.0)
    if avail_label < 12:                         # 数值过宽：让出 12px 给标签再截数值
        value = ellipsize(draw, value, value_font, max(12.0, width - gap - 12))
        value_w = _text_w(draw, value, value_font)
        avail_label = max(12.0, width - value_w - gap)
        if note_txt:
            note_txt, note_w = "", 0.0
    label = ellipsize(draw, label, label_font, avail_label)
    label_w = _text_w(draw, label, label_font)

    boxes = {"label": None, "note": None, "value": None}
    if label:
        draw.text((x, y), label, font=label_font, fill=label_fill)
        boxes["label"] = draw.textbbox((x, y), label, font=label_font)
    if note_txt:
        nx = x + label_w + 10
        draw.text((nx, y + 4), note_txt, font=note_font, fill=note_fill)
        boxes["note"] = draw.textbbox((nx, y + 4), note_txt, font=note_font)
    if value:
        vx = right - value_w
        draw.text((vx, y), value, font=value_font, fill=value_fill)
        boxes["value"] = draw.textbbox((vx, y), value, font=value_font)
    heights = [b[3] - b[1] for b in boxes.values() if b]
    return (max(heights) + 10 if heights else 8), boxes

def stripe_band(img, y0, h, alpha=22):
    dr = ImageDraw.Draw(img, "RGBA")
    x = -h
    while x < W:
        dr.polygon([(x, y0 + h), (x + h, y0), (x + h + 7, y0), (x + 7, y0 + h)],
                   fill=(248, 232, 0, alpha))
        x += 14
    dr.line([(0, y0), (W, y0)], fill=LINE2)
    dr.line([(0, y0 + h - 1), (W, y0 + h - 1)], fill=LINE2)

def progress_bar(dr, x, y, w, h, ratio, fill=GOLD):
    dr.rectangle([x, y, x + w, y + h], fill=(20, 30, 43), outline=LINE2)
    fw = int(w * max(0.0, min(1.0, ratio)))
    if fw > 0:
        dr.rectangle([x, y, x + fw, y + h], fill=fill)
    for k in (1, 2, 3):
        dr.line([x + int(w * k / 4), y, x + int(w * k / 4), y + h], fill=BG)

def section_label(dr, x, y, text, f_label):
    dr.rectangle([x, y + 2, x + 6, y + 2 + 28], fill=GOLD)
    dr.text((x + 16, y), text, font=f_label, fill=TEXT)


def _rich_font(font, tone):
    """Same-size bold face for highlighted runs (``F`` adds 2pt below 25)."""
    if tone not in ("yellow", "blue"):
        return font
    size = font.size
    return F(size - 2 if size <= 24 else size, True)


def _wrap_rich_runs(runs, max_width, font, dr):
    lines = []
    line = []
    width = 0
    for text, tone in runs or ():
        run_font = _rich_font(font, tone)
        for char in str(text or ""):
            if char in "\r":
                continue
            if char == "\n":
                if line:
                    lines.append(line)
                line, width = [], 0
                continue
            char_width = dr.textlength(char, font=run_font)
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


def _draw_rich_line(dr, x, y, line, font, color=DIM):
    for text, tone in line:
        run_font = _rich_font(font, tone)
        dr.text((x, y), text, font=run_font, fill=RICH_COLORS.get(tone, color))
        x += dr.textlength(text, font=run_font)
    return x


def _draw_rich_lines(dr, x, y, lines, font, line_height, color=DIM):
    for line in lines:
        _draw_rich_line(dr, x, y, line, font, color)
        y += line_height
    return y


def _money_amount(reward):
    if not reward:
        return "无"
    return (
        str(reward.get("name_cn") or reward.get("name") or "奖励")
        + (f" ×{reward.get('amount')}" if reward.get("amount") else "")
    )


def _draw_special_orders(dr, img, document, y):
    """Render directive/opportunity/minor channels as three independent cards."""
    models = orders.special_orders(document)
    dr.line([(PAD, y), (W - PAD, y)], fill=LINE2)
    y += 12
    dr.rectangle([PAD, y, PAD + 6, y + 30], fill=BLUE)
    dr.text((PAD + 18, y + 2), "独立指令通道", font=F(24, True), fill=TEXT)
    dr.text((PAD + 210, y + 10), "STRATEGIC ORDER CHANNELS", font=F(13), fill=MUTE)
    y += 44
    kinds = ("directive", "opportunity", "minor")
    gap = 12
    card_w = (W - PAD * 2 - gap * 2) // 3
    body_font = F(13)
    task_font = F(13)
    title_font = F(17, True)
    colors = {"directive": GOLD, "opportunity": BLUE, "minor": GREEN}
    prepared = []
    for kind in kinds:
        model = models.get(kind) or {}
        color = colors[kind]
        title_lines = wrap(model.get("title") or orders.CHANNEL_META[kind]["cn"], 24)
        body_lines = _wrap_rich_runs(
            model.get("rich_text") or (), card_w - 24, body_font, dr
        )
        task_lines = []
        for task in model.get("tasks") or []:
            target = task.get("planet_name") or task.get("type_cn") or "目标"
            suffix = f" · #{task['planet_index']}" if task.get("planet_index") is not None else ""
            task_lines.extend(wrap(
                f"{task.get('type_cn') or '目标'} {target} · {task.get('state_cn') or ''}{suffix}",
                30,
            ))
        meta_h = 22 if model.get("deadline_text") else 0
        empty_h = 34 if model.get("empty") else 0
        body_h = 18 * len(body_lines)
        locator_h = 15 * sum(1 for task in model.get("tasks") or [] if task.get("planet_index") is not None)
        task_h = (24 + 19 * len(task_lines) + locator_h) if task_lines else 0
        fallback_h = 18 * len(wrap("来源：重要指令附加目标 · 非官方独立 MINOR ORDER", 30)) if model.get("fallback") else 0
        card_h = 54 + 22 * len(title_lines) + meta_h + body_h + (10 if body_h else 0) + task_h + empty_h + fallback_h + 16
        prepared.append((model, color, title_lines, body_lines, task_lines, card_h))
    card_h = max((row[-1] for row in prepared), default=120)
    card_h = max(card_h, 190)
    for index, (model, color, title_lines, body_lines, task_lines, _height) in enumerate(prepared):
        x = PAD + index * (card_w + gap)
        dr.rectangle([x, y, x + card_w, y + card_h], fill=PANEL, outline=LINE)
        dr.rectangle([x, y, x + card_w, y + 4], fill=color)
        meta = orders.CHANNEL_META[kinds[index]]
        dr.text((x + 14, y + 12), meta["icon"] + " " + meta["cn"], font=F(17, True), fill=color)
        dr.text((x + 14, y + 34), meta["en"], font=F(10), fill=MUTE)
        state = model.get("status_cn") or "无记录"
        state_w = dr.textlength(state, font=F(12, True))
        dr.text((x + card_w - 12 - state_w, y + 15), state, font=F(12, True), fill=color)
        yy = y + 56
        for line in title_lines:
            dr.text((x + 14, yy), line, font=title_font, fill=TEXT)
            yy += 22
        if model.get("deadline_text"):
            dr.text((x + 14, yy), model["deadline_text"], font=F(12), fill=GOLD2)
            yy += 22
        if model.get("empty"):
            dr.text((x + 14, yy + 4), "当前无" + meta["cn"], font=F(15, True), fill=DIM)
            dr.text((x + 14, yy + 27), "发布满 7 天后自动退场。", font=F(12), fill=MUTE)
        else:
            yy = _draw_rich_lines(dr, x + 12, yy, body_lines, body_font, 18, color=DIM)
            if body_lines:
                yy += 8
            if task_lines:
                dr.line([(x + 14, yy), (x + card_w - 14, yy)], fill=LINE)
                yy += 8
                dr.text((x + 14, yy), "目标", font=F(12, True), fill=color)
                yy += 20
                for line in task_lines:
                    dr.text((x + 14, yy), line, font=task_font, fill=TEXT)
                    yy += 19
                for task in model.get("tasks") or []:
                    if task.get("planet_index") is None:
                        continue
                    locator = f"planet.html?idx={task['planet_index']}"
                    dr.text((x + 14, yy), locator, font=F(10), fill=MUTE)
                    yy += 15
        if model.get("fallback"):
            note = "来源：重要指令附加目标 · 非官方独立 MINOR ORDER"
            note_lines = wrap(note, 30)
            note_y = y + card_h - 12 - 15 * len(note_lines)
            for line in note_lines:
                dr.text((x + 14, note_y), line, font=F(10), fill=BRASS)
                note_y += 15
    return y + card_h + 18

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
        content = shared.major_order_content(data)
        brief = content.get("text") or ""
        brief_runs = content.get("rich_text") or []
        war = data.get("war") or {}
        online = distribution["total"]
        campaigns = data.get("campaigns") or []
        news = orders.filtered_news(data)

        f_title = F(42, True); f_h2 = F(25, True); f_h3 = F(21, True)
        f_body = F(20); f_small = F(17); f_tiny = F(14)
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
            brief_lines = _wrap_rich_runs(brief_runs, W - PAD * 2 - 36, f_body, dr)
            head_h = 52
            # brief 区：16 顶距 + 30/行 + 10 底距 + 分隔线 8
            brief_h = (16 + 30 * len(brief_lines) + 18) if brief_lines else 0
            task_h = sum(126 for _ in tasks)   # 每任务：任务名 38 + 进度条 24 + 数值行 64
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
                yy = _draw_rich_lines(dr, PAD + 18, yy, brief_lines, f_body, 30, color=DIM)
                yy += 10
                dr.line([(PAD + 18, yy), (W - PAD - 18, yy)], fill=LINE)
                yy += 8
            for i, t in enumerate(tasks):
                name = f"任务 {i + 1}：{t.get('name') or t.get('action') or ''}"
                dr.text((PAD + 18, yy), name, font=f_h3, fill=TEXT)
                req = t.get("requirement") or {}
                if req.get("type") == "equipment" and (req.get("name") or req.get("en")):
                    equip = "限定：" + (req.get("name") or req.get("en"))
                    ew = dr.textlength(equip, font=f_small)
                    dr.rectangle([W - PAD - 18 - ew - 20, yy - 3, W - PAD - 18, yy + 29],
                                 fill=(46, 42, 10), outline=BRASS)
                    dr.text((W - PAD - 18 - ew - 10, yy + 1), equip, font=f_small, fill=GOLD2)
                yy += 38
                goal, prog = t.get("goal"), t.get("progress")
                percentage = shared.percent(prog, goal)
                r = (percentage or 0) / 100.0
                done = percentage == 100
                progress_bar(dr, PAD + 18, yy, W - PAD * 2 - 36, 16, r, fill=GREEN if done else GOLD)
                yy += 26
                pt = ("未知" if percentage is None else "已完成" if done else f"{r * 100:.1f}%")
                dr.text((PAD + 18, yy), pt, font=f_h3, fill=GREEN if done else GOLD)
                sub = f"进度 {shared.fmt_big_num(prog)} / 目标 {shared.fmt_big_num(goal)}"
                sw2 = dr.textlength(sub, font=f_small)
                dr.text((W - PAD - 18 - sw2, yy + 6), sub, font=f_small, fill=MUTE)
                yy += 62
            y += panel_h + 20

        y = _draw_special_orders(dr, img, data, y)

        # ══ 战区玩家分布 ══
        if dist_hist:
            latest = dist_hist[-1]
            total = latest.get("totalPlayers") or 0
            fac = latest.get("factions") or {}
            rows = [("超级地球", fac.get("Humans"), BLUE),
                    ("终结族", fac.get("Terminids"), (245, 197, 24)),
                    ("机器人", fac.get("Automatons"), (214, 128, 109)),
                    ("光能者", fac.get("Illuminate"), (207, 100, 248))]
            panel_h = 60 + 58 * len(rows) + 44
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
                yy += 58
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
                value = n.get("translated") or n.get("original") or n.get("message") or ""
                blocks.append(_wrap_rich_runs(
                    shared.rich_text_runs(value), W - PAD * 2 - 52, f_body, dr
                ))
            panel_h = 56 + sum(18 + 28 * len(ls) + 16 for ls in blocks) + 4
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 14, "最新要闻", f_h2)
            yy = y + 56
            for lines in blocks:
                first = True
                for ln in lines:
                    if first:
                        dr.rectangle([PAD + 18, yy + 11, PAD + 24, yy + 29], fill=BRASS)
                        _draw_rich_line(dr, PAD + 34, yy, ln, f_body, color=TEXT)
                        first = False
                    else:
                        _draw_rich_line(dr, PAD + 34, yy, ln, f_body, color=DIM)
                    yy += 28
                yy += 16
            y_after_news = y + panel_h + 20

        # ══ 页脚 ══
        y = (y_after_news if y_after_news else y) + 8
        dr.line([(PAD, y), (W - PAD, y)], fill=LINE)
        dr.text((PAD, y + 16), "数据来源：主站战况快照 · 源时间见页脚", font=f_tiny, fill=MUTE)
        ft = "发送 /帮助 /dss /战役 /查询 查看更多"
        fw = dr.textlength(ft, font=f_tiny)
        dr.text((W - PAD - fw, y + 16), ft, font=f_tiny, fill=MUTE)
        dr.text((PAD, y + 42), "真理部 · 管理式民主 · 本站与 Arrowhead Game Studios 无隶属关系",
                font=f_tiny, fill=(120, 138, 156))
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
        special_models = orders.special_orders(document)
        revision = "mo-card-v2:" + hashlib.sha256(json.dumps({
            "revision": order.get("revision"),
            "text": order.get("text"),
            "tasks": tasks,
            "deadline": assignment.get("deadline"),
            "special_orders": special_models,
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
            y = _draw_special_orders(dr, img, document, y)
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


def _render_cached(out_path, meta_path, rev, draw_fn, page_info=None, column_height=2100):
    meta = shared._snapshot.get() or {}
    # layout-v4-wide：多页合并单图（自左向右分区）+ 战略订单墙布局；版本号变化使旧分页缓存全部失效
    digest = hashlib.sha256((
        str(rev)
        + json.dumps(meta.get("_hd2", {}), sort_keys=True)
        + "layout-v4-wide"
        + str(column_height)
        + (str(page_info) if page_info else "")
    ).encode()).hexdigest()[:16]
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
        paths = save_pages(
            img,
            y + 20,
            out_path,
            meta,
            page_info=page_info,
            column_height=column_height,
        )
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
                planet_name = pl.get("name") or ""
                en = planet_name.upper()
                cn = smcn.get(shared.planet_key(planet_name)) or cp.get("name") or en
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

            ROW_H = 56
            panel_h = 54 + ROW_H * len(rows) + 36
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "全部战线（按在线人数排序）", F(25, True))
            yy = y + 54
            bw_x, bw_w = PAD + 320, W - PAD * 2 - 610
            for ri, (cn, sector, fac, ratio, players, state) in enumerate(rows):
                if ri % 2:  # 斑马纹：偶数行微亮，辅助横向追踪
                    dr.rectangle([PAD + 6, yy - 8, W - PAD - 6, yy + ROW_H - 8], fill=PANEL2)
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
                dr.text((PAD + 32, yy + 26), fac + " · " + state["status"] + (" · 截止 " + str(state["deadline"])[:16] if state["defending"] and state["deadline"] else ""), font=F(14), fill=color)
                yy += ROW_H
            dr.text((PAD + 18, y + panel_h - 28),
                    "解放/防御使用对应战役进度 · 数据不足显示未知",
                    font=F(13), fill=(120, 138, 156))
            return y + panel_h + 12

        return _render_cached(out_path, meta_path, rev, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def _dss_variables_source():
    """Prefer the live site table, then use the bundled parity snapshot."""
    try:
        value = _site_get(
            "https://jerry114514.github.io/HD2-Galatic_war-Map/tables/hd2_variables.json"
        )
        if isinstance(value, dict) and isinstance(value.get("categories"), list):
            return value
    except Exception:
        pass
    for path in _table_candidates("hd2_variables.json"):
        try:
            with open(path, encoding="utf-8") as stream:
                value = json.load(stream)
            if isinstance(value, dict):
                return value
        except (OSError, ValueError, TypeError):
            continue
    return {}


def _dss_passive(variables):
    for category in (variables or {}).get("categories") or []:
        if category.get("key") != "tactical_action":
            continue
        for entry in category.get("entries") or []:
            if 1238 in (entry.get("effect_ids") or []):
                return entry
    return {}


def _dss_snapshot_label(dss):
    timestamp = (
        (dss or {}).get("donationUpdatedAtMs")
        if "donationUpdatedAtMs" in (dss or {})
        else (dss or {}).get("clientTimeMs")
    )
    try:
        timestamp = float(timestamp)
        if timestamp <= 0:
            raise ValueError
        label = datetime.fromtimestamp(timestamp / 1000).astimezone().strftime("%m-%d %H:%M")
        suffix = "（上次成功快照）" if (dss or {}).get("donationSource") == "snapshot" else ""
        return f"捐赠数据截至 {label}{suffix}"
    except (TypeError, ValueError, OSError):
        return "捐赠数据时间未知"


def _dss_hero_file(model):
    filename = os.path.basename((model or {}).get("image") or shared.DSS_HERO_FALLBACK)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "dss-hero", filename)
    return path if os.path.isfile(path) and os.path.getsize(path) > 0 else None


def _dss_jump_label(dss):
    end = shared.number((dss or {}).get("electionEndWarTime"))
    current = shared.number((dss or {}).get("currentWarTime"))
    if end is None or current is None:
        return "空间站即将移动：等待信息更新"
    seconds = max(0, int(end - current))
    hours, remain = divmod(seconds, 3600)
    minutes, seconds = divmod(remain, 60)
    return f"空间站即将移动：{hours} 小时 {minutes} 分 {seconds} 秒"


def _dss_action_tone(model):
    if (model or {}).get("is_active"):
        return GREEN
    if (model or {}).get("is_cooldown"):
        return RED
    return GOLD


def _draw_dss_progress(img, dr, x, y, width, height, progress, tone):
    """Main-site-style contribution ratio plus a striped progress strip."""
    ratio = max(0.0, min(1.0, (progress or 0) / 100.0))
    filled = max(0, int(width * ratio))
    dr.rectangle([x, y, x + width, y + height], fill=(7, 9, 12), outline=LINE2)
    if filled:
        layer = Image.new("RGB", (width, height), tone)
        ldr = ImageDraw.Draw(layer)
        for offset in range(-height, width + height, 18):
            ldr.polygon(
                [
                    (offset, height),
                    (offset + height // 2, 0),
                    (offset + height // 2 + 7, 0),
                    (offset + 7, height),
                ],
                fill=(26, 14, 8),
            )
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rectangle([0, 0, filled, height], fill=255)
        img.paste(layer, (x, y), mask)
    dr.rectangle([x, y, x + width, y + height], outline=LINE2)
    label = "未知" if progress is None else f"{progress:.1f}%"
    label_w = dr.textlength(label, font=F(15, True)) + 22
    bx = x + (width - label_w) / 2
    dr.rectangle([bx, y + 3, bx + label_w, y + height - 3], fill=(0, 0, 0))
    dr.text((bx + 11, y + 4), label, font=F(15, True), fill=TEXT)


def _draw_dss_action(img, dr, y, model, position, total):
    """One full-width action: hero image, name, description and donation progress."""
    x0, x1 = PAD, W - PAD
    width = x1 - x0
    image_h = 250
    desc_lines = wrap(model.get("description") or "暂无本地中文说明。", 48)
    resources_text = "；".join(model.get("resource_lines") or [])
    resource_lines = wrap(resources_text, 78) if resources_text else []
    resource_h = 24 * len(resource_lines) if resource_lines else 0
    card_h = image_h + 210 + 29 * len(desc_lines) + resource_h
    tone = _dss_action_tone(model)
    dr.rectangle([x0, y, x1, y + card_h], fill=(24, 29, 37), outline=LINE2)
    image_box = (x0 + 4, y + 4, x1 - 4, y + 4 + image_h)
    hero = _crop_cover(
        _dss_hero_file(model),
        (image_box[2] - image_box[0], image_box[3] - image_box[1]),
    )
    if hero:
        img.paste(hero, (image_box[0], image_box[1]))
    else:
        _campaign_placeholder(dr, image_box, "DSS")
    overlay = Image.new("RGBA", (image_box[2] - image_box[0], 46),
                        (4, 8, 13, 205))
    img.paste(overlay, (image_box[0], image_box[3] - overlay.height), overlay)
    dr.text(
        (image_box[0] + 16, image_box[3] - 34),
        f"ACTION {position:02d}/{total:02d}",
        font=F(15, True),
        fill=TEXT,
    )
    phase_w = dr.textlength(model.get("phase") or "", font=F(17, True))
    dr.rectangle(
        [image_box[2] - phase_w - 34, image_box[3] - 48,
         image_box[2] - 14, image_box[3] - 16],
        fill=BG,
        outline=tone,
    )
    dr.text(
        (image_box[2] - phase_w - 24, image_box[3] - 42),
        model.get("phase") or "",
        font=F(17, True),
        fill=tone,
    )

    yy = y + image_h + 20
    dr.rectangle([x0 + 18, yy + 5, x0 + 23, yy + 31], fill=GOLD)
    dr.text((x0 + 34, yy), "战术行动", font=F(14, True), fill=GOLD2)
    yy += 32
    dr.text((x0 + 18, yy), model.get("name") or "未知战术行动",
            font=F(30, True), fill=TEXT)
    yy += 46
    for line in desc_lines:
        dr.text((x0 + 18, yy), line, font=F(19), fill=DIM)
        yy += 29
    yy += 14

    progress = model.get("progress")
    if resource_lines:
        for line in resource_lines:
            dr.text((x0 + 18, yy), line, font=F(15), fill=MUTE)
            yy += 24
    else:
        dr.text((x0 + 18, yy), "捐赠资源限制未下发", font=F(15), fill=MUTE)
        yy += 24
    dr.line([(x0 + 18, yy), (x1 - 18, yy)], fill=LINE)
    yy += 14
    dr.text((x0 + 18, yy), "贡献度 / CONTRIBUTION", font=F(14, True), fill=MUTE)
    yy += 30
    _draw_dss_progress(img, dr, x0 + 18, yy, width - 36, 24, progress, tone)
    return y + card_h + 16


def render_dss_card(out_dir=None):
    """/dss 图片卡：停靠信息 + 同一视图内完整展示全部 DSS 战术行动。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "dss_card.png")
        data = _site_get(DATA_URL)
        dss = data.get("dss") or {}
        variables = _dss_variables_source()
        models = shared.dss_action_models(dss, variables)
        revision = hashlib.sha256(json.dumps(
            {"dss": dss, "variables": variables},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()).hexdigest()
        rev = "dss-three-v6:" + revision
        meta_path = out_path + ".meta"

        def draw(dr, img):
            planets = {
                row.get("index"): row
                for row in data.get("planets") or []
                if isinstance(row, dict) and row.get("index") is not None
            }
            planet = planets.get(dss.get("planetIndex")) or {}
            planet_en = str(planet.get("name") or "")
            planet_cn = (
                _starmap_cn_map().get(shared.planet_key(planet_en))
                or planet_en
                or "未停靠"
            )
            y = _hud_head(dr, img, "民主空间站", "DEMOCRACY SPACE STATION")
            station_h = 112
            dr.rectangle([PAD, y, W - PAD, y + station_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "当前停靠", F(25, True))
            jump = _dss_jump_label(dss)
            jump_w = dr.textlength(jump, font=F(15, True)) + 24
            jump_box = [W - PAD - jump_w - 12, y + 14,
                        W - PAD - 12, y + 46]
            dr.rectangle(jump_box, fill=(246, 210, 0), outline=BRASS)
            dr.text((jump_box[0] + 12, jump_box[1] + 6), jump,
                    font=F(15, True), fill=(15, 18, 22))
            dr.text((PAD + 18, y + 50), planet_cn, font=F(34, True), fill=TEXT)
            location = "未停靠"
            if dss:
                location = " · ".join(
                    value for value in (planet_en, planet.get("sector")) if value
                )
            dr.text((PAD + 18, y + 88), location or "星球坐标未下发",
                    font=F(15), fill=MUTE)
            snapshot = _dss_snapshot_label(dss)
            snapshot_w = dr.textlength(snapshot, font=F(14))
            dr.text((W - PAD - 18 - snapshot_w, y + 88), snapshot,
                    font=F(14), fill=MUTE)
            y += station_h + 16

            heading_h = 52
            dr.rectangle([PAD, y, W - PAD, y + heading_h], fill=(20, 30, 43), outline=LINE2)
            dr.text((PAD + 18, y + 13), "战术行动", font=F(24, True), fill=TEXT)
            dr.text((PAD + 140, y + 20), "DSS TACTICAL ACTIONS",
                    font=F(13), fill=MUTE)
            count = f"{len(models)} 项同屏"
            count_w = dr.textlength(count, font=F(15, True))
            dr.text((W - PAD - 18 - count_w, y + 17), count,
                    font=F(15, True), fill=GOLD)
            y += heading_h + 12

            if models:
                for index, model in enumerate(models, 1):
                    y = _draw_dss_action(img, dr, y, model, index, len(models))
            else:
                empty_h = 92
                dr.rectangle([PAD, y, W - PAD, y + empty_h], fill=PANEL, outline=LINE)
                dr.text((PAD + 18, y + 18), "当前无进行中的战术行动",
                        font=F(22, True), fill=DIM)
                dr.text((PAD + 18, y + 53), "接口未下发可展示的募捐、激活或冷却行动。",
                        font=F(16), fill=MUTE)
                y += empty_h + 16

            passive = _dss_passive(variables)
            if passive:
                passive_cn = shared._dss_display_name(
                    passive.get("cn") or passive.get("name") or "行动支持"
                )
                passive_desc = passive.get("desc") or (
                    "DSS 停靠在这颗星球附近，一定程度上有助于该星球的解放战争进程。"
                )
                passive_lines = wrap(passive_desc, 58)[:2]
                passive_h = 72 + 22 * len(passive_lines)
                dr.rectangle([PAD, y, W - PAD, y + passive_h], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, PAD + 5, y + passive_h], fill=BLUE)
                dr.text((PAD + 18, y + 12), "常驻被动", font=F(14, True), fill=MUTE)
                dr.text((PAD + 100, y + 8), passive_cn, font=F(22, True), fill=TEXT)
                yy = y + 42
                for line in passive_lines:
                    dr.text((PAD + 18, yy), line, font=F(15), fill=DIM)
                    yy += 22
                y += passive_h + 14
            return y

        return _render_cached(
            out_path,
            meta_path,
            rev,
            draw,
            column_height=3200,
        )
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

    def merge_variables(value, overwrite=False):
        if not isinstance(value, dict):
            return
        for cat in value.get("categories") or []:
            nm = cat.get("name") or cat.get("key") or ""
            for entry in cat.get("entries") or []:
                label = entry.get("cn") or entry.get("name") or ""
                for eid in entry.get("effect_ids") or []:
                    if eid is None:
                        continue
                    if overwrite or eid not in cn:
                        if label:
                            cn[eid] = label
                        grp[eid] = nm

    try:
        merge_variables(_site_get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/hd2_variables.json"))
    except Exception:
        pass
    try:
        path = next((p for p in _table_candidates("hd2_variables.json") if os.path.isfile(p)), "")
        if path:
            with open(path, encoding="utf-8") as stream:
                merge_variables(json.load(stream), overwrite=True)
    except (OSError, ValueError, TypeError):
        pass
    try:
        with open(os.path.join(CACHE_DIR, "effect_id_cn.json"), encoding="utf-8") as stream:
            fb = json.load(stream)
    except Exception:
        try:
            fb = _get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/effect_id_cn.json")
            try:
                with open(os.path.join(CACHE_DIR, "effect_id_cn.json"), "w", encoding="utf-8") as stream:
                    json.dump(fb, stream, ensure_ascii=False)
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
    try:
        path = next((p for p in _table_candidates("effect_id_cn.json") if os.path.isfile(p)), "")
        if path:
            with open(path, encoding="utf-8") as stream:
                local = json.load(stream)
            for key, label in local.items():
                try:
                    eid = int(key)
                except (TypeError, ValueError):
                    continue
                if eid not in cn and label:
                    cn[eid] = label
    except (OSError, ValueError, TypeError):
        pass
    return cn, grp


def _dedupe_effect_items(eids, cn_map, grp):
    """Collapse multiple effect IDs that share the same localized label."""
    seen, items = set(), []
    for eid in eids or ():
        if eid is None:
            continue
        label = cn_map.get(eid) or ("#" + str(eid))
        if label in seen:
            continue
        seen.add(label)
        items.append((label, grp.get(eid) or ""))
    return items


def _effect_descriptions():
    """效果 ID → {desc, group}：本地 tables/hd2_variables.json（离线可用，不联网）。"""
    table, path = {}, next((p for p in _table_candidates("hd2_variables.json") if os.path.isfile(p)), "")
    if not path:
        return table
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError, TypeError):
        return table
    if not isinstance(value, dict):
        return table
    for cat in value.get("categories") or []:
        group = cat.get("name") or cat.get("key") or ""
        for entry in cat.get("entries") or []:
            desc = str(entry.get("desc") or "").strip()
            if not desc:
                continue
            for eid in entry.get("effect_ids") or []:
                if isinstance(eid, int) and eid not in table:
                    table[eid] = {"desc": desc, "group": group}
    return table


def _effect_items(eids, cn_map=None, grp=None, desc_map=None):
    """activeEffects → [{label, group, desc}]；同一中文名只保留一条，缺描述时用同名项补齐。"""
    cn_map, grp = cn_map or {}, grp or {}
    if desc_map is None:
        desc_map = _effect_descriptions()
    items, index = [], {}
    for eid in eids or ():
        if eid is None:
            continue
        info = desc_map.get(eid) or {}
        label = cn_map.get(eid) or ("#" + str(eid))
        desc = info.get("desc") or ""
        if label in index:
            row = items[index[label]]
            if not row["desc"] and desc:
                row["desc"] = desc
            continue
        index[label] = len(items)
        items.append({"label": label,
                      "group": grp.get(eid) or info.get("group") or "",
                      "desc": desc})
    return items


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


# ═══════════════════════════════════════════════════════════════
# 星球指挥台 A 区星球卡片（左列）与右列板块
# ═══════════════════════════════════════════════════════════════

# A 区版式版本：参与渲染缓存键，版式改动后旧布局图片自然失效
PLANET_CARD_LAYOUT_VERSION = 2
PLANET_CARD_WIDTH = 372
_CARD_PAD = 14
_ASSET_BASE = "https://jerry114514.github.io/HD2-Galatic_war-Map/assets/"
# 主站 effect-icons 里可用的 PNG 子集（其余是 SVG，Pillow 渲染不了，直接跳过）
_EFFECT_ICON_FILES = (
    ("无票者", "voteless.png"),
    ("入侵", "invasion_fleet.png"),
    ("蜂巢", "hive_lord.png"),
    ("科学", "center_of_science.png"),
    ("工厂", "factory_hub.png"),
    ("TCS", "tcs.png"),
)


def _faction_badge(dr, x, y, size, faction, color):
    """阵营徽标：主站 faction-icons 只有 SVG，Pillow 侧按阵营自绘几何图形兜底。"""
    r = size / 2.0
    cx, cy = x + r, y + r
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    if faction == "光能者":
        dr.ellipse([cx - r * .52, cy - r * .52, cx + r * .52, cy + r * .52], fill=BG)
        dr.ellipse([cx - r * .26, cy - r * .26, cx + r * .26, cy + r * .26], fill=color)
    elif faction == "终结族":
        dr.polygon([(cx, cy - r * .58), (cx + r * .60, cy + r * .42),
                    (cx - r * .60, cy + r * .42)], fill=BG)
    elif faction == "机器人":
        dr.rectangle([cx - r * .44, cy - r * .44, cx + r * .44, cy + r * .44], fill=BG)
    else:                                    # 超级地球 / 未知阵营
        dr.ellipse([cx - r * .34, cy - r * .34, cx + r * .34, cy + r * .34], fill=BG)


def _effect_icon_file(label):
    """词条小图标（主站 effect-icons 的 PNG 子集）→ 缓存路径；没有则 None。"""
    text = str(label or "")
    for key, fn in _EFFECT_ICON_FILES:
        if key not in text:
            continue
        cache_file = os.path.join(CACHE_DIR, "effect_icons", fn)
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 200:
            return cache_file
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            req = urllib.request.Request(_ASSET_BASE + "effect-icons/" + fn,
                                         headers={"User-Agent": "HD2Bot/1.0"})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
                data = r.read()
            if len(data) < 200:
                return None
            open(cache_file, "wb").write(data)
            return cache_file
        except Exception:
            return None
    return None


def _eta_text(hours, progress=None):
    """预计完成文案：与主站 duration() 同粒度（天/小时/分钟，超过 30 天报不可完成）。"""
    if hours is None:
        return "无法完成", "nonpositive"
    left = max(0., 100. - (progress if progress is not None else 0.))
    if left <= 0:
        return "已完成", "complete"
    if hours > 720:
        return "超过 30 天", "too_long"
    minutes = max(1, int(round(hours * 60)))
    if minutes >= 1440:
        return f"{minutes // 1440} 天 {minutes % 1440 // 60} 时", "ok"
    if minutes >= 60:
        return f"{minutes // 60} 时 {minutes % 60} 分", "ok"
    return f"{minutes} 分钟", "ok"


def planet_rate_view(data, planet, state=None):
    """速率口径（任务 C）：优先共享层 planet_rate_state（实测优先），缺失时推演兜底。"""
    state = state or shared.campaign_state(data, planet)
    fn = getattr(shared, "planet_rate_state", None)
    if callable(fn):
        try:
            view = fn(data, planet)
            if isinstance(view, dict):
                return view
        except (OSError, ValueError, TypeError):
            pass
    maxhp = state.get("max_health") or 0
    net = state.get("rate")
    resistance = state.get("resistance")
    players = planet.get("players") or 0
    impact = data.get("impactMultiplier") or 0
    online = ((data.get("war") or {}).get("statistics") or {}).get("playerCount") or 0
    share = players / online * 100 if online else None
    measured = bool(state.get("measured"))
    eta_hours = ((100 - (state.get("progress") or 0)) / net) if net and net > .0005 else None
    eta_text, eta_reason = _eta_text(eta_hours, state.get("progress"))
    return {
        "net": net,
        "source": "measured_campaign" if measured else "estimated",
        "source_label": "实测 · 防守速率" if measured else "推演",
        "measured": measured,
        "current": (net + resistance) if net is not None and resistance is not None else None,
        "actual": net if measured else None,
        "res_eq": resistance * maxhp / 1_000_000 if resistance is not None and maxhp else None,
        "eta_hours": eta_hours, "eta_text": eta_text, "eta_reason": eta_reason,
        "threshold": None, "threshold_met": None, "threshold_state": "unknown",
        "threshold_text": None, "threshold_note": None,
        "player_share": share,
        "strength_label": (None if state.get("defending") else
                           ("低" if (resistance or 0) < 2 else
                            "中" if (resistance or 0) < 4 else "高")),
        "progress": state.get("progress"), "max_health": maxhp, "hours": None,
        "online_total": online, "why": None,
    }


def _region_rows(planet, impact):
    """下属区域行：A 区摘要与 E 区表格共用这一份口径（不在绘图里另算一套）。"""
    rt_map, rzh_map = _region_tables()
    rst = rt_map.get(str(planet.get("index"))) or {}
    regions = rst.get("regions") or []
    if not regions:
        return []
    rzh = rzh_map.get(str(planet.get("index"))) or {}
    live_regions = planet.get("regions") or []

    def _live(ridx):
        for row in live_regions:
            if row.get("regionIndex") == ridx:
                return row
        if ridx < len(live_regions) and live_regions[ridx].get("regionIndex") is None:
            return live_regions[ridx]
        return None

    ours_pl = str(planet.get("currentOwner") or "").lower() == "humans"
    rows = []
    for k, rg in enumerate(regions):
        lv = _live(k) or {}
        flags = lv.get("flags") or 0
        factory = str(rg.get("region_type") or "").lower() == "megafactory" or bool(flags & 1)
        size = rg.get("size") or ""
        if factory:
            type_cn, stars = "超大型工厂", 4
        else:
            type_cn = _REGION_TYPE_ZH.get(size, size or "区域")
            stars = _REGION_STARS.get(size, 0)
        progress = None
        if lv.get("maxHealth"):
            progress = max(0.0, min(100.0, (1 - (lv.get("health") or 0) / lv["maxHealth"]) * 100))
        if progress is None and ours_pl:
            progress = 100.0                    # 己方星球官方不下发 regions → 按已解放（主站口径）
        if lv.get("owner"):
            owner_cn = FACTION_CN.get(lv["owner"], lv["owner"])
        elif ours_pl:
            owner_cn = "超级地球"                # 静态表 owner 是抓取当时的阵营，己方一律记超级地球
        else:
            owner_cn = FACTION_CN.get(rg.get("owner") or "", rg.get("owner") or "—")
        rate_txt = "—"
        rate = _region_rate(planet, lv, impact)
        if rate:
            net, _res, _measured = rate
            if progress is not None and net is not None:
                left = max(0.0, 100.0 - progress)
                if net > 0.0005:
                    h = left / net
                    eta = ("%d 小时" % round(h)) if h <= 24 else (
                        "%d 天 %d 时" % (int(h // 24), int(h % 24)) if h <= 720 else "超过 30 天")
                else:
                    eta = "不会解放"
                rate_txt = ("+" if net >= 0 else "−") + "%.2f%%/h · %s" % (abs(net), eta)
        en_name = rg.get("name") or ""
        rows.append({
            "name_cn": rzh.get(en_name) or en_name or "区域",
            "en_name": en_name,
            "owner_cn": owner_cn,
            "type_cn": type_cn,
            "stars": stars,
            "factory": factory,
            "players": lv.get("players"),
            "progress": progress,
            "rate_txt": rate_txt,
        })
    return rows


def _panel_frame(dr, x, y, w, h, title, color=GOLD):
    """右列面板骨架：底色 + 顶部色条 + 分区标题。"""
    dr.rectangle([x, y, x + w, y + h], fill=PANEL, outline=LINE)
    dr.rectangle([x, y, x + w, y + 3], fill=color)
    section_label(dr, x + 18, y + 13, title, F(23, True))


def _objectives_block(dr, objectives, width):
    """战术目标：先按列宽折行算高度（量→画两段式），返回 (layout, height)。"""
    text_w = width - 64 - 18
    layout, body = [], 0
    for objective in objectives:
        lines = wrap_text(dr, objective.get("text") or "", F(17), text_w)
        links = objective.get("target_links") or []
        layout.append((objective, lines, links))
        body += 34 + 23 * len(lines) + (22 if links else 0) + 22 * len(links) + 14
    return layout, 54 + (body if body else 76)


def _tactical_panel(dr, x, y, w, layout, height):
    """战术目标面板（B 区）：内容与旧版一致，只是宽度收到右列。"""
    _panel_frame(dr, x, y, w, height, "战术目标", color=BLUE)
    count_text = f"{len(layout)} 项可执行" if layout else "通道待命"
    cw = dr.textlength(count_text, font=F(14, True))
    dr.text((x + w - 18 - cw, y + 18), count_text, font=F(14, True), fill=BLUE)
    if not layout:
        dr.text((x + 18, y + 54), "当前无战术目标", font=F(19, True), fill=DIM)
        dr.text((x + 18, y + 82), "该星球暂未形成可执行的包围、对弈或星域解放条件。",
                font=F(15), fill=MUTE)
        return
    text_w = w - 64 - 18
    oy = y + 54
    for index, (objective, lines, links) in enumerate(layout, 1):
        dr.text((x + 18, oy), f"{index:02d}", font=F(18, True), fill=BLUE)
        dr.text((x + 64, oy - 4), ellipsize(dr, objective.get("title") or "", F(22, True), text_w),
                font=F(22, True), fill=TEXT)
        oy += 34
        for line in lines:
            dr.text((x + 64, oy), line, font=F(17), fill=DIM)
            oy += 23
        target_name = "、".join(
            link.get("name") or ("#" + str(link.get("index"))) for link in links
        )
        if target_name:
            dr.text((x + 64, oy),
                    ellipsize(dr, "目标星球：" + target_name, F(16, True), text_w),
                    font=F(16, True), fill=TEXT)
            oy += 22
        for link in links:
            # 窄列放不下完整 URL：去掉协议头再截断，保留可辨认的路径
            url_txt = str(link.get("url") or "")
            if url_txt.startswith("https://"):
                url_txt = url_txt[len("https://"):]
            dr.text((x + 64, oy), ellipsize(dr, url_txt, F(14), text_w),
                    font=F(14), fill=BLUE)
            oy += 22
        oy += 14


HEALTH_PANEL_HEIGHT = 50 + 34 * 3 + 10


def _health_panel(dr, x, y, w, rows):
    """血量与在线（C 区）：只放卡片没有的字段，双列三行。

    rows 每项是 (标签, 数值) 或 (标签, 数值, 副标签)；副标签放不下时由
    label_value_row 自动省略，不会和标签/数值重叠。
    """
    _panel_frame(dr, x, y, w, HEALTH_PANEL_HEIGHT, "血量与在线", color=GOLD)
    colw = (w - 36) // 2
    for i, row in enumerate(rows):
        label, value = row[0], row[1]
        note = row[2] if len(row) > 2 else None
        kx = x + 18 + (i % 2) * (colw + 18)
        ky = y + 50 + (i // 2) * 34
        label_value_row(dr, kx, ky, colw - 18, label, value, F(13), F(15, True),
                        note=note, note_font=F(12), gap=8, label_reserve_ratio=0.32,
                        note_fill=(120, 138, 156))


def _stats_block(dr, data, planet, state, width):
    """星球统计（D 区）：返回 (rows, present, height)。行 = (标签, 数值, 副标签)。"""
    stats = None
    for row in ((data.get("war_stats") or {}).get("planets_stats")) or []:
        if isinstance(row, dict) and row.get("planetIndex") == planet.get("index"):
            stats = row
            break
    if not stats:
        return [], False, 114
    won, lost = stats.get("missionsWon") or 0, stats.get("missionsLost") or 0
    total = won + lost
    rate = (won / total * 100) if total else 0
    shots, hits = stats.get("bulletsFired") or 0, stats.get("bulletsHit") or 0
    kills = [("终结族", stats.get("bugKills") or 0), ("机器人", stats.get("automatonKills") or 0),
             ("光能者", stats.get("illuminateKills") or 0)]
    owner_key = str(planet.get("currentOwner") or "").lower()
    key_map = {"terminids": "终结族", "automaton": "机器人", "illuminate": "光能者"}
    main_kill = next((k for k in kills if key_map.get(owner_key) == k[0]), None) \
        or max(kills, key=lambda k: k[1])
    others = [k for k in kills if k is not main_kill and k[1] > 0]
    rows = [
        ("任务完成", fmt(won), f"成功率 {rate:.1f}%" if total else "—"),
        ("任务失败", fmt(lost), f"占 {100 - rate:.1f}%" if total else "—"),
        ("开火", fmt(shots), "累计发数"),
        ("命中", fmt(hits), (f"命中/开火 {hits / shots * 100:.1f}%" if shots else "—")),
        (main_kill[0] + "击杀", fmt(main_kill[1]),
         "当前控制方" if owner_key else "历史主要交战阵营"),
        ("阵亡绝地潜兵", fmt(stats.get("deaths") or 0),
         "其他历史 " + " · ".join(k[0] + " " + fmt(k[1]) for k in others) if others else ""),
    ]
    row_h = 46 if width < 430 else 40          # 窄列时副标签另起一行，行高加一档
    return rows, True, 50 + row_h * 3 + 10


def _stats_panel(dr, x, y, w, rows, present, height):
    """星球统计面板：窄列时副标签下沉一行，避免与数值抢位置。"""
    _panel_frame(dr, x, y, w, height, "星球统计（本场战争累计）", color=GOLD)
    if not present:
        for i, line in enumerate(wrap_text(dr, "该星球暂无累计统计（官方 war_stats 里没有这一条；通常是从未被打过的星球）。",
                                           F(15), w - 36)):
            dr.text((x + 18, y + 54 + i * 22), line, font=F(15), fill=DIM)
        return
    colw = (w - 36) // 2
    row_h = 46 if w < 430 else 40
    for i, (label, value, note) in enumerate(rows):
        kx = x + 18 + (i % 2) * (colw + 18)
        ky = y + 50 + (i // 2) * row_h
        narrow_note = bool(note) and w < 430
        label_value_row(dr, kx, ky, colw - 18, label, value, F(15), F(21, True),
                        note=None if narrow_note else note, note_font=F(13), gap=12,
                        note_fill=(120, 138, 156),
                        value_fill=GOLD if "击杀" in label else TEXT)
        if narrow_note:
            dr.text((kx, ky + 24), ellipsize(dr, note, F(11), colw - 18),
                    font=F(11), fill=(120, 138, 156))


def draw_planet_card(planet, width, data=None, state=None, rate=None,
                     min_height=0, title_cn=None):
    """A 区星球卡片：返回 PIL.Image（宽 width，高 = 内容高度或 min_height）。

    字段照主站 planet.html 的目标星球卡；数据全部来自共享层
    （campaign_state / planet_rate_state / 区域静态表），本函数只负责排版。
    """
    width = max(260, int(width))
    if data is None:
        data = _site_get(DATA_URL)
    state = state or shared.campaign_state(data, planet)
    rate = rate if rate is not None else planet_rate_view(data, planet, state)
    pad = _CARD_PAD
    inner = width - pad * 2
    smcn = _starmap_cn_map()
    owner_en = planet.get("currentOwner") or ""
    owner = FACTION_CN.get(owner_en, owner_en or "未知阵营")
    ocolor = FACTION_COLOR.get(owner, GOLD)
    defending = bool(state.get("defending"))
    progress = state.get("progress") or 0.0
    resistance = state.get("resistance")
    max_health = state.get("max_health") or 0

    canvas_h = 760
    img = Image.new("RGB", (width, canvas_h), PANEL)
    dr = ImageDraw.Draw(img)
    y = pad

    # ① 阵营条：左徽标 + 名称，右在线玩家（字段与旧图一致，不重算）
    _faction_badge(dr, pad, y + 2, 22, owner, ocolor)
    dr.text((pad + 30, y + 1), ellipsize(dr, owner, F(16, True), inner - 120),
            font=F(16, True), fill=ocolor)
    players_txt = fmt(planet.get("players") or 0)
    pw = dr.textlength(players_txt, font=F(19, True))
    dr.text((width - pad - pw, y - 2), players_txt, font=F(19, True), fill=TEXT)
    hint = "在线"
    hw = dr.textlength(hint, font=F(11))
    dr.text((width - pad - pw - 8 - hw, y + 4), hint, font=F(11), fill=MUTE)
    y += 30

    # ② 星球主图 + 左下角词条图标
    photo_h = 152
    bimg = _biome_landscape(_planet_biome_name(planet))
    if bimg:
        try:
            from PIL import ImageOps
            img.paste(ImageOps.fit(Image.open(bimg).convert("RGB"), (inner, photo_h)),
                      (pad, y))
        except Exception:
            bimg = None
    if not bimg:
        dr.rectangle([pad, y, width - pad, y + photo_h], fill=PANEL2)
        icon = _planet_icon_file(planet.get("name"))
        if icon:
            try:
                emblem = ImageOps_thumbnail(icon, 84)
                img.paste(emblem, (pad + (inner - emblem.width) // 2,
                                   y + (photo_h - emblem.height) // 2), emblem
                          if emblem.mode == "RGBA" else None)
            except Exception:
                pass
        else:
            dr.text((pad + inner // 2, y + photo_h // 2 - 16), "◇", font=F(52), fill=LINE2)
    cn_map, grp = _planet_intel()
    items = _dedupe_effect_items(
        [i for i in (planet.get("activeEffects") or []) if i is not None], cn_map, grp)
    chip_y = y + photo_h - 34
    for i, (label, _group) in enumerate(items[:3]):
        chip_x = pad + 8 + i * 34
        dr.rectangle([chip_x, chip_y, chip_x + 28, chip_y + 28], fill=(8, 12, 18), outline=LINE2)
        icon_file = _effect_icon_file(label)
        if icon_file:
            try:
                small = ImageOps_thumbnail(icon_file, 22)
                img.paste(small, (chip_x + (28 - small.width) // 2,
                                  chip_y + (28 - small.height) // 2),
                          small if small.mode == "RGBA" else None)
                continue
            except Exception:
                pass
        # 没有对应 PNG 时退化成首字方块（居中，不越框）
        glyph = (label or "?")[:1]
        gw = dr.textlength(glyph, font=F(13, True))
        dr.text((chip_x + (28 - gw) / 2, chip_y + 6), glyph, font=F(13, True), fill=GOLD2)
    y += photo_h + 10

    # ③ 类型标签 + 金色星球名 + 英文名/星区
    kind = "防御战" if defending else "解放战役"
    kw = dr.textlength(kind, font=F(12, True)) + 14
    dr.rectangle([pad, y, pad + kw, y + 20], fill=(8, 12, 18), outline=ocolor)
    dr.text((pad + 7, y + 2), kind, font=F(12, True), fill=ocolor)
    y += 25
    cn_name = _planet_display_name(planet.get("name"), title_cn, smcn, planet.get("name"))
    dr.text((pad, y), ellipsize(dr, cn_name, F(27, True), inner), font=F(27, True), fill=GOLD)
    y += 36
    secmaps = _sector_maps()
    sec_cn = ((secmaps.get("pl_sec") or {}).get((planet.get("name") or "").upper())
              or (secmaps.get("sec_cn") or {}).get((planet.get("sector") or "").upper())
              or (planet.get("sector") or "") or "未知星区")
    subtitle = f"{str(planet.get('name') or '').upper()} · {sec_cn}"
    if not subtitle.endswith("星区"):
        subtitle += "星区"
    dr.text((pad, y), ellipsize(dr, subtitle, F(11), inner), font=F(11), fill=MUTE)
    y += 18

    # ④ 描述：按宽度换行，最多 3 行
    desc = ("这颗星球遇袭。绝地潜兵必须保卫星球，抵御暴政军队。" if defending else
            "这颗星球被列为敌占领地。绝地潜兵必须从残暴入侵者手中夺回控制权。")
    for line in wrap_text(dr, desc, F(12), inner, max_lines=3):
        dr.text((pad, y), line, font=F(12), fill=DIM)
        y += 18
    y += 4

    # ⑤ 进度条：已完成填阵营色，未完成用 45° 斜纹
    bar_y, bar_h = y, 9
    bar_left, bar_right = pad, width - pad
    dr.rectangle([bar_left, bar_y, bar_right, bar_y + bar_h], fill=(20, 30, 43), outline=LINE)
    fill_w = int((bar_right - bar_left - 2) * max(0.0, min(1.0, progress / 100.0)))
    if fill_w > 0:
        dr.rectangle([bar_left + 1, bar_y + 1, bar_left + 1 + fill_w, bar_y + bar_h - 1],
                     fill=GREEN if progress >= 99.9 else ocolor)
    for sx in range(bar_left + 2 + fill_w, bar_right - 1, 11):
        dr.line([(sx, bar_y + bar_h - 1), (min(sx + bar_h - 1, bar_right - 1), bar_y + 1)],
                fill=LINE2)
    y += bar_h + 6
    lib_txt = f"已解放 {progress:.2f}%"
    dr.text((pad, y), lib_txt, font=F(12, True), fill=ocolor)
    strength = rate.get("strength_label") or "—"
    res_txt = (f"抵抗强度 {strength}（{resistance:.2f} %/h）" if resistance is not None
               else "抵抗强度 未知")
    rw = dr.textlength(res_txt, font=F(12))
    dr.text((width - pad - rw, y), res_txt, font=F(12), fill=DIM)
    y += 18

    # ⑥ 数据来源徽标（实测 / 推演）
    source_label = rate.get("source_label") or "未知"
    sw = dr.textlength(source_label, font=F(11, True))
    source_color = GREEN if rate.get("measured") else BRASS
    dr.text((width - pad - sw, y), source_label, font=F(11, True), fill=source_color)
    dr.text((pad, y), "速率口径", font=F(11), fill=MUTE)
    y += 16

    # ⑦ 速率四行：等效抵抗度 / 当前推进 / 实际推进 / 预计完成
    dr.line([(pad, y + 2), (width - pad, y + 2)], fill=LINE)
    y += 10
    res_eq = rate.get("res_eq")
    current = rate.get("current")
    actual = rate.get("actual")
    eta_text = rate.get("eta_text") or "—"
    eta_reason = rate.get("eta_reason") or "unknown"

    def _signed(value):
        return "—" if value is None else ("+" if value >= 0 else "−") + f"{abs(value):.2f} %/h"

    rate_rows = [
        ("等效抵抗度", "—" if res_eq is None else f"{res_eq:.2f} %/h", MUTE),
        ("当前推进速率", _signed(current), DIM),
        ("实际推进速率", _signed(actual), RED if (actual is not None and actual < 0) else DIM),
        ("预计完成", eta_text, RED if eta_reason in ("nonpositive", "too_long") else TEXT),
    ]
    for label, value, color in rate_rows:
        dr.text((pad, y), label, font=F(12), fill=MUTE)
        vw = dr.textlength(value, font=F(13, True))
        dr.text((width - pad - vw, y - 1), value, font=F(13, True), fill=color)
        y += 22
    y += 2

    # ⑧ 持续推进门槛（文案由共享层给，避免「已达门槛」与「无法完成」自相矛盾）
    threshold_text = rate.get("threshold_text")
    if threshold_text:
        state_cn = rate.get("threshold_state")
        color = GREEN if state_cn == "met" else (RED if state_cn == "impossible" else BRASS)
        for line in wrap_text(dr, threshold_text, F(11), inner, max_lines=2):
            dr.text((pad, y), line, font=F(11), fill=color)
            y += 15
    y += 6

    # ⑨ 区域摘要（明细在 E 区）
    rows_r = _region_rows(planet, data.get("impactMultiplier") or 0)
    if rows_r:
        opened = sum(1 for row in rows_r if (row.get("progress") or 0) > 0)
        top = max(rows_r, key=lambda row: row.get("progress") or 0)
        summary = f"区域 {len(rows_r)} · 城市 / 超大型工厂 · 已开放 {opened}/{len(rows_r)}"
        for line in wrap_text(dr, summary, F(11), inner, max_lines=2):
            dr.text((pad, y), line, font=F(11), fill=DIM)
            y += 15
        detail = f"{top['name_cn']} {(top.get('progress') or 0):.2f}%"
        dr.text((pad, y), ellipsize(dr, detail, F(11, True), inner), font=F(11, True), fill=TEXT)
        y += 17

    height = max(y + pad, int(min_height))
    height = min(height, canvas_h)
    img = img.crop((0, 0, width, height))
    dr = ImageDraw.Draw(img)
    dr.rectangle([0, 0, width - 1, height - 1], outline=ocolor, width=1)
    dr.rectangle([0, 0, width - 1, 3], fill=ocolor)
    return img


def ImageOps_thumbnail(path, size):
    """读图并等比缩放到 size 以内（返回 RGBA 小图）；失败抛异常由调用方兜底。"""
    from PIL import ImageOps
    pic = ImageOps.contain(Image.open(path).convert("RGBA"), (size, size))
    return pic


def render_planet_card(slug, title_cn=None, out_dir=None):
    """星球指挥台图片卡（左列星球卡 + 右列战术目标/血量在线/星球统计 + 通栏区域与词条）"""
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
        # A 区版式版本进缓存键：布局一改，旧布局的缓存图片自然失效
        rev = ("planet-tactical-v2:layout%d:" % PLANET_CARD_LAYOUT_VERSION
               + str(data.get("fetchedAt") or "") + ":" + en)

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

            cn = _planet_display_name(slug, title_cn, smcn, pl.get("name"))
            # ── 右列 B/C/D 先量高度，A 区卡片据此留白对齐 ──
            right_x = PAD + PLANET_CARD_WIDTH + 18
            right_w = W - PAD - right_x
            rate_view = planet_rate_view(data, pl, state)
            objectives = tactical.compute_for(data, pl.get("index"))
            obj_layout, obj_h = _objectives_block(dr, objectives, right_w)
            stats_rows, stats_present, stats_h = _stats_block(dr, data, pl, state, right_w)
            right_h = obj_h + 18 + HEALTH_PANEL_HEIGHT + 18 + stats_h

            # ── A 区星球卡片（边框用当前阵营色，高度补齐右列） ──
            card = draw_planet_card(pl, PLANET_CARD_WIDTH, data=data, state=state,
                                    rate=rate_view, min_height=right_h, title_cn=cn)
            img.paste(card, (PAD, y))
            # 卡片内容高于右列时，把星球统计面板补到同高，两列底边对齐
            extra = max(0, card.height - right_h)
            if extra:
                stats_h += extra
                right_h += extra

            # ── B 战术目标 / C 血量与在线 / D 星球统计 ──
            _tactical_panel(dr, right_x, y, right_w, obj_layout, obj_h)
            ry = y + obj_h + 18
            rv_net = rate_view.get("net")
            eq_rate = None if rv_net is None else rv_net * hp_ratio
            pshare = rate_view.get("player_share")
            if pshare is None:
                pshare = share
            health_rows = [
                ("当前血量", f"{fmt(state['health'] or 0)} / {fmt(state['max_health'] or 0)}"),
                ("血量倍数", f"×{hp_ratio:.2f}（100 万血基准）"),
                ("每小时回血", f"{heal_h:,.0f} HP/h"),
                ("等效推进（基准球）",
                 "—" if eq_rate is None else
                 ("+" if eq_rate >= 0 else "−") + f"{abs(eq_rate):.2f} %/h"),
                ("在线占全服", "—" if pshare is None else f"{pshare:.2f}%"),
                ("当前生态", _planet_biome_name(pl) or "—"),
            ]
            _health_panel(dr, right_x, ry, right_w, health_rows)
            ry += HEALTH_PANEL_HEIGHT + 18
            _stats_panel(dr, right_x, ry, right_w, stats_rows, stats_present, stats_h)
            y += max(right_h, card.height) + 18

            # ── 下属区域（城市 / 超大型工厂；行数据与 A 区摘要同源） ──
            rows_r = _region_rows(pl, data.get("impactMultiplier") or 0)
            if rows_r:
                panel_h = 50 + 24 + 34 * len(rows_r) + 10
                dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
                section_label(dr, PAD + 18, y + 13, "下属区域（城市 / 超大型工厂）", F(23, True))
                hy = y + 50
                c_owner, c_plr, c_lib, c_rate = PAD + 330, PAD + 700, PAD + 830, W - PAD - 14
                for lbl, rx, ralign in (("区域", PAD + 14, 0), ("归属 · 类型", c_owner, 0),
                                        ("玩家", c_plr, 1), ("已解放", c_lib, 1),
                                        ("推进 · 预计", c_rate, 1)):
                    lw = dr.textlength(lbl, font=F(13))
                    dr.text(((rx - lw) if ralign else rx, hy), lbl, font=F(13), fill=MUTE)
                for i, row in enumerate(rows_r):
                    ry2 = hy + 24 + i * 34
                    dr.line([(PAD + 10, ry2), (W - PAD - 10, ry2)], fill=LINE)
                    dr.text((PAD + 14, ry2 + 7), row["name_cn"], font=F(17, True), fill=TEXT)
                    if row["en_name"] and row["en_name"] != row["name_cn"]:
                        dr.text((PAD + 14 + dr.textlength(row["name_cn"], font=F(17, True)) + 10,
                                 ry2 + 11), row["en_name"], font=F(11), fill=(120, 138, 156))
                    dr.text((c_owner, ry2 + 9),
                            row["owner_cn"] + " · " + row["type_cn"] + "★" * row["stars"],
                            font=F(14), fill=DIM)
                    rplayers = row["players"]
                    pw2 = dr.textlength(fmt(rplayers) if rplayers else "—", font=F(16, True))
                    dr.text((c_plr - pw2, ry2 + 8), fmt(rplayers) if rplayers else "—",
                            font=F(16, True), fill=TEXT)
                    lib_s = "—" if row["progress"] is None else "%.2f%%" % row["progress"]
                    lw2 = dr.textlength(lib_s, font=F(16, True))
                    dr.text((c_lib - lw2, ry2 + 8), lib_s, font=F(16, True),
                            fill=GREEN if row["progress"] == 100 else TEXT)
                    rate_txt = row["rate_txt"]
                    rw = dr.textlength(rate_txt, font=F(15))
                    dr.text((c_rate - rw, ry2 + 9), rate_txt, font=F(15),
                            fill=(120, 138, 156) if rate_txt == "—" else DIM)
                y += panel_h + 18

            # ══ 环境与作战词条（行动变量：词条 chip + 中文描述，分组同 hd2_variables） ══
            eids = [i for i in (pl.get("activeEffects") or []) if i is not None]
            items = _effect_items(eids, cn_map, grp)
            max_w = W - PAD - 18 - (PAD + 18)
            chip_font, desc_font = F(18, True), F(14)
            layout, yy, cur_group = [], y + 54, None
            for pos, item in enumerate(items):
                if pos:
                    yy += 12
                group = item["group"]
                if group and group != cur_group:
                    layout.append(("group", group, yy))
                    yy += 30
                    cur_group = group
                cw = dr.textlength(item["label"], font=chip_font) + 24
                layout.append(("chip", item["label"], yy, cw))
                yy += 34 + 6
                if item["desc"]:
                    for line in wrap_text(dr, item["desc"], desc_font, max_w, max_lines=2):
                        layout.append(("desc", line, yy))
                        yy += 21
            panel_h = max(110, (yy - y) + 6)
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "环境与作战词条", F(23, True))
            for block in layout:
                if block[0] == "group":
                    dr.text((PAD + 18, block[2]), block[1], font=F(15, True), fill=BRASS)
                elif block[0] == "chip":
                    cx, cy2, cw = PAD + 18, block[2], block[3]
                    dr.rectangle([cx, cy2, cx + cw, cy2 + 34], fill=(46, 42, 10), outline=BRASS)
                    dr.text((cx + 12, cy2 + 4), block[1], font=chip_font, fill=GOLD2)
                else:
                    dr.text((PAD + 18, block[2]), block[1], font=desc_font, fill=DIM)
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
                    font=F(14), fill=(120, 138, 156))
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
        ("/战报", "银河战况速报卡：MO、战略指令、战略机遇、次要指令、目标星球与兵力分布"),
        ("/重要指令", "独立 MO 通知卡：官方中文全文、三类特别指令、任务状态与定位链接"),
        ("/战线", "全银河战线列表：各战区解放/防御进度与交战双方"),
        ("/dss", "民主空间站：全部战术行动同屏，大图、说明、状态与捐献进度"),
        ("/战役", "活动战役档案墙：头图、全部阶段简报/战报、结果与奖励"),
        ("/api", "每分钟监测主仓 Issue；当前会话在内容变化时自动接收，支持手动重播"),
    ]),
    ("星球情报", "PLANET INTEL", [
        ("/查询 星球名", "星球指挥台卡：解放环、区域战况、环境词条、战术目标与目标链接"
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
        ("BTK 可选参数", "接在主命令后：距离=米；角度=0–3 或 正面/小角度/大角度/极端；"
                         "破片=数量；波及=部位1,部位2。参数与主命令一起发送"),
        ("/btk 卡阈值 武器 [全部]", "阈值矩阵卡：默认只扫致死部位；加“全部”扫描所有部位"),
        ("/btk DOT-FIRE 敌人", "持续伤害卡：每跳伤害、触发次数、总时长与元素倍率（无需部位）"),
        ("/btk 武器 / 敌人", "查看武器 ID、敌人阵营和指定敌人的可用部位"),
        ("/拆毁值 来源", "直击/爆炸分行判定，并对照广播塔、虫穴、构筑者和设施"),
        ("/拆毁值 来源 目标", "指定目标的 DF、门槛、爆炸距离和最大拆毁距离"),
        ("/拆毁值 直击 / 爆炸物 / 常见目标 / 建筑", "查看直击来源、爆炸曲线、常见目标和完整目标门槛"),
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
    "官方平台需 @机器人；协议端（SnowLuma/NapCat）可直接 /指令；名称支持中英文",
    "默认输出战术 HUD 图片卡，渲染失败自动回退文本；指令后加 t 可要纯文本",
    "新 MO 首次被战况命令发现时自动通知一次；/战报 和 /重要指令 始终显示当期内容",
    "没有官方独立 MINOR ORDER 时，次要指令只标记为“重要指令附加目标”",
    "名称无法确定时可先用 /wiki 查询标准写法，再执行 /btk 或 /拆毁值",
    "进群欢迎仅协议端通道（SnowLuma/NapCat）；战况以源数据时间与陈旧提示为准",
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
        command_font = F(19, True)
        col = min(390, max(250, max(int(dr.textlength(c, font=command_font))
                                    for c, _ in rows) + 30))
        for cmd, desc in rows:
            command_lines = _help_wrap(cmd, command_font, col - 28, dr)
            desc_font = F(18)
            tx = PAD + col + 16
            desc_lines = _help_wrap(desc, desc_font, W - PAD - tx, dr)
            command_lh, desc_lh = 25, 26
            command_h = command_lh * len(command_lines)
            desc_h = desc_lh * len(desc_lines)
            bh = max(42, max(command_h, desc_h) + 14)
            dr.rectangle([PAD, y, PAD + col, y + bh], fill=PANEL, outline=LINE2)
            dr.rectangle([PAD, y, PAD + 3, y + bh], fill=GOLD)
            cy = y + (bh - command_h) // 2
            for line in command_lines:
                dr.text((PAD + 14, cy), line, font=command_font, fill=GOLD2)
                cy += command_lh
            ty = y + (bh - desc_h) // 2
            for line in desc_lines:
                dr.text((tx, ty), line, font=desc_font, fill=TEXT)
                ty += desc_lh
            y += bh + 10
            dr.line([(PAD, y), (W - PAD, y)], fill=LINE)
            y += 6
        y += 4
    y += 8
    dr.line([(0, y), (W, y)], fill=LINE2)
    y += 16
    for note in HELP_NOTES:
        dr.text((PAD, y), "·", font=F(18, True), fill=BRASS)
        note_lines = _help_wrap(note, F(18), W - PAD * 2 - 20, dr)
        for line in note_lines:
            dr.text((PAD + 18, y), line, font=F(18), fill=DIM)
            y += 26
        y += 4
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
            "help-v10", _draw_help)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def save_pages(img, height, out_path, document=None, page_info=None, caption=None,
               max_area=None, png_level=6, column_height=2100):
    """超长画布合并为单张宽图：按安静行均衡切段，从左向右排列成连续分区。

    旧行为是竖切成多张 PNG 分多条消息发送（QQ 里刷屏且上下文割裂）；
    现在拼成一张图，每列顶部带「分区 i/N」标签，列间加分隔线，底部统一页脚。
    切段均衡化：先按总高算列数 N=ceil(H/2100)，每列目标高度 H/N，再在目标行
    附近 ±100px 内回退找安静行，避免「首列塞满、末列只剩尾巴」的失衡观感。
    单段内容（不超高）保持原单图输出，页脚同样收敛到底部条内。
    page_info：遗留参数，兼容保留；若提供 (i, n, 标题) 则标题用作列标签前缀。
    caption：自定义底部页脚文案（战报默认展示数据时间）。
    max_area：画布像素面积上限（宽×高），默认 34M；长内容卡（如 Wiki 全文）可放宽。
    png_level：PNG 压缩级别（1 最快体积最大，6 默认）；大幅面渲染建议降级换速度。
    """
    import math
    n_pages = max(1, math.ceil(height / max(1, column_height)))
    ideal = math.ceil(height / n_pages)
    spans, start = [], 0
    while start < height:
        end = min(height, start + ideal)
        if end < height:
            for row in range(end, max(start + ideal // 2, end - 100), -1):
                band = img.crop((PAD + 28, row, W - PAD - 28, row + 2)).convert("L")
                if band.point(lambda p: 255 if p > 100 else 0).getbbox() is None:
                    end = row
                    break
        spans.append((start, end))
        start = end
    # 吸收过小残段（安静行回退留下的尾巴），避免产生近空的列
    min_span = max(120, ideal // 8)
    merged_spans = [spans[0]]
    for s, e in spans[1:]:
        if e - s < min_span:
            ps, _pe = merged_spans[-1]
            merged_spans[-1] = (ps, e)
        else:
            merged_spans.append((s, e))
    spans = merged_spans
    doc = document or {}
    quality = " · 缓存/数据陈旧" if (doc.get("_hd2") or {}).get("stale") else ""
    ts = str(doc.get("fetchedAt") or "")[:16].replace("T", " ")
    n = len(spans)

    GUTTER, LABEL_H, FOOT_H = 28, (64 if n > 1 else 0), 56
    col_h = max(e - s for s, e in spans)
    total_w = n * W + (n - 1) * GUTTER
    total_h = LABEL_H + col_h + FOOT_H
    if total_w * total_h > (max_area or 34_000_000):  # 防极端内容撑爆内存
        raise ValueError("内容过长，请使用文字查询或原文链接")
    canvas = Image.new("RGB", (total_w, total_h), BG)
    dr = ImageDraw.Draw(canvas)
    for i, (s, e) in enumerate(spans):
        x = i * (W + GUTTER)
        if n > 1:
            if i:
                gx = x - GUTTER // 2
                dr.rectangle([gx - 1, 0, gx + 1, total_h - FOOT_H], fill=LINE)
            prefix = f"{page_info[2]} " if (page_info and len(page_info) >= 3) else ""
            dr.text((x + PAD, 18), f"{prefix}分区 {i + 1}/{n}", font=F(20, True), fill=GOLD)
            if i == 0:
                dr.text((x + PAD + 150, 26), "◄ 各分区自左向右连续阅读", font=F(13), fill=MUTE)
            dr.line([(x, LABEL_H - 10), (x + W, LABEL_H - 10)], fill=LINE2)
        canvas.paste(img.crop((0, s, W, e)), (x, LABEL_H))
    if caption is None:
        cap = f"数据时间 {ts or '见正文'}" + quality + (" · 各分区自左向右连续阅读" if n > 1 else "")
    else:
        cap = caption + (" · 各分区自左向右连续阅读" if n > 1 else "")
    dr.line([(0, total_h - FOOT_H), (total_w, total_h - FOOT_H)], fill=LINE)
    dr.text((PAD, total_h - FOOT_H + 16), cap, font=F(16), fill=DIM)
    mot_x = total_w - PAD - dr.textlength("真理部 · MOT", font=F(14))
    dr.text((mot_x, total_h - FOOT_H + 20), "真理部 · MOT", font=F(14), fill=(120, 138, 156))
    path = os.path.splitext(out_path)[0] + ".png"
    fd, tmp = tempfile.mkstemp(prefix=".card_", dir=os.path.dirname(path))
    os.close(fd)
    try:
        canvas.save(tmp, format="PNG")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return [path]
