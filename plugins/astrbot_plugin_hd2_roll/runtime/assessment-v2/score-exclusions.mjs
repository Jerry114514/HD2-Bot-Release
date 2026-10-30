// Equipment identity is separate from ammo facts: even a charged melee weapon
// is excluded from endurance by user policy, without deleting its finite stock.
export const MELEE_EQUIPMENT_IDS=Object.freeze([
  'cqc_19_stun_lance','cqc_2_saber','cqc_30_stun_baton','cqc_42_machete',
  'cqc_5_combat_hatchet','cqc_73_entrenchment_tool','support_cqc_1_outcast',
  'support_cqc_20_breaching_hammer','support_cqc_9_defoliation_tool'
]);
const melee=new Set(MELEE_EQUIPMENT_IDS);
export const isMeleeEquipment=row=>melee.has(row?.equipment_id);
