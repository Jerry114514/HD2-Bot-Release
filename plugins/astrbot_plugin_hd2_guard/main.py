"""Commands-only by default; this does not intercept non-message notices."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot.api.event import filter
from astrbot.api.star import Star
from astrbot_plugin_hd2_common.commands import parse_event
from astrbot_plugin_hd2_common.data import setting
from astrbot_plugin_hd2_common.choices import choices

GROUP_PLATFORMS = {"aiocqhttp": "napcat", "qq_official": "qq_official", "qq_official_webhook": "qq_official"}


class Hd2GuardPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def guard(self, event):
        event._hd2_choice = None
        event._hd2_choices_allowed = False
        raw = getattr(event.message_obj, "raw_message", None)
        if (event.get_platform_name() == "aiocqhttp" and callable(getattr(raw, "get", None))
                and raw.get("post_type") == "notice"):
            return  # Notice listeners (such as welcome) are not chat commands.
        adapter = str(getattr(event, "unified_msg_origin", "")).split(":", 1)[0]
        selected = setting("bot", "platform_id", default="")
        if selected and adapter != selected:
            choices.cancel(event)
            return
        groups = {str(g) for g in setting("bot", "groups", default=[])}
        parsed = parse_event(event)
        platform_name = event.get_platform_name()
        group_platform = GROUP_PLATFORMS.get(platform_name)
        # Legacy configurations contain NapCat's numeric group IDs. Official QQ
        # uses a separate OpenID namespace; its allowlist must be configured as such.
        applies = group_platform is None or group_platform == setting("bot", "platform", default="napcat")
        if groups and applies and str(getattr(event.message_obj, "group_id", "")) not in groups:
            choices.cancel(event)
            if parsed or not setting("bot", "allow_chat", default=False):
                event.stop_event()
            return
        event._hd2_choices_allowed = True
        if parsed:
            choices.cancel(event)
        else:
            get_extra = getattr(event, "get_extra", None)
            handlers = get_extra("activated_handlers") if callable(get_extra) else None
            wiki_enabled = handlers is None or any(
                h.handler_module_path.endswith("astrbot_plugin_hd2_wiki.main") for h in handlers)
            if wiki_enabled:
                event._hd2_choice = choices.handle(event)
            else:
                choices.cancel(event)
            if event._hd2_choice is not None:
                return
        if setting("bot", "allow_chat", default=False):
            return
        if event.is_wake_up() and not parsed:
            event.stop_event()
