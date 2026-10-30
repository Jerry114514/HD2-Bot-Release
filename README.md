# HD2 Bot

基于 AstrBot 的《绝地潜兵2》中文机器人，包含 10 个插件。战报、战线、DSS、战役、星球、Wiki 与配装支持黑金 HUD 图片；图片不可用时使用同一次查询的文字结果。

## 指令

| 指令 | 内容 |
| --- | --- |
| /帮助 | 指令说明 |
| /战报、/战线、/dss、/战役 | 同一主站快照的中文战况 |
| /查询 星球名 | 支持中文、英文、星球 ID 与 X-45 等连字符名称 |
| /变种 名称 | 当前战役的变种；源不可用与确实无结果分别提示 |
| /wiki 关键词 | 中文 Wiki 正文与可选表格/图片 |
| /银河星图 | 在线中文星图 |
| /api | 主仓 Issue #60 的 API 变更记录 |
| /分析 | 根据完整战线、MO、防御期限和速率提供建议，需要已配置的模型 |
| /roll | 黑箱平衡抽取，输出主手、副手、手雷和四个战备 |
| /roll 纯随机 | 显式纯随机抽取 |
| /战报t、/rollt、/wiki 关键词 文字 | 纯文字输出 |

Bot只提供整套七槽抽取，不提供单件替换、锁定或手动选装入口。平衡抽取只输出配装结果；评分、评价规则、种子、模型版本和尝试过程不进入用户输出。图片失败不会重抽，也不会退成旧四槽。抽取失败明确提示，不自动改成纯随机。

## 安装与平台

部署面向 Windows + AstrBot 桌面版。代码离线检查同时覆盖 Windows/Linux；Linux 运行需要自行准备中文字体和对应平台接入。

使用 Python 3.12（与本轮验证的 AstrBot 4.29.3 匹配）和 Node.js 22 或更高版本；AstrBot 使用支持事件优先级的版本（建议与部署使用的当前桌面版一并验证）。基础依赖：

```powershell
python -m pip install -r requirements.txt
# Wiki 表格（正文查询不强制依赖此项）
python -m pip install -r requirements-wiki.txt
# 可选：结构化源故障时的浏览器查询、WS 模拟测试
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

复制 `config.example.json` 为 `config.json`，选择 `bot.platform`：

| 能力 | qq_official | napcat |
| --- | --- | --- |
| 指令回复、图片上传 | 通过 AstrBot 官方 QQ 适配器，受平台权限约束 | 通过 AstrBot OneBot 适配器 |
| 登录/凭据 | 在 AstrBot 适配器配置；本项目不使用旧模板的 qq_official 凭据字段 | 配置 NapCat 登录和本机 WebUI token |
| 本控制台主动群推送/WS 测试 | 不支持；入口执行前提示，在 AstrBot 管理支持的会话能力 | 支持；要求明确目标群 |
| 进群欢迎 | 不支持成员变动通知 | 支持 OneBot group_increase |
| 启动器 | 启动 AstrBot/控制台，跳过 NapCat 和 WS 等待 | 启动已配置实例，提示在 NapCat 完成登录 |

`astrbot.data_dir` 必须指向所选实例，`bot.platform_id` 填 AstrBot 中该适配器的确切 ID。控制台只管理选定平台；数字群号校验仅用于 NapCat。平台/群/用户 ID 在插件日志中保留字符串。

将 `HD2_PROJECT_DIR` 指向仓库绝对路径，并让 AstrBot 桌面进程继承它。部署/更新前先预览：

```powershell
python sync_plugins.py --target "<AstrBot数据目录>/plugins" --dry-run
python sync_plugins.py --target "<AstrBot数据目录>/plugins"
# 已有不同代码时：停止/重载相应插件后执行；旧文件保存到目标目录的 .hd2-sync-backups/
python sync_plugins.py --target "<AstrBot数据目录>/plugins" --update
python scripts/check.py --config config.json
python scripts/launch.py
```

同步不会复制运行缓存或覆盖已有 welcome_config.json，也不删除其他插件。更新期间先停止/卸载 HD2 插件，文件同步完成后重载全部 10 个插件；避免运行中混用不同版本核心。

框架凭据、群配置和本地备份不提交。配置损坏会报错；进程管理按配置端口和可执行路径核验实例，端口被陌生进程占用时拒绝结束它。

## 数据、图片与日志

主源是 [Jerry 主站](https://jerry114514.github.io/HD2-Galatic_war-Map/)，机器人消费已翻译快照，常规战况不要求 LLM key。`data.site_base`/`data.news_json_url` 可调整主源，`data.backup_json_url` 可指定同结构的备用快照；源时间、版本和陈旧状态在共享层管理。主源暂时失败可在配置的最长陈旧窗口内使用有标记的本地快照；超过窗口拒绝输出实时结论。缺失数值与真实 0 分开处理。

防御使用事件进度和截止时间；区域优先历史差分，无法实测时才尝试有依据的推演。MO 按任务和单位分别展示。长卡自动分页，Wiki 摘要标明省略并保留原页链接。

控制台只绑定本机，变更请求验证 Host/Origin 和页面会话 token。每次发送独立持有内容；超时标为“结果不明”，不要盲目重发。配置备份在忽略的 temp/config_backups/。

日志默认只记录指令相关事件，以请求 ID 关联，追加写入并有界轮转。已生成、提交发送、平台 API 确认、失败、结果不明分别记录。模拟 WS 回执是测试结果，不能当作真实群接收证明。

## 验证与维护

```powershell
python -X utf8 scripts/check.py
```

检查包括语法、隔离函数、真实本机 HTTP、事件接口契约、Pillow 绘图及 Node 黑箱桥接；不会连接 QQ、使用凭据或发送消息。CI 在 Windows/Python 3.12/Node 24 与 Linux/Python 3.12/Node 22 运行离线检查。本轮在独立环境验证 AstrBot 4.29.3 可加载全部10插件；真实 QQ 收发待验，平台能力须结合实际适配器权限确认。

[修复与验收记录](docs/修复验收.md) 对应审查问题 1–21。旧 Agent 记录和架构分析属于历史材料，不代表当前运行状态。旧数据层与测试按当前图片架构选择性适配；没有整体回退当前插件。
