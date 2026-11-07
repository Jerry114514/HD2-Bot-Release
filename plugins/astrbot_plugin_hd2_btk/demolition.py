# -*- coding: utf-8 -*-
"""Direct-hit and explosive demolition values for hd2-btk-sdk v0.14.1."""
import json
from pathlib import Path
import re
import unicodedata


SOURCE_DATA = Path(__file__).with_name("demolition_sources.json")

HELP_TEXT = """💥 拆毁值评估

同时比较直击与爆炸：
/拆毁值 EAT-17
/拆毁值 EAT-17 炮舰设施
/拆毁值 直击 EAT-17 巨石
/拆毁值 爆炸 NUX-223 地狱火炸弹 巨石

爆炸距离曲线：
/拆毁值 爆炸 “飞鹰”500KG炸弹（延时爆炸） 距离=20
/拆毁值 爆炸 “飞鹰”500KG炸弹（延时爆炸） 巨石 距离=20

资料查询：
/拆毁值 直击
/拆毁值 爆炸物
/拆毁值 常见目标
/拆毁值 建筑
/拆毁值 建筑 巨石

只填来源时，会同时列出最佳直击与最佳爆炸路线，并对照广播塔、
虫穴内外层、机器人构筑者通风口/外层、干扰装置和轨道加农炮。

直击按武器射弹拆毁力；爆炸按内圈全额、普通 1−√x、巨型 1−x² 衰减，
结果截尾取整。要求爆炸伤害的目标不会被纯直击拆毁。"""

DISTANCE_RE = re.compile(r"(?<!\S)(?:距离|distance|d)\s*[=:]\s*(\d+(?:\.\d+)?)", re.I)
MODE_PREFIX_RE = re.compile(r"^(直击|direct|爆炸|blast|explosion)\s+", re.I)
REASON_CN = {
    "unknown-blast": "未知爆炸物",
    "unknown-structure": "未知建筑",
    "badr": "该目标只能通过爆炸伤害拆毁",
    "demolition-insufficient": "拆毁值不足，无法拆除",
    "demolition-insufficient-has-hp": "拆毁值不足，但可继续消耗本体生命值",
}


def _norm(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _name_aliases(name, key):
    aliases = {str(name or "")}
    if key in ("blasts", "sources"):
        first = re.split(r"\s+", str(name or ""), 1)[0]
        if first and first != name:
            aliases.add(first)
    before_paren = re.split(r"[（(]", str(name or ""), 1)[0].strip()
    if before_paren and before_paren != name:
        aliases.add(before_paren)
    return {alias for alias in aliases if alias}


def _aliases(row, key):
    aliases = set(row.get("aliases") or ())
    aliases.update(_name_aliases(row.get("name"), key))
    return aliases


def _match_prefix(rows, text, key):
    wanted = _norm(text)
    if not wanted:
        return None, ""
    matches = []
    for row in rows:
        for alias in _aliases(row, key):
            normalized = _norm(alias)
            if normalized and wanted.startswith(normalized):
                matches.append((len(normalized), normalized, alias, row))
    if not matches:
        return None, wanted
    best = max(length for length, *_ in matches)
    selected = []
    for length, _normalized, _alias, row in matches:
        if length == best and row not in selected:
            selected.append(row)
    if len(selected) != 1:
        names = "、".join(str(row.get("name")) for row in selected[:6])
        raise ValueError("名称前缀有歧义：" + names)
    return selected[0], wanted[best:].strip()


def _find_exact(rows, query, key):
    wanted = _norm(query)
    matches = [
        row for row in rows
        if any(_norm(alias) == wanted for alias in _aliases(row, key))
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("名称有歧义：" + "、".join(str(row.get("name")) for row in matches[:6]))
    return None


def _catalog(run_bridge):
    value = run_bridge("demolitionCatalog", {})
    structures = list(value.get("structures") or [])
    blasts = list(value.get("blasts") or [])
    if not structures or not blasts:
        raise RuntimeError("拆毁值数据不完整")
    by_name = {str(row.get("name")): row for row in blasts}
    by_structure = {str(row.get("name")): row for row in structures}
    payload = json.loads(SOURCE_DATA.read_text(encoding="utf-8"))
    curated = payload.get("sources") or []
    sources = []
    used_blasts = set()
    for item in curated:
        blast_rows = []
        for name in item.get("blasts") or ():
            row = by_name.get(name)
            if row is not None:
                blast_rows.append(row)
                used_blasts.add(name)
        sources.append({
            "name": item["name"],
            "aliases": item.get("aliases") or [],
            "direct": item.get("direct") or [],
            "blasts": blast_rows,
        })
    for row in blasts:
        if row.get("name") in used_blasts:
            continue
        sources.append({
            "name": row["name"],
            "aliases": list(_name_aliases(row["name"], "blasts")),
            "direct": [],
            "blasts": [row],
        })
    common_targets = []
    for item in payload.get("common_targets") or ():
        row = by_structure.get(str(item.get("name") or ""))
        if row is None:
            raise RuntimeError(f"常见目标不在 SDK 拆毁表中：{item.get('name')}")
        common_targets.append({
            **row,
            "label": item.get("label") or row["name"],
            "group": item.get("group") or "常见目标",
        })
    if not common_targets:
        raise RuntimeError("常见拆毁目标数据不完整")
    return structures, blasts, sources, common_targets


def _direct_verdict(hit, structure):
    need = structure.get("df")
    df = hit.get("df")
    if structure.get("badr"):
        return {"ok": False, "reason": "badr", "need": need}
    if df < need:
        return {
            "ok": False,
            "reason": "demolition-insufficient-has-hp" if structure.get("hp")
            else "demolition-insufficient",
            "need": need,
            "hp": structure.get("hp"),
        }
    return {"ok": True, "maxDistance": 0, "need": need}


def _best_direct_path(source, target):
    hits = list(source.get("direct") or ())
    if not hits:
        return {"available": False}
    candidates = [
        {
            "label": hit.get("label") or "直击",
            "df": hit.get("df"),
            "verdict": _direct_verdict(hit, target),
        }
        for hit in hits
    ]
    best = max(
        candidates,
        key=lambda row: (bool(row["verdict"].get("ok")), row.get("df") or 0),
    )
    return {"available": True, **best}


def _best_explosion_path(source, target, run_bridge):
    blasts = list(source.get("blasts") or ())
    if not blasts:
        return {"available": False}
    candidates = []
    for blast in blasts:
        verdict = run_bridge(
            "demolition",
            {"blast": blast["name"], "structure": target["name"]},
        )
        candidates.append({
            "label": blast["name"],
            "df": blast.get("df"),
            "verdict": verdict,
        })
    best = max(
        candidates,
        key=lambda row: (
            bool(row["verdict"].get("ok")),
            float(row["verdict"].get("maxDistance") or 0),
            row.get("df") or 0,
        ),
    )
    return {"available": True, **best}


def _common_comparisons(source, common_targets, run_bridge):
    comparisons = []
    for target in common_targets:
        comparisons.append({
            "name": target["name"],
            "label": target.get("label") or target["name"],
            "group": target.get("group") or "常见目标",
            "df": target.get("df"),
            "badr": bool(target.get("badr")),
            "hp": target.get("hp"),
            "direct": _best_direct_path(source, target),
            "explosion": _best_explosion_path(source, target, run_bridge),
        })
    return comparisons


def _path_short(path, mode):
    if not path.get("available"):
        return "未核实"
    verdict = path.get("verdict") or {}
    if verdict.get("ok"):
        distance = float(verdict.get("maxDistance") or 0)
        if mode == "direct":
            return "命中"
        return f"≤{distance:.2f} m" if distance > 0 else "内圈"
    if verdict.get("reason") == "badr":
        return "需爆炸"
    if verdict.get("reason") == "demolition-insufficient-has-hp":
        return "不足·可打HP"
    need = verdict.get("need")
    df = path.get("df")
    if need is not None and df is not None:
        return f"不足·差{max(0, need - df)}"
    return "不足"


def _format_common_comparison(view):
    source = view["source"]
    lines = [
        f"💥 BTK 拆毁值 · {source['name']}",
    ]
    direct = " / ".join(
        f"{hit.get('label') or '直击'} DF {hit.get('df')}"
        for hit in source.get("direct") or ()
    )
    explosions = " / ".join(
        f"{blast['name']} DF {blast.get('df')}"
        for blast in source.get("blasts") or ()
    )
    if view.get("mode") != "explosion":
        lines.append("直击：" + (direct or "暂无已核实数据"))
    if view.get("mode") != "direct":
        lines.append("爆炸：" + (explosions or "无"))
    lines.append("常见拆毁目标对照：")
    for row in view["comparisons"]:
        paths = []
        if view.get("mode") != "explosion":
            paths.append("直击 " + _path_short(row["direct"], "direct"))
        if view.get("mode") != "direct":
            paths.append("爆炸 " + _path_short(row["explosion"], "explosion"))
        requirement = f"DF {row['df']}"
        if row.get("badr"):
            requirement += "·需爆炸"
        lines.append(
            f"• {row['label']}（{row['group']}，{requirement}）："
            + "｜".join(paths)
        )
    lines.append("直击无距离衰减；爆炸路线按内圈全额和距离曲线计算，两者分别判定。")
    return "\n".join(lines)


def _blast_detail(row):
    curve = "巨型曲线 1−x²" if row.get("giant") else "普通曲线 1−√x"
    return (
        f"💥 {row['name']}\n"
        f"爆炸拆毁值：DF {row.get('df')}\n"
        f"爆炸半径：{row.get('r1')}–{row.get('r2')} m\n"
        f"爆炸伤害：{row.get('dmg')}\n"
        f"衰减：{curve}；造成爆炸伤害：{'是' if row.get('explosive') else '否'}"
    )


def _source_detail(source):
    lines = [f"💥 {source['name']}"]
    if source.get("direct"):
        lines.append("直击拆毁：")
        lines.extend(f"• {hit['label']}：DF {hit['df']}" for hit in source["direct"])
    else:
        lines.append("直击拆毁：暂无已核实的直击 DF")
    explosions = source.get("blasts") or []
    if explosions:
        lines.append("爆炸拆毁：")
        for blast in explosions:
            lines.append(
                f"• {blast['name']}：DF {blast['df']}，"
                f"{blast.get('r1')}–{blast.get('r2')} m"
            )
    else:
        lines.append("爆炸拆毁：无")
    return "\n".join(lines)


def _structure_detail(row, blasts):
    hp = row.get("hp")
    required = row.get("df")
    candidates = [
        blast for blast in blasts
        if blast.get("df", 0) >= required
        and not (row.get("badr") and not blast.get("explosive"))
    ]
    lines = [
        f"🏗️ {row['name']}",
        f"需求拆毁值：DF {required}",
        f"要求爆炸伤害：{'是' if row.get('badr') else '否'}",
    ]
    if hp:
        lines.append(f"本体：HP {hp[0]} / AV {hp[1]}")
    else:
        lines.append("本体生命值：无独立 HP 数据")
    if candidates:
        lines.append("可满足爆炸拆毁门槛：")
        lines.extend(f"• {blast['name']}（DF {blast['df']}）" for blast in candidates[:10])
        if len(candidates) > 10:
            lines.append(f"另有 {len(candidates) - 10} 项")
    else:
        lines.append("没有爆炸物达到拆毁值门槛。")
    return "\n".join(lines)


def _verdict_text(verdict, structure):
    if verdict.get("ok"):
        distance = float(verdict.get("maxDistance") or 0)
        return "可拆毁（命中即可）" if distance <= 0 else f"可拆毁，最大距离 {distance:.2f} m"
    reason = REASON_CN.get(verdict.get("reason"), verdict.get("reason") or "无法判定")
    if verdict.get("reason") == "demolition-insufficient" and verdict.get("need") is not None:
        return reason + f"（需求 DF {verdict['need']}）"
    if verdict.get("reason") == "demolition-insufficient-has-hp":
        hp = verdict.get("hp")
        suffix = f"（HP {hp[0]} / AV {hp[1]}）" if hp else ""
        return reason + suffix
    return reason


def _format_result(view):
    source, structure = view["source"], view["structure"]
    lines = [
        "💥 BTK 拆毁值评估",
        f"来源：{source['name']}",
        f"目标：{structure['name']}（需求 DF {structure.get('df')}，"
        f"{'需要爆炸拆毁' if structure.get('badr') else '允许直击拆毁'}）",
    ]
    if view.get("distance") is not None:
        lines.append(f"距离：{view['distance']:g} m")
    for result in view["results"]:
        mode = result["mode"]
        value = result.get("distanceDf")
        text = f"• {mode}"
        if value is not None:
            text += f"：距离处 DF {value}"
        else:
            text += f"：DF {result['df']}"
        text += "｜" + _verdict_text(result["verdict"], structure)
        if value is not None:
            need = structure.get("df")
            text += "｜" + ("达到门槛" if value >= need else f"还差 {need - value} DF")
        lines.append(text)
    if not any(result["verdict"].get("ok") for result in view["results"]):
        lines.append("结论：当前列出的直击/爆炸路径均不能拆毁该目标。")
    else:
        lines.append("结论：至少一种已列出路径达到拆毁门槛。")
    lines.append("口径：直击无距离衰减；爆炸内圈全额，普通 1−√x、巨型 1−x²，截尾取整。")
    return "\n".join(lines)


def build_view(args, run_bridge):
    """Parse /拆毁值 arguments into a renderable result."""
    text = " ".join(str(args or "").split())
    if not text or text.casefold() in ("帮助", "help", "用法"):
        return {"kind": "info", "text": HELP_TEXT}

    structures, blasts, sources, common_targets = _catalog(run_bridge)
    distance = None
    match = DISTANCE_RE.search(text)
    if match:
        distance = float(match.group(1))
        if not 0 <= distance <= 1000:
            raise ValueError("距离需在 0–1000 米之间")
        text = (text[:match.start()] + " " + text[match.end():]).strip()

    mode = None
    mode_match = MODE_PREFIX_RE.match(text)
    if mode_match:
        mode = "direct" if mode_match.group(1).casefold() in ("直击", "direct") else "explosion"
        text = text[mode_match.end():].strip()

    if text in ("爆炸物", "爆炸物 列表"):
        lines = [f"💥 爆炸拆毁来源（{len(blasts)}）"]
        lines.extend(f"• {row['name']}｜DF {row.get('df')}" for row in blasts)
        return {"kind": "info", "text": "\n".join(lines)}
    if text in ("直击", "直击 列表"):
        rows = [source for source in sources if source.get("direct")]
        lines = [f"🎯 直击拆毁来源（{len(rows)}）"]
        for source in rows:
            values = "、".join(f"{hit['label']} DF {hit['df']}" for hit in source["direct"])
            lines.append(f"• {source['name']}｜{values}")
        return {"kind": "info", "text": "\n".join(lines)}
    if text in ("常见", "常见目标", "常见单位", "对照"):
        lines = [f"🎯 常见拆毁目标（{len(common_targets)}）"]
        lines.extend(
            f"• {row['label']}（{row['group']}）｜DF {row.get('df')}"
            f"{'｜需爆炸' if row.get('badr') else ''}"
            for row in common_targets
        )
        lines.append("发送 /拆毁值 <来源> 可同时对照直击与爆炸拆毁结果。")
        return {"kind": "info", "text": "\n".join(lines)}
    if text in ("建筑", "结构", "建筑 列表", "结构 列表"):
        lines = [f"🏗️ 建筑拆毁门槛（{len(structures)}）"]
        lines.extend(
            f"• {row['name']}｜DF {row.get('df')}"
            f"{'｜需爆炸' if row.get('badr') else ''}"
            for row in structures
        )
        return {"kind": "info", "text": "\n".join(lines)}
    if text.startswith("建筑 ") or text.startswith("结构 "):
        query = text.split(" ", 1)[1].strip()
        row = _find_exact(structures, query, "structures")
        if row is None:
            raise ValueError(f"未找到建筑「{query}」")
        return {"kind": "info", "text": _structure_detail(row, blasts)}
    if text.startswith("爆炸物 "):
        query = text.split(" ", 1)[1].strip()
        row = _find_exact(blasts, query, "blasts")
        if row is None:
            raise ValueError(f"未找到爆炸物「{query}」")
        return {"kind": "info", "text": _blast_detail(row)}
    if text.startswith("来源 "):
        query = text.split(" ", 1)[1].strip()
        row = _find_exact(sources, query, "sources")
        if row is None:
            raise ValueError(f"未找到拆毁来源「{query}」")
        return {"kind": "info", "text": _source_detail(row)}

    if "|" in text:
        fields = [field.strip() for field in text.split("|")]
        if len(fields) != 2 or not all(fields):
            raise ValueError("管道格式应为：来源 | 建筑")
        source = _find_exact(sources, fields[0], "sources")
        structure = _find_exact(structures, fields[1], "structures")
        if source is None:
            raise ValueError(f"未找到拆毁来源「{fields[0]}」")
        if structure is None:
            raise ValueError(f"未找到建筑「{fields[1]}」")
        rest = ""
    else:
        source = None
        rest = ""
        if mode == "explosion":
            try:
                blast, blast_rest = _match_prefix(blasts, text, "blasts")
            except ValueError:
                blast, blast_rest = None, ""
            if blast is not None:
                source = {
                    "name": blast["name"],
                    "aliases": list(_name_aliases(blast["name"], "blasts")),
                    "direct": [],
                    "blasts": [blast],
                }
                rest = blast_rest
        if source is None:
            source, rest = _match_prefix(sources, text, "sources")
        if source is None:
            raise ValueError("未识别拆毁来源；请使用 /拆毁值 直击 或 /拆毁值 爆炸物 查看")
        structure = None
        if rest:
            structure, trailing = _match_prefix(structures, rest, "structures")
            if structure is None:
                raise ValueError(f"未识别建筑「{rest}」")
            if trailing:
                raise ValueError(f"建筑名称后有多余内容「{trailing}」")

    if structure is None and distance is None:
        view = {
            "kind": "demolition",
            "view_mode": "common_comparison",
            "source": source,
            "structure": None,
            "mode": mode,
            "comparisons": _common_comparisons(source, common_targets, run_bridge),
            "results": [],
            "distance": None,
        }
        view["text"] = _format_common_comparison(view)
        return view

    results = []
    if mode in (None, "direct"):
        for hit in source.get("direct") or ():
            results.append({
                "mode": "直击 · " + hit["label"],
                "df": hit["df"],
                "distanceDf": None,
                "verdict": _direct_verdict(hit, structure) if structure else {"ok": None},
            })
    if mode in (None, "explosion"):
        for blast in source.get("blasts") or ():
            verdict = (
                run_bridge("demolition", {"blast": blast["name"], "structure": structure["name"]})
                if structure else {"ok": None}
            )
            distance_df = (
                run_bridge("demolitionAt", {"blast": blast["name"], "distance": distance})
                if distance is not None else None
            )
            results.append({
                "mode": "爆炸 · " + blast["name"],
                "df": blast["df"],
                "distanceDf": distance_df,
                "verdict": verdict,
                "blast": blast,
            })
    if not results:
        label = "直击" if mode == "direct" else "爆炸"
        raise ValueError(f"该来源没有已核实的{label}拆毁数据")

    view = {
        "kind": "demolition",
        "source": source,
        "structure": structure,
        "results": results,
        "distance": distance,
    }
    if structure:
        view["text"] = _format_result(view)
    else:
        lines = [f"💥 {source['name']}", f"距离：{distance:g} m"]
        for result in results:
            lines.append(
                f"• {result['mode']}：DF {result['df']}"
                + (f" → 距离处 {result['distanceDf']} DF" if result["distanceDf"] is not None else "")
            )
        lines.append("直击不受距离衰减；爆炸值按 v0.14.1 曲线截尾取整。")
        view["text"] = "\n".join(lines)
    return view


def format_view(view):
    return str(view.get("text") or "")
