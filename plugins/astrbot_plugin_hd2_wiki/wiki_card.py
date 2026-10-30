"""Paginated Wiki cards: repeat column headers and cache every visible input."""
import hashlib
import json
import os
import time
from PIL import Image, ImageDraw, ImageOps
from astrbot_plugin_hd2_war_report.report_card import (
    W, PAD, BG, TEXT, GOLD, MUTE, LINE, F, _hud_head, _help_wrap, _render_cached, CACHE_DIR,
)
from .wiki_data import wiki_lookup, _fetch_thumb


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
    digest = hashlib.sha256(json.dumps([keyword,visible,"wiki-layout-5"],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    directory = out_dir or CACHE_DIR
    os.makedirs(directory,exist_ok=True)
    measure = ImageDraw.Draw(Image.new("RGB", (W,1)))
    pages, items, y = [], [], 110

    def new_page():
        nonlocal items,y
        if items:
            pages.append((items,y))
        items=[]; y=110

    def paragraph(text, size=18, color=TEXT):
        nonlocal y
        for line in _help_wrap(str(text), F(size), W-PAD*2, measure):
            if y+size+8 > 1850: new_page()
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
        new_page()
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
        def table_head():
            nonlocal y
            paragraph(heading,19,GOLD)
            for header in headers:
                lines,h=layout(header)
                if y+h>1750: raise ValueError("表头过高，请查看原文")
                items.append(("row",y,lines,cw,True));y+=h
        table_head()
        for row in rows[len(headers):]:
            lines,h=layout(row)
            if y+h>1800:
                new_page();table_head()
            if y+h>1800: raise ValueError("表格行过高，请查看原文")
            items.append(("row",y,lines,cw,False));y+=h
    paragraph(f"摘要：省略 {max(0,len(tables)-8)} 张表、{omitted_rows} 行；长单元格最多显示60字。",14,MUTE)
    paragraph("原文："+value["url"],13,MUTE)
    paragraph("页面版本 #"+str(value.get("rev","0")),14,MUTE)
    new_page()
    paths=[]
    for index,(content,height) in enumerate(pages):
        def draw(dr,img,content=content,height=height,index=index):
            _hud_head(dr,img,"Wiki 档案",f"WIKI · {index+1}/{len(pages)}")
            for item in content:
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
            return height+8
        out=os.path.join(directory,f"wiki_{digest[:16]}_{index+1}.png")
        path=_render_cached(out,out+".meta",digest,draw,page_info=(index+1,len(pages),"页面版本 #"+str(value.get("rev","0"))))
        paths.extend(path if isinstance(path,list) else [path])
    return paths[0] if len(paths)==1 else paths
