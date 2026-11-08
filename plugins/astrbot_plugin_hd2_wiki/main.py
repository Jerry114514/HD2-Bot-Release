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
from astrbot_plugin_hd2_common import errors
from astrbot_plugin_hd2_common.data import setting
from astrbot_plugin_hd2_common.runtime import wiki_blocking, singleflight, text_reply, request_scope
from astrbot_plugin_hd2_common.diagnostics import hd2_log
from astrbot_plugin_hd2_wiki.wiki_data import wiki_lookup, format_text, catalogue, RULE_VERSION
from astrbot_plugin_hd2_wiki.chat_context import (
    WIKI_LOOKUP_TOOL, build_wiki_context, chat_search_text, polish_chat_reply, render_chat_reference,
    render_reply_policy, render_tool_result, reply_limit, should_ground,
)
from astrbot_plugin_hd2_common.search import normalize
from astrbot_plugin_hd2_common.choices import choices, PAGE_SIZE
from astrbot_plugin_hd2_common.menu_receipts import capture_menu_receipts


def render(keyword, value):
    from astrbot_plugin_hd2_wiki.wiki_card import render_wiki_card
    return render_wiki_card(keyword, data=value)


class _LocalTempTextPart:
    """离线环境（无 AstrBot）下与 ``TextPart.mark_as_temp`` 等价的最小内容块。"""

    type = "text"

    def __init__(self, text):
        self.text = text
        self._no_save = True

    def mark_as_temp(self):
        return self

    def model_dump_for_context(self):
        return {"type": "text", "text": self.text, "_no_save": True}


def _temp_text_part(text):
    """构造只发给模型、不写入会话历史的文本块（`_no_save` 由框架过滤）。"""
    try:
        from astrbot.core.agent.message import TextPart
    except Exception:
        return _LocalTempTextPart(text)
    part = TextPart(text=str(text))
    marker = getattr(part, "mark_as_temp", None) or getattr(part, "make_as_temp", None)
    if callable(marker):
        marker()
    else:
        part._no_save = True
    return part


def _append_temp_context(req, text):
    """把一次性上下文追加到用户消息尾部，保持系统提示词稳定、可缓存。"""
    parts = getattr(req, "extra_user_content_parts", None)
    if parts is None:
        try:
            parts = []
            req.extra_user_content_parts = parts
        except Exception:
            return False
    parts.append(_temp_text_part(text))
    return True


class Hd2WikiPlugin(Star):
    async def terminate(self):
        choices.clear()

    def _chat_deadline(self, default=5.5):
        try:
            timeout = float(setting("chat", "wiki_timeout_seconds", default=default))
        except (TypeError, ValueError):
            timeout = default
        return time.monotonic() + max(1.0, min(12.0, timeout))

    def _ensure_wiki_tool(self, req):
        """Keep the managed wiki tool visible even after plugin tool filtering."""
        try:
            manager = self.context.get_llm_tool_manager()
            tool = manager.get_func(WIKI_LOOKUP_TOOL)
            if tool is None:
                return
            if getattr(req, "func_tool", None) is None:
                from astrbot.api import ToolSet
                req.func_tool = ToolSet()
            req.func_tool.add_tool(tool)
        except Exception:
            pass

    def _direct_quote_text(self, event):
        try:
            for message in event.get_messages():
                if type(message).__name__ == "Reply":
                    return str(getattr(message, "message_str", "") or "")
        except Exception:
            pass
        return ""

    @filter.on_llm_request()
    async def ground_chat_with_wiki(self, event, req):
        """Inject bounded wiki evidence into ordinary role-play chat."""
        try:
            if parse_event(event) or getattr(event, "_hd2_choice", None) is not None:
                return
            if event.get_extra("_hd2_chat_policy_done"):
                return
            current_message = str(getattr(req, "prompt", None) or getattr(event, "message_str", "") or "").strip()
            message = chat_search_text(
                current_message,
                getattr(req, "extra_user_content_parts", ()) or (),
                self._direct_quote_text(event),
            )
            try:
                reply_chars = int(setting("chat", "max_reply_chars", default=180))
            except (TypeError, ValueError):
                reply_chars = 180
            _append_temp_context(req, render_reply_policy(current_message, reply_chars))
            event.set_extra("_hd2_chat_policy_done", True)
            self._ensure_wiki_tool(req)
            if not setting("chat", "wiki_grounding", default=True):
                return
            if event.get_extra("_hd2_wiki_grounding_done"):
                return
            if not should_ground(message):
                return
            event.set_extra("_hd2_wiki_grounding_done", True)
            deadline = self._chat_deadline()
            try:
                max_chars = int(setting("chat", "wiki_context_chars", default=2600))
            except (TypeError, ValueError):
                max_chars = 2600
            value = await asyncio.wait_for(
                wiki_blocking(build_wiki_context, message, deadline=deadline,
                              max_chars=max(800, min(6000, max_chars))),
                timeout=max(1.1, deadline - time.monotonic() + 0.5),
            )
            reference = render_chat_reference(value)
            _append_temp_context(req, reference)
            hd2_log("generated", plugin="chat_grounding", source=value.get("source"),
                    output_mode="prompt", search={"status": value.get("status"),
                    "terms": value.get("terms"), "pages": [p.get("title") for p in value.get("pages", [])],
                    "elapsed_ms": value.get("elapsed_ms")})
        except Exception as error:
            try:
                fallback = {
                    "status": "unavailable",
                    "source": "https://helldivers.wiki.gg/zh",
                    "query": str(getattr(req, "prompt", "") or ""),
                    "pages": [], "candidates": [],
                    "warnings": [type(error).__name__],
                }
                _append_temp_context(req, render_chat_reference(fallback))
            except Exception:
                pass
            hd2_log("failed", plugin="chat_grounding", detail=type(error).__name__,
                    search={"status": "unavailable"})

    @filter.on_llm_response()
    async def polish_chat_response(self, event, response):
        """Apply lossless cleanup to chat replies without truncating content."""
        if parse_event(event) or getattr(event, "_hd2_choice", None) is not None:
            return
        original = getattr(response, "completion_text", None)
        if not isinstance(original, str) or not original.strip():
            return
        try:
            configured = int(setting("chat", "max_reply_chars", default=180))
        except (TypeError, ValueError):
            configured = 180
        limit = reply_limit(getattr(event, "message_str", "") or "", configured)
        polished = polish_chat_reply(original, limit)
        if polished != original:
            response.completion_text = polished
            hd2_log("generated", plugin="chat_cleanup", output_mode="response",
                    detail={"before": len(original), "after": len(polished),
                            "budget": limit, "content_truncated": False})

    @filter.llm_tool(name=WIKI_LOOKUP_TOOL)
    async def wiki_chat_lookup(self, event, query: str):
        """查询《绝地潜兵2》中文 Wiki。用于核实武器、战备、敌人、机制、背景和专有名词；不要用它查询实时战况。

        Args:
            query(string): 要查询的 Wiki 主题或明确词条名
        """
        deadline = self._chat_deadline(default=9.0)
        try:
            value = await asyncio.wait_for(
                wiki_blocking(build_wiki_context, query, deadline=deadline, max_chars=4200),
                timeout=max(1.1, deadline - time.monotonic() + 0.5),
            )
        except Exception as error:
            value = {
                "status": "unavailable", "source": "https://helldivers.wiki.gg/zh",
                "query": str(query or ""), "pages": [], "candidates": [],
                "warnings": [type(error).__name__], "terms": [],
            }
        hd2_log("generated", plugin="chat_grounding_tool", source=value.get("source"),
                output_mode="tool", search={"query": str(query or "")[:160],
                "status": value.get("status"), "pages": [p.get("title") for p in value.get("pages", [])],
                "elapsed_ms": value.get("elapsed_ms")})
        return render_tool_result(value)

    async def _menu(self, event, menu, value=None):
        fresh = value is not None
        if fresh and not choices.prepare(menu, value):
            return
        if not choices.current(menu):
            return
        try:
            prompt = format_text(menu.value, choice_page=menu.page,
                official=event.get_platform_name() in ("qq_official", "qq_official_webhook"))
            result = text_reply(event, prompt)
            with capture_menu_receipts(event, result) as receipts:
                yield result
            # Code after yield runs only after the framework sends the prompt.
            # A stopped hook or failed send must not leave an invisible menu.
            if getattr(event, "_has_send_oper", True):
                choices.activate(menu, refresh=fresh)
                choices.bind(menu, receipts)
        finally:
            if menu.state == "publishing":
                choices.discard(menu)

    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def keyword_trigger(self, event):
        parsed = parse_event(event)
        action = getattr(event, "_hd2_choice", None)
        if action is None and (not parsed or parsed[0] != "wiki"):
            return
        menu = None
        selected = None
        owns_menu = False
        try:
            if action is not None:
                menu = action.menu
                if action.kind == "duplicate":
                    return
                quote_errors = {
                    "quote_unknown": "无法确认你引用的候选菜单。请重新发送 /wiki 关键词；若仍无法引用，可不引用消息，直接 @机器人 回复编号。",
                    "quote_owner": "这是其他人的 Wiki 候选菜单，请先发送 /wiki 关键词，获取自己的候选。",
                    "quote_expired": "你引用的 Wiki 候选菜单已失效，请重新发送 /wiki 关键词，或引用你最新的有效候选菜单。",
                    "quote_page": "你引用的是旧一页候选，请引用最新显示的那一页，再回复对应编号。",
                }
                if action.kind in quote_errors:
                    yield text_reply(event, quote_errors[action.kind])
                    return
                if action.kind == "cancelled":
                    yield text_reply(event, "已取消本次 Wiki 选择。")
                    return
                if not choices.current(menu):
                    return
                if action.kind == "page":
                    owns_menu = True
                    async for result in self._menu(event, menu):
                        yield result
                    return
                if action.kind in ("busy", "range", "boundary"):
                    start = menu.page * PAGE_SIZE + 1
                    end = min(start + PAGE_SIZE - 1, len(menu.value["candidates"])) if menu.value else 0
                    text = {"busy":"正在查询，请稍候。", "range":f"本页请选择 {start}–{end}，或回复“下一页”/“上一页”。",
                            "boundary":"已到候选列表边界，可选择本页编号或回复“取消”。"}[action.kind]
                    yield text_reply(event, text)
                    return
                selected = menu.value["candidates"][action.index]
                owns_menu = True
                keyword, plain = selected["title"], menu.plain
            else:
                _, keyword, plain = parsed
                if not keyword:
                    yield text_reply(event, "用法：/wiki 关键词")
                    return
                if getattr(event, "_hd2_choices_allowed", False):
                    menu = choices.begin(event, plain)
                    owns_menu = True
            deadline = time.monotonic()+20
            key = ("wiki",normalize(keyword),plain,catalogue()["version"],RULE_VERSION,
                   (selected.get("pageid"),selected["title"]) if selected is not None else None)
            options = {"selected":selected} if selected is not None else {}
            value = await asyncio.wait_for(singleflight(key, lambda: wiki_blocking(
                wiki_lookup,keyword,include_tables=not plain,deadline=deadline,**options)),timeout=20)
            if menu is not None and not choices.current(menu):
                return  # A newer command or ordinary message superseded this lookup.
            if menu is not None and value.get("candidates"):
                async for result in self._menu(event, menu, value):
                    yield result
                hd2_log("generated", plugin="wiki", source=value["url"], output_mode="text",
                        search={"status":value["status"],"candidate_count":len(value["candidates"]),
                                **value.get("diagnostics",{})})
                return
            text = format_text(value)
            images = None
            if not plain and value["status"] in ("resolved", "ok") and time.monotonic() < deadline:
                # 全量渲染（含全部表格/行/完整单元格）耗时随页面大小增长，
                # 给渲染阶段独立的 60s 预算，不再复用抓取阶段的剩余 deadline
                render_deadline = time.monotonic() + 60
                try:
                    images = await asyncio.wait_for(wiki_blocking(render, keyword, {**value, "deadline": render_deadline}),
                                                    timeout=60)
                except Exception as error:
                    logger.warning(f"[HD2] Wiki图片不可用：{type(error).__name__}")
            if menu is not None and not choices.current(menu):
                return
            if selected is not None and value["status"] == "unavailable":
                choices.activate(menu)
                text += "\n候选仍在有效期内时，可以重新回复编号。"
            elif owns_menu:
                choices.discard(menu)
            if images:
                for image in images if isinstance(images,list) else [images]:
                    yield event.image_result(image)
            else:
                yield text_reply(event, text)
            hd2_log("generated", plugin="wiki", group=getattr(event.message_obj,"group_id",""),
                    source=value["url"],data_version=str(value["rev"]),
                    user=event.get_sender_id(), platform=getattr(event,"unified_msg_origin",""),
                    output_mode="image" if images else "text", search={"status":value["status"],
                    "revision":value["rev"],"table_status":value.get("table_status"),
                    "candidate_count":len(value.get("candidates",[])),"complete":value.get("search_complete"),
                    "warnings":value.get("warnings",[]), **value.get("diagnostics",{})})
        except Exception as error:
            if menu is not None and not choices.current(menu):
                return
            if selected is not None:
                choices.activate(menu)
            elif owns_menu:
                choices.discard(menu)
            detail = "查询超时，尚不能判断是否存在该词条。" if isinstance(error,TimeoutError) else str(error)
            hd2_log("failed",plugin="wiki",detail=type(error).__name__,search={"status":"unavailable","complete":False})
            if isinstance(error,TimeoutError):
                yield text_reply(event, f"⚠️ Wiki 查询超时，尚不能判断是否存在该词条。\n💡 稍后重试；或加 t 请求纯文本结果。")
            else:
                yield text_reply(event, errors.friendly("wiki", error))
        finally:
            if owns_menu and menu is not None and menu.state in ("loading", "publishing"):
                choices.discard(menu)
            event.stop_event()
