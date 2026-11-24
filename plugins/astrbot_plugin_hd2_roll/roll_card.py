# -*- coding: utf-8 -*-
"""/roll 图片卡：七格战术搭配（主手 / 副手 / 手雷 / 战略配备 ×4）。

视觉走 ``astrbot_plugin_hd2_common.ui`` 的黑曜石琥珀金 Token：深色图标槽 +
分类胶囊 + 2×2 战略配备网格；数据仍来自 ``loadout.draw``（与主站同源）。
"""
import hashlib
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot_plugin_hd2_common.paths import plugin_cache_dir  # noqa: E402
from astrbot_plugin_hd2_common.ui import components, pages  # noqa: E402
from astrbot_plugin_hd2_common.ui.layout import Canvas, ellipsize  # noqa: E402
from astrbot_plugin_hd2_common.ui.tokens import (  # noqa: E402
    AMBER, BRASS, CARD, CYAN, DIM, F, GUTTER, LINE, MUTE, PAD, TEXT, WELL,
)
from astrbot_plugin_hd2_roll.loadout import catalog, draw  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")
CACHE_DIR = str(plugin_cache_dir("astrbot_plugin_hd2_roll", create=False))
LAYOUT_TAG = "roll-layout-v2"

SLOT_LABEL = {"primary": "主手", "secondary": "副手", "grenade": "手雷"}
SLOT_EN = {"primary": "PRIMARY", "secondary": "SECONDARY", "grenade": "GRENADE"}


def paste_contain(img, src, box):
    """兼容旧入口：等比缩放居中贴图。"""
    components.paste_contain(img, src, box)


def icon_path(eid):
    return os.path.join(ICON_DIR, str(eid or "") + ".png")


def _entry_line(c: Canvas, y: int, entry: dict, slot_label: str, slot_en: str, zebra: bool):
    h = 78
    if zebra:
        c.dr.rounded_rectangle([c.pad, y, c.width - c.pad, y + h], radius=8, fill=CARD)
    eid = str(entry.get("id") or "")
    name = str(entry.get("name") or eid)
    name_en = str(entry.get("name_en") or "")
    components.icon_slot(c, c.pad + 12, y + 10, 58, icon_path(eid), fallback=name[:2])
    c.dr.text((c.pad + 84, y + 14), slot_label, font=F(10, True), fill=BRASS)
    c.dr.text((c.pad + 84, y + 32), ellipsize(c.dr, name, F(20, True), c.width * 0.44),
              font=F(20, True), fill=TEXT)
    if name_en and name_en.upper() != name.upper():
        c.dr.text((c.pad + 84, y + 58), ellipsize(c.dr, name_en, F(11), c.width * 0.44),
                  font=F(11), fill=MUTE)
    c.pill(c.width - c.pad - 96, y + 26, slot_en, color=CYAN, bg=(16, 48, 72), font=F(10, True), h=22)
    return h


def _stratagem_card(c: Canvas, x, y, w, h, entry: dict, index: int):
    c.panel(x, y, w, h, fill=CARD, outline=LINE)
    eid = str(entry.get("id") or f"stratagem-{index}")
    name = str(entry.get("name") or eid)
    name_en = str(entry.get("name_en") or "")
    components.icon_slot(c, x + 12, y + 14, 56, icon_path(eid), fallback=name[:2], accent=AMBER)
    c.dr.text((x + 80, y + 12), f"战备 {index}", font=F(10, True), fill=BRASS)
    c.dr.text((x + 80, y + 30), ellipsize(c.dr, name, F(18, True), w - 96),
              font=F(18, True), fill=TEXT)
    if name_en and name_en.upper() != name.upper():
        c.dr.text((x + 80, y + 56), ellipsize(c.dr, name_en, F(10), w - 96),
                  font=F(10), fill=MUTE)


def render_roll_card(out_dir=None, result=None):
    """七格配装卡 → PNG 路径；失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        if not catalog():
            return None
        lo = result if result is not None else draw()
        if not lo:
            return None
        c = Canvas()
        c.header("战术搭配器", "LOADOUT · 七格配装")
        c.section("武器装备", "PRIMARY · SECONDARY · GRENADE", hint="槽位随机 · 七件互异")
        for i, slot in enumerate(("primary", "secondary", "grenade")):
            entry = lo.get(slot) or {}
            c.y += 4
            _entry_line(c, c.y, entry, SLOT_LABEL[slot], SLOT_EN[slot], zebra=(i % 2 == 0))
            c.y += 78 + 4

        c.gap(6)
        c.section("战略配备", "STRATAGEMS ×4", hint="机甲最多 1 台")
        cw = int((c.width - c.pad * 2 - GUTTER) / 2)
        start = c.y
        strats = lo.get("stratagems") or []
        for i in range(4):
            entry = strats[i] if i < len(strats) else {}
            x = c.pad + (i % 2) * (cw + GUTTER)
            y = start + (i // 2) * (96 + GUTTER)
            _stratagem_card(c, x, y, cw, 96, entry, i + 1)
        c.y = start + 2 * 96 + GUTTER
        c.paragraph("主手 · 副手 · 手雷 · 战略配备 ×4；抽取规则与主站 hd2-loadout 引擎一致。",
                    font=F(11), color=MUTE)
        digest = hashlib.sha256(
            (json.dumps(lo, ensure_ascii=False, sort_keys=True) + LAYOUT_TAG).encode()
        ).hexdigest()[:16]
        out = os.path.join(out_dir, f"roll_{digest}.png")

        def paint(_dr, _img):
            _img.paste(c.img.crop((0, 0, c.width, min(c.y + 4, c.img.height))), (0, 0))
            return c.y + 4

        return pages.render_cached(out, out + ".meta", lo, paint, width=960, tall=2400,
                                   layout_tag=LAYOUT_TAG,
                                   caption="为超级地球而战 · 真理部 · 管理式民主（与 Arrowhead 无隶属关系）")
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(CACHE_DIR, "roll_card.png")
    path = render_roll_card(os.path.dirname(os.path.abspath(out)) or CACHE_DIR)
    print("rendered:", path)
