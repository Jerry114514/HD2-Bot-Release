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
        # ---- 主站数据源（Jerry114514.github.io，Actions 每 5 分钟更新，全部可覆盖）----
        # 站点基址：data.json / 实测帧 / 翻译产物 / tables 的公共前缀
        "site_base": "https://jerry114514.github.io/HD2-Galatic_war-Map",
        # 站点战况主文件：含 dispatches（30 条原始新闻）+ news（最新 10 条官方中文）
        #   + assignments/dss/campaigns/planets/player_distribution/ownership_history 等
        "news_json_url": "https://jerry114514.github.io/HD2-Galatic_war-Map/data.json",
        # 实测帧目录（15 分钟粒度，解放速率 / MO 速率实测；生成时间超 3 小时视为断更自动停用）
        "recent_base": "https://jerry114514.github.io/HD2-Galatic_war-Map/data/recent",
        "recent_fresh_max_hours": 3,
        # 翻译仓库推送到公开 Pages 的产物（data.json 合并滞后时的译文回退层）
        "transnews_url": "https://jerry114514.github.io/HD2-Galatic_war-Map/data/translated/TransNews.json",
        "campaign_zh_url": "https://jerry114514.github.io/HD2-Galatic_war-Map/data/campaign_zh.json",
        # 主站 tables 目录（本地 tables/ 缺文件时从这里拉）
        "tables_base": "https://jerry114514.github.io/HD2-Galatic_war-Map/tables",
        # 官方 API（最后兜底链）与风控头（无密钥；建议换成自己的 QQ 号）
        "official_api_url": "https://api.live.prod.thehelldiversgame.com",
        "super_client": "YOUR_QQ_NUMBER",
        # 信息流来源：hd2map=优先读站点 JSON（失败自动回退 TransNews→API）；api=只走原来的 API
        "news_source": "hd2map",
        "news_cache_ttl": 120,
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
