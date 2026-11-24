"""流式画布：Token 感知换行 + 先量后画，消除手写像素坐标。

业务卡片只描述「画什么」（标题、KPI、进度条、明细行、胶囊），由 :class:`Canvas`
负责测高、留白与不越界；``done()`` 返回 ``(image, height)``，交给
:func:`astrbot_plugin_hd2_common.ui.pages.render_cached` 出图。
"""
from __future__ import annotations

import re
from datetime import datetime

from PIL import Image, ImageDraw

from astrbot_plugin_hd2_common.ui.tokens import (
    AMBER, AMBER_BG, AMBER_DIM, BG, BRASS, CARD, CARD2, CYAN, CYAN_BG, DIM, F, GUTTER,
    GREEN, LINE, LINE2, MUTE, PAD, RADIUS, RED, TALL, TEXT, W, WELL,
)

_WORD = re.compile(r"[A-Za-z0-9]+(?:[%‰.,:+\-/][A-Za-z0-9]+)*[%‰]?")


def tokens(text: str) -> list[str]:
    """把文本切成不可再拆的最小单元（英文词/数字/百分比整体，中文逐字）。"""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\r":
            i += 1
            continue
        if ch == "\n":
            out.append("\n")
            i += 1
            continue
        m = _WORD.match(text, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(ch)
        i += 1
    return out


def ellipsize(dr, text, font, max_w: float, ellipsis: str = "…") -> str:
    text = str(text or "")
    if max_w <= 0:
        return ""
    if dr.textlength(text, font=font) <= max_w:
        return text
    if dr.textlength(ellipsis, font=font) >= max_w:
        return ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if dr.textlength(text[:mid], font=font) + dr.textlength(ellipsis, font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ellipsis


def wrap_runs(dr, runs, font, max_w: float) -> list[list[tuple[str, object]]]:
    """按像素宽度折行；英文词与 ``50%`` 这类数值不跨行拆断。"""
    lines: list[list[tuple[str, object]]] = []
    cur: list[tuple[str, object]] = []
    cur_w = 0.0
    pending_space = False

    def flush():
        nonlocal cur, cur_w
        if cur:
            lines.append(cur)
        cur, cur_w = [], 0.0

    for text, style in runs:
        for tok in tokens(str(text or "")):
            if tok == "\n":
                flush()
                pending_space = False
                continue
            if tok.isspace() and tok != "\n":
                pending_space = True
                continue
            tw = dr.textlength(tok, font=font)
            lead = " " if pending_space and cur else ""
            pending_space = False
            if tw > max_w and not cur:          # 单元本身超宽 → 逐字硬切
                for ch in tok:
                    cw = dr.textlength(ch, font=font)
                    if cur and cur_w + cw > max_w:
                        flush()
                    if cur and cur[-1][0] and cur[-1][1] == style:
                        cur[-1] = (cur[-1][0] + ch, style)
                    else:
                        cur.append((ch, style))
                    cur_w += cw
                continue
            if cur and cur_w + tw + dr.textlength(lead, font=font) > max_w:
                flush()
                lead = ""
            if lead:
                if cur:
                    cur[-1] = (cur[-1][0] + lead, cur[-1][1])
                cur_w += dr.textlength(lead, font=font)
            if cur and cur[-1][1] == style:
                cur[-1] = (cur[-1][0] + tok, style)
            else:
                cur.append((tok, style))
            cur_w += tw
    flush()
    return lines


class Canvas:
    """流式画布：``self.y`` 是当前写入位置，所有绘制方法返回消耗的高度。"""

    def __init__(self, width: int = W, pad: int = PAD, tall: int = TALL, bg=BG):
        self.width, self.pad, self.bg = width, pad, bg
        self.img = Image.new("RGB", (width, tall), bg)
        self.dr = ImageDraw.Draw(self.img)
        self.y = 0
        self.footer_drawn = False
        self.footer_caption = ""
        self.footer_static = False

    # ── 度量 ──────────────────────────────────────────
    def text_w(self, text, font) -> float:
        return float(self.dr.textlength(str(text or ""), font=font))

    def line_h(self, font) -> int:
        b = self.dr.textbbox((0, 0), "国Ag", font=font)
        return b[3] - b[1]

    def _ensure(self, need: int):
        if self.y + need > self.img.height:
            new_h = max(self.img.height * 2, self.y + need + 200)
            bigger = Image.new("RGB", (self.width, new_h), self.bg)
            bigger.paste(self.img, (0, 0))
            self.img = bigger
            self.dr = ImageDraw.Draw(self.img)

    # ── 基础图元 ───────────────────────────────────────
    def gap(self, h: int = 12):
        self.y += h
        return h

    def panel(self, x, y, w, h, fill=CARD, outline=LINE, radius=RADIUS, accent=None):
        self.dr.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill, outline=outline)
        if accent:
            self.dr.rounded_rectangle([x, y, x + 4, y + h], radius=2, fill=accent)

    def pill(self, x, y, text, color=AMBER, bg=None, font=None, pad_x=10, h=24):
        font = font or F(12, True)
        tw = self.text_w(text, font)
        bg = bg if bg is not None else (30, 34, 42)
        self.dr.rounded_rectangle([x, y, x + tw + pad_x * 2, y + h], radius=h // 2, fill=bg)
        self.dr.text((x + pad_x, y + (h - self.line_h(font)) // 2 - 1), text, font=font, fill=color)
        return tw + pad_x * 2

    # ── 结构 ──────────────────────────────────────────
    def header(self, title, en="", right="", right2="", show_time=True):
        h = 84
        self.dr.rectangle([0, 0, self.width, h], fill=WELL)
        for x in range(-h, self.width, 28):        # 45° 细斜纹
            self.dr.line([(x, h), (x + h, 0)], fill=(245, 197, 24, 16), width=1)
        self.dr.rectangle([0, 0, 5, h], fill=AMBER)
        title_font = F(30, True)
        self.dr.text((self.pad, 20), title, font=title_font, fill=TEXT)
        tw = self.text_w(title, title_font)
        if en:
            self.dr.text((self.pad + tw + 26, 32), en, font=F(11), fill=MUTE)
        if show_time:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            sw = self.text_w(stamp, F(12))
            self.dr.text((self.width - self.pad - sw, 14), stamp, font=F(12), fill=MUTE)
        line2 = right2 or "真理部 · MOT"
        sw2 = self.text_w(line2, F(12))
        self.dr.text((self.width - self.pad - sw2, 38), line2, font=F(12), fill=BRASS)
        if right:
            sw3 = self.text_w(right, F(12))
            self.dr.text((self.width - self.pad - sw3, 62), right, font=F(12), fill=MUTE)
        self.dr.line([(0, h), (self.width, h)], fill=LINE2)
        self.y = h + 18
        return self.y

    def section(self, title, en="", hint=""):
        self._ensure(52)
        y = self.y
        self.dr.rounded_rectangle([self.pad, y + 5, self.pad + 4, y + 25], radius=2, fill=AMBER)
        self.dr.text((self.pad + 12, y), title, font=F(19, True), fill=TEXT)
        x = self.pad + 12 + self.text_w(title, F(19, True)) + 10
        if en:
            self.dr.text((x, y + 5), en, font=F(11), fill=MUTE)
            x += self.text_w(en, F(11)) + 10
        if hint:
            hw = self.text_w(hint, F(12))
            self.dr.text((self.width - self.pad - hw, y + 4), hint, font=F(12), fill=MUTE)
        self.y = y + 34
        return 34

    def heading(self, text, sub="", font_size=30, color=TEXT, sub_color=MUTE):
        self._ensure(font_size + 20)
        font = F(font_size, True)
        self.dr.text((self.pad, self.y), text, font=font, fill=color)
        th = self.line_h(font)
        if sub:
            self.dr.text((self.pad + self.text_w(text, font) + 12, self.y + font_size // 3),
                         sub, font=F(13), fill=sub_color)
        self.y += th + 10
        return th + 10

    def paragraph(self, runs, font=None, color=DIM, max_w=None, line_gap=6):
        """runs: 字符串，或 [(text, style)]；style 可为颜色或 None。"""
        font = font or F(14)
        max_w = max_w or (self.width - self.pad * 2)
        if isinstance(runs, str):
            runs = [(runs, color)]
        lines = wrap_runs(self.dr, runs, font, max_w)
        if not lines:
            return 0
        lh = self.line_h(font) + line_gap
        self._ensure(lh * len(lines))
        for line in lines:
            x = self.pad
            for text, style in line:
                self.dr.text((x, self.y), text, font=font, fill=style or color)
                x += self.text_w(text, font)
            self.y += lh
        return lh * len(lines)

    def banner(self, text, label="结论", tone="amber", font_size=17):
        palette = {"amber": (AMBER, AMBER_BG), "green": (GREEN, (16, 52, 42)),
                   "red": (RED, (58, 22, 26)), "cyan": (CYAN, CYAN_BG)}
        color, bg = palette.get(tone, palette["amber"])
        font = F(font_size, True)
        max_w = self.width - self.pad * 2 - 32 - (self.text_w(label, F(11, True)) + 24 if label else 0)
        lines = wrap_runs(self.dr, [(text, TEXT)], font, max_w)
        h = 20 + len(lines) * (self.line_h(font) + 5)
        self._ensure(h + 12)
        self.panel(self.pad, self.y, self.width - self.pad * 2, h, fill=bg,
                   outline=color, accent=color)
        x = self.pad + 16
        if label:
            x += self.pill(x, self.y + 9, label, color=color, bg=bg) + 12
        yy = self.y + 10
        for line in lines:
            xx = x if yy == self.y + 10 else self.pad + 16 + (self.text_w(label, F(11, True)) + 34 if label else 0)
            for text, _style in line:
                self.dr.text((xx, yy), text, font=font, fill=TEXT)
                xx += self.text_w(text, font)
            yy += self.line_h(font) + 5
        self.y += h + 14
        return h + 14

    def kpi(self, items, cols=3, h=92, accents=None):
        """items: [(label, value, sub)]；网格并排，返回总高。"""
        n = len(items)
        cols = max(1, min(cols, n))
        gw = (self.width - self.pad * 2 - GUTTER * (cols - 1)) / cols
        rows = (n + cols - 1) // cols
        accents = accents or [AMBER] * n
        self._ensure(rows * (h + GUTTER))
        for i, (label, value, sub) in enumerate(items):
            r, c = divmod(i, cols)
            x = self.pad + c * (gw + GUTTER)
            y = self.y + r * (h + GUTTER)
            self.panel(x, y, gw, h, fill=CARD, accent=accents[i])
            self.dr.text((x + 14, y + 10), label, font=F(11, True), fill=MUTE)
            vf = F(26, True)
            self.dr.text((x + 14, y + 28), ellipsize(self.dr, value, vf, gw - 28), font=vf, fill=TEXT)
            if sub:
                self.dr.text((x + 14, y + h - 22), ellipsize(self.dr, sub, F(11), gw - 28),
                             font=F(11), fill=DIM)
        used = rows * h + (rows - 1) * GUTTER
        self.y += used + 14
        return used + 14

    def rows(self, items, row_h=46, zebra=True, label_font=None, value_font=None):
        """无框行级明细：左标签，右数值；斑马纹。items: [(label, value, note|None)]"""
        label_font = label_font or F(15)
        value_font = value_font or F(15, True)
        total = len(items) * row_h
        self._ensure(total + 6)
        for i, item in enumerate(items):
            label, value = item[0], item[1]
            note = item[2] if len(item) > 2 else None
            y = self.y + i * row_h
            if zebra and i % 2 == 1:
                self.dr.rectangle([self.pad, y, self.width - self.pad, y + row_h], fill=WELL)
            self.dr.text((self.pad + 10, y + (row_h - self.line_h(label_font)) // 2 - 1),
                         ellipsize(self.dr, label, label_font, (self.width - self.pad * 2) * 0.45),
                         font=label_font, fill=TEXT)
            vw = self.text_w(value, value_font)
            self.dr.text((self.width - self.pad - 12 - vw, y + (row_h - self.line_h(value_font)) // 2 - 1),
                         value, font=value_font, fill=TEXT)
            if note:
                nw = self.text_w(note, F(11))
                self.dr.text((self.width - self.pad - 12 - vw - nw - 10,
                              y + (row_h - self.line_h(F(11))) // 2), note, font=F(11), fill=MUTE)
        self.y += total + 6
        return total + 6

    def progress(self, label, ratio, value_text="", color=AMBER, sub="", h=10):
        pct = max(0.0, min(1.0, float(ratio or 0.0)))
        self._ensure(64)
        y = self.y
        self.dr.text((self.pad, y), label, font=F(13, True), fill=DIM)
        if value_text:
            vw = self.text_w(value_text, F(13, True))
            self.dr.text((self.width - self.pad - vw, y), value_text, font=F(13, True), fill=color)
        bar_y = y + 24
        self.dr.rounded_rectangle([self.pad, bar_y, self.width - self.pad, bar_y + h],
                                  radius=h // 2, fill=WELL)
        if pct > 0:
            self.dr.rounded_rectangle([self.pad, bar_y, self.pad + max(h, (self.width - self.pad * 2) * pct),
                                       bar_y + h], radius=h // 2, fill=color)
        self.y = bar_y + h
        if sub:
            self.dr.text((self.pad, self.y + 6), sub, font=F(11), fill=MUTE)
            self.y += 6 + self.line_h(F(11))
        self.y += 12
        return 36 + h

    def chips(self, items, gap=8, h=26):
        """自适应换行的胶囊组；items: [(text, color, bg)] 或 text。"""
        x, y = self.pad, self.y
        line_used = h
        for it in items:
            if isinstance(it, str):
                text, color, bg = it, DIM, (30, 34, 42)
            else:
                text, color, bg = it
            font = F(11, True)
            w = self.text_w(text, font) + 22
            if x + w > self.width - self.pad and x > self.pad:
                x = self.pad
                y += h + gap
                line_used += h + gap
            self.pill(x, y, text, color=color, bg=bg, font=font, h=h)
            x += w + gap
        self.y = y + h + 10
        return line_used + 10

    def footer(self, caption="", static=False, brand="真理部 · MOT"):
        """记录页脚文案（由 pages.save_pages 统一绘制，避免出现双层页脚）。

        静态卡（/帮助 等）由调用方显式传 ``static=True``，页脚不再出现
        「数据时间 见正文」这类无意义占位。
        """
        self.footer_caption = caption
        self.footer_static = static
        self.footer_drawn = True
        return 0

    def done(self):
        if not self.footer_drawn:
            self.footer(static=False)
        height = min(self.y + 6, self.img.height)
        return self.img.crop((0, 0, self.width, height)), height


def two_cols(width: int = W, pad: int = PAD, gap: int = GUTTER):
    """左右并排双栏的 (左 x, 左宽, 右 x, 右宽)。"""
    cw = (width - pad * 2 - gap) / 2
    return pad, cw, pad + cw + gap, cw
