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
from astrbot_plugin_hd2_common.runtime import wiki_blocking, singleflight, new_request, request_id
from astrbot_plugin_hd2_monitor.main import hd2_log
from astrbot_plugin_hd2_wiki.wiki_data import wiki_lookup, format_text, catalogue, RULE_VERSION
from astrbot_plugin_hd2_common.search import normalize


def render(keyword, value):
    from astrbot_plugin_hd2_wiki.wiki_card import render_wiki_card
    return render_wiki_card(keyword, data=value)


class Hd2WikiPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event):
        parsed = parse_event(event)
        if not parsed or parsed[0] != "wiki":
            return
        _, keyword, plain = parsed
        if not keyword:
            yield event.plain_result("用法：/wiki 关键词")
            event.stop_event()
            return
        token = new_request(event)
        try:
            deadline = time.monotonic()+20
            key = ("wiki",normalize(keyword),plain,catalogue()["version"],RULE_VERSION)
            value = await asyncio.wait_for(singleflight(key, lambda: wiki_blocking(
                wiki_lookup,keyword,include_tables=not plain,deadline=deadline)),timeout=20)
            text = format_text(value)
            images = None
            if not plain and value["status"] in ("resolved","ok") and time.monotonic()<deadline:
                try:
                    images = await asyncio.wait_for(wiki_blocking(render, keyword, {**value,"deadline":deadline}),
                                                    timeout=max(.01,deadline-time.monotonic()))
                except Exception as error:
                    logger.warning(f"[HD2] Wiki图片不可用：{type(error).__name__}")
            if images:
                for image in images if isinstance(images,list) else [images]:
                    yield event.image_result(image)
            else:
                yield event.plain_result(text)
            hd2_log("generated", plugin="wiki", group=getattr(event.message_obj,"group_id",""),
                    source=value["url"],data_version=str(value["rev"]),
                    user=event.get_sender_id(), platform=getattr(event,"unified_msg_origin",""),
                    output_mode="image" if images else "text", search={"status":value["status"],
                    "revision":value["rev"],"table_status":value.get("table_status"),
                    "candidate_count":len(value.get("candidates",[])),"complete":value.get("search_complete"),
                    "warnings":value.get("warnings",[]), **value.get("diagnostics",{})})
        except Exception as error:
            detail = "查询超时，尚不能判断是否存在该词条。" if isinstance(error,TimeoutError) else str(error)
            hd2_log("failed",plugin="wiki",detail=type(error).__name__,search={"status":"unavailable","complete":False})
            yield event.plain_result("⚠️ Wiki查询失败：" + detail)
        finally:
            request_id.reset(token)
            event.stop_event()
