"""Copy the ten plugins, keeping runtime configuration and existing code recoverable."""
import argparse
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parent


def sync(target, dry_run=False, update=False):
    target = Path(target).resolve()
    backup = target / ".hd2-sync-backups" / uuid4().hex
    copied = []
    for source in sorted((ROOT / "plugins").rglob("*")):
        if not source.is_file() or {"cache", "__pycache__"}.intersection(source.parts):
            continue
        relative = source.relative_to(ROOT / "plugins")
        dest = (target / relative).resolve()
        if not dest.is_relative_to(target):
            raise ValueError("部署目标包含指向目录外的链接")
        if dest.exists():
            if dest.name == "welcome_config.json" or source.read_bytes() == dest.read_bytes():
                continue
            if not update:
                raise ValueError(f"已有不同文件 {relative}；预览后使用 --update，旧文件将备份")
        copied.append(str(relative))
    # Validate the entire copy set before mutating the target.
    if not dry_run:
        for relative in copied:
            source, dest = ROOT / "plugins" / relative, target / relative
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                old = backup / relative
                old.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(dest,old)
            with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as stream:
                tmp = Path(stream.name)
            try:
                shutil.copy2(source,tmp)
                tmp.replace(dest)
            finally:
                tmp.unlink(missing_ok=True)
    return copied


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target",required=True,type=Path,help="AstrBot data/plugins directory")
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--update",action="store_true")
    args=parser.parse_args()
    files=sync(args.target,args.dry_run,args.update)
    print(("将同步" if args.dry_run else "已同步") + f" {len(files)} 个文件；请在 AstrBot 重载插件。")


if __name__=="__main__":main()
