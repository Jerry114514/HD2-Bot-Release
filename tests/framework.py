"""Small event contract doubles; no QQ transport or production framework state."""
import logging
import sys
import types


def install():
    def decorator(*args, **kwargs):
        def bind(fn):
            fn.priority = kwargs.get("priority", 0)
            return fn
        return bind
    class Star:
        def __init__(self, context=None):
            self.context = context
    class Component:
        def __init__(self, *args, **kwargs):
            self.__dict__.update(kwargs)
    modules = {name:types.ModuleType(name) for name in ["astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star", "astrbot.api.message_components", "astrbot.core", "astrbot.core.message", "astrbot.core.message.components"]}
    modules["astrbot.api"].logger = logging.getLogger("test_astrbot")
    modules["astrbot.api.event"].AstrMessageEvent = Event
    modules["astrbot.api.event"].filter = types.SimpleNamespace(event_message_type=decorator, EventMessageType=types.SimpleNamespace(ALL=0,GROUP_MESSAGE=1))
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].register = decorator
    for name in ("astrbot.api.message_components", "astrbot.core.message.components"):
        for cls in ("At", "Plain", "Reply", "Image"):
            setattr(modules[name],cls,type(cls,(Component,),{}))
    sys.modules.update(modules)


class Event:
    def __init__(self, text, group="official-group-A", user="official-user-B", wake=True):
        self.message_str=text
        self.message_obj=types.SimpleNamespace(group_id=group,raw_message={})
        self.unified_msg_origin="test_adapter:GroupMessage:"+group
        self.user=user
        self.wake=wake
        self.stopped=False
    def get_sender_id(self):return self.user
    def get_self_id(self):return "self"
    def is_wake_up(self):return self.wake
    def get_messages(self):return []
    def get_sender(self):return types.SimpleNamespace(card="",nickname="tester")
    def stop_event(self):self.stopped=True
    def plain_result(self,text):return ("text",text)
    def image_result(self,path):return ("image",path)
