# -*- coding: utf-8 -*-
"""OneBot v11 HTTP 通用消息发送脚本：python onebot_send_msg.py <消息文件路径> [群号]

协议端为 SnowLuma（NapCat 同团队新一代实现）：不再走 NapCat WebUI 的
debug-primary 私有接口，改用标准 OneBot v11 HTTP（默认 http://127.0.0.1:3000），
鉴权用 snowluma.access_token（Bearer）。消息以 CQ 码字符串发送，
需在 SnowLuma 的 onebot 配置里把 messageFormat 设为 string。
"""
import json, urllib.request, urllib.error, sys, io, os

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

BASE = _g("snowluma", "onebot_http", d="http://127.0.0.1:3000").rstrip("/")
TOKEN = _g("snowluma", "access_token", d="")
DEFAULT_GROUP = int(_g("bot", "default_group", d=0))

if not TOKEN:
    print("❌ 未配置 snowluma.access_token，请在 config.json 填写 SnowLuma OneBot accessToken"
          "（见其数据目录 config/onebot*.json）")
    sys.exit(1)


def send(group_id: int, message: str) -> bool:
    payload = {"group_id": group_id, "message": message, "auto_escape": False}
    req = urllib.request.Request(
        BASE + "/send_group_msg",
        data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        print(f"❌ OneBot HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        return False
    except Exception as e:
        print(f"❌ OneBot HTTP 请求失败：{type(e).__name__}")
        return False
    return body.get("retcode") == 0 or body.get("status") == "ok"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("USAGE: python onebot_send_msg.py <msg_file> [group_id]")
        sys.exit(2)
    msg_file = sys.argv[1]
    group_id = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_GROUP
    if not os.path.exists(msg_file):
        print(f"RESULT: FILE_NOT_FOUND {msg_file}")
        sys.exit(1)
    with open(msg_file, encoding="utf-8") as f:
        msg = f.read()
    ok = send(group_id, msg)
    print("RESULT:", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)
