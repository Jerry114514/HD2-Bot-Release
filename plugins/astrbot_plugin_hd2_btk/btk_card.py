"""HUD 图片卡：BTK 武器伤害、双状态对比、卡阈值扫描。

统一走 ``astrbot_plugin_hd2_common.ui``：黑曜石琥珀金 Token + 流式布局 +
双栏对比版式；纯文本通道由 ``main.format_view`` 提供，两通道同源。
"""
import hashlib
import json
import os

from astrbot_plugin_hd2_common.paths import plugin_cache_dir
from astrbot_plugin_hd2_common.ui import pages
from astrbot_plugin_hd2_common.ui.layout import Canvas, ellipsize, wrap_runs
from astrbot_plugin_hd2_common.ui.tokens import (
    AMBER, BRASS, CARD, CYAN, DIM, F, GREEN, GREEN_BG, GUTTER, LINE, MUTE, RED, RED_BG,
    TEXT,
)

CACHE_DIR = str(plugin_cache_dir("astrbot_plugin_hd2_btk", create=False))
ANGLE_NAMES = ("正面命中", "小角度命中", "大角度命中", "极端角度命中")
LAYOUT_TAG = "btk-layout-v7"


def _fmt(value, digits=2):
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _state_label(name: str) -> str:
    name = str(name or "")
    return "护甲完好" if name.endswith("·完好") else "护甲全破"


def _condition(options) -> str:
    return (f"{_fmt(options.get('distance'))} m · "
            f"{ANGLE_NAMES[int(options.get('angle') or 0)]} · "
            f"破片 {options.get('shrapnelHits')}")


def _weapon_head(c: Canvas, weapon: dict):
    name = str(weapon.get("name") or weapon.get("id") or "")
    c.heading(name, sub=str(weapon.get("id") or ""), font_size=28, color=TEXT)
    meta = " · ".join(str(x) for x in (weapon.get("cat"), weapon.get("type")) if x)
    if meta:
        mf = F(12)
        c.dr.text((c.width - c.pad - c.text_w(meta, mf), c.y - c.line_h(F(13)) - 2),
                  meta, font=mf, fill=MUTE)


def _target_panel(c: Canvas, title: str, label: str, right: str = "") -> int:
    c.panel(c.pad, c.y, c.width - c.pad * 2, 92)
    c.dr.text((c.pad + 16, c.y + 12), title, font=F(11, True), fill=MUTE)
    c.dr.text((c.pad + 16, c.y + 32), label, font=F(25, True), fill=TEXT)
    if right:
        rf = F(12)
        c.dr.text((c.width - c.pad - 16 - c.text_w(right, rf), c.y + 52), right, font=rf, fill=DIM)
    c.y += 92 + 14
    return 106


def _detail_bar(c: Canvas, x, y, w, label, value, value_color=TEXT, accent=None, h=48):
    c.panel(x, y, w, h, fill=CARD, outline=LINE, accent=accent)
    c.dr.text((x + 14, y + (h - c.line_h(F(11))) // 2 - 1), label, font=F(11), fill=MUTE)
    vf = F(15, True)
    c.dr.text((x + w - 14 - c.text_w(value, vf), y + (h - c.line_h(vf)) // 2 - 1),
              ellipsize(c.dr, value, vf, w * 0.62), font=vf, fill=value_color)


def _stuck_text(result: dict) -> str:
    stuck = result.get("stuck")
    if not stuck:
        return "未卡阈值"
    if stuck.get("kind") == "one":
        ratio = stuck.get("ratio")
        return f"1 发覆盖 {100 / ratio:.1f}%" if ratio else "1 发覆盖 —"
    return f"前序命中差 {_fmt(stuck.get('missing'))}"


def _draw_dot(c: Canvas, view: dict):
    result = view["result"]
    enemy = view["enemy"]
    c.header("持续伤害测试", "BTK · DAMAGE OVER TIME")
    _weapon_head(c, view["weapon"])
    _target_panel(c, "持续伤害目标", f"{enemy['enemy']} · 主体", _condition(view["options"]))
    c.kpi([
        ("每跳伤害", _fmt(result.get("perTrigger")), f"AP {result.get('ap')} / AV {result.get('av')}"),
        ("触发次数", str(result.get("triggers")), "按主体总血量计算"),
        ("总持续时间", f"{_fmt(result.get('seconds'), 1)} s", "触发次数 × 单跳时长"),
    ], cols=3, accents=[RED, AMBER, CYAN])
    c.section("结算路径", "RESOLUTION")
    c.rows([
        ("元素", str(result.get("element") or "通用"), None),
        ("元素倍率", f"×{_fmt(result.get('mult'))}", None),
        ("穿甲系数", f"×{_fmt(result.get('factor'))}", None),
    ])
    return c


def _draw_compute(c: Canvas, view: dict):
    result = view["result"]
    weapon = view["weapon"]
    enemy = view["enemy"]
    part = view["part"]
    c.header("武器伤害测试", "BTK · BALLISTIC TERMINAL")
    _weapon_head(c, weapon)
    if not result.get("ok"):
        c.heading("无法计算该组合", font_size=24, color=RED)
        message = result.get("message") or result.get("reason")
        if not message and result.get("type") == "dot":
            message = "该元素无法穿透目标主体"
        c.paragraph(str(message or "未知错误"), font=F(17), color=TEXT)
        return c
    if result.get("type") == "dot":
        c.heading(str(weapon.get("name") or weapon.get("id")), font_size=26)
        return _draw_dot(c, view)

    _target_panel(c, "目标部位", f"{enemy['enemy']} · {part['part']}", _condition(view["options"]))
    beam = result.get("beamSeconds") is not None
    btk_value = f"{_fmt(result.get('beamSeconds'), 1)} s" if beam else f"{result.get('btk')} 发"
    ttk_value = "光束" if beam else (f"{_fmt(result.get('ttk'))} s" if result.get("ttk") is not None else "—")
    c.kpi([
        ("累计照射" if beam else "击杀击发 BTK", btk_value, "致死部位优先"),
        ("理论耗时 TTK", ttk_value, "不含反应与瞄准"),
        ("单次命中伤害", _fmt(result.get("perShotDamage")), f"每发 {result.get('pelletsPerShot') or 1} 弹丸"),
    ], cols=3, accents=[AMBER, CYAN, RED])
    c.section("杀伤判定", "KILL DETAIL")
    c.rows([
        ("击杀路径", str(result.get("label") or result.get("path") or "—"), None),
        ("卡阈值", _stuck_text(result), None),
    ], row_h=44)
    details = []
    if result.get("cripple"):
        details.append(("打残", f"{result['cripple'].get('shots')} 发"))
    if result.get("full"):
        details.append(("致死", f"{result['full'].get('shots')} 发"))
    if float(result.get("bleedSec") or 0) > 0:
        details.append(("流血致死", f"{_fmt(result['bleedSec'], 1)} 秒"))
    if details:
        c.section("伤害状态", "STATUS")
        c.kpi([(k, v, "") for k, v in details], cols=len(details), h=74,
              accents=[CYAN] * len(details))
    if view["options"].get("blastParts"):
        c.paragraph("波及：" + "、".join(view["options"]["blastParts"]), font=F(12), color=DIM)
    return c


def _conclusion(entries) -> tuple[str, str]:
    ok = [e for e in entries if (e.get("result") or {}).get("ok")]
    missing = [e for e in entries if e.get("error")]
    if missing:
        return "状态 2 缺少该部位，只有护甲完好状态可计算", "amber"
    if len(ok) < 2:
        return "仅一个状态可计算，请检查部位名称", "amber"
    a, b = ok[0]["result"], ok[1]["result"]
    ba, bb = a.get("btk"), b.get("btk")
    if ba is not None and bb is not None:
        if ba == bb:
            return "两种护甲状态击杀击发相同，差异只体现在装甲部位的减伤", "cyan"
        if bb < ba:
            ta, tb = a.get("ttk"), b.get("ttk")
            if ta is not None and tb is not None:
                return (f"破甲后击杀更快：{ba} 发 / {_fmt(ta)} s → {bb} 发 / {_fmt(tb)} s，"
                        f"推荐先破甲再输出主体"), "green"
            return f"破甲后击杀更快：{ba} 发 → {bb} 发，推荐先破甲再输出主体", "green"
        return f"护甲完好状态击杀更少：{ba} 发 → {bb} 发，建议打装甲外露部位", "amber"
    return "双状态对比：护甲完好 vs 护甲全破", "amber"


def _state_column(c: Canvas, x, w, entry, index, accent, height=330):
    y = c.y
    c.panel(x, y, w, height, fill=CARD, outline=LINE, accent=accent)
    name = str((entry.get("enemy") or {}).get("enemy") or "")
    c.pill(x + 14, y + 12, f"状态 {index} · {_state_label(name)}",
           color=accent, bg=(GREEN_BG if accent == GREEN else RED_BG), font=F(11, True))
    nf = F(10)
    c.dr.text((x + w - 14 - c.text_w(name, nf), y + 18), ellipsize(c.dr, name, nf, w * 0.5),
              font=nf, fill=MUTE)
    result = entry.get("result") or {}
    if entry.get("error"):
        c.dr.text((x + 14, y + 52), "该状态没有这个部位", font=F(17, True), fill=DIM)
        available = "、".join(str(p.get("part")) for p in ((entry.get("enemy") or {}).get("parts") or [])[:4])
        c.dr.text((x + 14, y + 78), ellipsize(c.dr, "可用：" + available, F(11), w - 28),
                  font=F(11), fill=MUTE)
        return height + 12
    if not result.get("ok"):
        message = str(result.get("message") or result.get("reason") or "无法计算")
        lines = wrap_runs(c.dr, [(message, TEXT)], F(13), w - 28)[:3]
        yy = y + 52
        for line in lines:
            xx = x + 14
            for text, _style in line:
                c.dr.text((xx, yy), text, font=F(13), fill=DIM)
                xx += c.text_w(text, F(13))
            yy += 20
        return height + 12

    beam = result.get("beamSeconds") is not None
    big = f"{_fmt(result.get('beamSeconds'), 1)} s" if beam else f"{result.get('btk')} 发"
    bf = F(34, True)
    c.dr.text((x + 14, y + 48), big, font=bf, fill=TEXT)
    ttk = "光束" if beam else (f"TTK {_fmt(result.get('ttk'))} s" if result.get("ttk") is not None else "")
    if ttk:
        c.dr.text((x + 14 + c.text_w(big, bf) + 10, y + 62), ttk, font=F(14, True), fill=accent)
    rows = [
        ("单次命中伤害", _fmt(result.get("perShotDamage"))),
        ("击杀路径", str(result.get("label") or result.get("path") or "—")),
        ("卡阈值", _stuck_text(result)),
        ("护甲 / 该状态部位", f"AV {(entry.get('part') or {}).get('av', '—')} · "
                              f"{(entry.get('part') or {}).get('part') or '主体'}"),
    ]
    ry = y + 106
    for label, value in rows:
        _detail_bar(c, x + 14, ry, w - 28, label, value)
        ry += 50
    return height + 12


def _draw_compare(c: Canvas, view: dict):
    entries = view["entries"]
    c.header("双状态对比", "BTK · DUAL STATE")
    _weapon_head(c, view["weapon"])
    base = str((entries[0].get("enemy") or {}).get("enemy") or "").split("·", 1)[0]
    part = entries[0].get("part")
    _target_panel(c, "对比目标（同一部位，两种护甲状态）",
                  f"{base} · {part['part'] if part else '—'}", _condition(view["options"]))
    text, tone = _conclusion(entries)
    c.banner(text, label="结论", tone=tone)
    cw = (c.width - c.pad * 2 - GUTTER) / 2
    start_y = c.y
    _state_column(c, c.pad, cw, entries[0], 1, GREEN)
    _state_column(c, c.pad + cw + GUTTER, cw, entries[1] if len(entries) > 1 else {}, 2, RED)
    c.y = start_y + 330 + 12
    return c


def _draw_scan(c: Canvas, view: dict):
    weapon = view["weapon"]
    rows = view["rows"]
    fatal_only = view["fatalOnly"]
    c.header("卡阈值扫描", "BTK · THRESHOLD MATRIX")
    c.heading(f"{weapon['id']} · {weapon['name']}", font_size=24)
    context = ("致死部位" if fatal_only else "全部部位") + f" · 命中 {len(rows)} 个组合"
    cf = F(12)
    c.dr.text((c.width - c.pad - c.text_w(context, cf), c.y - c.line_h(F(13)) - 2),
              context, font=cf, fill=MUTE)
    if not rows:
        c.banner("未发现卡阈值组合（BTK≤3 且前序命中达到 90% 的组合为空）", label="结果", tone="cyan")
        return c
    shown = rows[:10]
    head_y = c.y + 18
    c.dr.text((c.pad + 10, head_y), "目标 · 部位", font=F(11, True), fill=MUTE)
    for label, right_x in (("BTK", c.width - c.pad - 150), ("阈值余量", c.width - c.pad - 10)):
        hw = c.text_w(label, F(11, True))
        c.dr.text((right_x - hw, head_y), label, font=F(11, True), fill=MUTE)
    c.y = head_y + 26
    items = []
    for i, row in enumerate(shown, 1):
        target = f"{row.get('enemy')} · {row.get('part')}"
        detail = (f"前 {max(0, int(row.get('btk') or 1) - 1)} 发 "
                  f"{float(row.get('ratio') or 0) * 100:.1f}% · 差 {_fmt(row.get('missing'))}")
        items.append((f"{i:02d}  {target}", f"{row.get('btk')} 发", detail))
    c.rows(items, row_h=56, value_font=F(17, True))
    if len(rows) > len(shown):
        c.paragraph(f"另有 {len(rows) - len(shown)} 个组合未显示。", font=F(12), color=MUTE)
    return c


def _caption(view: dict) -> str:
    kind = view.get("kind")
    if kind == "compare":
        return "加 ·完好 / ·护甲已破坏（也可写 ·破损）可只查一种状态；口径 hd2-btk-sdk v0.14.1"
    if kind == "scan":
        return "口径：距离 0m、正面命中；卡阈值 = 前序命中达到 90%"
    if (view.get("result") or {}).get("type") == "dot":
        return "DoT 不参与普通 BTK 卡阈值；结果按主体承伤计算"
    return "口径：hd2-btk-sdk v0.14.1 · 距离/角度/破片见目标栏"


def render_btk_card(view, out_dir=None):
    if view.get("kind") not in ("compute", "compare", "scan"):
        return None
    out_dir = out_dir or CACHE_DIR
    os.makedirs(out_dir, exist_ok=True)
    digest = hashlib.sha256(
        (json.dumps(view, ensure_ascii=False, sort_keys=True) + LAYOUT_TAG).encode("utf-8")
    ).hexdigest()[:16]
    out = os.path.join(out_dir, f"btk_{view['kind']}_{digest}.png")

    def draw(_dr, _img):
        c = Canvas()
        kind = view["kind"]
        if kind == "compare":
            _draw_compare(c, view)
        elif kind == "compute":
            _draw_compute(c, view)
        else:
            _draw_scan(c, view)
        _img.paste(c.img.crop((0, 0, c.width, min(c.y + 6, c.img.height))), (0, 0))
        return c.y + 6

    return pages.render_cached(out, out + ".meta", view, draw, width=960, tall=4200,
                               layout_tag=LAYOUT_TAG, caption=_caption(view))
