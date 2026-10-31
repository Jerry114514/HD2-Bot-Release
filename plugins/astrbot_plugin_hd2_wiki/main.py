"""Wiki queries retain their text result when optional image dependencies are unavailable."""
import os
import sys
import asyncio
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Star
from astrbot_plugin_hd2_common.commands import parse_event
from astrbot_plugin_hd2_common.runtime import wiki_blocking, singleflight, text_reply, request_scope
from astrbot_plugin_hd2_common.diagnostics import hd2_log
from astrbot_plugin_hd2_wiki.wiki_data import wiki_lookup, format_text, catalogue, RULE_VERSION
from astrbot_plugin_hd2_common.search import normalize
from astrbot_plugin_hd2_common.choices import choices, PAGE_SIZE


def render(keyword, value):
    from astrbot_plugin_hd2_wiki.wiki_card import render_wiki_card
    return render_wiki_card(keyword, data=value)


class Hd2WikiPlugin(Star):
    async def terminate(self):
        choices.clear()

    async def _menu(self, event, menu, value=None):
        fresh = value is not None
        if fresh and not choices.prepare(menu, value):
            return
        if not choices.current(menu):
            return
        try:
            yield text_reply(event, format_text(menu.value, choice_page=menu.page,
                official=event.get_platform_name() in ("qq_official", "qq_official_webhook")))
            # Code after yield runs only after the framework sends the prompt.
            # A stopped hook or failed send must not leave an invisible menu.
            if getattr(event, "_has_send_oper", True):
                choices.activate(menu, refresh=fresh)
        finally:
            if menu.state == "publishing":
                choices.discard(menu)

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def keyword_trigger(self, event):
        parsed = parse_event(event)
        action = getattr(event, "_hd2_choice", None)
        if action is None and (not parsed or parsed[0] != "wiki"):
            return
        menu = None
        selected = None
        owns_menu = False
        try:
            if action is not None:
                menu = action.menu
                if action.kind == "duplicate":
                    return
                if action.kind == "cancelled":
                    yield text_reply(event, "已取消本次 Wiki 选择。")
                    return
                if not choices.current(menu):
                    return
                if action.kind == "page":
                    owns_menu = True
                    async for result in self._menu(event, menu):
                        yield result
                    return
                if action.kind in ("busy", "range", "boundary"):
                    start = menu.page * PAGE_SIZE + 1
                    end = min(start + PAGE_SIZE - 1, len(menu.value["candidates"])) if menu.value else 0
                    text = {"busy":"正在查询，请稍候。", "range":f"本页请选择 {start}–{end}，或回复“下一页”/“上一页”。",
                            "boundary":"已到候选列表边界，可选择本页编号或回复“取消”。"}[action.kind]
                    yield text_reply(event, text)
                    return
                selected = menu.value["candidates"][action.index]
                owns_menu = True
                keyword, plain = selected["title"], menu.plain
            else:
                _, keyword, plain = parsed
                if not keyword:
                    yield text_reply(event, "用法：/wiki 关键词")
                    return
                if getattr(event, "_hd2_choices_allowed", False):
                    menu = choices.begin(event, plain)
                    owns_menu = True
            deadline = time.monotonic()+20
            key = ("wiki",normalize(keyword),plain,catalogue()["version"],RULE_VERSION,
                   (selected.get("pageid"),selected["title"]) if selected is not None else None)
            options = {"selected":selected} if selected is not None else {}
            value = await asyncio.wait_for(singleflight(key, lambda: wiki_blocking(
                wiki_lookup,keyword,include_tables=not plain,deadline=deadline,**options)),timeout=20)
            if menu is not None and not choices.current(menu):
                return  # A newer command or ordinary message superseded this lookup.
            if menu is not None and value.get("candidates"):
                async for result in self._menu(event, menu, value):
                    yield result
                hd2_log("generated", plugin="wiki", source=value["url"], output_mode="text",
                        search={"status":value["status"],"candidate_count":len(value["candidates"]),
                                **value.get("diagnostics",{})})
                return
            text = format_text(value)
            images = None
            if not plain and value["status"] in ("resolved","ok") and time.monotonic()<deadline:
                try:
                    images = await asyncio.wait_for(wiki_blocking(render, keyword, {**value,"deadline":deadline}),
                                                    timeout=max(.01,deadline-time.monotonic()))
                except Exception as error:
                    logger.warning(f"[HD2] Wiki图片不可用：{type(error).__name__}")
            if menu is not None and not choices.current(menu):
                return
            if selected is not None and value["status"] == "unavailable":
                choices.activate(menu)
                text += "\n候选仍在有效期内时，可以重新回复编号。"
            elif owns_menu:
                choices.discard(menu)
            if images:
                for image in images if isinstance(images,list) else [images]:
                    yield event.image_result(image)
            else:
                yield text_reply(event, text)
            hd2_log("generated", plugin="wiki", group=getattr(event.message_obj,"group_id",""),
                    source=value["url"],data_version=str(value["rev"]),
                    user=event.get_sender_id(), platform=getattr(event,"unified_msg_origin",""),
                    output_mode="image" if images else "text", search={"status":value["status"],
                    "revision":value["rev"],"table_status":value.get("table_status"),
                    "candidate_count":len(value.get("candidates",[])),"complete":value.get("search_complete"),
                    "warnings":value.get("warnings",[]), **value.get("diagnostics",{})})
        except Exception as error:
            if menu is not None and not choices.current(menu):
                return
            if selected is not None:
                choices.activate(menu)
            elif owns_menu:
                choices.discard(menu)
            detail = "查询超时，尚不能判断是否存在该词条。" if isinstance(error,TimeoutError) else str(error)
            hd2_log("failed",plugin="wiki",detail=type(error).__name__,search={"status":"unavailable","complete":False})
            yield text_reply(event, "⚠️ Wiki查询失败：" + detail)
        finally:
            if owns_menu and menu is not None and menu.state in ("loading", "publishing"):
                choices.discard(menu)
            event.stop_event()
