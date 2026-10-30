// Bundled-backpack capability, derived at runtime from the model's own traits source
// rows (`<id>.infobox.traits`). Two evidence tiers, two views:
//   - candidate tier (bundleBackpacks / bundleCatalog): any traits row whose tokens
//     carry both 'Support Weapon' and 'Backpack'. Structure extraction only — usable
//     for checking and proposal, exactly like catalog.active_slots.
//   - verified tier (verifiedBundles / verifiedBundleCatalog(model, build)): the subset
//     whose traits fact is state 'verified' with a non-null value and verified
//     same-build evidence. Only this view authorizes certification paths (compileAction,
//     evaluator role checks, replay), so a witness that actually depends on the extra
//     backpack slot never rests on an unknown row. An unresolved row never poisons
//     other gear.
// A card whose weapon and backpack travel together stays one equipment id and one draw
// ticket; the backpack is that same id in the backpack slot.
import {factAtGrade} from './grade.mjs';

const TOKENS = Object.freeze({weapon: 'Support Weapon', backpack: 'Backpack'});
const traitsTokens = raw => String(raw ?? '').split('•').map(t => t.trim()).filter(Boolean);

export function bundleBackpacks(model) {
  const facts = new Map((model?.facts?.facts || []).map(f => [f.id, f]));
  const bundled = new Map(), unresolved = new Map();
  for (const e of model?.catalog?.equipment || []) {
    if (!(e.active_slots || []).includes('support_weapon')) continue;
    const fact = facts.get(`${e.id}.infobox.traits`);
    if (!fact) { unresolved.set(e.id, {reason: 'TRAITS_ROW_MISSING'}); continue; }
    if (fact.state === 'conflicted' || fact.conflicts?.length) { unresolved.set(e.id, {reason: 'TRAITS_ROW_CONFLICTED', fact_id: fact.id}); continue; }
    const tokens = traitsTokens(fact.value ?? fact.candidate_value);
    if (!tokens.length) { unresolved.set(e.id, {reason: 'TRAITS_ROW_UNPARSED', fact_id: fact.id}); continue; }
    if (!tokens.includes(TOKENS.weapon) || !tokens.includes(TOKENS.backpack)) continue;
    bundled.set(e.id, {fact_id: fact.id, traits: tokens, evidence_refs: [...(fact.evidence_refs || [])]});
  }
  return {bundled, unresolved};
}

// The verified subset for one game build: the traits fact must be verified with a
// non-null value and every cited evidence piece verified for exactly that build.
// build===undefined yields an empty set, never a guess.
export function verifiedBundles(model, build, grade) {
  // The bundled backpack slot is only as strong as the grade of the traits row it is
  // derived from: a same-build verified row on the verified grade, a fully bound
  // source-reviewed row on the source-reviewed grade. No grade, no slot.
  const verified = new Map();
  for (const [id, entry] of bundleBackpacks(model).bundled) {
    if (factAtGrade(model, entry.fact_id, build, grade).known) verified.set(id, entry);
  }
  return verified;
}

const augment = (catalog, bundled) => {
  const freeze = value => { if (value && typeof value === 'object') Object.values(value).forEach(freeze); return Object.freeze(value); };
  return freeze({
    ...catalog,
    equipment: catalog.equipment.map(e => bundled.has(e.id) && !(e.active_slots || []).includes('backpack')
      ? freeze({...e, active_slots: Object.freeze([...(e.active_slots || []), 'backpack'])})
      : e),
  });
};

const candidateCache = new WeakMap();
const verifiedCache = new WeakMap();

// Read-only candidate view (checking/proposal). The published catalog object is never
// mutated (it is frozen after load and its bytes back the model hash); identical
// models share one derived view.
export function bundleCatalog(model) {
  if (!model?.catalog) return model?.catalog;
  if (!candidateCache.has(model)) candidateCache.set(model, augment(model.catalog, bundleBackpacks(model).bundled));
  return candidateCache.get(model);
}

// Read-only verified view for certification paths. Only weapons whose bundled trait is
// verified for this exact game build gain the backpack slot here.
export function verifiedBundleCatalog(model, build, grade) {
  if (!model?.catalog) return model?.catalog;
  let byBuild = verifiedCache.get(model);
  if (!byBuild) { byBuild = new Map(); verifiedCache.set(model, byBuild); }
  const key = `${grade}:${build}`;
  if (!byBuild.has(key)) byBuild.set(key, augment(model.catalog, verifiedBundles(model, build, grade)));
  return byBuild.get(key);
}
