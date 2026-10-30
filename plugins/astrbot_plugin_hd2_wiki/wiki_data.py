"""Independent MediaWiki lookup and optional table extraction."""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import logging
from pathlib import Path
from astrbot_plugin_hd2_common.data import get_json
logger = logging.getLogger(__name__)
CACHE_DIR = str(Path(__file__).parent / "cache")
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
    url = WIKI_API + "?" + urllib.parse.urlencode({"format":"json",**params})
    result = get_json(url, ttl=120, timeout=FETCH_TIMEOUT)
    if result.get("error"):
        raise RuntimeError("Wiki数据不可用")
    return result

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

def _cell_text(cell):
    """单元格文本：<br> 转 ' / '，剥标签，压空白，截 CELL_MAX。"""
    for br in cell.find_all("br"):
        br.replace_with(" / ")
    txt = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
    return txt if len(txt) <= CELL_MAX else txt[:CELL_MAX-1] + "…"

def _extract_tables(html):
    """parse HTML → [{"section", "title", "rows": [[cell,...],...]}]
    跳过：tabs 切换器 / navbox / <2 行的表；表格归属最近的 h2/h3 小节。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
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
        "action": "query", "prop": "extracts|pageimages|info|revisions", "rvprop": "ids", "titles": kw,
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
    tables, revid = [], str((title.get("revisions") or [{}])[0].get("revid") or title.get("lastrevid") or "0")
    try:
        pr = _wiki_get({"action": "parse", "oldid": revid,
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
        "thumb": (title.get("thumbnail") or {}).get("source"),
        "candidates": candidates,
        "truncated": truncated,
    }

def _page_query(title):
    """词条直取（redirects=1 跟随重定向）：extract + 主图缩略 + url + revid。"""
    return _wiki_get({
        "action": "query",
        "prop": "extracts|pageimages|info|revisions", "rvprop": "ids",
        "titles": title,
        "explaintext": 1,
        "exsectionformat": "plain",
        "piprop": "thumbnail",
        "pithumbsize": 600,
        "inprop": "url",
        "redirects": 1,
    })
