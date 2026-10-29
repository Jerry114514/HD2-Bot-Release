# -*- coding: utf-8 -*-
"""astrbot_plugin_hd2_wiki —— /wiki 词条查询

/wiki <关键词>：搜索 helldivers.wiki.gg 中文站（MediaWiki 只读匿名 API），
取词条纯文本与主图渲染黑金 HUD 图片卡（无编辑按钮/空段落模板等界面残留）。
渲染失败回退文本（wiki_lookup 的纯文本结果）。

依赖：渲染基建与样式复用 astrbot_plugin_hd2_war_report.report_card（跨插件导入）。
"""
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

import os as _os
import sys as _sys

# 监控埋点（hd2_monitor）+ 跨插件导入（lookup 同款模式）
_PLUGINS_PARENT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PLUGINS_PARENT not in _sys.path:
    _sys.path.insert(0, _PLUGINS_PARENT)
_MON_PARENT = _PLUGINS_PARENT
try:
    from astrbot_plugin_hd2_monitor.main import hd2_log as _log
except Exception:
    def _log(*a, **k):
        pass

try:
    from astrbot_plugin_hd2_wiki.wiki_card import render_wiki_card, wiki_lookup
except Exception as _e:
    render_wiki_card = None
    wiki_lookup = None
    logger.warning(f"[HD2-Wiki] 渲染器导入失败，/wiki 将走文本兜底: {_e}")


def _gid(event):
    try:
        return int(getattr(event.message_obj, "group_id", 0) or 0)
    except Exception:
        return 0


def _uid(event):
    try:
        return int(event.get_sender_id() or 0)
    except Exception:
        return 0


def _uname(event):
    try:
        s = event.get_sender()
        return (getattr(s, "card", "") or getattr(s, "nickname", "") or "")
    except Exception:
        return ""


class Hd2WikiPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event: AstrMessageEvent):
        """/wiki 触发：/wiki 或 /wiki 关键词"""
        text = (event.message_str or "").strip()
        if not text:
            return
        try:
            if str(event.get_sender_id()) == str(event.get_self_id()):
                return
        except Exception:
            pass
        if not text.startswith("/"):
            return
        cmd = text[1:].strip()
        if cmd.split(" ", 1)[0].lower() not in ("wiki", "wiki查询", "查wiki"):
            return
        kw = cmd.split(" ", 1)[1].strip() if " " in cmd else ""
        _t0 = time.time()
        _log("matched", plugin="wiki", text=text, group=_gid(event), user=_uid(event), name=_uname(event))
        if not kw:
            yield event.plain_result(
                "📖 用法：/wiki 关键词\n"
                "搜索 helldivers.wiki.gg 中文站词条，如 /wiki 战略配备、/wiki 飞鹰")
            event.stop_event()
            return
        # 图片卡优先
        if render_wiki_card:
            try:
                card = render_wiki_card(kw)
                if card:
                    yield event.image_result(card)
                    _log("done", plugin="wiki", text=kw, group=_gid(event), user=_uid(event),
                         cost=time.time() - _t0, detail=f"wiki-card {card}")
                    event.stop_event()
                    return
            except Exception as e:
                logger.warning(f"[HD2-Wiki] 图片卡渲染失败，回退文本: {e}")
        # 文本回退：同源 wiki_lookup 纯文本
        try:
            if not wiki_lookup:
                yield event.plain_result("⚠️ Wiki 模块不可用（渲染基建缺失）。")
                event.stop_event()
                return
            data = wiki_lookup(kw)
            if data.get("status") == "not_found":
                yield event.plain_result(
                    "📖 未在 helldivers.wiki.gg 中文站找到「%s」。\n站内搜索：%s/search?search=%s"
                    % (kw, "https://helldivers.wiki.gg/zh", kw))
            else:
                body = f"📖 {data['title']}\n{data['url']}\n\n{data['text']}"
                if data.get("candidates"):
                    body += "\n\n相关词条：" + " · ".join(data["candidates"])
                yield event.plain_result(body)
            _log("done", plugin="wiki", text=kw, group=_gid(event), user=_uid(event),
                 cost=time.time() - _t0, detail="wiki-text")
        except Exception as e:
            _log("failed", plugin="wiki", text=kw, group=_gid(event), user=_uid(event),
                 cost=time.time() - _t0, detail=str(e)[:150])
            yield event.plain_result(f"⚠️ Wiki 查询失败：{e}")
        event.stop_event()
        return
