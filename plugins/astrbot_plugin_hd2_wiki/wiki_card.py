# -*- coding: utf-8 -*-
"""/wiki 图片卡渲染器（astrbot_plugin_hd2_wiki）

搜索并抽取 helldivers.wiki.gg 中文站词条（MediaWiki API，只读匿名），
纯文本 extract + 页面主图渲染成黑金 HUD 卡。样式基建复用
astrbot_plugin_hd2_war_report.report_card（跨插件导入，lookup 同款模式）。

为什么不会出现「[编辑|编辑源代码]」「This section of the article is empty」：
这两类都是 wiki 页面 HTML 渲染层的界面元素/维护模板，MediaWiki 的
prop=extracts&explaintext=1 只返回正文纯文本，天然不包含；渲染层再按行
过滤一次作为双保险（WIKI_JUNK_RE）。

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
        F, _hud_head, _render_cached, _help_wrap,
        CACHE_DIR,
    )
except Exception as _e:  # 渲染基建不可用 → 整卡降级，main.py 回退文本
    raise ImportError(f"report_card 样式基建导入失败: {_e}")

WIKI_API = "https://helldivers.wiki.gg/zh/api.php"
WIKI_HOME = "https://helldivers.wiki.gg/zh"
FETCH_TIMEOUT = 20
TEXT_LIMIT = 1500          # 卡面正文上限（字符），超出截断提示去原页
THUMB_BOX = 236            # 主图框边长

# wiki 界面/维护残留（extract 理论上没有，双保险逐行过滤）
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
        text = (cut[:nl] if nl > TEXT_LIMIT // 2 else cut).rstrip() + "\n……（全文较长，完整内容见原页）"
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


def wiki_lookup(keyword):
    """搜索 + 抽取主流程 → dict(status/title/url/rev/text/thumb/candidates/truncated)"""
    kw = (keyword or "").strip()
    # 1. 精确/重定向直取
    pages = [_page_query(kw)]
    title = None
    for data in pages:
        pg = next(iter((data.get("query") or {}).get("pages", {}).values()), None)
        if pg and pg.get("missing") is None and pg.get("extract"):
            title = pg
            break
    candidates = []
    # 2. 直取失败 → 搜索（重定向/消歧义过滤，mediawiki.js search 同款两步）
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
                "rev": "none", "text": "", "thumb": None,
                "candidates": candidates, "truncated": False}
    text, truncated = _clean_extract(title.get("extract"))
    return {
        "status": "ok",
        "title": title.get("title") or kw,
        "url": title.get("fullurl") or WIKI_HOME,
        "rev": str(title.get("lastrevid") or "0"),
        "text": text,
        "thumb": _fetch_thumb((title.get("thumbnail") or {}).get("source")),
        "candidates": candidates,
        "truncated": truncated,
    }


def _slug(s):
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", (s or "")[:24]).strip("_") or "w"


def _draw_wiki(kw, data):
    def draw(dr, img):
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

        text = data["text"] or "（该词条暂无正文内容）"
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
        # 正文折行：主图高度内按侧栏宽度排，越过后剩余文本按全宽重排
        all_side = _help_wrap(text, F(18), text_w_side, dr)
        side_n, yy = 0, y
        while side_n < len(all_side) and yy + lh <= side_bottom:
            side_n += 1
            yy += lh
        head, rest = all_side[:side_n], all_side[side_n:]
        if rest:
            rest = _help_wrap("\n".join(rest), F(18), text_w_full, dr)
        lines = head + rest
        # 小节标题判定（extract plain 格式）：短行 + 无句末标点 + 前一行为空 → 金色加粗
        sec, prev_blank = set(), False
        for i, ln in enumerate(lines):
            if not ln:
                prev_blank = True
                continue
            if (prev_blank and len(ln) <= 12
                    and not re.search(r"[。，、！？：…「」（）]$", ln)):
                sec.add(i)
            prev_blank = False
        budget = max(6, (2600 - 220 - y) // lh)
        if len(lines) > budget:
            lines = lines[:budget - 1] + ["……（全文较长，完整内容见原页）"]
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
        y += 8
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
    """/wiki 图片卡：搜索→抽取→渲染。失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        data = wiki_lookup(keyword)
        out_path = os.path.join(out_dir, "wiki_%s.png" % _slug(data["title"]))
        meta_path = out_path + ".meta.json"
        # rev = 词条标题 + 页面修订号：wiki 一编辑，缓存自动失效重渲染
        return _render_cached(out_path, meta_path,
                              "wiki:%s:%s" % (data["title"], data["rev"]),
                              _draw_wiki(keyword, data))
    except Exception:
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    import sys as _s
    kw = _s.argv[1] if len(_s.argv) > 1 else "战略配备"
    print("rendered:", render_wiki_card(kw, os.path.join(CACHE_DIR, "wiki_test")))
