# hd2-btk-sdk v0.14.1

绝地潜兵 2 武器击杀计算器（v8）的引擎包，给 QQ 机器人 / 后端用。零依赖，Node 直接 require。

## 快速开始
\`\`\`js
const sdk = require('./index.js');
sdk.compute({ weapon: 'AR-23', enemy: '武斗虫', part: '头部' });
\`\`\`

## API
- \`compute({weapon, enemy, part, distance=0, angle=0, shrapnelHits=0, blastParts=[]})\`
  返回 \`{btk, shots, pelletsNeeded, ttk, beamSeconds, path, label, cripple, full, bleedSec, perShotDamage, stuck}\`
- \`scanStuck({weapon, fatalOnly=true})\` 该武器在哪些敌人部位卡阈值
- \`listWeapons({cat})\` / \`listEnemies({faction})\` 给指令做菜单

## DoT（持续伤害）
武器 `type: 'dot'` 时**不需要传 part**：
```js
sdk.compute({ weapon: 'DOT-FIRE', enemy: '强袭虫' });
// → { perTrigger: 292.5, triggers: 9, seconds: 27, factor: 0.65, mult: 1.5 }
```
DoT 只作用主体、有穿甲（AP>AV 全伤 / AP=AV 65% / AP<AV 免疫）、按触发次数×时长累加，并乘敌人元素倍率（`enemy.mult.fire` 等）。

## 拆毁值（v0.14 新增）

```js
sdk.demolition('“飞鹰”500KG炸弹（延时爆炸）', '炮舰设施');
// → { ok:false, reason:'demolition-insufficient', need:60 }
sdk.demolition('NUX-223 地狱火炸弹', '巨石');
// → { ok:true, maxDistance:17, need:60 }
sdk.demolitionAt('“飞鹰”500KG炸弹（延时爆炸）', 16.7);
// → 40
```

`reason` 取值：`badr`（需爆炸伤害）、`demolition-insufficient`（无血量，拆不掉）、
`demolition-insufficient-has-hp`（拆不掉但可清血量，返回 `hp: [血量, AV]`）。

曲线：内圈全额；内外圈之间 普通 `df×(1−√x)`、巨型 `df×(1−x²)`；外圈归零；结果截尾取整。
`sdk.structures` / `sdk.blasts` 可直接拿来给指令做菜单。

## 口径（务必保持一致）
- 取整：**只在最后一步** ceil，中间全浮点
- 甲弹：AP>AV 100%，AP=AV 65%，AP<AV 0（跳弹）
- 耐久混算：\`标准×(1−耐久) + 耐久伤×耐久\`
- 距离衰减：\`rv=e^(αs)\`，\`α=−4.71e−4·Cd·D²/m\`，\`rd=0.75rv²+0.25\`
- 爆炸：简化模型，**内圈全额**，爆抗 100% 转主体
- 传导：部位伤害×传导率，受部位最大血量封顶
- TTK：\`(BTK−1) ÷ (RPM÷60)\`
- 光束：不算 BTK，算累计照射秒数（0.1s 向上取整）
- 卡阈值：BTK=1/2/3 且「差一发就够」的程度 ≥90%

## 数据来源
helldivers.wiki.gg/zh 的「详细武器数据」与「部位分析」，2026-11。未标注的假设见 README 顶部。

## 自检
```bash
node selftest.js
```
全量扫描 94,360 组（武器 × 敌人 × 部位）+ DoT 公式核对 + 护盾/建筑完整性。任何异常会以 `[类型] 描述` 列出，退出码非 0。
