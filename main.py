# -*- coding: utf-8 -*-
"""HD2 绝地潜兵2 战况机器人 · 全家桶（AstrBot 插件市场单一安装入口）

12 合一架构：本模块是市场发布的唯一注册入口。加载后实例化仓库
plugins/ 下的各子系统（实现零改动），并以**本模块**为注册位置提供
带子系统前缀的委托 handler——确保：
  · handler.__module__ 归属本插件（AstrBot 按 handler.__module__ 查
    star_map 分发，继承而来的方法会因 __module__ 指向子模块而失活）
  · handler_full_name（模块名_方法名）全局唯一（12 个子系统的
    keyword_trigger 若直接注册会互相覆盖）
  · guard(1000)/monitor(1100)/lookup(100) 等优先级与 12 插件分散部署
    完全一致
  · request_scope 的 ContextVar 嵌套安全（内外各自 set/reset）

配置：复制 config.example.json 为本插件目录内 config.json（即
data/plugins/<本插件>/config.json），字段见仓库 README。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, os.path.join(_ROOT, "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from astrbot_plugin_hd2_common.runtime import request_scope

# 子系统实现：仓库内既有插件类，逻辑零改动、直接复用
from astrbot_plugin_hd2_btk.main import Hd2BtkPlugin
from astrbot_plugin_hd2_demolition.main import Hd2DemolitionPlugin
from astrbot_plugin_hd2_guard.main import Hd2GuardPlugin
from astrbot_plugin_hd2_lookup.main import Hd2LookupPlugin
from astrbot_plugin_hd2_monitor.main import Hd2MonitorPlugin
from astrbot_plugin_hd2_planet_info.main import Hd2PlanetInfoPlugin
from astrbot_plugin_hd2_roll.main import Hd2RollPlugin
from astrbot_plugin_hd2_variant_query.main import Hd2VariantQueryPlugin
from astrbot_plugin_hd2_war_report.main import Hd2WarReportPlugin
from astrbot_plugin_hd2_welcome.main import Hd2WelcomePlugin
from astrbot_plugin_hd2_wiki.main import Hd2WikiPlugin

__all__ = ["Hd2SuitePlugin"]


class Hd2SuitePlugin(Star):
    """HD2 全家桶：战报/指令/战线/DSS/战役/星球/变种/Wiki/测伤/配装/帮助/欢迎/防护/埋点。"""

    def __init__(self, context: Context):
        super().__init__(context)
        self._war = Hd2WarReportPlugin(context)
        self._planet = Hd2PlanetInfoPlugin(context)
        self._variant = Hd2VariantQueryPlugin(context)
        self._wiki = Hd2WikiPlugin(context)
        self._roll = Hd2RollPlugin(context)
        self._btk = Hd2BtkPlugin(context)
        self._demolition = Hd2DemolitionPlugin(context)
        self._lookup = Hd2LookupPlugin(context)
        self._guard = Hd2GuardPlugin(context)
        self._monitor = Hd2MonitorPlugin(context)
        self._welcome = Hd2WelcomePlugin(context)

    async def initialize(self):
        await self._war.initialize()

    async def terminate(self):
        for sub, attr in ((self._war, "terminate"), (self._wiki, "terminate")):
            try:
                await getattr(sub, attr)()
            except Exception as error:
                logger.warning(f"[HD2] 子系统 terminate 异常：{type(error).__name__}: {error}")

    # ── 优先级 1100：命令日志埋点（先于一切） ──
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1100)
    async def suite_monitor(self, event):
        await self._monitor.monitor(event)

    # ── 优先级 1000：非指令拦截 / 平台白名单 ──
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def suite_guard(self, event):
        await self._guard.guard(event)

    # ── 优先级 100：/帮助 作战手册 ──
    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    @request_scope
    async def suite_lookup(self, event: AstrMessageEvent):
        async for reply in self._lookup.keyword_trigger(event):
            yield reply

    # ── 默认优先级：各战况子系统（原始注册顺序：btk/roll/variant/war/wiki/planet） ──
    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def suite_btk(self, event: AstrMessageEvent):
        async for reply in self._btk.keyword_trigger(event):
            yield reply

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def suite_demolition(self, event: AstrMessageEvent):
        async for reply in self._demolition.keyword_trigger(event):
            yield reply

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def suite_roll(self, event: AstrMessageEvent):
        async for reply in self._roll.keyword_trigger(event):
            yield reply

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def suite_variant(self, event: AstrMessageEvent):
        async for reply in self._variant.keyword_trigger(event):
            yield reply

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def suite_war_report(self, event: AstrMessageEvent):
        async for reply in self._war.keyword_trigger(event):
            yield reply

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def suite_wiki(self, event: AstrMessageEvent):
        async for reply in self._wiki.keyword_trigger(event):
            yield reply

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def suite_planet(self, event: AstrMessageEvent):
        async for reply in self._planet.keyword_trigger(event):
            yield reply

    # ── 群消息：进群欢迎 ──
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def suite_welcome(self, event: AstrMessageEvent):
        await self._welcome.on_group_message(event)
