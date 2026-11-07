"""Append-only, bounded command diagnostics.

The log lives in the plugin data directory
(``data/plugin_data/astrbot_plugin_hd2_common/logs/commands.jsonl``), so nothing is
written to the plugin installation directory or the project root.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time
from contextlib import contextmanager
from .paths import plugin_data_dir
from .runtime import request_id

try:  # AstrBot 运行时只允许使用框架提供的 logger
    from astrbot.api import logger
except Exception:  # 离线工具/单元测试：框架不可用时退化为 stderr，不引入内置 logging
    class _StderrLogger:
        """Minimal stand-in used only when the AstrBot runtime is unavailable."""

        @staticmethod
        def _emit(message, *args, **_kwargs):
            try:
                sys.stderr.write((message % args if args else str(message)) + "\n")
            except Exception:
                pass

        debug = info = warning = error = _emit

    logger = _StderrLogger()

LOG_FILE = str(plugin_data_dir("astrbot_plugin_hd2_common", "logs", "commands.jsonl", create=False))
MAX_BYTES = 512 * 1024
_lock = threading.Lock()


@contextmanager
def _process_lock(path):
    with open(str(path)+".lock", "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.seek(0,2)==0:
                stream.write(b"0");stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def hd2_log(stage, plugin="", text="", group="", user="", name="", detail="", cost=None,
            wake=False, platform="", source_time=None, stale=None, output_mode="", source="", data_version="", **extra):
    record = {"ts": time.time(), "time": time.strftime("%m-%d %H:%M:%S"),
              "request_id": request_id.get(), "stage": "generated" if stage == "done" else stage, "plugin": plugin,
              "text": str(text or "")[:160], "group": str(group or ""), "user": str(user or ""),
              "name": str(name or "")[:40], "detail": str(detail or "")[:300],
              "cost": round(cost, 3) if isinstance(cost, (int, float)) else None, "wake": bool(wake),
              "platform": str(platform or ""), "source_time": source_time, "stale": stale,
              "output_mode": output_mode, "source":source, "data_version":data_version}
    if isinstance(extra.get("search"), dict):
        record["search"] = {k:extra["search"][k] for k in ("status","table_status","candidate_count",
            "complete","requests","cache_hits","coalesced","elapsed_ms","warnings","stages","resolution_cache") if k in extra["search"]}
    try:
        with _lock:
            path = Path(LOG_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            with _process_lock(path):
                if path.exists() and path.stat().st_size >= MAX_BYTES:
                    os.replace(path, str(path) + ".1")
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        logger.warning("HD2 diagnostic log unavailable: %s",error)
