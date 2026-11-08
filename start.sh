#!/usr/bin/env bash
# HD2 Bot Linux 一键启动器（对应 Windows 的 start.bat）
# 读取 config.json 中 astrbot.python 作为解释器，启动 scripts/launch.py
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

echo "============================================"
echo "  HD2 Bot Launcher (AstrBot + WebUI)"
echo "============================================"

if [ ! -f config.json ]; then
    echo "[WARN] config.json not found."
    echo "  Copy config.example.json to config.json first, then fill in:"
    echo "  - bot.self_id / bot.groups"
    echo "  - astrbot.exe / astrbot.python（Linux 下 exe 指向可执行启动脚本）"
    echo "  - llm.api_key"
    echo ""
    exit 1
fi

# 从 config.json 读取 python 解释器（JSON 解析失败或未配置时回退 python3）
PY="$(python3 - <<'EOF'
import json
try:
    with open("config.json", encoding="utf-8-sig") as f:
        conf = json.load(f)
    py = (conf.get("astrbot") or {}).get("python") or ""
    print(py if py else "python3")
except Exception:
    print("python3")
EOF
)"

# 配置的解释器不可用时回退 python3（兼容照抄 Windows 配置的情况）
if ! command -v "$PY" >/dev/null 2>&1 && [ ! -x "$PY" ]; then
    echo "[WARN] 配置的 python 解释器不可用: $PY，回退 python3"
    PY="python3"
fi

echo "[launch] Python: $PY"
exec "$PY" -X utf8 scripts/launch.py
