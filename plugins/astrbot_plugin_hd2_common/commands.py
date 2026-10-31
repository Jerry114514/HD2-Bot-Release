"""Command parsing and presentation models shared by bot and console."""
from dataclasses import dataclass
import html
import json
from pathlib import Path
import re
import unicodedata

from . import data

ALIASES = {
    "战报": "report", "目前战况如何": "report", "现在在打哪里": "report",
    "重要指令是什么": "report", "战况如何": "report", "重要指令": "report",
    "打哪里": "report", "major order": "report", "majororder": "report",
    "战线": "campaigns", "dss": "dss", "战役": "briefing", "分析": "analysis",
    "api": "api", "银河星图": "map", "星图": "map",
    "roll": "roll", "随机战备": "roll", "roll战备": "roll",
    "wiki": "wiki", "wiki查询": "wiki", "查wiki": "wiki",
    "帮助": "help", "help": "help", "星球": "planet", "查询": "planet", "查": "planet",
    "变种": "variant", "查询变种": "variant", "查变种": "variant",
}
VARIANT_TERMS = json.loads((Path(__file__).parent / "variant_terms.json").read_text(encoding="utf-8"))


def parse_event(event):
    """Parse the adapter's original text, before AstrBot removes wake prefixes.

    Do not prepend '/' to the processed text: an @ or private message also wakes
    the framework, but must not turn ordinary conversation into an HD2 command.
    """
    original = getattr(getattr(event, "message_obj", None), "message_str", None)
    return parse(original if isinstance(original, str) else getattr(event, "message_str", ""))


def parse(message):
    text = " ".join(unicodedata.normalize("NFKC", message or "").split())
    text = re.sub(r"^@[\w.\-]+\s+", "", text)
    if not text.startswith("/"):
        return None
    text = text[1:].strip()
    for alias in sorted(ALIASES, key=len, reverse=True):
        if text.lower() == alias:
            return ALIASES[alias], "", False
        if text.lower() in (alias + "t", alias + "文字"):
            return ALIASES[alias], "", True
        if text.lower().startswith(alias + " "):
            args = text[len(alias):].strip()
            plain = args in ("t", "文字") or args.endswith((" t", " 文字"))
            if plain:
                args = args.rsplit(" ",1)[0] if " " in args else ""
            return ALIASES[alias], args, plain
    for english, chinese in VARIANT_TERMS.items():
        if text.upper() == english or text in (chinese, chinese + "信息"):
            return "variant", chinese, False
    # Keep the established /<planet>信息 spelling without substring-routing other commands.
    for suffix in ("信息", "情报", "详情", "info"):
        if text.lower().endswith(suffix) and len(text) > len(suffix):
            return "planet", text[:-len(suffix)].strip(), False
    return None


def clean(value):
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()


def names():
    return json.loads((Path(__file__).parent / "planet_names.json").read_text(encoding="utf-8"))


def planet_name(planet):
    name = planet.get("name") or str(planet.get("index", "未知星球"))
    return names().get(name.upper(), name)


FACTIONS = {"Humans": "超级地球", "Terminids": "终结族", "Terminid": "终结族",
            "Automaton": "机器人", "Automatons": "机器人", "Illuminate": "光能族",
            1: "超级地球", 2: "终结族", 3: "机器人", 4: "光能族"}
ACTION_NAMES = {"EAGLE STORM": "飞鹰风暴", "ORBITAL BLOCKADE": "轨道封锁", "HEAVY ORDNANCE DISTRIBUTION": "重型军械分发"}
TASK_NAMES = {2: "消灭敌人", 3: "完成任务", 9: "解放星球", 11: "解放指定星球", 13: "守住指定星球", 15: "收集资源"}


def mo_tasks(doc):
    mo, assignment = doc.get("major_order") or {}, doc.get("assignments") or {}
    enriched = mo.get("tasks") or {}
    rows = enriched.get("items", []) if isinstance(enriched, dict) else enriched
    if rows:
        return [{**row, "name": row.get("name") or row.get("label") or
                 (str(row.get("action") or TASK_NAMES.get(row.get("type"), "任务")) +
                  (" · " + str(row["faction_name"]) if row.get("faction_name") else ""))} for row in rows]
    result = []
    for i, task in enumerate(assignment.get("tasks") or []):
        values, types = task.get("values") or [], task.get("valueTypes") or []
        goal = next((values[j] for j, t in enumerate(types) if t == 3 and j < len(values)), None)
        pidx = next((values[j] for j, t in enumerate(types) if t == 12 and j < len(values)), None)
        planet = next((p for p in doc.get("planets", []) if p.get("index") == pidx), {})
        progress = assignment.get("progress") or []
        result.append({"type": task.get("type"), "name": TASK_NAMES.get(task.get("type"), f"任务{i+1}") +
                       (" · " + planet_name(planet) if planet else ""), "goal": goal,
                       "progress": progress[i] if i < len(progress) else None})
    return result


def value(value, digits=1):
    return "未知" if value is None else f"{value:.{digits}f}"


def order_text(doc):
    mo, assignment = doc.get("major_order") or {}, doc.get("assignments") or {}
    if not assignment and not mo:
        return "🎖️ 当前没有进行中的重要指令。"
    lines = ["🎖️ 重要指令", clean(mo.get("translated_brief") or assignment.get("briefing") or mo.get("brief"))]
    if lines[1] and not re.search(r"[\u4e00-\u9fff]", lines[1]):
        lines.insert(1, "暂缺中文简报，以下为来源原文：")
    for row in mo_tasks(doc):
        current, goal = row.get("progress"), row.get("goal")
        lines.append(f"• {clean(row.get('name') or row.get('label') or TASK_NAMES.get(row.get('type'), '任务'))}："
                     f"{value(data.percent(current, goal))}%（{data.fmt_big_num(current)}/{data.fmt_big_num(goal)}）")
    if assignment.get("deadline"):
        lines.append("截止：" + assignment["deadline"])
        if data.timestamp(assignment["deadline"]) is not None and data.timestamp(doc.get("fetchedAt")) is not None and data.timestamp(assignment["deadline"]) <= data.timestamp(doc["fetchedAt"]):
            lines.append("该指令截止时间已过，等待新指令。")
    return "\n".join(line for line in lines if line)


def campaign_rows(doc):
    planets = {str(p.get("index")): p for p in doc.get("planets", [])}
    rows = []
    for campaign in doc.get("campaigns") or []:
        planet = planets.get(str((campaign.get("planet") or {}).get("index", campaign.get("planetIndex"))))
        if planet:
            rows.append((planet, data.campaign_state(doc, planet)))
    return sorted(rows, key=lambda row: -(data.number(row[0].get("players")) or 0))


def campaigns_text(doc):
    lines = ["🌌 战线总览"]
    for planet, state in campaign_rows(doc):
        lines.append(f"• {planet_name(planet)}｜{FACTIONS.get(state['faction'], state['faction'])} {state['status']}｜"
                     f"{value(state['progress'])}%｜在线 {data.fmt_big_num(planet.get('players'))}｜"
                     f"净速率 {value(state['rate'], 3)}%/h（{'实测' if state['measured'] else '推演' if state['rate'] is not None else '未知'}）"
                     + (f"｜防御截止 {state['deadline']}" if state['defending'] and state['deadline'] else ""))
    return "\n".join(lines) if len(lines) > 1 else "🌌 当前没有进行中的战线。"


def find_planet(doc, query):
    query = data.planet_key(query)
    name_map = names()
    matches = {}
    for planet in doc.get("planets") or []:
        if query in (str(planet.get("index")), data.planet_key(planet.get("name")), data.planet_key(name_map.get(str(planet.get("name")).upper()))):
            matches[str(planet.get("index"))] = planet
    if len(matches) == 1:
        return next(iter(matches.values()))
    if matches:
        raise ValueError("星球名称有歧义，请使用星球 ID：" + "、".join(matches))
    raise ValueError("未找到该星球，请使用完整中文名、英文名或星球 ID")


def planet_text(doc, planet):
    state = data.campaign_state(doc, planet)
    lines = ["🪐 " + planet_name(planet), f"状态：{state['status']}", f"阵营：{FACTIONS.get(state['faction'], state['faction'])}",
             f"{'防御' if state['defending'] else '解放'}进度：{value(state['progress'])}%",
             f"在线：{data.fmt_big_num(planet.get('players'))}",
             f"净速率：{value(state['rate'], 3)}%/h（{'实测' if state['measured'] else '推演' if state['rate'] is not None else '未知'}）"]
    if state['deadline']:
        lines.append("防御截止：" + str(state['deadline']))
    for key, label in (("biome", "生态"), ("environmentals", "环境"), ("hazards", "环境风险"), ("activeEffects", "行动效果")):
        items = planet.get(key)
        if items:
            if not isinstance(items, list):
                items = [items]
            lines.append(label + "：" + "；".join(clean(x.get("description") or x.get("name")) if isinstance(x, dict) else clean(x) for x in items))
    return "\n".join(lines)


def dss_text(doc):
    dss = doc.get("dss") or {}
    if not dss:
        return "🛰️ 暂无民主空间站数据。"
    planet = next((p for p in doc.get("planets", []) if p.get("index") == dss.get("planetIndex")), {})
    lines = ["🛰️ 民主空间站", "停靠：" + planet_name(planet)]
    for action in dss.get("tacticalActions") or []:
        status = data.dss_phase(dss, action)
        lines.append("• " + ACTION_NAMES.get(action.get("name"), clean(action.get("name"))) + "｜" + status)
        for cost in action.get("cost") or []:
            lines.append(f"  捐献：{value(data.percent(cost.get('current'), cost.get('target')))}%（"
                         f"{data.fmt_big_num(cost.get('current'))}/{data.fmt_big_num(cost.get('target'))}）")
    return "\n".join(lines)


def briefing_text(doc):
    active = doc.get("active_campaign") or {}
    if not active.get("available"):
        return "🎬 当前没有可用的战役简报。"
    lines = ["🎬 " + clean(active.get("title_cn") or active.get("title")), clean(active.get("description_cn") or active.get("description"))]
    for phase in [data.current_phase(active)]:
        lines.extend([clean(phase.get("title_cn") or phase.get("title")), clean(phase.get("briefing_cn") or phase.get("briefing"))])
    return "\n\n".join(line for line in lines if line)


def news_text(doc):
    return "\n\n".join(clean(n.get("translated") or n.get("message")) for n in (doc.get("news") or [])[:3])


@dataclass
class Result:
    kind: str
    text: str
    document: dict
    query: str = ""


def result(kind, query="", doc=None):
    if kind == "api":
        issue = data.get_json("https://api.github.com/repos/Jerry114514/Jerry114514.github.io/issues/60", ttl=300) if doc is None else doc
        body = str(issue.get("body") or "暂无变更记录")
        url = issue.get("html_url") or "https://github.com/Jerry114514/Jerry114514.github.io/issues/60"
        return Result(kind, "📡 API变更监测（主仓Issue #60）\n" + body[:6000] + ("\n……完整记录：" + url if len(body)>6000 else "\n"+url),
                      {"fetchedAt":issue.get("updated_at") or "未知", "_issue_body":body})
    doc = data.site_doc() if doc is None else doc
    if kind == "planet":
        planet = find_planet(doc, query)
        query = planet["name"]
        body = planet_text(doc, planet)
    elif kind == "report":
        dist = data.player_distribution(doc)
        summary = "在线总数：" + data.fmt_big_num(dist["total"]) + "\n" + "｜".join(label+" "+data.fmt_big_num(dist[key]) for key,label in (("humans","超级地球"),("terminids","终结族"),("automatons","机器人"),("illuminate","光能族")))
        body = "\n\n".join([order_text(doc), summary, campaigns_text(doc), news_text(doc)])
    elif kind == "mo":
        body = order_text(doc)
    elif kind == "campaigns":
        body = campaigns_text(doc)
    elif kind == "dss":
        body = dss_text(doc)
    elif kind == "briefing":
        body = briefing_text(doc)
    else:
        raise ValueError("未知战况命令")
    meta = doc.get("_hd2") or {}
    footer = f"数据时间：{doc.get('fetchedAt', '未知')}" + ("｜缓存/数据陈旧" if meta.get("stale") else "")
    return Result(kind, body.strip() + "\n\n" + footer, doc, query)


def render_result(view):
    from astrbot_plugin_hd2_war_report import report_card as card
    functions = {"report": card.render_report_card, "campaigns": card.render_campaigns_card,
                 "dss": card.render_dss_card, "briefing": card.render_briefing_card, "api":card.render_api_card}
    with data.snapshot_scope(view.document):
        if view.kind == "planet":
            return card.render_planet_card(view.query)
        return functions[view.kind]()
