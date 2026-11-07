# -*- coding: utf-8 -*-
"""/拆毁值 command backed by hd2-btk-sdk v0.14.1."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Star

from astrbot_plugin_hd2_btk.demolition import build_view, format_view
from astrbot_plugin_hd2_btk.runtime_bridge import run as run_bridge
from astrbot_plugin_hd2_common import errors
from astrbot_plugin_hd2_common.commands import parse_event
from astrbot_plugin_hd2_common.diagnostics import hd2_log
from astrbot_plugin_hd2_common.runtime import blocking, request_scope, text_reply


def render(view):
    from astrbot_plugin_hd2_demolition.demolition_card import render_demolition_card
    return render_demolition_card(view)


class Hd2DemolitionPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def keyword_trigger(self, event):
        parsed = parse_event(event)
        if not parsed or parsed[0] != "demolition":
            return
        _, args, plain = parsed
        started = time.monotonic()
        group = str(getattr(event.message_obj, "group_id", "") or "")
        try:
            hd2_log("matched", plugin="demolition", text=getattr(event, "message_str", ""),
                    group=group, user=event.get_sender_id())
            view = await blocking(build_view, args, run_bridge)
            image = None
            if view.get("kind") == "demolition" and not plain:
                try:
                    image = await blocking(render, view)
                except Exception as error:
                    logger.warning(f"[HD2-DEMO] 图片不可用，回退文字：{type(error).__name__}")
            if image:
                for path in image if isinstance(image, list) else [image]:
                    yield event.image_result(path)
            else:
                yield text_reply(event, format_view(view))
            hd2_log("done", plugin="demolition", group=group,
                    user=event.get_sender_id(), cost=time.monotonic() - started,
                    output_mode="image" if image else "text")
        except Exception as error:
            logger.warning(f"[HD2-DEMO] {type(error).__name__}: {error}")
            hd2_log("failed", plugin="demolition", detail=type(error).__name__,
                    group=group, user=event.get_sender_id())
            yield text_reply(event, errors.friendly("demolition", error))
        finally:
            event.stop_event()
