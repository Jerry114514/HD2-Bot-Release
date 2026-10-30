import {DIMENSIONS, equipmentIDs, canOccupy} from './common.mjs';
import {DEFAULT_EVIDENCE_GRADE, declaredGrade, evidenceKnownAt, factAtGrade} from './grade.mjs';
import {verifiedBundleCatalog} from './bundles.mjs';
import {directDamage, demolitionRoute} from './damage.mjs';
import {verifiedBlastAmount} from './blast-proof.mjs';
// One grade per read: the grade is threaded explicitly from the profile that owns
// the calibration through every compiler and replay guard, so a verified row can
// never be mixed into a source-reviewed run (or the reverse). No grade means
// 'verified', the historical contract, so every existing caller is unchanged.
export function evidenceKnown(model, refs, build, grade = DEFAULT_EVIDENCE_GRADE) {
  return evidenceKnownAt(model, refs, build, grade);
}
export function factValue(model, id, build, grade = DEFAULT_EVIDENCE_GRADE) {
  return factAtGrade(model, id, build, grade);
}
const time=v=>Number.isSafeInteger(v)&&v>=0;
export function eventValid(e) {
  if(!e || !time(e.offset_ms) || typeof e.key!=='string' || !e.key) return false;
  if(e.kind==='resource')return Number.isFinite(e.delta) && (e.delta>=0||e.offset_ms===0);
  if(e.kind==='damage')return Number.isFinite(e.amount)&&e.amount>=0;
  if(e.kind==='control')return time(e.duration_ms);
  if(e.kind==='spawn')return Number.isFinite(e.target?.hp)&&e.target.hp>0&&time(e.target.post_lethal_ms??0);
  if(e.kind==='equip')return ['weapon','backpack'].includes(e.key)&&typeof e.equipment_id==='string';
  if(e.kind==='demolition')return typeof e.component_id==='string';
  if(e.kind==='disengage')return Number.isSafeInteger(e.duration_ms)&&e.duration_ms>=1&&Array.isArray(e.targets)&&e.targets.length>0&&e.targets.every(t=>typeof t==='string'&&!!t)&&new Set(e.targets).size===e.targets.length;
  return e.kind==='flag' && ['string','boolean','number'].includes(typeof e.value);
}
export function syntheticProfile(model, profile) {
  const refs=profile?.calibration?.evidence_refs;
  return profile?.game_build==='synthetic-only' && evidenceKnown(model, refs, profile.game_build, 'verified') && refs.every(id=>model.facts.evidence.find(e=>e.id===id)?.kind==='synthetic');
}
// Reload actions carry an explicit account/ownership block; the compiler re-verifies it
// against the model instead of trusting the action text. Ownership comes from the
// published resource entries, the magazine credit must land exactly at completion, and
// the timing provenance must be a verified fact (or explicitly synthetic evidence on a
// synthetic profile) — a bare certified flag or unrelated evidence proves nothing.
function compileReload(model, a, build, synthetic, grade) {
  const r = a.reload;
  const weapon = a.required_equipment[0];
  const byID = new Map((model.facts.resources || []).map(x => [x.id, x]));
  const own = resourceID => { const entry = byID.get(resourceID); return entry && entry.equipment_id === weapon ? entry : null; };
  if (!own(r.magazine?.resource_id)) return null;
  if (r.reserve && !own(r.reserve.resource_id)) return null;
  if (r.reserve && r.reserve.requires_backpack && a.requires_slots?.backpack !== weapon) return null;
  // Account UNITS are part of the reload's arithmetic: a loose per-shot reserve
  // (rounds/shells) is debited round for round, so one load's debit must equal
  // its credit — 40 spare rounds + 2 loaded are 20 reloads, never 40 magazines,
  // and one round can never refill a whole magazine. A whole-magazine reserve
  // stays 1 magazine per reload. Undeclared units keep the legacy reading.
  {
    const RELOAD_UNITS = new Set(['magazines', 'rounds', 'shells']);
    const loose = u => u === 'rounds' || u === 'shells';
    const ru = r.reserve?.unit ?? null, mu = r.magazine?.unit ?? null;
    // Caller labels cannot erase the unit of a verified published spare row.
    const entry=r.reserve&&own(r.reserve.resource_id),sourceUnits=new Set();
    for(const [property,unit] of [['spare_rounds','rounds'],['spare_shells','shells'],['spare_magazines','magazines']]){
      for(const id of entry?.candidate_bindings?.[property]||[]){
        const row=model.facts.facts.find(f=>f.id===id),v=factValue(model, id, build, grade);
        if(v.known&&row?.subject_id===weapon&&row.property===property)sourceUnits.add(unit);
      }
    }
    if(sourceUnits.size>1)return null;
    const sourceUnit=[...sourceUnits][0];
    if(sourceUnit&&loose(sourceUnit)&&(ru!==sourceUnit||mu!==sourceUnit))return null;
    if(sourceUnit==='magazines'&&ru!==null&&ru!==sourceUnit)return null;
    if((ru!==null||mu!==null)&&(ru===null||mu===null||!loose(mu)))return null;
    if (ru !== null && !RELOAD_UNITS.has(ru)) return null;
    if (mu !== null && !RELOAD_UNITS.has(mu)) return null;
    if (r.reserve && ru !== null && mu !== null) {
      if (loose(ru) && mu !== ru) return null;
      if (loose(ru) && r.reserve.consumed !== r.magazine.credit) return null;
      if (ru === 'magazines' && r.reserve.consumed !== 1) return null;
    }
  }
  const credits = (a.events || []).filter(e => e.kind === 'resource' && e.key === r.magazine.key && e.delta > 0);
  if (credits.length !== 1 || credits[0].delta !== r.magazine.credit || credits[0].offset_ms !== a.busy_ms) return null;
  if (r.reserve) {
    const debits = (a.events || []).filter(e => e.kind === 'resource' && e.key === r.reserve.key && e.delta < 0);
    if (debits.length !== 1 || debits[0].delta !== -r.reserve.consumed || debits[0].offset_ms !== 0) return null;
  }
  if (r.no_reserve) {
    // The exemption must be a verified boolean of THIS weapon with an explicit
    // no-consumption semantic property; an arbitrary known-true boolean proves nothing.
    if (!(r.no_reserve.fact_refs || []).length || !r.no_reserve.fact_refs.every(id => {
      if (!(a.fact_refs || []).includes(id)) return false;
      const row = model.facts.facts.find(x => x.id === id);
      const f = factValue(model, id, build, grade);
      return row?.subject_id === weapon && row?.property === 'no_reserve_reload' && f.known && f.value === true;
    })) return null;
  }
  // The timing provenance must substantively prove THIS reload's duration: a verified
  // seconds fact of the same weapon with the reload-time property whose value converts
  // exactly to busy_ms, or an explicitly synthetic evidence entry — and synthetic
  // credentials are only accepted on synthetic profiles, never mixed into real builds.
  const source = r.time_source || {};
  if (typeof source.fact_ref === 'string') {
    if (!(a.fact_refs || []).includes(source.fact_ref)) return null;
    const row = model.facts.facts.find(x => x.id === source.fact_ref);
    const f = factValue(model, source.fact_ref, build, grade);
    if (!f.known) return null;
    if (row?.subject_id !== weapon) return null;
    const expectedProperty = r.kind === 'tactical' ? 'tactical_reload_time' : 'reload_time';
    if (row?.property !== expectedProperty) return null;
    if (row?.unit !== null && row?.unit !== 's') return null;
    if (typeof f.value !== 'number' || !Number.isFinite(f.value) || f.value < 0) return null;
    if (!Number.isSafeInteger(f.value * 1000) || f.value * 1000 !== a.busy_ms) return null;
  } else if (typeof source.synthetic === 'string') {
    if (!synthetic) return null;
    const e = (model.facts.evidence || []).find(x => x.id === source.synthetic);
    if (!e || e.kind !== 'synthetic' || e.state !== 'verified' || e.game_build !== build) return null;
    if (!(a.evidence_refs || []).includes(e.id)) return null;
  } else return null;
  return true;
}
const RESUPPLY_KINDS = ['public_box', 'backpack_pack', 'backpack_refill'];
const RESUPPLY_CALLED_KINDS = new Set(['public_box', 'backpack_refill']);
const RESUPPLY_RECEIVE_PROPERTY = 'mags_from_supply';
const RESUPPLY_TIME_PROPERTIES = new Set(['call_in_time', 'pickup_time']);
// The public resupply call is a profile-baseline SYSTEM action, never a draw slot: the
// profile must register the subject (profile.public_supply) with same-build verified
// evidence and bind the box-inventory resource entry plus the subject's own verified
// cooldown and call-in facts. A registered entry admits resource-only supply events for
// this exact subject — no damage/equip/flag events, no other equipment, no unregistered
// or weakly evidenced subject.
function verifySupplyPolicy(model, policy, build, grade) {
  if (!policy || typeof policy !== 'object' || Array.isArray(policy)) return null;
  if (typeof policy.subject_id !== 'string' || !policy.subject_id) return null;
  if (!evidenceKnown(model, policy.evidence_refs, build, grade)) return null;
  const entry = (model.facts.resources || []).find(x => x.id === policy.resource_id);
  if (!entry || entry.equipment_id !== policy.subject_id) return null;
  const knownMs = ref => {
    if (typeof ref !== 'string' || !ref) return null;
    const row = model.facts.facts.find(x => x.id === ref);
    const f = factValue(model, ref, build, grade);
    if (!f.known || row?.subject_id !== policy.subject_id) return null;
    if (row?.unit !== null && row?.unit !== 's') return null;
    if (typeof f.value !== 'number' || !Number.isFinite(f.value) || f.value < 0 || !Number.isSafeInteger(f.value * 1000)) return null;
    return f.value * 1000;
  };
  const cooldownMs = knownMs(policy.cooldown_fact_ref);
  const callInMs = knownMs(policy.call_in_fact_ref);
  if (cooldownMs == null || callInMs == null) return null;
  const cooldownRow = model.facts.facts.find(x => x.id === policy.cooldown_fact_ref);
  const callInRow = model.facts.facts.find(x => x.id === policy.call_in_fact_ref);
  if (cooldownRow?.property !== 'cooldown' || callInRow?.property !== 'call_in_time') return null;
  return {subject_id: policy.subject_id, resource_id: policy.resource_id, cooldownMs, callInMs, cooldown_fact_ref: policy.cooldown_fact_ref, call_in_fact_ref: policy.call_in_fact_ref};
}
// Resupply actions carry an explicit supply block; the compiler re-verifies it against
// the model instead of trusting the action text: exactly ONE box/pack is consumed at
// the start (a resupply without a consumed unit is a free refill), every credited
// account belongs to the equipment the supply declares, each amount equals that
// equipment's own verified per-box supply fact (mags_from_supply only — the ammo-box
// column is the map pickup and spare-magazine rows are capacity, never per-box refill
// facts), credits land exactly at completion, one account is never credited twice by
// one box, the drawn pack inventory belongs to the backpack actually worn, and a
// called box is bound to the registered public-supply policy with a deterministic
// cooldown key derived from the registered resource entry, so aliasing the consumed ledger key cannot fork the call cycle. Supply actions move
// resources only. Synthetic credentials stay on synthetic profiles, never real builds.
function compileResupply(model, a, build, synthetic, policy, catalogByID, grade) {
  const r = a.resupply;
  if (!r || typeof r !== 'object' || Array.isArray(r) || !RESUPPLY_KINDS.includes(r.kind)) return null;
  if ((a.events || []).some(e => e.kind !== 'resource')) return null;
  const byID = new Map((model.facts.resources || []).map(x => [x.id, x]));
  const consumed = r.consumed;
  if (!consumed || typeof consumed.key !== 'string' || !consumed.key) return null;
  const consumedEntry = byID.get(consumed.resource_id);
  if (!consumedEntry) return null;
  const called = RESUPPLY_CALLED_KINDS.has(r.kind);
  if (called) {
    if (!policy || consumedEntry.equipment_id !== policy.subject_id || consumed.resource_id !== policy.resource_id) return null;
    if (!a.cooldown || a.cooldown.key !== 'cooldown:' + consumed.resource_id || a.cooldown.duration_ms !== policy.cooldownMs) return null;
    if (!(a.fact_refs || []).includes(policy.cooldown_fact_ref)) return null;
  }
  if (r.kind === 'backpack_pack' && consumedEntry.equipment_id !== a.requires_slots?.backpack) return null;
  const debits = (a.events || []).filter(e => e.kind === 'resource' && e.delta < 0);
  if (debits.length !== 1 || debits[0].key !== consumed.key || debits[0].delta !== -1 || debits[0].offset_ms !== 0) return null;
  if (!Array.isArray(r.supplies) || !r.supplies.length) return null;
  const credits = (a.events || []).filter(e => e.kind === 'resource' && e.delta > 0);
  if (credits.length !== r.supplies.length) return null;
  const seen = new Set();
  for (const s of r.supplies) {
    if (seen.has(s.key) || s.key === consumed.key) return null;
    seen.add(s.key);
    const entry = byID.get(s.resource_id);
    if (!entry || entry.equipment_id !== s.equipment_id) return null;
    if (!(a.fact_refs || []).includes(s.fact_ref)) return null;
    const row = model.facts.facts.find(x => x.id === s.fact_ref);
    const f = factValue(model, s.fact_ref, build, grade);
    if (!f.known || row?.subject_id !== s.equipment_id || row?.property !== RESUPPLY_RECEIVE_PROPERTY) return null;
    if (f.value !== s.amount) return null;
    // One public box also stocks the pack of the supply backpack actually worn; the
    // pack of a backpack carried in the loadout but not worn is never credited.
    const gear = catalogByID?.get(s.equipment_id);
    if (gear && (gear.active_slots || []).includes('backpack') && !(gear.active_slots || []).includes('support_weapon') && a.requires_slots?.backpack !== s.equipment_id) return null;
    const credit = credits.find(e => e.key === s.key);
    if (!credit || credit.delta !== s.amount || credit.offset_ms !== a.busy_ms) return null;
  }
  const source = r.time_source || {};
  if (typeof source.fact_ref === 'string') {
    if (!(a.fact_refs || []).includes(source.fact_ref)) return null;
    const row = model.facts.facts.find(x => x.id === source.fact_ref);
    const f = factValue(model, source.fact_ref, build, grade);
    if (!f.known) return null;
    if (row?.subject_id !== a.required_equipment[0]) return null;
    if (!RESUPPLY_TIME_PROPERTIES.has(row.property)) return null;
    if (row?.unit !== null && row?.unit !== 's') return null;
    if (typeof f.value !== 'number' || !Number.isFinite(f.value) || f.value < 0) return null;
    if (!Number.isSafeInteger(f.value * 1000) || f.value * 1000 !== a.busy_ms) return null;
    if (called && (source.fact_ref !== policy.call_in_fact_ref || policy.callInMs !== a.busy_ms)) return null;
  } else if (typeof source.synthetic === 'string') {
    if (called || !synthetic) return null;
    const e = (model.facts.evidence || []).find(x => x.id === source.synthetic);
    if (!e || e.kind !== 'synthetic' || e.state !== 'verified' || e.game_build !== build) return null;
    if (!(a.evidence_refs || []).includes(e.id)) return null;
  } else return null;
  return true;
}
// Demolition actions carry an explicit demolition block; the compiler re-verifies it
// against the actual events instead of trusting the text: the declared hole set must
// match the demolition events one-to-one (no undeclared holes, none declared-but-missing,
// no repeats), a declared component must equal every event component, a multi-hole
// closure binds a same-equipment source whose cap covers the actual count (the TED-63
// citation covers only its own weapon and is hard-capped at 2), demolition actions
// never carry positive resource credits, the single start-time debit must match the
// declared {key, resource_id, consumed>0} account which must be published and owned by
// the acting equipment (or the closure is explicitly no_cost with a resolvable
// synthetic source), and the timing provenance must resolve: a verified positive time
// fact of this subject, or synthetic evidence that only exists on synthetic profiles.
// Real builds without complete provenance fail closed here (REAL_CALIBRATION_REQUIRED
// stays a module-level report; the engine simply refuses to certify).
const DEMOLITION_FLAG_PREFIX='demolition:';
const MULTI_HOLE_WIKI={source:'ted63_well_placed',equipment_id:'ted_63_dynamite',max_holes:2};
function compileDemolition(model,a,build,synthetic,grade){
  const d=a.demolition;
  const demoEvents=(a.events||[]).filter(e=>e.kind==='demolition');
  if(!d||typeof d!=='object'||Array.isArray(d))return false;
  if(!demoEvents.length)return false;
  const equip=a.required_equipment[0];
  if(!Array.isArray(d.holes)||!d.holes.length||d.holes.some(k=>typeof k!=='string'||!k)||new Set(d.holes).size!==d.holes.length)return false;
  const actual=demoEvents.map(e=>e.key);
  if(new Set(actual).size!==actual.length||actual.length!==d.holes.length||actual.some(k=>!d.holes.includes(k)))return false;
  if(d.component_id!==undefined&&demoEvents.some(e=>e.component_id!==d.component_id))return false;
  if(demoEvents.length>1){
    const mh=d.multi_hole,src=mh?.source;
    if(!mh||mh.equipment_id!==equip||!Number.isSafeInteger(mh.max_holes)||mh.max_holes<1||demoEvents.length>mh.max_holes)return false;
    if(typeof src?.synthetic==='string'){
      if(!synthetic)return false;
      const e=(model.facts.evidence||[]).find(x=>x.id===src.synthetic);
      if(!e||e.kind!=='synthetic'||e.state!=='verified'||e.game_build!==build||!(a.evidence_refs||[]).includes(e.id))return false;
    } else if(!(src&&src.wiki===MULTI_HOLE_WIKI.source&&equip===MULTI_HOLE_WIKI.equipment_id&&demoEvents.length<=MULTI_HOLE_WIKI.max_holes))return false;
  }
  const events=a.events||[];
  if(events.some(e=>e.kind==='resource'&&Number.isFinite(e.delta)&&e.delta>0))return false;
  const neg=events.filter(e=>e.kind==='resource'&&Number.isFinite(e.delta)&&e.delta<0);
  const r=d.resource,nc=d.no_cost;
  if(r&&nc)return false;
  if(r){
    if(typeof r.key!=='string'||!r.key||typeof r.resource_id!=='string'||!r.resource_id||!Number.isFinite(r.consumed)||r.consumed<=0)return false;
    if(neg.length!==1||neg[0].key!==r.key||neg[0].delta!==-r.consumed||neg[0].offset_ms!==0)return false;
    const entry=(model.facts.resources||[]).find(x=>x.id===r.resource_id);
    if(!entry||entry.equipment_id!==equip)return false;
  } else if(nc){
    if(typeof nc.source!=='string'||!nc.source.startsWith('synthetic:'))return false;
    if(!synthetic)return false;
    const e=(model.facts.evidence||[]).find(x=>x.id===nc.source.slice('synthetic:'.length));
    if(!e||e.kind!=='synthetic'||e.state!=='verified'||e.game_build!==build||!(a.evidence_refs||[]).includes(e.id))return false;
    if(neg.length)return false;
  } else return false;
  const source=d.time_source||{};
  if(typeof source.fact_ref==='string'){
    if(!(a.fact_refs||[]).includes(source.fact_ref))return false;
    const row=model.facts.facts.find(x=>x.id===source.fact_ref);
    const f=factValue(model, source.fact_ref, build, grade);
    if(!f.known||row?.subject_id!==equip)return false;
    if(!['demolition_busy_ms','demolition_time','throw_time','use_time'].includes(row?.property))return false;
    if(row.unit!=='s'&&row.unit!=='ms')return false;
    const busyMs=f.value*(row.unit==='s'?1000:1);
    if(!Number.isSafeInteger(busyMs)||busyMs<1||busyMs!==a.busy_ms)return false;
    const offsetRef=source.offset_fact_ref;
    if(typeof offsetRef!=='string'||!(a.fact_refs||[]).includes(offsetRef))return false;
    const offsetRow=model.facts.facts.find(x=>x.id===offsetRef),offset=factValue(model, offsetRef, build, grade);
    if(!offset.known||offsetRow?.subject_id!==equip||!['demolition_offset_ms','fuse_time','trigger_delay'].includes(offsetRow?.property)||!['s','ms'].includes(offsetRow.unit))return false;
    const offsetMs=offset.value*(offsetRow.unit==='s'?1000:1);
    if(!Number.isSafeInteger(offsetMs)||offsetMs<0||demoEvents.some(e=>e.offset_ms!==offsetMs))return false;
  } else if(typeof source.synthetic==='string'){
    if(!synthetic)return false;
    const e=(model.facts.evidence||[]).find(x=>x.id===source.synthetic);
    if(!e||e.kind!=='synthetic'||e.state!=='verified'||e.game_build!==build||!(a.evidence_refs||[]).includes(e.id))return false;
  } else return false;
  return true;
}
// TASK-B composite detonation events (e.blast): the engine re-derives what the
// facts can support and enforces the event boundaries — it never trusts a
// caller-supplied total.
//   * 传递 (transfer): recomputed from the verified percent_to_main/overflow
//     facts of the claimed part; a wrong or unverified transfer fails.
//   * 成本 (cost): one detonation group per paid unit (the sum of the action's
//     negative resource deltas must equal the group count).
//   * 命中集合 (hit set): every claimed part must be a published part of the
//     claimed target, unique within its group; a group is either part claims
//     or the single fallback/main application, never both.
//   * 总伤害 (total): the group's pool-relevant sum must equal the route's
//     hit_path amount for determinate single-claim/merged routes, and the
//     transfer+main sum for explicit hit-set routes.
function compileBlastEvent(model, a, e, build, synthetic, grade) {
  if (!synthetic && !verifiedBlastAmount(model, a, e, build, (m,id,b)=>factValue(m,id,b,grade), (m,refs,b)=>evidenceKnown(m,refs,b,grade), grade)) return false;
  const b = e.blast;
  if (!b || typeof b !== 'object') return false;
  if (typeof e.detonation_id !== 'string' || !e.detonation_id) return false;
  if (!Number.isFinite(e.amount) || e.amount < 0) return false;
  // The component must belong to an allowed, required equipment.
  const component = (model.facts.components || []).find(c => c.id === b.component_id);
  if (!component) return false;
  const owner = component.equipment_id;
  if (!a.required_equipment?.includes(owner) || !model.catalog?.equipment?.find(x => x.id === owner)?.component_ids?.includes(component.id)) return false;
  // Part binding: the claimed part must be published on the claimed target,
  // and the cited fact ids must be that part's own rows.
  const targetEntry = (model.targets?.targets || []).find(t => t.id === e.target_id);
  if (!targetEntry) return false;
  const claimPartId = b.contact_share === 'direct' ? (e.part_id ?? 'main') : (b.claim?.part_id ?? e.part_id ?? 'main');
  if (!claimPartId || typeof claimPartId !== 'string') return false;
  if (b.contact_share !== 'direct' && (e.part_id ?? undefined) !== (claimPartId === 'main' ? undefined : claimPartId)) return false;
  if (e.part_id != null && typeof e.part_id !== 'string') return false;
  const partRow = (targetEntry.parts || []).find(p => p.id === `${e.target_id}.${claimPartId}`);
  if (!partRow) return false;
  const facts = b.facts ?? {};
  const expectedPartFacts = {armor: partRow.properties?.armor_level, durable: partRow.properties?.durable, health: partRow.properties?.health, fatal: partRow.properties?.fatal, percent_to_main: partRow.properties?.percent_to_main, overflow_cap: partRow.properties?.overflow_cap};
  for (const [key, expected] of Object.entries(expectedPartFacts)) {
    if (facts[key] === undefined || facts[key] === null) continue;
    if (facts[key] !== expected) return false; // a fact id from another part/target never proves this hit
  }
  for (const id of Object.values(facts)) {
    if (id === undefined || id === null) continue;
    if (typeof id !== 'string' || !factValue(model, id, build, grade).known) return false;
  }
  // Transfer re-derivation: percent_to_main is a percent-text fact; the
  // capped rule trims at the part's verified pool. Detached parts ("-")
  // never carry a transfer.
  if (e.part_id != null && e.transfer != null) {
    const pctFact = facts.percent_to_main;
    if (!pctFact) return false;
    const f = factValue(model, pctFact, build, grade);
    if (!f.known || typeof f.value !== 'string') return false;
    const trimmed = f.value.trim();
    if (trimmed === '-') return false;
    const m = /^(\d+(?:\.\d+)?)\s*%$/.exec(trimmed);
    if (!m) return false;
    const fraction = Number(m[1]) / 100;
    let expected = Math.ceil(e.amount * fraction);
    const rule = b.overflow_rule;
    if (rule && rule.rule === 'capped_at_remaining') {
      const poolFact = facts.health;
      const pool = poolFact ? factValue(model, poolFact, build, grade) : {known: false};
      if (!pool.known || !Number.isFinite(pool.value)) return false;
      expected = Math.ceil(Math.min(e.amount, pool.value) * fraction);
    }
    if (e.transfer !== expected) return false;
  }
  if (e.part_id == null && e.transfer != null) return false; // main application never transfers
  // Action-level boundaries: unique parts per detonation group, one fallback
  // application at most, groups paid once.
  const blastEvents = (a.events || []).filter(x => x.kind === 'damage' && x.blast);
  const groups = new Map();
  for (const x of blastEvents) {
    if (typeof x.detonation_id !== 'string' || !x.detonation_id) return false;
    if (!groups.has(x.detonation_id)) groups.set(x.detonation_id, []);
    groups.get(x.detonation_id).push(x);
  }
  for (const group of groups.values()) {
    if (new Set(group.map(x => x.offset_ms)).size !== 1 || new Set(group.map(x => x.key)).size !== 1 || new Set(group.map(x => x.target_id)).size !== 1) return false;
    const partIds = group.filter(x => x.blast?.contact_share !== 'direct').map(x => x.part_id ?? null);
    if (new Set(partIds).size !== partIds.length) return false; // same part instance twice in one detonation
    const mains = partIds.filter(p => p === null).length;
    if (mains > 1 || (mains === 1 && partIds.length > 1)) return false; // fallback + part claims double count
    const directs = group.filter(x => x.blast?.contact_share === 'direct').length;
    if (directs > 1) return false; // the two components of one trigger, never two triggers
  }
  const debits = (a.events || []).filter(x => x.kind === 'resource' && Number.isFinite(x.delta) && x.delta < 0).reduce((n, x) => n + -x.delta, 0);
  const callOnly = a.blast_cost?.kind === 'call';
  if (callOnly) {
    const cost=a.blast_cost, f=factValue(model, cost.cooldown_fact_ref, build, grade), row=model.facts.facts.find(x=>x.id===cost.cooldown_fact_ref);
    if(!synthetic){
      const u=factValue(model, cost.uses_fact_ref, build, grade), ur=model.facts.facts.find(x=>x.id===cost.uses_fact_ref);
      if(!u.known||ur.subject_id!==owner||ur.property!=='uses'||typeof u.value!=='string'||!['unlimited','∞'].includes(u.value.trim().toLowerCase()))return false;
    }
    if (groups.size!==1 || debits!==0 || cost.equipment_id!==owner || row?.subject_id!==owner || row.property!=='cooldown' || !f.known || !Number.isFinite(f.value) || f.value<=0 || row.unit!=='s' || a.cooldown?.key!==`cooldown:${owner}` || a.cooldown.duration_ms!==Math.round(f.value*1000)) return false;
  } else {
    if (debits !== groups.size) return false;
    if (!synthetic) {
      const cost=a.blast_cost, resource=model.facts.resources?.find(r=>r.id===cost?.resource_id);
      const spent=(a.events||[]).filter(x=>x.kind==='resource'&&x.delta<0);
      if (cost?.kind!=='ammo'||resource?.equipment_id!==owner||spent.length!==1||spent[0].key!==cost.key||spent[0].delta!==-groups.size) return false;
    }
  }
  // Total anchor: the group's pool-relevant sum equals the route's declared
  // per-shot amount (single-claim / merged), or the transfer+main sum for hit
  // sets. A group that cannot reach its route's number is a forged total.
  const it = a.integration;
  if (it?.hit_path && Number.isFinite(it.hit_path.amount)) {
    for (const group of groups.values()) {
      let total = 0;
      if (it.route_label === 'hit_set') {
        for (const x of group) total += (x.part_id != null ? (x.transfer ?? 0) : x.amount);
      } else {
        const poolPart = it.route_label === 'part' ? it.route_id : null;
        for (const x of group) {
          if (poolPart ? x.part_id === poolPart : x.part_id == null) total += x.amount;
        }
      }
      if (total !== it.hit_path.amount) return false;
    }
  }
  return true;
}
export function caseDependencies(model, spec, profile) {
  const grade=declaredGrade(profile);
  if((spec.fact_refs||[]).some(id=>!factValue(model, id, profile.game_build, grade).known))return false;
  const synthetic=syntheticProfile(model,profile);
  if(!synthetic&&!evidenceKnown(model, spec.evidence_refs, profile.game_build, grade))return false;
  const instances=[...Object.entries(spec.initial?.targets||{}),...(spec.observations||[]).flatMap(o=>o.alternatives.flatMap(a=>(a.events||[]).filter(e=>e.kind==='spawn').map(e=>[e.key,e.target])))];
  for(const [id,t] of instances) {
    const target=model.targets?.targets?.find(x=>x.id===(t.target_id||id));
    if(target) {
      const refs=[...(target.fact_refs||[]),...(target.parts||[]).filter(p=>['body','main','global'].includes(p.id)).flatMap(p=>Object.entries(p.properties||{}).filter(([k])=>['hp','health','post_lethal_ms'].includes(k)).map(([,v])=>v))];
      if(refs.some(ref=>!factValue(model, ref, profile.game_build, grade).known))return false;
    }
    if(!synthetic) {
      if(!target)return false;
      for(const k of ['hp','post_lethal_ms']) {const f=factValue(model, t[k+'_fact'], profile.game_build, grade);if(!f.known||f.value!==(t[k]??0))return false;}
      for (const [partID, state] of Object.entries(t.parts || {})) {
        const part = target.parts?.find(p => p.id === partID || p.id === `${target.id}.${partID}`);
        const hp = factValue(model, part?.properties?.health, profile.game_build, grade);
        const fatal = factValue(model, part?.properties?.fatal, profile.game_build, grade);
        if (!part || !hp.known || hp.value !== state.hp || !fatal.known || fatal.value !== state.fatal) return false;
      }
    }
  }
  return true;
}
function compileDeployment(model,a,build,synthetic,grade){
  const d=a.deployment;
  if(!d||!['sentry','mine'].includes(d.kind)||a.required_equipment?.length!==1||d.equipment_id!==a.required_equipment[0]||d.flag!==`deployed:${d.equipment_id}`)return false;
  if(model.catalog.equipment.find(e=>e.id===d.equipment_id)?.selection_slot!=='stratagem')return false;
  if(d.lifetime_ms!==null&&(!Number.isSafeInteger(d.lifetime_ms)||d.lifetime_ms<1))return false;
  if(d.lifetime_ms===null&&d.kind!=='mine')return false;
  const flags=a.events.filter(e=>e.kind==='flag');
  if(flags.length!==1||flags[0].key!==d.flag||flags[0].value!==true||flags[0].offset_ms!==a.busy_ms)return false;
  if(a.events.some(e=>e.kind!=='flag'&&(e.kind!=='resource'||e.delta>=0||e.offset_ms!==0))||!a.cooldown)return false;
  if(!synthetic){
    const own=(ref,property)=>{const f=model.facts.facts.find(f=>f.id===ref),v=factValue(model, ref, build, grade);return v.known&&f?.subject_id===d.equipment_id&&f.property===property?{...v,unit:f.unit}:null;};
    const kind=own(d.kind_fact_ref,'deployment_kind'),life=own(d.lifetime_fact_ref,'active_lifetime'),call=own(d.call_in_fact_ref,'call_in_time'),cd=own(d.cooldown_fact_ref,'cooldown');
    if(kind?.value!==d.kind||!life||!call||!cd||typeof call.value!=='number'||!Number.isFinite(call.value)||typeof cd.value!=='number'||!Number.isFinite(cd.value)||cd.value<=0||call.unit!=='s'||cd.unit!=='s'||call.value*1000!==a.busy_ms||a.cooldown.key!==`cooldown:${d.equipment_id}`||cd.value*1000!==a.cooldown.duration_ms)return false;
    if(d.lifetime_ms===null?life.value!=='until_triggered':life.unit!=='s'||typeof life.value!=='number'||life.value*1000!==d.lifetime_ms)return false;
    for(const e of a.events.filter(e=>e.kind==='resource')){
      const r=model.facts.resources?.find(r=>r.id===d.resource_bindings?.[e.key]);if(r?.equipment_id!==d.equipment_id)return false;
    }
  }
  return true;
}
export function compileAction(model, a, loadout, profile) {
  const allowed=new Set(equipmentIDs(loadout)), build=profile.game_build;
  const grade=declaredGrade(profile);
  // Certification paths see the verified bundle view: the extra backpack slot exists
  // only where the bundled trait is verified for this exact game build.
  const byID=new Map(verifiedBundleCatalog(model,build,grade).equipment.map(e=>[e.id,e]));
  // A called box is a system action admitted only through the registered public_supply
  // policy bound to this exact subject; the policy subject joins the admissible set for
  // this action alone and never becomes a draw slot.
  const calledKind = !!(a && a.resupply && RESUPPLY_CALLED_KINDS.has(a.resupply.kind));
  const supplyPolicy = calledKind ? verifySupplyPolicy(model, profile.public_supply, build, grade) : null;
  if (calledKind && (!supplyPolicy || a.required_equipment?.[0] !== supplyPolicy.subject_id)) return null;
  if (supplyPolicy) allowed.add(supplyPolicy.subject_id);
  if(!a.id||a.state!==grade||!a.required_equipment?.length||a.required_equipment.some(id=>!allowed.has(id))||!time(a.busy_ms)||a.busy_ms<1||!Array.isArray(a.events)||!a.events.every(eventValid)||!evidenceKnown(model, a.evidence_refs, build, grade)||(a.fact_refs||[]).some(id=>!factValue(model, id, build, grade).known))return null;
  if(a.excluded_dimensions !== undefined && (!Array.isArray(a.excluded_dimensions) || a.excluded_dimensions.some(d => !DIMENSIONS.includes(d)))) return null;
  if(a.cooldown&&(!a.cooldown.key||!time(a.cooldown.duration_ms)))return null;
  if(a.requires_resources!==undefined) {
    if(!a.requires_resources||typeof a.requires_resources!=='object'||Array.isArray(a.requires_resources))return null;
    for(const [key,bounds] of Object.entries(a.requires_resources)) {
      if(!key||!bounds||typeof bounds!=='object'||Array.isArray(bounds)||!Object.keys(bounds).length||Object.keys(bounds).some(k=>!['min','max'].includes(k)))return null;
      if(Object.values(bounds).some(v=>!Number.isFinite(v)||v<0)||bounds.min!==undefined&&bounds.max!==undefined&&bounds.min>bounds.max)return null;
    }
  }
  // Held gear must be from the loadout AND role-legal for its slot: a grenade or primary
  // cannot occupy the weapon/backpack slots even when selected. A bundled weapon's own
  // id may occupy the backpack slot it ships with — but only under the verified view.
  if(Object.entries(a.requires_slots||{}).some(([slot,id])=>!allowed.has(id)||!canOccupy(byID.get(id),slot)))return null;
  if(a.reload!==undefined && !compileReload(model,a,build,syntheticProfile(model,profile),grade))return null;
  if(a.resupply!==undefined && !compileResupply(model,a,build,syntheticProfile(model,profile),supplyPolicy,byID,grade))return null;
  // A demolition-bearing action must carry a substantive, event-matched demolition
  // declaration; a bare demolition block or bare demolition events never compile, and
  // synthetic shortcuts (multi-hole/no-cost/time provenance) only exist on synthetic
  // profiles, never inside a real build.
  if((a.demolition!==undefined||(a.events||[]).some(e=>e&&e.kind==='demolition'))&&!compileDemolition(model,a,build,syntheticProfile(model,profile),grade))return null;
  if(a.deployment!==undefined&&!compileDeployment(model,a,build,syntheticProfile(model,profile),grade))return null;
  const action=structuredClone(a);
  if(action.deployment)for(const e of action.events)if(e.kind==='flag'&&action.deployment.lifetime_ms!==null)e.expires_after_ms=action.deployment.lifetime_ms;
  for(const e of action.events) {
    delete e.deployment_guards;
    // The declared escape window must fit inside the action's own busy time: the
    // carrying action actually executes the window it declares, at both compile
    // and replay time a shorter action is not a substitute for it.
    if(e.kind==='disengage'&&e.offset_ms+e.duration_ms>a.busy_ms)return null;
    // No ordinary flag may enter the demolition closure namespace at compile time either.
    if(e.kind==='flag'&&typeof e.key==='string'&&e.key.startsWith(DEMOLITION_FLAG_PREFIX))return null;
    if(e.kind==='equip'&&(!allowed.has(e.equipment_id)||!canOccupy(byID.get(e.equipment_id),e.key)))return null;
    if(e.kind==='demolition') {
      const c=model.facts.components?.find(c=>c.id===e.component_id), gear=model.catalog.equipment.find(x=>x.id===c?.equipment_id);
      if(!c||!allowed.has(c.equipment_id)||!a.required_equipment.includes(c.equipment_id)||!gear?.component_ids?.includes(c.id))return null;
      const demo=factValue(model, c.properties?.demolition, build, grade), usable=factValue(model, c.properties?.usable, build, grade);
      const route=profile.demolition_routes?.find(r=>r.id===e.route_id);
      const boundRoute=route&&route.component_id===c.id&&route.profile_id===profile.id&&evidenceKnown(model, route.evidence_refs, build, grade)?route:null;
      if(!demolitionRoute({id:c.id,kind:c.kind,demolition:demo.known?demo.value:null,usable:usable.known&&usable.value===true,state:grade,evidence_refs:a.evidence_refs},boundRoute,grade))return null;
      e.route_verified=true;
    }
    if(e.kind==='damage'&&e.blast) {
      if(!compileBlastEvent(model,a,e,build,syntheticProfile(model,profile),grade))return null;
      if(e.blast.overflow_rule?.rule==='capped_at_remaining') {
        const f=factValue(model, e.blast.facts.percent_to_main, build, grade);
        const m=typeof f.value==='string'&&/^(\d+(?:\.\d+)?)\s*%$/.exec(f.value.trim());
        if(!m)return null;
        e.transfer_policy={rule:'capped_at_remaining',fraction:Number(m[1])/100};
      } else delete e.transfer_policy;
    } else if(e.kind==='damage'&&e.direct) {
      const input={};
      for(const [key,id] of Object.entries(e.direct)) {const f=factValue(model, id, build, grade);if(!f.known)return null;input[key]=f.value;}
      const result=directDamage(input);if(result.status!=='KNOWN'||e.amount!==result.damage)return null;
    } else if(e.kind==='damage'&&!syntheticProfile(model,profile)) {
      const path=profile.hit_paths?.find(p=>p.id===e.path_id);
      if(!path||path.equipment_id!==a.required_equipment.find(id=>id===path.equipment_id)||path.target_id!==e.target_id||path.part_id!==e.part_id||path.amount!==e.amount||!evidenceKnown(model, path.evidence_refs, build, grade))return null;
    }
  }
  return action;
}
