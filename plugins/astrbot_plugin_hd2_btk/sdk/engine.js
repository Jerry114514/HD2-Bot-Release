/* Helldivers 2 BTK 引擎 —— v2
 * 范围：原版实弹 / 激光射弹 / 电弧；**不含爆炸、DoT、穿透减速、光束(beam)**
 *
 * 规则（helldivers.wiki.gg/zh，pageid 2381）：
 *   §甲弹对抗   AP>AV 100%，AP=AV 65%，AP<AV 0
 *   §耐久伤害   base = 标准×(1−耐久) + 耐久伤×耐久
 *   §速度衰减   α = −4.71e−4·Cd·D²/m；rd = 0.75·rv² + 0.25
 *   §伤害传导   部位伤害 × 传导率 → 主体，单发封顶于「部位最大血量 × 传导率」
 *   §健康值&流血值  第二个池，生命值耗尽后启用；流血速度>0 由时间收走，=0 则是额外血量
 *   §致死部位   清空致死部位即致死（立即或流血），不需要再清空主体
 *   §取整       只在最终结果取整（敌人 HP 为整型）
 *
 * v2 新增：
 *   · 多弹丸：同时报「击发次数」和「需要命中的弹丸数」
 *   · 打残 / 打死：有流血速度的部位给两个数（打残 = 清空生命值池进入流血；打死 = 连健康值一起打掉）
 *   · 电弧：无弹道衰减，但有硬射程，超出即打不到
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.HD2 = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var K_CONST = 4.71e-4, RD_QUAD = 0.75, RD_FLOOR = 0.25;
  var ANGLE_NAMES = ['正面命中', '小角度命中', '大角度命中', '极端角度命中'];

  function alpha(w) { return w.drag ? -K_CONST * w.drag * w.caliber * w.caliber / w.mass : 0; }
  function velocityAt(w, s) { return w.drag ? w.v0 * Math.exp(alpha(w) * s) : w.v0; }
  function distanceFactor(w, s) {
    if (!w.drag) return 1;
    var rv = Math.exp(alpha(w) * s);
    return RD_QUAD * rv * rv + RD_FLOOR;
  }
  function penFactor(ap, av) {
    if (ap == null || av == null) return null;
    return ap > av ? 1 : ap === av ? 0.65 : 0;
  }
  function apOf(w, i) { return w.ap ? w.ap[Math.min(i || 0, w.ap.length - 1)] : null; }

  // "200 [70/s]" → {health:200, rate:70}（负号是"流失"写法）
  function bleedInfo(part) {
    if (!part.bleed) return null;
    var m = /([\d,]+)\s*\[\s*(-?[\d.]+)\s*\/\s*s\s*\]/.exec(String(part.bleed));
    if (!m) return null;
    return { health: Number(m[1].replace(/,/g, '')), rate: Math.abs(Number(m[2])) };
  }

  // 一个生命池：打残池（只清生命值）/ 打死池（连健康值）
  function bleedSeconds(obj) {
    var b = bleedInfo(obj);
    return b && b.rate > 0 ? b.health / b.rate : 0;
  }

  function pools(hp, bleed) {
    if (hp == null) return null;
    var b = bleedInfo({ bleed: bleed });
    var health = b ? b.health : 0, rate = b ? b.rate : 0;
    return {
      cripple: (rate > 0 && health > 0) ? hp : null,   // 打残：清空生命值 → 进入流血
      full: hp + (health || 0),                        // 打死：生命值 + 健康值
      health: health, rate: rate
    };
  }

  function inRange(w, s) { return !(w.range && s > w.range); }

  // 单颗弹丸伤害（不乘弹丸数）
  function perPelletDamage(w, part, s, opts) {
    opts = opts || {};
    if (!inRange(w, s)) return 0;
    var k = penFactor(apOf(w, opts.angle), part.av);
    if (!k) return 0;
    var dur = part.durability || 0;
    var base = w.dmg * (1 - dur) + (w.dmg2 == null ? w.dmg : w.dmg2) * dur;
    return base * k * distanceFactor(w, s);
  }

  // 兼容旧名：一轮齐射的总伤害
  function perShotDamage(w, part, s, opts) {
    return perPelletDamage(w, part, s, opts) * (w.pellets || 1);
  }

  function transmitPerShot(dmg, part) {
    if (part.to_main == null) return 0;
    var t = dmg * part.to_main;
    if (part.cap) {
      var p = pools(part.hp, part.bleed);
      if (p) t = Math.min(t, p.full * part.to_main);
    }
    return t;
  }

  // 把"要打掉多少血"换算成 {pellets, shots}
  function need(amount, per, pellets) {
    if (!(per > 0)) return null;
    var n = pellets || 1;
    return { pellets: Math.ceil(amount / (per / n)), shots: Math.ceil(amount / per) };
  }

  // 简化版爆炸：一次命中算三次（部位 A、主体直击、A 的传导）
  // 爆炸吃爆抗、吃自己的穿甲；标准=耐久，所以不吃部位耐久值
  // 简化版爆炸：内圈全额；爆抗 100% 的部位伤害转主体；只算用户勾选的部位 + 主体直击
  function explosionOf(w, part, enemy, s, opts) {
    var ex = w.explosion;
    var zero = { toPart: 0, toMain: 0, ap: null, raw: 0, detail: [] };
    if (!ex || !ex.dmg || !inRange(w, s)) return zero;
    opts = opts || {};
    var apIn = ex.ap[0];
    var mainPart = enemy.parts.find(function (p) { return p.part === '主体'; }) || { av: 4, exdr: 0 };
    var detail = [];
    // 主体直击一次
    var eM = pen2(apIn, mainPart.av) * ex.dmg * (1 - (mainPart.exdr || 0));
    detail.push({ part: '主体（直击）', v: eM });
    // 用户勾选的被波及部位
    (opts.blastParts || []).forEach(function (name) {
      if (name === part.part) return;                    // 击中部位单独算
      var p = enemy.parts.find(function (x) { return x.part === name; });
      if (!p) return;
      var d = pen2(apIn, p.av) * ex.dmg * (1 - (p.exdr || 0));
      if ((p.exdr || 0) >= 1) { eM += d; detail.push({ part: name + '（爆抗100%→主体）', v: d }); return; }
      if (p.to_main != null) eM += d * p.to_main;
      detail.push({ part: name, v: d * (p.to_main == null ? 0 : p.to_main), raw: d });
    });
    // 命中部位本体
    var eA = pen2(apIn, part.av) * ex.dmg * (1 - (part.exdr || 0));
    var transA = part.to_main == null ? 0 : eA * part.to_main;
    if (part.cap) { var q = pools(part.hp, part.bleed); if (q) transA = Math.min(transA, q.full * part.to_main); }
    if ((part.exdr || 0) >= 1) { eM += eA; eA = 0; transA = 0; }
    return { toPart: Math.floor(eA), toMain: Math.floor(eM + transA), ap: apIn, raw: ex.dmg, detail: detail };
  }
  function pen2(ap, av) { if (ap == null || av == null) return 0; return ap > av ? 1 : ap === av ? 0.65 : 0; }

  // 破片：用户给命中该部位的破片数，每片独立走甲弹+耐久
  function shrapnelOf(w, part, s, opts) {
    var sh = w.shrapnel;
    var n = (opts && opts.shrapnelHits) || 0;
    if (!sh || !n || !inRange(w, s)) return { toPart: 0, toMain: 0, per: 0, hits: 0 };
    var k = pen2(sh.ap[0], part.av);
    if (!k) return { toPart: 0, toMain: 0, per: 0, hits: n };
    var dur = part.durability || 0;
    var per = (sh.dmg * (1 - dur) + sh.dmg2 * dur) * k;
    var toPart = per * n;
    var toMain = part.to_main == null ? 0 : toPart * part.to_main;
    return { toPart: toPart, toMain: toMain, per: per, hits: n };
  }

  /* ---------- 击杀路径 ---------- */
  function killPaths(w, part, enemy, s, opts) {
    var per = perPelletDamage(w, part, s, opts);
    var pellets = w.pellets || 1;
    var mainPool = pools(enemy.mainHp, enemy.bleed);
    var partPool = pools(part.hp, part.bleed);
    var expl = explosionOf(w, part, enemy, s, opts);
    var shra = shrapnelOf(w, part, s, opts);
    var perPart = per * pellets + expl.toPart + shra.toPart;
    var perMain = (part.to_main == null ? per * pellets : per * pellets * part.to_main)
      + expl.toMain + shra.toMain;
    var out = { per: per, pellets: pellets, paths: [], outOfRange: !inRange(w, s), expl: expl, shra: shra, perPart: perPart, perMain: perMain };
    if (!(perPart > 0) && !(perMain > 0)) return out;

    function mk(kind, pool, perHit, label, bleedSec) {
      if (!(perHit > 0)) return null;
      var cripple = pool.cripple != null ? need(pool.cripple, perHit, pellets) : null;
      var full = need(pool.full, perHit, pellets);
      return { kind: kind, label: label, pool: pool, perHit: perHit,
        cripple: cripple, full: full, primary: cripple || full, bleedSec: bleedSec };
    }
    if (part.fatal && partPool && perPart > 0) {
      var p1 = mk('part', partPool, perPart, '打爆致死部位', partPool.rate > 0 ? partPool.health / partPool.rate : 0);
      if (p1) { p1.maxTransmit = partPool.full * (part.to_main == null ? 1 : part.to_main); out.paths.push(p1); }
    }
    if (mainPool && perMain > 0) {
      var shotsDestroyPart = partPool ? Math.ceil(partPool.full / Math.max(perPart, 1e-9)) : Infinity;
      var shotsMain = Math.ceil(mainPool.full / perMain);
      if (part.to_main == null || shotsMain <= shotsDestroyPart) {
        var p2 = mk('main', mainPool, perMain, part.to_main == null ? '打死主体' : '传导打死主体',
          mainPool.rate > 0 ? mainPool.health / mainPool.rate : 0);
        if (p2) out.paths.push(p2);
      }
    }
    if (!out.paths.length && partPool && perPart > 0) {
      var p3 = mk('destroy', partPool, perPart, '只能摧毁该部位', partPool.rate > 0 ? partPool.health / partPool.rate : 0);
      if (p3) { p3.maxTransmit = partPool.full * (part.to_main == null ? 1 : part.to_main); out.paths.push(p3); }
    }
    return out;
  }

  // 光束：累计照射时间（0.1 s 向上取整）
  // DoT：只作用于 main，有穿甲，按「触发次数 × 时长」累加
  function dotPlan(w, enemy, angle) {
    const main = enemy.parts.find(p => p.part === '主体') || enemy.parts[0];
    const av = main ? main.av : 0;
    const ap = w.ap[angle || 0] != null ? w.ap[angle || 0] : w.ap[0];
    const factor = ap > av ? 1 : (ap === av ? 0.65 : 0);
    const dur = w.duration || 1;
    const el = w.element ? ((enemy.mult && enemy.mult[w.element]) || 1) : 1;
    const per = w.dps * factor * dur * el;
    if (!(per > 0)) return { per: 0, triggers: Infinity, seconds: Infinity, av, ap, factor, dur, el: 1 };
    const triggers = Math.ceil(enemy.mainHp / per);
    // 最后一次触发只需部分时间：剩余伤害 ÷ 每秒伤害
    const dpsEff = per / dur;                       // 每秒有效伤害
    const lastFrac = dur - ((triggers * per - enemy.mainHp) / dpsEff);
    const raw = (triggers - 1) * dur + lastFrac;
    const seconds = Math.ceil(raw * 10) / 10;       // 0.1 s 向上取整
    return { per, triggers, seconds, raw, av, ap, factor, dur, el };
  }

  // TTK = (BTK-1) / (RPM/60)
  // 卡阈值：BTK=1 看余量是否 <11.1%；BTK=2/3 看前 n-1 发是否已打掉 >=90%
  function thresholdInfo(btk, perHit, pool) {
    if (!isFinite(btk) || !(pool > 0) || !(perHit > 0)) return null;
    if (btk > 3) return null;
    var ratio, missing;
    if (btk === 1) {
      ratio = perHit / pool;                 // 一发打掉的比例
      if (ratio > 1 / 0.9) return null;      // 余量 > 11.1%，不算卡
      missing = perHit - pool;               // 溢出量
      return { stuck: true, kind: 'one', ratio: 1 / ratio, overflow: missing };
    }
    ratio = ((btk - 1) * perHit) / pool;
    if (ratio < 0.9) return null;
    missing = pool - (btk - 1) * perHit;
    return { stuck: true, kind: 'n', ratio: ratio, missing: missing };
  }

  function ttk(btk, rpm) {
    if (!(rpm > 0) || !isFinite(btk) || btk < 1) return null;
    return (btk - 1) / (rpm / 60);
  }

  function beamTime(w, part, enemy, s, opts) {
    var per = perPelletDamage(w, part, s, opts);
    if (!(per > 0)) return Infinity;
    var rate = (w.rpm || 60) / 60;
    var tick = per * rate;
    var mp = pools(enemy.mainHp, enemy.bleed);
    var pp = pools(part.hp, part.bleed);
    var cands = [];
    if (mp) cands.push(mp.full / tick);
    if (part.fatal && pp) cands.push(pp.full / tick);
    if (!cands.length) return Infinity;
    return Math.ceil(Math.min.apply(null, cands) * 10) / 10;
  }

  function btkAt(w, part, enemy, s, opts) {
    if (typeof enemy === 'number') enemy = { mainHp: enemy };
    var r = killPaths(w, part, enemy, s, opts);
    var out = {
      per: r.per, pellets: r.pellets, paths: r.paths, outOfRange: r.outOfRange,
      btk: null, path: null, shots: null, pelletsNeeded: null,
      byPart: null, byMain: null,
      cripple: null, full: null, bleedSec: 0
    };
    for (var i = 0; i < r.paths.length; i++) {
      if (r.paths[i].kind === 'part') out.byPart = r.paths[i].primary.shots;
      if (r.paths[i].kind === 'main') out.byMain = r.paths[i].primary.shots;
    }
    if (!r.paths.length) { out.btk = Infinity; return out; }
    var best = r.paths[0];
    for (var j = 1; j < r.paths.length; j++) if (r.paths[j].primary.shots < best.primary.shots) best = r.paths[j];
    out.path = best.kind;
    out.shots = best.primary.shots;
    out.pelletsNeeded = best.primary.pellets;
    out.btk = best.primary.shots;
    out.cripple = best.cripple;
    out.full = best.full;
    out.bleedSec = best.bleedSec;
    out.maxTransmit = best.maxTransmit;
    out.label = best.label;
    return out;
  }

  /* ---------- 突变点 ---------- */
  function solveDistance(w, part, target, opts) {
    opts = opts || {};
    var k = penFactor(apOf(w, opts.angle), part.av);
    if (!k) return null;
    var dur = part.durability || 0;
    var base = w.dmg * (1 - dur) + (w.dmg2 == null ? w.dmg : w.dmg2) * dur;
    var A = base * k;
    if (!(A > 0)) return null;
    if (!w.drag) return target >= A ? 0 : null;
    var denom = (target / A - RD_FLOOR) / RD_QUAD;
    if (!(denom > 0)) return null;
    return Math.log(denom) / (2 * alpha(w));
  }

  function ladder(w, part, enemy, opts) {
    opts = opts || {};
    if (typeof enemy === 'number') enemy = { mainHp: enemy };
    var maxS = opts.maxS || 100, step = opts.step || 1;
    var rows = [], cur = null;
    for (var s = 0; s <= maxS + 1e-9; s += step) {
      var b = btkAt(w, part, enemy, s, opts).btk;
      if (!cur || cur.btk !== b) {
        if (cur) { cur.to = s; rows.push(cur); }
        cur = { from: s, to: null, btk: b };
      }
    }
    if (cur) { cur.to = maxS; rows.push(cur); }
    return rows;
  }

  function breakpoints(w, part, enemy, opts) {
    opts = opts || {};
    if (typeof enemy === 'number') enemy = { mainHp: enemy };
    var maxS = opts.maxS || 100;
    if (!isFinite(btkAt(w, part, enemy, 0, opts).btk)) return [];
    var d0 = perPelletDamage(w, part, 0, opts);
    var dE = perPelletDamage(w, part, maxS, opts);
    if (!(d0 > 0)) return [];
    var cand = [], mainPool = pools(enemy.mainHp, enemy.bleed);
    var partPool = pools(part.hp, part.bleed);

    function addThresholds(pool, factor, why) {
      if (!pool) return;
      var per0 = factor == null ? d0 : d0 * factor;
      var perE = factor == null ? dE : dE * factor;
      [pool.cripple, pool.full].forEach(function (amount) {
        if (amount == null) return;
        var a0 = Math.ceil(amount / per0), a1 = Math.ceil(amount / perE);
        if (!isFinite(a0) || !isFinite(a1) || a1 - a0 > 5000) { if (!isFinite(a1)) a1 = a0 + 5000; }
        for (var n = a0; n < a1; n++) cand.push({ s: solveDistance(w, part, (amount / n) / (factor == null ? 1 : factor), opts), why: why });
      });
    }
    if (partPool) addThresholds(partPool, null, part.fatal ? '致死部位被打爆' : '部位被打爆，停止传导');
    if (mainPool) addThresholds(mainPool, part.to_main == null ? null : part.to_main, '主体血量见底');

    var out = [];
    cand.sort(function (a, b) { return a.s - b.s; });
    for (var i = 0; i < cand.length; i++) {
      var s2 = cand[i].s;
      if (s2 == null || s2 < -1e-9 || s2 > maxS) continue;
      var eps = Math.max(1e-5, s2 * 1e-6);
      var rb = btkAt(w, part, enemy, Math.max(0, s2 - eps), opts);
      var ra = btkAt(w, part, enemy, s2 + eps, opts);
      if (!(ra.btk > rb.btk)) continue;
      var why = ra.path === 'part' ? '致死部位被打爆' : ra.path === 'destroy' ? '部位被打爆，停止传导' : '主体血量见底';
      if (out.length && Math.abs(out[out.length - 1].at - s2) < 1e-3) { out[out.length - 1].to = ra.btk; out[out.length - 1].why = why; continue; }
      out.push({ at: Math.max(0, s2), from: rb.btk, to: ra.btk, why: why });
    }
    return out;
  }

  function describe(w, part, enemy, s, opts) {
    if (typeof enemy === 'number') enemy = { mainHp: enemy };
    var r = btkAt(w, part, enemy, s, opts);
    var k = penFactor(apOf(w, (opts && opts.angle) || 0), part.av);
    var dur = part.durability || 0;
    var base = w.dmg * (1 - dur) + (w.dmg2 == null ? w.dmg : w.dmg2) * dur;
    var mp = pools(enemy.mainHp, enemy.bleed);
    return {
      alpha: alpha(w), rv: w.drag ? Math.exp(alpha(w) * s) : 1, rd: distanceFactor(w, s),
      k: k, base: base, per: r.per, perShot: r.per * (w.pellets || 1),
      dmgExact: r.per, dmgShown: Math.floor(r.per),
      transmit: part.to_main == null ? r.per : r.per * part.to_main,
      btk: r.btk, path: r.path, label: r.label, bleedSec: r.bleedSec,
      shots: r.shots, pelletsNeeded: r.pelletsNeeded, pellets: w.pellets || 1,
      cripple: r.cripple, full: r.full,
      byPart: r.byPart, byMain: r.byMain,
      partPool: pools(part.hp, part.bleed), mainPool: mp,
      outOfRange: r.outOfRange, maxTransmit: r.maxTransmit
    };
  }

  return {
    K_CONST: K_CONST, RD_QUAD: RD_QUAD, RD_FLOOR: RD_FLOOR, ANGLE_NAMES: ANGLE_NAMES,
    alpha: alpha, velocityAt: velocityAt, distanceFactor: distanceFactor,
    penFactor: penFactor, apOf: apOf, bleedInfo: bleedInfo, bleedSeconds: bleedSeconds, pools: pools,
    shootOutHp: function (p) { var q = pools(p.hp, p.bleed); return q ? q.full : null; },
    explosionOf: explosionOf, thresholdInfo: thresholdInfo, dotPlan: dotPlan, ttk: ttk, beamTime: beamTime, shrapnelOf: shrapnelOf, perPelletDamage: perPelletDamage, perShotDamage: perShotDamage,
    transmitPerShot: transmitPerShot, killPaths: killPaths, btkAt: btkAt,
    solveDistance: solveDistance, ladder: ladder, breakpoints: breakpoints, describe: describe
  };
});
