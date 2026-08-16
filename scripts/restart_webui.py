# -*- coding: utf-8 -*-
"""WebUI 自重启助手：延迟 1 秒后杀掉占用 WebUI 端口的进程，再重新拉起 hd2_webui.py
用法：pythonw restart_webui.py（由 WebUI 的「重启服务」按钮调用，独立进程运行）
路径来自 hd2_config（config.json），环境变量 HD2_PROJECT_DIR 指定项目根。
"""
import subprocess, time, sys, os

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

time.sleep(1)

# 1) 杀占用 WebUI 端口的旧进程
PORT = int(_g("webui", "port", d=8630))
subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     f"Get-NetTCPConnection -LocalPort {PORT} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"],
    capture_output=True, timeout=20,
)
time.sleep(2)

# 2) 重新启动 WebUI（无窗口）
python_path = _g("astrbot", "python", d="")
PYTHONW = python_path.replace("python.exe", "pythonw.exe") if python_path else "pythonw"
WEBUI = os.path.join(_ROOT, "scripts", "hd2_webui.py")
try:
    subprocess.Popen(
        [PYTHONW, "-X", "utf8", WEBUI],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ, HD2_PROJECT_DIR=_ROOT),
    )
except Exception as e:
    with open(os.path.join(_ROOT, "restart_webui_err.log"), "w", encoding="utf-8") as f:
        f.write(str(e))
