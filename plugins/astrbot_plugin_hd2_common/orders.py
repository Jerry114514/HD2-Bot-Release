"""Independent strategic directive, opportunity and minor-order channels."""
from datetime import datetime, timezone
import re

from . import data as shared
from .sources import PLANET_NAME_CN


CHANNEL_META = {
    "directive": {"cn": "战略指令", "en": "STRATEGIC DIRECTIVE", "icon": "⌖"},
    "opportunity": {"cn": "战略机遇", "en": "STRATEGIC OPPORTUNITY", "icon": "◇"},
    "minor": {"cn": "次要指令", "en": "MINOR ORDER", "icon": "▸"},
}


def _text(value):
    return shared._plain_rich_text(value)


def _assignments(doc):
    rows = doc.get("assignments_all")
    if not isinstance(rows, list) or not rows:
        assignment = doc.get("assignments")
        rows = [assignment] if isinstance(assignment, dict) and assignment.get("tasks") else []
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not row.get("tasks"):
            continue
        key = str(row.get("id") or id(row))
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _title(assignment):
    return _text((assignment or {}).get("title")).upper()


def _assignment_active(doc, assignment):
    if not assignment:
        return False
    deadline = shared.timestamp(assignment.get("deadline") or assignment.get("expiration"))
    now = shared.timestamp(doc.get("fetchedAt")) or datetime.now(timezone.utc).timestamp()
    return deadline is None or deadline > now


def _latest_assignment(rows, matcher):
    matches = [row for row in rows if matcher.search(_title(row))]
    return max(
        matches,
        key=lambda row: (
            shared.timestamp(row.get("startedAt") or row.get("startTime")) or 0,
            shared.number(row.get("id")) or 0,
        ),
        default=None,
    )


def _official_texts(doc):
    value = doc.get("official_texts") or doc.get("_official_texts") or {}
    return value if isinstance(value, dict) else {}


def _assignment_translation(doc, assignment):
    wanted = str((assignment or {}).get("id") or "")
    if not wanted:
        return {}
    for row in _official_texts(doc).get("assignments") or []:
        if isinstance(row, dict) and str(row.get("id32")) == wanted:
            return row
    return {}


def _split_dispatch(message):
    title, body, _runs = shared.split_rich_text(message)
    return title, body


def _dispatch_maps(doc):
    dispatches = {}
    for row in doc.get("dispatches") or []:
        if isinstance(row, dict) and row.get("id") is not None:
            dispatches[str(row["id"])] = dict(row)
    official = _official_texts(doc)
    for row in official.get("dispatches") or []:
        if isinstance(row, dict) and row.get("id") is not None:
            key = str(row["id"])
            dispatches.setdefault(key, {}).update(row)
    news = {
        str(row.get("id")): row
        for row in doc.get("news") or []
        if isinstance(row, dict) and row.get("id") is not None
    }
    official_map = {
        str(row.get("id")): row
        for row in official.get("dispatches") or []
        if isinstance(row, dict) and row.get("id") is not None
    }
    return dispatches, news, official_map


def resolve_dispatch(doc, matcher=None, preferred_id=None):
    dispatches, news, official = _dispatch_maps(doc)
    row = None
    if preferred_id is not None:
        row = dispatches.get(str(preferred_id))
    elif matcher is not None:
        candidates = []
        for key, item in dispatches.items():
            raw = item.get("message") or item.get("raw_message") or item.get("title") or ""
            title, _body = _split_dispatch(raw)
            if matcher.search(title):
                candidates.append(item)
        row = max(
            candidates,
            key=lambda item: (
                shared.timestamp(item.get("published") or item.get("published_at")) or 0,
                shared.number(item.get("id")) or 0,
            ),
            default=None,
        )
    if not row:
        return None
    key = str(row.get("id") or "")
    news_item, official_item = news.get(key, {}), official.get(key, {})
    content = next((
        value for value in (
            # Tagged sources first, by field kind (official > raw > original),
            # regardless of which object carries the field.
            news_item.get("official_message"),
            official_item.get("official_message"),
            news_item.get("raw_message"),
            official_item.get("raw_message"),
            news_item.get("message"),
            row.get("raw_message"),
            row.get("message"),
            # Translation-chain fields are plain text and only serve as fallback.
            news_item.get("translated"),
            news_item.get("message_zh"),
            official_item.get("translated"),
            row.get("original"),
            official_item.get("original"),
        )
        if _text(value)
    ), "")
    title, body, rich_runs = shared.split_rich_text(content)
    return {
        "id": row.get("id"),
        "published": row.get("published") or row.get("published_at") or official_item.get("published_at"),
        "title": title or _text(row.get("title")),
        "body": body,
        "rich_text": rich_runs,
        "source": official_item.get("text_source") or ("news" if news_item else "dispatch"),
    }


def _task_rows(doc, assignment, offset=0):
    planets = {
        str(row.get("index")): row
        for row in doc.get("planets") or []
        if isinstance(row, dict) and row.get("index") is not None
    }
    progress_rows = (assignment or {}).get("progress") or []
    rows = []
    for index, task in enumerate(((assignment or {}).get("tasks") or [])[offset:], offset):
        if not isinstance(task, dict):
            continue
        values, types = task.get("values") or [], task.get("valueTypes") or []
        goal = next((values[pos] for pos, kind in enumerate(types) if kind == 3 and pos < len(values)), None)
        planet_index = next((values[pos] for pos, kind in enumerate(types) if kind == 12 and pos < len(values)), None)
        current = progress_rows[index] if index < len(progress_rows) else None
        planet = planets.get(str(planet_index), {})
        raw_name = str((planet or {}).get("name") or "")
        planet_name = (
            PLANET_NAME_CN.get(raw_name.upper(), raw_name)
            if planet else (f"星球 #{planet_index}" if planet_index is not None else "")
        )
        held = str((planet or {}).get("currentOwner")) in ("Humans", "1")
        task_type = int(task.get("type") or 0)
        done = held if task_type == 13 else (
            current is not None and goal is not None and current >= goal
        )
        percent = shared.percent(current, goal)
        rows.append({
            "index": index,
            "type": task_type,
            "type_cn": "守持" if task_type == 13 else "目标",
            "planet_index": planet_index,
            "planet": planet,
            "planet_name": planet_name,
            "goal": goal,
            "current": current,
            "percent": percent,
            "done": done,
            "state_cn": ("已守住" if done else "待守") if task_type == 13 else (
                "已完成" if done else f"{(percent or 0):.1f}%"
            ),
            "url": shared.site_url(f"planet.html?idx={planet_index}") if planet_index is not None else "",
        })
    return rows


def _deadline(assignment):
    return (assignment or {}).get("deadline") or (assignment or {}).get("expiration") or ""


def _deadline_text(doc, assignment):
    deadline = shared.timestamp(_deadline(assignment))
    now = shared.timestamp(doc.get("fetchedAt")) or datetime.now(timezone.utc).timestamp()
    if deadline is None:
        return ""
    seconds = max(0, int(deadline - now))
    if seconds == 0:
        return "已结束"
    days, remain = divmod(seconds, 86400)
    hours, remain = divmod(remain, 3600)
    minutes = remain // 60
    return f"剩余 {days}天 {hours}小时 {minutes}分"


def _model(doc, kind, assignment=None, dispatch=None, opening_dispatch=None, tasks=None, active=None, status_cn=""):
    meta = CHANNEL_META[kind]
    assignment_translation = _assignment_translation(doc, assignment)
    title = (
        (dispatch or {}).get("title")
        or _text(assignment_translation.get("title"))
        or _text((assignment or {}).get("title"))
        or meta["cn"]
    )
    raw_body = (dispatch or {}).get("body") or ""
    raw_source = (
        raw_body
        or assignment_translation.get("raw_message")
        or assignment_translation.get("translated")
        or (assignment or {}).get("briefing")
        or ""
    )
    body = raw_body or _text(raw_source)
    is_active = _assignment_active(doc, assignment) if active is None and assignment else bool(active)
    if not assignment and dispatch:
        is_active = bool(active)
    return {
        **meta,
        "kind": kind,
        "assignment": assignment,
        "dispatch": dispatch,
        "opening_dispatch": opening_dispatch,
        "title": title,
        "body": body,
        "rich_text": (dispatch or {}).get("rich_text") or shared.rich_text_runs(raw_source),
        "tasks": list(tasks or ()),
        "active": is_active,
        "status_cn": status_cn or ("进行中" if is_active else "已结束" if assignment or dispatch else ""),
        "status_en": "ACTIVE" if is_active else "ENDED" if assignment or dispatch else "",
        "deadline": _deadline(assignment),
        "deadline_text": _deadline_text(doc, assignment),
        "empty": not bool(assignment or dispatch or opening_dispatch),
    }


def special_orders(doc):
    assignments = _assignments(doc)
    major = _latest_assignment(assignments, re.compile(r"^MAJOR\s+ORDER|^重要指令", re.I))
    if major is None and isinstance(doc.get("assignments"), dict):
        major = doc["assignments"]
    directive_assignment = _latest_assignment(
        assignments, re.compile(r"STRATEGIC\s+(?:DIRECTIVE|THREAT)|战略指令|战略威胁", re.I)
    )
    minor_assignment = _latest_assignment(assignments, re.compile(r"^MINOR\s+ORDER|^次要指令", re.I))
    directive = resolve_dispatch(doc, re.compile(r"NEW\s+STRATEGIC\s+DIRECTIVE|STRATEGIC\s+DIRECTIVE", re.I))

    strategic = doc.get("strategic") or {}
    opportunity_status = resolve_dispatch(doc, preferred_id=strategic.get("dispatch_id"))
    opportunity_opening = resolve_dispatch(
        doc, re.compile(r"^NEW\s+STRATEGIC\s+OPPORTUNITY\s*$", re.I)
    )
    opportunity_active = bool(strategic.get("active"))
    opportunity = _model(
        doc,
        "opportunity",
        assignment=major if opportunity_active else None,
        dispatch=opportunity_status or opportunity_opening,
        opening_dispatch=opportunity_opening,
        tasks=_task_rows(doc, major, 1) if opportunity_active and major else [],
        active=opportunity_active,
        status_cn=_text(strategic.get("status_cn")) or ("进行中" if opportunity_active else "已结束"),
    )
    if strategic.get("status"):
        opportunity["status_en"] = str(strategic["status"]).upper()

    minor_dispatch = resolve_dispatch(doc, re.compile(r"(?:NEW\s+)?MINOR\s+ORDER(?:\s|$)", re.I))
    if minor_assignment:
        minor = _model(
            doc, "minor", assignment=minor_assignment, dispatch=minor_dispatch,
            tasks=_task_rows(doc, minor_assignment),
        )
    elif major and len(major.get("tasks") or []) > 1:
        fallback = dict(major)
        fallback["title"] = "重要指令附加目标"
        fallback["briefing"] = (
            "当前重要指令包含附加守持目标。最高指挥部会在指令截止时一并核验"
            "下列星球的控制状态。"
        )
        minor = _model(
            doc, "minor", assignment=fallback, dispatch=None,
            tasks=_task_rows(doc, major, 1),
        )
        minor["title"] = "重要指令附加目标"
        minor["body"] = (
            "当前重要指令包含附加守持目标。最高指挥部会在指令截止时一并核验"
            "下列星球的控制状态。"
        )
        minor["rich_text"] = shared.rich_text_runs(minor["body"])
        minor["fallback"] = True
    else:
        minor = _model(doc, "minor", dispatch=minor_dispatch)

    return {
        "major": major,
        "directive": _model(
            doc, "directive", assignment=directive_assignment, dispatch=directive,
            tasks=_task_rows(doc, directive_assignment),
            status_cn="进行中" if _assignment_active(doc, directive_assignment) else "已结束",
        ),
        "opportunity": opportunity,
        "minor": minor,
    }


def special_order_dispatch_ids(doc):
    models = special_orders(doc)
    ids = set()
    for model in models.values():
        if not isinstance(model, dict):
            continue
        for key in ("dispatch", "opening_dispatch"):
            dispatch_id = (model.get(key) or {}).get("id")
            if dispatch_id is not None:
                ids.add(str(dispatch_id))
    return ids


def filtered_news(doc):
    excluded = special_order_dispatch_ids(doc)
    mo_revision = str(shared.major_order_content(doc).get("dispatch_id") or "")
    if mo_revision:
        excluded.add(mo_revision)
    return [
        item for item in doc.get("news") or []
        if isinstance(item, dict) and str(item.get("id")) not in excluded
    ]


def special_orders_text(doc, models=None):
    models = models or special_orders(doc)
    blocks = ["📌 独立指令通道"]
    for kind in ("directive", "opportunity", "minor"):
        model = models.get(kind) or {}
        meta = CHANNEL_META[kind]
        state = model.get("status_cn") or "无记录"
        blocks.append(f"{meta['icon']} {meta['cn']} / {meta['en']}｜{state}")
        if model.get("empty"):
            blocks.append(f"当前无{meta['cn']}。")
            continue
        blocks.append(model.get("title") or meta["cn"])
        if model.get("body"):
            blocks.append(model["body"])
        if model.get("fallback"):
            blocks.append("来源标识：重要指令附加目标（不是官方独立 MINOR ORDER）")
        if model.get("deadline_text"):
            blocks.append("截止：" + model["deadline_text"])
        for task in model.get("tasks") or []:
            detail = f"• {task['type_cn']} {task['planet_name']}｜{task['state_cn']}"
            if task.get("url"):
                detail += "｜" + task["url"]
            blocks.append(detail)
    return "\n\n".join(block for block in blocks if block)
