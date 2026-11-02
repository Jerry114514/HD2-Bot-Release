const fs = require('fs');
const path = require('path');
const sdk = require('./index.js');
const { weapons, enemies } = sdk.data;

let issues = [];
const add = (kind, msg) => issues.push(`[${kind}] ${msg}`);

/* 1) 全量扫描：BST 异常值 */
let combos = 0, ok = 0, inf = 0;
for (const w of weapons) {
  if (w.type === 'dot') continue;
  for (const e of enemies) {
    for (const p of e.parts) {
      combos++;
      const r = sdk.compute({ weapon: w.id, enemy: e.enemy, part: p.part });
      if (!r.ok) { inf++; continue; }
      const b = r.btk;
      if (!Number.isInteger(b) || b < 1) add('BTK', `${w.id}/${e.enemy}/${p.part} btk=${b}`);
      if (r.shots < 1) add('SHOTS', `${w.id}/${e.enemy}/${p.part} shots=${r.shots}`);
      if (r.ttk != null && (r.ttk < 0 || Number.isNaN(r.ttk))) add('TTK', `${w.id}/${e.enemy}/${p.part} ttk=${r.ttk}`);
      if (r.perShotDamage != null && r.perShotDamage < 0) add('DMG', `${w.id}/${e.enemy}/${p.part} per=${r.perShotDamage}`);
      if (r.cripple && r.full && r.full.shots < r.cripple.shots) add('POOL', `${w.id}/${e.enemy}/${p.part} full<cripple`);
      ok++;
    }
  }
}
console.log(`扫描 ${combos} 组：有效 ${ok}，不可穿透/超程 ${inf}`);

/* 2) DoT 数学：逐条核对公式 */
let dotBad = 0;
for (const w of weapons.filter(x => x.type === 'dot')) {
  for (const e of enemies) {
    const d = sdk.compute({ weapon: w.id, enemy: e.enemy });
    const main = e.parts.find(p => p.part === '主体') || e.parts[0];
    const av = main ? main.av : 0;
    const ap = w.ap[0];
    const factor = ap > av ? 1 : ap === av ? 0.65 : 0;
    if (!factor) { if (d.ok) add('DOT', `${w.id}/${e.enemy} 应免疫却算成功`); continue; }
    const el = (e.mult && e.mult[w.element]) || 1;
    const dpsEff = w.dps * factor * el;
    const expect = Math.ceil((e.mainHp / dpsEff) * 10) / 10;
    if (Math.abs(d.seconds - expect) > 0.101) { add('DOT', `${w.id}/${e.enemy} 实=${d.seconds} 期=${expect}`); dotBad++; }
    if (d.seconds > d.triggers * w.duration + 1e-9) add('DOT', `${w.id}/${e.enemy} 时长超过满触发`);
    if (d.triggers < 1) add('DOT', `${w.id}/${e.enemy} 触发=${d.triggers}`);
  }
}
console.log(`DoT 核对完成，偏差 ${dotBad} 条`);

/* 3) 关键锚点回归 */
const anchor = [
  { w: 'AR-23', e: '武斗虫（低难）', p: '头部', btk: 2 },
  { w: 'R-63', e: '蹂躏者', p: '头部', btk: 1 },
  { w: 'P-113', e: '蹂躏者', p: '头部', btk: 1 },
  { w: 'SG-8S', e: '强袭虫', p: '头部' },
];
for (const a of anchor) {
  const r = sdk.compute({ weapon: a.w, enemy: a.e, part: a.p });
  if (a.btk != null && r.btk !== a.btk) add('ANCHOR', `${a.w}/${a.e}/${a.p} 期望 ${a.btk} 实得 ${r.btk}`);
}
const fire = sdk.compute({ weapon: 'DOT-FIRE', enemy: '武斗虫（高难）' });
if (Math.abs(fire.seconds - 4.1) > 1e-6) add('ANCHOR', `燃烧×高难武斗虫 期望 4.1s 实得 ${fire.seconds}`);

/* 4) 护盾/建筑完整性 */
const shieldUnits = enemies.filter(e => /（护盾）$/.test(e.enemy));
for (const s of shieldUnits) {
  const base = s.enemy.replace('（护盾）', '');
  if (!enemies.some(e => e.enemy === base || e.enemy.startsWith(base + '（'))) add('SHIELD', `${s.enemy} 找不到母体`);
  if (!(s.mainHp > 0)) add('SHIELD', `${s.enemy} 无血量`);
}
const builds = enemies.filter(e => e.enemy.includes('拆毁值'));
console.log(`护盾单位 ${shieldUnits.length} 个，建筑标注 ${builds.length} 个`);

/* 5) 数据完整性 */
for (const e of enemies) {
  if (!e.parts || !e.parts.length) add('DATA', `${e.enemy} 无部位`);
  if (!(e.mainHp > 0)) add('DATA', `${e.enemy} mainHp=${e.mainHp}`);
  if (!e.parts.some(p => p.part === '主体')) add('DATA', `${e.enemy} 缺「主体」`);
  for (const p of e.parts) {
    if (p.hp != null && (p.hp < 0 || Number.isNaN(p.hp))) add('DATA', `${e.enemy}/${p.part} hp=${p.hp}`);
    if (Number.isNaN(p.av)) add('DATA', `${e.enemy}/${p.part} av=NaN`);
  }
}
const names = new Set();
for (const e of enemies) { if (names.has(e.enemy)) add('DATA', `重名敌人 ${e.enemy}`); names.add(e.enemy); }

const wids = new Set();
for (const w of weapons) { if (wids.has(w.id)) add('DATA', `重名武器 ${w.id}`); wids.add(w.id); }

console.log(`\n发现问题 ${issues.length} 条`);
issues.slice(0, 40).forEach(x => console.log('  ' + x));
process.exitCode = issues.length ? 1 : 0;
