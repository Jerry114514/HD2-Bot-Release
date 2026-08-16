# -*- coding: utf-8 -*-
"""NapCat WebUI 通用消息发送脚本：python napcat_send2.py <消息文件路径> [群号]"""
import json, hashlib, urllib.request, urllib.error, sys, io, os

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
    if len(sys.argv) < 2:
        print("USAGE: python napcat_send2.py <msg_file> [group_id]")
        sys.exit(2)
    msg_file = sys.argv[1]
    group_id = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_GROUP
    if not os.path.exists(msg_file):
        print(f"RESULT: FILE_NOT_FOUND {msg_file}")
        sys.exit(1)
    cred = login()
    if not cred:
        print("RESULT: LOGIN_FAILED")
        sys.exit(1)
    with open(msg_file, encoding="utf-8") as f:
        msg = f.read()
    ok = send(cred, group_id, msg)
    print("RESULT:", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)
