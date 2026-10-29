# -*- coding: utf-8 -*-
"""catalog.json 全部装备图标 → 本地 PNG（PNG/JPG 直接下载，SVG 经 Playwright 转 128px）"""
import base64
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")
CATALOG_URLS = [
    "https://jerry114514.github.io/shared/hd2-loadout/data/catalog.json",
    "http://127.0.0.1:8770/shared/hd2-loadout/data/catalog.json",
]

def fetch(u):
    req = urllib.request.Request(u, headers={"User-Agent": "HD2Bot/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()

def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    cat = None
    for u in CATALOG_URLS:
        try:
            cat = json.loads(fetch(u))
            break
        except Exception as e:
            print("catalog fail:", u, str(e)[:60])
    if not cat:
        print("NO CATALOG")
        sys.exit(1)
    eq = cat.get("equipment") or []
    svgs = []
    direct = skip = 0
    for e in eq:
        iid, url = e.get("id"), e.get("icon")
        if not iid or not url:
            continue
        out = os.path.join(ICON_DIR, iid + ".png")
        if os.path.exists(out) and os.path.getsize(out) > 300:
            skip += 1
            continue
        if url.lower().endswith(".svg"):
            svgs.append((iid, url))
            continue
        try:
            data = fetch(url)
            if len(data) > 200:
                open(out, "wb").write(data)
                direct += 1
            else:
                svgs.append((iid, url))
        except Exception as ex:
            print("FAIL", iid, str(ex)[:60])
            svgs.append((iid, url))
    print(f"direct={direct} skip={skip} svg待转={len(svgs)}")
    if not svgs:
        print("ALL DONE")
        return
    from playwright.sync_api import sync_playwright
    ok = err = 0
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_context(viewport={"width": 128, "height": 128}).new_page()
        for iid, url in svgs:
            out = os.path.join(ICON_DIR, iid + ".png")
            try:
                svg = fetch(url)
                b64 = base64.b64encode(svg).decode()
                html = ('<body style="margin:0;background:transparent">'
                        '<img src="data:image/svg+xml;base64,' + b64 + '" '
                        'style="width:128px;height:128px;object-fit:contain;display:block"></body>')
                pg.set_content(html)
                pg.wait_for_timeout(100)
                pg.screenshot(path=out, omit_background=True)
                ok += 1
            except Exception as ex:
                err += 1
                print("SVGFAIL", iid, str(ex)[:60])
        b.close()
    print(f"svg ok={ok} err={err}")

if __name__ == "__main__":
    main()
