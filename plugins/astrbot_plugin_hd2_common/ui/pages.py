"""出图与分页合图（从 report_card 下沉，供所有插件共用）。

- :func:`save_pages`：超长画布按安静行均衡切段，自左向右合并为单张宽图。
- :func:`render_cached`：按 digest 复用成品，未命中则用 ``draw_fn`` 重绘。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
from datetime import datetime

from PIL import Image, ImageDraw

from astrbot_plugin_hd2_common.ui.tokens import BG, DIM, F, GOLD, LINE, LINE2, MUTE, PAD, TEXT, W

_render_locks: dict[str, threading.Lock] = {}
_render_lock_guard = threading.Lock()


def _doc_meta(document) -> dict:
    return (document or {}).get("_hd2") or {}


def save_pages(img, height, out_path, document=None, page_info=None, caption=None,
               max_area=None, png_level=6, column_height=2100, static=False):
    """超长画布合并为单张宽图：按安静行均衡切段，从左向右排列成连续分区。"""
    width = img.width
    n_pages = max(1, math.ceil(height / max(1, column_height)))
    ideal = math.ceil(height / n_pages)
    spans, start = [], 0
    while start < height:
        end = min(height, start + ideal)
        if end < height:
            for row in range(end, max(start + ideal // 2, end - 100), -1):
                band = img.crop((PAD + 20, row, width - PAD - 20, row + 2)).convert("L")
                if band.point(lambda p: 255 if p > 100 else 0).getbbox() is None:
                    end = row
                    break
        spans.append((start, end))
        start = end
    min_span = max(120, ideal // 8)
    merged = [spans[0]]
    for s, e in spans[1:]:
        if e - s < min_span:
            ps, _pe = merged[-1]
            merged[-1] = (ps, e)
        else:
            merged.append((s, e))
    spans = merged
    doc = document or {}
    quality = " · 缓存/数据陈旧" if _doc_meta(doc).get("stale") else ""
    ts = str(doc.get("fetchedAt") or "")[:16].replace("T", " ")
    n = len(spans)

    GUTTER, LABEL_H, FOOT_H = 24, (56 if n > 1 else 0), 44
    col_h = max(e - s for s, e in spans)
    total_w = n * width + (n - 1) * GUTTER
    total_h = LABEL_H + col_h + FOOT_H
    if total_w * total_h > (max_area or 34_000_000):
        raise ValueError("内容过长，请使用文字查询或原文链接")
    canvas = Image.new("RGB", (total_w, total_h), BG)
    dr = ImageDraw.Draw(canvas)
    for i, (s, e) in enumerate(spans):
        x = i * (width + GUTTER)
        if n > 1:
            if i:
                gx = x - GUTTER // 2
                dr.rectangle([gx - 1, 0, gx + 1, total_h - FOOT_H], fill=LINE)
            prefix = f"{page_info[2]} " if (page_info and len(page_info) >= 3) else ""
            dr.text((x + PAD, 14), f"{prefix}分区 {i + 1}/{n}", font=F(18, True), fill=GOLD)
            dr.line([(x, LABEL_H - 10), (x + width, LABEL_H - 10)], fill=LINE2)
        canvas.paste(img.crop((0, s, width, e)), (x, LABEL_H))
    if caption is None:
        if static:
            cap = "界面生成 " + datetime.now().strftime("%Y-%m-%d %H:%M")
        else:
            cap = f"数据快照 {ts or '见正文'}" + quality
    else:
        cap = caption + quality
    if n > 1:
        cap += " · 各分区自左向右连续阅读"
    dr.line([(0, total_h - FOOT_H), (total_w, total_h - FOOT_H)], fill=LINE)
    dr.text((PAD, total_h - FOOT_H + 12), cap, font=F(13), fill=DIM)
    brand = "真理部 · MOT"
    dr.text((total_w - PAD - dr.textlength(brand, font=F(12)), total_h - FOOT_H + 14),
            brand, font=F(12), fill=MUTE)
    path = os.path.splitext(out_path)[0] + ".png"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".card_", dir=os.path.dirname(path) or ".")
    os.close(fd)
    try:
        canvas.save(tmp, format="PNG")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return [path]


def render_cached(out_path, meta_path, rev, draw_fn, page_info=None, column_height=2100,
                  width=W, tall=2600, layout_tag="layout-v5", caption=None, static=False):
    """按 digest 复用成品图；返回单路径或路径列表。"""
    stem, _ = os.path.splitext(out_path)
    digest = hashlib.sha256(
        (str(rev) + layout_tag + str(column_height) + (str(page_info) if page_info else "")
         + (str(caption) if caption else "")).encode()
    ).hexdigest()[:16]
    out_path = stem + "_" + digest + ".png"
    meta_path = out_path + ".meta"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with _render_lock_guard:
        lock = _render_locks.setdefault(out_path, threading.Lock())
    with lock:
        try:
            with open(meta_path, encoding="utf-8") as fh:
                paths = json.load(fh)["pages"]
            if paths and all(os.path.isfile(p) for p in paths):
                return paths[0] if len(paths) == 1 else paths
        except (OSError, ValueError, KeyError):
            pass
        img = Image.new("RGB", (width, tall), BG)
        y = draw_fn(ImageDraw.Draw(img), img)
        if y + 20 > tall:
            if y > 60000:
                raise ValueError("内容过长，请使用文字查询或原文链接")
            img = Image.new("RGB", (width, y + 40), BG)
            y = draw_fn(ImageDraw.Draw(img), img)
        paths = save_pages(img, y + 20, out_path, page_info=page_info,
                           column_height=column_height, caption=caption, static=static)
        from astrbot_plugin_hd2_common import data as _data
        _data.atomic_json(meta_path, {"pages": paths})
        return paths[0] if len(paths) == 1 else paths
