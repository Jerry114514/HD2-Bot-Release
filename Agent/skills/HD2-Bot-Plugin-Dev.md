---
name: HD2-Bot-Plugin-Dev
description: HD2 战报机器人的插件开发与维护技能。适用于：新增/修改 HD2 行动变量词条（变种、DSS 战略行动、环境条件等）、更新星球信息插件词库、新增 / 指令功能（如 /查表）、同步插件到 AstrBot 部署目录、验证插件加载。当用户提到"新增参数/词条"、"更新对照表"、"加个功能"、"/查表"、"插件"时使用。
version: 1.0.0
---

# HD2-Bot-Plugin-Dev

维护「绝地潜兵2 战报机器人」的 AstrBot 插件：词库更新、新指令开发、部署与验证。

## 适用环境

- AstrBot 桌面版 `<AstrBot安装目录>`（WebUI http://127.0.0.1:6185，后端日志 `<AstrBot数据目录>\logs\backend.log`）
- 插件部署目录：`<AstrBot数据目录>\data\plugins\<插件名>\`（生效位置）
- 插件开发目录：`<项目根>\<插件名>\`（源码/归档，改完要同步过去）
- 数据源 API：见文末

## 插件清单（.astrbot\data\plugins\ 下）

| 插件 | 功能 | 触发 |
| --- | --- | --- |
| astrbot_plugin_hd2_war_report | 战况速报（重要指令+新闻+战区分布） | /战报 等关键词 |
| astrbot_plugin_hd2_planet_info | 星球信息（抵抗度/行动变量/POI） | /星球名 信息 |
| astrbot_plugin_hd2_lookup | /查表（变量对照表+星区对照表） | /查表 |
| astrbot_plugin_hd2_roll | 随机战备 | /roll |
| astrbot_plugin_hd2_variant_query | 变种扫描查询（哪颗星球有该变种） | /变种名 |
| astrbot_plugin_hd2_guard | 指令守卫（非 / 消息拦截，关 LLM 聊天） | — |

## 对照表文件（数据源，用户维护）

- 变量对照表：`<项目根>\HD2行动变量对照表.md`（分类=`## `，参数=表格行 |原文|译文|描述|）
- 星区对照表：`<项目根>\星图对照表_修正版.md`（|星区|英文|中文|）

用户更新对照表后，/查表 插件动态读取无需重启；但 planet_info 插件词库是硬编码，需要手动同步。

## 新增词条 → 更新 planet_info 词库

流程：
1. 用户在对照表填好新词条翻译 → 把 `VARIANT_CN` 按对照表补全（键=英文原文大写，值=(译文, 描述)，描述与对照表逐字一致）
2. 新分组要加：`VARIANT_GROUP_CN`（分组英文名→中文）+ `VARIANT_GROUP_ICON`（图标）+ `VARIANT_GROUP_OF`（参数→分组映射）
3. DSS 战略行动类词条加进 `TACTICAL_ACTION_CN`（不进 VARIANT_CN）
4. 环境条件类词条加进 `ENV_COND_CN`（不进 VARIANT_CN）

识别逻辑（`_translate_planet_info`）：遍历所有分组输出命中参数（不要只输出第一个分组）；`raw_params` 截取 3000 字符且截到 `POINTS OF INTEREST` 之前（防历史区误匹配）。

## 新增 / 指令功能

模式：新建独立插件目录（main.py + metadata.yaml），Star 子类 + `@filter.event_message_type(filter.EventMessageType.ALL)` 的 `keyword_trigger`，规则：
- 必须 `/` 前缀才响应（非 / 消息会被 guard 插件拦截）
- **必须过滤机器人自己发的消息**：`event.get_sender_id() == event.get_self_id()` 时 return（同号多端防自我触发）
- 数据文件用 `io.open(路径, encoding="utf-8")` 动态读取，路径写绝对路径
- metadata.yaml 格式：`---\nname: <插件名>\ndesc: <中文描述>\nauthor: local\nversion: 1.0.0\n---`

## 部署与重启

```powershell
# 1. 复制到部署目录（开发目录 -> 生效目录）
Copy-Item "<项目根>\<插件>\main.py","...\metadata.yaml" -Destination "<AstrBot数据目录>\data\plugins\<插件>\" -Force
# 2. 同步回开发目录（部署版改了就覆盖回 HD2-Bot）
# 3. 重启 AstrBot（只杀 AstrBot 后端，勿误杀 Web 控制台 8630）
#    注意：不能用 -match 'AstrBot' 过滤——WebUI 的 python.exe 路径在 <AstrBot安装目录>\ 下也会被匹配！
#    精确匹配后端启动脚本 launch_backend.py 即可：
Stop-Process -Name "astrbot-desktop-tauri" -Force -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'launch_backend' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3; Start-Process "<AstrBot安装目录>\astrbot-desktop-tauri.exe"
# 4. 等 WebUI 就绪（轮询 6185 端口）
# 5. 查日志确认插件加载：backend.log 里找 "Loading plugin <插件名>"
```

注意：重启会同时重启后端，NapCat（WS 客户端连 6199）会自动重连，但 QQ 会话可能掉线需重新扫码（见 HD2-Bot-Online skill）。

## 本地测试（不重启机器人）

### 1. 纯函数 stub 测试

插件 import astrbot 模块，无法直接跑。用 stub 方式测试解析逻辑：
- 在 `sys.modules` 注入 fake `astrbot.api.logger/event/star`（Star/Context/AstrMessageEvent/filter 空类）
- `importlib.util.spec_from_file_location` 加载 main.py
- 测试 `_parse_var_table`/`_query_param`/`_parse_starmap`/`_translate_planet_info` 等纯函数
- 参考：`<项目根>\.cowork-temp\test_lookup_and_planetinfo.py`
- PowerShell 管道会破坏 UTF-8 中文输出：设 `$env:PYTHONIOENCODING="utf-8"` 并把输出重定向到文件再读

### 2. WS 注入端到端测试（推荐，能测机器人真实响应）

脚本：`<项目根>\napcat_ws_inject.py`（需 `pip install websocket-client`）

```powershell
python napcat_ws_inject.py "战报"          # 触发战况速报
python napcat_ws_inject.py "查表 孢裂变种"   # 触发 /查表
```

原理：NapCat 配置 `reportSelfMessage=false` → 主号自己发的群消息不会上报 AstrBot，**用推送方式发指令机器人收不到**；必须伪装 WS 客户端直连 AstrBot 6199（OneBot 反向 WS 服务端）注入群消息事件。关键细节：
- 握手必须带 `X-Client-Role: universal` 和 `X-Self-ID: <机器人QQ>` 头（aiocqhttp `_handle_wsr` 强制要求，缺了返回 400）
- user_id 用假成员号（≠ 机器人自己，否则被 self 过滤拦截）；群号要在平台白名单内
- AstrBot 处理时会调 `get_group_member_info`/`get_stranger_info`/`get_msg` 等 API，**必须对每个 API 调用返回假响应**，否则处理中断
- 机器人回复 = 收到的 `send_group_msg` action 的 params.message
- 实测已验证：/战报、/查表、/查表 星区 巴纳德 均正常回复

## 踩坑记录

- PowerShell 管道 GBK 乱码：写/读含中文文件必须用 Python 脚本或 `-Encoding UTF8`，不要经管道拼接中文
- hosts 有 `#S302` 劫持 GitHub/Google → 需走 ghproxy.net 镜像；插件 API 调用若失败先怀疑网络
- /查表 的"未查询到"提示文案是用户指定的固定文案：「未查询到该参数，请输入「/查表」 或 「/查表 星系」查看参数。」
- 星区译名只认用户填的中文，未填保持英文原文，**不要自己编音译**
- LLM 翻译术语表（LLM_GLOSSARY）注入到 war_report 插件，新增术语要同步加
- NapCat `reportSelfMessage=false`：主号自己发的群消息不会上报给 AstrBot → 推送测试发指令无法触发机器人，测响应要用 WS 注入（见上）
- aiocqhttp WS 握手必须带 `X-Client-Role` 头，否则 hypercorn 返回 400

## 数据源 API

- Major Order：`https://cdn.helldiverscompanion.com/live/assignments/recent.json`
- 战区分布：`https://cdn.helldiverscompanion.com/live/extendedApiInformation/2days.json`
- 实时新闻/战役：`https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live`
- 星球列表：`https://api.helldivers2.dev/api/v1/planets`（需 X-Super-Client / X-Super-Contact 头）
