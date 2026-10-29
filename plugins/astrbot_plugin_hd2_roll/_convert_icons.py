# -*- coding: utf-8 -*-
"""loadout.json 的 wiki.gg SVG 图标 → 本地 PNG（透明底，128px）· 一次性转换工具"""
import base64
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")
LOADOUT_URLS = [
    "https://jerry114514.github.io/HD2_Wiki/data/wiki/zh/loadout.json",
    "http://127.0.0.1:8770/HD2_Wiki/data/wiki/zh/loadout.json",  # 本地回退
]

def fetch_json(urls):
    last = None
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "HD2Bot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:
            last = e
    raise last

def fetch_bytes(urls):
    last = None
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "HD2Bot/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except Exception as e:
            last = e
    raise last

def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    d = fetch_json(LOADOUT_URLS)
    items = d.get("stratagems") or []
    print("stratagems:", len(items))
    from playwright.sync_api import sync_playwright
    done = fail = 0
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_context(viewport={"width": 128, "height": 128}).new_page()
        for it in items:
            iid, url = it.get("id"), it.get("icon")
            if not iid or not url:
                fail += 1
                continue
            out = os.path.join(ICON_DIR, iid + ".png")
            if os.path.exists(out) and os.path.getsize(out) > 300:
                done += 1
                continue
            try:
                svg = fetch_bytes([url, url.replace("https://", "http://")])
                b64 = base64.b64encode(svg).decode()
                html = ('<body style="margin:0;background:transparent">'
                        '<img src="data:image/svg+xml;base64,' + b64 + '" '
                        'style="width:128px;height:128px;object-fit:contain;display:block"></body>')
                pg.set_content(html)
                pg.wait_for_timeout(120)
                pg.screenshot(path=out, omit_background=True)
                done += 1
            except Exception as e:
                fail += 1
                print("FAIL", iid, str(e)[:60])
        b.close()
    print(f"done={done} fail={fail} dir={ICON_DIR}")

if __name__ == "__main__":
    main()
