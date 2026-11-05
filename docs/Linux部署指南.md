# Linux 部署指南（Linux-Branch）

> 本分支（`Linux-Branch`）在 main 的基础上做 Linux 适配：**字体跨平台解析、启动链路跨平台化**。
> Windows 部署请看 [部署指南](部署指南.md) 与 README；本文只讲差异部分。

## 0. 前置要求

| 组件 | 说明 |
| --- | --- |
| Linux x86_64 / arm64 | Debian/Ubuntu、Fedora、Arch 等主流发行版均可 |
| Python 3.11+ | 系统包或 AstrBot 自带均可 |
| Pillow | 图卡渲染依赖（`pip install pillow`） |
| SnowLuma（可选协议端） | QQ 接入需要；推荐官方 Docker 镜像，或 linux-x64 包 ./launcher.sh |
| AstrBot v4.x | QQ 机器人框架，官方支持 Linux（CLI / Docker / 源码） |

## 1. 字体（用户反馈的核心修复）

### 问题

main 分支的三处渲染源码硬编码了 Windows 字体路径，Linux 下图片卡渲染必然失败（回退纯文本）：

- `plugins/astrbot_plugin_hd2_war_report/report_card.py`（战报卡）
- `plugins/astrbot_plugin_hd2_roll/roll_card.py`（配装卡）
- `scripts/render_report_card.py`（样图渲染器）

### 本分支的修复

新增共享解析器 `plugins/astrbot_plugin_hd2_common/fonts.py`，三个渲染入口统一改走它。
解析顺序：

1. 环境变量 `HD2_FONT` / `HD2_FONT_BOLD`（显式指定，最高优先）
2. 平台候选清单：Windows（微软雅黑 → 黑体 → 宋体 → Arial）/ Linux（文泉驿微米黑 → 正黑 → 思源黑体 SC → Noto CJK SC → Droid Sans Fallback）/ macOS（苹方 → 冬青黑体）
3. Linux 下遍历字体目录按文件名特征匹配（wqy / noto cjk / source han / sarasa 等）
4. `fc-match`（fontconfig）动态发现 zh-capable 字体
5. 兜底返回首个候选（PIL 抛错时由调用方回退文本输出，不崩进程）

### Linux 需要装一个 CJK 字体

任选其一（有 root 权限推荐前两个）：

```bash
# Debian / Ubuntu
sudo apt install fonts-wqy-microhei        # 文泉驿微米黑（候选清单第 1 位）
# 或
sudo apt install fonts-noto-cjk            # Noto Sans CJK

# Fedora
sudo dnf install wqy-microhei-fonts google-noto-sans-cjk-fonts

# Arch
sudo pacman -S wqy-microhei noto-fonts-cjk
```

无 root 权限时把字体文件放到用户目录即可被解析器扫描到：

```bash
mkdir -p ~/.local/share/fonts
cp msyh.ttc ~/.local/share/fonts/    # 或任意 CJK ttf/ttc/otf
```

也可用环境变量直接指定（无需扫描）：

```bash
export HD2_FONT=/path/to/your/font.ttc
# export HD2_FONT_BOLD=/path/to/bold.ttc   # 不设则常规体代替加粗
```

### 验证

```bash
python3 -c "
import sys; sys.path.insert(0, 'plugins')
from astrbot_plugin_hd2_common.fonts import resolve_font_pair
print(resolve_font_pair())
"
```

输出存在的字体文件路径即成功；然后跑 `/战报` 或 `python3 scripts/render_report_card.py out.png` 看渲染是否正常。

## 2. 启动方式

Windows 的 `start.bat` 对应 Linux 的 **`start.sh`**：

```bash
chmod +x start.sh
./start.sh
```

行为与 start.bat 一致：校验 config.json → 读取 `astrbot.python` 指定的解释器 → 启动 `scripts/launch.py`。
配置的解释器不可用时自动回退 `python3`。

## 3. config.json 差异点

对照 `config.example.json`，Linux 部署需注意：

| 配置项 | Windows 习惯值 | Linux 建议 |
| --- | --- | --- |
| `astrbot.python` | `D:/AstrBot/backend/python/python.exe` | `/usr/bin/python3` 或 venv 内 python |
| `astrbot.exe` | 桌面版 exe 路径 | 指向**可执行启动脚本**（AstrBot CLI 或自建 wrapper.sh），如 `~/astrbot/start.sh` |
| `snowluma.dir` | Windows 解压目录（含 `launcher.bat`） | Linux 解压目录（含 `launcher.sh`）；Docker 部署时可留空（见下） |
| `snowluma.webui_url` | `http://127.0.0.1:5099` | 不变（Docker 映射到本机端口即可） |
| `snowluma.onebot_http` | `http://127.0.0.1:3000` | 不变（控制台推送走 OneBot HTTP） |
| `snowluma.access_token` | SnowLuma 数据目录 config/onebot*.json 的 accessToken | 同左 |

## 4. 协议端 SnowLuma（Linux 用 Docker）

协议端为 **SnowLuma**（NapCat 同团队新一代 OneBot v11 实现）。它通过 hook 注入真实 QQ 进程，
扫码登录在 QQ 窗口完成；Linux 无桌面时用官方镜像自带的 noVNC 扫码：

```bash
docker run -d --name snowluma \
  --restart unless-stopped \
  --shm-size=1g \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  -p 6081:6081 -p 5099:5099 -p 3000:3000 -p 3001:3001 \
  -v qq-gateway-data:/app/data \
  -v qq-client-config:/app/.config \
  -v qq-client-data:/app/.local/share \
  motricseven7/snowluma:latest
```

> `--cap-add=SYS_PTRACE` 与 `--security-opt seccomp=unconfined` **不能省**（注入需要）；
> QQ（Chromium）需要 `--shm-size=1g`。

三步完成接入：

1. **扫码登录**：浏览器打开 `http://127.0.0.1:6081/`（noVNC），密码在容器日志：
   `docker logs snowluma 2>&1 | grep -E "远程桌面密码:|remote desktop password:" | tail -n 1`，
   在远程桌面里的 QQ 窗口用手机 QQ 扫码
2. **WebUI 配置**：打开 `http://127.0.0.1:5099/`（首次临时密码在日志中打印一次），在 OneBot
   网络配置里添加 **wsClients 反向 WS** 指向 AstrBot：`ws://127.0.0.1:6199/ws`（role: Universal）
3. **回填 accessToken**：把 SnowLuma 生成的 accessToken 填入项目 `config.json` 的
   `snowluma.access_token`；其 onebot 配置的 `messageFormat` 设为 `string`（控制台推送用 CQ 码字符串）

> 不用 Docker 也可以：下载 SnowLuma 的 `linux-x64` 包解压后 `./launcher.sh`（启动器已支持直接拉起），
> 或用一键安装脚本；只要 `snowluma.webui_url` 端口可达，控制台的"已监听"检测就能工作。

## 5. 跨平台化的技术点（本分支改动清单）

| 文件 | 改动 |
| --- | --- |
| `plugins/astrbot_plugin_hd2_common/fonts.py` | **新增**：共享 CJK 字体解析器（环境变量 → 平台候选 → 目录扫描 → fc-match → 兜底） |
| `plugins/astrbot_plugin_hd2_war_report/report_card.py` | 硬编码候选清单改走共享解析器 |
| `plugins/astrbot_plugin_hd2_roll/roll_card.py` | 删除硬编码 `C:\Windows\Fonts` 常量，改走共享解析器 |
| `scripts/render_report_card.py` | 同上（保留独立运行时的本地候选回退） |
| `plugins/astrbot_plugin_hd2_planet_info/main.py` | DeepSeek 翻译配置读取路径 `C:\Users\<USERNAME>\...` → `~/.astrbot/data/cmd_config.json`（环境变量 `HD2_CMD_CONFIG` 优先不变） |
| `scripts/launch.py` | 端口探测 PowerShell → 纯 socket；进程列表 → tasklist/ps；WebUI 后台启动参数分平台（DETACHED_PROCESS / start_new_session）；start_snowluma 双平台统一（launcher.bat / launcher.sh） |
| `scripts/instance.py` | 新增 /proc 实现的端口归属校验与 SIGTERM 停止；Windows 路径保持 PowerShell 原逻辑；路径归一化改 `os.path`（双平台各自正确） |
| `scripts/hd2_webui.py` | 端口监听/WS 连接检测跨平台；进程列表跨平台；「协议端WebUI」按钮 xdg-open/startfile |
| `start.sh` | **新增**：Linux 启动器 |
| `.gitattributes` | 补 `*.sh text eol=lf`（防止 Windows 端提交把 CRLF 带进 shell 脚本） |

### 已知边界（Linux 下与 Windows 的行为差异）

- 协议端已从 NapCat 迁移到 SnowLuma：旧 `napcat.*` 配置键失效，请改用 `snowluma.*`（webui 5099 / onebot_http 3000 / access_token）。

- 协议端进程级管理依赖 snowluma.dir 指向本地解压目录（含 launcher.sh）；Docker 部署时请直接操作容器（docker restart snowluma），控制台状态监测（端口/WS 连接）正常。
- `instance.stop()` 在 Linux 下只停止**与配置匹配**的进程；无权限读取的他人进程会被跳过，绝不误杀。
- 加粗渲染：文泉驿等无独立 Bold 字重时用常规体渲染（与 Windows 雅黑加粗观感略有差异）。

## 6. 故障排查

| 现象 | 处理 |
| --- | --- |
| 图片卡回退纯文本 | 看 AstrBot 日志里 PIL 报错；确认 `resolve_font_pair()` 返回的路径存在；装 CJK 字体 |
| 渲染出的中文是方框 | 字体存在但不含 CJK（命中了 DejaVu 之类）→ 用 `HD2_FONT` 显式指定 |
| start.sh 报 `\r` 相关错误 | 说明文件被转成 CRLF；`dos2unix start.sh` 或重新 checkout（`.gitattributes` 已兜底） |
| WebUI"未监听"但服务明明在跑 | 服务绑定的是 `::1`/IPv6 而 start.sh 探测 127.0.0.1 → 确认服务绑定 `0.0.0.0` 或 `127.0.0.1` |
| 重启 AstrBot 按钮无效 | `astrbot.exe` 必须指向真实可执行文件路径（与运行中进程一致），见 §3 |
