// Eligibility precedes target-route scoring. A fire/gas status' AP or a
// penetrable light weakpoint does not turn a low-AP gun into an anti-heavy gun.
export function heavyEligibility(row,model) {
  const threshold=model.scenario.heavy_scoring.minimum_penetration;
  const facts=row.facts;
  const feeds=(facts.service_modes??facts.modes).filter(m=>m.feed_bound);
  const modes=feeds.length?feeds:facts.modes.filter(m=>!m.backblast);
  const components=[...modes.map(m=>({id:m.mode_id,channel:'direct',pen:m.pen,normal:m.normal,durable:m.durable})),
    ...facts.areas.map(a=>({id:a.id,channel:'explosion',pen:a.pen,normal:a.normal,durable:a.durable}))]
    .filter(c=>c.normal>0||c.durable>0);
  const values=components.filter(c=>Number.isFinite(c.pen)).map(c=>c.pen);
  const qualifying=components.filter(c=>c.pen>=threshold);
  const exception=model.scenario.heavy_scoring.confirmed_exception_ids.includes(row.equipment_id);
  return {state:qualifying.length||exception?'ELIGIBLE':components.length&&values.length===components.length?'BELOW_THRESHOLD':'UNKNOWN',
    minimum_penetration:threshold,max_penetration:values.length?Math.max(...values):null,
    qualifying_components:qualifying,confirmed_exception:exception,
    basis:'USER_MINIMUM_HEAVY_PENETRATION; DIRECT_OR_EXPLOSION; STATUS_AP_DOES_NOT_QUALIFY',
    scope:'ELIGIBILITY_ONLY; NOT_A_DAMAGE_TIME_OR_INVENTORY_GUARANTEE'};
}
