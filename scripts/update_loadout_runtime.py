"""Vendor the published core's transitive modules and matching data from one Git commit."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PREFIX="shared/hd2-loadout/"


def vendor(repository, ref, destination):
    commit=subprocess.check_output(["git","rev-parse",ref+"^{commit}"],cwd=repository,text=True).strip()
    files={}
    def collect(path):
        path=str(PurePosixPath(path))
        if path in files:return
        if not path.startswith(PREFIX) or ".." in PurePosixPath(path).parts:
            raise ValueError("Invalid runtime import")
        content=subprocess.check_output(["git","-c","http.version=HTTP/1.1","-c","gc.auto=0","-c","maintenance.auto=false","show",commit+":"+path],cwd=repository)
        files[path]=content
        if path.endswith(".mjs"):
            import posixpath
            for relative in re.findall(r'''(?:from\s*|import\s*)['"](\.[^'"]+)['"]''',content.decode("utf-8")):
                collect(posixpath.normpath(posixpath.join(posixpath.dirname(path),relative)))
    for path in ("assessment-v2/draw.mjs","assessment-v2/equipment.json","data/catalog.json"):
        collect(PREFIX+path)
    manifest={"repository":"Jerry114514/Jerry114514.github.io","commit":commit,"files":{}}
    for path,content in files.items():
        relative=path.removeprefix(PREFIX)
        output=destination/relative
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(content)
        manifest["files"][relative]=hashlib.sha256(content).hexdigest()
    (destination/"snapshot.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-repo",required=True,type=Path)
    parser.add_argument("--ref",required=True)
    args=parser.parse_args()
    manifest=vendor(args.source_repo,args.ref,ROOT/"plugins/astrbot_plugin_hd2_roll/runtime")
    print(f"Pinned {len(manifest['files'])} runtime files from {manifest['commit']}")
