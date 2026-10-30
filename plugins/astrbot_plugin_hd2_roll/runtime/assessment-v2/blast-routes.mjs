import {finiteBlast} from './mechanisms.mjs';

// Only declared stable placements enter this path. A one-shot HP domain is not
// a damage value; repeated hits come from a measured part and a bound component.
export function repeatedBlastRoutes(row, targetId, model) {
  const inputs = model.blast_routes;
  if (!inputs || row.facts.inventory.kind !== 'finite') return [];
  const routes = [];
  for (const placement of inputs.placements.filter(p => p.equipment_id === row.equipment_id
    && p.target_id === targetId && p.repeatable)) {
    const key = `${targetId}:${placement.part_id}`, panel = model.targets[key], extra = inputs.parts[key];
    const blast = row.facts.areas.find(a => a.id === placement.component_id);
    if (!panel?.fatal || !extra || extra.residual_threat_ms !== 0 || !blast) continue;
    const result = finiteBlast({blast, ap: model.ap, geometry: {id: key, basis: placement.basis},
      targets: [{...panel, ...extra, instance_id: targetId,
        distance_m: placement.distance_m, line_of_sight: placement.line_of_sight}]});
    if (result.status !== 'ESTIMATE' || !(result.hits[0].damage > 0)) continue;
    const damage = result.hits[0].damage, rounds = Math.ceil(panel.hp / damage);
    const delay = row.equipment_id === 'g_123_thermite' ? model.scenario.thermite_effect_ms : row.facts.fuse_ms;
    if (!Number.isSafeInteger(delay) || delay < 0) continue;
    routes.push({weapon_id: row.equipment_id, mode_id: blast.id, rounds,
      prepare_ms: 0, release_ms: model.scenario.throw_ms, interval_ms: 1, effect_delay_ms: delay,
      damage_per_round: damage, target_hp: panel.hp, per_round_damage: damage,
      route: 'REPEATED_STABLE_BLAST_PART', one_shot: rounds === 1,
      measured_target: key, condition: placement.condition, explosion_resistance: extra.explosion_resistance,
      placement_basis: placement.basis, source_ref: row.facts.source_ref, target_source: extra.source_url,
      confirmed_rounds: placement.confirmed_rounds, damage_policy: placement.damage_policy});
  }
  return routes;
}
