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
import urllib.request
import urllib.error
import datetime as _dt
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
        raise ValueError("控制台主动推送只支持已配置的 NapCat 通道；官方 QQ 请在 AstrBot 适配器中配置支持的会话能力")
    value = _default_group() if raw is None or str(raw).strip() == "" else raw
    return int(_norm_gid(value))
PORT = int(_g("webui", "port", d=8630))
from scripts import instance
sys.path.insert(0, os.path.join(_ROOT, "plugins"))
from astrbot_plugin_hd2_common import commands as service
from astrbot_plugin_hd2_common.diagnostics import hd2_log
from astrbot_plugin_hd2_common.runtime import new_request, request_id
_CONFIG_LOCK = threading.RLock()
CSRF_TOKEN = secrets.token_urlsafe(32)

SEND_SCRIPT = os.path.join(BASE, "scripts", "napcat_send_msg.py")
GEN_REPORT = os.path.join(BASE, "scripts", "gen_report_live.py")
REPORT_FILE = os.path.join(TEMP, "report_live2.txt")
WS_INJECT = os.path.join(BASE, "scripts", "napcat_ws_inject.py")
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
NAPCAT_DIR = _g("napcat", "dir", d="")
QQ_EXE = _g("napcat", "qq_exe", d="")
QRCODE_FILE = _cfg.resolve_napcat(_g("napcat", "qrcode_rel", d="cache/qrcode.png")) if _cfg else os.path.join(NAPCAT_DIR, "cache", "qrcode.png")
LOAD_JS = os.path.join(NAPCAT_DIR, "loadNapCat.js")
NAP_MAIN = os.path.join(NAPCAT_DIR, "NapCatWinBootMain.exe")
NAP_HOOK = os.path.join(NAPCAT_DIR, "NapCatWinBootHook.dll")

PS = ["powershell", "-NoProfile", "-Command"]


def _ps(cmd: str, timeout=20) -> str:
    try:
        r = subprocess.run(PS + [cmd], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _proc_names() -> list:
    out = _ps("Get-Process | Where-Object { $_.ProcessName -match 'astrbot|NapCat|QQ' } | Select-Object -ExpandProperty ProcessName")
    return [l.strip() for l in out.splitlines() if l.strip()]


def _port_listen(port: int) -> bool:
    return _ps(f"(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Measure-Object).Count") not in ("", "0")


def _ws_connected():
    if instance.platform() != "napcat":
        return False
    port = int(_g("astrbot","ws_port",d=6199))
    return _ps(f"(Get-NetTCPConnection -LocalPort {port} -State Established -ErrorAction SilentlyContinue | Measure-Object).Count") not in ("","0")


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


def _start_napcat() -> str:
    if any(p.startswith("NapCat") for p in _proc_names()):
        return "✅ NapCat 已在运行"
    env = dict(os.environ)
    env["NAPCAT_PATCH_PACKAGE"] = os.path.join(NAPCAT_DIR, "qqnt.json")
    env["NAPCAT_LOAD_PATH"] = LOAD_JS
    env["NAPCAT_INJECT_PATH"] = NAP_HOOK
    env["NAPCAT_LAUNCHER_PATH"] = NAP_MAIN
    env["NAPCAT_MAIN_PATH"] = os.path.join(NAPCAT_DIR, "napcat.mjs")
    try:
        with io.open(LOAD_JS, "w", encoding="utf-8") as f:
            f.write('(async () => {await import("file:///' + NAPCAT_DIR.replace('\\', '/') + '/napcat.mjs")})()')
        subprocess.Popen([NAP_MAIN, QQ_EXE, NAP_HOOK], env=env, cwd=NAPCAT_DIR)
        return "✅ NapCat 启动指令已发出（等待约 25 秒生成二维码）"
    except Exception as e:
        return f"❌ 启动 NapCat 失败: {e}"


def action_boot_all():
    lines = [_start_astrbot()]
    if instance.platform() == "napcat":
        from scripts.launch import start_napcat
        lines.append(start_napcat())
    else:
        lines.append("官方平台模式，请在 AstrBot 适配器完成认证；跳过 NapCat 登录")
    return "\n".join(lines)


def action_status():
    port = int(_g("astrbot","webui_port",d=6185))
    lines = ["当前接入：" + instance.platform(), f"AstrBot {port}：" + ("已监听" if _port_listen(port) else "未监听")]
    if instance.platform() == "napcat":
        lines.append(f"OneBot {_g('astrbot','ws_port',d=6199)}：" + ("存在连接（不等于消息发送成功）" if _ws_connected() else "未连接"))
    else:
        lines.append("官方平台认证和连接状态在 AstrBot 适配器中确认；控制台不使用 NapCat 推送/欢迎能力")
    return "\n".join(lines)


def action_restart_napcat():
    if instance.platform() != "napcat":
        return "❌ 当前是官方平台模式，不启动 NapCat"
    instance.stop("napcat")
    from scripts.launch import start_napcat
    return start_napcat()


def action_open_qrcode() -> str:
    if not os.path.exists(QRCODE_FILE):
        return "❌ 二维码文件不存在：" + QRCODE_FILE
    try:
        os.startfile(QRCODE_FILE)
        return "✅ 已打开二维码图片：" + QRCODE_FILE + f"\n（用手机 QQ 扫一扫登录主号 {_g('bot','self_id', d='机器人号')}）"
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
        return "❌ 控制台主动推送只支持已配置的 NapCat；官方平台能力在 AstrBot 管理"
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
# 👥 群管理：AstrBot 入群白名单 + 推送群列表（增删 / 设默认 / 生效）
# ============================================================
# 生效链路（本项目实测）：
#   AstrBot 的入群白名单由 pipeline 阶段 WhitelistCheckStage 在**启动时读一次**
#   并缓存在内存里：优先 <AstrBot data>/platform_whitelist.json（平台级），
#   无该文件时回退 cmd_config.json 的 platform_settings.id_whitelist（全局）。
#   => 只改 JSON 文件不生效，必须让 AstrBot 重新加载。本控制台按顺序尝试：
#      1) Dashboard API 热重载（POST /api/config/astrbot/update，同进程 reload，零重启）
#      2) 重启 AstrBot 桌面版（astrbot-desktop-tauri.exe，保留其启动环境）
#      3) 都失败 -> 明确提示需手动重启，并说明文件已改好
#   本项目 config.json 的 bot.groups 只决定"推送到哪些群"，与"能否触发"无关，故两处都要同步。
WL_BAK_KEEP = 3          # 每个配置最多保留的备份份数
_CFG_PATH = os.path.join(BASE, "config.json")

_ASTRBOT_DATA_DIR = None         # 解析结果缓存：(dir, cmd_config mtime) / None
_LIVE_EXE = None                 # 上一次成功启动 AstrBot 用的 exe（重启时复用）


def _now_tag() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


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


def _local_ip() -> str:
    """本机对 AstrBot 服务可达的地址（WebUI 可能只监听 127.0.0.1 或 0.0.0.0）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _aiohttp_dashboard():
    """返回 Dashboard 的候选 base_url 列表（去掉重复、保序）"""
    port = int(_g("astrbot", "webui_port", d=6185) or 6185)
    wl = str(_g("napcat", "webui_url", d="") or "")
    m = re.search(r"https?://([\d.]+)", wl)
    host = m.group(1) if m else "127.0.0.1"
    urls = []
    for h in (host, "127.0.0.1", "localhost", _local_ip()):
        if not h:
            continue
        u = f"http://{h}:{port}"
        if u not in urls:
            urls.append(u)
    return urls


def _http_json(url: str, payload=None, headers=None, timeout=15):
    """极简 JSON HTTP 客户端：返回 (ok, status, dict|文本)"""
    data = None
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers,
                                method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return True, resp.status, json.loads(raw)
            except Exception:
                return True, resp.status, raw
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        try:
            return False, e.code, json.loads(raw)
        except Exception:
            return False, e.code, raw
    except Exception as e:
        return False, 0, str(e)


def _astrbot_data_dirs() -> list:
    """候选 AstrBot 数据目录（按配置显式指定优先，然后按标准位置）"""
    cands = []
    try:
        cands.append(str(_g("astrbot", "data_dir", d="") or ""))
    except Exception:
        pass
    cands += [
        os.path.expanduser("~/.astrbot/data"),      # AstrBot 桌面版标准数据目录
        "D:/AstrBot/backend/data",
        os.path.join(BASE, "data"),
    ]
    try:
        cands.append(str(_g("astrbot", "app_dir", d="") or ""))
    except Exception:
        pass
    out = []
    for p in cands:
        if not p:
            continue
        p = os.path.abspath(p)
        # app_dir 指向 .../backend/app，数据目录是 .../backend/data
        if p.replace("\\", "/").endswith("/app"):
            p = os.path.join(os.path.dirname(p), "data")
        if p not in out and os.path.isdir(p):
            out.append(p)
    return out


def _astrbot_data_dir(refresh=False):
    path = str(_g("astrbot", "data_dir", d="") or "")
    if not path:
        raise RuntimeError("请明确配置 astrbot.data_dir，不能自动选择其他实例")
    path = _cfg.resolve(path)
    config = os.path.join(path, "cmd_config.json")
    if not os.path.isdir(path):
        raise RuntimeError("指定的 AstrBot 数据目录不存在")
    return path, config, _mode_of(config)


def _read_whitelist(force=False):
    directory, _, _ = _astrbot_data_dir()
    path = os.path.join(directory, "platform_whitelist.json")
    plat = _read_json(path, default={})
    adapter = str(_g("bot", "platform_id", d="") or "")
    if not adapter:
        raise RuntimeError("请配置 bot.platform_id，群管理必须限定到一个 AstrBot 适配器")
    if not isinstance(plat, dict):
        raise RuntimeError("平台白名单格式错误")
    return {"source":path,"groups":[str(g) for g in plat.get(adapter,[])],"plat":plat,"per_platform":True}


def _norm_gid(raw) -> str:
    """群号规范化 + 校验：纯数字、5~12 位"""
    gid = str(raw or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{4,11}", gid):
        raise ValueError("群号需为 5~12 位数字，不能包含其他字符")
    return gid


def action_group_list():
    out = {"platform":instance.platform(),"push_groups":[str(g) for g in (_g("bot","groups",d=[]) or [])],
           "default_group":str(_g("bot","default_group",d="") or ""),"data_dir":str(_g("astrbot","data_dir",d="") or ""),
           "config_active":_cfg.config_path(),"running_groups":[]}
    if instance.platform() == "qq_official":
        out["source_kind"] = "官方平台会话由 AstrBot 管理"
    else:
        try:
            whitelist = _read_whitelist(force=True)
            out.update({"running_groups":whitelist["groups"],"running_source":whitelist["source"],"source_kind":"已保存的所选平台白名单"})
        except Exception as error:
            out["error"] = str(error)
    return json.dumps(out,ensure_ascii=False)


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


def _apply_whitelist(plat, per_platform, groups):
    with _CONFIG_LOCK:
        current = _read_whitelist(force=True)
        target = str(_g("bot", "platform_id", d=""))
        existing = current["plat"]
        values = sorted(set(current["groups"]) | set(map(str,groups)))
        existing[target] = values
        _backup_file(current["source"])
        _atomic_write(current["source"], json.dumps(existing,ensure_ascii=False,indent=2))
    return ["✅ 已保存所选平台白名单；未修改其他平台及全局白名单"], True


def _running_bot_procs():
    return [{"ProcessId":pid} for pid in instance.verified_pids("astrbot")]


def _restart_astrbot():
    instance.stop("astrbot")
    return [_start_astrbot()]


def _dashboard_hot_reload(groups: list) -> list:
    """用 AstrBot Dashboard API 热重载白名单（同进程内生效，零重启）。
    返回失败原因列表；成功返回空列表。
    """
    user = str(_g("astrbot", "dashboard_user", d="astrbot") or "astrbot")
    pwd = str(_g("astrbot", "dashboard_password", d="astrbot") or "astrbot")
    errs = []
    for base in _aiohttp_dashboard():
        # 1) 登录拿 JWT
        ok_l, code_l, body_l = _http_json(base + "/api/auth/login",
                                         payload={"username": user, "password": pwd})
        token = ""
        if isinstance(body_l, dict):
            token = str(body_l.get("token") or "")
            if not token and isinstance(body_l.get("data"), dict):
                token = str(body_l["data"].get("token") or "")
        if not ok_l or not token:
            errs.append(f"{base} 登录失败（HTTP {code_l}）：{str(body_l)[:120]}")
            continue
        hdr = {"Authorization": "Bearer " + token}
        # 2) 读取当前配置 -> 并入群号 -> 写回（服务端保存时会 reload_pipeline_scheduler）
        ok_g, code_g, body_g = _http_json(base + "/api/config/system-config", headers=hdr)
        cfg = body_g.get("data", body_g) if isinstance(body_g, dict) else {}
        if isinstance(cfg, dict) and "config" in cfg and isinstance(cfg["config"], dict):
            cfg = cfg["config"]
        if not ok_g or not isinstance(cfg, dict) or not cfg:
            errs.append(f"{base} 读取配置失败（HTTP {code_g}）：{str(body_g)[:120]}")
            continue
        ps = cfg.setdefault("platform_settings", {})
        cur = [str(x).strip() for x in (ps.get("id_whitelist") or []) if str(x).strip()]
        ps["id_whitelist"] = [int(x) if x.isdigit() else x
                              for x in sorted(set(cur) | set(groups), key=lambda x: (len(x), x))]
        ok_u, code_u, body_u = _http_json(base + "/api/config/astrbot/update",
                                          payload={"conf_id": "default", "config": cfg}, headers=hdr)
        if ok_u and not (isinstance(body_u, dict) and body_u.get("status") == "error"):
            return []   # 成功
        errs.append(f"{base} 保存失败（HTTP {code_u}）：{str(body_u)[:120]}")
    return errs


def _reload_or_restart(groups):
    return "ℹ️ 配置已保存。HD2指令守卫读取当前推送群范围；AstrBot自身白名单请在后台重载所选平台。文件回读不代表框架配置已生效。"


def action_group_add(raw: str) -> str:
    """添加群号：写白名单（AstrBot）+ 写推送群列表（config.json），然后生效"""
    if instance.platform() != "napcat":
        return "❌ 官方平台请在 AstrBot 中管理会话与白名单"
    try:
        gid = _norm_gid(raw)
    except ValueError as e:
        return f"❌ {e}"
    try:
        wl = _read_whitelist(force=True)
    except Exception as e:
        return f"❌ 读取 AstrBot 白名单失败：{e}"
    msgs = [f"👥 添加群 {gid}"]
    try:
        wl_msgs, _ = _apply_whitelist(wl["plat"], wl["per_platform"], [gid])
        msgs += wl_msgs
    except Exception as e:
        return f"❌ 写入 AstrBot 白名单失败：{e}"
    # 推送群列表
    push = [str(g) for g in (_g("bot", "groups", d=[]) or [])]
    if gid in push:
        msgs.append(f"• 推送群列表已含 {gid}（bot.groups 无变化）")
    else:
        try:
            _, pmsg = _set_push_groups(push + [gid])
            msgs += pmsg
        except Exception as e:
            msgs.append(f"⚠️ 写 config.json 失败: {e}")
    msgs.append("")
    msgs.append(_reload_or_restart([gid]))
    try:
        msgs.append("\n· 回读已保存白名单：" + ", ".join(_read_whitelist(force=True)["groups"]))
    except Exception:
        pass
    return "\n".join(msgs)


def action_group_remove(raw):
    try:
        if instance.platform() != "napcat":
            raise ValueError("官方平台请在 AstrBot 中管理会话白名单")
        gid = _norm_gid(raw)
        with _CONFIG_LOCK:
            current = _read_whitelist(force=True)
            target = str(_g("bot", "platform_id", d=""))
            current["plat"][target] = [x for x in current["groups"] if x != gid]
            _backup_file(current["source"])
            _atomic_write(current["source"], json.dumps(current["plat"],ensure_ascii=False,indent=2))
            groups = [str(g) for g in (_g("bot","groups",d=[]) or []) if str(g) != gid]
            default = _default_group()
            if str(default) == gid:
                default = int(groups[0]) if groups else 0
            _set_push_groups(groups,default)
        return "✅ 已移除群 " + gid + "\n" + _reload_or_restart([])
    except Exception as error:
        return "❌ " + str(error)


def action_group_default(raw: str) -> str:
    """设为默认推送群（只影响推送，不影响是否响应）"""
    if instance.platform() != "napcat":
        return "❌ 官方平台请在 AstrBot 中管理会话与白名单"
    try:
        gid = _norm_gid(raw)
    except ValueError as e:
        return f"❌ {e}"
    push = [str(g) for g in (_g("bot", "groups", d=[]) or [])]
    if gid not in push:
        return f"❌ 群 {gid} 不在推送群列表里，请先「添加群」"
    try:
        _, pmsg = _set_push_groups(push, int(gid))
    except Exception as e:
        return f"❌ 写 config.json 失败: {e}"
    return ("✅ 默认推送群已设为 " + gid + "\n" + "\n".join(pmsg) +
            f"\n· 推送群列表：{', '.join(push)}\n"
            "· 提示：默认群只决定 WebUI 里不带群号的推送目标（界面显示需重启 WebUI 才更新）")


def action_restart_astrbot() -> str:
    """手动重启 AstrBot（白名单改完但未生效时用）"""
    lines = ["🔄 重启 AstrBot："]
    lines += _restart_astrbot()
    try:
        lines.append("· 已保存白名单：" + ", ".join(_read_whitelist(force=True)["groups"]))
    except Exception:
        pass
    return "\n".join(lines)


LOG_FILE = os.path.join(BASE, "temp", "指令日志.jsonl")


def action_logs() -> str:
    """读取指令执行流水（最近 200 条）"""
    try:
        rows = []
        if os.path.exists(LOG_FILE):
            with io.open(LOG_FILE, encoding="utf-8") as f:
                for line in f.readlines()[-200:]:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            pass
        return json.dumps({"logs": rows}, ensure_ascii=False)
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
    "restart_napcat": action_restart_napcat,
    "open_qrcode": action_open_qrcode,
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
    <p class="desc">启动 AstrBot + NapCat，自动打开二维码扫码登录 QQ，验证全链路（机器人上线）</p>
    <div class="btn-row">
      <button onclick="run('boot_all')">🚀 一键启动</button>
      <button class="sec" onclick="run('open_qrcode')">📱 打开二维码</button>
      <button class="sec" onclick="run('restart_napcat')">🔄 重启NapCat</button>
      <button class="sec" onclick="run('restart_astrbot')">🔄 重启AstrBot</button>
      <button class="sec" onclick="run('restart_webui')">🔁 重启WebUI</button>
      <button onclick="run('status')">🔍 状态</button>
    </div>
    <div class="hint">一键启动约 30~60 秒；「重启 NapCat」用于 QQ 掉线后重新扫码（会先关掉旧 QQ 进程）；「重启 WebUI」用于本页面卡死/服务被杀后自愈</div>
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
    <h3>👥 群号管理（白名单 / 推送）</h3>
    <p class="desc">添加或移除群号：同时更新 AstrBot 入群白名单与推送群列表，并让改动生效</p>
    <div class="btn-row">
      <button onclick="openGroupDialog()">👥 打开群管理</button>
      <button class="sec" onclick="run('group_list')">🔍 查看当前生效群</button>
    </div>
    <div class="hint">入群白名单由 AstrBot 在<b>启动时读一次</b>并缓存在内存，改文件不会立刻生效：本卡片会先尝试 Dashboard 热重载（零重启），不可用时才重启 AstrBot</div>
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
    <h3>👥 群号管理</h3>
    <div class="grp-src" id="groupSrc">加载中...</div>
    <div class="grp-list" id="groupChips"></div>
    <div class="grp-row">
      <input type="text" id="groupInput" placeholder="群号，如 123456789（纯数字）" style="flex:1;margin:0">
      <button onclick="groupAct('group_add')">➕ 添加</button>
      <button class="danger" onclick="groupAct('group_remove')">➖ 移除</button>
      <button class="sec" onclick="groupAct('group_default')">⭐ 设为默认推送群</button>
    </div>
    <div class="grp-row">
      <button class="sec" onclick="groupRefresh()">🔍 刷新</button>
      <button class="sec" onclick="run('restart_astrbot')">🔄 重启 AstrBot</button>
      <button class="danger" onclick="closeGroupDialog()">✖ 关闭</button>
    </div>
    <div class="grp-status" id="groupStatus"></div>
    <div class="hint">「添加/移除」会同时改 AstrBot 的 <code>platform_whitelist.json</code> + <code>cmd_config.json</code> 和本项目 <code>config.json</code> 的推送群列表；改前自动备份。默认推送群只影响推送目标。</div>
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
  src.textContent = '⏳ 读取中...'; chips.innerHTML = '';
  try {
    const d = await callApi('group_list');
    const info = JSON.parse(d.output);
    if (info.error) {
      src.textContent = '⚠️ ' + info.error;
    } else {
      src.innerHTML = 'AstrBot 数据目录：<code>' + escapeHtml(info.data_dir || '-') + '</code><br>'
        + '白名单来源（' + escapeHtml(info.source_kind || '-') + '）：<code>' + escapeHtml(info.running_source || '-') + '</code><br>'
        + '已保存白名单（' + (info.running_groups || []).length + ' 个）';
    }
    const rg = info.running_groups || [];
    chips.innerHTML = rg.length
      ? rg.map(g => `<span class="grp-chip"><b>${escapeHtml(g)}</b>${(info.push_groups||[]).map(String).includes(String(g))?'📤':''}<span title="移除此群" data-group="${escapeHtml(g)}" onclick="groupAct('group_remove',this.dataset.group)">✖</span></span>`).join('')
      : '<span class="hint">暂无所选平台白名单；以 AstrBot 配置与已配置推送群范围为准</span>';
    src.innerHTML += `<br>推送群列表（${(info.push_groups||[]).length} 个）：${escapeHtml((info.push_groups||[]).join(', ') || '-')}`
      + `　默认推送群：<b>${escapeHtml(info.default_group||'-')}</b>`;
    src.innerHTML += `<br>推送配置：<code>${escapeHtml(info.config_active || '-')}</code>`
      + ((info.config_local && info.config_local !== info.config_active)
          ? `<br>同目录副本：<code>${escapeHtml(info.config_local)}</code>` : '');
  } catch (e) {
    src.textContent = '❌ 读取失败: ' + e;
  }
}
async function groupAct(action, preset) {
  const input = document.getElementById('groupInput');
  const gid = (preset !== undefined && preset !== null) ? String(preset) : (input.value || '').trim();
  if (!gid) { gsSet('❌ 请先填群号'); return; }
  if (action === 'group_remove' && !confirm('确认移除群 ' + gid + '？\n（会同时移出 AstrBot 白名单和推送群列表）')) return;
  gsSet('⏳ 执行中...（添加/移除会尝试热重载，必要时重启 AstrBot，可能需要 20~60 秒）');
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
        if not self._trusted_request(mutation=True):
            self._send(403,'{"ok":false,"output":"请求来源或会话校验失败，请刷新控制台"}')
            return
        if self.path != "/api/action":
            self._send(404,"Not Found","text/plain")
            return
        token = new_request()
        try:
            length = int(self.headers.get("Content-Length",0))
            if not 0 < length <= 65536:
                raise ValueError("请求长度无效")
            req = json.loads(self.rfile.read(length).decode("utf-8"))
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
