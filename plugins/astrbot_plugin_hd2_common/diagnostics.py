"""Append-only, bounded diagnostics without AstrBot dependencies."""
import json
import logging
import os
from pathlib import Path
import threading
import time
from contextlib import contextmanager
from .runtime import request_id
_ROOT = Path(os.environ.get("HD2_PROJECT_DIR") or Path(__file__).resolve().parents[2])
LOG_FILE = str(_ROOT / "temp/指令日志.jsonl")
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
        logging.getLogger(__name__).warning("HD2 diagnostic log unavailable: %s",error)

