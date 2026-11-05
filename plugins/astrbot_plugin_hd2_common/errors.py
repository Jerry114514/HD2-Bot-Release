# -*- coding: utf-8 -*-
"""指令报错提示统一化（astrbot_plugin_hd2_common/errors.py）

历史问题：各插件 catch 后自行拼字符串，格式不一、暴露异常类名
（如「变种数据不可用：TimeoutError」），且不区分网络故障 / 参数错误 /
数据缺失，用户不知道下一步该做什么。

统一口径（friendly）：
  1. 网络类（超时/连接失败/HTTP 错误）→ 说明是主站或上游不可达，给重试建议
  2. 参数/数据语义错误（ValueError 等，消息本身已面向用户）→ 保留语义 + 附用法示例
  3. 其余未知异常 → 只报异常类型不抛内部细节，引导稍后重试或反馈
"""
import socket
import urllib.error

# 网络类异常（URLError 覆盖 HTTPError；ConnectionError/gaierror 是 OSError 子类但语义明确）
NETWORK_ERRORS = (TimeoutError, ConnectionError, socket.gaierror, urllib.error.URLError)

# 指令 → (对用户的中文名, 默认用法示例)
USAGE = {
    "report": ("战报", "示例：/战报 或 /战报t（纯文本）"),
    "mo": ("重要指令", "示例：/重要指令 或 /重要指令t"),
    "campaigns": ("战线", "示例：/战线 或 /战线t"),
    "dss": ("DSS", "示例：/dss 或 /dsst"),
    "briefing": ("战役", "示例：/战役 或 /战役t"),
    "api": ("API 监测", "示例：/api"),
    "map": ("银河星图", "示例：/星图"),
    "planet": ("星球查询", "示例：/查询 X-45，或 /星球 <星球名>"),
    "variant": ("变种查询", "示例：/变种 <星球名>，或 /查变种"),
    "wiki": ("Wiki 查询", "示例：/wiki <词条名>"),
    "roll": ("随机战备", "示例：/roll（纯模式）或 /rollt"),
    "btk": ("BTK 测伤", "示例：/btk测伤 <武器> [目标]"),
    "analysis": ("行动分析", "示例：/分析"),
    "help": ("帮助", "示例：/帮助 或 /help"),
}


def friendly(command, error, usage=None):
    """把异常转为统一格式的用户提示。

    command：USAGE 的 key（如 "report"）或任意中文名；
    usage：自定义用法提示，覆盖该指令的默认示例。
    """
    title, default_usage = USAGE.get(command, (str(command), ""))
    use = (usage or default_usage).strip()

    if isinstance(error, NETWORK_ERRORS):
        lines = [f"⚠️ {title}暂时不可用：主站或上游接口网络异常，请稍后重试。"]
        if use:
            lines.append(f"💡 持续失败可加 t 改用纯文本（{use}）。")
        return "\n".join(lines)

    msg = str(error).strip() or type(error).__name__
    if isinstance(error, (ValueError, KeyError, LookupError)):
        # 语义错误的消息本身已写明原因与建议（find_planet / 变种源校验等）
        body = f"⚠️ {title}：{msg}"
        if use and ("未找到" in msg or "歧义" in msg or "用法" in msg or "无效" in msg):
            body += f"\n💡 {use}"
        return body

    detail = f"（{type(error).__name__}）"
    return (f"⚠️ {title}：命令执行失败{detail}，请稍后重试。\n"
            f"💡 若持续出现，请反馈并附上时间点，管理员可在日志中定位。")
