# -*- coding: utf-8 -*-
"""Convert the pinned loadout catalog's wiki.gg SVGs to validated local PNGs."""
import base64
import json
import os
import sys
import urllib.request
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")
CATALOG = os.path.join(HERE, "runtime", "data", "catalog.json")

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


def valid_png(path):
    try:
        with Image.open(path) as image:
            if image.format != "PNG" or image.width <= 0 or image.height <= 0:
                return False
            image.verify()
        return os.path.getsize(path) > 300
    except (OSError, ValueError):
        return False


def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    with open(CATALOG, encoding="utf-8") as stream:
        catalog = json.load(stream)
    items = catalog.get("equipment") or []
    print("equipment:", len(items))
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
            if valid_png(out):
                done += 1
                continue
            try:
                try:
                    svg = fetch_bytes([url, url.replace("https://", "http://")])
                except Exception:
                    # Broken .png files from the old converter may already contain
                    # the source SVG; use them so an offline repair still works.
                    if not os.path.exists(out):
                        raise
                    svg = open(out, "rb").read()
                    if b"<svg" not in svg[:1024]:
                        raise
                b64 = base64.b64encode(svg).decode()
                html = ('<body style="margin:0;background:transparent">'
                        '<img src="data:image/svg+xml;base64,' + b64 + '" '
                        'style="width:128px;height:128px;object-fit:contain;display:block"></body>')
                pg.set_content(html)
                pg.wait_for_timeout(120)
                tmp = out + ".tmp.png"
                try:
                    pg.screenshot(path=tmp, omit_background=True)
                    if not valid_png(tmp):
                        raise ValueError("生成的图标不是有效 PNG")
                    os.replace(tmp, out)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                done += 1
            except Exception as e:
                fail += 1
                print("FAIL", iid, str(e)[:60])
        b.close()
    print(f"done={done} fail={fail} dir={ICON_DIR}")

if __name__ == "__main__":
    main()
