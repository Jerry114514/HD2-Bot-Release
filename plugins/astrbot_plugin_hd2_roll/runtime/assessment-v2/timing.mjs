// Shared readiness constraints for capability estimates and finite resource plans.
// Times are integer milliseconds. Aim/recoil may overlap a reload; charging a
// fresh round follows it. This is an explicit engineering policy, not animation data.
export function shotAt({ready_at, previous_at = null, interval_ms, acquire_ms = 0,
  warmup_ms = 0, charge_ms = 0, work_ms = 0, new_target = false}) {
  if (![ready_at, interval_ms, acquire_ms, warmup_ms, charge_ms, work_ms].every(n => Number.isFinite(n) && n >= 0)
    || previous_at !== null && (!Number.isFinite(previous_at) || previous_at < 0)) throw new TypeError('INVALID_SHOT_CLOCK');
  return Math.ceil(previous_at === null ? ready_at + acquire_ms + warmup_ms + charge_ms + work_ms
    : Math.max(previous_at + interval_ms, previous_at + (new_target ? acquire_ms + work_ms : 0), ready_at + charge_ms + work_ms));
}

export function reloadChoice({capacity, interval_ms, reload_ms, charge_ms = 0, round_reload_profile}) {
  const full = Number.isFinite(reload_ms) && reload_ms >= 0
    ? ((capacity - 1) * interval_ms + Math.max(interval_ms, reload_ms + charge_ms)) / capacity : null;
  const partial = round_reload_profile?.partial_one_ms;
  const top = capacity > 1 && Number.isFinite(partial) && partial > 0 ? Math.max(interval_ms, partial + charge_ms) : null;
  return {strategy: top !== null && (full === null || top <= full) ? 'top_up_one' : 'full_empty',
    full_mean_interval_ms: full, top_up_interval_ms: top};
}

const gcd = (a, b) => b ? gcd(b, a % b) : a;

// Exact periodic tempo for a repeating target pattern. Counts reload boundaries
// over the joint ammunition/target period without an arbitrarily long simulation.
// It assumes replenishable stock ONLY to measure tempo; ammunition sufficiency is
// always a separate finite ledger test. The free initial magazine is not repeated.
export function sustainedTempo({entries, capacity, interval_ms, acquire_ms = 0,
  reload_ms, charge_ms = 0, work_ms = 0, round_reload_profile}) {
  if (!Number.isSafeInteger(capacity) || capacity < 1 || !(interval_ms > 0) || !Number.isFinite(work_ms) || work_ms < 0
    || !entries?.length || entries.some(e => !Number.isSafeInteger(e.btk) || e.btk < 1 || !Number.isSafeInteger(e.count) || e.count < 1)) {
    return {status: 'UNKNOWN', reason: 'SUSTAINED_PATTERN_REQUIRED'};
  }
  const shots = entries.reduce((n, e) => n + e.btk * e.count, 0), count = entries.reduce((n, e) => n + e.count, 0);
  const divisor = gcd(shots, capacity), reloadFrequency = divisor / capacity;
  const partial = round_reload_profile?.partial_one_ms;
  const fullKnown = Number.isFinite(reload_ms) && reload_ms >= 0;
  const topKnown = capacity > 1 && Number.isFinite(partial) && partial > 0;
  let fullTime = 0, topTime = 0, index = 0;
  for (const [position, e] of entries.entries()) for (let n = 0; n < e.count; n++) for (let shot = 1; shot <= e.btk; shot++) {
    index++;
    const next = n + 1 < e.count ? e : entries[(position + 1) % entries.length];
    const base = Math.max(interval_ms, shot === e.btk ? (next.acquire_ms ?? acquire_ms) + work_ms : 0);
    fullTime += base + (fullKnown && index % divisor === 0 ? reloadFrequency * (Math.max(base, reload_ms + charge_ms + work_ms) - base) : 0);
    if (topKnown) topTime += Math.max(base, partial + charge_ms);
  }
  const candidates = [];
  if (fullKnown) candidates.push({strategy: 'full_empty', pattern_ms: fullTime});
  if (topKnown) candidates.push({strategy: 'top_up_one', pattern_ms: topTime});
  candidates.sort((a, b) => a.pattern_ms - b.pattern_ms);
  if (!candidates.length) return {status: 'UNKNOWN', reason: 'SUSTAINED_RELOAD_REQUIRED'};
  const best = candidates[0];
  return {status: 'ESTIMATE', ...best, shots_per_pattern: shots, targets_per_pattern: count,
    targets_per_second: 1000 * count / best.pattern_ms, candidates,
    scope: 'REPEATING_FEED_TEMPO_ONLY; FINITE_STOCK_CHECK_REQUIRED'};
}
