# -*- coding: utf-8 -*-
"""/wiki 图片卡渲染器（astrbot_plugin_hd2_wiki）

搜索并抽取 helldivers.wiki.gg 中文站词条（MediaWiki API，只读匿名），
内容 = 正文纯文本摘要（prop=extracts）+ 数据表（action=parse 的 HTML 经
bs4 解析出全部 wikitable），渲染成黑金 HUD **长图**（允许 9000px 高）。

为什么不会出现「[编辑|编辑源代码]」「This section of the article is empty」：
这些是 wiki 页面 HTML 渲染层的界面元素/维护模板；纯文本 extract 天然不含，
表格从 parse HTML 抽取时也只取 <table> 的 th/td 数据单元格，标题文本另外
剥掉「[编辑|编辑源代码]」尾巴；渲染层再按行过滤一次双保险（WIKI_JUNK_RE）。

API 语义参照站内既有 JS 客户端（mediawiki.js）：搜索（list=search）→
重定向/消歧义过滤（prop=info|pageprops）→ redirects=1 直取目标页。
HD1 词条：zh 站基本未收录（探测「决裁者」0 结果），不做复杂过滤，保留扩展位。
"""
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request

# 样式与缓存基建：跨插件复用 war_report 的 report_card（lookup 同款模式）
import os as _os
import sys as _sys
_PLUGINS_PARENT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PLUGINS_PARENT not in _sys.path:
    _sys.path.insert(0, _PLUGINS_PARENT)

from astrbot.api import logger

try:
    from astrbot_plugin_hd2_war_report.report_card import (
        W, PAD, BG, PANEL, LINE, LINE2, TEXT, DIM, MUTE, GOLD, GOLD2, BRASS,
        BLUE, GREEN, RED,
        F, _hud_head, _help_wrap, _render_cached,
        CACHE_DIR,
    )
except Exception as _e:  # 渲染基建不可用 → 整卡降级，main.py 回退文本
    raise ImportError(f"report_card 样式基建导入失败: {_e}")

from astrbot_plugin_hd2_wiki.wiki_data import wiki_lookup, _fetch_thumb

WIKI_API = "https://helldivers.wiki.gg/zh/api.php"
WIKI_HOME = "https://helldivers.wiki.gg/zh"
FETCH_TIMEOUT = 25
TEXT_LIMIT = 900           # 正文摘要上限（字符）；数据主要在表格
THUMB_BOX = 236            # 主图框边长
WIKI_TALL = 9000           # 长图画布上限（按内容裁切）
TABLES_MAX = 8             # 最多渲染的表格数
TABLE_ROWS_MAX = 26        # 单表最多数据行（超出截断提示）
CELL_MAX = 60              # 单元格文本上限

# wiki 界面/维护残留（extract 双保险逐行过滤）
WIKI_JUNK_RE = re.compile(
    r"This section of (?:the )?article is empty|You can help by adding"
    r"|^\s*\[\s*编辑.*?\]\s*$|^\s*编辑\s*$|^\s*编辑源代码\s*$")








# ── 表格抽取（action=parse 的 HTML → 结构化行） ──









def _slug(s):
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", (s or "")[:24]).strip("_") or "w"


def _render_wiki_cached(out_path, meta_path, rev, draw_fn):
    return _render_cached(out_path, meta_path, rev, draw_fn)


def _draw_table_block(dr, y, tb):
    """单个数据表块：小节名（变化时）+ 表名带 + 行。返回 (y, drawn_section)"""
    if tb["section"] and tb["section"] != _draw_table_block.last_section:
        _draw_table_block.last_section = tb["section"]
        dr.text((PAD, y), tb["section"], font=F(21, True), fill=GOLD)
        y += 36
    if tb["title"]:
        dr.rectangle([PAD, y, W - PAD, y + 34], fill=(26, 20, 10), outline=BRASS)
        dr.text((PAD + 12, y + 5), tb["title"], font=F(19, True), fill=GOLD2)
        y += 40
    ncols = max(len(r) for r in tb["rows"])
    col_w = (W - PAD * 2 - 24) / max(1, ncols)
    for r in tb["rows"]:
        dr.line([(PAD + 10, y), (W - PAD - 10, y)], fill=LINE)
        if len(r) == 1:  # 单格行 → 表内分区带
            dr.rectangle([PAD, y + 1, W - PAD, y + 29], fill=(26, 20, 10))
            dr.text((PAD + 12, y + 4), r[0], font=F(17, True), fill=GOLD2)
        else:
            dr.text((PAD + 12, y + 5), r[0], font=F(15, True), fill=DIM)
            if ncols == 2:
                vw = dr.textlength(r[-1], font=F(16, True))
                dr.text(((PAD + col_w * (ncols - 1)) + (col_w - vw) / 2 + 6, y + 4),
                        r[-1], font=F(16, True), fill=TEXT)
            else:
                for ci in range(1, len(r)):
                    vw = dr.textlength(r[ci], font=F(15))
                    vx = PAD + 12 + col_w * ci + (col_w - vw) / 2
                    dr.text((vx, y + 6), r[ci], font=F(15), fill=TEXT)
        y += 30
        for ln in _help_wrap("正文与表格为摘要；最多8表、每表26行，完整内容见原文链接。", F(16), W - PAD*2, dr):
            dr.text((PAD, y), ln, font=F(16), fill=MUTE)
            y += 24
    return y + 8


_draw_table_block.last_section = ""


def _draw_wiki(kw, data):
    def draw(dr, img):
        _draw_table_block.last_section = ""
        y = _hud_head(dr, img, "Wiki 档案", "HELLDIVERS WIKI · ZH")
        if data.get("status") == "not_found":
            dr.text((PAD, y + 8), "未找到相关词条", font=F(30, True), fill=GOLD2)
            y += 56
            for ln in _help_wrap("在 helldivers.wiki.gg 中文站没有搜到「%s」。"
                                 "试试更短的关键词（武器型号、敌人名、机制名）。" % kw,
                                 F(19), W - PAD * 2, dr):
                dr.text((PAD, y), ln, font=F(19), fill=TEXT)
                y += 28
            dr.text((PAD, y + 10), "直达站内搜索：" + WIKI_HOME + "/search?search=" +
                    urllib.parse.quote(kw), font=F(14), fill=MUTE)
            y += 40
            return y + 6

        # 标题 + 查询回显 + 链接
        dr.text((PAD, y + 2), data["title"], font=F(34, True), fill=GOLD2)
        y += 48
        if data["title"] != kw:
            dr.text((PAD, y), "查询：%s" % kw, font=F(14), fill=MUTE)
            y += 22
        show_url = urllib.parse.unquote(data["url"]).replace("https://", "")
        dr.text((PAD, y), show_url[:96], font=F(13), fill=BLUE)
        y += 30

        text = data["text"] or ""
        thumb = data.get("thumb")
        tw = THUMB_BOX
        tx0 = W - PAD - tw
        # 主图（右上，紧贴标题下；无图则文本全宽）
        if thumb:
            try:
                from PIL import Image, ImageOps
                box = ImageOps.fit(Image.open(thumb).convert("RGB"), (tw, tw))
                img.paste(box, (tx0, y + 4))
                dr.rectangle([tx0, y + 4, tx0 + tw, y + 4 + tw], outline=LINE2)
            except Exception as e:
                logger.warning(f"[HD2-Wiki] 主图绘制失败: {e}")
                thumb = None
        text_w_full = W - PAD * 2
        text_w_side = text_w_full - tw - 24 if thumb else text_w_full
        lh = 27
        side_bottom = (y + 4 + tw + 14) if thumb else 0
        if text:
            # 正文折行：主图高度内按侧栏宽度排，越过后剩余文本按全宽重排
            # 小节标题判定（extract plain 格式）：短行 + 无句末标点 + 前一行为空 → 金色加粗
            all_side = _help_wrap(text, F(18), text_w_side, dr)
            side_n, yy = 0, y
            while side_n < len(all_side) and yy + lh <= side_bottom:
                side_n += 1
                yy += lh
            head, rest = all_side[:side_n], all_side[side_n:]
            if rest:
                rest = _help_wrap("\n".join(rest), F(18), text_w_full, dr)
            lines = head + rest
            sec, prev_blank = set(), False
            for i, ln in enumerate(lines):
                if not ln:
                    prev_blank = True
                    continue
                if (prev_blank and len(ln) <= 12
                        and not re.search(r"[。，、！？：…「」（）]$", ln)):
                    sec.add(i)
                prev_blank = False
            for i, ln in enumerate(lines):
                if thumb and i == side_n and y < side_bottom:
                    y = side_bottom                     # 切到全宽行前先越过图底
                if ln == "":
                    y += 14                             # 空行压半高（段落间距）
                    continue
                if i in sec:
                    dr.text((PAD, y), ln, font=F(19, True), fill=GOLD2)
                else:
                    dr.text((PAD, y), ln, font=F(18), fill=TEXT)
                y += lh
            y += 10
        elif thumb:
            y = max(y + tw + 18, side_bottom)

        # 数据表（长图主体）
        if data.get("tables"):
            dr.line([(0, y), (W, y)], fill=LINE2)
            y += 14
            for tb in data["tables"]:
                y = _draw_table_block(dr, y, tb)
            y += 4

        if data.get("candidates"):
            cand = "相关词条：" + " · ".join(data["candidates"])
            for ln in _help_wrap(cand, F(15), text_w_full, dr):
                dr.text((PAD, y), ln, font=F(15), fill=BRASS)
                y += 22
            y += 4
        # 页脚
        dr.line([(0, y), (W, y)], fill=LINE2)
        y += 12
        foot = "数据来自 helldivers.wiki.gg 中文站（匿名只读 API） · 页面版本 #%s" % data.get("rev", "0")
        dr.text((PAD, y), foot, font=F(14), fill=MUTE)
        y += 24
        return y + 6
    return draw


def render_wiki_card(keyword, out_dir=None, data=None):
    """/wiki 长图卡：搜索→抽取正文与表格→渲染。失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        data = dict(data) if data is not None else wiki_lookup(keyword)
        if data.get("thumb") and str(data["thumb"]).startswith("http"):
            data["thumb"] = _fetch_thumb(data["thumb"])
        out_path = os.path.join(out_dir, "wiki_%s.png" % _slug(data["title"]))
        meta_path = out_path + ".meta.json"
        # rev = 词条标题 + 页面修订号：wiki 一编辑，缓存自动失效重渲染
        return _render_wiki_cached(out_path, meta_path,
                                   "wiki:%s:%s" % (data["title"], data["rev"]),
                                   _draw_wiki(keyword, data))
    except Exception:
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    import sys as _s
    kw = _s.argv[1] if len(_s.argv) > 1 else "轨道120MM高爆弹火力网"
    print("rendered:", render_wiki_card(kw, os.path.join(CACHE_DIR, "wiki_test")))
