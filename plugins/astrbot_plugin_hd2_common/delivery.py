"""图文双通道分发：纯文本开关、渲染失败回退与统一埋点。

各业务插件不再各写一遍「先出图，失败回退文字」：这里集中处理
``plain``（``t`` / ``文字``）、``kind`` 白名单、多页合并结果以及日志口径，
保证图片与纯文本来自同一份 ViewModel（``view["text"]``）。
"""
from __future__ import annotations

from astrbot.api import logger

from astrbot_plugin_hd2_common.diagnostics import hd2_log
from astrbot_plugin_hd2_common.runtime import blocking, text_reply

__all__ = ["image_paths", "prepare"]


def image_paths(image):
    """渲染结果归一化为路径列表（单图 / 多页 / None）。"""
    if not image:
        return []
    if isinstance(image, (list, tuple)):
        return [str(p) for p in image if p]
    return [str(image)]


async def prepare(event, *, plugin: str, view, text: str, plain: bool = False,
                  kinds=None, render_fn=None):
    """按 plain / kind / 渲染结果返回 ``(待发送条目, 方式)``。

    调用方统一 ``items, mode = await prepare(...)`` 后逐条 ``yield``，
    再按 ``mode`` 记录 ``output_mode`` 埋点。
    """
    kind = view.get("kind") if isinstance(view, dict) else None
    want_image = bool(render_fn) and not plain and (kinds is None or kind in kinds)
    image = None
    if want_image:
        try:
            image = await blocking(render_fn, view)
        except Exception as error:  # 图片失败绝不能吞掉内容
            logger.warning(f"[HD2] {plugin} 图片不可用，回退文字：{type(error).__name__}")
            hd2_log("fallback", plugin=plugin, detail=type(error).__name__)
    paths = image_paths(image)
    if paths:
        return [event.image_result(path) for path in paths], "image"
    return [text_reply(event, text)], "text"
