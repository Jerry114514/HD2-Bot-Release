# -*- coding: utf-8 -*-
"""Wiki 卡：全量内容渲染，超长时按分区合并为单张宽图（自左向右连续阅读）。

2026-11 改版：旧实现只画前 8 张表、每表 26 行、单元格 60 字、画布 30000px 封顶，
长页面大量内容被丢弃；现按用户要求**全量渲染**——所有表格、所有行、完整单元格文本，
正文不再 900 字截断；超高内容复用 report_card.save_pages 的多页合并逻辑
（均衡切段、分区标签、统一页脚），不再依赖单图高度上限。
"""
import hashlib
import json
import os
import time
from PIL import Image, ImageDraw, ImageOps
from astrbot_plugin_hd2_war_report.report_card import (
    W, PAD, BG, TEXT, GOLD, MUTE, LINE, F, _hud_head, _help_wrap, save_pages,
)
from astrbot_plugin_hd2_common.paths import plugin_cache_dir
from .wiki_data import wiki_lookup, _fetch_thumb

# 成品卡与缩略图缓存统一落 wiki 插件数据目录，不写插件安装目录。
CACHE_DIR = str(plugin_cache_dir("astrbot_plugin_hd2_wiki", create=False))

WIKI_LONG_MAX = 70000        # 内容画布预检上限（超高内容仍拒绝，避免内存失控）
WIKI_MAX_AREA = 80_000_000   # 合并宽图像素面积上限（约 36 列）
WIKI_PNG_LEVEL = 3           # 大幅面 PNG 用低压缩换渲染速度


def _render_long_cached(out_path, meta_path, digest, draw, height, caption):
    """渲染全量内容并缓存为合并宽图；返回 PNG 路径。"""
    try:
        meta = json.loads(open(meta_path, encoding="utf-8").read())
        if meta.get("digest") == digest and os.path.isfile(out_path):
            with Image.open(out_path) as cached:
                cached.verify()
            return out_path
    except (OSError, ValueError, KeyError):
        pass
    if height + 40 > WIKI_LONG_MAX:
        raise ValueError("Wiki 内容超过渲染上限，请使用文字查询或原文链接")
    img = Image.new("RGB", (W, max(2600, height + 40)), BG)
    bottom = draw(ImageDraw.Draw(img), img)
    img = img.crop((0, 0, W, bottom + 12))
    paths = save_pages(img, bottom + 12, out_path, document=None,
                       caption=caption, max_area=WIKI_MAX_AREA, png_level=WIKI_PNG_LEVEL)
    with open(meta_path, "w", encoding="utf-8") as stream:
        json.dump({"digest": digest, "height": bottom + 12}, stream)
    return paths[0]


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
    digest = hashlib.sha256(json.dumps([keyword,visible,"wiki-layout-7-wide"],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
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
    for tb in tables:
        heading=tb.get("section","")+" · "+tb.get("title","")
        rows=tb["rows"]
        if not rows: continue
        columns=max(map(len,rows)); cw=(W-2*PAD)/columns
        def layout(row):
            lines=[]
            for cell in row:
                lines.append(_help_wrap(cell,F(15),max(12,cw-12),measure) or [""])
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
    total_rows=sum(len(t["rows"]) for t in tables)
    paragraph(f"全文渲染：{len(tables)} 张表 · {total_rows} 行 · 单元格完整显示",14,MUTE)
    paragraph("原文："+value["url"],13,MUTE)
    paragraph("页面版本 #"+str(value.get("rev","0")),14,MUTE)

    def draw(dr,img):
        _hud_head(dr,img,"Wiki 档案","HELLDIVERS WIKI · ZH · 全文长图")
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
    caption=f"Wiki · helldivers.wiki.gg 中文站 · 页面版本 #{value.get('rev','0')}"
    return _render_long_cached(out,out+".meta",digest,draw,y,caption)
