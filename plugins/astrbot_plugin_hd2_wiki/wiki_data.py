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
import contextvars
from functools import lru_cache
from html import unescape
from collections import OrderedDict
import threading
from astrbot_plugin_hd2_common.search import match_name, normalize, models, RULE_VERSION
logger = logging.getLogger(__name__)
CACHE_DIR = str(Path(__file__).parent / "cache")
WIKI_API = "https://helldivers.wiki.gg/zh/api.php"
WIKI_HOME = "https://helldivers.wiki.gg/zh"
FETCH_TIMEOUT = 8
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

_request = contextvars.ContextVar("wiki_query", default=None)
CATALOGUE = Path(__file__).with_name("names.json")
_resolutions = OrderedDict()
_resolution_lock = threading.Lock()


@lru_cache(maxsize=2)
def _catalogue_at(stamp):
    doc = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    if doc.get("schema") != 1 or not isinstance(doc.get("entries"), list):
        raise ValueError("名称目录格式错误")
    return doc


def catalogue():
    return _catalogue_at(CATALOGUE.stat().st_mtime_ns)


def remaining():
    state = _request.get()
    left = state["deadline"]-time.monotonic() if state else FETCH_TIMEOUT
    if left <= 0:
        raise TimeoutError("查询时间预算已用尽")
    return min(FETCH_TIMEOUT, left)


def _wiki_get(params):
    url = WIKI_API + "?" + urllib.parse.urlencode({"format":"json", **params})
    def validate(value):
        if not isinstance(value,dict) or value.get("error"):
            raise ValueError("Wiki 接口错误")
        node = value.get("parse" if params.get("action")=="parse" else "query")
        if not isinstance(node,dict):
            raise ValueError("Wiki 返回缺少数据字段")
        if params.get("action")=="parse" and not isinstance(node.get("text"),str):
            raise ValueError("Wiki 表格字段缺失")
        if params.get("list") == "search" and not isinstance(node.get("search"),list):
            raise ValueError("Wiki 搜索结果不完整")
        if params.get("prop") != "text" and params.get("list") != "search" and not isinstance(node.get("pages"),dict):
            raise ValueError("Wiki 页面列表不完整")
    start = time.monotonic()
    try:
        return get_json(url, ttl=120, timeout=remaining(), validate=validate, stats=_request.get())
    finally:
        state = _request.get()
        if state is not None:
            state.setdefault("stages",[]).append({"stage":params.get("list") or params.get("action"),
                "elapsed_ms":round((time.monotonic()-start)*1000)})


def _pages(value):
    return list(value["query"]["pages"].values())


def _page_query(title):
    return _wiki_get({"action":"query", "prop":"extracts|pageimages|info|revisions|pageprops",
        "ppprop":"disambiguation", "rvprop":"ids", "titles":title, "explaintext":1,
        "exsectionformat":"plain", "piprop":"thumbnail", "pithumbsize":600, "inprop":"url", "redirects":1})


def _valid(page):
    return bool(page and "missing" not in page and "invalid" not in page and page.get("pageid",-1)>0)


def _extract_tables(html):
    from .tables import extract
    return extract(html)[0]


def _html(page):
    revision = (page.get("revisions") or [{}])[0].get("revid") or page.get("lastrevid")
    if not revision:
        raise ValueError("缺少页面修订，不能混用版本")
    result = _wiki_get({"action":"parse","oldid":revision,"prop":"text","formatversion":2})["parse"]
    text = result.get("text")
    if not isinstance(text,str) or result.get("revid",revision) != revision:
        raise ValueError("Wiki 表格数据或修订不一致")
    return text


def _candidate(page, reason="Wiki 全文", tier=3, rank=0, snippet=""):
    return {"title":page["title"],"pageid":page.get("pageid"),"url":page.get("fullurl") or
        WIKI_HOME+"/wiki/"+urllib.parse.quote(page["title"].replace(" ","_")),
        "reason":reason,"tier":tier,"rank":rank,"distance":0,"snippet":unescape(re.sub(r"<[^>]*>","",snippet))[:180],
        "scope":"未核实", "disambiguation":"disambiguation" in page.get("pageprops",{})}


def _local(query):
    found = []
    for entry in catalogue()["entries"]:
        score = match_name(query,entry["title"],entry["aliases"],entry["verified"])
        if score is None:
            continue
        row = _candidate(entry, score[2],score[0])
        row.update(distance=score[1],scope=entry["scope"],category=entry.get("category"), verified=entry["verified"])
        found.append(row)
    return _merge(found)


def _merge(rows):
    found = {}
    for row in sorted(rows,key=lambda c:(c["tier"],c["distance"],c["rank"],len(c["title"]),c["title"])):
        ident = row.get("pageid") or normalize(row["title"])
        found.setdefault(ident,row)
    return list(found.values())


def _disambiguation(page):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_html(page),"html.parser")
    main = soup.select_one(".mw-parser-output") or soup
    for node in main.select(".navbox, .toc, .mw-editsection, script, style, .hatnote"):
        node.decompose()
    titles = []
    for link in main.select("li a[href], p a[href]"):
        path = urllib.parse.urlparse(urllib.parse.urljoin(WIKI_HOME+"/",link["href"]))
        if path.hostname != "helldivers.wiki.gg" or not path.path.startswith("/zh/wiki/"):
            continue
        title = urllib.parse.unquote(path.path.split("/zh/wiki/",1)[1]).replace("_"," ")
        if title and ":" not in title and title != page["title"] and title not in titles:
            titles.append(title)
    if not titles:
        return [], False
    info = _wiki_get({"action":"query","prop":"info|pageprops","ppprop":"disambiguation","inprop":"url",
                      "titles":"|".join(titles[:40])})
    return [_candidate(p,"消歧义页面",1,i) for i,p in enumerate(_pages(info)) if _valid(p)], len(titles)<=40


def wiki_lookup(keyword, include_tables=True, deadline=None):
    start = time.monotonic()
    state = {"deadline":deadline or start+20,"requests":0,"cache_hits":0,"coalesced":0}
    token = _request.set(state)
    kw = (keyword or "").strip()
    result = {"status":"unavailable","title":kw,"query":kw,"url":WIKI_HOME+"/index.php?"+urllib.parse.urlencode({"search":kw}),
        "rev":"none","text":"","tables":[],"thumb":None,"candidates":[],"table_status":"not_requested",
        "search_complete":False,"has_more":False,"warnings":[],"source":WIKI_API,"rule_version":RULE_VERSION}
    rows = []
    try:
        if not kw or len(kw)>160 or "|" in kw or "\x00" in kw:
            raise ValueError("请输入 1–160 字的单个查询，不支持 | 分隔查询")
        remaining()
        result["catalogue_version"] = catalogue()["version"]
        rows = _local(kw)
        try:
            from rapidfuzz.distance import OSA  # optional for direct lookup
        except ImportError:
            result["warnings"].append("名称纠错依赖未安装，仍可精确查询和搜索 Wiki")
        exact = [r for r in rows if r["tier"]==0]
        if len(exact)>1:
            result.update(status="ambiguous", candidates=exact, search_complete=True)
            return result
        resolution_key = (WIKI_API,normalize(kw),result["catalogue_version"],RULE_VERSION)
        with _resolution_lock:
            cached = _resolutions.get(resolution_key)
        cached = cached if cached and time.monotonic()-cached[0]<60 else None
        target = exact[0]["title"] if exact else cached[1] if cached else kw
        state["resolution_cache"] = bool(cached)
        pages = _pages(_page_query(target))
        page = pages[0] if pages else None
        if _valid(page) and models(kw) and models(kw) != models(page["title"]):
            result.update(status="ambiguous",candidates=[_candidate(page,"型号与目标冲突，请确认",1)])
            return result
        if _valid(page) and cached and page["pageid"] != cached[2]:
            with _resolution_lock: _resolutions.pop(resolution_key,None)
            result.update(status="ambiguous",candidates=[_candidate(page,"缓存目标已变化，请确认",1)])
            return result
        if _valid(page) and exact and page["pageid"] != exact[0]["pageid"]:
            result.update(status="ambiguous",candidates=[_candidate(page,"名称目录目标已变化，请确认",1)])
            return result
        if _valid(page) and "disambiguation" in page.get("pageprops",{}):
            result.update(status="ambiguous",title=page["title"],url=page.get("fullurl",result["url"]))
            try:
                choices, complete = _disambiguation(page)
                rows = _merge(rows+choices)
                result["search_complete"] = complete
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                result["warnings"].append("消歧义候选未完整取得："+type(error).__name__)
            result["candidates"] = rows
            return result
        if not _valid(page):
            continuation = {}
            for batch in range(3):
                search = _wiki_get({"action":"query","list":"search","srsearch":kw,"srnamespace":0,
                    "srlimit":20,"srprop":"snippet|redirecttitle","srinfo":"totalhits|suggestion|rewrittenquery",**continuation})
                hits = search["query"]["search"]
                result["search_info"] = search["query"].get("searchinfo",{})
                if hits:
                    info = _wiki_get({"action":"query","prop":"info|pageprops","ppprop":"disambiguation","inprop":"url",
                                      "titles":"|".join(h["title"] for h in hits)})
                    indexed = {p["title"]:p for p in _pages(info) if _valid(p)}
                    for rank,hit in enumerate(hits):
                        p = indexed.get(hit["title"])
                        if not p: continue
                        score = match_name(kw,p["title"], [hit["redirecttitle"]] if hit.get("redirecttitle") else [])
                        c = _candidate(p,score[2] if score else "Wiki 全文（归属未核实）", score[0] if score else 3,
                                       batch*20+rank,hit.get("snippet",""))
                        c["distance"] = score[1] if score else 0
                        rows.append(c)
                continuation = search.get("continue") or {}
                result["has_more"] = bool(continuation)
                result["search_complete"] = not continuation
                if not continuation: break
            rows = _merge(rows)
            exact = [r for r in rows if r["tier"]==0 and not r["disambiguation"]]
            if len(exact)==1 and result["search_complete"]:
                page = next(iter(_pages(_page_query(exact[0]["title"]))),None)
                if not _valid(page) or "disambiguation" in page.get("pageprops",{}):
                    page = None
            if not _valid(page):
                result.update(status="ambiguous" if rows else "not_found" if result["search_complete"] else "unavailable",candidates=rows)
                return result
        result.update(status="resolved",title=page["title"],pageid=page["pageid"],url=page.get("fullurl",result["url"]),
            rev=str((page.get("revisions") or [{}])[0].get("revid") or page.get("lastrevid") or "0"),search_complete=True)
        with _resolution_lock:
            _resolutions[resolution_key] = (time.monotonic(),page["title"],page["pageid"])
            _resolutions.move_to_end(resolution_key)
            while len(_resolutions)>128: _resolutions.popitem(last=False)
        result["text"], result["truncated"] = _clean_extract(page.get("extract"))
        if include_tables:
            result["thumb"] = (page.get("thumbnail") or {}).get("source")
            try:
                from .tables import extract
                tables, bad = extract(_html(page))
                result.update(tables=tables,table_status="unsupported" if bad else "available" if tables else "none",
                              unsupported_tables=bad)
            except (OSError, ValueError, RuntimeError, ImportError) as error:
                result["table_status"] = "unavailable"
                result["warnings"].append("表格暂不可用："+type(error).__name__)
        return result
    except (OSError, ValueError, RuntimeError) as error:
        result.update(status="ambiguous" if rows else "unavailable",candidates=_merge(rows),search_complete=False)
        result["warnings"].append(str(error) if isinstance(error,(ValueError,TimeoutError)) else type(error).__name__)
        return result
    finally:
        result["has_more"] = result["has_more"] or not result["search_complete"]
        result["diagnostics"] = {k:v for k,v in state.items() if k!="deadline"}
        result["diagnostics"]["elapsed_ms"] = round((time.monotonic()-start)*1000)
        _request.reset(token)


def format_text(value):
    status = value["status"]
    lines = ["📖 "+value["title"]]
    if status in ("resolved","ok"):
        lines.extend([value.get("text") or "该词条暂无正文摘要。", "正文摘要；完整数据见原文。"])
        if value.get("table_status") in ("unavailable","unsupported"):
            lines.append("表格未完整取得，请查看原文。")
    elif status=="ambiguous":
        lines.append("请使用完整词条名选择：")
    elif status=="not_found":
        lines.append("本次搜索未找到相关词条。")
    else:
        lines.append("Wiki 数据暂不可用，尚不能判断是否存在该词条。")
    candidates = value.get("candidates",[])
    for i,c in enumerate(candidates[:5],1):
        lines.append(f"{i}. {c['title']}（{c['reason']}）\n/wiki {c['title']}")
        if c.get("snippet"): lines.append(c["snippet"])
    if len(candidates)>5:
        lines.append(f"另有 {len(candidates)-5} 个候选；可补充名称或查看搜索链接。")
    if not value.get("search_complete",True):
        lines.append("搜索尚未完成，以上仅为已取得的结果。")
    lines.extend(value.get("warnings",[]))
    lines.append(value["url"])
    return "\n".join(lines)



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
        text = (cut[:nl] if nl > TEXT_LIMIT // 2 else cut).rstrip() + "\n……（正文较长，完整内容见原页）"
        truncated = True
    return text, truncated

def _fetch_thumb(src, deadline=None):
    """下载页面主图（只收 png/jpg/jpeg 位图；svg 交给无图版式）→ 本地缓存路径或 None"""
    if not src:
        return None
    parsed = urllib.parse.urlparse(src)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".wiki.gg"):
        return None
    low = src.split("?")[0].lower()
    if not low.endswith((".png", ".jpg", ".jpeg")):
        return None
    ext = ".png" if low.endswith(".png") else ".jpg"
    cache_file = os.path.join(CACHE_DIR, "wiki",
                              "thumb_" + hashlib.md5(src.encode()).hexdigest()[:16] + ext)
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 500:
        try:
            from PIL import Image
            with Image.open(cache_file) as cached: cached.verify()
            return cache_file
        except (OSError, ValueError):
            pass
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        req = urllib.request.Request(src, headers={"User-Agent": "HD2Bot/1.0 (wiki-card)"})
        timeout = min(FETCH_TIMEOUT, deadline-time.monotonic()) if deadline else FETCH_TIMEOUT
        if timeout <= 0: return None
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(8*1024*1024+1)
        if len(data)>8*1024*1024: return None
        if len(data) < 500:
            return None
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as fetched: fetched.verify()
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(cache_file),prefix=".thumb_")
        try:
            with os.fdopen(fd,"wb") as stream: stream.write(data)
            os.replace(tmp,cache_file)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return cache_file
    except Exception as e:
        logger.warning(f"[HD2-Wiki] 主图下载失败: {e}")
        return None
