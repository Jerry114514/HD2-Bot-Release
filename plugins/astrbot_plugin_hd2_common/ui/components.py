"""高阶组件：图标槽、表格、时间线、阵营横条等可复用卡内构件。"""
from __future__ import annotations

from PIL import Image, ImageOps

from astrbot_plugin_hd2_common.ui.layout import Canvas, ellipsize
from astrbot_plugin_hd2_common.ui.tokens import (
    AMBER, BRASS, CARD, CARD2, CYAN, DIM, F, GUTTER, LINE, LINE2, MUTE, PAD, TEXT, WELL,
    faction_color,
)


def paste_contain(img: Image.Image, src: Image.Image, box: tuple[int, int, int, int]):
    """等比缩放贴图并居中到 ``(x, y, w, h)``。"""
    x, y, w, h = (int(v) for v in box)
    w = max(1, w)
    h = max(1, h)
    icon = ImageOps.contain(src.convert("RGBA"), (w, h))
    img.paste(icon, (x + (w - icon.width) // 2, y + (h - icon.height) // 2), icon)


def icon_slot(c: Canvas, x, y, size, path=None, fallback: str = "",
              accent=AMBER, radius: int = 8):
    """深色图标槽；无图时回退为首字母/符号。"""
    x, y, size = int(x), int(y), int(size)
    c.dr.rounded_rectangle([x, y, x + size, y + size], radius=radius, fill=WELL, outline=LINE)
    drawn = False
    if path:
        try:
            with Image.open(path) as src:
                paste_contain(c.img, src, (x + 6, y + 6, size - 12, size - 12))
            drawn = True
        except (OSError, ValueError):
            drawn = False
    if not drawn and fallback:
        font = F(max(12, size // 3), True)
        tw = c.text_w(fallback[:2], font)
        c.dr.text((int(x + (size - tw) / 2), int(y + (size - c.line_h(font)) / 2) - 1),
                  fallback[:2], font=font, fill=accent)


def table(c: Canvas, headers, rows, widths, row_h: int = 46, header_h: int = 30,
          zebra: bool = True, aligns=None):
    """轻表：列宽按比例分配，首列左对齐，其余默认右对齐；返回总高。"""
    total_w = c.width - c.pad * 2
    scale = total_w / float(sum(widths))
    cols = [w * scale for w in widths]
    aligns = aligns or (["left"] + ["right"] * (len(headers) - 1))
    self_x = c.pad
    x0 = self_x
    c._ensure(header_h + row_h * len(rows))
    y = c.y
    xx = x0
    for i, head in enumerate(headers):
        if aligns[i] == "left":
            c.dr.text((xx + 10, y + 8), head, font=F(11, True), fill=MUTE)
        else:
            hw = c.text_w(head, F(11, True))
            c.dr.text((xx + cols[i] - 10 - hw, y + 8), head, font=F(11, True), fill=MUTE)
        xx += cols[i]
    c.dr.line([(x0, y + header_h - 6), (x0 + total_w, y + header_h - 6)], fill=LINE2)
    yy = y + header_h
    for r, row in enumerate(rows):
        if zebra and r % 2 == 1:
            c.dr.rectangle([x0, yy, x0 + total_w, yy + row_h], fill=WELL)
        xx = x0
        for i, cell in enumerate(row):
            font = F(14, True) if i == 0 else F(14)
            max_w = cols[i] - 20
            text = ellipsize(c.dr, str(cell), font, max_w)
            if aligns[i] == "left":
                c.dr.text((xx + 10, yy + (row_h - c.line_h(font)) // 2 - 1), text, font=font, fill=TEXT)
            else:
                tw = c.text_w(text, font)
                c.dr.text((xx + cols[i] - 10 - tw, yy + (row_h - c.line_h(font)) // 2 - 1),
                          text, font=font, fill=TEXT)
            xx += cols[i]
        yy += row_h
    c.y = yy + 8
    return header_h + row_h * len(rows) + 8


def timeline(c: Canvas, items, dot_color=CYAN, headline_w_ratio: float = 0.32):
    """时间线：左侧圆点竖线，右侧标题 + 说明。items: [(title, sub)]"""
    if not items:
        return 0
    start = c.y
    x = c.pad + 8
    c._ensure(56 * len(items) + 10)
    c.dr.line([(x, start + 6), (x, start + 52 * len(items))], fill=LINE)
    for title, sub in items:
        cy = c.y + 8
        c.dr.ellipse([x - 4, cy - 4, x + 4, cy + 4], fill=dot_color)
        c.dr.text((x + 16, c.y + 2), ellipsize(c.dr, title, F(14, True),
                                               (c.width - c.pad * 2) * 0.9),
                  font=F(14, True), fill=TEXT)
        if sub:
            c.dr.text((x + 16, c.y + 22), ellipsize(c.dr, sub, F(11),
                                                    (c.width - c.pad * 2) * 0.9),
                      font=F(11), fill=MUTE)
        c.y += 52
    return c.y - start
