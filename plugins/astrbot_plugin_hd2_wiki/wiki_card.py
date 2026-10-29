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
        F, _hud_head, _help_wrap,
        CACHE_DIR,
    )
except Exception as _e:  # 渲染基建不可用 → 整卡降级，main.py 回退文本
    raise ImportError(f"report_card 样式基建导入失败: {_e}")

from bs4 import BeautifulSoup  # AstrBot 后端自带（4.15+，lxml 解析器）

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


def _wiki_get(params):
    """MediaWiki api.php GET，重试 2 次，返回 JSON（带 error 抛错）。"""
    qs = urllib.parse.urlencode({"format": "json", **params})
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(
                WIKI_API + "?" + qs,
                headers={"User-Agent": "HD2Bot/1.0 (wiki-card; read-only)"})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            if data.get("error"):
                raise RuntimeError("Wiki API 错误 %s: %s"
                                   % (data["error"].get("code"), data["error"].get("info")))
            return data
        except Exception as e:
            last = e
    raise last


def _clean_extract(ext):
    """纯文本清洗：剥维护模板残留行、压缩连续空行、按上限截断。返回 (text, truncated)"""
    lines, out = (ext or "").splitlines(), []
    for ln in lines:
        s = ln.strip()
        if not s:
            if out and out[-1] != "":
                out.append("")
            continue
        if WIKI_JUNK_RE.search(s):
            continue
        out.append(s)
    text = "\n".join(out).strip()
    truncated = False
    if len(text) > TEXT_LIMIT:
        cut = text[:TEXT_LIMIT]
        nl = cut.rfind("\n")
        text = (cut[:nl] if nl > TEXT_LIMIT // 2 else cut).rstrip() + "\n……（正文较长，数据见下方表格与原页）"
        truncated = True
    return text, truncated


def _fetch_thumb(src):
    """下载页面主图（只收 png/jpg/jpeg 位图；svg 交给无图版式）→ 本地缓存路径或 None"""
    if not src:
        return None
    low = src.split("?")[0].lower()
    if not low.endswith((".png", ".jpg", ".jpeg")):
        return None
    ext = ".png" if low.endswith(".png") else ".jpg"
    cache_file = os.path.join(CACHE_DIR, "wiki",
                              "thumb_" + hashlib.md5(src.encode()).hexdigest()[:16] + ext)
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 500:
        return cache_file
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        req = urllib.request.Request(src, headers={"User-Agent": "HD2Bot/1.0 (wiki-card)"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            data = r.read()
        if len(data) < 500:
            return None
        open(cache_file, "wb").write(data)
        return cache_file
    except Exception as e:
        logger.warning(f"[HD2-Wiki] 主图下载失败: {e}")
        return None


# ── 表格抽取（action=parse 的 HTML → 结构化行） ──

def _cell_text(cell):
    """单元格文本：<br> 转 ' / '，剥标签，压空白，截 CELL_MAX。"""
    for br in cell.find_all("br"):
        br.replace_with(" / ")
    txt = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
    return txt[:CELL_MAX]


def _extract_tables(html):
    """parse HTML → [{"section", "title", "rows": [[cell,...],...]}]
    跳过：tabs 切换器 / navbox / <2 行的表；表格归属最近的 h2/h3 小节。"""
    soup = BeautifulSoup(html, "lxml")
    for sel in ("div.toc", "sup.reference", "div.mw-references-wrap",
                "style", "script", "table.navbox", "div.navbox"):
        for x in soup.select(sel):
            x.decompose()
    out, section, seen = [], "", set()
    for el in soup.find_all(["h2", "h3", "table"]):
        if el.name in ("h2", "h3"):
            h = re.sub(r"\[.*?\]\s*$", "", el.get_text(" ", strip=True)).strip()
            h = h.replace("编辑", "").strip()
            if h:
                section = h
            continue
        cls = " ".join(el.get("class") or [])
        if "tabs" in cls or "navbox" in cls:
            continue
        raw = []
        for tr in el.find_all("tr"):
            cs = tr.find_all(["th", "td"])
            cells = [_cell_text(c) for c in cs]
            if any(cells):
                raw.append((cs, cells))
        if len(raw) < 2:
            continue
        # 表名：首行单格（或全 th）视作标题带；否则用小节名
        first_cs, first_cells = raw[0]
        is_band = len(first_cells) == 1 or all(c.name == "th" for c in first_cs)
        title = first_cells[0] if is_band else ""
        rows = [cells for _, cells in (raw[1:] if is_band else raw)]
        rows = rows[:TABLE_ROWS_MAX]
        if len(rows) < 1:
            continue
        key = (section, title)
        if key in seen:
            continue
        seen.add(key)
        out.append({"section": section or "数据", "title": title,
                    "rows": [r for r in rows if any(r)]})
        if len(out) >= TABLES_MAX:
            break
    return out


def wiki_lookup(keyword):
    """搜索 + 抽取主流程 → dict(status/title/url/rev/text/tables/thumb/candidates)"""
    kw = (keyword or "").strip()
    title = None
    pg = next(iter((_wiki_get({
        "action": "query", "prop": "extracts|pageimages|info", "titles": kw,
        "explaintext": 1, "exsectionformat": "plain", "piprop": "thumbnail",
        "pithumbsize": 600, "inprop": "url", "redirects": 1,
    }).get("query") or {}).get("pages", {}).values()), None)
    if pg and pg.get("missing") is None:
        title = pg
    candidates = []
    # 直取失败 → 搜索（重定向/消歧义过滤，mediawiki.js search 同款两步）
    if title is None:
        s = _wiki_get({"action": "query", "list": "search", "srsearch": kw,
                       "srlimit": 6, "srnamespace": 0})
        hits = [h["title"] for h in (s.get("query") or {}).get("search", [])]
        if hits:
            info = _wiki_get({"action": "query", "prop": "info|pageprops",
                              "pprop": "disambiguation",
                              "titles": "|".join(hits)})
            bad = set()
            for p in (info.get("query") or {}).get("pages", {}).values():
                if p.get("redirect") is not None or \
                        (p.get("pageprops") or {}).get("disambiguation") is not None:
                    bad.add(p.get("title"))
            good = [h for h in hits if h not in bad]
            candidates = good[1:5]
            if good:
                pg = next(iter((_page_query(good[0]).get("query") or {})
                               .get("pages", {}).values()), None)
                if pg and pg.get("missing") is None:
                    title = pg
    if title is None:
        return {"status": "not_found", "title": kw, "url": WIKI_HOME,
                "rev": "none", "text": "", "tables": [], "thumb": None,
                "candidates": candidates}
    text, truncated = _clean_extract(title.get("extract"))
    # 表格：action=parse（重定向跟随），revid 用 parse 的（同一次页面版本）
    tables, revid = [], str(title.get("lastrevid") or "0")
    try:
        pr = _wiki_get({"action": "parse", "page": title.get("title") or kw,
                        "prop": "text", "formatversion": 2, "redirects": 1})
        pp = pr.get("parse") or {}
        revid = str(pp.get("revid") or revid)
        t = pp.get("text") or ""
        if isinstance(t, list):        # formatversion=2 下 text 是字符串数组
            t = "".join(t)
        tables = _extract_tables(t)
    except Exception as e:
        logger.warning(f"[HD2-Wiki] 表格抽取失败（仅出正文）: {e}")
    return {
        "status": "ok",
        "title": title.get("title") or kw,
        "url": title.get("fullurl") or WIKI_HOME,
        "rev": revid,
        "text": text,
        "tables": tables,
        "thumb": _fetch_thumb((title.get("thumbnail") or {}).get("source")),
        "candidates": candidates,
        "truncated": truncated,
    }


def _page_query(title):
    """词条直取（redirects=1 跟随重定向）：extract + 主图缩略 + url + revid。"""
    return _wiki_get({
        "action": "query",
        "prop": "extracts|pageimages|info",
        "titles": title,
        "explaintext": 1,
        "exsectionformat": "plain",
        "piprop": "thumbnail",
        "pithumbsize": 600,
        "inprop": "url",
        "redirects": 1,
    })


def _slug(s):
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", (s or "")[:24]).strip("_") or "w"


def _render_wiki_cached(out_path, meta_path, rev, draw_fn):
    """wiki 专用缓存外壳：长图画布（WIKI_TALL），其余与 report_card._render_cached 同款。"""
    try:
        if os.path.exists(out_path) and os.path.exists(meta_path):
            if json.loads(open(meta_path, encoding="utf-8").read()).get("rev") == rev:
                return out_path
    except Exception:
        pass
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (W, WIKI_TALL), BG)
        dr = ImageDraw.Draw(img)
        y = draw_fn(dr, img)
        img = img.crop((0, 0, W, min(WIKI_TALL, y + 20)))
        tmp = out_path + ".tmp"
        img.save(tmp, format="PNG")
        os.replace(tmp, out_path)
        try:
            open(meta_path, "w", encoding="utf-8").write(
                json.dumps({"rev": rev, "ts": __import__("time").time()}))
        except Exception:
            pass
        return out_path
    except Exception:
        import traceback
        traceback.print_exc()
        return None


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
                if y > WIKI_TALL - 400:
                    break
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


def render_wiki_card(keyword, out_dir=None):
    """/wiki 长图卡：搜索→抽取正文与表格→渲染。失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        data = wiki_lookup(keyword)
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
