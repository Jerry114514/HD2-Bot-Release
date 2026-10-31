"""Commands-only by default; this does not intercept non-message notices."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot.api.event import filter
from astrbot.api.star import Star
from astrbot_plugin_hd2_common.commands import parse_event
from astrbot_plugin_hd2_common.data import setting

GROUP_PLATFORMS = {"aiocqhttp": "napcat", "qq_official": "qq_official", "qq_official_webhook": "qq_official"}


class Hd2GuardPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def guard(self, event):
        raw = getattr(event.message_obj, "raw_message", None)
        if (event.get_platform_name() == "aiocqhttp" and callable(getattr(raw, "get", None))
                and raw.get("post_type") == "notice"):
            return  # Notice listeners (such as welcome) are not chat commands.
        adapter = str(getattr(event, "unified_msg_origin", "")).split(":", 1)[0]
        selected = setting("bot", "platform_id", default="")
        if selected and adapter != selected:
            return
        groups = {str(g) for g in setting("bot", "groups", default=[])}
        parsed = parse_event(event)
        platform_name = event.get_platform_name()
        group_platform = GROUP_PLATFORMS.get(platform_name)
        # Legacy configurations contain NapCat's numeric group IDs. Official QQ
        # uses a separate OpenID namespace; its allowlist must be configured as such.
        applies = group_platform is None or group_platform == setting("bot", "platform", default="napcat")
        if parsed and groups and applies and str(getattr(event.message_obj, "group_id", "")) not in groups:
            event.stop_event()
            return
        if setting("bot", "allow_chat", default=False):
            return
        if event.is_wake_up() and not parsed:
            event.stop_event()
