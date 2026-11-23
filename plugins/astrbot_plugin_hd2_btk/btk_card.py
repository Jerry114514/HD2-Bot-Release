"""Templated HUD cards for BTK comparisons and threshold scans."""
import hashlib
import json
import os

from astrbot_plugin_hd2_war_report.report_card import (
    W, PAD, BG, PANEL, LINE, LINE2, TEXT, DIM, MUTE,
    GOLD, GOLD2, BRASS, BLUE, GREEN, RED, F,
    _hud_head, _help_wrap, _render_cached,
)
from astrbot_plugin_hd2_common.paths import plugin_cache_dir

# 成品卡缓存落插件数据目录，不写插件安装目录。
CACHE_DIR = str(plugin_cache_dir("astrbot_plugin_hd2_btk", create=False))
ANGLE_NAMES = ("正面命中", "小角度命中", "大角度命中", "极端角度命中")


def _fmt(value, digits=2):
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _panel(dr, x, y, w, h, accent=GOLD):
    dr.rectangle([x, y, x + w, y + h], fill=PANEL, outline=LINE)
    dr.rectangle([x, y, x + 4, y + h], fill=accent)


def _kpi(dr, x, y, w, h, label, value, sub, accent=GOLD):
    _panel(dr, x, y, w, h, accent)
    dr.text((x + 18, y + 14), label, font=F(15, True), fill=MUTE)
    dr.text((x + 18, y + 42), value, font=F(34, True), fill=TEXT)
    if sub:
        dr.text((x + 18, y + h - 31), sub, font=F(14), fill=DIM)


def _section_title(dr, x, y, text, en=""):
    dr.rectangle([x, y + 2, x + 6, y + 30], fill=GOLD)
    dr.text((x + 16, y), text, font=F(22, True), fill=TEXT)
    if en:
        xx = x + 16 + dr.textlength(text, font=F(22, True)) + 12
        dr.text((xx, y + 7), en, font=F(12), fill=MUTE)


def _scan_detail(row):
    if row.get("kind") == "one":
        ratio = row.get("ratio")
        coverage = 100 / ratio if ratio else 0
        return f"1发覆盖 {coverage:.1f}% · 溢出 {_fmt(row.get('overflow'))}"
    return (f"前 {max(0, int(row.get('btk') or 1) - 1)} 发 "
            f"{float(row.get('ratio') or 0) * 100:.1f}% · 差 {_fmt(row.get('missing'))}")


def _draw_dot(dr, img, view, y):
    result = view["result"]
    options = view["options"]
    weapon = view["weapon"]
    enemy = view["enemy"]
    _panel(dr, PAD, y, W - PAD * 2, 96, BLUE)
    dr.text((PAD + 20, y + 13), "持续伤害目标", font=F(14, True), fill=MUTE)
    dr.text((PAD + 20, y + 42), f"{enemy['enemy']} · 主体", font=F(28, True), fill=TEXT)
    element = result.get("element") or "通用"
    condition = f"{ANGLE_NAMES[int(options['angle'])]} · {element} ×{_fmt(result.get('mult'))}"
    cw = dr.textlength(condition, font=F(15))
    dr.text((W - PAD - 20 - cw, y + 60), condition, font=F(15), fill=DIM)
    y += 112

    gap = 14
    kw = (W - PAD * 2 - gap * 2) / 3
    _kpi(dr, PAD, y, kw, 126, "每跳伤害", _fmt(result.get("perTrigger")),
         f"AP {result.get('ap')} / AV {result.get('av')}", RED)
    _kpi(dr, PAD + kw + gap, y, kw, 126, "触发次数", str(result.get("triggers")),
         "按主体总血量计算", GOLD)
    _kpi(dr, PAD + (kw + gap) * 2, y, kw, 126, "总持续时间",
         f"{_fmt(result.get('seconds'), 1)} s", "触发次数 × 单跳时长", BLUE)
    y += 142

    _panel(dr, PAD, y, W - PAD * 2, 76, GREEN)
    dr.text((PAD + 18, y + 11), "结算路径", font=F(14, True), fill=MUTE)
    dr.text((PAD + 18, y + 38),
            f"主体持续伤害 · 元素倍率 ×{_fmt(result.get('mult'))}", font=F(23, True), fill=TEXT)
    y += 94

    info = [
        ("元素", element),
        ("穿甲系数", f"×{_fmt(result.get('factor'))}"),
        ("作用范围", "仅主体"),
    ]
    gap = 14
    w = (W - PAD * 2 - gap * 2) / 3
    for i, (label, value) in enumerate(info):
        x = PAD + i * (w + gap)
        _panel(dr, x, y, w, 82, LINE2)
        dr.text((x + 16, y + 12), label, font=F(14, True), fill=MUTE)
        dr.text((x + 16, y + 39), value, font=F(23, True), fill=TEXT)
    y += 105
    dr.text((PAD, y), "DoT 不参与普通 BTK 卡阈值；结果按主体承伤计算。",
            font=F(13), fill=MUTE)
    return y + 34


def _draw_compute(dr, img, view):
    result = view["result"]
    options = view["options"]
    weapon = view["weapon"]
    enemy = view["enemy"]
    part = view["part"]
    y = _hud_head(dr, img, "武器伤害测试", "BTK · BALLISTIC TERMINAL")

    if not result.get("ok"):
        dr.text((PAD, y + 18), "无法计算该组合", font=F(34, True), fill=RED)
        y += 68
        message = result.get("message") or result.get("reason")
        if not message and result.get("type") == "dot":
            message = "该元素无法穿透目标主体"
        message = str(message or "未知错误")
        for line in _help_wrap(message, F(21), W - PAD * 2, dr):
            dr.text((PAD, y), line, font=F(21), fill=TEXT)
            y += 32
        return y + 30

    name = result.get("weapon") or weapon.get("name") or weapon.get("id")
    dr.text((PAD, y + 8), name, font=F(36, True), fill=GOLD2)
    tw = dr.textlength(name, font=F(36, True))
    dr.text((PAD + tw + 16, y + 21), weapon.get("id", ""), font=F(17, True), fill=BRASS)
    meta = " · ".join(str(x) for x in (weapon.get("cat"), weapon.get("type")) if x)
    if meta:
        mw = dr.textlength(meta, font=F(14))
        dr.text((W - PAD - mw, y + 22), meta, font=F(14), fill=MUTE)
    y += 62
    if result.get("type") == "dot":
        return _draw_dot(dr, img, view, y)

    target_w = W - PAD * 2
    _panel(dr, PAD, y, target_w, 96, BLUE)
    dr.text((PAD + 20, y + 13), "目标部位", font=F(14, True), fill=MUTE)
    dr.text((PAD + 20, y + 42), f"{enemy['enemy']} · {part['part']}", font=F(28, True), fill=TEXT)
    angle = ANGLE_NAMES[int(options["angle"])]
    condition = f"{_fmt(options['distance'])} m · {angle} · 破片 {options['shrapnelHits']}"
    cw = dr.textlength(condition, font=F(15))
    dr.text((W - PAD - 20 - cw, y + 60), condition, font=F(15), fill=DIM)
    y += 112

    gap = 14
    kw = (W - PAD * 2 - gap * 2) / 3
    beam = result.get("beamSeconds") is not None
    btk_value = f"{_fmt(result.get('beamSeconds'), 1)} s" if beam else f"{result.get('btk')} 发"
    btk_label = "累计照射" if beam else "BTK · 击杀击发"
    ttk_value = "光束" if beam else (f"{_fmt(result.get('ttk'))} s" if result.get("ttk") is not None else "—")
    _kpi(dr, PAD, y, kw, 126, btk_label, btk_value, "致死部位优先", GOLD)
    _kpi(dr, PAD + kw + gap, y, kw, 126, "TTK · 理论耗时", ttk_value, "不含反应与瞄准", BLUE)
    _kpi(dr, PAD + (kw + gap) * 2, y, kw, 126, "单次命中伤害",
         _fmt(result.get("perShotDamage")), f"每发 {result.get('pelletsPerShot') or 1} 弹丸", RED)
    y += 142

    path = result.get("label") or result.get("path") or "—"
    _panel(dr, PAD, y, W - PAD * 2, 66, GREEN)
    dr.text((PAD + 18, y + 10), "击杀路径", font=F(14, True), fill=MUTE)
    dr.text((PAD + 18, y + 33), path, font=F(23, True), fill=TEXT)
    y += 82

    details = []
    if result.get("cripple"):
        details.append(("打残", f"{result['cripple'].get('shots')} 发"))
    if result.get("full"):
        details.append(("致死", f"{result['full'].get('shots')} 发"))
    if float(result.get("bleedSec") or 0) > 0:
        details.append(("流血致死", f"{_fmt(result['bleedSec'], 1)} 秒"))
    if not details:
        details.append(("伤害状态", "单一路径"))
    detail_w = (W - PAD * 2 - gap * (len(details) - 1)) / len(details)
    for i, (label, value) in enumerate(details):
        x = PAD + i * (detail_w + gap)
        _panel(dr, x, y, detail_w, 82, LINE2)
        dr.text((x + 16, y + 12), label, font=F(14, True), fill=MUTE)
        dr.text((x + 16, y + 38), value, font=F(25, True), fill=TEXT)
    y += 98

    stuck = result.get("stuck")
    if stuck:
        if stuck.get("kind") == "one":
            ratio = stuck.get("ratio")
            text = f"1 发覆盖 {100 / ratio:.1f}% · 溢出 {_fmt(stuck.get('overflow'))}"
        else:
            text = (f"前 {max(0, int(result.get('btk') or 1) - 1)} 发完成 "
                    f"{float(stuck.get('ratio') or 0) * 100:.1f}% · 差 {_fmt(stuck.get('missing'))}")
        _panel(dr, PAD, y, W - PAD * 2, 82, GOLD)
        dr.text((PAD + 18, y + 12), "卡阈值命中", font=F(15, True), fill=GOLD)
        dr.text((PAD + 18, y + 40), text, font=F(24, True), fill=GOLD2)
    else:
        _panel(dr, PAD, y, W - PAD * 2, 82, LINE2)
        dr.text((PAD + 18, y + 12), "阈值判断", font=F(15, True), fill=MUTE)
        dr.text((PAD + 18, y + 40), "未卡阈值", font=F(24, True), fill=TEXT)
    y += 98

    if options.get("blastParts"):
        blast = "波及：" + "、".join(options["blastParts"])
        dr.text((PAD, y), blast, font=F(14), fill=DIM)
        y += 26
    dr.text((PAD, y), "仅输出结果；伤害口径与 hd2-btk-sdk v0.14.1 一致。",
            font=F(13), fill=MUTE)
    y += 34
    return y


def _clip(dr, text, font, max_w):
    text = str(text or "")
    if dr.textlength(text, font=font) <= max_w:
        return text
    while text and dr.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _stuck_text(result):
    stuck = result.get("stuck")
    if not stuck:
        return "未卡阈值"
    if stuck.get("kind") == "one":
        ratio = stuck.get("ratio")
        return f"1 发覆盖 {100 / ratio:.1f}%" if ratio else "1 发覆盖 —"
    return f"差 {_fmt(stuck.get('missing'))} 伤害"


def _draw_compare_state(dr, entry, index, accent, y):
    enemy = entry["enemy"]
    name = str(enemy.get("enemy") or "")
    label = "护甲完好" if name.endswith("·完好") else "护甲全破"
    _panel(dr, PAD, y, W - PAD * 2, 64, accent)
    dr.text((PAD + 18, y + 9), f"状态 {index}", font=F(13, True), fill=MUTE)
    dr.text((PAD + 18, y + 28), label, font=F(24, True), fill=TEXT)
    name_w = dr.textlength(name, font=F(15))
    dr.text((W - PAD - 18 - name_w, y + 34), name, font=F(15), fill=DIM)
    y += 78

    result = entry.get("result") or {}
    if entry.get("error"):
        _panel(dr, PAD, y, W - PAD * 2, 74, LINE2)
        dr.text((PAD + 18, y + 12), "该状态没有这个部位", font=F(20), fill=DIM)
        available = "、".join(str(part.get("part")) for part in (enemy.get("parts") or [])[:5])
        dr.text((PAD + 18, y + 44), _clip(dr, "可用：" + available, F(14), W - PAD * 2 - 36),
                font=F(14), fill=MUTE)
        return y + 92
    if not result.get("ok"):
        message = str(result.get("message") or result.get("reason") or "无法计算")
        lines = _help_wrap(message, F(19), W - PAD * 2 - 36, dr)[:2]
        panel_h = 40 + 26 * len(lines)
        _panel(dr, PAD, y, W - PAD * 2, panel_h, LINE2)
        yy = y + 12
        for line in lines:
            dr.text((PAD + 18, yy), line, font=F(19), fill=DIM)
            yy += 26
        return y + panel_h + 14
    if result.get("type") == "dot":
        _panel(dr, PAD, y, W - PAD * 2, 74, LINE2)
        dr.text((PAD + 18, y + 12), "持续伤害目标 · 主体", font=F(15, True), fill=MUTE)
        dr.text((PAD + 18, y + 40),
                f"每跳 {_fmt(result.get('perTrigger'))} · {result.get('triggers')} 跳 · "
                f"共 {_fmt(result.get('seconds'), 1)} 秒",
                font=F(22, True), fill=TEXT)
        return y + 92

    gap = 14
    kw = (W - PAD * 2 - gap * 2) / 3
    part = entry.get("part")
    beam = result.get("beamSeconds") is not None
    btk_value = f"{_fmt(result.get('beamSeconds'), 1)} s" if beam else f"{result.get('btk')} 发"
    btk_label = "累计照射" if beam else "BTK · 击杀击发"
    ttk_value = "光束" if beam else (
        f"{_fmt(result.get('ttk'))} s" if result.get("ttk") is not None else "—")
    _kpi(dr, PAD, y, kw, 118, btk_label, btk_value,
         f"{part['part'] if part else '主体'}", accent)
    _kpi(dr, PAD + kw + gap, y, kw, 118, "TTK · 理论耗时", ttk_value,
         "不含反应与瞄准", BLUE)
    _kpi(dr, PAD + (kw + gap) * 2, y, kw, 118, "单次命中伤害",
         _fmt(result.get("perShotDamage")),
         f"每发 {result.get('pelletsPerShot') or 1} 弹丸", RED)
    y += 134

    half = (W - PAD * 2 - gap) / 2
    path = result.get("label") or result.get("path") or "—"
    _panel(dr, PAD, y, half, 66, LINE2)
    dr.text((PAD + 16, y + 10), "击杀路径", font=F(14, True), fill=MUTE)
    dr.text((PAD + 16, y + 33), _clip(dr, path, F(19), half - 32), font=F(19, True), fill=TEXT)
    stuck = bool(result.get("stuck"))
    _panel(dr, PAD + half + gap, y, half, 66, GOLD if stuck else LINE2)
    dr.text((PAD + half + gap + 16, y + 10), "卡阈值", font=F(14, True),
            fill=GOLD if stuck else MUTE)
    dr.text((PAD + half + gap + 16, y + 33), _clip(dr, _stuck_text(result), F(19), half - 32),
            font=F(19, True), fill=GOLD2 if stuck else TEXT)
    return y + 82


def _draw_compare(dr, img, view):
    weapon = view["weapon"]
    options = view["options"]
    entries = view["entries"]
    y = _hud_head(dr, img, "双状态对比", "BTK · DUAL STATE")

    name = weapon.get("name") or weapon.get("id")
    dr.text((PAD, y + 8), name, font=F(36, True), fill=GOLD2)
    tw = dr.textlength(name, font=F(36, True))
    dr.text((PAD + tw + 16, y + 21), weapon.get("id", ""), font=F(17, True), fill=BRASS)
    meta = " · ".join(str(x) for x in (weapon.get("cat"), weapon.get("type")) if x)
    if meta:
        mw = dr.textlength(meta, font=F(14))
        dr.text((W - PAD - mw, y + 22), meta, font=F(14), fill=MUTE)
    y += 62

    base = str(entries[0]["enemy"].get("enemy") or "").split("·", 1)[0]
    part = entries[0].get("part")
    target = f"{base} · {part['part'] if part else '—'}"
    _panel(dr, PAD, y, W - PAD * 2, 96, BLUE)
    dr.text((PAD + 20, y + 13), "对比目标（同一部位，两种护甲状态）", font=F(14, True), fill=MUTE)
    dr.text((PAD + 20, y + 42), _clip(dr, target, F(28, True), W - PAD * 2 - 40),
            font=F(28, True), fill=TEXT)
    condition = (f"{_fmt(options['distance'])} m · "
                 f"{ANGLE_NAMES[int(options['angle'])]} · 破片 {options['shrapnelHits']}")
    cw = dr.textlength(condition, font=F(15))
    dr.text((W - PAD - 20 - cw, y + 60), condition, font=F(15), fill=DIM)
    y += 112

    for index, entry in enumerate(entries):
        accent = GREEN if index == 0 else RED
        y = _draw_compare_state(dr, entry, index + 1, accent, y)
    dr.text((PAD, y), "需要单独结果时，在敌人名后加 ·完好 或 ·护甲已破坏（也可写 ·破损）。",
            font=F(13), fill=MUTE)
    return y + 30


def _draw_scan(dr, img, view):
    weapon = view["weapon"]
    rows = view["rows"]
    fatal_only = view["fatalOnly"]
    y = _hud_head(dr, img, "卡阈值扫描", "BTK · THRESHOLD MATRIX")
    dr.text((PAD, y + 8), f"{weapon['id']} · {weapon['name']}", font=F(32, True), fill=GOLD2)
    context = ("致死部位" if fatal_only else "全部部位") + f" · 命中 {len(rows)} 个组合"
    cw = dr.textlength(context, font=F(16))
    dr.text((W - PAD - cw, y + 20), context, font=F(16), fill=DIM)
    y += 62

    if not rows:
        _panel(dr, PAD, y, W - PAD * 2, 96, LINE2)
        dr.text((PAD + 18, y + 18), "未发现卡阈值组合", font=F(26, True), fill=TEXT)
        dr.text((PAD + 18, y + 56), "BTK≤3 且前序命中达到 90% 的组合为空。",
                font=F(15), fill=MUTE)
        return y + 120

    shown = rows[:10]
    table_h = 54 + len(shown) * 66
    dr.rectangle([PAD, y, W - PAD, y + table_h], fill=PANEL, outline=LINE)
    dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
    dr.text((PAD + 18, y + 14), "目标 · 部位", font=F(14, True), fill=MUTE)
    dr.text((PAD + 610, y + 14), "BTK", font=F(14, True), fill=MUTE)
    dr.text((PAD + 700, y + 14), "阈值余量", font=F(14, True), fill=MUTE)
    yy = y + 50
    for i, row in enumerate(shown, 1):
        if i % 2 == 0:
            dr.rectangle([PAD + 1, yy, W - PAD - 1, yy + 66], fill=(22, 32, 46))
        dr.text((PAD + 18, yy + 18), f"{i:02d}", font=F(16, True), fill=BRASS)
        target = f"{row.get('enemy')} · {row.get('part')}"
        if len(target) > 30:
            target = target[:29] + "…"
        dr.text((PAD + 60, yy + 15), target, font=F(19, True), fill=TEXT)
        dr.text((PAD + 610, yy + 14), str(row.get("btk")), font=F(22, True), fill=GOLD2)
        dr.text((PAD + 700, yy + 17), _scan_detail(row), font=F(17), fill=DIM)
        dr.line([(PAD + 12, yy + 66), (W - PAD - 12, yy + 66)], fill=LINE)
        yy += 66
    y += table_h + 18
    if len(rows) > len(shown):
        dr.text((PAD, y), f"另有 {len(rows) - len(shown)} 个组合未显示。",
                font=F(14), fill=MUTE)
        y += 26
    dr.text((PAD, y), "口径：距离 0m、正面命中；卡阈值定义为前序命中达到 90%。",
            font=F(13), fill=MUTE)
    y += 34
    return y


def render_btk_card(view, out_dir=None):
    if view.get("kind") not in ("compute", "compare", "scan"):
        return None
    out_dir = out_dir or CACHE_DIR
    os.makedirs(out_dir, exist_ok=True)
    digest = hashlib.sha256(
        (json.dumps(view, ensure_ascii=False, sort_keys=True) + "btk-layout-v2").encode("utf-8")
    ).hexdigest()
    out = os.path.join(out_dir, f"btk_{view['kind']}.png")

    def draw(dr, img):
        kind = view["kind"]
        if kind == "compare":
            return _draw_compare(dr, img, view)
        return _draw_compute(dr, img, view) if kind == "compute" else _draw_scan(dr, img, view)

    path = _render_cached(out, out + ".meta.json", digest, draw)
    return path[0] if isinstance(path, list) and len(path) == 1 else path
