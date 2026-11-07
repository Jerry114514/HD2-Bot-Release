# -*- coding: utf-8 -*-
"""Shared JSON bridge for the BTK and demolition plugins."""
import json
from pathlib import Path
import subprocess

from astrbot_plugin_hd2_common.data import setting


BRIDGE = Path(__file__).resolve().parent / "bridge.js"


def run(action, payload):
    node = str(setting("runtime", "node", default="node") or "node")
    try:
        timeout = float(setting("runtime", "btk_timeout_seconds", default=15))
    except (TypeError, ValueError):
        timeout = 15
    timeout = min(60, max(1, timeout))
    try:
        process = subprocess.run(
            [node, str(BRIDGE), action, json.dumps(payload, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except FileNotFoundError as error:
        raise RuntimeError("BTK 引擎不可用：未找到 Node 运行时") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("BTK 计算超时") from error
    try:
        response = json.loads((process.stdout or "").strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("BTK 引擎返回异常") from error
    if not response.get("ok"):
        raise ValueError(str(response.get("error") or "BTK 计算失败"))
    return response.get("result")
