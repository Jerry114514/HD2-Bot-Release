"""Commands-only by default; this does not intercept non-message notices."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot.api.event import filter
from astrbot.api.star import Star
from astrbot_plugin_hd2_common.commands import parse
from astrbot_plugin_hd2_common.data import setting


class Hd2GuardPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def guard(self, event):
        adapter = str(getattr(event, "unified_msg_origin", "")).split(":", 1)[0]
        selected = setting("bot", "platform_id", default="")
        if selected and adapter != selected:
            return
        groups = {str(g) for g in setting("bot", "groups", default=[])}
        if parse(event.message_str) and groups and str(getattr(event.message_obj, "group_id", "")) not in groups:
            event.stop_event()
            return
        if setting("bot", "allow_chat", default=False):
            return
        if event.is_wake_up() and not parse(event.message_str):
            event.stop_event()
