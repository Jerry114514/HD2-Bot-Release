"""Offline checks and optional configuration preflight; never starts or sends."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))


def preflight(path):
    import hd2_config as cfg
    import os
    os.environ["HD2_CONFIG"] = str(path)
    value = cfg.load_config(force=True)
    mode = value["bot"]["platform"]
    if mode not in ("napcat", "qq_official"):
        raise ValueError("bot.platform 无效")
    if mode == "napcat" and not value["napcat"]["token"]:
        raise ValueError("NapCat 模式缺少 token；请在本机配置，勿提交凭据")
    if not value["astrbot"].get("data_dir"):
        raise ValueError("请指定 astrbot.data_dir")
    node = value.get("runtime", {}).get("node", "node")
    run = subprocess.run([node,"--version"],capture_output=True,text=True,check=True)
    if int(run.stdout.lstrip("v").split(".")[0]) < 22:
        raise ValueError("需要 Node.js 22 或更高版本")
    print("配置结构与 Node 运行时检查通过（未验证凭据或平台连接）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.config:
        preflight(args.config)
        return
    for path in [*ROOT.glob("*.py"), *ROOT.joinpath("plugins").rglob("*.py"), *ROOT.joinpath("scripts").glob("*.py")]:
        ast.parse(path.read_text(encoding="utf-8-sig"),filename=str(path))
    subprocess.run([sys.executable,"-X","utf8","-m","unittest","discover","-s","tests","-v"],cwd=ROOT,check=True)


if __name__ == "__main__":
    main()
