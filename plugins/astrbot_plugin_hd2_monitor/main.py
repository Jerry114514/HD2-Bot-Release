"""Bounded command diagnostics. Generation and platform acknowledgement are distinct."""
import json
import os
from pathlib import Path
import sys
import threading
import time

from astrbot.api.event import filter
from astrbot.api.star import Context, Star

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from astrbot_plugin_hd2_common.commands import parse_event
from astrbot_plugin_hd2_common.runtime import request_id, new_request

from astrbot_plugin_hd2_common.diagnostics import hd2_log


class Hd2MonitorPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1100)
    async def monitor(self, event):
        if not parse_event(event):
            return
        token = new_request(event)
        try:
            hd2_log("received", text=event.message_str,
                    group=getattr(event.message_obj, "group_id", ""),
                    user=event.get_sender_id(), platform=getattr(event, "unified_msg_origin", ""),
                    wake=event.is_wake_up())
        finally:
            request_id.reset(token)
