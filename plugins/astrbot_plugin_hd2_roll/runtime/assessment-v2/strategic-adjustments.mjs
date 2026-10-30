const COMBAT_ROLES = new Set(['chaff', 'close_defense', 'anti_medium', 'anti_heavy']);
const MORTAR_IDS = new Set([
  'deployable_mortar_sentry',
  'deployable_ems_mortar_sentry',
  'deployable_gas_mortar_sentry',
]);
const ADJUSTMENT_ID = 'USER_EAGLE_HALF_MORTAR_QUARTER_20261028';

// Apply to the finished automatic B x V x k x M contribution. Physical work,
// ammunition and final manual scores remain separate from this display factor.
export function applyStrategicCombatAdjustment(row, role, contribution) {
  if (!COMBAT_ROLES.has(role)
    || !(row?.category === 'eagle' || MORTAR_IDS.has(row?.equipment_id))
    || contribution?.score_origin === 'MANUAL_CALIBRATED'
    || contribution?.status !== 'KNOWN' || !Number.isFinite(contribution.value)
    || contribution.combat_adjustment?.id === ADJUSTMENT_ID) return contribution;
  const factor=MORTAR_IDS.has(row.equipment_id)?.25:.5;
  return {...contribution, value: contribution.value * factor,
    combat_adjustment: {id: ADJUSTMENT_ID, factor, before_value: contribution.value,
      basis: 'EAGLE_USER_HALF; MORTAR_FURTHER_DOWNWARD_ENGINEERING_POINT_0.25_PENDING_REVIEW'}};
}
