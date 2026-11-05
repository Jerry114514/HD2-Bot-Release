"""Manage only the configured instance that actually owns the selected port."""
import json
import ntpath
import shlex
import subprocess
import sys
from urllib.parse import urlsplit
import hd2_config as cfg


def platform():
    mode = cfg.norm_platform(cfg.get("bot", "platform", default="napcat"))
    if mode not in ("napcat", "qq_official"):
        raise ValueError("bot.platform 必须为 napcat(=OneBot协议端通道,接受别名 snowluma/onebot) 或 qq_official")
    return mode


def port(kind):
    if kind == "napcat":
        # OneBot 协议端（SnowLuma）WebUI 端口，默认 5099
        return urlsplit(cfg.get("snowluma", "webui_url", default="http://127.0.0.1:5099")).port or 80
    return int(cfg.get("astrbot", "webui_port", default=6185) if kind == "astrbot" else cfg.get("webui", "port", default=8630))


def normalized(path):
    path = str(path or "").strip('"')
    return ntpath.normcase(ntpath.normpath(path)) if path else ""


def configured(path):
    if not path:
        return ""
    return normalized(path if ntpath.isabs(path) else cfg.resolve(path))


def _is_snowluma(record):
    """OneBot 协议端（SnowLuma）进程判定：exe 在协议端目录下，或命令行含 snowluma/index.mjs"""
    argv = record.get("Argv")
    if argv is not None:
        parts = [str(v) for v in argv]
    else:
        try:
            parts = shlex.split(record.get("CommandLine") or "", posix=False)
        except ValueError:
            return False
    joined = " ".join(parts).lower()
    if "snowluma" in joined or "index.mjs" in joined:
        return True
    sdir = configured(cfg.get("snowluma", "dir", default="")).lower()
    return bool(sdir) and sdir in normalized(record.get("ExecutablePath") or "").lower()


def matches(record, kind):
    exe = normalized(record.get("ExecutablePath"))
    if not exe:
        return False
    if kind == "napcat":
        # 协议端端口（5099）由 SnowLuma 的 node 进程持有；QQ.exe 本体不监听该端口
        return _is_snowluma(record) or exe == configured(cfg.get("snowluma", "qq_exe", default=""))
    expected = cfg.get("astrbot", "exe", default="")
    if kind == "astrbot" and expected and exe == configured(expected):
        return True
    python = configured(cfg.get("astrbot", "python", default=sys.executable))
    if exe not in {python, normalized(sys.executable)}:
        return False
    try:
        args = [normalized(v) for v in shlex.split(record.get("CommandLine") or "", posix=False)]
    except ValueError:
        return False
    script = cfg.resolve("scripts/hd2_webui.py") if kind == "webui" else ntpath.join(cfg.get("astrbot", "app_dir", default=""), "launch_backend.py")
    return configured(script) in args


def select(records, owners, kind):
    by_pid = {int(r["ProcessId"]): r for r in records}
    selected = []
    for pid in sorted(set(map(int, owners))):
        record = by_pid.get(int(pid))
        if record is None or not matches(record, kind):
            raise RuntimeError("目标端口由其他进程占用，拒绝结束进程")
        selected.append(int(pid))
    return selected


def _query(command):
    run = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=15, check=True)
    value = json.loads(run.stdout) if run.stdout.strip() else []
    return value if isinstance(value, list) else [value]


def verified_pids(kind):
    owners = _query(f"Get-NetTCPConnection -LocalPort {port(kind)} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess | ConvertTo-Json -Compress")
    if not owners:
        return []
    ids = sorted({int(pid) for pid in owners})
    clause = " or ".join(f"ProcessId={pid}" for pid in ids)
    records = _query('Get-CimInstance Win32_Process -Filter "' + clause + '" | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress')
    return select(records, owners, kind)


def stop(kind):
    pids = verified_pids(kind)
    if pids:
        subprocess.run(["powershell", "-NoProfile", "-Command", "Stop-Process -Id " + ",".join(map(str,pids)) + " -Force -ErrorAction Stop"], check=True, capture_output=True, timeout=15)
    return pids
