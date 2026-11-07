# -*- coding: utf-8 -*-
"""report_card.py 追加补丁：/api API 变动监测卡（主仓 Issue #60）"""
p = "report_card.py"
b = open(p, encoding="utf-8").read()

new_code = '''
# ═══════════════════════════════════════════════════════════════
# /api API 变动监测卡（来源：主仓 Issue #60 · CI 自动刷新）
# ═══════════════════════════════════════════════════════════════

ISSUE_URL = "https://api.github.com/repos/Jerry114514/Jerry114514.github.io/issues/60"
_github_cache = {"etag": None, "body": None, "ts": 0}


def _fetch_issue60():
    """抓主仓 Issue #60（ETag 条件请求，304 不计 API 限额）+ 300s 进程缓存。"""
    if time.time() - _github_cache["ts"] < 300 and _github_cache["body"]:
        return _github_cache["body"]
    import urllib.error
    headers = {"User-Agent": "HD2Bot/1.0 (api-watch)", "Accept": "application/vnd.github+json"}
    if _github_cache["etag"]:
        headers["If-None-Match"] = _github_cache["etag"]
    req = urllib.request.Request(ISSUE_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            data = json.load(r)
            _github_cache["etag"] = r.headers.get("ETag")
            _github_cache["body"] = data.get("body") or ""
            _github_cache["ts"] = time.time()
    except urllib.error.HTTPError as e:
        if e.code == 304 and _github_cache["body"] is not None:
            _github_cache["ts"] = time.time()
            return _github_cache["body"]
        raise
    return _github_cache["body"]


def _parse_issue60(body):
    """解析 Issue #60 正文 → {detected, effect_count, no_change, groups}。
    只解析分隔线之前的最新状态（--- 之后是手动重放的版式示例）。"""
    import re as _re
    part = (body or "").split("\\n---")[0]
    m = _re.search(r"检测时间（UTC）：(\\S+)", part)
    detected = m.group(1) if m else ""
    m = _re.search(r"银河效果定义：(\\d+) 条", part)
    effect_count = int(m.group(1)) if m else None
    no_change = "本次未检测到" in part
    groups = []
    for ch in _re.split(r"## 变更：", part)[1:]:
        name = ch.split("\\n")[0].strip()
        head, sep, tail = ch.partition("### 移除的星球效果")
        added = _re.findall(r"`(\\d+)` — (.+)", head)
        removed = _re.findall(r"`(\\d+)` — (.+)", tail) if sep else []
        groups.append({"name": name, "added": added, "removed": removed})
    return {"detected": detected, "effect_count": effect_count,
            "no_change": no_change, "groups": groups}


def render_api_card(out_dir=None):
    """/api 图片卡：主仓 Issue #60 的 API 变动监测。失败返回 None（调用方回退文本）。"""
    out_dir = out_dir or CACHE_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        body = _fetch_issue60()
        info = _parse_issue60(body)
        out_path = os.path.join(out_dir, "api_card.png")
        meta_path = out_path + ".meta"
        rev = str(info.get("detected") or "") + ":" + str(len(info.get("groups") or []))

        def draw(dr, img):
            changed = not info["no_change"]
            tone = GOLD if changed else GREEN
            y = _hud_head(dr, img, "API 变动监测", "API CHANGE WATCH")

            # 状态横条
            panel_h = 92
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=tone)
            st = ("🔔 检测到 " + str(len(info["groups"])) + " 组变更") if changed \\
                else "✔ 基线一致 · 无新增 / 移除 / 定义变更"
            dr.text((PAD + 18, y + 16), st, font=F(28, True), fill=tone)
            dt = info.get("detected") or "—"
            dr.text((PAD + 18, y + 58), "检测时间（UTC）：" + dt, font=F(15), fill=DIM)
            try:
                du = datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                local = du.astimezone().strftime("%Y-%m-%d %H:%M")
                lw = dr.textlength("仰齐浜时间：" + local, font=F(15))
                dr.text((W - PAD - 18 - lw, y + 58), "仰齐浜时间：" + local, font=F(15), fill=DIM)
            except Exception:
                pass
            y += panel_h + 18

            # 变更分组（有变更才渲染）
            groups = info["groups"]
            if groups:
                total_rows = sum(24 + 26 * (len(g["added"]) + len(g["removed"]) + 1) + 14
                                 for g in groups)
                panel_h = 52 + total_rows + 12
                dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, W - PAD, y + 3], fill=tone)
                section_label(dr, PAD + 18, y + 13, "变更明细", F(23, True))
                yy = y + 54
                for g in groups:
                    dr.text((PAD + 18, yy), "📍 " + g["name"], font=F(21, True), fill=TEXT)
                    yy += 30
                    for kind, mark, col in (("added", "✅ 新增", GREEN), ("removed", "❌ 移除", RED)):
                        for eid, cn in g[kind]:
                            dr.text((PAD + 40, yy), mark, font=F(16, True), fill=col)
                            dr.text((PAD + 128, yy), "#" + eid + " — " + cn,
                                    font=F(18, True), fill=TEXT)
                            yy += 26
                    yy += 14
            else:
                panel_h = 84
                dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                dr.text((PAD + 18, y + 26), "对照表与上一轮一致，无需行动。",
                        font=F(19), fill=DIM)
            y += panel_h + 18

            # 效果定义规模
            dr.text((PAD, y), "当前银河效果定义：" + str(info.get("effect_count") or "—") + " 条",
                    font=F(16), fill=DIM)
            y += 34

            # 页脚
            dr.line([(PAD, y), (W - PAD, y)], fill=LINE)
            dr.text((PAD, y + 14), "来源：主仓 Issue #60（CI 每日自动检测并刷新）",
                    font=F(14), fill=MUTE)
            ft = "明细与历史：github.com/Jerry114514/Jerry114514.github.io/issues/60"
            fw = dr.textlength(ft, font=F(14))
            dr.text((W - PAD - fw, y + 14), ft, font=F(14), fill=MUTE)
            dr.text((PAD, y + 40), "对照口径：官方 API 结构（字段名 + 类型）比对，基线随 CI 更新",
                    font=F(14), fill=(80, 80, 80))
            return y + 88

        return _render_cached(out_path, meta_path, rev, draw)
    except Exception:
        if os.environ.get("HD2_CARD_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


if __name__ == "__main__":'''

anchor = '\nif __name__ == "__main__":'
assert anchor in b, "anchor missing"
assert "render_api_card" not in b, "already patched"
b = b.replace(anchor, new_code, 1)
open(p, "w", encoding="utf-8").write(b)
print("api card added")
