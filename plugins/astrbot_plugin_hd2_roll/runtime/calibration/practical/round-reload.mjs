/**
 * 逐发装填资料解析 + 持续射击节奏纯函数（泵动 / 战斗霰弹枪“打一填一”）。
 *
 * 职责边界：
 * - 只读 snapshot item.infobox 里明确以逐发装填命名的字段，不接受“Rounds Reload”特性本身作为装填时间。
 * - 只严格接受 s / ms 两种单位；其他或缺失单位一律 null，不猜。
 * - Empty（空仓第一发）与 Partial（仓内仍有弹时的补一发）分开保存，绝不互换。
 * - 不产出容量、不合并多弹种/多枪管库存：capacity 由调用方显式给出。
 * - 不产出武器分，不写入任何既有生成器/评分路径。
 *
 * 节奏模型（全部为工程形状，不是源表证明的实测节奏）：
 * - full_empty：打空整管再装满，长期每发周期 = interval_ms + full_empty_ms / capacity。
 * - top_up_one：打一发补一发，串行周期 = interval_ms + partial_one_ms，理想窗口重叠后为
 *   interval_ms + partial_one_ms - overlap_ms。
 * - overlap='fire_interval' 表示“用固有射击间隔覆盖部分装弹时长”的用户操作机制下的可复现窗口工程估计；
 *   只抵扣 min(interval_ms, partial_one_ms)，因此 top_up 周期 = max(interval_ms, partial_one_ms)，
 *   派生射速不可能超过原始射速。源表并未证明完全重叠，也不代表所有目标/操作下都能无损完成。
 */

const MS_PER_SECOND = 1000;

export const ROUND_RELOAD_KEYS = Object.freeze({
  fullEmpty: 'rounds_reload_full_time',
  oneRound: 'reload_time_for_one_round',
  following: 'reload_time_for_1_round',
});

const ROUND_RELOAD_LABELS = Object.freeze({
  fullEmpty: 'Rounds Reload Full Time',
  oneRound: 'Reload Time For One Round',
  following: 'Reload Time for >1 Round',
});

const OVERLAP_MODES = Object.freeze(['none', 'fire_interval']);
const TIME_TOKEN_SOURCE = '(\\d+(?:\\.\\d+)?)\\s*(ms|s)(?![a-z])';
const ROUND_QUALIFIER_SOURCE = `${TIME_TOKEN_SOURCE}\\s*\\(\\s*(empty|partial)\\s*\\)`;

function round(value, digits = 6) {
  if (!Number.isFinite(value)) return null;
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function toMilliseconds(numberText, unit) {
  const parsed = Number(numberText);
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  return round(unit === 'ms' ? parsed : parsed * MS_PER_SECOND, 3);
}

function isObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function infoboxEntries(item) {
  const infobox = isObject(item) ? item.infobox : null;
  if (Array.isArray(infobox)) return infobox.filter(isObject);
  if (isObject(infobox)) {
    return Object.entries(infobox)
      .filter(([, value]) => isObject(value))
      .map(([key, value]) => ({ key, ...value }));
  }
  return [];
}

function fieldRaws(entries, key) {
  const seen = new Set();
  const raws = [];
  for (const entry of entries) {
    if (typeof entry.key !== 'string' || entry.key.trim() !== key) continue;
    let raw = '';
    if (typeof entry.raw === 'string' && entry.raw.trim() !== '') raw = entry.raw;
    else if (Array.isArray(entry.values)) raw = entry.values.filter((v) => typeof v === 'string').join('\n');
    if (raw !== '' && !seen.has(raw)) {
      seen.add(raw);
      raws.push(raw);
    }
  }
  return raws;
}

function scanTokens(text) {
  const pattern = new RegExp(TIME_TOKEN_SOURCE, 'gi');
  const tokens = [];
  let match;
  while ((match = pattern.exec(text)) !== null) {
    const unit = match[2].toLowerCase();
    tokens.push({ token: match[0].trim(), unit, ms: toMilliseconds(match[1], unit) });
  }
  return tokens;
}

/** 同一字段内出现多个不同数值（如 Upgraded 行、多弹种行）时不给单一值。 */
function resolveTokens(tokens, raws) {
  const values = new Set(tokens.map((token) => token.ms));
  if (values.size === 1) {
    return { ms: tokens[0].ms, token: tokens[0].token, unit: tokens[0].unit, reason: null };
  }
  const tokenText = tokens.length > 0 ? tokens.map((token) => token.token).join(' | ') : null;
  if (values.size > 1) return { ms: null, token: tokenText, unit: null, reason: 'ambiguous_multiple_values' };
  return { ms: null, token: null, unit: null, reason: raws.length > 0 ? 'unparsable' : 'missing' };
}

function parseSingleField(entries, key) {
  const raws = fieldRaws(entries, key);
  return { ...resolveTokens(scanTokens(raws.join('\n')), raws), raw: raws.join('\n') };
}

/** reload_time_for_one_round 的 Empty / Partial 必须分开；无标注的裸值不分配到任何一侧。 */
function parseOneRoundField(entries) {
  const raws = fieldRaws(entries, ROUND_RELOAD_KEYS.oneRound);
  const buckets = { empty: [], partial: [], unlabeled: [] };
  for (const raw of raws) {
    const labeled = new RegExp(ROUND_QUALIFIER_SOURCE, 'gi');
    let match;
    while ((match = labeled.exec(raw)) !== null) {
      const unit = match[2].toLowerCase();
      buckets[match[3].toLowerCase()].push({ token: match[0].trim(), unit, ms: toMilliseconds(match[1], unit) });
    }
    const remainder = raw.replace(new RegExp(ROUND_QUALIFIER_SOURCE, 'gi'), ' ');
    buckets.unlabeled.push(...scanTokens(remainder));
  }
  return {
    raw: raws.join('\n'),
    empty: resolveTokens(buckets.empty, []),
    partial: resolveTokens(buckets.partial, []),
    unlabeled: buckets.unlabeled,
  };
}

function makeEvidence(valueName, key, label, resolved, revid, extra = {}) {
  const entry = {
    value: valueName,
    key,
    label,
    raw: extra.raw !== undefined ? extra.raw : resolved.raw || '',
    token: resolved.token === undefined ? null : resolved.token,
    unit: resolved.unit === undefined ? null : resolved.unit,
    value_ms: resolved.ms === undefined ? null : resolved.ms,
    revid,
  };
  if (extra.qualifier) entry.qualifier = extra.qualifier;
  return entry;
}

function readRevid(item) {
  const provenance = isObject(item) ? item.provenance : null;
  const revid = isObject(provenance) ? provenance.revid : null;
  return Number.isInteger(revid) ? revid : null;
}

/**
 * 从 snapshot 装备条目解析逐发装填资料。
 * @param {object} item snapshot item（建议 category='primary' && kind='weapon' 的武器条目）
 * @returns {{status:'KNOWN'|'PARTIAL'|'UNKNOWN', full_empty_ms:number|null,
 *   partial_one_ms:number|null, empty_one_ms:number|null, following_one_ms:number|null,
 *   evidence:Array<object>, notes:string[]}}
 */
export function roundReloadProfile(item) {
  const evidence = [];
  const notes = [];
  const entries = infoboxEntries(item);
  const revid = readRevid(item);

  if (entries.length === 0) {
    notes.push('missing:infobox');
    return {
      status: 'UNKNOWN',
      full_empty_ms: null,
      partial_one_ms: null,
      empty_one_ms: null,
      following_one_ms: null,
      evidence,
      notes,
    };
  }
  if (revid === null) notes.push('missing:provenance.revid');

  const full = parseSingleField(entries, ROUND_RELOAD_KEYS.fullEmpty);
  const oneRound = parseOneRoundField(entries);
  const following = parseSingleField(entries, ROUND_RELOAD_KEYS.following);

  if (full.reason) notes.push(`${full.reason}:${ROUND_RELOAD_KEYS.fullEmpty}`);
  if (following.reason) notes.push(`${following.reason}:${ROUND_RELOAD_KEYS.following}`);
  if (oneRound.raw === '') {
    notes.push(`missing:${ROUND_RELOAD_KEYS.oneRound}`);
  } else if (oneRound.empty.reason === 'missing' && oneRound.partial.reason === 'missing') {
    notes.push(`unlabeled:${ROUND_RELOAD_KEYS.oneRound}`);
    if (oneRound.unlabeled.length > 1) notes.push(`ambiguous_multiple_values:${ROUND_RELOAD_KEYS.oneRound}`);
  } else {
    if (oneRound.empty.reason) notes.push(`${oneRound.empty.reason}:${ROUND_RELOAD_KEYS.oneRound}:empty`);
    if (oneRound.partial.reason) notes.push(`${oneRound.partial.reason}:${ROUND_RELOAD_KEYS.oneRound}:partial`);
    if (oneRound.unlabeled.length > 0) notes.push(`unlabeled:${ROUND_RELOAD_KEYS.oneRound}`);
  }

  const fullEmptyMs = full.ms;
  const emptyOneMs = oneRound.empty.ms;
  const partialOneMs = oneRound.partial.ms;
  const followingOneMs = following.ms;

  if (fullEmptyMs !== null) {
    evidence.push(makeEvidence('full_empty_ms', ROUND_RELOAD_KEYS.fullEmpty, ROUND_RELOAD_LABELS.fullEmpty, full, revid));
  }
  if (emptyOneMs !== null) {
    evidence.push(
      makeEvidence('empty_one_ms', ROUND_RELOAD_KEYS.oneRound, ROUND_RELOAD_LABELS.oneRound, oneRound.empty, revid, {
        raw: oneRound.raw,
        qualifier: 'empty',
      }),
    );
  }
  if (partialOneMs !== null) {
    evidence.push(
      makeEvidence('partial_one_ms', ROUND_RELOAD_KEYS.oneRound, ROUND_RELOAD_LABELS.oneRound, oneRound.partial, revid, {
        raw: oneRound.raw,
        qualifier: 'partial',
      }),
    );
  }
  if (followingOneMs !== null) {
    evidence.push(makeEvidence('following_one_ms', ROUND_RELOAD_KEYS.following, ROUND_RELOAD_LABELS.following, following, revid));
  }

  // “Rounds Reload”特性只是标签：没有逐发装填字段就不套用（多枪管/下挂主射击模式不得自动继承）。
  const hasRoundTrait = fieldRaws(entries, 'traits').some((raw) => /rounds reload/i.test(raw));
  if (hasRoundTrait && evidence.length === 0) {
    notes.push('trait_rounds_reload_without_round_fields');
  }

  // 多弹种容量（如 "45 (4mm)\n4 (10g)"、"8 (x2)"）本模块不合并，只提示调用方自行给出容量。
  const capacityRaws = fieldRaws(entries, 'capacity');
  if (capacityRaws.some((raw) => raw.includes('\n') || /\(x\s*\d+\)/i.test(raw))) {
    notes.push('capacity_declares_multiple_ammo_types');
  }

  const knownCount = [fullEmptyMs, emptyOneMs, partialOneMs, followingOneMs].filter((value) => typeof value === 'number').length;
  const status = knownCount === 4 ? 'KNOWN' : knownCount === 0 ? 'UNKNOWN' : 'PARTIAL';

  return {
    status,
    full_empty_ms: fullEmptyMs,
    partial_one_ms: partialOneMs,
    empty_one_ms: emptyOneMs,
    following_one_ms: followingOneMs,
    evidence,
    notes,
  };
}

function makeCandidate(strategy, cycleMs, overlapMs, assumptions) {
  return {
    strategy,
    cycle_ms: round(cycleMs, 6),
    rounds_per_second: round(MS_PER_SECOND / cycleMs, 6),
    overlap_ms: round(overlapMs, 6),
    assumptions,
  };
}

function isPositiveNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

/**
 * 持续射击节奏：比较“空仓整管”与“打一填一”。
 * @param {object} options
 * @param {number} options.interval_ms 原始射击间隔（ms，来自 fire rate，调用方换算）
 * @param {number} options.capacity 单次装填所服务的弹量（正整数，调用方按单一弹种给出）
 * @param {object} options.profile roundReloadProfile(...) 的返回
 * @param {'none'|'fire_interval'} [options.overlap] 重叠假设；默认 none（保守串行）
 * @returns {{status:'READY'|'PARTIAL'|'UNKNOWN', candidates:Array<object>, best:object|null, missing:string[]}}
 */
export function roundReloadCadence(options = {}) {
  const source = isObject(options) ? options : {};
  const intervalMs = source.interval_ms;
  const capacity = source.capacity;
  const profile = source.profile;
  const overlap = source.overlap === undefined ? 'none' : source.overlap;

  const invalid = [];
  const missing = [];
  const hasInterval = intervalMs !== undefined && intervalMs !== null;
  const hasCapacity = capacity !== undefined && capacity !== null;
  const intervalOk = isPositiveNumber(intervalMs);
  const capacityOk = isPositiveNumber(capacity) && Number.isInteger(capacity);
  const overlapOk = OVERLAP_MODES.includes(overlap);
  const profileOk = isObject(profile);
  const hasProfile = profile !== undefined && profile !== null;
  if (hasInterval && !intervalOk) invalid.push('invalid:interval_ms');
  if (hasCapacity && !capacityOk) invalid.push('invalid:capacity');
  if (!overlapOk) invalid.push(`invalid:overlap:${String(overlap)}`);
  if (hasProfile && !profileOk) invalid.push('invalid:profile');
  if (invalid.length > 0) {
    return { status: 'UNKNOWN', candidates: [], best: null, missing: invalid };
  }
  // 必需输入缺失与“给错值”分开：缺 interval_ms/profile 时任何候选都无法成立。
  const absent = [];
  if (!hasProfile) absent.push('profile');
  if (!intervalOk) absent.push('interval_ms');
  if (absent.length > 0) {
    return { status: 'UNKNOWN', candidates: [], best: null, missing: absent };
  }

  const candidates = [];

  if (isPositiveNumber(profile.full_empty_ms)) {
    if (!capacityOk) {
      // 容量缺失/不合法只影响本候选，不默认成 1 或合并多弹种库存。
      missing.push('capacity');
    } else {
      const cycleMs = intervalMs + profile.full_empty_ms / capacity;
      candidates.push(
        makeCandidate('full_empty', cycleMs, 0, [
          'empty magazine is reloaded to full: cycle amortises full_empty_ms over capacity rounds',
          'per-round long-run cycle = interval_ms + full_empty_ms / capacity',
          'no overlap between a full magazine swap and firing is modelled (overlap_ms = 0)',
          'geometry assumes every magazine is emptied before the next full reload',
        ]),
      );
    }
  } else {
    missing.push('full_empty_ms');
  }

  if (isPositiveNumber(profile.partial_one_ms)) {
    const overlapMs = overlap === 'fire_interval' ? Math.min(intervalMs, profile.partial_one_ms) : 0;
    const cycleMs = intervalMs + profile.partial_one_ms - overlapMs;
    const assumptions = [
      'one round fired, then one round inserted: cycle = interval_ms + partial_one_ms - overlap_ms',
      'partial_one_ms (top-up while the magazine still holds rounds) is used, not empty_one_ms',
      'following_one_ms is not used by this single-round steady-state estimate',
    ];
    if (overlap === 'fire_interval') {
      assumptions.push(
        'overlap_mode=fire_interval is an engineering estimate of a reproducible window under the user-described shoot-then-top-up mechanic',
        'the source table does not prove reload/fire overlap; only min(interval_ms, partial_one_ms) is discounted',
        'cycle = max(interval_ms, partial_one_ms) so the derived rate never exceeds the original fire rate',
      );
    } else {
      assumptions.push('overlap_mode=none: firing and top-up insert are strictly serial (conservative baseline)');
    }
    candidates.push(makeCandidate('top_up_one', cycleMs, overlapMs, assumptions));
  } else {
    missing.push('partial_one_ms');
  }

  let best = null;
  for (const candidate of candidates) {
    if (best === null || candidate.cycle_ms < best.cycle_ms) best = candidate;
  }

  const status = candidates.length === 0 ? 'UNKNOWN' : missing.length > 0 ? 'PARTIAL' : 'READY';
  return { status, candidates, best, missing };
}
