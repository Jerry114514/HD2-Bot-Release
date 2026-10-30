"""Restart only the console process that requested this helper."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(os.environ.get("HD2_PROJECT_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))
from scripts import instance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", required=True, type=int)
    args = parser.parse_args()
    time.sleep(1)
    pids = instance.verified_pids("webui")
    if pids != [args.parent_pid]:
        raise RuntimeError("控制台进程已改变，拒绝重启其他实例")
    instance.stop("webui")
    time.sleep(1)
    subprocess.Popen([sys.executable, str(ROOT / "scripts/hd2_webui.py")],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     env=dict(os.environ, HD2_PROJECT_DIR=str(ROOT)))


if __name__ == "__main__":
    main()
