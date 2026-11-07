# -*- coding: utf-8 -*-
"""
HD2 Bot 统一配置加载器（二次部署核心）
=========================================
配置来源（优先级从高到低）：
  1. 环境变量 HD2_CONFIG 指定的 JSON 文件
  2. <HD2_PROJECT_DIR>/config.json
  3. 本文件内 DEFAULTS（内置默认值）

部署者只需复制 config.example.json 为 config.json 并填写自己的
QQ 号 / 群号 / SnowLuma accessToken / 路径即可，所有代码零硬编码。

路径字段支持相对路径（相对项目根自动解析）与绝对路径。

协议端：本项目通过 OneBot v11 接入 QQ，推荐协议端为 SnowLuma
（NapCat 同团队的新一代实现）；bot.platform 填 napcat（或别名
snowluma / onebot，运行时归一化）即代表"OneBot 协议端通道"。
"""
import json
import os
import copy

PLATFORM_ALIASES = {"snowluma": "napcat", "onebot": "napcat"}

DEFAULTS = {
    "bot": {
        "platform": "napcat",     # napcat(=OneBot协议端通道,接受别名 snowluma/onebot) / qq_official; official credentials are configured in AstrBot
        "platform_id": "",        # exact AstrBot adapter id for per-platform management
        "allow_chat": False,
        "self_id": "",          # 机器人 QQ 号（协议端登录号）
        "admin_qq": "",         # 管理员 QQ（可选）
        "groups": [],           # bot.platform 的群白名单：协议端群号 / 官方群OpenID，不跨平台套用
        "default_group": 0,     # 推送默认目标群
    },
    "snowluma": {
        "webui_url": "http://127.0.0.1:5099",   # SnowLuma WebUI（默认 5099）
        "onebot_http": "http://127.0.0.1:3000", # OneBot v11 HTTP 服务（控制台推送走它）
        "access_token": "",                     # OneBot accessToken（SnowLuma 数据目录 config/onebot*.json 的 accessToken）
        "dir": "",                              # SnowLuma 目录（含 launcher.bat / launcher.sh / index.mjs）
        "qq_exe": "C:/Program Files/Tencent/QQNT/QQ.exe",
        "ws_url": "ws://127.0.0.1:6199/ws",     # AstrBot OneBot 反向 WS
    },
    "astrbot": {
        "data_dir": "",           # explicitly selected instance; never scan other profiles
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
    "chat": {
        "wiki_grounding": True,
        "wiki_timeout_seconds": 5.5,
        "wiki_context_chars": 2600,
        "max_reply_chars": 180,
    },
    "data": {
        "site_base": "https://jerry114514.github.io/HD2-Galatic_war-Map",
        "max_stale_seconds": 3600,
        "api_watch_enable": True,
        "api_watch_interval": 60,
        "translate_url": "https://api.mymemory.translated.net/get",
        "assignments_url": "https://cdn.helldiverscompanion.com/live/assignments/recent.json",
        "live_api_url": "https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live",
        "extended_api_url": "https://cdn.helldiverscompanion.com/live/extendedApiInformation/2days.json",
        "planets_url": "https://api.helldivers2.dev/api/v1/planets",
        # 站点（HD2-Galatic_war-Map）每 5 分钟生成的战况数据：信息流的现成 JSON 源。
        # 含 dispatches（30 条原始新闻）+ news（最新 10 条的中文译文），机器人直接读它，
        # 不必自己现抓 API，也顺带省掉这批新闻的 LLM 翻译。
        "news_json_url": "https://jerry114514.github.io/HD2-Galatic_war-Map/data.json",
        # 信息流来源：hd2map=优先读上面的 JSON（失败自动回退 API）；api=只走原来的 API
        "news_source": "hd2map",
        "news_cache_ttl": 120,
    },
}

_cache = None
_cache_signature = None


def project_dir() -> str:
    """项目根目录：环境变量 HD2_PROJECT_DIR 优先，否则本文件所在目录"""
    return os.environ.get("HD2_PROJECT_DIR") or os.path.dirname(os.path.abspath(__file__))


def config_path() -> str:
    p = os.environ.get("HD2_CONFIG")
    if p:
        return p
    return os.path.join(project_dir(), "config.json")


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(force: bool = False) -> dict:
    """加载配置（合并 DEFAULTS + config.json），结果缓存"""
    global _cache, _cache_signature
    path = config_path()
    stat = os.stat(path) if os.path.exists(path) else None
    signature = (path, stat.st_mtime_ns, stat.st_size) if stat else (path, None, None)
    if _cache is not None and not force and signature == _cache_signature:
        return _cache
    cfg = _deep_merge(DEFAULTS, {})
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg = _deep_merge(cfg, json.load(f))
        except (ValueError, OSError) as e:
            raise RuntimeError(f"配置文件不可用：{path}") from e
    _cache = cfg
    _cache_signature = signature
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
    """相对路径 -> 基于协议端（SnowLuma）目录的绝对路径；兼容旧函数名"""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    ndir = get("snowluma", "dir", default="") or get("napcat", "dir", default="")
    if not ndir:
        return path
    return os.path.join(ndir, path)


def norm_platform(mode: str) -> str:
    """bot.platform 值归一化：snowluma / onebot 是 napcat（OneBot 协议端通道）的别名"""
    return PLATFORM_ALIASES.get(str(mode or "").strip().lower(), mode)


def require(*keys) -> str:
    """取配置，缺失时抛错（用于启动前校验）"""
    v = get(*keys)
    if v in (None, "", []):
        raise RuntimeError(f"配置缺失: {'.'.join(keys)}，请在 config.json 中填写")
    return v
