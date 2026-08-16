# -*- coding: utf-8 -*-
"""伪装群成员消息注入测试：直接连 AstrBot OneBot WS(6199)，模拟群成员发指令，打印机器人回复。

用法：
  python napcat_ws_inject.py "指令文本" [群号]
  示例：
    python napcat_ws_inject.py "战报"                # 触发战况速报
    python napcat_ws_inject.py "查表"                # 触发 /查表
    python napcat_ws_inject.py "查表 孢裂变种"
    python napcat_ws_inject.py "查表 星区 巴纳德"

原理：
- NapCat reportSelfMessage=false，主号自己发的群消息不会上报 AstrBot → 无法用推送测试响应
- 本脚本伪装成一个 WS 客户端连接 AstrBot 的 6199 反向 WS 服务端（NapCat 同款角色）
- 注入一条群消息事件（user_id 用假成员号，避免被 self 过滤拦截）
- AstrBot 处理后会通过 WS 下发 API 调用（send_group_msg），打印出来即机器人回复
"""
import json, sys, io, time, random, os
import websocket

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

WS_URL = _g("napcat", "ws_url", d="ws://127.0.0.1:6199/ws")
SELF_ID = str(_g("bot", "self_id", d=""))   # 机器人 QQ（NapCat 登录号）
DEFAULT_GROUP = int(_g("bot", "default_group", d=0))
FAKE_USER = int(_g("inject", "fake_user", d=2428164570))  # 伪装的群成员 user_id（≠ 机器人自己）
FAKE_NAME = _g("inject", "fake_name", d="绝地潜兵·测试员")

if not SELF_ID:
    print("❌ 未配置 bot.self_id，请在 config.json 填写机器人 QQ 号")
    sys.exit(1)


def build_event(group_id: int, text: str, at: bool = True) -> dict:
    msg = []
    if at:
        msg.append({"type": "at", "data": {"qq": SELF_ID}})
    msg.append({"type": "text", "data": {"text": text}})
    raw = f"[CQ:at,qq={SELF_ID}] {text}" if at else text
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": random.randint(1000000, 9999999),
        "group_id": group_id,
        "user_id": FAKE_USER,
        "message": msg,
        "raw_message": raw,
        "self_id": int(SELF_ID),
        "sender": {"user_id": FAKE_USER, "nickname": FAKE_NAME, "card": "", "role": "member"},
        "time": int(time.time()),
    }


def msg_to_text(message) -> str:
    """把 OneBot 消息（array 或 str）转成可读文本"""
    if isinstance(message, str):
        return message
    parts = []
    for seg in message or []:
        t = seg.get("type", "")
        d = seg.get("data", {}) or {}
        if t == "text":
            parts.append(d.get("text", ""))
        elif t == "at":
            qq = d.get("qq", "")
            parts.append(f"@{qq}")
        elif t == "image":
            parts.append("[图片]")
        else:
            parts.append(f"[{t}]")
    return "".join(parts)


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "战报"
    group_id = DEFAULT_GROUP
    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        group_id = int(sys.argv[2])
    if not text.startswith("/"):
        text = "/" + text

    evt = build_event(group_id, text)
    print(f"WS 连接 {WS_URL} ...")
    # ⚠️ X-Self-ID 必须用独立测试 ID（不能与机器人 self_id 相同）：
    # aiocqhttp 的 API 客户端表按 X-Self-ID 存 dict，同 ID 会覆盖/删除 NapCat 的注册，
    # 导致 NapCat 发送通道丢失（ApiNotAvailable）
    ws = websocket.create_connection(
        WS_URL, timeout=35,
        header=[
            f"X-Client-Role: universal",
            f"X-Self-ID: hd2-inject-test",
        ],
    )
    ws.send(json.dumps(evt))
    print(f"已注入: 群{group_id} 成员{FAKE_USER} 指令={text!r}")
    print("等待机器人回复（最长 30 秒）...")

    replies = []
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            ws.settimeout(max(0.5, deadline - time.time()))
            frame = ws.recv()
        except websocket.WebSocketTimeoutException:
            break
        except Exception as e:
            print(f"[ws 断开] {e}")
            break
        try:
            data = json.loads(frame)
        except Exception:
            continue
        action = data.get("action")
        if action == "send_group_msg":
            params = data.get("params", {}) or {}
            replies.append(msg_to_text(params.get("message")))
            # 回执，让 AstrBot 认为发送成功
            ws.send(json.dumps({
                "status": "ok", "retcode": 0,
                "data": {"message_id": random.randint(1000000, 9999999)},
                "echo": data.get("echo"),
            }))
        elif action == "get_group_member_info":
            # AstrBot 处理 @ 时需要查群名片，返回假数据让它继续
            params = data.get("params", {}) or {}
            ws.send(json.dumps({
                "status": "ok", "retcode": 0,
                "data": {
                    "group_id": params.get("group_id"),
                    "user_id": params.get("user_id"),
                    "nickname": FAKE_NAME,
                    "card": "",
                    "role": "member",
                },
                "echo": data.get("echo"),
            }))
        elif action:
            print(f"[api] {action} {json.dumps(data.get('params', {}), ensure_ascii=False)[:200]}")
            ws.send(json.dumps({
                "status": "ok", "retcode": 0,
                "data": {},
                "echo": data.get("echo"),
            }))

    ws.close()
    if replies:
        print("\n" + "=" * 40)
        print(f"✅ 机器人回复了 {len(replies)} 条：")
        for i, r in enumerate(replies, 1):
            print(f"\n----- 回复 {i} -----\n{r}")
        print("=" * 40)
    else:
        print("\n⚠️ 未收到机器人回复（可能：指令未匹配 / 白名单 / 唤醒条件）")


if __name__ == "__main__":
    main()
