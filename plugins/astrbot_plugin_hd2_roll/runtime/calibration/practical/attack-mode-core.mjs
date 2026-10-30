// Exact source-table links for one projectile and its impact explosion.
// This does not assume multi-part blast coverage or extra shrapnel hits.
const value=(table,key)=>table?.rows.find(r=>r.key===key)?.raw;
const number=s=>{const m=/^\s*([\d,.]+)/.exec(String(s??''));return m?Number(m[1].replaceAll(',','')):null;};
export function projectileMechanics(item,table,armorTiers){
  const impact=value(table,'explosion_on_impact');
  // Source typo P/40-K vs P40-K: punctuation normalization only when unique.
  const normalize = id => String(id ?? '').replaceAll('/', '').toLowerCase();
  const matches = impact ? item.tables.filter(t=>t.kind==='attack_explosion'&&normalize(t.id)===normalize(impact)) : [];
  const linked=matches.length === 1 ? matches[0] : null;
  let explosion=null;
  if(linked){
    const normal=number(value(linked,'damage_inner_radius')),durable=number(value(linked,'inner_durable')),pen=armorTiers[value(linked,'pen_inner')];
    if([normal,durable,pen].every(Number.isFinite))explosion={id:linked.id,normal,durable,pen,inner_radius_m:number(value(linked,'area_of_effect_inner_radius')),outer_radius_m:number(value(linked,'area_of_effect_outer_radius')??value(linked,'outer_radius')),outer_normal:number(value(linked,'damage_outer_radius')),outer_durable:number(value(linked,'outer_durable')),outer_pen:armorTiers[value(linked,'pen_outer')],demolition_force:number(value(linked,'demolition_force'))};
  }
  const stats=item.tables.filter(t=>t.kind==='weapon_stats').flatMap(t=>t.rows);
  const binding=stats.find(r=>/^(?:charge_at|at)_\d+_\d+_s$/.test(r.key)&&new RegExp(`(?:×|x)\\s*${table.id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}\\s*$`,'i').test(r.raw));
  const match=binding?/^(?:charge_at|at)_(\d+)_(\d+)_s$/.exec(binding.key):null;
  const charge_ms=match?Number(`${match[1]}.${match[2]}`)*1000:null;
  const direct_multiplier=binding?number(binding.raw):1;
  return {mode_id:table.id,explosion,charge_ms,direct_multiplier,arming_distance_m:number(value(table,'arming_distance')),unresolved_impact:!!impact&&!explosion};
}
