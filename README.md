# HD2 Ministry Of Truth Bot — 绝地潜兵2 真理部战况 QQ 机器人

> 🤖 **AI 生成声明**：本项目由 **DeepSeek V4-Flash 的 Agent 能力**协作完成（代码、架构、文档均通过 Agent 生成与迭代）。项目根目录下 [`Agent/`](Agent/) 文件夹保存了开发过程中的工作报告与记忆记录（已脱敏），可供参考、二次开发与部署。

基于 AstrBot + NapCat + Onebot 的《绝地潜兵 2》战况速报 QQ 群机器人。自动抓取银河战争实时数据，提供战报、DSS 状态、战役简报、战线列表、LLM 战局分析、行动变量/星区查询、随机战备等功能，全部支持中文界面与中英文查询。

## 📜 免责声明

> ⚠️ **非官方项目**：本项目为**个人学习与技术交流**用途，与 Arrowhead Game Studios 无任何关联，未获官方授权或认可。请勿用于商业用途。
>
> ⚠️ **数据来源**：部分数据来自游戏官方 API（`api.live.prod.thehelldiversgame.com`，经个人抓包确认其公开可访问）与第三方社区 API（Helldivers Companion / helldivers2.dev）。官方接口可能随时间变化或失效，本项目不保证数据持续可用。
>
> ⚠️ **数据所有权**：游戏内容、名称、图标等版权归 Arrowhead Game Studios 所有；社区 API 数据归各提供方所有。本项目仅做技术演示。
>
> 📧 **联系我们**：若 Arrowhead 官方或相关权利方认为本项目对接口的使用不当，请通过 GitHub Issues 联系我们，我们会第一时间删除相关代码或调整实现方式。

## ✨ 功能特性

| 指令 | 说明 |
| --- | --- |
| `/战报` | 战况速报：重要指令（LLM 翻译 + **任务进度**）+ 目标星球战况 + 跃迁封锁提示 + 战区玩家分布 + 最新资讯 |
| `/战线` | 当前全部战线：星球｜敌方阵营（终结族/机器人/光能族）｜解放战/防御战｜解放百分比/HP + 跃迁航道封锁列表 |
| `/战役` | 当前战役（Campaign）简报：史诗背景 + 阶段任务（LLM 翻译 + 术语表） + 奖励 + 跃迁封锁 |
| `/dss` | DSS 民主空间站：停靠星球、生效效果（中文 + 完整描述）、下一轮投票倒计时；离线/移动时输出 LLM 翻译的最新 DSS 资讯（缓存） |
| `/分析` | LLM 战局分析：威胁概述/关键目标/行动建议/风险提示（真理部风格，解放度越高优先级越高） |
| `/查表` | 行动变量对照表：12 分类 / 参数详情 / 分类子条目 / 星区列表 / 星区星球（中英文均可） |
| `/星球 <名>` | 星球详情（官方源）：占领方、抵抗度、防御战 HP/失守%、行动变量词条、跃迁封锁、MO 目标标记 |
| `/roll` | 随机战备推荐（全类型战备池） |
| `/<变种名>` | 扫描战役星球，输出存在该变种的星球（官方效果数据，毫秒级） |

配套 Web 控制台（浏览器操作）：一键启动部署、扫码登录、一键群发（战报/战役/DSS/分析）、指定群推送、机器人响应测试（WS 注入）、自重启。

## 🏗️ 架构

```
┌─────────┐   OneBot v11 (WS)   ┌──────────┐
│  NapCat │ ◄─────────────────► │  AstrBot │  ← 7 个 HD2 插件（含公共库）
│ (QQ 接入)│    ws://:6199/ws    │ (框架)   │
└─────────┘                     └────┬─────┘
                                     │ 数据抓取（官方优先，社区兜底）
                          ┌──────────▼──────────┐
                          │ 官方 API（主源）     │ ← MO/战役/战线/DSS/新闻/解放%
                          │ api.live.prod.       │
                          │   thehelldiversgame.com│
                          ├─────────────────────┤
                          │ Helldivers Companion│ ← 备用（官方故障时）
                          │ + helldivers2.dev   │ ← 星球名映射
                          └─────────────────────┘
```

- **AstrBot**：QQ 群机器人框架（部署插件、LLM 接入、消息管线）
- **NapCat**：OneBot v11 实现，QQNT 注入，负责 QQ 收发
- **插件**（`plugins/`）：war_report（战报/战线/战役/dss/分析）、planet_info（/星球）、lookup（/查表）、roll、variant_query（变种扫描）、guard（指令守卫）、**hd2_common（公共库：共享常量/抓取/表加载/译名映射，被 3 个插件 import）**
- **Web 控制台**（`scripts/hd2_webui.py`）：浏览器管理面板，一键部署/推送/测试
- **对照表**（`tables/`）：行动变量对照表、星图对照表、DSS 效果关联表、效果中文映射表（可自行维护，动态读取）
- **缓存**（`plugins/astrbot_plugin_hd2_war_report/cache/`）：重要指令缓存（最近 5 条新闻无 NMO 时复用）、DSS 资讯缓存（LLM 翻译）、新闻缓存（LLM 翻译固定输出）

### 数据源说明（2026-08 升级）

1. **官方 API 主源**：`https://api.live.prod.thehelldiversgame.com`（Arrowhead 官方后端，2026-08 从游戏流量抓包发现）。公开可访问（仅需 `X-Super-Client` 头），提供最权威的 MO/战役/星球状态/DSS/历史战役数据
2. **社区备选**：官方故障（如 500）时自动回退 Helldivers Companion 聚合端点，保证服务不中断
3. **星球名/译名**：helldivers2.dev 实时星球名 + 本地对照表中文译名（星区显示查本地星图表，不依赖官方 sector 数字）

## 🚀 快速开始

前置：Windows 10/11 + AstrBot 桌面版 + NapCat + QQNT 9.9.x + Python（AstrBot 自带）

**方式一：一键启动（推荐）**

```bash
# 1. 配置（复制模板并填写：QQ 号/群号/NapCat token/AstrBot 路径/LLM key）
copy config.example.json config.json

# 2. 双击 start.bat —— 自动启动 AstrBot + NapCat + Web 控制台并打开浏览器
```

**方式二：手动启动**

```bash
# 1. 克隆项目并配置
copy config.example.json config.json
# 2. 部署插件到 AstrBot：将 plugins/astrbot_plugin_* 复制到 <AstrBot 数据目录>/data/plugins/，重启 AstrBot
# 3. 设置环境变量并启动 Web 控制台
set HD2_PROJECT_DIR=%CD%
python scripts/hd2_webui.py
```

详细步骤见 [docs/部署指南.md](docs/部署指南.md)。

### 📚 文档索引（docs/）

| 文档 | 说明 |
| --- | --- |
| [功能详解.md](docs/功能详解.md) | 每个功能的**描述 + 实现细节**（数据源/逻辑/缓存/容错） |
| [部署指南.md](docs/部署指南.md) | 环境准备 / 插件部署 / 配置填写 |
| [使用说明.md](docs/使用说明.md) | 群内指令与玩法说明 |
| [模块架构分析报告.md](docs/模块架构分析报告.md) | 模块功能表 / 耦合度 / 可维护性 / 开源隐患 / 建议 |
| [抓取指南.md](docs/抓取指南.md) | 面向二次开发，如有个人改进/开发需求可参考 |


> ⚠️ 重启 AstrBot 请通过桌面版（astrbot-desktop-tauri.exe）拉起后端，不要手动 `Start-Process launch_backend.py`（会丢失环境导致 6199 WS 服务与 aiocqhttp 适配器不启动）。

## 🔄 一键同步（维护用）

开发基准为 `HD2-Bot/plugins`，修改后一键同步到 Release / 备用 / AstrBot 部署版：

```bash
python sync_plugins.py            # 全量同步 + 自动编译校验
python sync_plugins.py --dry-run  # 预览差异（不写入）
```

- 同步内容：`plugins/`（含 hd2_common 公共库）+ `tables/` `scripts/` `docs/` + README/config 等根文件
- Release / 部署版：镜像同步（含清理多余文件，部署版保护 `tables` 兜底副本）；hd2-bot：仅推送（硬链接目录）
- 同步后自动对 3 个目标全部 `.py` 做 `py_compile` 校验，任一失败即中止

## 📂 目录结构

```
hd2-bot/
├── sync_plugins.py        # 一键同步脚本（开发 → Release/部署版 + 自动编译校验）
├── hd2_config.py          # 统一配置加载器（config.json + 环境变量）
├── config.example.json    # 配置模板（复制为 config.json 填写）
├── plugins/               # 7 个 AstrBot 插件（含 hd2_common 公共库）
├── scripts/               # Web 控制台 / 推送 / WS 注入 / 自重启
├── tables/                # 对照表数据（行动变量/星图/DSS 效果/效果中文映射）
├── persona/               # 人格设定（真理部 MTB-114514）
└── docs/                  # 部署指南、使用说明、工作总结、架构分析、可行性评估
```

## ⚙️ 配置说明

所有本机信息集中在 `config.json`（已加入 .gitignore，不会误提交）：
- `bot.self_id` — 机器人 QQ 号（NapCat 登录号）
- `bot.groups` / `bot.default_group` — 白名单群 / 默认推送群
- `napcat.token` — NapCat WebUI token（登录接口用）
- `napcat.dir` — NapCat 目录（一键启动部署用）
- `astrbot.exe/python/app_dir` — AstrBot 路径
- `llm.api_key` — DeepSeek API key（战报 LLM 翻译/分析）
- `paths.*` — 对照表路径（相对项目根）

环境变量 `HD2_PROJECT_DIR` 指向项目根（插件读取对照表/配置用）。

### 缓存策略（war_report 插件）

- **重要指令缓存**：最近 5 条新闻无 `NEW MAJOR ORDER` 时直接复用本地缓存（MO 未更新，避免重复翻译/防 API 波动）；有新 NMO 时实时抓取并更新缓存
- **DSS 资讯缓存**：涉及 DSS 的最新一条资讯，抓取后走 LLM 翻译存中文，DSS 离线/移动时作为补充说明输出
- **新闻缓存**：按新闻 id 缓存 LLM 翻译结果，id 不变则输出固定文案
- 缓存统一存放在 `plugins/astrbot_plugin_hd2_war_report/cache/` 目录

## 📄 License

MIT License — 详见 [LICENSE](LICENSE)。

## 🙏 致谢

- [AstrBot](https://github.com/Soulter/AstrBot) — QQ 机器人框架
- [NapCat](https://github.com/NapNeko/NapCatQQ) — OneBot 实现
- [Helldivers Companion](https://helldiverscompanion.com) — 星图 + 社区数据 API
- [helldivers2.dev](https://helldivers2.dev) — 星球数据 API
- Arrowhead Game Studios — 官方 API（api.live.prod.thehelldiversgame.com）
