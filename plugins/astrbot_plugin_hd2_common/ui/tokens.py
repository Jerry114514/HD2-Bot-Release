"""统一设计 Token：黑曜石琥珀金（Obsidian Amber）。

所有图片卡共用一套色板与字号阶梯；金色只用于主标题、关键焦距与 MO 进度，
结构线与常规标签用冷灰 / 全息青，语义状态用高对比前景 + 低饱和衬底胶囊。

字体统一走 :mod:`astrbot_plugin_hd2_common.fonts`（跨平台 + HD2_FONT 覆盖），
禁止在业务插件里硬编码字体路径。
"""
from __future__ import annotations

from PIL import ImageFont

from astrbot_plugin_hd2_common.fonts import resolve_font_pair

# ── 画布与层级 ────────────────────────────────────────────────
BG = (11, 13, 18)          # 画布底色
WELL = (14, 17, 24)        # 内嵌槽 / 图标底
CARD = (19, 23, 32)        # 常规卡片
CARD2 = (26, 31, 43)       # 次级卡片 / 斑马纹亮行
LINE = (40, 48, 64)        # 结构线
LINE2 = (56, 68, 90)       # 强调结构线 / 描边

# ── 文字阶梯 ──────────────────────────────────────────────────
TEXT = (244, 245, 247)     # 主文字
DIM = (176, 184, 198)      # 次级文字
MUTE = (118, 128, 146)     # 辅助标签

# ── 品牌与强调 ────────────────────────────────────────────────
AMBER = (245, 197, 24)     # 主聚焦 / MO 进度
AMBER_BG = (48, 40, 14)    # 金色低饱和衬底
AMBER_DIM = (255, 224, 102)
CYAN = (56, 189, 248)      # 结构引导 / 副强调
CYAN_BG = (16, 48, 72)
BRASS = (199, 176, 31)     # 暗金（页脚 / 次级强调）

# ── 语义状态 ──────────────────────────────────────────────────
GREEN = (52, 211, 153)
GREEN_BG = (16, 52, 42)
RED = (248, 113, 113)
RED_BG = (58, 22, 26)
VIOLET = (167, 139, 250)
VIOLET_BG = (38, 30, 58)
ORANGE = (251, 146, 60)
ORANGE_BG = (56, 32, 12)

# 旧名兼容：/战报 等模块仍引用 GOLD/BLUE/PANEL 等符号
GOLD = AMBER
GOLD2 = AMBER_DIM
BLUE = CYAN
PANEL = CARD
SUPPORT = ORANGE

# ── 阵营色（只做细光条 / 胶囊 / 进度条，不铺满边框） ────────────
FACTION = {
    "超级地球": CYAN,
    "终结族": AMBER,
    "机器人": ORANGE,
    "光能者": VIOLET,
    "未知": MUTE,
}
FACTION_BG = {
    "超级地球": CYAN_BG,
    "终结族": AMBER_BG,
    "机器人": ORANGE_BG,
    "光能者": VIOLET_BG,
    "未知": (30, 34, 42),
}

# ── 版式栅格 ──────────────────────────────────────────────────
W = 960                    # 单栏标准宽度（手机缩略图可读）
PAD = 32                   # 画布左右留白
GUTTER = 20                # 双栏/网格间隙
RADIUS = 10                # 微圆角
TALL = 6400                # 初始画布高度（按内容裁剪）
FOOTER_H = 44

_fcache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def F(sz: int, bold: bool = False):
    """统一字号入口（CJK 小字号 +2pt，与旧模板视觉体积一致）。"""
    if sz <= 24:
        sz += 2
    key = (sz, bold)
    if key not in _fcache:
        reg, bold_p = resolve_font_pair()
        _fcache[key] = ImageFont.truetype(bold_p if bold else reg, sz, index=0)
    return _fcache[key]


def faction_color(name: str) -> tuple[int, int, int]:
    return FACTION.get(str(name or ""), MUTE)


def faction_bg(name: str) -> tuple[int, int, int]:
    return FACTION_BG.get(str(name or ""), (30, 34, 42))
