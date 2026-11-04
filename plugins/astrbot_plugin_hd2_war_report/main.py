"""HD2 war commands: one snapshot, one result, image or text presentation."""
import asyncio
import os
import sys
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
try:
    from astrbot.api.event import MessageChain
except ImportError:
    from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.star import Context, Star

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot_plugin_hd2_common import api_watch, commands, data, orders
from astrbot_plugin_hd2_common.runtime import blocking, text_reply, request_scope
from astrbot_plugin_hd2_common.sources import (
    fetch_json as _fetch_json, fetch_official as _fetch_official,
    load_planet_map as _load_planet_map, PLANET_NAME_CN, OFFICIAL_WAR_ID, LIVE_API_URL,
)
from astrbot_plugin_hd2_common.diagnostics import LOG_FILE, hd2_log as _log
from astrbot_plugin_hd2_war_report.mo_seen import MoSeenStore

GALAXY_MAP_REPLY = "🌌 中文银河星图\n" + data.site_url("galaxy-map-v2.html")
WAR_KINDS = {"report", "mo", "campaigns", "dss", "briefing", "analysis", "api", "map"}
_fmt_big_num = data.fmt_big_num


def _site_doc(force=False):
    return data.site_doc(force)


def _site_zh(force=False):
    doc = data.site_doc(force)
    mo = doc.get("major_order") or {}
    return {"brief": mo.get("translated_brief") or "", "brief_title": mo.get("title") or "",
            "news": {str(n["id"]): {"text": n.get("translated") or ""} for n in doc.get("news", []) if n.get("id") is not None}}


def _get_major_order(planet_map=None, brief_translated=None, brief_cn=None):
    return commands.result("mo").text


def _get_campaigns(planet_map=None):
    return commands.result("campaigns").text


def _get_warzone_distribution():
    return commands.campaigns_text(data.site_doc())


async def _get_dss_status(planet_map=None, context=None):
    return (await blocking(commands.result, "dss")).text


def event_fields(event):
    return {"group": str(getattr(event.message_obj, "group_id", "") or ""),
            "user": str(event.get_sender_id() or ""), "platform": str(getattr(event, "unified_msg_origin", "") or "")}


class Hd2WarReportPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._warm_task = None
        self._api_task = None
        self._api_lock = None
        self._api_state = api_watch.ApiWatchState(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "api_watch_state.json")
        )
        self._mo_seen = MoSeenStore(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "mo_seen.json")
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def keyword_trigger(self, event: AstrMessageEvent):
        parsed = commands.parse_event(event)
        if not parsed or parsed[0] not in WAR_KINDS:
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return
        kind, args, plain = parsed
        if args:
            yield text_reply(event, "⚠️ 该命令不接受额外参数；可加 t 或文字查看纯文本。")
            event.stop_event()
            return
        begin = time.monotonic()
        fields = event_fields(event)
        _log("matched", plugin="war_report", text=event.message_str, **fields)
        try:
            if kind == "map":
                yield text_reply(event, GALAXY_MAP_REPLY)
            elif kind == "analysis":
                yield text_reply(event, await self._get_analysis(getattr(event,"unified_msg_origin",None)))
            else:
                view = await blocking(commands.result, kind)
                if self.context is not None:
                    major = (orders.special_orders(view.document).get("major") or {})
                    revision = str(major.get("id") or "")
                    user_key = self._mo_seen.key_for(event)
                    if kind in ("report", "mo"):
                        await blocking(self._mo_seen.mark_seen, user_key, revision)
                    else:
                        for item in await self._new_mo_announcements(event, view.document):
                            yield item
                if kind == "api":
                    await blocking(
                        self._api_state.subscribe,
                        getattr(event, "unified_msg_origin", ""),
                        view.document.get("_issue_revision"),
                    )
                images = None
                if not plain:
                    try:
                        images = await blocking(commands.render_result, view)
                    except Exception as error:
                        logger.warning(f"[HD2] 图片不可用，使用同快照文字：{type(error).__name__}")
                        _log("degraded", plugin="war_report", detail="图片不可用，沿用同一结果", **fields)
                if images:
                    for path in images if isinstance(images, list) else [images]:
                        yield event.image_result(path)
                else:
                    yield text_reply(event, view.text)
                _log("generated", plugin="war_report", output_mode="image" if images else "text",
                     source=(view.document.get("_hd2") or {}).get("source", ""),data_version=view.document.get("fetchedAt", ""),
                     source_time=view.document.get("fetchedAt"), stale=(view.document.get("_hd2") or {}).get("stale"),
                     cost=time.monotonic() - begin, **fields)
        except Exception as error:
            logger.warning(f"[HD2] 命令失败：{error}")
            _log("failed", plugin="war_report", detail=str(error), **fields)
            yield text_reply(event, "⚠️ " + str(error))
        finally:
            event.stop_event()

    async def _get_analysis(self, origin=None):
        view = await blocking(commands.result, "report")
        conf = self.context.get_config(origin)
        provider_id = (conf.get("provider_settings") or {}).get("default_provider_id")
        if not provider_id:
            return "⚠️ 未配置分析使用的 LLM。"
        response = await self.context.llm_generate(
            chat_provider_id=provider_id, prompt=view.text,
            system_prompt="依据给出的完整战线、重要指令、防御截止和推进速率，用简体中文给出350字以内的行动建议。"
                          "结合指令约束、期限与当前进度说明取舍；不把解放度高固定视为最高优先级。"
                          "未知速率、陈旧数据或缺失的补给线信息必须说明，不能补造。星球名沿用输入。",
        )
        return (response.completion_text or "").strip() or "⚠️ 分析暂不可用。"

    async def _new_mo_announcements(self, event, document):
        major = (orders.special_orders(document).get("major") or {})
        revision = str(major.get("id") or "")
        user_key = self._mo_seen.key_for(event)
        if not await blocking(self._mo_seen.needs_announcement, user_key, revision):
            return []
        view = await blocking(commands.result, "mo", doc=document)
        try:
            images = await blocking(commands.render_result, view)
        except Exception as error:
            logger.warning(f"[HD2] MO 自动通知图片不可用，回退文字：{type(error).__name__}")
            images = None
        await blocking(self._mo_seen.mark_seen, user_key, revision)
        if images:
            return [
                event.image_result(path)
                for path in (images if isinstance(images, list) else [images])
            ]
        return [text_reply(event, view.text)]

    async def _get_campaign_brief(self):
        return (await blocking(commands.result, "briefing")).text

    async def _build_report(self):
        return (await blocking(commands.result, "report")).text

    async def _warm_loop(self):
        while True:
            try:
                await blocking(data.site_doc)
            except Exception as error:
                logger.warning(f"[HD2] 快照预热失败：{error}")
            await asyncio.sleep(240)

    async def _notify_api_sessions(self, issue):
        if self.context is None or not issue:
            return
        view = await blocking(commands.result, "api", doc=issue)
        images = None
        try:
            images = await blocking(commands.render_result, view)
        except Exception as error:
            logger.warning(f"[HD2] API 通知图片不可用，回退文字：{type(error).__name__}")
        revision = str(issue.get("revision") or "")
        for origin in self._api_state.pending_sessions(revision):
            try:
                chain = MessageChain()
                if images:
                    for path in images if isinstance(images, list) else [images]:
                        chain.file_image(path)
                else:
                    chain.message(api_watch.issue_text(issue))
                sent = await self.context.send_message(origin, chain)
                if sent is False:
                    logger.warning(f"[HD2] API 通知目标不可用：{origin}")
                    continue
                self._api_state.mark_delivered(origin, revision)
                _log("api_push", plugin="war_report", target=origin, detail=revision)
            except Exception as error:
                logger.warning(f"[HD2] API 通知发送失败 {origin}：{type(error).__name__}")

    async def _poll_api_watch(self):
        async with self._api_lock:
            issue = await blocking(api_watch.fetch_issue60)
            revision = str(issue.get("revision") or "")
            previous = self._api_state.last_revision()
            changed = self._api_state.observe(issue)
            if not previous:
                for origin in self._api_state.pending_sessions(revision):
                    self._api_state.mark_delivered(origin, revision)
                return
            if not changed and not self._api_state.pending_sessions(revision):
                return
            await self._notify_api_sessions(issue)

    async def _api_watch_loop(self):
        interval = max(60, int(data.setting(
            "data", "api_watch_interval", default=api_watch.DEFAULT_INTERVAL_SECONDS
        )))
        while True:
            try:
                await self._poll_api_watch()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(f"[HD2] API 每分钟巡检失败：{type(error).__name__}")
            await asyncio.sleep(interval)

    async def initialize(self):
        self._api_lock = asyncio.Lock()
        self._api_state.discover_from_log(LOG_FILE)
        if data.setting("data", "api_watch_enable", default=True):
            self._api_task = asyncio.create_task(self._api_watch_loop())
        if data.setting("data", "report_warm_enable", default=True):
            self._warm_task = asyncio.create_task(self._warm_loop())

    async def terminate(self):
        if self._api_task:
            self._api_task.cancel()
            try:
                await self._api_task
            except asyncio.CancelledError:
                pass
            self._api_task = None
        if self._warm_task:
            self._warm_task.cancel()
            try:
                await self._warm_task
            except asyncio.CancelledError:
                pass
            self._warm_task = None
