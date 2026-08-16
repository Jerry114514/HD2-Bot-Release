# -*- coding: utf-8 -*-
"""NapCat WebUI 快速推送测试脚本 v3
用法：
  python napcat_send2.py "消息内容" [群号]        # 直接传消息文本
  python napcat_send2.py @文件路径 [群号]          # 从文件读消息（@ 前缀表示文件）
  python napcat_send2.py                          # 默认发 usage_guide_v4.txt 到默认群

说明：这是「发消息到群」的推送测试（走 NapCat WebUI API）。
注意：NapCat 配置 reportSelfMessage=false，主号自己发的消息不会上报给 AstrBot，
     所以这种方式发出去的指令机器人收不到、不会触发回复。
     要测试「机器人响应」请用 napcat_ws_inject.py（伪装群成员注入 WS）。
"""
import json, hashlib, urllib.request, urllib.error, sys, io, os
import os as _os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 配置化：环境变量 HD2_PROJECT_DIR 指向项目根（含 config.json）
_ROOT = os.environ.get("HD2_PROJECT_DIR")
if _ROOT and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    import hd2_config as _cfg
    def _g(*k, d=None):
        return _cfg.get(*k, default=d)
except Exception:
    def _g(*k, d=None):
        return d

BASE = _g("napcat", "webui_url", d="http://127.0.0.1:6099").rstrip("/")
TOKEN = _g("napcat", "token", d="")
DEFAULT_GROUP = int(_g("bot", "default_group", d=0))
DEFAULT_MSG_FILE = _g("paths", "guide", d="tables/群使用说明.txt")
if not _os.path.isabs(DEFAULT_MSG_FILE):
    DEFAULT_MSG_FILE = _os.path.join(_ROOT or _os.path.dirname(_os.path.abspath(__file__)), DEFAULT_MSG_FILE)

if not TOKEN:
    print("❌ 未配置 napcat.token，请在 config.json 填写 NapCat WebUI token")
    sys.exit(1)


def _req(path, method="GET", payload=None, headers=None):
    url = BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def login() -> str:
    pw = hashlib.sha256((TOKEN + ".napcat").encode("utf-8")).hexdigest()
    code, body = _req("/api/auth/login", "POST", {"hash": pw})
    if code == 200:
        try:
            d = json.loads(body)
            return d.get("data", {}).get("Credential", "")
        except Exception:
            return ""
    return ""


def send(credential: str, group_id: int, message: str) -> bool:
    headers = {"Authorization": f"Bearer {credential}"}
    payload = {"action": "send_group_msg", "params": {"group_id": group_id, "message": message}}
    code, body = _req("/api/Debug/call/debug-primary", "POST", payload, headers)
    try:
        d = json.loads(body)
        inner = d.get("data") or {}
        return inner.get("status") == "ok" or inner.get("retcode") == 0
    except Exception:
        return False


if __name__ == "__main__":
    msg = None
    group_id = DEFAULT_GROUP
    args = [a for a in sys.argv[1:] if a]
    for a in args:
        if a.isdigit():
            group_id = int(a)
        elif a.startswith("@"):
            fp = a[1:]
            if not os.path.exists(fp):
                print(f"RESULT: FILE_NOT_FOUND {fp}")
                sys.exit(1)
            with open(fp, encoding="utf-8") as f:
                msg = f.read()
        else:
            msg = a
    if msg is None:
        if os.path.exists(DEFAULT_MSG_FILE):
            with open(DEFAULT_MSG_FILE, encoding="utf-8") as f:
                msg = f.read()
        else:
            msg = "推送测试"
    cred = login()
    if not cred:
        print("RESULT: LOGIN_FAILED")
        sys.exit(1)
    ok = send(cred, group_id, msg)
    print(f"RESULT: {'OK' if ok else 'FAILED'} group={group_id} len={len(msg)}")
    sys.exit(0 if ok else 1)
