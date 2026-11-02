"""Single long Wiki card: keep all visible tables in one cached image."""
import hashlib
import json
import os
import tempfile
import time
from PIL import Image, ImageDraw, ImageOps
from astrbot_plugin_hd2_war_report.report_card import (
    W, PAD, BG, TEXT, GOLD, MUTE, LINE, F, _hud_head, _help_wrap, CACHE_DIR,
)
from .wiki_data import wiki_lookup, _fetch_thumb

WIKI_LONG_MAX = 30000


def _render_long_cached(out_path, meta_path, digest, draw, height):
    """Render and cache one dynamically-sized Wiki image."""
    try:
        meta = json.loads(open(meta_path, encoding="utf-8").read())
        if meta.get("digest") == digest and os.path.isfile(out_path):
            with Image.open(out_path) as cached:
                cached.verify()
            return out_path
    except (OSError, ValueError, KeyError):
        pass
    if height + 40 > WIKI_LONG_MAX:
        raise ValueError("Wiki 表格长图超过 30000 像素，请使用文字查询或原文链接")
    img = Image.new("RGB", (W, max(2600, height + 40)), BG)
    bottom = draw(ImageDraw.Draw(img), img)
    if bottom > WIKI_LONG_MAX:
        raise ValueError("Wiki 表格长图超过 30000 像素，请使用文字查询或原文链接")
    img = img.crop((0, 0, W, min(WIKI_LONG_MAX, bottom + 12)))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".wiki_", suffix=".png", dir=os.path.dirname(out_path))
    os.close(fd)
    try:
        img.save(tmp, format="PNG")
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    with open(meta_path, "w", encoding="utf-8") as stream:
        json.dump({"digest": digest, "height": img.height}, stream)
    return out_path


def render_wiki_card(keyword, out_dir=None, data=None):
    value = dict(data) if data is not None else wiki_lookup(keyword)
    if value["status"] not in ("resolved", "ok"):
        return None
    deadline = value.pop("deadline", time.monotonic()+8)
    if time.monotonic() >= deadline:
        return None
    thumb = _fetch_thumb(value.get("thumb"), deadline=deadline) if value.get("thumb") else None
    thumb_image = None
    if thumb:
        try:
            with Image.open(thumb) as src:
                thumb_image = ImageOps.contain(src.convert("RGB"), (200,200))
        except (OSError, ValueError):
            pass
    visible = {k:v for k,v in value.items() if k not in ("diagnostics", "deadline")}
    visible["thumb_available"] = thumb_image is not None
    digest = hashlib.sha256(json.dumps([keyword,visible,"wiki-layout-6-long"],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    directory = out_dir or CACHE_DIR
    os.makedirs(directory,exist_ok=True)
    measure = ImageDraw.Draw(Image.new("RGB", (W,1)))
    items, y = [], 110

    def paragraph(text, size=18, color=TEXT):
        nonlocal y
        for line in _help_wrap(str(text), F(size), W-PAD*2, measure):
            items.append(("text",y,line,size,color));y+=size+8

    paragraph(value["title"],28,GOLD)
    if keyword != value["title"]: paragraph("查询："+keyword,14,MUTE)
    if thumb_image is not None:
        items.append(("thumb",y,thumb_image)); y+=thumb_image.height+10
    paragraph(value.get("text") or "暂无正文摘要")
    if value.get("table_status") in ("unavailable","unsupported"):
        paragraph("表格未完整取得或含不支持布局，请核对原文。",16,GOLD)
    tables=value.get("tables",[])
    omitted_rows=sum(max(0,len(t["rows"])-26) for t in tables[:8])
    clipped_cells=0
    for tb in tables[:8]:
        heading=tb.get("section","")+" · "+tb.get("title","")
        rows=tb["rows"][:26]
        if not rows: continue
        columns=max(map(len,rows)); cw=(W-2*PAD)/columns
        def layout(row):
            nonlocal clipped_cells
            lines=[]
            for cell in row:
                if len(cell)>60: clipped_cells+=1
                lines.append(_help_wrap(cell[:60]+("…" if len(cell)>60 else ""),F(15),max(12,cw-12),measure) or [""])
            return lines, max(map(len,lines))*22+12
        headers=[]
        for i,row in enumerate(rows):
            flags=(tb.get("headers") or [])[i] if i<len(tb.get("headers",[])) else []
            if flags and all(flags): headers.append(row)
            else: break
        paragraph(heading,19,GOLD)
        for header in headers:
            lines,h=layout(header)
            items.append(("row",y,lines,cw,True));y+=h
        for row in rows[len(headers):]:
            lines,h=layout(row)
            items.append(("row",y,lines,cw,False));y+=h
    paragraph(f"摘要：省略 {max(0,len(tables)-8)} 张表、{omitted_rows} 行；长单元格最多显示60字。",14,MUTE)
    paragraph("原文："+value["url"],13,MUTE)
    paragraph("页面版本 #"+str(value.get("rev","0")),14,MUTE)

    def draw(dr,img):
        _hud_head(dr,img,"Wiki 档案","HELLDIVERS WIKI · ZH · 单页长图")
        for item in items:
            kind,yy,*args=item
            if kind=="text":
                text,size,color=args; dr.text((PAD,yy),text,font=F(size),fill=color)
            elif kind=="thumb": img.paste(args[0],(PAD,yy))
            else:
                lines,cw,is_header=args
                dr.line([(PAD,yy),(W-PAD,yy)],fill=LINE)
                for col,cell in enumerate(lines):
                    for ln,line in enumerate(cell):
                        dr.text((PAD+col*cw+5,yy+6+ln*22),line,font=F(15,is_header),fill=GOLD if is_header else TEXT)
        dr.line([(PAD,y+8),(W-PAD,y+8)],fill=LINE)
        dr.text((PAD,y+18),"Wiki 数据来自 helldivers.wiki.gg 中文站 · 页面版本 #"+
                str(value.get("rev","0")),font=F(14),fill=MUTE)
        return y+42

    out=os.path.join(directory,f"wiki_{digest[:16]}_long.png")
    return _render_long_cached(out,out+".meta",digest,draw,y)
