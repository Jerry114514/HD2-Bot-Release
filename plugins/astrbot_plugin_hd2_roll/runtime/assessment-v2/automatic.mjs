import {packetEfficiency} from './strategic.mjs';
import {capacityCurve, REALIZATION} from './formulas.mjs';

const ROLES = ['chaff', 'close_defense', 'anti_medium'];
const IDS = new Set(['backpack_ax_ar_23_guard_dog', 'backpack_ax_arc_3_k_9',
  'backpack_ax_las_5_guard_dog_rover', 'backpack_ax_tx_13_purifier', 'backpack_ax_flam_75_hot_dog']);
const finite = n => Number.isFinite(n) && n > 0;
const unknown = (row, role, reason, extra = {}) => ({
  equipment_id: row.equipment_id, role, contribution: {status: 'UNKNOWN', value: null, reason, ...extra}
});

// Continuous operation is a candidate conditional on live, accessible targets.
// It is auxiliary to the held weapon; it must not become a held-gun mode.
function referenceAndTargets(model, role) {
  const requests = model?.scenario?.[role === 'chaff' ? 'chaff' : 'medium'];
  const ref = model?.equipment?.find(x => x.equipment_id === model?.references?.[role]);
  const modes = ref?.capabilities?.modes ?? [];
  if (!Array.isArray(requests) || !requests.length || !modes.length) return null;
  const targets = requests.map(({id, count}) => ({
    target: model.targets?.[role === 'anti_medium' ? `${id.split(':')[0]}:main` : id], count
  }));
  if (targets.some(x => !x.target || !Number.isSafeInteger(x.count) || x.count <= 0)) return null;
  let rate;
  if (role === 'chaff') {
    const rates = modes.map(m => m.chaff?.sustained?.targets_per_second).filter(finite);
    rate = rates.length ? Math.max(...rates) : null;
  } else {
    const times = requests.map(({id, count}) => {
      const values = modes.map(m => m.anti_medium?.find(t => t.target_id === id)?.response?.repeat_cycle_ms)
        .filter(finite);
      return values.length ? count * Math.min(...values) / 1000 : null;
    });
    rate = times.every(finite) ? requests.reduce((n, x) => n + x.count, 0) / times.reduce((a, b) => a + b, 0) : null;
  }
  return finite(rate) ? {targets, ref, rate} : null;
}

export function assessAutomatic(row, model) {
  if (!IDS.has(row?.equipment_id)) return [];
  const id = row.equipment_id;
  return ROLES.map(role => {
    if (role === 'close_defense') return unknown(row, role, 'AUTOMATIC_CLOSE_RESPONSE_AND_TARGET_SWITCH_REQUIRED');
    if (id === 'backpack_ax_las_5_guard_dog_rover')
      return unknown(row, role, 'AUTOMATIC_LASER_DWELL_HEAT_AND_SWITCH_REQUIRED');
    if (id === 'backpack_ax_tx_13_purifier')
      return unknown(row, role, 'AUTOMATIC_GAS_EMISSION_AND_REFILL_TIMING_REQUIRED');
    if (id === 'backpack_ax_flam_75_hot_dog')
      return unknown(row, role, 'AUTOMATIC_FLAME_EMISSION_AND_REFILL_TIMING_REQUIRED');
    const isGuard = id === 'backpack_ax_ar_23_guard_dog';
    const modeId = isGuard ? 'AR-23P_P' : 'AXARC-3_K-9_A';
    const mode = row.facts?.modes?.find(x => x.mode_id === modeId);
    if (!mode || !['normal', 'durable', 'pen'].every(k => Number.isFinite(mode[k])))
      return unknown(row, role, 'BOUND_AUTOMATIC_DAMAGE_REQUIRED');
    if (isGuard && (mode.capacity !== 45 || mode.rpm !== 660))
      return unknown(row, role, 'GUARD_DOG_FIRST_MAGAZINE_OR_RATE_UNVERIFIED');
    const reference = referenceAndTargets(model, role);
    if (!reference || !model.ap) return unknown(row, role, 'REFERENCE_OR_TARGET_REQUIRED');
    // Isolate the exact projectile/arc. No blast, chained-hit or automatic
    // ignition credit, and no precision-head assumption for medium targets.
    const packetRow = {facts: {modes: [mode], areas: [], effects: []},
      strategic_profile: {payload_mode_id: modeId, gas: false}};
    const efficiency = packetEfficiency(packetRow, reference.targets, model.ap);
    if (efficiency.status !== 'ESTIMATE') return unknown(row, role, efficiency.reason);
    const q = efficiency.target_equivalents_per_round;
    if (!Number.isFinite(q) || q < 0) return unknown(row, role, 'INVALID_PACKET_EFFICIENCY');
    if (isGuard) {
      // A conditional first magazine is not an unconditional lower bound:
      // hit, target access and time to switch have not been established.
      return unknown(row, role, 'REARM_AND_TARGET_SWITCH_TIMING_REQUIRED', {
        physical: {first_magazine_rounds: 45, fire_rate_rpm: 660,
          nominal_fire_seconds: 45 / 11, conditional_target_equivalents: Math.min(
            reference.targets.reduce((n, x) => n + x.count, 0), 45 * q),
          reference_rate: reference.rate},
        efficiency, assumptions: ['CONDITIONAL_DECLARED_HITS_ONLY', 'ONE_FIRST_MAGAZINE_ONLY',
          'NO_RELOAD_OR_FULL_DURATION_CREDIT', 'NO_SWITCH_TIME_OR_HIT_PROBABILITY_INFERRED']});
    }
    // Wiki tactical observation is approximate, not an exact engine timing.
    const cycle = 5;
    const rate = q / cycle;
    const ratio = rate / reference.rate;
    // Engineering reuse of existing turret factor, not user calibration. The
    // candidate assumes targets are in range and the drone remains active; it
    // does not credit chaining or stun. No deployment-CD cycle is invented.
    return {equipment_id: id, role, contribution: {
      status: 'KNOWN', value: capacityCurve(ratio) * REALIZATION.automatic_backpack,
      source: 'AUTOMATIC_BACKPACK_AUXILIARY', calibration_status: 'FORMULA_CANDIDATE',
      not_a_loadout_verdict: true, realization_factor: REALIZATION.automatic_backpack,
      physical: {cycle_seconds: cycle, target_equivalents_per_second: rate,
        reference_rate: reference.rate, capacity_ratio: ratio},
      efficiency, source_url: 'https://helldivers.wiki.gg/wiki/AX/ARC-3_K-9',
      assumptions: ['APPROXIMATE_WIKI_FIVE_SECOND_CYCLE_CANDIDATE',
        'IN_RANGE_ALERTED_ACCESSIBLE_TARGETS_AND_ACTIVE_DRONE',
        'ONE_DIRECT_ARC_ONLY_NO_CHAIN_OR_STUN_CREDIT',
        'AUTOMATIC_TURRET_FACTOR_ENGINEERING_REUSE_NOT_USER_CALIBRATION',
        'NOT_A_PLAYER_HELD_WEAPON', 'NO_HIT_PROBABILITY_ESTIMATED']
    }};
  });
}
