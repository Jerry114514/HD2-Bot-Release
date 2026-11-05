# -*- coding: utf-8 -*-
"""
HD2 Bot 一键启动器
==================
按顺序启动：AstrBot → SnowLuma(QQ 协议端) → Web 控制台 → 打开浏览器。
所有路径/账号从 config.json 读取（hd2_config），部署者只需配置一次。

用法：
  python scripts/launch.py
  或直接双击项目根目录的 start.bat
"""
import os
import socket
import sys
import time
import subprocess
import webbrowser

_ROOT = os.environ.get("HD2_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import hd2_config as cfg
from scripts import instance


def _g(*keys, default=None):
    return cfg.get(*keys, default=default)


def port_listen(port: int) -> bool:
    """端口是否在监听（纯 socket 探测，Windows / Linux 通用）"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False


def proc_names() -> list:
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=15,
            )
            names = []
            for line in (out.stdout or "").splitlines():
                parts = line.split('","')
                if parts and (parts[0].strip('" ').lower().startswith(("astrbot", "napcat", "snowluma", "node", "qq"))):
                    names.append(parts[0].strip('" '))
            return names
        out = subprocess.run(["ps", "-eo", "comm="], capture_output=True, text=True, timeout=15)
        return [l.strip() for l in (out.stdout or "").splitlines()
                if l.strip().lower().startswith(("astrbot", "napcat", "snowluma", "node", "qq"))]
    except Exception:
        return []


def wait_port(port: int, timeout: int = 90) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if port_listen(port):
            return True
        time.sleep(3)
    return False


def start_astrbot() -> str:
    if port_listen(int(_g("astrbot", "webui_port", default=6185))):
        instance.verified_pids("astrbot")
        return "✅ 已配置实例的 Dashboard 正在监听"
    exe = _g("astrbot", "exe", default="")
    if not exe or not os.path.exists(exe):
        hint = ("（Linux 下可将 astrbot.exe 配置为可执行启动脚本，"
                "如 AstrBot 官方 CLI 或自建的 wrapper.sh）" if os.name != "nt" else "")
        return f"⚠️ 未找到 AstrBot（config.json 的 astrbot.exe），跳过{hint}"
    subprocess.Popen([exe])
    if wait_port(int(_g("astrbot", "webui_port", default=6185)), 90):
        return "ℹ️ Dashboard 已监听，平台认证请在 AstrBot 中确认"
    return "⚠️ AstrBot 启动中，WebUI 尚未就绪（稍后可用）"


def start_snowluma() -> str:
    """启动 OneBot 协议端 SnowLuma（NapCat 同团队新一代实现）。

    SnowLuma 自动监视并注入 QQ.exe（hookAutoLoad），先启 QQ 或先启 SnowLuma 均可；
    扫码登录在桌面 QQ 窗口完成，WebUI（默认 5099）做账号与 OneBot 网络配置。
    """
    if instance.platform() != "napcat":
        return "ℹ️ 官方平台模式，跳过协议端"
    if port_listen(instance.port("napcat")):
        instance.verified_pids("napcat")
        return "✅ SnowLuma 已在运行（WebUI " + str(instance.port("napcat")) + "）"
    sdir = _g("snowluma", "dir", default="")
    launcher = os.path.join(sdir, "launcher.bat" if os.name == "nt" else "launcher.sh")
    if not sdir or not os.path.exists(launcher):
        return ("⚠️ 未找到 SnowLuma（config.json 的 snowluma.dir，应含 launcher.bat / launcher.sh），"
                "跳过；可手动启动或改用 Docker（Linux 见 docs/Linux部署指南.md）")
    # QQ 未运行时顺手拉起（SnowLuma 会自动注入）；失败不阻塞
    qq_exe = _g("snowluma", "qq_exe", default="")
    if qq_exe and os.path.exists(qq_exe):
        try:
            subprocess.Popen([qq_exe])
        except Exception:
            pass
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        extra["start_new_session"] = True
    try:
        subprocess.Popen([launcher], cwd=sdir, env=dict(os.environ, HD2_PROJECT_DIR=_ROOT), **extra)
    except Exception as e:
        return f"❌ 启动 SnowLuma 失败: {e}"
    if wait_port(instance.port("napcat"), 60):
        return ("✅ SnowLuma 已启动；首次启动请从其控制台日志取临时密码登录 WebUI，"
                "并在桌面 QQ 窗口完成扫码登录")
    return ("ℹ️ SnowLuma 启动中；扫码登录请在 QQ 窗口完成，"
            f"WebUI 默认 {_g('snowluma', 'webui_url', default='http://127.0.0.1:5099')}")


def start_webui() -> str:
    if port_listen(int(_g("webui", "port", default=8630))):
        instance.verified_pids("webui")
        return "✅ 当前 Web 控制台已在运行"
    py = _g("astrbot", "python", default="python")
    pyw = py
    extra = {}
    if os.name == "nt":
        pyw = py.replace("python.exe", "pythonw.exe") if py.lower().endswith("python.exe") else py
        extra["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        extra["start_new_session"] = True
    webui = os.path.join(_ROOT, "scripts", "hd2_webui.py")
    try:
        subprocess.Popen(
            [pyw, "-X", "utf8", webui],
            close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, HD2_PROJECT_DIR=_ROOT), **extra,
        )
        return "✅ Web 控制台启动中"
    except Exception as e:
        return f"⚠️ Web 控制台启动失败: {e}"


def main():
    print("=" * 46)
    print("  HD2 Bot · 一键启动")
    print("=" * 46)

    # 配置校验
    conf_path = cfg.config_path()
    if not os.path.exists(conf_path):
        print("❌ 未找到 config.json，请先复制 config.example.json 为 config.json 并填写配置。")
        sys.exit(1)
    if not _g("bot", "self_id"):
        print("⚠️ 提示：config.json 中 bot.self_id 未填写（不影响启动，但影响部分功能）")

    # 1. AstrBot
    print("\n[1/4] AstrBot ...")
    print("  " + start_astrbot())

    if instance.platform() == "napcat":
        print(start_snowluma())
        print("请在协议端（SnowLuma）WebUI 或桌面 QQ 窗口完成扫码登录；状态和发送结果需分别确认。")
    else:
        print("官方平台模式：请在 AstrBot 适配器中配置凭据；跳过协议端和 OneBot 等待。")

    # 4. WebUI + 浏览器
    print("\n[4/4] Web 控制台 ...")
    print("  " + start_webui())
    time.sleep(4)
    web_url = f"http://127.0.0.1:{int(_g('webui', 'port', default=8630))}"
    try:
        webbrowser.open(web_url)
        print(f"  🌐 已打开 {web_url}")
    except Exception:
        print(f"  🌐 请手动访问 {web_url}")

    print("\n" + "=" * 46)
    print("  启动完成！扫码登录：桌面 QQ 窗口（SnowLuma 自动注入），")
    print("  WebUI 配置见 " + _g("snowluma", "webui_url", default="http://127.0.0.1:5099"))
    print("=" * 46)


if __name__ == "__main__":
    main()
