# -*- coding: utf-8 -*-
"""测试引导：项目根入 sys.path + astrbot 桩（无 AstrBot 环境导入插件模块）"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 测试环境必须钉在本仓库：系统里可能残留指向旧部署的 HD2_PROJECT_DIR，
# 会让旧 tables 遮蔽仓库内新表（对齐工作要防的就是这个）。
os.environ["HD2_PROJECT_DIR"] = ROOT
for p in (ROOT, os.path.join(ROOT, "plugins")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _mod(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


def install_astrbot_stub() -> None:
    if "astrbot" in sys.modules and hasattr(sys.modules["astrbot"], "_hd2_stub"):
        return
    astrbot = _mod("astrbot")
    astrbot._hd2_stub = True
    api = _mod("astrbot.api")

    class _Logger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    api.logger = _Logger()

    api_event = _mod("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class _Filter:
        EventMessageType = type("EventMessageType", (), {"ALL": 1})

        def event_message_type(self, *_a, **_k):
            def deco(f):
                return f
            return deco

    api_event.AstrMessageEvent = AstrMessageEvent
    api_event.filter = _Filter()

    mc = _mod("astrbot.api.message_components")

    class Plain:
        pass

    mc.Plain = Plain

    star_mod = _mod("astrbot.api.star")

    class Context:
        pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    star_mod.Context = Context
    star_mod.Star = Star

    _mod("astrbot.core")
    _mod("astrbot.core.message")
    comp = _mod("astrbot.core.message.components")

    class At:
        pass

    class Reply:
        pass

    comp.At = At
    comp.Reply = Reply


install_astrbot_stub()
