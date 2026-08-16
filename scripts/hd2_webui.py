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
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# UTF-8 统一
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ============ 配置化（hd2_config + config.json） ============
_ROOT = os.environ.get("HD2_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    import hd2_config as _cfg
    def _g(*k, d=None):
        return _cfg.get(*k, default=d)
except Exception:
    def _g(*k, d=None):
        return d

PYTHON = _g("astrbot", "python", d="python")
BASE = _ROOT
HD2 = _ROOT
TEMP = os.path.join(BASE, "temp")
os.makedirs(TEMP, exist_ok=True)
GROUP_ID = int(_g("bot", "default_group", d=0))
PORT = int(_g("webui", "port", d=8630))

SEND_SCRIPT = os.path.join(BASE, "scripts", "napcat_send_msg.py")
GEN_REPORT = os.path.join(BASE, "scripts", "gen_report_live.py")
REPORT_FILE = os.path.join(TEMP, "report_live2.txt")
WS_INJECT = os.path.join(BASE, "scripts", "napcat_ws_inject.py")
RESTART_HELPER = os.path.join(BASE, "scripts", "restart_webui.py")
GUIDE_FILE = _cfg.resolve(_g("paths", "guide", d="tables/群使用说明.txt")) if "_cfg" in dir() and _cfg else os.path.join(BASE, "tables", "群使用说明.txt")

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
QRCODE_FILE = _cfg.resolve_napcat(_g("napcat", "qrcode_rel", d="cache/qrcode.png")) if "_cfg" in dir() and _cfg else os.path.join(NAPCAT_DIR, "cache", "qrcode.png")
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


def _ws_connected() -> bool:
    return _ps("(Get-NetTCPConnection -LocalPort 6199 -State Established -ErrorAction SilentlyContinue | Measure-Object).Count") not in ("", "0")


def _start_astrbot() -> str:
    if _port_listen(6185):
        return "✅ AstrBot 已在运行（WebUI 6185）"
    try:
        subprocess.Popen([ASTRBOT_EXE])
    except Exception as e:
        return f"❌ 启动 AstrBot 失败: {e}"
    for _ in range(20):
        time.sleep(3)
        if _port_listen(6185):
            return "✅ AstrBot 已启动（WebUI 6185）"
    return "⚠️ AstrBot 启动中，WebUI 尚未就绪（稍等片刻）"


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


def action_boot_all() -> str:
    lines = ["🛠️ 一键启动部署："]
    lines.append("· " + _start_astrbot())
    lines.append("· " + _start_napcat())
    for _ in range(30):
        time.sleep(2)
        if os.path.exists(QRCODE_FILE) and time.time() - os.path.getmtime(QRCODE_FILE) < 180:
            break
    if os.path.exists(QRCODE_FILE):
        import datetime as _dt
        mt = _dt.datetime.fromtimestamp(os.path.getmtime(QRCODE_FILE)).strftime("%H:%M:%S")
        lines.append(f"· 📱 二维码已生成（{mt}）")
        lines.append(f"· 请用手机 QQ 扫一扫登录主号 {_g('bot','self_id', d='机器人号')}（若报已登录，先退出 PC 端 QQ）")
        try:
            os.startfile(QRCODE_FILE)
            lines.append("· 已用图片查看器打开二维码")
        except Exception:
            pass
        lines.append(f"· 缓存路径：{QRCODE_FILE}")
    else:
        lines.append("· ⚠️ 二维码尚未生成，稍后点「📱 打开二维码」")
    for _ in range(20):
        time.sleep(3)
        if _ws_connected():
            break
    lines.append("· WS 6199 连接：" + ("✅ 已连接（机器人上线）" if _ws_connected() else "⏳ 未连接（等待扫码登录）"))
    return "\n".join(lines)


def action_status() -> str:
    import datetime as _dt
    names = _proc_names()
    lines = ["🔍 全链路状态："]
    lines.append("· AstrBot 进程：" + ("✅ 运行中" if any(n == "astrbot-desktop-tauri" for n in names) else "❌ 未运行"))
    lines.append("· WebUI 6185：" + ("✅ 监听中" if _port_listen(6185) else "❌ 未监听"))
    lines.append("· NapCat 进程：" + ("✅ 运行中" if any(n.startswith("NapCat") for n in names) else "❌ 未运行"))
    lines.append("· QQ 进程：" + ("✅ 存在" if "QQ" in names else "❌ 无"))
    lines.append("· WS 6199：" + ("✅ 已连接" if _ws_connected() else "❌ 未连接"))
    if os.path.exists(QRCODE_FILE):
        mt = _dt.datetime.fromtimestamp(os.path.getmtime(QRCODE_FILE)).strftime("%m-%d %H:%M:%S")
        lines.append(f"· 二维码：{QRCODE_FILE}（更新于 {mt}）")
    return "\n".join(lines)


def action_restart_napcat() -> str:
    _ps("Stop-Process -Name NapCatWinBootMain,QQ -Force -ErrorAction SilentlyContinue")
    time.sleep(3)
    lines = ["🔄 已停止旧 NapCat/QQ 进程，重新启动..."]
    lines.append("· " + _start_napcat())
    for _ in range(30):
        time.sleep(2)
        if os.path.exists(QRCODE_FILE) and time.time() - os.path.getmtime(QRCODE_FILE) < 180:
            break
    if os.path.exists(QRCODE_FILE):
        lines.append("· 📱 新二维码已生成，点「打开二维码」扫码（先退出 PC 端 QQ）")
        try:
            os.startfile(QRCODE_FILE)
            lines.append("· 已用图片查看器打开二维码")
        except Exception:
            pass
    else:
        lines.append("· ⚠️ 二维码尚未生成，稍后再试")
    return "\n".join(lines)


def action_open_qrcode() -> str:
    if not os.path.exists(QRCODE_FILE):
        return "❌ 二维码文件不存在：" + QRCODE_FILE
    try:
        os.startfile(QRCODE_FILE)
        return "✅ 已打开二维码图片：" + QRCODE_FILE + f"\n（用手机 QQ 扫一扫登录主号 {_g('bot','self_id', d='机器人号')}）"
    except Exception as e:
        return f"❌ 打开失败: {e}"


def action_send_guide() -> str:
    """一键推送：把群聊版使用说明直接发到群"""
    try:
        with io.open(GUIDE_FILE, encoding="utf-8") as f:
            guide = f.read().strip()
        if not guide:
            return "❌ 使用说明文件为空：" + GUIDE_FILE
        push = send_msg(guide)
        return f"📖 使用说明已推送\n{push}\n\n──── 内容预览 ────\n{guide[:200]}"
    except Exception as e:
        return f"❌ 推送使用说明失败: {e}"


def action_send_guide_to(group_id) -> str:
    """推送使用说明到指定群"""
    try:
        gid = int(str(group_id).strip())
        with io.open(GUIDE_FILE, encoding="utf-8") as f:
            guide = f.read().strip()
        push = send_msg(guide, gid)
        return f"📖 已推送使用说明到群 {gid}\n{push}"
    except Exception as e:
        return f"❌ 推送失败: {e}"


def action_push_all() -> str:
    """全局推送：使用说明推送到所有白名单群"""
    try:
        groups = _g("bot", "groups", d=[]) or []
        if not groups:
            return "❌ 未配置 bot.groups"
        with io.open(GUIDE_FILE, encoding="utf-8") as f:
            guide = f.read().strip()
        lines = ["📖 全局推送使用说明（" + str(len(groups)) + " 个群）："]
        for g in groups:
            r = send_msg(guide, int(g))
            lines.append(f"群 {g}: {r}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 全局推送失败: {e}"


def _push_text_all(title: str, text: str) -> str:
    """把内容推送到所有白名单群，返回汇总"""
    groups = _g("bot", "groups", d=[]) or []
    if not groups:
        return "❌ 未配置 bot.groups"
    lines = [f"{title}（{len(groups)} 个群）："]
    for g in groups:
        r = send_msg(text, int(g))
        lines.append(f"群 {g}: {r}")
    return "\n".join(lines)


def action_push_all_report() -> str:
    """战报群发：生成战报推送到所有群"""
    r = run_sub([PYTHON, GEN_REPORT], timeout=180)
    if os.path.exists(REPORT_FILE):
        with io.open(REPORT_FILE, encoding="utf-8") as f:
            report = f.read()
        return _push_text_all("📊 战报群发", report)
    return "❌ 战报生成失败\n" + (r.stdout or r.stderr)[-300:]


def action_push_all_dss() -> str:
    """DSS 群发"""
    import asyncio as _aio
    try:
        for p in APP_PATHS:
            sys.path.insert(0, p)
        wr = _load_plugin(PLUGIN_WAR)
        pm = wr._load_planet_map()
        text = _aio.run(wr._get_dss_status(pm))
        return _push_text_all("🛰️ DSS 群发", text)
    except Exception as e:
        return f"❌ DSS 群发失败: {e}"


def action_push_all_campaigns() -> str:
    """战役群发"""
    try:
        for p in APP_PATHS:
            sys.path.insert(0, p)
        wr = _load_plugin(PLUGIN_WAR)
        pm = wr._load_planet_map()
        text = wr._get_campaigns(pm)
        return _push_text_all("⚔️ 战役群发", text)
    except Exception as e:
        return f"❌ 战役群发失败: {e}"


def action_push_all_analysis() -> str:
    """LLM 战局分析群发（走 WS 注入拿回复后群发）"""
    r = run_sub([PYTHON, WS_INJECT, "/分析", str(GROUP_ID)], timeout=180)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    reply = _extract_reply(out)
    if not reply:
        return "❌ 机器人未返回有效回复，未推送。\n\n" + out[-300:]
    return _push_text_all("🧠 战局分析群发", reply)


def action_push_all_campaign_brief() -> str:
    """战役简报群发（走 WS 注入 /战役 拿回复后群发）"""
    r = run_sub([PYTHON, WS_INJECT, "/战役", str(GROUP_ID)], timeout=180)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    reply = _extract_reply(out)
    if not reply:
        return "❌ 机器人未返回有效回复，未推送。\n\n" + out[-300:]
    return _push_text_all("🎬 战役简报群发", reply)


def action_groups() -> str:
    """返回可用群列表（供前端下拉）"""
    return json.dumps({"groups": _g("bot", "groups", d=[]) or [], "default": GROUP_ID}, ensure_ascii=False)


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

def action_restart_webui() -> str:
    """重启 WebUI 自身：由独立进程延迟杀旧 + 拉起新进程（约 5 秒后服务恢复，需刷新页面）"""
    try:
        subprocess.Popen(
            [PYTHON.replace("python.exe", "pythonw.exe") if PYTHON.lower().endswith("python.exe") else "pythonw", RESTART_HELPER],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return "🔁 正在重启 WebUI（约 5 秒后服务恢复），请稍后刷新页面。\n若 10 秒后仍无法访问，请双击 启动HD2推送终端.bat 手动启动。"
    except Exception as e:
        return f"❌ 触发重启失败: {e}"



def run_sub(args, timeout=180):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env, cwd=HD2)
    return r


def send_msg(text: str, group_id=None) -> str:
    """推送消息到群（走 NapCat WebUI API），group_id 缺省用默认群"""
    gid = int(group_id) if group_id else GROUP_ID
    msg_file = os.path.join(TEMP, "webui_send_msg.txt")
    with io.open(msg_file, "w", encoding="utf-8") as f:
        f.write(text)
    r = run_sub([PYTHON, SEND_SCRIPT, msg_file, str(gid)], timeout=60)
    ok = "RESULT: OK" in r.stdout
    return ("✅ 已推送到群 %d" % gid) if ok else ("❌ 推送失败: " + (r.stdout or r.stderr)[-300:])


def action_report() -> str:
    """生成战报并推送（LLM 翻译，约 30-60 秒）"""
    r = run_sub([PYTHON, GEN_REPORT], timeout=180)
    if os.path.exists(REPORT_FILE):
        with io.open(REPORT_FILE, encoding="utf-8") as f:
            report = f.read()
        lines = ["📊 战报已生成（%d 字符），推送结果：" % len(report)]
        lines.append(send_msg(report))
        lines.append("")
        lines.append("──── 战报内容 ────")
        lines.append(report)
        return "\n".join(lines)
    return "❌ 战报生成失败\n" + (r.stdout or r.stderr)[-500:]


def action_roll() -> str:
    try:
        for p in APP_PATHS:
            sys.path.insert(0, p)
        roll_mod = _load_plugin(PLUGIN_ROLL)
        result = roll_mod._roll_stratagems(4)
        return "🎲 随机战备\n" + result + "\n\n推送结果：" + send_msg(result)
    except Exception as e:
        return f"❌ Roll 失败: {e}"


def action_planet(name: str) -> str:
    import asyncio
    try:
        for p in APP_PATHS:
            sys.path.insert(0, p)
        planet_mod = _load_plugin(PLUGIN_PLANET)

        async def run():
            slug = planet_mod._planet_slug_from_query(name.strip().lower())
            if not slug:
                return "⚠️ 未识别该星球名，请用中英文名重试。"
            title_cn = None
            for en, cn in planet_mod._get_planet_name_map().items():
                if cn and en.lower().replace(" ", "_") == slug:
                    title_cn = cn
                    break
            if slug in ("super-earth", "superearth", "super_earth"):
                result = planet_mod._translate_planet_info({"is_super_earth": True})
            else:
                body = await planet_mod._fetch_planet_page_text(slug)
                info = planet_mod._parse_planet_info(body)
                result = planet_mod._translate_planet_info(info)
            if title_cn:
                result = f"🪐 {title_cn}\n\n" + result
            return result

        result = asyncio.run(run())
        return "🪐 星球信息\n" + result + "\n\n推送结果：" + send_msg(result)
    except Exception as e:
        return f"❌ 星球信息失败: {e}"


def action_variant(name: str) -> str:
    import asyncio
    try:
        for p in APP_PATHS:
            sys.path.insert(0, p)
        variant_mod = _load_plugin(PLUGIN_VARIANT)

        async def run():
            matched = None
            for kw, cn in variant_mod.VARIANT_CN.items():
                if cn in name:
                    matched = kw
                    break
            if not matched:
                if "变种" in name or "变异" in name:
                    return "未查询到该变种信息。切莫粗心大意。"
                return "⚠️ 未识别该变种名（如：孢裂变种/掠食变种/无脑群氓/生化人/蟑龙/霸王虫/炽灼部队/占领者/喷气旅）"
            scan = await variant_mod._scan_all_variants()
            planets = scan.get(matched, [])
            cn_name = variant_mod.VARIANT_CN[matched]
            if not planets:
                return "未查询到该变种信息。切莫粗心大意。"
            cn_map = variant_mod._load_planet_cn_map()
            lines = ["目前的变种信息："]
            for p in planets:
                p_cn = cn_map.get(p.upper(), p)
                lines.append(f"{p_cn}——{cn_name}")
            return "\n".join(lines)

        result = asyncio.run(run())
        return "🧬 变种查询\n" + result + "\n\n推送结果：" + send_msg(result)
    except Exception as e:
        return f"❌ 变种查询失败: {e}"


def action_custom(text: str) -> str:
    return "📢 自定义消息\n" + text + "\n\n推送结果：" + send_msg(text)


def action_inject(cmd: str) -> str:
    """WS 注入测试：伪装群成员发指令，显示机器人回复（不推送到群）"""
    r = run_sub([PYTHON, WS_INJECT, cmd, str(GROUP_ID)], timeout=90)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    return "🧪 注入指令: " + cmd + "\n\n" + out


def _extract_reply(out: str) -> str:
    """从 ws_inject 输出中提取机器人回复正文（找不到返回空串）"""
    m = re.search(r"✅ 机器人回复了.*?-----\s*\n(.*?)\n={5,}", out, re.S)
    if m:
        return m.group(1).strip()
    if "----- 回复 1 -----" in out:
        return out.split("----- 回复 1 -----", 1)[1].strip().rstrip("=").strip()
    return ""


def action_inject_push(cmd: str) -> str:
    """WS 注入 + 推送：拿到机器人回复后直接推送到群"""
    r = run_sub([PYTHON, WS_INJECT, cmd, str(GROUP_ID)], timeout=90)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    reply = _extract_reply(out)
    if not reply:
        return "❌ 机器人未返回有效回复，未推送。\n\n" + out[-500:]
    push = send_msg(reply)
    return f"📤 注入指令: {cmd}\n\n✅ 机器人回复已推送到群 {GROUP_ID}\n{push}\n\n──── 回复内容 ────\n{reply}"


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
      <button class="sec" onclick="run('restart_webui')">🔁 重启WebUI</button>
      <button onclick="run('status')">🔍 状态</button>
    </div>
    <div class="hint">一键启动约 30~60 秒；「重启 NapCat」用于 QQ 掉线后重新扫码（会先关掉旧 QQ 进程）；「重启 WebUI」用于本页面卡死/服务被杀后自愈</div>
  </div>

  <div class="card">
    <h3>🚀 一键发送</h3>
    <p class="desc">点击即生成并推送到群（无需输入）</p>
    <div class="btn-row">
      <button onclick="run('inject_push','/战报')">📊 战报</button>
      <button class="sec" onclick="run('inject_push','/dss')">🛰️ DSS</button>
      <button class="sec" onclick="run('inject_push','/战线')">⚔️ 战线</button>
      <button class="sec" onclick="run('inject_push','/战役')">🎬 战役</button>
      <button onclick="run('inject_push','/分析')">🧠 分析</button>
      <button class="sec" onclick="run('inject_push','/查表')">📚 查表</button>
      <button class="sec" onclick="run('inject_push','/roll')">🎲 Roll</button>
      <button class="danger" onclick="run('send_guide')">📖 使用说明</button>
    </div>
    <div class="btn-row" style="margin-top:10px">
      <select id="targetGroup" style="flex:1;background:#0f141d;border:1px solid var(--border);color:var(--text);border-radius:8px;padding:8px;font-size:13px">
        <option value="">加载中...</option>
      </select>
      <button class="danger" onclick="run('send_guide_to', document.getElementById('targetGroup').value)">📖 推送说明到所选群</button>
      <button class="sec" onclick="run('push_all')">🌐 全局推送说明</button>
    </div>
    <div class="hint">分析约 30~60 秒；战报约 30~60 秒；其余约 5~15 秒</div>
  </div>

  <div class="card">
    <h3>📤 一键群发（所有白名单群）</h3>
    <p class="desc">生成内容并推送到全部群（{GROUP} 等）</p>
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
    <p class="desc">生成最新战况（重要指令 + 目标星球 + 战区分布 + 资讯），推送到群 {GROUP}</p>
    <div class="btn-row"><button onclick="run('report')">生成并推送</button></div>
  </div>

  <div class="card">
    <h3>🎲 推送随机战备</h3>
    <p class="desc">从全类型战备池随机抽取 4 个，推送到群</p>
    <div class="btn-row"><button class="sec" onclick="run('roll')">Roll 并推送</button></div>
  </div>

  <div class="card">
    <h3>🪐 星球信息</h3>
    <p class="desc">抓取星球页面：抵抗度 / 行动变量 / POI（群内指令：/星球 &lt;星球名&gt;，中英文均可）</p>
    <input type="text" id="planet" placeholder="如：奥密克戎 或 OMICRON">
    <div class="btn-row"><button class="sec" onclick="run('planet', document.getElementById('planet').value)">查询并推送</button></div>
  </div>

  <div class="card">
    <h3>🧬 变种查询</h3>
    <p class="desc">扫描战役星球，输出存在该变种的所有星球</p>
    <input type="text" id="variant" placeholder="如：孢裂变种 / 喷气旅">
    <div class="btn-row"><button class="sec" onclick="run('variant', document.getElementById('variant').value)">查询并推送</button></div>
  </div>

  <div class="card">
    <h3>📢 自定义消息</h3>
    <p class="desc">直接推送一段文本到群</p>
    <input type="text" id="custom" placeholder="输入要推送的内容...">
    <div class="btn-row"><button onclick="run('custom', document.getElementById('custom').value)">推送</button></div>
  </div>

  <div class="card">
    <h3>🧪 查询推送 / 响应测试</h3>
    <p class="desc">输入指令（查表/战报/星球等），「测试并推送」= 注入拿到机器人回复后直接推送到群；「仅测试」= 只看回复不发群</p>
    <input type="text" id="inject" value="/查表" placeholder="/战报  /查表  /查表 星区 巴纳德  /查表 孢裂变种  /roll">
    <div class="btn-row">
      <button class="danger" onclick="run('inject_push', document.getElementById('inject').value)">📤 测试并推送</button>
      <button class="sec" onclick="run('inject', document.getElementById('inject').value)">🧪 仅测试</button>
      <button class="sec" onclick="document.getElementById('inject').value='/查表'">/查表</button>
      <button class="sec" onclick="document.getElementById('inject').value='/查表 星区 巴纳德'">星区</button>
      <button class="sec" onclick="document.getElementById('inject').value='/查表 孢裂变种'">参数</button>
      <button class="sec" onclick="document.getElementById('inject').value='/dss'">DSS</button>
      <button class="sec" onclick="document.getElementById('inject').value='/战役'">战役</button>
      <button class="sec" onclick="document.getElementById('inject').value='/分析'">分析</button>
    </div>
    <div class="hint">快捷按钮仅填充输入框；「测试并推送」会把机器人回复发到群 {GROUP}</div>
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

<script>
let busy = false;
function setStatus(s) { document.getElementById('status').textContent = s; }
async function run(action, value) {
  if (busy) return;
  const body = { action };
  if (value !== undefined) body.value = value;
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
      headers: { 'Content-Type': 'application/json' },
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
    sel.innerHTML = list.map(g => `<option value="${g}">群 ${g}${g===d.default?'（默认）':''}</option>`).join('');
    if (!list.length) sel.innerHTML = '<option value="">未配置群</option>';
  } catch (e) {}
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
  const m = {received:['已接收','#4da6ff'], blocked:['已拦截','#f87171'], matched:['处理中','#fbbf24'], done:['完成','#4ade80'], failed:['失败','#f87171'], ignored:['忽略','#9aa3b2']};
  const [t,c] = m[stage] || [stage,'#9aa3b2'];
  return `<span style="color:${c};font-weight:600">${t}</span>`;
}
function logDetail(l) {
  const s = [];
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
    <td>${l.time||''}</td><td>${l.group||'-'}</td><td>${l.name||l.user||'-'}</td>
    <td title="${(l.text||'').replace(/"/g,'&quot;')}">${(l.text||'').slice(0,24)}</td>
    <td>${statusBadge(l.stage)}</td><td>${l.plugin||'-'}</td><td>${l.cost!=null?l.cost+'s':''}</td>
    <td><button class="mini" onclick="toggleLogDetail(this,'${l.ts}')">▼</button></td></tr>
    <tr id="d-${l.ts}" style="display:none"><td colspan="8" class="log-detail-cell">${logDetail(l)}</td></tr>`).join('');
}
function toggleLogDetail(btn, ts){ const el=document.getElementById('d-'+ts); const on=el.style.display==='none'; el.style.display=on?'':'none'; btn.textContent=on?'▲':'▼'; }
function setLogFilter(f){ logFilter=f; renderLogs(); }
setInterval(refreshLogs, 3000);
refreshLogs();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send(200, PAGE.replace("{GROUP}", str(GROUP_ID)), "text/html")
        elif self.path == "/api/logs":
            self._send(200, action_logs(), "application/json")
        elif self.path == "/api/groups":
            self._send(200, action_groups(), "application/json")
        else:
            self._send(404, "Not Found", "text/plain")

    def do_POST(self):
        if self.path != "/api/action":
            self._send(404, "Not Found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            action = req.get("action", "")
            value = str(req.get("value", "")).strip()
            fn = ACTIONS.get(action)
            if not fn:
                self._send(200, json.dumps({"ok": False, "output": "未知操作"}))
                return
            output = fn(value) if action in ("planet", "variant", "custom", "inject", "inject_push", "send_guide_to") else fn()
            self._send(200, json.dumps({"ok": True, "output": output}, ensure_ascii=False))
        except Exception as e:
            import traceback
            self._send(200, json.dumps({"ok": False, "output": "服务端异常: %s\n%s" % (e, traceback.format_exc())}, ensure_ascii=False))


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
