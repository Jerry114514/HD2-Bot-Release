// Independent certificate checker: deliberately does not import planner or ledger transitions.
import {actionAllowedInCase, clone, stable, legality, equipmentIDs, canOccupy, validSlotsShape} from './common.mjs';
import {compileAction, caseDependencies} from './proof.mjs';
import {holdingOptions} from './holding.mjs';
import {verifiedBundleCatalog} from './bundles.mjs';
import {predeploymentRows} from './predeployment.mjs';
import {DEFAULT_EVIDENCE_GRADE, declaredGrade, isEvidenceGrade} from './grade.mjs';
export function replay(spec, actions, certificate, context) {
  if (!context?.model || !context.loadout || !context.profile) return {valid: false, reason: 'MODEL_AND_LOADOUT_CONTEXT_REQUIRED', leaves: 0};
  // The independent checker reads at exactly the grade the profile declares; an
  // unselected run stays on the historical verified contract, and a mismatch is a
  // refusal rather than a silent re-read at another grade.
  const grade = context.evidence_grade === undefined ? DEFAULT_EVIDENCE_GRADE : context.evidence_grade;
  if (!isEvidenceGrade(grade)) return {valid: false, reason: 'EVIDENCE_GRADE_UNKNOWN', evidence_grade: grade, leaves: 0};
  if (declaredGrade(context.profile) !== grade) return {valid: false, reason: 'EVIDENCE_GRADE_MISMATCH', evidence_grade: grade, leaves: 0};
  const boundProfile=context.model.profiles.profiles.find(p=>p.id===context.profile.id);
  if (legality(context.loadout,context.model.catalog).status!=='LEGAL' || stable(boundProfile)!==stable(context.profile) || !boundProfile.cases.some(c=>stable(c)===stable(spec))) return {valid:false,reason:'CERTIFICATE_CONTEXT_MISMATCH',leaves:0};
  if (!caseDependencies(context.model, spec, context.profile)) return {valid:false, reason: 'SCENARIO_FACTS_UNVERIFIED', leaves: 0};
  const verified = actions.map(a => compileAction(context.model, a, context.loadout, context.profile));
  if (verified.some(a => !a)) return {valid:false, reason: 'ACTION_PROOF_REJECTED', leaves: 0};
  const originals=context.model.actions||context.profile.actions||[];
  if (verified.some(a=> {const source=originals.find(s=>s.id===a.id);return !source||stable(compileAction(context.model,source,context.loadout,context.profile))!==stable(a);}))return {valid:false,reason:'ACTION_CONTEXT_MISMATCH',leaves:0};
  actions = verified;
  // The replay re-derives every initial-holding fact from the case, loadout and catalog on
  // its own; it never trusts that the evaluator already screened the case. The catalog is
  // the same bundle-augmented view the evaluator used, re-derived from the same model.
  const selected = spec.initial?.holding === 'select_from_loadout';
  const catalog = verifiedBundleCatalog(context.model, context.profile.game_build, grade);
  const catalogByID = new Map(catalog.equipment.map(e => [e.id, e]));
  const inLoadout = new Set(equipmentIDs(context.loadout));
  function checkedInitialSlots(raw) {
    if (!validSlotsShape(raw)) throw Error('invalid initial slot');
    if (raw === undefined) return {};
    for (const [slot, id] of Object.entries(raw)) {
      if (!inLoadout.has(id)) throw Error('INITIAL_SLOT_NOT_IN_LOADOUT');
      if (!canOccupy(catalogByID.get(id), slot)) throw Error('INITIAL_SLOT_ROLE_INVALID');
    }
    return clone(raw);
  }
  const legalHoldings = selected ? holdingOptions(context.loadout, catalog) : null;
  const byID = new Map(actions.map(a => [a.id, a]));
  let setupRows;try{setupRows=predeploymentRows(spec,actions,context.profile);}catch(e){return {valid:false,reason:e.message,leaves:0};}
  let leaves = 0;
  function checkTrace(trace) {
    const r = clone(spec.initial.resources || {}), targets = clone(spec.initial.targets || {});
    const flags = {}, safety = {}, cooldowns = {}, demolitions = {}, flag_expiries={};
    // Independent arithmetic on the replay's own initial accounts and clocks.
    for(const row of setupRows){
      const a=row.action,d=a.deployment;
      for(const [key,b] of Object.entries(a.requires_resources||{})){const v=r[key]?.value;if(!Number.isFinite(v)||b.min!==undefined&&v<b.min||b.max!==undefined&&v>b.max)throw Error('predeployment resource requirement');}
      for(const e of a.events.filter(e=>e.kind==='resource')){const account=r[e.key];if(!account||account.value+e.delta<0||d.resource_bindings?.[e.key]!==undefined&&account.resource_id!==d.resource_bindings[e.key])throw Error('predeployment overspend');account.value+=e.delta;}
      if(a.cooldown)cooldowns[a.cooldown.key]=Math.max(0,a.cooldown.duration_ms-row.started_before_ms);
      if(d.lifetime_ms===null||row.age_ms<d.lifetime_ms){flags[d.flag]=true;if(d.lifetime_ms!==null)flag_expiries[d.flag]=d.lifetime_ms-row.age_ms;}
    }
    if (selected && spec.initial?.slots !== undefined) throw Error('invalid initial slot');
    let slots = selected ? {} : checkedInitialSlots(spec.initial?.slots);
    let time = 0, busy = 0, observation = 0;
    const queue = [], seen = new Set(), observed = [];
    const phase = e => e.kind === 'resource' ? (e.delta < 0 ? 0 : 2) : 1;
    function event(e) {
      if (seen.has(e.id)) throw Error('duplicate event'); seen.add(e.id);
      if((e.deployment_guards||[]).some(k=>flag_expiries[k]!==undefined&&flag_expiries[k]<=e.at))throw Error('deployment expired before effect');
      if (e.kind === 'resource') {
        if (!r[e.key] || !Number.isFinite(e.delta) || r[e.key].value + e.delta < 0) throw Error('resource overspend');
        r[e.key].value = Math.min(r[e.key].cap, r[e.key].value + e.delta);
      } else if (e.kind === 'damage') {
        const t = targets[e.key];
        if (!t || e.target_id != null && e.target_id !== t.target_id || !Number.isFinite(e.amount) || e.amount < 0 || e.requires_control && !(t.control_until >= e.at)) throw Error('invalid hit');
        if (e.part_id) {
          const p = t.parts?.[e.part_id]; if (!p || p.hp <= 0) throw Error('invalid part');
          const partBefore=p.hp;
          p.hp = Math.max(0, p.hp - e.amount); if (p.hp === 0 && p.fatal === true) t.hp = 0;
          if (e.transfer != null) { if (!Number.isFinite(e.transfer) || e.transfer < 0) throw Error('invalid transfer'); t.hp = Math.max(0, t.hp - (e.transfer_policy?.rule==='capped_at_remaining'?Math.ceil(Math.min(e.amount,partBefore)*e.transfer_policy.fraction):e.transfer)); }
        } else t.hp = Math.max(0, t.hp - e.amount);
        if (t.hp === 0 && t.destroyed_at == null) { t.destroyed_at = e.at; t.neutralized_at = e.at + (t.post_lethal_ms ?? 0); }
      } else if (e.kind === 'control') {
        const t = targets[e.key]; if (!t || t.control_immune || !Number.isSafeInteger(e.duration_ms) || e.duration_ms < 0) throw Error('invalid control');
        // Same interval honesty as the ledger: a late-starting stun never covers
        // the time before it started, so the covered start moves with the extension.
        const until = e.at + e.duration_ms;
        if (until > (t.control_until || 0)) {
          t.control_from = (t.control_until || 0) >= e.at ? (t.control_from ?? 0) : e.at;
          t.control_until = until;
        }
      } else if (e.kind === 'spawn') {
        if (targets[e.key] || !(e.target?.hp > 0)) throw Error('invalid spawn'); targets[e.key] = clone(e.target);
      } else if (e.kind === 'flag') {
        // Bare demolition:* flags never close holes on the replay's own state either.
        if (typeof e.key === 'string' && e.key.startsWith('demolition:')) throw Error('demolition flag bypass');
        flags[e.key] = e.value;
        if(e.expires_after_ms!==undefined)flag_expiries[e.key]=e.at+e.expires_after_ms;
      }
      else if (e.kind === 'disengage') {
        // Independently re-derived safety-window proof: a target inside its post-lethal
        // delay is not neutralized yet, and control that started after the window start
        // never covered it. Either way the escape is rejected here.
        if (!Number.isSafeInteger(e.duration_ms) || e.duration_ms < 1 || !Array.isArray(e.targets) || !e.targets.length || new Set(e.targets).size !== e.targets.length) throw Error('invalid disengage');
        if (e.targets.some(k => !targets[k])) throw Error('disengage unknown target');
        const end = e.at + e.duration_ms;
        if (e.targets.some(k => {
          const t = targets[k];
          if (t.neutralized_at != null && t.neutralized_at <= e.at) return false;
          const coverEnd = t.neutralized_at != null && t.neutralized_at < end ? t.neutralized_at : end;
          return (t.control_from ?? 0) > e.at || (t.control_until || 0) < coverEnd;
        })) throw Error('disengage control gap');
        safety[e.key] = {at: e.at, duration_ms: e.duration_ms, until: end, targets: [...e.targets]};
      } else if (e.kind === 'equip') {
        if (!['weapon', 'backpack'].includes(e.key)) throw Error('invalid active slot');
        if (!inLoadout.has(e.equipment_id)) throw Error('equip outside loadout');
        if (!canOccupy(catalogByID.get(e.equipment_id), e.key)) throw Error('equip role invalid');
        slots[e.key] = e.equipment_id;
      }
      else if (e.kind === 'demolition') {
        if (!e.route_verified || !e.component_id) throw Error('unverified demolition');
        // Independent closure record with the same first-event-wins rule: repeats never
        // re-close a hole and ordinary flags stay out of this namespace.
        if (demolitions[e.key] != null) throw Error('duplicate hole closure');
        demolitions[e.key] = e.at;
      }
      else throw Error('unsupported event');
    }
    function advance(to) {
      const nextObs = (spec.observations || [])[observation];
      if (!Number.isSafeInteger(to) || to < time || nextObs && nextObs.at < to) throw Error('skipped observation');
      queue.sort((a, b) => a.at - b.at || phase(a) - phase(b) || a.order[0]-b.order[0] || a.order[1]-b.order[1]);
      while (queue.length && queue[0].at <= to) event(queue.shift());
      time = to;
    }
    for (const [serial, row] of trace.entries()) {
      if (row.kind === 'hold') {
        // One holding choice, made at t=0 before anything else, shared by all futures.
        if (!selected || serial !== 0 || row.at !== 0) throw Error('invalid hold row');
        if (!legalHoldings.some(option => stable(option) === stable(row.slots))) throw Error('illegal holding selection');
        slots = clone(row.slots);
      } else if (row.kind === 'observe') {
        const obs = (spec.observations || [])[observation], alt = obs?.alternatives.find(a => a.id === row.alternative_id);
        if (!alt || obs.id !== row.observation_id || row.at !== time || time !== obs.at) throw Error('invalid observation');
        observed.push(alt.id); observation++;
        for (const [i, e] of (alt.events || []).entries()) { if (e.offset_ms !== 0 || e.kind === 'disengage') throw Error('invalid observation event'); event({...e, at: time, id: `o${observation}:${i}`}); }
      } else if (row.kind === 'wait') {
        if (row.at !== time || row.until <= time) throw Error('invalid wait'); advance(row.until);
      } else if (row.kind === 'action') {
        const a = byID.get(row.action_id);
        if (a && !actionAllowedInCase(a, spec)) throw Error('action excluded from this dimension');
        if (!a || row.at !== time || time < busy || !Number.isSafeInteger(a.busy_ms) || a.busy_ms < 1 || a.target && (!targets[a.target] || targets[a.target].hp <= 0)) throw Error('invalid action');
        if (Object.entries(a.requires_slots || {}).some(([k, v]) => slots[k] !== v) || Object.entries(a.requires_flags || {}).some(([k, v]) => flags[k] !== v || flag_expiries[k]!==undefined&&flag_expiries[k]<=time)) throw Error('unmet prerequisite');
        for(const [key,limit] of Object.entries(a.requires_resources||{})) {
          const available=r[key]?.value;
          if(!Number.isFinite(available)||limit.min!==undefined&&available<limit.min||limit.max!==undefined&&available>limit.max)throw Error('resource prerequisite failed');
        }
        // Reload accounts are re-bound here, independently of the planner: the initial
        // entry behind each consumed key must carry the resource_id the action claims.
        if (a.reload) {
          const bound = [[a.reload.magazine.key, a.reload.magazine.resource_id]];
          if (a.reload.reserve) bound.push([a.reload.reserve.key, a.reload.reserve.resource_id]);
          if (bound.some(([key, rid]) => r[key]?.resource_id !== rid)) throw Error('resource binding mismatch');
        }
        // Resupply accounts are re-bound the same way, on the replay's own state.
        if (a.resupply) {
          const bound = [[a.resupply.consumed?.key, a.resupply.consumed?.resource_id], ...(a.resupply.supplies || []).map(s => [s.key, s.resource_id])];
          if (bound.some(([key, rid]) => r[key]?.resource_id !== rid)) throw Error('resource binding mismatch');
        }
        // Demolition consumption is re-bound on the replay's own state: a declared account
        // must match the initial entry whenever the case binds that key.
        if (a.demolition?.resource) {
          const entry = r[a.demolition.resource.key];
          if (entry?.resource_id !== undefined && entry.resource_id !== a.demolition.resource.resource_id) throw Error('resource binding mismatch');
        }
        if (a.cooldown && time < (cooldowns[a.cooldown.key] || 0)) throw Error('cooldown violation');
        const events = (a.events || []).map((e, i) => ({...e,deployment_guards:Object.keys(a.requires_flags||{}).filter(k=>flag_expiries[k]!==undefined), at: time + e.offset_ms, id: `a${serial}:${i}`,order:[serial,i]}));
        if (events.some(e => !Number.isSafeInteger(e.offset_ms) || e.offset_ms < 0 || e.kind === 'resource' && e.delta < 0 && e.offset_ms !== 0)) throw Error('invalid event time');
        // TASK-B composite detonation boundaries, re-derived here independently
        // of the compiler: one detonation group per paid unit, no duplicate
        // part instance per group, no fallback mixed with part claims.
        if (events.some(e => e.kind === 'damage' && e.blast)) {
          const groups = new Map();
          for (const e of events) {
            if (e.kind !== 'damage' || !e.blast) continue;
            if (typeof e.detonation_id !== 'string' || !e.detonation_id) throw Error('invalid detonation group');
            if (!groups.has(e.detonation_id)) groups.set(e.detonation_id, []);
            groups.get(e.detonation_id).push(e);
          }
          for (const group of groups.values()) {
            if(new Set(group.map(e=>e.offset_ms)).size!==1||new Set(group.map(e=>e.key)).size!==1||new Set(group.map(e=>e.target_id)).size!==1)throw Error('detonation boundary mismatch');
            if(group.filter(e=>e.blast.contact_share==='direct').length>1)throw Error('duplicate direct share');
            const partIds = group.filter(e=>e.blast.contact_share!=='direct').map(e => e.part_id ?? null);
            if (new Set(partIds).size !== partIds.length) throw Error('duplicate part in one detonation');
            const mains = partIds.filter(p => p === null).length;
            if (mains > 1 || (mains === 1 && partIds.length > 1)) throw Error('fallback and part claims double count');
          }
          const debits = events.filter(e => e.kind === 'resource' && Number.isFinite(e.delta) && e.delta < 0).reduce((n, e) => n + -e.delta, 0);
          if(a.blast_cost?.kind==='call'){if(debits!==0||groups.size!==1||!a.cooldown)throw Error('invalid call cost');}
          else if (debits !== groups.size) throw Error('a detonation group rode the same ammo twice');
          if(a.blast_cost?.kind==='ammo'&&r[a.blast_cost.key]?.resource_id!==a.blast_cost.resource_id)throw Error('blast resource binding mismatch');
        }
        // Re-derived, independent of the compiler: the escape action must itself occupy
        // the whole window it declares, or the window was never executed.
        if (events.some(e => e.kind === 'disengage' && e.offset_ms + e.duration_ms > a.busy_ms)) throw Error('disengage exceeds action window');
        events.sort((a, b) => a.at - b.at || phase(a) - phase(b) || a.order[0]-b.order[0] || a.order[1]-b.order[1]);
        for (const e of events.filter(e => e.at === time)) event(e);
        queue.push(...events.filter(e => e.at > time));
        busy = time + a.busy_ms;
        if (a.cooldown) cooldowns[a.cooldown.key] = time + a.cooldown.duration_ms;
        const obs = (spec.observations || [])[observation]; advance(obs && obs.at <= busy ? obs.at : busy);
      } else throw Error('unknown trace row');
      if (time > spec.horizon_ms) throw Error('horizon exceeded');
    }
    const goals = [...spec.goals, ...(spec.conditional_goals || []).filter(g => observed.includes(g.alternative_id))];
    if (observation !== (spec.observations || []).length || time < (spec.finish_after_ms || 0)) throw Error('incomplete policy');
    for (const g of goals) {
      let ok = false;
      if (g.kind === 'neutralized' || g.kind === 'destroyed') { const at = targets[g.target]?.[g.kind === 'neutralized' ? 'neutralized_at' : 'destroyed_at']; ok = at != null && at <= g.by_ms && at <= time; }
      if (g.kind === 'resource') ok = time >= (g.at_ms || 0) && r[g.key]?.value >= g.min;
      if (g.kind === 'flag') ok = time >= (g.at_ms || 0) && flags[g.key] === g.value;
      if (g.kind === 'safety') {
        // Window completion, deadline and the scenario-bound duration are re-checked
        // here on the replay's own state, never inherited from the planner. The
        // all-neutralized alternative is re-derived just as independently: hp 0
        // alone never counts, the post-lethal delay must have finished in time.
        const w = safety[g.key];
        const escaped = !!w && w.until <= g.by_ms && w.until <= time && (!Number.isSafeInteger(g.min_duration_ms) || w.duration_ms >= g.min_duration_ms) && (g.targets || []).every(t => w.targets.includes(t));
        const bound = g.targets || [];
        ok = escaped || (bound.length > 0 && bound.every(t => { const at = targets[t]?.neutralized_at; return at != null && at <= g.by_ms && at <= time; }));
      }
      if (g.kind === 'demolition') ok = demolitions[g.key] != null && demolitions[g.key] <= g.by_ms;
      if (!ok) throw Error('goal failed: ' + g.dimension);
    }
    leaves++;
    return {resources: r, targets, time};
  }
  function visit(node, requiredPrefix = [], expectedAlternative = null, path = []) {
    if (node?.kind === 'leaf') {
      if (stable(node.trace.slice(0, requiredPrefix.length)) !== stable(requiredPrefix)) throw Error('future-dependent prefix');
      if (expectedAlternative && node.trace[requiredPrefix.length]?.alternative_id !== expectedAlternative) throw Error('missing branch');
      if (stable(node.trace.filter(r => r.kind === 'observe').map(r => [r.observation_id, r.alternative_id])) !== stable(path)) throw Error('missing universal policy node');
      return checkTrace(node.trace);
    }
    if (node?.kind !== 'and') throw Error('invalid certificate');
    if (stable(node.prefix.slice(0, requiredPrefix.length)) !== stable(requiredPrefix)) throw Error('future-dependent prefix');
    if (expectedAlternative && node.prefix[requiredPrefix.length]?.alternative_id !== expectedAlternative) throw Error('wrong branch prefix');
    if (stable(node.prefix.filter(r => r.kind === 'observe').map(r => [r.observation_id, r.alternative_id])) !== stable(path)) throw Error('missing universal branch prefix');
    const obs = (spec.observations || []).find(o => o.id === node.observation_id);
    if (!obs || node.branches.length !== obs.alternatives.length || new Set(node.branches.map(b => b.alternative_id)).size !== obs.alternatives.length) throw Error('incomplete universal branch');
    for (const alt of obs.alternatives) {
      const branch = node.branches.find(b => b.alternative_id === alt.id);
      if (!branch) throw Error('missing branch');
      visit(branch.certificate, node.prefix, alt.id, [...path, [obs.id, alt.id]]);
    }
  }
  try { visit(certificate); return {valid: true, leaves, evidence_grade: grade}; } catch (e) { return {valid: false, reason: e.message, leaves, evidence_grade: grade}; }
}
