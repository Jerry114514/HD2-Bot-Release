/* 拆毁值计算模块（v0.14）
 * 曲线：内圈全额；内外圈之间 普通 1-√x、巨型 1-x²；外圈归零；结果截尾取整
 * x = (d - r1) / (r2 - r1)
 */
const STRUCTURES = [
  { name: '货物集装箱（中立）', df: 20 },
  { name: '非法广播塔（中立）', df: 30 },
  { name: '叛变科研站（中立）', df: 50 },
  { name: '拓殖地围墙（超级地球）', df: 40 },
  { name: '防御闸门（超级地球）', df: 40 },
  { name: '防御发电机（超级地球）', df: 40 },
  { name: '特大城市建筑（超级地球）', df: 40 },
  { name: '吐酸泰坦虫穴', df: 40 },
  { name: '虫穴（内部）', df: 20, badr: true },
  { name: '虫穴（外部）', df: 40 },
  { name: '侵染塔巢', df: 50 },
  { name: '尖啸虫巢穴塔', df: 60 },
  { name: '孢子喷涌虫', df: 50, hp: [2500, 2] },
  { name: '生物处理器', df: 40, hp: [5000, 1] },
  { name: '巨型构筑者（外部）', df: 50, hp: [4000, 5] },
  { name: '巨型构筑者（通风口）', df: 20, badr: true, hp: [4000, 5] },
  { name: '探测塔', df: 50 },
  { name: '机器人构筑者（外部）', df: 40, hp: [1500, 5] },
  { name: '机器人构筑者（通风口）', df: 20, badr: true, hp: [1500, 5] },
  { name: '燃料储备', df: 60 },
  { name: '炮舰设施', df: 60 },
  { name: '超大型工厂大门', df: 40 },
  { name: '轨道加农炮', df: 60 },
  { name: '战略配备干扰装置', df: 50 },
  { name: '感知干扰器电源', df: 30, hp: [180, 0] },
  { name: '凝视者', df: 20, hp: [900, 5] },
  { name: '凝视者尖塔', df: 60, hp: [900, 9] },
  { name: '停泊的穿梭舰（舱门）', df: 20, badr: true, hp: [5000, 5] },
  { name: '停泊的穿梭舰（外部）', df: 50, hp: [5000, 5] },
  { name: '停泊的穿梭舰（护盾）', df: 50, badr: true, hp: [2500, 0] },
  { name: '闪电尖塔', df: 20, hp: [200, 2] },
  { name: '巨石', df: 60 }
];

const BLASTS = [
  { name: '“飞鹰”500KG炸弹（延时爆炸）', df: 50, r1: 10, r2: 25, dmg: 1500, giant: true, explosive: true },
  { name: '“飞鹰”500KG炸弹（命中爆炸）', df: 40, r1: 1, r2: 3, dmg: 100, explosive: true },
  { name: '“飞鹰”空袭', df: 40, r1: 5, r2: 10, dmg: 800, explosive: true },
  { name: '轨道精准攻击', df: 50, r1: 4, r2: 12, dmg: 1500, explosive: true },
  { name: '轨道120MM高爆弹火力网', df: 50, r1: 3.3, r2: 10, dmg: 1200, explosive: true },
  { name: '轨道380MM高爆弹火力网', df: 50, r1: 4, r2: 12, dmg: 1500, explosive: true },
  { name: '轨道游走火力网', df: 50, r1: 4, r2: 12, dmg: 1500, explosive: true },
  { name: '轨道炮攻击', df: 50, r1: 2.5, r2: 2.5, dmg: 1000, explosive: true },
  { name: '轨道毒气攻击', df: 50, r1: 1, r2: 15, dmg: 0, explosive: true },
  { name: 'EAT-411 荡平者', df: 40, r1: 10, r2: 25, dmg: 2500, explosive: true },
  { name: 'MS-11 单兵导弹发射井', df: 40, r1: 10, r2: 25, dmg: 2500, explosive: true },
  { name: 'GP-20 最后通牒', df: 40, r1: 2, r2: 12, dmg: 2000, explosive: true },
  { name: 'MD-17 反坦克地雷', df: 40, r1: 3, r2: 7, dmg: 2000, explosive: true },
  { name: 'B/MD C4背包', df: 40, r1: 3, r2: 7, dmg: 2000, explosive: true },
  { name: 'G/40-K 热熔地雷', df: 40, r1: 2.5, r2: 6, dmg: 2000, explosive: true },
  { name: 'G-48 千兆级榴弹', df: 40, r1: 2, r2: 12, dmg: 2000, explosive: true },
  { name: 'G-60 反坦克追踪者', df: 40, r1: 4, r2: 12, dmg: 1800, explosive: true },
  { name: 'TED-63 炸药', df: 40, r1: 6, r2: 12, dmg: 1000, explosive: true },
  { name: 'B-100 便携式地狱火炸弹', df: 60, r1: 17, r2: 25, dmg: 10000, giant: true, explosive: true },
  { name: 'NUX-223 地狱火炸弹', df: 60, r1: 17, r2: 25, dmg: 10000, giant: true, explosive: true },
  { name: '超级地球武装部队大炮（迷你核弹）', df: 60, r1: 17, r2: 25, dmg: 10000, giant: true, explosive: true },
  { name: '超级地球武装部队大炮（高爆弹）', df: 50, r1: 4, r2: 12, dmg: 1500, explosive: true },
  { name: '超级地球武装部队大炮（炸弹）', df: 50, r1: 4, r2: 12, dmg: 1500, explosive: true },
  { name: '超级地球武装部队大炮（静电场）', df: 30, r1: 1, r2: 13, dmg: 0, explosive: true },
  { name: '超级地球武装部队大炮（凝固汽油弹）', df: 30, r1: 1.6, r2: 13, dmg: 250, explosive: true },
  { name: 'G-4 毒气榴弹', df: 30, r1: 2, r2: 7, dmg: 3, explosive: true },
  { name: 'G-12 高爆弹', df: 30, r1: 2.5, r2: 7, dmg: 800, explosive: true },
  { name: 'G-123 铝热弹', df: 30, r1: 1.5, r2: 2.5, dmg: 2000, explosive: true },
  { name: 'GL-21 榴弹发射器', df: 30, r1: 3.5, r2: 7.5, dmg: 400, explosive: true },
  { name: 'GL-28 弹链式榴弹发射器', df: 30, r1: 2, r2: 4, dmg: 150, explosive: true },
  { name: 'EAT-17 消耗性反坦克武器', df: 30, r1: 1.5, r2: 3, dmg: 150, explosive: true },
  { name: 'GR-8 无后坐力炮', df: 30, r1: 3, r2: 7.5, dmg: 800, explosive: true },
  { name: 'AC-8 机炮', df: 30, r1: 2, r2: 4, dmg: 150, explosive: true },
  { name: 'A/M-12 迫击哨戒炮', df: 30, r1: 3, r2: 9, dmg: 400, explosive: true },
  { name: 'RL-77 空爆火箭弹发射器（火箭爆炸）', df: 25, r1: 3, r2: 5, dmg: 150, explosive: true },
  { name: 'RL-77 空爆火箭弹发射器（集束爆炸）', df: 20, r1: 4, r2: 6, dmg: 500, explosive: true },
  { name: 'G-6 破片弹', df: 20, r1: 4, r2: 10, dmg: 500, explosive: true },
  { name: 'R-36 爆裂铳（爆炸）', df: 20, r1: 4, r2: 7, dmg: 225, explosive: true },
  { name: 'CB-9 爆燃弩（爆炸）', df: 20, r1: 3, r2: 6, dmg: 350, explosive: true },
  { name: 'LAS-99 类星体加农炮（爆炸）', df: 30, r1: 1.5, r2: 3, dmg: 150, explosive: true },
  { name: 'FAF-14 飞矛（爆炸）', df: 30, r1: 1.5, r2: 3, dmg: 200, explosive: true },
  { name: 'LAS-98 激光大炮', df: 20, r1: 0, r2: 0, dmg: 0, explosive: false },
  { name: 'APW-1 反器材步枪', df: 20, r1: 0, r2: 0, dmg: 0, explosive: false },
  { name: 'G-31 电弧', df: 20, r1: 0, r2: 0, dmg: 0, explosive: false }
];

const findS = (n) => STRUCTURES.find((x) => x.name === n) || STRUCTURES.find((x) => n && n.indexOf(x.name) >= 0);
const findB = (n) => BLASTS.find((x) => x.name === n) || BLASTS.find((x) => n && n.indexOf(x.name) >= 0);

/** 距离 d 处的拆毁值（截尾取整） */
function demolitionAt(blast, d) {
  const b = typeof blast === 'string' ? findB(blast) : blast;
  if (!b) throw new Error('未知爆炸');
  if (d <= b.r1) return b.df;
  if (d > b.r2) return 0;
  const x = (d - b.r1) / (b.r2 - b.r1);
  const f = b.giant ? 1 - x * x : 1 - Math.sqrt(x);
  return Math.floor(b.df * f);
}

/** 能否拆毁；返回 {ok, reason, maxDistance} */
function canDemolish(blastName, structureName) {
  const b = findB(blastName), s = findS(structureName);
  if (!b) return { ok: false, reason: 'unknown-blast' };
  if (!s) return { ok: false, reason: 'unknown-structure' };
  if (s.badr && !b.explosive) return { ok: false, reason: 'badr', need: s.df };
  if (b.df < s.df) {
    return { ok: false, reason: s.hp ? 'demolition-insufficient-has-hp' : 'demolition-insufficient', need: s.df, hp: s.hp || null };
  }
  if (b.df === s.df) return { ok: true, maxDistance: b.r1, need: s.df };
  const ratio = s.df / b.df;
  const x = b.giant ? Math.sqrt(1 - ratio) : Math.pow(1 - ratio, 2);
  return { ok: true, maxDistance: b.r1 + x * (b.r2 - b.r1), need: s.df };
}

module.exports = { STRUCTURES, BLASTS, demolitionAt, canDemolish };
