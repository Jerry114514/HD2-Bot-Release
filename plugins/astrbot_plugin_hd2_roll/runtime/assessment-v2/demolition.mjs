import {HOLE_ROUTES} from '../demolition/sources.mjs';
import {resourceWeapons, supplyProviders} from './engagement.mjs';
import {executeResourcePlan} from './resources.mjs';

// Destruction of the weapon/vehicle itself is not a usable hole-closing attack.
const physicalComponents=row=>(row.facts.demolition_components??[]).filter(c=>!c.backblast&&c.attack_type!=='status'
  &&!/backblast|overcharge|exosuit destruction/i.test(c.component_id??''));
const holeRoute=c=>!Number.isFinite(c.force)?null:c.force>=HOLE_ROUTES.outside.min_force?'outside':
  c.attack_type==='explosion'&&c.force>=HOLE_ROUTES.inside_explosive.min_force?'inside_explosive':
  c.force>=HOLE_ROUTES.inside_any.min_force?'inside_any':null;
export function demolitionCapability(row) {
  if(row.category==='deployable'||row.facts.known_noncombat||row.utility?.combat_damage===false
    ||row.strategic_profile?.realization_class==='smoke')return {state:'KNOWN_ZERO',qualifies:false,reason:'USER_EXCLUSION'};
  const components=physicalComponents(row),qualified=components.filter(c=>holeRoute(c));
  return {state:qualified.length?'QUALIFIED':components.length&&components.every(c=>Number.isFinite(c.force))?'KNOWN_ZERO':'UNKNOWN',
    qualifies:qualified.length>0,components:qualified,
    max_force:components.some(c=>Number.isFinite(c.force))?Math.max(...components.map(c=>c.force??0)):null,
    basis:'SINGLE_COMPONENT_FORCE; DIRECT_OR_EXPLOSION_40_SUFFICIENT; NO_CROSS_HIT_SUM',
    scope:'STRUCTURE_ELIGIBILITY; STOCK_AND_TIMING_SEPARATE'};
}
const qualifiedRed=row=>['eagle','orbital'].includes(row.category)&&demolitionCapability(row).qualifies;
const finitePack=row=>row.category==='backpack'&&demolitionCapability(row).qualifies
  &&Number.isSafeInteger(row.facts.inventory.capacity)&&row.facts.inventory.total>0
  &&physicalComponents(row).filter(c=>c.force>=40).length===1;

// Evidence: docs/ai/assessment-v2-pool-completion-20261028/payload-inventory/.
// These are independent feeds, or the confirmed loaded lower bound where the
// starting reserve/reload is not bound. They do not certify any action timeline.
const independentFeeds={
  ar_gl_21_one_two:{component:'GP-31_P_IE',stock:4,capacity:6,refill:{public:5,portable:5},feed:'underbarrel'},
  support_40_k_thermal_gun:{component:'40-K_MELTAGUN_B',existing:true},
  support_cqc_20_breaching_hammer:{component:'CQC-20_BREACHING_HAMMER_IE',existing:true},
  support_ms_11_missile_silo:{component:'SOLO_SILO_EImpact',stock:1,feed:'missile'},
  vehicle_td_110_maelstrom:{component:'SWP_MAELSTROMIII_E',stock:20,feed:'missiles'},
};
const wholeMeltagunRound=(row,m)=>row.equipment_id==='support_40_k_thermal_gun'
  &&m.service_conversion==='CANDIDATE_1.4S_BEAM_AT_LISTED_DPS; NO_ORDINARY_FIRE';
const boundModes=row=>(row.facts.service_modes??row.facts.modes??[])
  .filter(m=>m.feed_bound && (!m.continuous||wholeMeltagunRound(row,m)));
const modeBinds=(m,id)=>id===(m.source_mode_id??m.mode_id)||id===m.explosion?.id;
const validAccountIds=row=>new Set(resourceWeapons([row]).map(w=>w.id));
const boundComponent=(row,id)=>{
  const accounts=validAccountIds(row);
  return (row.facts.service_modes??row.facts.modes??[]).some(m=>m.feed_bound&&modeBinds(m,id)
    &&accounts.has(m.resource_id??row.equipment_id));
};
const independentFeed=row=>{
  const spec=independentFeeds[row.equipment_id];
  const component=spec&&physicalComponents(row).find(c=>c.component_id===spec.component&&holeRoute(c));
  if(!component||boundComponent(row,spec.component))return null;
  if(spec.existing&&!validAccountIds(row).has(row.equipment_id))return null;
  return {...spec,component};
};
export function demolitionResources(rows) {
  return resourceWeapons(rows).concat(rows.filter(qualifiedRed).flatMap(r=>{
    const p=r.strategic_profile,uses=p?.family==='orbital'?1:p?.uses;
    return Number.isSafeInteger(uses)&&uses>0?[{id:r.equipment_id,loaded:uses,capacity:uses,reserve:0,reserve_capacity:0,
      feed:'loose',reload_ms:0,refill:{public:0,portable:0}}]:[];
  }),rows.filter(r=>finitePack(r)&&!r.facts.executable_backpack).map(r=>({id:r.equipment_id,loaded:r.facts.inventory.total,
    capacity:r.facts.inventory.total,reserve:0,reserve_capacity:0,
    feed:'loose',reload_ms:0,refill:r.facts.refill,
    timing_scope:'INVENTORY_ONLY_PACK_CHARGES; NOT_A_LOADED_MAGAZINE_OR_RELOAD_TIME'})),
  rows.flatMap(r=>{const f=independentFeed(r);return f&&!f.existing?[{id:`${r.equipment_id}#demolition_${f.feed}`,
    equipment_id:r.equipment_id,loaded:f.stock,capacity:f.capacity??f.stock,reserve:0,reserve_capacity:0,
    feed:'loose',reload_ms:0,refill:f.refill??{public:0,portable:0},timing_scope:'INVENTORY_ONLY_CONFIRMED_FEED'}]:[];}));
}

// One ordinary hole, one paid round. Only components actually bound to the feed
// may qualify; shrapnel, backblast, a secondary barrel and green deployment never
// borrow the gun's full magazine. This is a source-derived inventory check, not
// the older formal proof engine's verified certificate.
export function demolitionRoutes(row, scenario) {
  if (!demolitionCapability(row).qualifies) return [];
  if(row.strategic_profile) {
    const p=row.strategic_profile;
    if(!qualifiedRed(row))return [];
    const component=physicalComponents(row).find(c=>holeRoute(c));
    if(!component)return [];
    return [{weapon_id:row.equipment_id,mode_id:'targeted-call',component_id:component.component_id,rounds:1,
      route:holeRoute(component),placement:holeRoute(component)==='outside'?'USER_DEFAULT_OUTSIDE_CONTACT; ONE_HOLE_PER_CALL':'USER_DEFAULT_INSIDE_CONTACT; ONE_HOLE_PER_CALL',
      prepare_ms:0,interval_ms:1,effect_delay_ms:0,switch_ms:0,source_ref:row.facts.source_ref,
      inventory_only:true,timing_scope:'FIRST_CYCLE_INVENTORY_ONLY; NO_ELAPSED_TIME_CLAIM; NO_MULTI_HOLE_CREDIT'}];
  }
  if (!['primary', 'secondary', 'support', 'throwables','vehicle','backpack'].includes(row.category)) return [];
  const facts = row.facts, components = facts.demolition_components ?? [];
  const routes = [];
  const allowed = physicalComponents(row);
  const add = (component, mode) => {
    const name = holeRoute(component);
    if (!name) return;
    const delay = row.equipment_id === 'g_123_thermite' ? scenario.thermite_effect_ms : facts.fuse_ms ?? 0;
    routes.push({weapon_id: mode?.resource_id??row.equipment_id, mode_id: mode?.mode_id, rounds: 1,
      component_id: component.component_id, route: name, placement: name==='outside'?'DIRECT_CONTACT_WITH_ORDINARY_HOLE':'INSIDE_ORDINARY_HOLE',
      prepare_ms: row.category === 'throwables' ? scenario.throw_ms : scenario.heavy_aim_ms,
      interval_ms: mode?.rpm > 0 ? Math.ceil(60000 / mode.rpm) : 1, effect_delay_ms: delay,
      switch_ms: scenario.weapon_switch_ms, source_ref: facts.source_ref,
      timing_scope: 'INVENTORY_TEST; NO_MAP_TRAVEL_OR_CLEARANCE_DEADLINE'});
  };
  if (row.category === 'throwables'||finitePack(row)) {
    for(const component of allowed)add(component,null);
    if(finitePack(row))for(const route of routes)Object.assign(route,{inventory_only:true,
      timing_scope:'FINITE_PACK_INVENTORY_ONLY; RELOAD_AND_DETONATION_TIME_UNBOUND'});
  } else for (const mode of boundModes(row)) {
    if(!validAccountIds(row).has(mode.resource_id??row.equipment_id))continue;
    for (const component of allowed.filter(c => holeRoute(c)&&modeBinds(mode,c.component_id))) {
      add(component, mode);
      if((mode.kind!=='projectile'||wholeMeltagunRound(row,mode))&&routes.length)Object.assign(routes.at(-1),{inventory_only:true,
        prepare_ms:0,interval_ms:1,effect_delay_ms:0,switch_ms:0,timing_scope:'BOUND_FEED_INVENTORY_ONLY; NO_ELAPSED_TIME_CLAIM'});
    }
  }
  const independent=independentFeed(row);
  if(independent){
    add(independent.component,{resource_id:independent.existing?row.equipment_id:`${row.equipment_id}#demolition_${independent.feed}`,
      mode_id:`demolition_${independent.feed??'existing_feed'}`});
    Object.assign(routes.at(-1),{inventory_only:true,prepare_ms:0,interval_ms:1,effect_delay_ms:0,switch_ms:0,
      timing_scope:'INDEPENDENT_FEED_INVENTORY_ONLY; NO_ELAPSED_TIME_CLAIM'});
  }
  return routes;
}

export function assessFiveHoles(rows, model) {
  const weapons = demolitionResources(rows), ids = new Set(weapons.map(w => w.equipment_id ?? w.id));
  const routes = rows.filter(r => ids.has(r.equipment_id)).flatMap(r => demolitionRoutes(r, model.scenario));
  // At most five actions. Enumerate allocations across known inventories; do not
  // assume a single launcher alone must fund all five holes. All use one ledger.
  const unique = [...new Map(routes.map(r => [`${r.weapon_id}:${r.mode_id ?? ''}`, r])).values()];
  const count = model.scenario.demolition_holes;
  let best = null, tested = 0;
  const search = (index, remaining, steps) => {
    if (index === unique.length) {
      if (remaining || steps.length !== count) return;
      tested++;
      const plan = steps.map((step, i) => ({...step, hole_id: `ordinary-hole-${i + 1}`}));
      const result = executeResourcePlan({weapons, providers: supplyProviders(rows, model.scenario), steps: plan});
      if (result.status === 'COMPLETED' && (!best || result.elapsed_ms < best.elapsed_ms)) best = {result, plan};
      return;
    }
    for (let n = 0; n <= remaining; n++) search(index + 1, remaining - n, [...steps, ...Array.from({length: n}, () => unique[index])]);
  };
  if (unique.length) search(0, count, []);
  return best ? {status: 'PASS', closed_holes: count, plan: best.plan, supplies_taken: best.result.supplies_taken,
    ammo_remaining: best.result.state.resources, tested_plans: tested,
    timing_status:best.plan.some(s=>s.inventory_only)?'INVENTORY_ONLY; NO_COMPLETION_TIME_CLAIM':'EXISTING_ACTION_TIMELINE',
    scope: 'FIVE_DISTINCT_ORDINARY_HOLES; FULL_STATE_RESET; GREEN_ZERO; USER_DEFAULT_QUALIFIED_CONTACT'}
    : {status: 'UNKNOWN', closed_holes: null, tested_plans: tested, reason: 'NO_COMPLETED_KNOWN_DEMOLITION_PLAN',
      scope: 'PARTIAL_HANDHELD_AND_TARGETED_RED_ROUTES; SCATTERED_MULTI_HOLE_NOT_ASSUMED'};
}
