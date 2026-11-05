# -*- coding: utf-8 -*-
"""Manage only the configured instance that actually owns the selected port.

Windows：PowerShell Get-NetTCPConnection / Get-CimInstance（原有路径）。
Linux：解析 /proc/net/tcp 找监听 socket inode → /proc/<pid>/fd 反查 pid →
       /proc/<pid>/exe + cmdline 做归属校验（无权限读取的进程安全跳过）。
"""
import json
import os
import re
import shlex
import signal
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
    # os.path 在 Windows 上即 ntpath、Linux 上即 posixpath，双平台各自归一化
    path = str(path or "").strip('"')
    return os.path.normcase(os.path.normpath(path)) if path else ""


def configured(path):
    if not path:
        return ""
    return normalized(path if os.path.isabs(path) else cfg.resolve(path))


def _exe_variants(path):
    """同一可执行文件的等价写法（含符号链接真实路径），避免误判"""
    out = {normalized(path)}
    try:
        out.add(normalized(os.path.realpath(str(path or ""))))
    except OSError:
        pass
    return out


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
    if kind == "astrbot" and expected and exe in _exe_variants(expected):
        return True
    python = configured(cfg.get("astrbot", "python", default=sys.executable))
    if exe not in (_exe_variants(python) | _exe_variants(sys.executable)):
        return False
    argv = record.get("Argv")
    if argv is not None:
        args = [normalized(v) for v in argv]
    else:
        try:
            args = [normalized(v) for v in shlex.split(record.get("CommandLine") or "", posix=False)]
        except ValueError:
            return False
    script = cfg.resolve("scripts/hd2_webui.py") if kind == "webui" else os.path.join(cfg.get("astrbot", "app_dir", default=""), "launch_backend.py")
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


# ============ Windows 实现（PowerShell） ============

def _query(command):
    run = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=15, check=True)
    value = json.loads(run.stdout) if run.stdout.strip() else []
    return value if isinstance(value, list) else [value]


def verified_pids(kind):
    if os.name != "nt":
        return _posix_verified_pids(kind)
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
        if os.name != "nt":
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        else:
            subprocess.run(["powershell", "-NoProfile", "-Command", "Stop-Process -Id " + ",".join(map(str, pids)) + " -Force -ErrorAction Stop"], check=True, capture_output=True, timeout=15)
    return pids


# ============ Linux 实现（/proc） ============

_LISTEN_STATE = "0A"  # /proc/net/tcp 的 LISTEN 状态码


def _listening_inodes(port_int):
    """从 /proc/net/tcp(6) 找出该端口的监听 socket inode 集合"""
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table, encoding="ascii") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 10 or parts[3] != _LISTEN_STATE:
                        continue
                    try:
                        local_port = int(parts[1].rsplit(":", 1)[1], 16)
                    except (ValueError, IndexError):
                        continue
                    if local_port == int(port_int):
                        inodes.add(int(parts[9]))
        except (OSError, ValueError, IndexError):
            continue
    return inodes


def _proc_record(pid):
    """读取 /proc/<pid> 构造进程记录；无权限时返回 None（安全跳过）"""
    proc_dir = f"/proc/{pid}"
    try:
        exe = os.readlink(os.path.join(proc_dir, "exe"))
        with open(os.path.join(proc_dir, "cmdline"), "rb") as f:
            argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        return {"ProcessId": pid, "ExecutablePath": exe, "Argv": argv}
    except OSError:
        return None


def _posix_verified_pids(kind):
    """/proc 版 verified_pids：只返回校验通过且与配置匹配的 pid；
    端口被无关进程占用时不抛错、不纳入结果（stop 不会误杀）。"""
    try:
        inodes = _listening_inodes(port(kind))
        if not inodes:
            return []
        owners = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            fddir = os.path.join("/proc", entry, "fd")
            try:
                fdnames = os.listdir(fddir)
            except OSError:
                continue
            for fd in fdnames:
                try:
                    target = os.readlink(os.path.join(fddir, fd))
                except OSError:
                    continue
                m = re.match(r"socket:\[(\d+)\]", target)
                if m and int(m.group(1)) in inodes:
                    owners.append(pid)
                    break
        selected = []
        for pid in sorted(set(owners)):
            record = _proc_record(pid)
            if record is None:
                continue  # 无权限读取（他人进程）：跳过，绝不误杀
            try:
                if matches(record, kind):
                    selected.append(pid)
            except Exception:
                continue
        return selected
    except Exception:
        return []
