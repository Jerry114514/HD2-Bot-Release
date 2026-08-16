# -*- coding: utf-8 -*-
"""
HD2 Bot 统一配置加载器（二次部署核心）
=========================================
配置来源（优先级从高到低）：
  1. 环境变量 HD2_CONFIG 指定的 JSON 文件
  2. <HD2_PROJECT_DIR>/config.json
  3. 本文件内 DEFAULTS（内置默认值）

部署者只需复制 config.example.json 为 config.json 并填写自己的
QQ 号 / 群号 / NapCat token / 路径即可，所有代码零硬编码。

路径字段支持相对路径（相对项目根自动解析）与绝对路径。
"""
import json
import os

DEFAULTS = {
    "bot": {
        "self_id": "",          # 机器人 QQ 号（NapCat 登录号）
        "admin_qq": "",         # 管理员 QQ（可选）
        "groups": [],           # 允许的群号列表（白名单）
        "default_group": 0,     # 推送默认目标群
    },
    "napcat": {
        "webui_url": "http://127.0.0.1:6099",   # NapCat WebUI
        "token": "",                            # NapCat WebUI token（登录用）
        "dir": "",                              # NapCat 目录（含 NapCatWinBootMain.exe）
        "qq_exe": "C:/Program Files/Tencent/QQNT/QQ.exe",
        "qrcode_rel": "cache/qrcode.png",       # 二维码相对 NapCat 目录
        "ws_url": "ws://127.0.0.1:6199/ws",     # AstrBot OneBot 反向 WS
    },
    "astrbot": {
        "exe": "D:/AstrBot/astrbot-desktop-tauri.exe",
        "python": "D:/AstrBot/backend/python/python.exe",
        "webui_port": 6185,
        "ws_port": 6199,
    },
    "paths": {
        "var_table": "tables/HD2行动变量对照表.md",
        "starmap": "tables/星图对照表_修正版.md",
        "guide": "tables/群使用说明.txt",
    },
    "webui": {
        "host": "127.0.0.1",
        "port": 8630,
    },
    "inject": {
        "fake_user": 2428164570,   # WS 注入测试用假成员号
        "fake_name": "绝地潜兵·测试员",
    },
    "data": {
        "translate_url": "https://api.mymemory.translated.net/get",
        "assignments_url": "https://cdn.helldiverscompanion.com/live/assignments/recent.json",
        "live_api_url": "https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live",
        "extended_api_url": "https://cdn.helldiverscompanion.com/live/extendedApiInformation/2days.json",
        "planets_url": "https://api.helldivers2.dev/api/v1/planets",
    },
}

_cache = None


def project_dir() -> str:
    """项目根目录：环境变量 HD2_PROJECT_DIR 优先，否则本文件所在目录"""
    return os.environ.get("HD2_PROJECT_DIR") or os.path.dirname(os.path.abspath(__file__))


def config_path() -> str:
    p = os.environ.get("HD2_CONFIG")
    if p:
        return p
    return os.path.join(project_dir(), "config.json")


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(force: bool = False) -> dict:
    """加载配置（合并 DEFAULTS + config.json），结果缓存"""
    global _cache
    if _cache is not None and not force:
        return _cache
    cfg = _deep_merge(DEFAULTS, {})
    path = config_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = _deep_merge(cfg, json.load(f))
        except Exception as e:
            print(f"[hd2_config] 读取配置失败 {path}: {e}")
    _cache = cfg
    return cfg


def get(*keys, default=None):
    """按键路径取值，如 get('bot','self_id')"""
    cur = load_config()
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def resolve(path: str) -> str:
    """相对路径 -> 基于项目根的绝对路径；绝对路径原样返回"""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(project_dir(), path)


def resolve_napcat(path: str) -> str:
    """相对路径 -> 基于 NapCat 目录的绝对路径"""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    ndir = get("napcat", "dir", default="")
    if not ndir:
        return path
    return os.path.join(ndir, path)


def require(*keys) -> str:
    """取配置，缺失时抛错（用于启动前校验）"""
    v = get(*keys)
    if v in (None, "", []):
        raise RuntimeError(f"配置缺失: {'.'.join(keys)}，请在 config.json 中填写")
    return v
