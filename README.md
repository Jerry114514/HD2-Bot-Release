# HD2 Ministry Of Truth Bot — 绝地潜兵2 真理部战况 QQ 机器人

> 🤖 **AI 生成声明**：本项目由 **DeepSeek V4-Flash 的 Agent 能力**协作完成（代码、架构、文档均通过 Agent 生成与迭代）。项目根目录下 [`Agent/`](Agent/) 文件夹保存了开发过程中的工作报告与记忆记录（已脱敏），可供参考、二次开发与部署。

基于 AstrBot 的《绝地潜兵 2》战况播报 QQ 机器人。自动同步银河战争实时数据，**全套指令输出主站同款黑金 HUD 图片卡**：战报、战线、DSS、星球指挥台、战役简报、随机配装、Wiki 查询、API 变动监测、LLM 战局分析，全部中文界面、中英文查询。

线上运行于 **QQ 官方机器人平台**（HD2_QQbot）；亦支持 NapCat / OneBot v11 接入。

## 📜 免责声明

> ⚠️ **非官方项目**：本项目为**个人学习与技术交流**用途，与 Arrowhead Game Studios 无任何关联，未获官方授权或认可。请勿用于商业用途。
>
> ⚠️ **数据来源**：部分数据来自游戏官方 API（`api.live.prod.thehelldiversgame.com`，经个人抓包确认其公开可访问）与第三方社区 API（Helldivers Companion / helldivers2.dev / helldivers.wiki.gg）。官方接口可能随时间变化或失效，本项目不保证数据持续可用。
>
> ⚠️ **数据所有权**：游戏内容、名称、图标等版权归 Arrowhead Game Studios 所有；社区 API 数据归各提供方所有。本项目仅做技术演示。
>
> 📧 **联系我们**：若 Arrowhead 官方或相关权利方认为本项目对接口的使用不当，请通过 GitHub Issues 联系我们，我们会第一时间删除相关代码或调整实现方式。

## ✨ 指令一览（默认输出战术 HUD 图片卡，渲染失败自动回退文本）

| 指令 | 说明 |
| --- | --- |
| `/战报` | 银河战况速报卡：重要指令进度、目标星球、四阵营兵力分布、最新资讯 |
| `/战线` | 全银河战线列表：各战区解放/防御进度与交战双方 |
| `/dss` | 民主空间站：停靠星球、生效行动效果（词条去重对照）、投票与捐献进度 |
| `/战役` | 战役简报卡：战役故事与阶段目标（中文优先，无 LLM 依赖） |
| `/查询 星球名` | **星球指挥台卡**：生态背景图、解放环、兵力趋势、区域战况、下属区域（城市/工厂）、环境词条、星区中文名（`/星球 名` 同效） |
| `/roll` | 随机抽取七格战术配装：主武器/副武器/手雷 + 四战备，带总评与装备图标 |
| `/wiki 关键词` | Helldivers 中文 Wiki 词条查询长图：数据表 + 正文摘要 + 主图（自动跟随重定向与页面更新） |
| `/api` | 游戏 API 变动监测：效果/接口变更从主仓 Issue 实时抓取 |
| `/<变种名>` | 该变种当前出没星球（如 `/孢裂变种`） |
| `/分析` | AI 战局分析：威胁评估与目标建议（需 LLM 连通，断连自动回退常规播报） |
| `/帮助` | 作战手册图片卡：全部指令速查 |

新成员进群会自动收到 @欢迎与指令速查（welcome 模块）。

## 🌌 银河星图（快捷指令 · 线上中文星图）

**用法：群内 @机器人 → 在快捷指令栏中选择「/银河星图」→ 机器人立即返回线上中文星图的入口链接。**

> 入口：<https://jerry114514.github.io/HD2-Galatic_war-Map/galaxy-map-v2.html>

打开后是一张可交互的**线上中文银河星图**（与机器人同数据源、每 5 分钟同步）：

- **273 颗星球实时战况**：按阵营着色（超级地球/终结族/机器人/光能族），圆点大小对应在线人数，一眼看清三条战线在哪里烧
- **补给线连线**：星球间的进攻通道与归属关系，判断进攻方向
- **归属变化追踪**：哪些星球刚易主、哪些在打防守
- **星球指挥台**：点击任意星球进入详情页——解放环、兵力趋势、下属区域、环境词条、补给线邻居（与 `/查询` 图片卡同口径）

**使用建议**：把链接收藏在浏览器里；组队出击前先在星图上确认目标星球的解放进度与抵抗度，再回群里 `/查询 星球名` 看细节。手机 / 电脑浏览器均可打开，纯静态页面无需登录。

## 🔌 接入方式

| 通道 | 触发方式 | 现状 |
| --- | --- | --- |
| **QQ 官方机器人平台**（`qq_official`，HD2_QQbot） | 群内 **@机器人** + 指令；支持平台侧快捷指令（如「/银河星图」） | 线上运行中 |
| NapCat / OneBot v11（`aiocqhttp`） | 群内直接 `/指令`（无需 @） | 可选接入 |

> 官方平台只能收到 @机器人 的消息（腾讯 API 能力边界），NapCat 通道则无需 @；两条通道的指令处理完全一致。新成员进群欢迎需要 NapCat 通道（官方平台无成员变动事件）。

## 🏗️ 架构

```
QQ 官方平台 ─┐                       ┌─────────────────────────┐
NapCat/OneBot ─┴─► AstrBot (框架) ◄── │ plugins/ 10 个 HD2 插件  │
                                      │ war_report  战况/战线/DSS│
                                      │             /战役/api/星图│
                                      │ planet_info 星球指挥台   │
                                      │ wiki        Wiki 长图    │
                                      │ roll        七格配装     │
                                      │ lookup      /帮助        │
                                      │ variant_query 变种出没   │
                                      │ welcome      进群欢迎    │
                                      │ monitor/guard/common    │
                                      └───────────┬─────────────┘
                                                  │ 数据（主站同源，官方 API 兜底）
                                      ┌───────────▼─────────────┐
                                      │ HD2-Galatic_war-Map 站点 │
                                      │ data.json（每 5 分钟）    │
                                      │ + 各静态表/帧/图床        │
                                      └─────────────────────────┘
```

- **AstrBot**：QQ 机器人框架（插件管线、多平台适配）
- **插件**（`plugins/`）：全部指令的图片卡渲染与数据装配；公共库 `hd2_common` 提供共享常量/抓取/译名映射
- **数据主源**：主站 [HD2-Galatic_war-Map](https://jerry114514.github.io/HD2-Galatic_war-Map/) 每 5 分钟生成的 `data.json`（官方 API 落盘 + 翻译仓中文），机器人直接读站点 JSON 保证与网站口径一致；静态表（星球区域/星区/词条/效果中文）与图床素材（biome 背景/星球图标）同样取自主站并本地缓存
- **Web 控制台**（`scripts/hd2_webui.py`）：一键启动、扫码登录、群发推送、群号管理
- **API 推送脚本**（`scripts/push_via_github_api.py`）：github.com 直连受限时经 Git Data API 同步仓库

## 🚀 快速开始

前置：Windows 10/11 + AstrBot 桌面版 + Python（AstrBot 自带）；接入 QQ 官方机器人需在 [QQ 开放平台](https://q.qq.com) 创建机器人并获得 AppID/Token；接入 NapCat 需 QQNT 9.9.x + NapCat。

```bash
# 1. 配置（复制模板并填写：机器人凭据/群号/AstrBot 路径/LLM key）
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

> ⚠️ 重启 AstrBot 请通过桌面版（astrbot-desktop-tauri.exe）拉起后端，不要手动 `Start-Process launch_backend.py`（会丢失环境导致 WS 服务与平台适配器不启动）。

## 🔄 一键同步（维护用）

开发基准为 `HD2-Bot/plugins`，修改后一键同步到 Release / 备用 / AstrBot 部署版：

```bash
python sync_plugins.py            # 全量同步 + 自动编译校验
python sync_plugins.py --dry-run  # 预览差异（不写入）
```

- 同步内容：`plugins/`（含 hd2_common 公共库）+ `tables/` `scripts/` `docs/` + README/config 等根文件
- 同步后自动对全部目标 `.py` 做 `py_compile` 校验，任一失败即中止
- 仓库推送：github.com 直连受限时使用 `python scripts/push_via_github_api.py main <上次同步的本地tip>`（Git Data API 通道）

## 📂 目录结构

```
hd2-bot/
├── sync_plugins.py        # 一键同步脚本（开发 → Release/部署版 + 自动编译校验）
├── hd2_config.py          # 统一配置加载器（config.json + 环境变量）
├── config.example.json    # 配置模板（复制为 config.json 填写）
├── plugins/               # AstrBot 插件（含 hd2_common 公共库，指令全部图片卡输出）
├── scripts/               # Web 控制台 / 推送 / API 链式推送 / WS 注入 / 自重启
├── tables/                # 对照表数据（行动变量/星图/DSS 效果/使用说明）
├── persona/               # 人格设定（真理部 MTB-114514）
└── docs/                  # 部署指南、使用说明、架构分析
```

## ⚙️ 配置说明

所有本机信息集中在 `config.json`（已加入 .gitignore，不会误提交）：
- `bot.self_id` — 机器人标识 / `bot.groups`、`bot.default_group` — 白名单群 / 默认推送群
- `qq_official.*` — QQ 官方机器人 AppID/Token/Secret（官方平台接入）
- `napcat.*` — NapCat WebUI token / 目录（OneBot 接入，可选）
- `astrbot.exe/python/app_dir` — AstrBot 路径
- `llm.api_key` — LLM key（仅 `/分析` 用，其余指令零 LLM 依赖）
- `paths.*` / `data.*` — 对照表路径与站点数据源 URL（默认已指向主站）

环境变量 `HD2_PROJECT_DIR` 指向项目根（插件读取对照表/配置用）。

## 🌐 中文新闻翻译管道（网站侧）

HD2 战况网站（[Jerry114514.github.io/HD2-Galatic_war-Map](https://jerry114514.github.io/HD2-Galatic_war-Map/)）的中文内容由独立的私密翻译仓库 **HD2Web-Trans** 提供：

```
HD2Web-Trans（私密翻译仓库）
  ├─ 每 5 分钟：抓取官方 dispatches + assignments → 去标签 → 去重 → 翻译（含术语库）
  ├─ 推送 TransNews.json（{ mo_brief, items[最新10条] }）
  ↓
HD2-Galatic_war-Map（公开 Page 仓库）
  ├─ fetch-data.yml：生成 data.json 后合并翻译
  ├─ data.major_order.translated_brief（MO 简报中文）+ data.news（新闻中文）
  ↓
机器人 / 战报卡片
  └─ 只读站点 data.json（中文直接可用，前台零 LLM 调用）
```

- **降级策略**：翻译不可用时网站照常展示原文，机器人回退本地缓存，两套系统相互独立
- 官中缺失时 DeepSeek 机翻兜底（网站侧），机器人不再重复实现翻译

### 缓存策略（war_report 插件）

- **战报快照**：后台每 4 分钟（带抖动）用站点中文数据组装快照，前台毫秒级返回；MO 间隙标记 `quality=gap` 并缩短有效期
- **新闻缓存**：按新闻 id 缓存，站点译文覆盖旧 LLM 译文（llm/raw 永不顶掉站点译文）
- **图片卡缓存**：按数据版本（fetchedAt/页面修订号）缓存成品 PNG——wiki 卡在词条被编辑后自动重渲染
- 缓存统一存放在 `plugins/astrbot_plugin_hd2_war_report/cache/` 目录（已 gitignore）

## 📄 License

MIT License — 详见 [LICENSE](LICENSE)。

## 🙏 致谢

- [AstrBot](https://github.com/Soulter/AstrBot) — QQ 机器人框架
- [HD2-Galatic_war-Map](https://jerry114514.github.io/HD2-Galatic_war-Map/) — 中文战况主站（机器人数据主源）与线上中文星图
- [helldivers.wiki.gg](https://helldivers.wiki.gg/zh) — 中文 Wiki（/wiki 数据来源）
- [Helldivers Companion](https://helldiverscompanion.com) / [helldivers2.dev](https://helldivers2.dev) — 社区数据 API
- Arrowhead Game Studios — 官方 API（api.live.prod.thehelldiversgame.com）
