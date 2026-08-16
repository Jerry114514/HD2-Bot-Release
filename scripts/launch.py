# -*- coding: utf-8 -*-
"""
HD2 Bot 一键启动器
==================
按顺序启动：AstrBot → NapCat(QQ) → Web 控制台 → 打开浏览器。
所有路径/账号从 config.json 读取（hd2_config），部署者只需配置一次。

用法：
  python scripts/launch.py
  或直接双击项目根目录的 start.bat
"""
import os
import sys
import time
import subprocess
import webbrowser

_ROOT = os.environ.get("HD2_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import hd2_config as cfg


def _g(*keys, default=None):
    return cfg.get(*keys, default=default)


def port_listen(port: int) -> bool:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() not in ("", "0")
    except Exception:
        return False


def proc_names() -> list:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.ProcessName -match 'astrbot|NapCat|QQ' } | Select-Object -ExpandProperty ProcessName"],
            capture_output=True, text=True, timeout=15,
        )
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
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
        return "✅ AstrBot 已在运行"
    exe = _g("astrbot", "exe", default="")
    if not exe or not os.path.exists(exe):
        return "⚠️ 未找到 AstrBot（config.json 的 astrbot.exe），跳过"
    subprocess.Popen([exe])
    if wait_port(int(_g("astrbot", "webui_port", default=6185)), 90):
        return "✅ AstrBot 已启动"
    return "⚠️ AstrBot 启动中，WebUI 尚未就绪（稍后可用）"


def start_napcat() -> str:
    if any(n.startswith("NapCat") for n in proc_names()):
        return "✅ NapCat 已在运行"
    ndir = _g("napcat", "dir", default="")
    qq_exe = _g("napcat", "qq_exe", default="")
    if not ndir or not os.path.exists(os.path.join(ndir, "NapCatWinBootMain.exe")):
        return "⚠️ 未找到 NapCat（config.json 的 napcat.dir），跳过"
    env = dict(os.environ)
    env["NAPCAT_PATCH_PACKAGE"] = os.path.join(ndir, "qqnt.json")
    env["NAPCAT_LOAD_PATH"] = os.path.join(ndir, "loadNapCat.js")
    env["NAPCAT_INJECT_PATH"] = os.path.join(ndir, "NapCatWinBootHook.dll")
    env["NAPCAT_LAUNCHER_PATH"] = os.path.join(ndir, "NapCatWinBootMain.exe")
    env["NAPCAT_MAIN_PATH"] = os.path.join(ndir, "napcat.mjs")
    with open(os.path.join(ndir, "loadNapCat.js"), "w", encoding="utf-8") as f:
        f.write(f'(async () => {{await import("file:///{ndir.replace(os.sep, "/")}/napcat.mjs")}})()')
    subprocess.Popen(
        [os.path.join(ndir, "NapCatWinBootMain.exe"), qq_exe, os.path.join(ndir, "NapCatWinBootHook.dll")],
        env=env, cwd=ndir,
    )
    return "✅ NapCat 启动指令已发出（等待约 25 秒生成二维码）"


def start_webui() -> str:
    if port_listen(int(_g("webui", "port", default=8630))):
        return "✅ Web 控制台已在运行"
    py = _g("astrbot", "python", default="python")
    pyw = py.replace("python.exe", "pythonw.exe") if py.lower().endswith("python.exe") else py
    webui = os.path.join(_ROOT, "scripts", "hd2_webui.py")
    try:
        subprocess.Popen(
            [pyw, "-X", "utf8", webui],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, HD2_PROJECT_DIR=_ROOT),
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

    # 2. NapCat
    print("\n[2/4] NapCat / QQ ...")
    print("  " + start_napcat())
    time.sleep(8)

    # 3. 二维码 / 连接
    print("\n[3/4] 二维码 / 连接 ...")
    qr = cfg.resolve_napcat(_g("napcat", "qrcode_rel", default="cache/qrcode.png"))
    ws_port = int(_g("astrbot", "ws_port", default=6199))
    connected = False
    for _ in range(30):
        time.sleep(3)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-NetTCPConnection -LocalPort {ws_port} -State Established -ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=15,
        )
        if out.stdout.strip() not in ("", "0"):
            connected = True
            break
    if connected:
        print("  ✅ WS 已连接（机器人上线，登录态自动恢复）")
    else:
        if os.path.exists(qr):
            print(f"  📱 请扫码登录（已打开二维码）：{qr}")
            try:
                os.startfile(qr)
            except Exception:
                pass
            # 再等 60 秒
            for _ in range(20):
                time.sleep(3)
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"(Get-NetTCPConnection -LocalPort {ws_port} -State Established -ErrorAction SilentlyContinue | Measure-Object).Count"],
                    capture_output=True, text=True, timeout=15,
                )
                if out.stdout.strip() not in ("", "0"):
                    connected = True
                    break
            print("  ✅ WS 已连接" if connected else "  ⚠️ 尚未连接，请扫码后查看 Web 控制台「状态」")

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
    print("  启动完成！若 QQ 需扫码，二维码在：")
    print("  " + (qr if os.path.exists(qr) else "(未生成)"))
    print("=" * 46)


if __name__ == "__main__":
    main()
