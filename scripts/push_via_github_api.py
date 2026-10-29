# -*- coding: utf-8 -*-
"""GitHub API 链式推送（网络受限通道）

github.com:443 直连被墙而 api.github.com 可达时，用 Git Data API 把
本地领先 origin/main 的提交逐个重建到远端：增量 blob 上传 → base_tree
建树 → 按原始作者/时间戳/时区复刻提交对象（sha 必须与本地一致，否则中止）。
支持合并提交（多父）。用法：

    GH_PAT=github_pat_xxx python scripts/push_via_github_api.py [分支名=main]

远端 main 若已含本地没有的提交（分叉），先在本地做合并再跑本脚本。
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = "Jerry114514/HD2-Bot-Release"
API = "https://api.github.com"
BRANCH = sys.argv[1] if len(sys.argv) > 1 else "main"
TOKEN = os.environ.get("GH_PAT")
if not TOKEN:
    sys.exit("缺少 GH_PAT 环境变量")


def api(method, path, payload=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + TOKEN,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "api-push"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            if e.code in (500, 502, 503) and attempt < 2:
                time.sleep(2)
                continue
            print("HTTP %s %s -> %s" % (method, path, e.code), e.read()[:300])
            raise
        except Exception:
            if attempt < 2:
                time.sleep(2)
                continue
            raise


def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, check=True).stdout


def git_out(*args):
    return git(*args).decode("utf-8", "replace").strip()


def iso_at(unix, off):
    sign = 1 if off.startswith("+") else -1
    tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
    return datetime.fromtimestamp(int(unix), tz).isoformat()


# 待推提交（旧→新）与链起点
commits = git_out("rev-list", "--reverse", "origin/main..main").split()
if not commits:
    print("本地与远端已同步，无事可做")
    sys.exit(0)
first_parent_of_chain = git_out("rev-parse", commits[0] + "^")   # 链的父（远端应有）

remote_main = api("GET", "/repos/%s/git/ref/heads/%s" % (REPO, BRANCH))["object"]["sha"]
base_tree = api("GET", "/repos/%s/git/commits/%s" % (REPO, first_parent_of_chain))["tree"]["sha"]
parent = first_parent_of_chain
print("远端 %s: %s | 链起点父: %s | 待推: %d" %
      (BRANCH, remote_main[:10], first_parent_of_chain[:10], len(commits)))

# 链起点父必须与远端 main 一致（否则分叉，先合并再推）
if first_parent_of_chain != remote_main and \
        first_parent_of_chain not in [c["sha"] for c in api(
            "GET", "/repos/%s/commits?sha=%s&per_page=30" % (REPO, BRANCH))]:
    sys.exit("FATAL: 链起点父 %s 不在远端 %s 分支上——先合并远端再推"
             % (first_parent_of_chain[:10], BRANCH))

uploaded = set()
tip = None
for i, c in enumerate(commits, 1):
    raw = git("cat-file", "commit", c).decode("utf-8", "replace")
    head, _, msg = raw.partition("\n\n")
    parents = [l.split(" ")[1] for l in head.split("\n") if l.startswith("parent ")]
    meta = git_out("show", "-s", "--format=%an%x01%ae%x01%cn%x01%ce", c).split("\x01")
    tz_a = [l for l in head.split("\n") if l.startswith("author ")][0].rsplit(" ", 1)[-1]
    tz_c = [l for l in head.split("\n") if l.startswith("committer ")][0].rsplit(" ", 1)[-1]
    at = [l for l in head.split("\n") if l.startswith("author ")][0].rsplit(" ", 2)[-2]
    ct = [l for l in head.split("\n") if l.startswith("committer ")][0].rsplit(" ", 2)[-2]

    diff = git_out("-c", "core.quotePath=false", "diff-tree", "-r",
                   "--no-renames", "--no-commit-id", "--diff-filter=ADCMR", c)
    entries, pending = [], []
    for line in diff.splitlines():
        meta_d, path = line.split("\t", 1)
        tok = meta_d.split(" ")          # [":旧mode", 新mode, 旧sha, 新sha, 状态]
        old, new, mode = tok[2], tok[3], tok[1]
        path = path.strip('"')
        if new == "0" * 40:              # 删除：sha=null
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        elif new in uploaded:
            entries.append({"path": path, "mode": mode, "type": "blob", "sha": new})
        else:
            pending.append((new, path))
            entries.append(None)
    for j, e in enumerate(entries):
        if e is None:
            sha, path = pending.pop(0)
            content = subprocess.run(["git", "cat-file", "blob", sha],
                                     capture_output=True, check=True).stdout
            b = api("POST", "/repos/%s/git/blobs" % REPO,
                    {"content": base64.b64encode(content).decode(),
                     "encoding": "base64"})
            assert b["sha"] == sha, "blob sha 不一致: %s" % path
            uploaded.add(sha)
            entries[j] = {"path": path, "mode": "100644", "type": "blob", "sha": sha}
    # GitRPC 可见性延迟防护：确认所有新 blob 可读再建树
    for e in entries:
        if e and e["sha"]:
            for attempt in range(10):
                try:
                    api("GET", "/repos/%s/git/blobs/%s" % (REPO, e["sha"]))
                    break
                except urllib.error.HTTPError as ex:
                    if ex.code in (404, 422) and attempt < 9:
                        time.sleep(3)
                    else:
                        raise
    tree = None
    for attempt in range(10):                    # BadObjectState 重试（RPC 可见性延迟）
        try:
            tree = api("POST", "/repos/%s/git/trees" % REPO,
                       {"base_tree": base_tree, "tree": entries})
            break
        except urllib.error.HTTPError as ex:
            if ex.code == 422 and attempt < 9:
                print("  tree 422 重试 %d..." % (attempt + 1))
                time.sleep(5)
            else:
                raise
    local_tree = git_out("rev-parse", c + "^{tree}")
    if tree["sha"] != local_tree:
        sys.exit("FATAL: tree sha 不一致 @ %s\n  api=%s\n  git=%s"
                 % (c, tree["sha"], local_tree))
    commit = api("POST", "/repos/%s/git/commits" % REPO, {
        "message": msg,
        "tree": tree["sha"],
        "parents": parents,
        "author": {"name": meta[0], "email": meta[1], "date": iso_at(at, tz_a)},
        "committer": {"name": meta[2], "email": meta[3], "date": iso_at(ct, tz_c)},
    })
    if commit["sha"] != c:
        sys.exit("FATAL: commit sha 不一致 @ %s\n  api=%s\n  git=%s"
                 % (c, commit["sha"], c))
    base_tree, parent, tip = tree["sha"], commit["sha"], commit["sha"]
    print("[%2d/%d] %s ✓ %s" % (i, len(commits), c[:10],
                                git_out("log", "-1", "--format=%s", c)[:44]))

try:
    api("PATCH", "/repos/%s/git/refs/heads/%s" % (REPO, BRANCH), {"sha": tip, "force": False})
except urllib.error.HTTPError as e:
    if e.code == 422:
        print("NON-FF：远端 %s 分叉（分叉提交已全部上传）。" % BRANCH)
        print("处理：以 %s 为内容的合并提交（第二父=远端当前 tip）后即可快进。" % tip[:10])
        print("MERGE_TIP=%s" % tip)
        sys.exit(0)
    raise
final = api("GET", "/repos/%s/git/ref/heads/%s" % (REPO, BRANCH))["object"]["sha"]
print("远端 %s = %s | 本地 = %s" % (BRANCH, final[:10], git_out("rev-parse", BRANCH)[:10]))
print("DONE" if final == git_out("rev-parse", BRANCH) else "MISMATCH!")
