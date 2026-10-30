"""Generate the same report as /战报 without model credentials."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(os.environ.get("HD2_PROJECT_DIR") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))
from astrbot_plugin_hd2_common.commands import result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "temp/report_live2.txt")
    args = parser.parse_args()
    text = result("report").text
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print("战报已生成")


if __name__ == "__main__":
    main()
