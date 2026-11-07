/* hd2-btk-sdk · 给 QQ 机器人 / 后端用的薄封装
 * 依赖：./engine.js（纯函数）、./data/*.json
 * 所有口径与网页版一致：只在最后一步取整；爆炸按内圈全额；TTK=(BTK-1)/(RPM/60)
 */
const path = require('path');
const E = require('./engine.js');
const weapons = require('./data/weapons.json');
const enemies = require('./data/enemies.json');

const byId = {};
for (const w of weapons) byId[w.id] = w;
const byEnemy = {};
for (const e of enemies) byEnemy[e.enemy] = e;

/** 武器列表（可按类别过滤） */
function listWeapons(filter) {
  const cat = filter && filter.cat;
  return weapons
    .filter((w) => !cat || w.cat === cat)
    .map((w) => ({ id: w.id, name: w.name, cat: w.cat, type: w.type, rpm: w.rpm || null }));
}

/** 敌人列表（可按阵营过滤），附部位摘要 */
function listEnemies(filter) {
  const f = filter && filter.faction;
  return enemies
    .filter((e) => !f || e.faction === f)
    .map((e) => ({
      enemy: e.enemy, faction: e.faction, mainHp: e.mainHp,
      parts: e.parts.map((p) => ({ part: p.part, hp: p.hp ?? null, av: p.av, fatal: p.fatal }))
    }));
}

/** 核心：算一次 */
function compute(opt) {
  const w = typeof opt.weapon === 'object' ? opt.weapon : byId[opt.weapon];
  const en = typeof opt.enemy === 'object' ? opt.enemy : byEnemy[opt.enemy];
  if (!w) throw new Error('未知武器：' + opt.weapon);
  if (!en) throw new Error('未知敌人：' + opt.enemy);
  if (w.type === 'dot') {
    const d = E.dotPlan(w, en, Number(opt.angle || 0));
    return { ok: isFinite(d.triggers), type: 'dot', weapon: w.name, enemy: en.enemy,
      perTrigger: d.per, triggers: d.triggers, seconds: d.seconds,
      ap: d.ap, av: d.av, factor: d.factor, element: w.element || null, mult: d.el };
  }

  const part = en.parts.find((p) => p.part === opt.part);
  if (!part) throw new Error('该敌人没有部位：' + opt.part);

  const s = Number(opt.distance || 0);
  const opts = { angle: Number(opt.angle || 0), shrapnelHits: Number(opt.shrapnelHits || 0), blastParts: opt.blastParts || [] };
  const r = E.btkAt(w, part, en, s, opts);
  const pr = r.paths && r.paths[0];

  const beamSec = w.type === 'beam' ? E.beamTime(w, part, en, s, opts) : null;
  if (!isFinite(r.btk)) {
    return { ok: false, reason: r.outOfRange ? 'out-of-range' : 'cannot-penetrate',
      message: r.outOfRange ? '超出射程' : '打不穿（AP 低于护甲）',
      beamSeconds: beamSec };
  }

  const pool = pr && pr.cripple && pr.primary === pr.cripple ? pr.pool.cripple : (pr ? pr.pool.full : null);
  const ti = pr ? E.thresholdInfo(r.btk, pr.perHit, pool) : null;

  return {
    ok: true,
    weapon: w.name, enemy: en.enemy, part: part.part, distance: s,
    btk: r.btk,
    shots: r.shots,
    pelletsNeeded: r.pelletsNeeded,
    pelletsPerShot: w.pellets || 1,
    ttk: w.type === 'beam' ? null : E.ttk(r.btk, w.rpm),
    beamSeconds: beamSec,
    path: r.path,
    label: pr ? pr.label : null,
    cripple: r.cripple ? { shots: r.cripple.shots, pellets: r.cripple.pellets } : null,
    full: r.full ? { shots: r.full.shots, pellets: r.full.pellets } : null,
    bleedSec: r.bleedSec || 0,
    perShotDamage: pr ? pr.perHit : null,
    stuck: ti ? { kind: ti.kind, ratio: ti.ratio, missing: ti.missing, overflow: ti.overflow } : null
  };
}

/** 卡阈值扫描：这把武器在哪些敌人身上差一发 */
function scanStuck(opt) {
  const w = typeof opt.weapon === 'object' ? opt.weapon : byId[opt.weapon];
  if (!w) throw new Error('未知武器：' + opt.weapon);
  const fatalOnly = opt.fatalOnly !== false;
  const out = [];
  for (const en of enemies) {
    for (const p of en.parts) {
      if (fatalOnly && !p.fatal) continue;
      const r = E.btkAt(w, p, en, 0, { angle: 0, shrapnelHits: 0, blastParts: [] });
      if (!isFinite(r.btk) || r.btk > 3 || !r.paths[0]) continue;
      const pr = r.paths[0];
      const pool = pr.cripple && pr.primary === pr.cripple ? pr.pool.cripple : pr.pool.full;
      const ti = E.thresholdInfo(r.btk, pr.perHit, pool);
      if (ti && ti.stuck) out.push({ enemy: en.enemy, part: p.part, btk: r.btk, ...ti });
    }
  }
  out.sort((a, b) => (a.kind === 'one') - (b.kind === 'one') || (b.ratio || 0) - (a.ratio || 0));
  return out;
}

const demo = require('./demolition.js');

module.exports = { compute, scanStuck, listWeapons, listEnemies, engine: E, data: { weapons, enemies },
  demolition: demo.canDemolish, demolitionAt: demo.demolitionAt, structures: demo.STRUCTURES, blasts: demo.BLASTS };
