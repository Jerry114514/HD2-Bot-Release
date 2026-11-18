# -*- coding: utf-8 -*-
"""
HD2 战报机器人 · Web 控制台（浏览器操作，替代命令行 TUI）
用法: python hd2_webui.py  → 自动打开 http://127.0.0.1:8630

功能：
  1. 推送战报 / 随机战备 / 星球信息 / 变种查询 / 自定义消息（真实发送到群）
  2. 机器人响应测试（WS 注入伪装群成员指令，不污染群聊，直接看机器人回复）
"""
import sys
import io
import os
import re
import json
import time
import socket
import shutil
import secrets
import tempfile
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# UTF-8 统一
if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ============ 配置化（hd2_config + config.json） ============
_ROOT = os.environ.get("HD2_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    import hd2_config as _cfg
except Exception:
    _cfg = None


def _g(*k, d=None):
    return _cfg.get(*k, default=d) if _cfg else d

PYTHON = _g("astrbot", "python", d="python")
BASE = _ROOT
HD2 = _ROOT
TEMP = os.path.join(BASE, "temp")
os.makedirs(TEMP, exist_ok=True)
def _default_group() -> int:
    """默认推送群：**每次调用时**读配置。
    不能用模块级常量——那样"设为默认推送群"后必须重启控制台才生效。"""
    try:
        return int(_g("bot", "default_group", d=0) or 0)
    except Exception:
        return 0


def _target_group(raw=None):
    if instance.platform() != "napcat":
        raise ValueError("控制台主动推送只支持已配置的协议端通道（SnowLuma）；官方 QQ 请在 AstrBot 适配器中配置支持的会话能力")
    value = _default_group() if raw is None or str(raw).strip() == "" else raw
    return int(_norm_gid(value))
PORT = int(_g("webui", "port", d=8630))
from scripts import instance
sys.path.insert(0, os.path.join(_ROOT, "plugins"))
from astrbot_plugin_hd2_common import commands as service
from astrbot_plugin_hd2_common import diagnostics as _diagnostics
from astrbot_plugin_hd2_common.diagnostics import hd2_log
from astrbot_plugin_hd2_common.runtime import new_request, request_id
_CONFIG_LOCK = threading.RLock()
CSRF_TOKEN = secrets.token_urlsafe(32)

SEND_SCRIPT = os.path.join(BASE, "scripts", "onebot_send_msg.py")
GEN_REPORT = os.path.join(BASE, "scripts", "gen_report_live.py")
REPORT_FILE = os.path.join(TEMP, "report_live2.txt")
WS_INJECT = os.path.join(BASE, "scripts", "ws_inject.py")
RESTART_HELPER = os.path.join(BASE, "scripts", "restart_webui.py")
GUIDE_FILE = _cfg.resolve(_g("paths", "guide", d="tables/群使用说明.txt")) if _cfg else os.path.join(BASE, "tables", "群使用说明.txt")

PLUGIN_PLANET = os.path.join(BASE, "plugins", "astrbot_plugin_hd2_planet_info")
PLUGIN_ROLL = os.path.join(BASE, "plugins", "astrbot_plugin_hd2_roll")
PLUGIN_VARIANT = os.path.join(BASE, "plugins", "astrbot_plugin_hd2_variant_query")
PLUGIN_WAR = os.path.join(BASE, "plugins", "astrbot_plugin_hd2_war_report")

APP_PATHS = [p for p in [_g("astrbot", "app_dir", d="")] if p]

import importlib.util


def _load_plugin(plugin_dir: str):
    """按唯一模块名加载插件 main.py，避免 `import main` 的 sys.modules 缓存冲突（多插件同名模块问题）"""
    path = os.path.join(plugin_dir, "main.py")
    mod_name = "hd2_plugin_" + str(abs(hash(plugin_dir)))
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ===== 一键启动部署相关（配置化） =====
ASTRBOT_EXE = _g("astrbot", "exe", d="")
SNOWLUMA_DIR = _g("snowluma", "dir", d="")
SNOWLUMA_QQ_EXE = _g("snowluma", "qq_exe", d="")
SNOWLUMA_WEBUI_URL = _g("snowluma", "webui_url", d="http://127.0.0.1:5099")

PS = ["powershell", "-NoProfile", "-Command"]


def _ps(cmd: str, timeout=20) -> str:
    try:
        r = subprocess.run(PS + [cmd], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _proc_names() -> list:
    if os.name == "nt":
        out = _ps("Get-Process | Where-Object { $_.ProcessName -match 'astrbot|SnowLuma|node|QQ' } | Select-Object -ExpandProperty ProcessName")
        return [l.strip() for l in out.splitlines() if l.strip()]
    try:
        out = subprocess.run(["ps", "-eo", "comm="], capture_output=True, text=True, timeout=15)
        return [l.strip() for l in (out.stdout or "").splitlines()
                if l.strip().lower().startswith(("astrbot", "napcat", "snowluma", "node", "qq"))]
    except Exception:
        return []


def _port_listen(port: int) -> bool:
    """socket 探测，Windows / Linux 通用"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False


def _tcp_established_count(port: int) -> int:
    """统计与本地 port 处于 ESTABLISHED 的连接数（Windows 用 PowerShell，Linux 用 /proc）"""
    if os.name == "nt":
        return 1 if _ps(f"(Get-NetTCPConnection -LocalPort {port} -State Established -ErrorAction SilentlyContinue | Measure-Object).Count") not in ("", "0") else 0
    count = 0
    local_hex = f"{int(port):04X}"
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table, encoding="ascii") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    if parts[3] != "01":  # 01 = ESTABLISHED
                        continue
                    if parts[1].rsplit(":", 1)[1].upper() == local_hex or parts[2].rsplit(":", 1)[1].upper() == local_hex:
                        count += 1
        except (OSError, ValueError, IndexError):
            continue
    return count


def _ws_connected():
    if instance.platform() != "napcat":
        return False
    port = int(_g("astrbot","ws_port",d=6199))
    return _tcp_established_count(port) > 0


def _start_astrbot():
    port = int(_g("astrbot","webui_port",d=6185))
    if _port_listen(port):
        instance.verified_pids("astrbot")
        return f"✅ 当前实例已监听 {port}；平台连接状态请在 AstrBot 中确认"
    exe = _cfg.resolve(str(_g("astrbot","exe",d="")))
    if not os.path.isfile(exe):
        return "❌ 未找到已配置的 AstrBot 程序"
    subprocess.Popen([exe],env=dict(os.environ,HD2_PROJECT_DIR=_ROOT))
    return "ℹ️ AstrBot 启动请求已提交，尚未确认平台在线"


def _start_snowluma() -> str:
    """启动 OneBot 协议端 SnowLuma：直接复用启动器逻辑（端口检测 → launcher.bat/sh → 扫码提示）"""
    from scripts.launch import start_snowluma
    return start_snowluma()


def action_boot_all():
    lines = [_start_astrbot()]
    if instance.platform() == "napcat":
        lines.append(_start_snowluma())
    else:
        lines.append("官方平台模式，请在 AstrBot 适配器完成认证；跳过协议端登录")
    return "\n".join(lines)


def action_status():
    port = int(_g("astrbot","webui_port",d=6185))
    lines = ["当前接入：" + instance.platform(), f"AstrBot {port}：" + ("已监听" if _port_listen(port) else "未监听")]
    if instance.platform() == "napcat":
        lines.append(f"OneBot {_g('astrbot','ws_port',d=6199)}：" + ("存在连接（不等于消息发送成功）" if _ws_connected() else "未连接"))
        lines.append(f"协议端 WebUI {SNOWLUMA_WEBUI_URL}：" + ("已监听" if _port_listen(instance.port("napcat")) else "未监听"))
    else:
        lines.append("官方平台认证和连接状态在 AstrBot 适配器中确认；控制台不使用协议端推送/欢迎能力")
    return "\n".join(lines)


def action_restart_snowluma():
    if instance.platform() != "napcat":
        return "❌ 当前是官方平台模式，不启动协议端"
    instance.stop("napcat")
    return _start_snowluma()


def action_open_webui() -> str:
    """SnowLuma 无二维码落盘文件：扫码在桌面 QQ 窗口完成；此按钮直达协议端 WebUI"""
    try:
        if os.name == "nt":
            os.startfile(SNOWLUMA_WEBUI_URL)
        else:
            subprocess.Popen(["xdg-open", SNOWLUMA_WEBUI_URL])
        return ("✅ 已打开协议端 WebUI：" + SNOWLUMA_WEBUI_URL
                + f"\n（首次登录密码在其控制台日志；扫码在桌面 QQ 窗口完成，主号 {_g('bot','self_id', d='机器人号')}）")
    except Exception as e:
        return f"❌ 打开失败: {e}"


def action_send_guide(group=None) -> str:
    """一键推送：把群聊版使用说明直接发到群"""
    try:
        gid = _target_group(group)
        with io.open(GUIDE_FILE, encoding="utf-8") as f:
            guide = f.read().strip()
        if not guide:
            return "❌ 使用说明文件为空：" + GUIDE_FILE
        push = send_msg(guide, gid)
        return f"📖 使用说明发送结果（目标群 {gid}）\n{push}\n\n──── 内容预览 ────\n{guide[:200]}"
    except Exception as e:
        return f"❌ 推送使用说明失败: {e}"


def action_send_guide_to(group_id=None) -> str:
    """推送使用说明到指定群（未指定时用默认推送群）"""
    try:
        gid = _target_group(group_id)
        with io.open(GUIDE_FILE, encoding="utf-8") as f:
            guide = f.read().strip()
        push = send_msg(guide, gid)
        return f"📖 已推送使用说明到群 {gid}\n{push}"
    except Exception as e:
        return f"❌ 推送失败: {e}"


def action_push_all():
    with open(GUIDE_FILE,encoding="utf-8") as stream:
        return _push_text_all("使用说明推送",stream.read())


def _push_text_all(title,text):
    if instance.platform() != "napcat":
        return "❌ 控制台主动推送只支持已配置的协议端（SnowLuma）；官方平台能力在 AstrBot 管理"
    groups = [_target_group(g) for g in (_g("bot","groups",d=[]) or [])]
    if not groups:
        return "❌ 未配置推送目标"
    return title + "\n" + "\n".join(send_msg(text,group) for group in groups)


def action_push_all_report() -> str:
    """战报群发：生成战报推送到所有群"""
    try:
        return _push_text_all("📊 战报群发", _generate_report())
    except Exception as e:
        return f"❌ 战报生成失败：{e}"


def action_push_all_dss():
    return _push_text_all("战况推送",service.result("dss").text)


def action_push_all_campaigns():
    return _push_text_all("战况推送",service.result("campaigns").text)


def action_push_all_analysis() -> str:
    """LLM 战局分析群发（走 WS 注入拿回复后群发；注入群用当前默认群）"""
    gid = _target_group()
    r = run_sub([PYTHON, WS_INJECT, "/分析", str(gid)], timeout=180)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    reply = _extract_reply(out)
    if not reply:
        return "❌ 机器人未返回有效回复，未推送。\n\n" + out[-300:]
    return _push_text_all("🧠 战局分析群发", reply)


def action_push_all_campaign_brief():
    return _push_text_all("战况推送",service.result("briefing").text)


def action_groups() -> str:
    """返回可用群列表（供前端下拉）"""
    return json.dumps({"groups": _g("bot", "groups", d=[]) or [], "default": _default_group()}, ensure_ascii=False)


# ============================================================
# 👥 群号管理：只维护本项目 config.json 的 bot.groups（推送群 / 指令守卫范围）
# ============================================================
# AstrBot 4.29.3 起：
#   * 框架不再读取 <AstrBot data>/platform_whitelist.json，入群白名单只认
#     cmd_config.json 的 platform_settings.id_whitelist，设置入口是
#     AstrBot WebUI → 配置 → 访问控制 → 白名单 ID 列表。
#   * 本控制台因此只写本项目 config.json 的 bot.groups：HD2 指令守卫每次事件
#     实时读取（plugins/astrbot_plugin_hd2_guard/main.py），保存即生效，无需重启。
#   * 旧 platform_whitelist.json 若仍存在，只做只读探测 + 一次性迁移提示，绝不写它。
FRAMEWORK_WHITELIST_HINT = (
    "框架入群白名单请在 AstrBot WebUI → 配置 → 访问控制 → 白名单 ID 列表"
    "（platform_settings.id_whitelist）中设置；本控制台只维护本项目 config.json 的 bot.groups。"
)
_CFG_PATH = os.path.join(BASE, "config.json")


def _mode_of(path: str):
    """取文件 stat（不存在返回 None）"""
    try:
        return os.stat(path)
    except Exception:
        return None


def _read_json(path: str, default=None):
    """读 JSON，失败抛异常（带文件名，便于前端展示原因）
    注意：必须用 utf-8-sig —— AstrBot 自己写出的配置带 UTF-8 BOM。"""
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        raise RuntimeError(f"读取 {os.path.basename(path)} 失败（JSON 格式错误？）: {e}")


def _atomic_write(path: str, text: str) -> None:
    """原子写：同目录临时文件 + os.replace，避免写一半把配置写坏"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_webui_", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _backup_file(path):
    if not os.path.isfile(path):
        return ""
    import hashlib
    folder = os.path.join(TEMP,"config_backups")
    os.makedirs(folder,exist_ok=True)
    key = hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:12]
    backup = os.path.join(folder,os.path.basename(path) + ".bak_webui_" + key + "_" + str(time.time_ns()))
    shutil.copy2(path,backup)
    return backup


def _astrbot_data_dir(refresh=False):
    path = str(_g("astrbot", "data_dir", d="") or "")
    if not path:
        raise RuntimeError("请明确配置 astrbot.data_dir，不能自动选择其他实例")
    path = _cfg.resolve(path)
    config = os.path.join(path, "cmd_config.json")
    if not os.path.isdir(path):
        raise RuntimeError("指定的 AstrBot 数据目录不存在")
    return path, config, _mode_of(config)


def _push_groups():
    """本项目 config.json 的推送群列表（bot.groups），统一转字符串便于比较"""
    return [str(g) for g in (_g("bot", "groups", d=[]) or [])]


def _legacy_whitelist_path() -> str:
    """旧版 platform_whitelist.json 的路径；未配置 astrbot.data_dir 时返回空串（不猜其他实例）"""
    if not str(_g("astrbot", "data_dir", d="") or "").strip():
        return ""
    try:
        directory, _, _ = _astrbot_data_dir()
    except Exception:
        return ""
    return os.path.join(directory, "platform_whitelist.json")


def _read_legacy_whitelist() -> dict:
    """只读探测旧版 platform_whitelist.json（AstrBot 4.29.3 起框架已不再读取它）。
    返回 {"path","exists","adapter","groups","platforms","pending","error"}；
    任何情况下都不写、不删除这个文件。"""
    adapter = str(_g("bot", "platform_id", d="") or "")
    info = {"path": _legacy_whitelist_path(), "exists": False, "adapter": adapter,
            "groups": [], "platforms": [], "pending": [], "error": ""}
    if not info["path"] or not os.path.isfile(info["path"]):
        return info
    info["exists"] = True
    try:
        data = _read_json(info["path"], default={})
        if not isinstance(data, dict):
            raise RuntimeError("旧白名单不是 JSON 对象")
        info["platforms"] = sorted(str(k) for k in data)
        raw = data.get(adapter, []) if adapter else []
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise RuntimeError("旧白名单中当前平台的段不是数组")
        info["groups"] = sorted({str(g).strip() for g in raw if str(g).strip()})
    except Exception as e:
        info["error"] = str(e)
    info["pending"] = [g for g in info["groups"] if g not in _push_groups()]
    return info


def _norm_gid(raw) -> str:
    """群号规范化 + 校验：纯数字、5~12 位"""
    gid = str(raw or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{4,11}", gid):
        raise ValueError("群号需为 5~12 位数字，不能包含其他字符")
    return gid


def action_group_list():
    """群列表：只报告本项目 bot.groups + 旧白名单的只读探测结果（不再读框架白名单）"""
    legacy = _read_legacy_whitelist()
    groups = _push_groups()
    out = {"platform": instance.platform(), "groups": groups, "push_groups": groups,
           "default_group": str(_g("bot", "default_group", d="") or ""),
           "data_dir": str(_g("astrbot", "data_dir", d="") or ""),
           "config_active": _active_cfg_path(), "config_local": _local_cfg_path(),
           "source_kind": "本项目 config.json 的 bot.groups（推送群 / 指令守卫范围）",
           "framework_whitelist": FRAMEWORK_WHITELIST_HINT, "legacy": legacy}
    if instance.platform() == "qq_official":
        out["source_kind"] = "官方平台会话由 AstrBot 管理；bot.groups 仍决定本项目推送目标"
    return json.dumps(out, ensure_ascii=False)


def _active_cfg_path() -> str:
    """**进程真正使用**的 config.json 路径。
    注意：hd2_config 会优先用环境变量 HD2_PROJECT_DIR 定位项目根，
    它可能与本控制台脚本所在目录不同（本机就是这种情况：环境变量指向
    HD2-Bot，而控制台跑在 HD2-Bot-Release）。推送/群列表读的是前者，
    所以写也必须写前者，否则会出现"文件改了但列表不变"。
    """
    try:
        if _cfg:
            return os.path.abspath(_cfg.config_path())
    except Exception:
        pass
    return _CFG_PATH


def _local_cfg_path() -> str:
    """本控制台脚本所在目录的 config.json（工作区里的那份，保持同步用）"""
    return _CFG_PATH


def action_config_paths() -> str:
    """诊断用：显示两份 config.json 的实际路径"""
    act, loc = _active_cfg_path(), _local_cfg_path()
    return json.dumps({
        "active": act,
        "local": loc,
        "same": os.path.normcase(act) == os.path.normcase(loc),
        "env_project_dir": os.environ.get("HD2_PROJECT_DIR", ""),
        "active_groups": _g("bot", "groups", d=[]) or [],
    }, ensure_ascii=False)


def _write_push_groups(path: str, groups: list, default_group=None) -> dict:
    """把群列表写进指定 config.json（改前备份、原子写）"""
    cfg = _read_json(path, default=None)
    if not isinstance(cfg, dict):
        raise RuntimeError("config.json 不存在或格式异常：" + path)
    cfg.setdefault("bot", {})
    cfg["bot"]["groups"] = [int(g) for g in groups]
    if default_group is not None:
        cfg["bot"]["default_group"] = int(default_group)
    bak = _backup_file(path)
    _atomic_write(path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    return {"path": path, "backup": bak, "cfg": cfg}


def _set_push_groups(groups, default_group=None):
    path = _cfg.config_path()
    with _CONFIG_LOCK:
        updated = _write_push_groups(path, groups, default_group)
        _cfg.load_config(force=True)
    return updated["cfg"], ["✅ 已保存当前配置的推送群列表"]


def _restart_astrbot():
    instance.stop("astrbot")
    return [_start_astrbot()]


def action_group_add(raw=None) -> str:
    """添加推送群：只写本项目 config.json 的 bot.groups（不再碰 AstrBot 白名单文件）"""
    if instance.platform() != "napcat":
        return "❌ 官方平台请在 AstrBot 中管理会话与白名单"
    try:
        gid = _norm_gid(raw)
    except ValueError as e:
        return f"❌ {e}"
    push = _push_groups()
    if gid in push:
        return (f"ℹ️ 群 {gid} 已在推送群列表中（bot.groups 无变化）\n\n"
                "· " + FRAMEWORK_WHITELIST_HINT)
    groups = push + [gid]
    try:
        _, msgs = _set_push_groups(groups)
    except Exception as e:
        return f"❌ 写 config.json 失败: {e}"
    return "\n".join([
        f"👥 已添加群 {gid}", *msgs,
        f"· 推送群列表（{len(groups)} 个）：{', '.join(groups)}",
        "· 已即时生效：HD2 指令守卫每次事件实时读取 bot.groups，无需重启 AstrBot",
        "· " + FRAMEWORK_WHITELIST_HINT,
    ])


def action_group_remove(raw=None) -> str:
    """移除推送群：只改本项目 config.json 的 bot.groups（旧白名单文件保持原样）"""
    try:
        if instance.platform() != "napcat":
            raise ValueError("官方平台请在 AstrBot 中管理会话白名单")
        gid = _norm_gid(raw)
        push = _push_groups()
        if gid not in push:
            return (f"ℹ️ 群 {gid} 不在推送群列表中（bot.groups 无变化）\n\n"
                    "· " + FRAMEWORK_WHITELIST_HINT)
        groups = [g for g in push if g != gid]
        old_default = _default_group()
        default = old_default
        if str(default) == gid:
            default = int(groups[0]) if groups else 0
        _, msgs = _set_push_groups(groups, default)
        lines = [f"✅ 已移除群 {gid}", *msgs,
                 f"· 推送群列表（{len(groups)} 个）：{', '.join(groups) or '（空）'}"]
        if str(old_default) == gid:
            lines.append(f"· 原默认推送群被移除，已自动改为 {default}" if groups
                         else "· 原默认推送群被移除，且已无推送群，默认群清空")
        lines.append("· " + FRAMEWORK_WHITELIST_HINT)
        return "\n".join(lines)
    except Exception as error:
        return "❌ " + str(error)


def action_group_default(raw=None) -> str:
    """设为默认推送群（只影响推送目标，不影响是否响应）"""
    if instance.platform() != "napcat":
        return "❌ 官方平台请在 AstrBot 中管理会话与白名单"
    try:
        gid = _norm_gid(raw)
    except ValueError as e:
        return f"❌ {e}"
    push = _push_groups()
    if gid not in push:
        return f"❌ 群 {gid} 不在推送群列表里，请先「添加群」"
    try:
        _, pmsg = _set_push_groups(push, int(gid))
    except Exception as e:
        return f"❌ 写 config.json 失败: {e}"
    return ("✅ 默认推送群已设为 " + gid + "\n" + "\n".join(pmsg) +
            f"\n· 推送群列表：{', '.join(push)}\n"
            "· 提示：默认群只决定控制台里不带群号的推送目标；刷新页面即更新")


def action_group_migrate(_value=None) -> str:
    """把旧版 platform_whitelist.json 中当前平台（bot.platform_id）的群号一次性并入
    bot.groups。旧文件保持原样：不删除、不回写（AstrBot 4.29.3 起框架已不读取它）。"""
    if instance.platform() != "napcat":
        return "❌ 官方平台请在 AstrBot 中管理会话与白名单"
    legacy = _read_legacy_whitelist()
    if not legacy["exists"]:
        configured = str(_g("astrbot", "data_dir", d="") or "")
        where = legacy["path"] or (f"astrbot.data_dir={configured} 的目录不存在" if configured
                                   else "未配置 astrbot.data_dir")
        return f"ℹ️ 未发现旧版 platform_whitelist.json（{where}），无需迁移"
    if legacy["error"]:
        return f"❌ 旧白名单读取失败：{legacy['error']}"
    if not legacy["adapter"]:
        return "❌ 请先配置 bot.platform_id，迁移需要确认旧文件里哪一段属于当前平台"
    valid = [g for g in legacy["groups"] if re.fullmatch(r"[1-9][0-9]{4,11}", g)]
    skipped = [g for g in legacy["groups"] if g not in valid]
    push = _push_groups()
    added = [g for g in valid if g not in push]
    merged = push + added
    lines = [f"📥 旧名单迁移（平台 {legacy['adapter']}）：{legacy['path']}",
             f"· 旧名单群号 {len(valid)} 个，其中 {len(added)} 个将并入本项目 bot.groups"]
    if skipped:
        lines.append("· 跳过非数字群号：" + ", ".join(skipped))
    if not added:
        lines.append("ℹ️ 旧名单中的群号都已在本项目 bot.groups 中，未修改配置")
        lines.append("· 旧文件保持原样，可自行删除（AstrBot 4.29.3 起框架不再读取它）")
        return "\n".join(lines)
    try:
        _, msgs = _set_push_groups(merged)
    except Exception as e:
        return "❌ 写 config.json 失败: " + str(e)
    lines += msgs
    lines.append(f"· 迁移后推送群列表（{len(merged)} 个）：{', '.join(merged)}")
    lines.append("· 旧文件保持原样（未删除、未回写），确认无误后可自行归档删除")
    lines.append("· " + FRAMEWORK_WHITELIST_HINT)
    return "\n".join(lines)


def action_restart_astrbot() -> str:
    """手动重启 AstrBot（排障用；修改 bot.groups 本身不需要重启）"""
    lines = ["🔄 重启 AstrBot："]
    lines += _restart_astrbot()
    lines.append("· 当前推送群列表（bot.groups）：" + (", ".join(_push_groups()) or "（空）"))
    lines.append("· 修改 bot.groups 不需要重启 AstrBot（指令守卫每次事件实时读取配置）")
    return "\n".join(lines)


# 指令流水现在写在 AstrBot 插件数据目录（data/plugin_data/...）；旧版 temp/ 文件仅作兼容读取。
LOG_FILE = _diagnostics.LOG_FILE
LEGACY_LOG_FILE = os.path.join(BASE, "temp", "指令日志.jsonl")


def _log_candidates():
    return [LOG_FILE, LEGACY_LOG_FILE]


def action_logs() -> str:
    """读取指令执行流水（最近 200 条）"""
    try:
        rows, target = [], next((p for p in _log_candidates() if os.path.exists(p)), LOG_FILE)
        if os.path.exists(target):
            with io.open(target, encoding="utf-8") as f:
                for line in f.readlines()[-200:]:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            pass
        return json.dumps({"logs": rows, "path": target}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"logs": [], "error": str(e)}, ensure_ascii=False)

def action_restart_webui():
    helper = os.path.join(_ROOT,"scripts","restart_webui.py")
    subprocess.Popen([sys.executable,helper,"--parent-pid",str(os.getpid())],creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),env=dict(os.environ,HD2_PROJECT_DIR=_ROOT))
    return "ℹ️ 已请求重启当前控制台，完成后请刷新页面"



def run_sub(args, timeout=180):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["HD2_PROJECT_DIR"] = _ROOT
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env, cwd=HD2)
    return r


def send_msg(text, group_id=None):
    gid = _target_group(group_id)
    fd, path = tempfile.mkstemp(prefix="send_",suffix=".txt",dir=TEMP)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            stream.write(text)
        hd2_log("submitted",plugin="console",group=gid,platform="napcat",detail="提交发送请求")
        try:
            run = run_sub([PYTHON,SEND_SCRIPT,path,str(gid)],timeout=60)
        except subprocess.TimeoutExpired:
            hd2_log("unknown",plugin="console",group=gid,platform="napcat",detail="发送超时，不能确认平台是否接收")
            return "⚠️ 发送结果不明，请先核对群消息，不要直接重复发送"
        ok = run.returncode == 0 and "RESULT: OK" in run.stdout
        hd2_log("acknowledged" if ok else "failed",plugin="console",group=gid,platform="napcat",detail="平台API已确认" if ok else "未取得成功回执")
        return f"✅ 平台已确认群 {gid} 的发送请求" if ok else "❌ 未取得发送成功回执：" + (run.stdout or run.stderr)[-300:]
    finally:
        os.unlink(path)


def _generate_report() -> str:
    """仅使用本次成功生成的产物，不读取历史战报文件。"""
    with tempfile.TemporaryDirectory(prefix="report_", dir=TEMP) as work:
        output = os.path.join(work, "report.txt")
        r = run_sub([PYTHON, GEN_REPORT, "--output", output], timeout=180)
        if r.returncode != 0 or not os.path.isfile(output):
            raise RuntimeError((r.stderr or r.stdout or "未生成战报")[-500:])
        with open(output, encoding="utf-8") as f:
            report = f.read().strip()
        if not report:
            raise RuntimeError("生成结果为空")
        return report


def action_report(group=None) -> str:
    """生成战报并推送（LLM 翻译，约 30-60 秒）"""
    gid = _target_group(group)
    try:
        report = _generate_report()
        lines = [f"📊 战报已生成（{len(report)} 字符），目标群 {gid}，推送结果："]
        lines.append(send_msg(report, gid))
        lines.append("")
        lines.append("──── 战报内容 ────")
        lines.append(report)
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 战报生成失败：{e}"


def action_roll(group=None,mode="assessment"):
    gid = _target_group(group)
    from astrbot_plugin_hd2_roll.loadout import draw, format_result
    result = draw(mode)
    text = format_result(result)
    return text + "\n\n" + send_msg(text,gid)


def action_planet(name, group=None):
    gid = _target_group(group)
    view = service.result("planet",name)
    return view.text + "\n\n" + send_msg(view.text,gid)


def action_variant(name: str, group=None) -> str:
    import asyncio
    try:
        gid = _target_group(group)
        for p in APP_PATHS:
            sys.path.insert(0, p)
        variant_mod = _load_plugin(PLUGIN_VARIANT)

        async def run():
            matched = None
            for kw, cn in variant_mod.VARIANT_CN.items():
                if name.strip() == cn or name.strip().upper() == kw:
                    matched = kw
                    break
            if not matched:
                if "变种" in name or "变异" in name:
                    return "当前没有该变种的活动战役。"
                return "⚠️ 未识别该变种名（如：孢裂变种/掠食变种/无脑群氓/生化人/蟑龙/霸王虫/炽灼部队/占领者/喷气旅）"
            scan = await variant_mod._scan_all_variants()
            planets = scan.get(matched, [])
            cn_name = variant_mod.VARIANT_CN[matched]
            if not planets:
                return "当前没有该变种的活动战役。"
            cn_map = variant_mod._load_planet_cn_map()
            lines = ["目前的变种信息："]
            for p in planets:
                p_cn = cn_map.get(p.upper(), p)
                lines.append(f"{p_cn}——{cn_name}")
            return "\n".join(lines)

        result = asyncio.run(run())
        return f"🧬 变种查询（目标群 {gid}）\n" + result + "\n\n推送结果：" + send_msg(result, gid)
    except Exception as e:
        return f"❌ 变种查询失败: {e}"


def action_custom(text: str, group=None) -> str:
    gid = _target_group(group)
    return f"📢 自定义消息（目标群 {gid}）\n" + text + "\n\n推送结果：" + send_msg(text, gid)


def action_inject(cmd: str, group=None) -> str:
    """WS 注入测试：伪装群成员发指令，显示机器人回复（不推送到群）"""
    gid = _target_group(group)
    r = run_sub([PYTHON, WS_INJECT, cmd, str(gid)], timeout=90)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    return f"🧪 注入指令: {cmd}（模拟群 {gid}）\n\n" + out


def _extract_reply(out: str) -> str:
    """从 ws_inject 输出中提取机器人回复正文（找不到返回空串）"""
    m = re.search(r"✅ 机器人回复了.*?-----\s*\n(.*?)\n={5,}", out, re.S)
    if m:
        return m.group(1).strip()
    if "----- 回复 1 -----" in out:
        return out.split("----- 回复 1 -----", 1)[1].strip().rstrip("=").strip()
    return ""


def action_inject_push(cmd: str, group=None) -> str:
    """WS 注入 + 推送：拿到机器人回复后直接推送到目标群"""
    gid = _target_group(group)
    r = run_sub([PYTHON, WS_INJECT, cmd, str(gid)], timeout=90)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    reply = _extract_reply(out)
    if not reply:
        return "❌ 机器人未返回有效回复，未推送。\n\n" + out[-500:]
    push = send_msg(reply, gid)
    return f"📤 注入指令: {cmd}（模拟群 {gid}）\n\n机器人模拟回复的推送结果（群 {gid}）\n{push}\n\n──── 回复内容 ────\n{reply}"


ACTIONS = {
    "boot_all": action_boot_all,
    "status": action_status,
    "restart_snowluma": action_restart_snowluma,
    "open_webui": action_open_webui,
    "restart_webui": action_restart_webui,
    "send_guide": action_send_guide,
    "send_guide_to": action_send_guide_to,
    "push_all": action_push_all,
    "push_all_report": action_push_all_report,
    "push_all_dss": action_push_all_dss,
    "push_all_campaigns": action_push_all_campaigns,
    "push_all_campaign_brief": action_push_all_campaign_brief,
    "push_all_analysis": action_push_all_analysis,
    "groups": action_groups,
    "group_list": action_group_list,
    "group_add": action_group_add,
    "group_remove": action_group_remove,
    "group_default": action_group_default,
    "group_migrate": action_group_migrate,
    "restart_astrbot": action_restart_astrbot,
    "logs": action_logs,
    "report": action_report,
    "roll": action_roll,
    "planet": action_planet,
    "variant": action_variant,
    "custom": action_custom,
    "inject": action_inject,
    "inject_push": action_inject_push,
}

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HD2 真理部控制台</title>
<style>
  :root {
    --bg1: #0b0e14; --bg2: #12161f; --card: #1a202c; --card2: #202838;
    --text: #e8e6e3; --muted: #9aa3b2; --accent: #ffd84d; --accent2: #4da6ff;
    --ok: #4ade80; --err: #f87171; --border: #2c3444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    background: radial-gradient(1200px 600px at 80% -10%, #1d2b4a 0%, var(--bg1) 55%), var(--bg1);
    color: var(--text); min-height: 100vh; padding: 24px;
  }
  header { display: flex; align-items: center; gap: 14px; margin-bottom: 22px; flex-wrap: wrap; }
  header h1 { font-size: 22px; letter-spacing: 1px; }
  header .tag {
    font-size: 12px; color: var(--bg1); background: var(--accent);
    padding: 3px 10px; border-radius: 20px; font-weight: 600;
  }
  header .status { font-size: 12px; color: var(--ok); margin-left: auto; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
  .card {
    background: linear-gradient(160deg, var(--card2), var(--card));
    border: 1px solid var(--border); border-radius: 14px; padding: 18px;
  }
  .card h3 { font-size: 15px; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
  .card p.desc { font-size: 12px; color: var(--muted); margin-bottom: 12px; line-height: 1.6; }
  input[type=text] {
    width: 100%; background: #0f141d; border: 1px solid var(--border); color: var(--text);
    border-radius: 8px; padding: 9px 12px; font-size: 14px; margin-bottom: 10px; outline: none;
  }
  input[type=text]:focus { border-color: var(--accent2); }
  .btn-row { display: flex; gap: 8px; flex-wrap: wrap; }
  button {
    flex: 1; min-width: 90px; background: var(--accent); color: #141414; border: none;
    border-radius: 8px; padding: 10px 12px; font-size: 14px; font-weight: 600; cursor: pointer;
    transition: transform .08s, filter .15s;
  }
  button:hover { filter: brightness(1.08); }
  button:active { transform: scale(.97); }
  button:disabled { opacity: .45; cursor: wait; }
  button.sec { background: var(--accent2); color: #0d1420; }
  button.danger { background: var(--err); color: #1a0a0a; }
  .output {
    margin-top: 18px; background: #0d1117; border: 1px solid var(--border);
    border-radius: 12px; padding: 16px; white-space: pre-wrap; word-break: break-word;
    font-family: Consolas, "Microsoft YaHei", monospace; font-size: 13px; line-height: 1.7;
    max-height: 480px; overflow-y: auto; display: none;
  }
  .output.show { display: block; }
  .output .head { color: var(--accent); font-weight: 700; margin-bottom: 6px; }
  .hint { font-size: 11px; color: var(--muted); margin-top: 8px; line-height: 1.5; }
  .mini { flex: 0 0 auto; min-width: 0; padding: 4px 10px; font-size: 12px; }
  .log-tools { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; font-size: 12px; color: var(--muted); }
  .log-tools input { width: 200px; margin: 0; }
  .log-table-wrap { max-height: 380px; overflow-y: auto; border: 1px solid var(--border); border-radius: 10px; }
  .log-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .log-table th { position: sticky; top: 0; background: #161c28; color: var(--muted); text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
  .log-table td { padding: 7px 10px; border-bottom: 1px solid #232b3b; }
  .log-table tr:hover td { background: #1c2433; }
  .log-detail-cell { background: #0d1320 !important; color: #b8c4d8; white-space: pre-wrap; font-size: 12px; line-height: 1.8; }
  .modal { position: fixed; inset: 0; background: rgba(5,8,14,.78); display: none; align-items: center;
           justify-content: center; padding: 24px; z-index: 99; }
  .modal.show { display: flex; }
  .modal-box { background: linear-gradient(160deg, var(--card2), var(--card)); border: 1px solid var(--border);
               border-radius: 14px; padding: 20px; width: min(720px, 96vw); max-height: 88vh; overflow-y: auto; }
  .modal-box h3 { font-size: 16px; margin-bottom: 14px; }
  .grp-src { font-size: 12px; color: var(--muted); line-height: 1.8; margin-bottom: 12px; word-break: break-all; }
  .grp-migrate { display: flex; align-items: center; gap: 10px; justify-content: space-between;
                 background: rgba(255,190,90,.08); border: 1px solid rgba(255,190,90,.35);
                 border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; font-size: 12px;
                 color: #e8c98a; line-height: 1.7; }
  .grp-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 16px; }
  .grp-chip { background: #0f141d; border: 1px solid var(--border); border-radius: 20px; padding: 5px 12px;
              font-size: 13px; display: inline-flex; align-items: center; gap: 8px; }
  .grp-chip b { color: var(--accent); }
  .grp-chip span { cursor: pointer; color: var(--err); font-weight: 700; }
  .grp-status { margin-top: 14px; background: #0d1117; border: 1px solid var(--border); border-radius: 10px;
                padding: 12px; white-space: pre-wrap; font-family: Consolas, "Microsoft YaHei", monospace;
                font-size: 12px; line-height: 1.7; color: #b8c4d8; display: none; max-height: 260px; overflow-y: auto; }
  .grp-status.show { display: block; }
  .grp-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
</style>
</head>
<body>
<header>
  <h1>🦅 真理部自助控制台</h1>
  <span class="tag">绝地潜兵2 · HD2</span>
  <span class="status" id="status">● 服务运行中</span>
</header>

<div class="grid">
  <div class="card">
    <h3>🛠️ 一键启动部署</h3>
    <p class="desc">启动 AstrBot + SnowLuma（自动注入 QQ），在 QQ 窗口扫码登录，验证全链路（机器人上线）</p>
    <div class="btn-row">
      <button onclick="run('boot_all')">🚀 一键启动</button>
      <button class="sec" onclick="run('open_webui')">🌐 协议端WebUI</button>
      <button class="sec" onclick="run('restart_snowluma')">🔄 重启SnowLuma</button>
      <button class="sec" onclick="run('restart_astrbot')">🔄 重启AstrBot</button>
      <button class="sec" onclick="run('restart_webui')">🔁 重启WebUI</button>
      <button onclick="run('status')">🔍 状态</button>
    </div>
    <div class="hint">一键启动约 30~60 秒；「重启 SnowLuma」用于 QQ 掉线后重新注入（会先关掉旧协议端进程）；「重启 WebUI」用于本页面卡死/服务被杀后自愈</div>
  </div>

  <div class="card">
    <h3>🚀 一键发送</h3>
    <p class="desc">点击即生成并推送；推送目标 = 下方所选的群</p>
    <div class="btn-row" style="margin-bottom:10px">
      <span style="align-self:center;font-size:12px;color:var(--muted)">推送目标</span>
      <select id="targetGroup" onchange="updateTargetHint()" style="flex:1;background:#0f141d;border:1px solid var(--border);color:var(--text);border-radius:8px;padding:8px;font-size:13px">
        <option value="">加载中...</option>
      </select>
    </div>
    <div class="btn-row">
      <button onclick="run('inject_push','/战报',{group:targetGroup()})">📊 战报</button>
      <button class="sec" onclick="run('inject_push','/dss',{group:targetGroup()})">🛰️ DSS</button>
      <button class="sec" onclick="run('inject_push','/战线',{group:targetGroup()})">⚔️ 战线</button>
      <button class="sec" onclick="run('inject_push','/战役',{group:targetGroup()})">🎬 战役</button>
      <button onclick="run('inject_push','/分析',{group:targetGroup()})">🧠 分析</button>
      <button class="sec" onclick="run('inject_push','/查表',{group:targetGroup()})">📚 查表</button>
      <button class="sec" onclick="run('inject_push','/roll',{group:targetGroup()})">🎲 Roll</button>
      <button class="danger" onclick="run('send_guide',null,{group:targetGroup()})">📖 使用说明</button>
    </div>
    <div class="btn-row" style="margin-top:10px">
      <button class="sec" onclick="run('push_all')">🌐 推送到所有群（使用说明）</button>
    </div>
    <div class="hint" id="targetHint">所选群对上方<b>所有</b>发送按钮生效（战报/DSS/战线/战役/分析/查表/Roll/使用说明）；「推送到所有群」与下方「一键群发」无视选择，固定发给 <code>bot.groups</code> 里的全部群。分析/战报约 30~60 秒，其余约 5~15 秒</div>
  </div>

  <div class="card">
    <h3>📤 一键群发（所有推送群）</h3>
    <p class="desc">生成内容并推送到全部群（<span class="js-default-group">{GROUP}</span> 等 <code>bot.groups</code> 内所有群）</p>
    <div class="btn-row">
      <button onclick="run('push_all_report')">📊 战报</button>
      <button class="sec" onclick="run('push_all_campaigns')">⚔️ 战线</button>
      <button class="sec" onclick="run('push_all_campaign_brief')">🎬 战役</button>
      <button class="sec" onclick="run('push_all_dss')">🛰️ DSS</button>
      <button onclick="run('push_all_analysis')">🧠 分析</button>
      <button class="danger" onclick="run('push_all')">📖 使用说明</button>
    </div>
    <div class="hint">战报/分析约 1~3 分钟；战役/DSS/说明约 10~30 秒</div>
  </div>

  <div class="card">
    <h3>📊 推送战报</h3>
    <p class="desc">生成最新战况（重要指令 + 目标星球 + 战区分布 + 资讯），推送到所选的群</p>
    <div class="btn-row"><button onclick="run('report',null,{group:targetGroup()})">生成并推送</button></div>
  </div>

  <div class="card">
    <h3>🎲 推送随机战备</h3>
    <p class="desc">从全类型战备池随机抽取 4 个，推送到所选的群</p>
    <div class="btn-row"><button class="sec" onclick="run('roll',null,{group:targetGroup()})">抽取配装并推送</button><button class="sec" onclick="run('roll',null,{group:targetGroup(),mode:'pure'})">纯随机配装并推送</button></div>
  </div>

  <div class="card">
    <h3>🪐 星球信息</h3>
    <p class="desc">抓取星球页面：抵抗度 / 行动变量 / POI（群内指令：/星球 &lt;星球名&gt;，中英文均可）</p>
    <input type="text" id="planet" placeholder="如：奥密克戎 或 OMICRON">
    <div class="btn-row"><button class="sec" onclick="run('planet', document.getElementById('planet').value, {group:targetGroup()})">查询并推送</button></div>
  </div>

  <div class="card">
    <h3>🧬 变种查询</h3>
    <p class="desc">扫描战役星球，输出存在该变种的所有星球</p>
    <input type="text" id="variant" placeholder="如：孢裂变种 / 喷气旅">
    <div class="btn-row"><button class="sec" onclick="run('variant', document.getElementById('variant').value, {group:targetGroup()})">查询并推送</button></div>
  </div>

  <div class="card">
    <h3>📢 自定义消息</h3>
    <p class="desc">直接推送一段文本到所选的群</p>
    <input type="text" id="custom" placeholder="输入要推送的内容...">
    <div class="btn-row"><button onclick="run('custom', document.getElementById('custom').value, {group:targetGroup()})">推送</button></div>
  </div>

  <div class="card">
    <h3>👥 群号管理（推送群列表）</h3>
    <p class="desc">维护本项目 <code>config.json</code> 的 <code>bot.groups</code>：HD2 指令守卫的响应范围与推送目标</p>
    <div class="btn-row">
      <button onclick="openGroupDialog()">👥 打开群管理</button>
      <button class="sec" onclick="run('group_list')">🔍 查看当前群列表</button>
    </div>
    <div class="hint">只写本项目 <code>config.json</code>，保存即生效（指令守卫每次事件实时读取）。<b>框架入群白名单</b>不在这里：请到 <b>AstrBot WebUI → 配置 → 访问控制 → 白名单 ID 列表</b>（<code>platform_settings.id_whitelist</code>）设置；AstrBot 4.29.3 起不再读取 <code>platform_whitelist.json</code>。</div>
  </div>

  <div class="card">
    <h3>🧪 查询推送 / 响应测试</h3>
    <p class="desc">输入指令（查表/战报/星球等），「测试并推送」= 注入拿到机器人回复后直接推送到所选的群；「仅测试」= 只看回复不发群</p>
    <input type="text" id="inject" value="/帮助" placeholder="/战报  /帮助  /查询 X-45  /变种 名称  /roll">
    <div class="btn-row">
      <button class="danger" onclick="run('inject_push', document.getElementById('inject').value, {group:targetGroup()})">📤 测试并推送</button>
      <button class="sec" onclick="run('inject', document.getElementById('inject').value, {group:targetGroup()})">🧪 仅测试</button>
      <button class="sec" onclick="document.getElementById('inject').value='/查表'">/查表</button>
      <button class="sec" onclick="document.getElementById('inject').value='/查表 星区 巴纳德'">星区</button>
      <button class="sec" onclick="document.getElementById('inject').value='/查表 孢裂变种'">参数</button>
      <button class="sec" onclick="document.getElementById('inject').value='/dss'">DSS</button>
      <button class="sec" onclick="document.getElementById('inject').value='/战役'">战役</button>
      <button class="sec" onclick="document.getElementById('inject').value='/分析'">分析</button>
    </div>
    <div class="hint">快捷按钮仅填充输入框；「测试并推送」会把机器人回复发到<b>上方所选的群</b>（注入也模拟该群）</div>
  </div>

  <div class="card" style="grid-column: 1 / -1;">
    <h3>📋 指令执行流水</h3>
    <p class="desc">实时监控指令接收状态与执行步骤（3 秒自动刷新）</p>
    <div class="log-tools">
      <span>筛选：</span>
      <button class="mini" onclick="setLogFilter('all')">全部</button>
      <button class="mini" onclick="setLogFilter('blocked')">已拦截</button>
      <button class="mini" onclick="setLogFilter('matched')">处理中</button>
      <button class="mini" onclick="setLogFilter('done')">完成</button>
      <button class="mini" onclick="setLogFilter('failed')">失败</button>
      <input type="text" id="logSearch" placeholder="搜索指令/群号/昵称..." oninput="renderLogs()">
    </div>
    <div class="log-table-wrap">
      <table class="log-table">
        <thead><tr><th>时间</th><th>群</th><th>用户</th><th>指令</th><th>状态</th><th>插件</th><th>耗时</th><th></th></tr></thead>
        <tbody id="logBody"><tr><td colspan="8" style="color:var(--muted);text-align:center">加载中...</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<div class="output" id="output"><span class="head" id="output-head"></span><span id="output-body"></span></div>

<div class="modal" id="groupModal">
  <div class="modal-box">
    <h3>👥 群号管理（推送群列表）</h3>
    <div class="grp-src" id="groupSrc">加载中...</div>
    <div class="grp-migrate" id="groupMigrate" style="display:none">
      <div id="groupMigrateText"></div>
      <button class="sec" onclick="groupMigrate()">📥 导入旧名单</button>
    </div>
    <div class="grp-list" id="groupChips"></div>
    <div class="grp-row">
      <input type="text" id="groupInput" placeholder="群号，如 123456789（纯数字）" style="flex:1;margin:0">
      <button onclick="groupAct('group_add')">➕ 添加</button>
      <button class="danger" onclick="groupAct('group_remove')">➖ 移除</button>
      <button class="sec" onclick="groupAct('group_default')">⭐ 设为默认推送群</button>
    </div>
    <div class="grp-row">
      <button class="sec" onclick="groupRefresh()">🔍 刷新</button>
      <button class="danger" onclick="closeGroupDialog()">✖ 关闭</button>
    </div>
    <div class="grp-status" id="groupStatus"></div>
    <div class="hint">「添加 / 移除 / 设为默认」只改本项目 <code>config.json</code> 的 <code>bot.groups</code>（改前自动备份），保存即生效。<br><b>框架入群白名单</b>请在 <b>AstrBot WebUI → 配置 → 访问控制 → 白名单 ID 列表</b>（<code>platform_settings.id_whitelist</code>）中设置；AstrBot 4.29.3 起不再读取 <code>platform_whitelist.json</code>，本控制台也不再写它。</div>
  </div>
</div>

<script>
let busy = false;
function setStatus(s) { document.getElementById('status').textContent = s; }
/** 当前"推送目标"下拉框选中的群号（空串表示未选/未加载） */
function targetGroup() {
  const el = document.getElementById('targetGroup');
  return el ? (el.value || '') : '';
}
async function run(action, value, extra) {
  if (busy) return;
  const body = { action };
  if (value !== undefined) body.value = value;
  if (extra && typeof extra === 'object') Object.assign(body, extra);
  const out = document.getElementById('output');
  const head = document.getElementById('output-head');
  const b = document.getElementById('output-body');
  out.classList.add('show');
  head.textContent = '⏳ 执行中，请稍候（战报/星球/变种可能需要 10~60 秒）...';
  b.textContent = '';
  busy = true; setStatus('⏳ 处理中...');
  const btns = document.querySelectorAll('button');
  btns.forEach(x => x.disabled = true);
  try {
    const resp = await fetch('/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-HD2-CSRF': '{CSRF_TOKEN}' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    head.textContent = data.ok ? '✅ 完成' : '❌ 出错';
    b.textContent = data.output || '(无输出)';
  } catch (e) {
    head.textContent = '❌ 网络错误';
    b.textContent = String(e);
  } finally {
    busy = false; setStatus('● 服务运行中');
    btns.forEach(x => x.disabled = false);
    out.scrollTop = out.scrollHeight;
  }
}
let allLogs = [], logFilter = 'all';
async function loadGroups() {
  try {
    const r = await fetch('/api/groups');
    const d = await r.json();
    const sel = document.getElementById('targetGroup');
    if (!sel) return;
    const list = d.groups || [];
    sel.replaceChildren(...list.map(g => new Option('群 '+g+(String(g)===String(d.default)?'（默认）':''),String(g))));
    if (!list.length) sel.innerHTML = '<option value="">未配置群</option>';
    // 用接口返回的真实默认群刷新页面文案（页面初始值是服务端渲染的，改过默认群后需要更新）
    // 避免"设为默认推送群"后界面仍显示旧群号
    document.querySelectorAll('.js-default-group').forEach(el => { el.textContent = d.default; });
    updateTargetHint();
  } catch (e) {}
}
/** 把「当前推送目标」显示在下方提示里，避免选错群 */
function updateTargetHint() {
  const el = document.getElementById('targetHint');
  if (!el) return;
  const g = targetGroup();
  if (!g) return;
  if (el.dataset.base === undefined) el.dataset.base = el.innerHTML;
  el.innerHTML = `🎯 当前推送目标：<b style="color:var(--accent)">群 ${escapeHtml(g)}</b>　`
    + `<a href="#" onclick="event.preventDefault();openGroupDialog()" style="color:var(--accent2)">管理群号</a><br>`
    + el.dataset.base;
}
loadGroups();
async function refreshLogs() {
  try {
    const r = await fetch('/api/logs');
    const d = await r.json();
    if (d.logs) { allLogs = d.logs; renderLogs(); }
  } catch (e) {}
}
function statusBadge(stage) {
  const m = {generated:['已生成（未确认收发）','#4ade80'], degraded:['降级输出','#fbbf24'], submitted:['已提交发送','#fbbf24'], acknowledged:['平台已确认','#4ade80'], unknown:['发送结果不明','#fbbf24'],received:['已接收','#4da6ff'], blocked:['已拦截','#f87171'], matched:['处理中','#fbbf24'], done:['完成','#4ade80'], failed:['失败','#f87171'], ignored:['忽略','#9aa3b2']};
  const [t,c] = m[stage] || [stage,'#9aa3b2'];
  return `<span style="color:${c};font-weight:600">${escapeHtml(t)}</span>`;
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function logDetail(l) {
  const s = [];
  if (l.request_id) s.push('请求：'+l.request_id);
  if (l.platform) s.push('平台：'+l.platform);
  if (l.source_time) s.push('数据时间：'+l.source_time+(l.stale?'（陈旧）':''));
  if (l.source) s.push('来源：'+l.source);
  if (l.data_version) s.push('数据版本：'+l.data_version);
  if (l.output_mode) s.push('输出：'+l.output_mode);
  if (['generated','degraded','acknowledged','unknown','submitted'].includes(l.stage)) s.push(l.detail||'');
  if (l.stage==='blocked') s.push(`⛔ 白名单拦截：${l.detail||''}`);
  if (l.stage==='matched') s.push(`🔍 命中插件 ${l.plugin}，开始执行`);
  if (l.stage==='done') s.push(`✅ 执行完成${l.cost!=null?'（'+l.cost+'s）':''}${l.detail?'：'+l.detail:''}`);
  if (l.stage==='failed') s.push(`❌ 执行失败：${l.detail||''}`);
  if (l.stage==='received') s.push(`📥 已接收消息${l.wake?'（唤醒）':''}`);
  return s.join('\n');
}
function renderLogs() {
  const q = (document.getElementById('logSearch')?.value||'').toLowerCase();
  const rows = allLogs.filter(l => (logFilter==='all' || l.stage===logFilter) && (!q || (l.text||'').toLowerCase().includes(q) || String(l.group||'').includes(q) || (l.name||'').toLowerCase().includes(q)));
  const body = document.getElementById('logBody');
  if (!body) return;
  if (!rows.length) { body.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center">暂无记录</td></tr>'; return; }
  body.innerHTML = rows.map(l => `<tr>
    <td>${escapeHtml(l.time||'')}</td><td>${escapeHtml(l.group||'-')}</td><td>${escapeHtml(l.name||l.user||'-')}</td>
    <td title="${escapeHtml(l.text||'')}">${escapeHtml((l.text||'').slice(0,24))}</td>
    <td>${statusBadge(l.stage)}</td><td>${escapeHtml(l.plugin||'-')}</td><td>${escapeHtml(l.cost!=null?l.cost+'s':'')}</td>
    <td><button class="mini" onclick="toggleLogDetail(this,'${Number(l.ts)||0}')">▼</button></td></tr>
    <tr id="d-${Number(l.ts)||0}" style="display:none"><td colspan="8" class="log-detail-cell">${escapeHtml(logDetail(l))}</td></tr>`).join('');
}
function toggleLogDetail(btn, ts){ const el=document.getElementById('d-'+ts); const on=el.style.display==='none'; el.style.display=on?'':'none'; btn.textContent=on?'▲':'▼'; }
function setLogFilter(f){ logFilter=f; renderLogs(); }
setInterval(refreshLogs, 3000);
refreshLogs();

// ===== 👥 群号管理 =====
async function callApi(action, value) {
  const body = { action };
  if (value !== undefined) body.value = value;
  const r = await fetch('/api/action', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-HD2-CSRF': '{CSRF_TOKEN}' }, body: JSON.stringify(body),
  });
  return await r.json();
}
function gsSet(text) {
  const el = document.getElementById('groupStatus');
  el.classList.add('show');
  el.textContent = text;
}
function openGroupDialog() {
  document.getElementById('groupModal').classList.add('show');
  document.getElementById('groupStatus').classList.remove('show');
  groupRefresh();
}
function closeGroupDialog() { document.getElementById('groupModal').classList.remove('show'); }
async function groupRefresh() {
  const src = document.getElementById('groupSrc'), chips = document.getElementById('groupChips');
  const mg = document.getElementById('groupMigrate'), mgText = document.getElementById('groupMigrateText');
  src.textContent = '⏳ 读取中...'; chips.innerHTML = ''; mg.style.display = 'none';
  try {
    const d = await callApi('group_list');
    const info = JSON.parse(d.output);
    const groups = (info.groups || info.push_groups || []).map(String);
    src.innerHTML = '群号来源：<b>' + escapeHtml(info.source_kind || 'bot.groups') + '</b>'
      + '<br>推送配置：<code>' + escapeHtml(info.config_active || '-') + '</code>'
      + ((info.config_local && info.config_local !== info.config_active)
          ? '<br>同目录副本：<code>' + escapeHtml(info.config_local) + '</code>' : '')
      + '<br>推送群列表（' + groups.length + ' 个）　默认推送群：<b>' + escapeHtml(info.default_group || '-') + '</b>'
      + '<br><span class="hint">' + escapeHtml(info.framework_whitelist || '') + '</span>';
    chips.innerHTML = groups.length
      ? groups.map(g => `<span class="grp-chip"><b>${escapeHtml(g)}</b>${String(g)===String(info.default_group)?'⭐':''}<span title="移除此群" data-group="${escapeHtml(g)}" onclick="groupAct('group_remove',this.dataset.group)">✖</span></span>`).join('')
      : '<span class="hint">推送群列表为空：HD2 指令守卫将不限制群；推送前请先添加群</span>';
    const leg = info.legacy || {};
    if (leg.exists) {
      const pending = (leg.pending || []).map(String);
      let t = '⚠️ 检测到旧版 <code>platform_whitelist.json</code>（' + escapeHtml(leg.path || '-') + '），AstrBot 4.29.3 起框架已不再读取它。';
      if (leg.error) {
        t += ' 读取失败：' + escapeHtml(leg.error);
      } else if (!leg.adapter) {
        t += ' 请先配置 <code>bot.platform_id</code>，才能按当前平台导入旧名单。';
      } else {
        t += ' 当前平台（' + escapeHtml(leg.adapter) + '）旧名单 ' + (leg.groups || []).length + ' 个'
          + (pending.length ? '，其中 ' + pending.length + ' 个尚未进入本项目 <code>bot.groups</code>。' : '，已全部在本项目 <code>bot.groups</code> 中。');
      }
      mgText.innerHTML = t;
      mg.querySelector('button').style.display = (leg.adapter && !leg.error && pending.length) ? '' : 'none';
      mg.style.display = 'flex';
    }
  } catch (e) {
    src.textContent = '❌ 读取失败: ' + e;
  }
}
async function groupMigrate() {
  if (!confirm('把旧版 platform_whitelist.json 中当前平台的群号并入本项目 bot.groups？\n（旧文件保持原样，不删除；框架已不再读取它）')) return;
  gsSet('⏳ 正在导入旧名单...');
  const btns = document.querySelectorAll('.modal-box button');
  btns.forEach(b => b.disabled = true);
  try {
    const d = await callApi('group_migrate');
    gsSet((d.ok ? '' : '❌ 出错\n') + (d.output || '(无输出)'));
  } catch (e) {
    gsSet('❌ 网络错误: ' + e);
  } finally {
    btns.forEach(b => b.disabled = false);
    groupRefresh();
    loadGroups();
  }
}
async function groupAct(action, preset) {
  const input = document.getElementById('groupInput');
  const gid = (preset !== undefined && preset !== null) ? String(preset) : (input.value || '').trim();
  if (!gid) { gsSet('❌ 请先填群号'); return; }
  if (action === 'group_remove' && !confirm('确认从推送群列表移除群 ' + gid + '？\n（只改本项目 config.json 的 bot.groups，不影响 AstrBot 框架白名单）')) return;
  gsSet('⏳ 执行中...（只写本项目 config.json，通常立即完成）');
  const btns = document.querySelectorAll('.modal-box button');
  btns.forEach(b => b.disabled = true);
  try {
    const d = await callApi(action, gid);
    gsSet((d.ok ? '' : '❌ 出错\n') + (d.output || '(无输出)'));
    input.value = '';
  } catch (e) {
    gsSet('❌ 网络错误: ' + e);
  } finally {
    btns.forEach(b => b.disabled = false);
    groupRefresh();
    // 默认群/推送列表可能变了：立刻刷新下拉框与页面上的群号文案，免手动刷新页面
    if (action !== 'group_list') loadGroups();
  }
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeGroupDialog(); });
document.getElementById('groupModal').addEventListener('click', e => {
  if (e.target.id === 'groupModal') closeGroupDialog();
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _trusted_request(self, mutation=False):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        if host not in hosts or (origin is not None and origin != "http://" + host):
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        if mutation:
            token = self.headers.get("X-HD2-CSRF", "")
            return (self.headers.get_content_type() == "application/json"
                    and secrets.compare_digest(token, CSRF_TOKEN))
        return True

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._trusted_request():
            self._send(403, '{"ok":false,"output":"请求来源不可信"}')
            return
        if self.path == "/" or self.path == "/index.html":
            page = PAGE.replace("{GROUP}", str(_default_group())).replace("{CSRF_TOKEN}", CSRF_TOKEN)
            self._send(200, page, "text/html")
        elif self.path == "/api/logs":
            self._send(200, action_logs(), "application/json")
        elif self.path == "/api/groups":
            self._send(200, action_groups(), "application/json")
        else:
            self._send(404, "Not Found", "text/plain")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        body = self.rfile.read(length) if 0 < length <= 65536 else None
        if not self._trusted_request(mutation=True):
            self._send(403,'{"ok":false,"output":"请求来源或会话校验失败，请刷新控制台"}')
            return
        if self.path != "/api/action":
            self._send(404,"Not Found","text/plain")
            return
        token = new_request()
        try:
            if body is None:
                raise ValueError("请求长度无效")
            req = json.loads(body.decode("utf-8"))
            if not isinstance(req,dict):
                raise ValueError("请求必须为JSON对象")
            if set(req) - {"action","value","group","mode"}:
                raise ValueError("请求含不支持的参数；抽取仅支持整套结果")
            action = req.get("action")
            fn = ACTIONS.get(action) if isinstance(action,str) else None
            if fn is None:
                raise ValueError("未知操作")
            value, group = str(req.get("value","")).strip(), req.get("group")
            with _CONFIG_LOCK:
                if action == "roll":
                    output = fn(group=group,mode=req.get("mode","assessment"))
                elif action in ("planet","variant","custom","inject","inject_push"):
                    output = fn(value,group=group)
                elif action in ("group_add","group_remove","group_default","group_migrate"):
                    output = fn(value)
                elif action in ("send_guide","report","roll","send_guide_to"):
                    output = fn(group=group)
                else:
                    output = fn()
            self._send(200,json.dumps({"ok":not str(output).startswith(("❌","⚠️")),"output":output},ensure_ascii=False))
        except (ValueError,UnicodeError) as error:
            self._send(400,json.dumps({"ok":False,"output":str(error)},ensure_ascii=False))
        except Exception as error:
            print("控制台操作失败：",type(error).__name__,file=sys.stderr)
            self._send(500,'{"ok":false,"output":"操作失败，请检查配置和服务端日志"}')
        finally:
            request_id.reset(token)


def open_browser():
    try:
        import webbrowser
        time.sleep(1.2)
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    except Exception:
        pass


if __name__ == "__main__":
    print(f"HD2 真理部控制台启动: http://127.0.0.1:{PORT}")
    threading.Thread(target=open_browser, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
