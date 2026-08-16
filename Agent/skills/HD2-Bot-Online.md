---
name: HD2-Bot-Online
description: 一键启动 AstrBot + NapCat 使 HD2 战报 QQ 机器人上线。包含环境检查、AstrBot 启动、NapCat 注入启动、扫码登录、连接验证的完整流程，以及常见故障处理。
version: 1.0.0
---

# HD2-Bot-Online

启动本机 AstrBot + NapCat，使「绝地潜兵2 战报机器人」QQ 群机器人恢复在线的完整流程。

## 适用环境

- Windows 10/11 x64
- AstrBot 桌面版安装于 `<AstrBot安装目录>`
- NapCat v4.18.x 解压于 `<NapCat目录>`
- QQNT 9.9.x 安装于 `<QQNT路径>`
- 本机 hosts 有 `#S302` 条目劫持 GitHub 域名（S302 未运行时 GitHub 不可直连）

## 检查清单（按序执行）

1. 检查 AstrBot 进程与端口
2. 检查 NapCat 进程与 WS 连接
3. 检查 QQ 登录状态（NapCat 拉起的 QQ 实例）
4. 验证 AstrBot 日志出现「aiocqhttp 适配器已连接」

## 详细步骤

### 1. 检查 AstrBot 是否运行

```powershell
Get-Process | Where-Object { $_.ProcessName -match 'astrbot-desktop|python' }
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 6185, 6199 }
```

- 6185 = WebUI（http://127.0.0.1:6185，用户名 Jerry）
- 6199 = OneBot 反向 WS 监听端口（AstrBot 做服务端）
- 若 python 进程存在且两端口监听 → 已运行，跳到步骤 2

### 2. 启动 AstrBot（若未运行）

```powershell
Start-Process -FilePath "<AstrBot安装目录>\astrbot-desktop-tauri.exe"
Start-Sleep -Seconds 18
```

- 等待约 18 秒让后端完全启动
- 确认 6185/6199 监听

### 3. 检查 NapCat 进程

```powershell
Get-Process | Where-Object { $_.ProcessName -match 'NapCat|QQ' }
```

- 期望看到：`NapCatWinBootMain` + 多个 `QQ` 子进程（NapCat 注入的 QQNT）
- 若存在且 6199 有 Established 连接 → 已上线，跳到步骤 6
- 若 NapCat 进程不存在或 QQ 掉线 → 继续步骤 4

### 4. 启动 NapCat（注入式启动）

```powershell
$napdir = "<NapCat目录>\napcat"
$env:NAPCAT_PATCH_PACKAGE = "$napdir\qqnt.json"
$env:NAPCAT_LOAD_PATH = "$napdir\loadNapCat.js"
$env:NAPCAT_INJECT_PATH = "$napdir\NapCatWinBootHook.dll"
$env:NAPCAT_LAUNCHER_PATH = "$napdir\NapCatWinBootMain.exe"
$env:NAPCAT_MAIN_PATH = "$napdir\napcat.mjs"
Set-Content -Path "$napdir\loadNapCat.js" -Value '(async () => {await import("file:///<NapCat目录>/napcat/napcat.mjs")})()' -Encoding UTF8
Start-Process -FilePath "$napdir\NapCatWinBootMain.exe" -ArgumentList '"<QQNT路径>"', "$napdir\NapCatWinBootHook.dll" -WorkingDirectory $napdir
```

- 等待 25~30 秒，NapCat 会生成二维码
- 二维码路径：`<NapCat目录>\napcat\cache\qrcode.png`

### 5. 扫码登录 QQ

- 让用户用手机 QQ 扫二维码（路径见上），登录主号 <机器人QQ>
- 重要：**扫码前先退出用户自己的 QQ 客户端**（PC 端同一账号不能双开，否则报「当前账号已登录，无法重复登录」）
- 登录成功后 NapCat 会自动重连 AstrBot

### 6. 验证上线

```powershell
# WS 连接
Get-NetTCPConnection | Where-Object { $_.LocalPort -eq 6199 -and $_.State -eq 'Established' }
# AstrBot 日志
Select-String -Path "<AstrBot数据目录>\logs\backend.log" -Pattern "适配器已连接" -Encoding UTF8 | Select-Object -Last 1
```

- 出现 `aiocqhttp(OneBot v11) 适配器已连接` 即上线成功
- 群里 @机器人 发「战报」测试

## 常见故障

### 报「当前账号已登录，无法重复登录」
- 原因：用户 QQ 客户端与 NapCat 实例冲突
- 解决：关闭用户 QQ 客户端，重启 NapCat 重新扫码

### GitHub 无法访问（下载/API 失败）
- 原因：hosts 里 `#S302` 条目把 GitHub 域名指向 127.0.0.1
- 解决：使用镜像 `https://ghproxy.net/https://github.com/...` 或 `https://gh-proxy.com/...`

### NapCat 启动失败（launcher-user.bat 报 QQ path invalid）
- 原因：启动脚本依赖注册表 QQ 卸载信息，绿色安装无此条目
- 解决：用步骤 4 手动设环境变量 + 直接调 NapCatWinBootMain.exe 的方式

### 消息没反应但连接正常
- 检查白名单：`<AstrBot数据目录>\data\platform_whitelist.json`（平台级白名单，default=机器人号只收 <群号>，napcat=主号收 <群号>+<群号>）
- 检查唤醒：必须 @ 机器人（wake_prefix 已清空）
- 检查人格：`default_personality` 应指向「真理部自助回复机器人-MTB-114514」

## 关键路径速查

- AstrBot 安装：`<AstrBot安装目录>`
- AstrBot 数据：`<AstrBot数据目录>\data`（cmd_config.json、platform_whitelist.json、data_v4.db）
- AstrBot 日志：`<AstrBot数据目录>\logs\backend.log`
- NapCat 目录：`<NapCat目录>`
- NapCat OneBot 配置：`NapCat\napcat\config\onebot11_<机器人QQ>.json`（WS 客户端指向 ws://127.0.0.1:6199/ws）
- NapCat WebUI：http://127.0.0.1:6099（token 见 `config\webui.json`，接口调试可手动发群消息）
- 战报插件：`<AstrBot数据目录>\data\plugins\astrbot_plugin_hd2_war_report`
- 星图对照表：`<项目根>\星图对照表_修正版.md`

## 数据源（战报插件用）

- Major Order：`https://cdn.helldiverscompanion.com/live/assignments/recent.json`
- 战区玩家分布：`https://cdn.helldiverscompanion.com/live/extendedApiInformation/2days.json`
- 新闻/实时：`https://helldiverscompanion.com/api/hell-divers-2-api/get-api-data-live`
- 星球名映射：`https://api.helldivers2.dev/api/v1/planets`（需 X-Super-Client/X-Super-Contact 头）
