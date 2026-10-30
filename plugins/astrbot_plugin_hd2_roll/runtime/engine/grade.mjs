// Evidence grade: WHICH KIND of evidence an evaluation is allowed to read.
//
// One run reads exactly ONE grade. There is no mixing and no upgrading:
//
//   'verified'        game-measured facts bound to a concrete game_build: fact
//                     state 'verified' + a non-null value + every cited evidence
//                     row verified for the same build. This is the historical
//                     contract and stays the default for every caller that does
//                     not opt in.
//   'source_reviewed' per-field wiki source review (review-records whitelist):
//                     fact state 'source_reviewed' + the reviewed value + one
//                     base review record + the applied review evidence row
//                     carrying url/revid-or-fetch-time/locator, and NO
//                     game_build claim (game_build must be null on both sides).
//
// Compatibility matrix (a row is readable only in its own column):
//
//   row shape                                            verified  source_reviewed
//   fact.state 'verified' + same-build verified evidence   accept    reject
//   fact.state 'source_reviewed' + review binding          reject    accept
//   fact.state 'unknown' / 'conflicted' / value null       reject    reject
//   candidate_value only, no closing record                reject    reject
//   evidence kind 'synthetic' (build synthetic-only)       diag.*    reject
//   evidence 'ev.staging.*' (kind staging_candidate)       reject    reject
//   evidence kind 'field_review' with a game_build         reject    reject
//
// (*) synthetic evidence only ever backs the diagnostic synthetic profile path
// that already existed (engine/proof.mjs syntheticProfile); it is never a real
// material source and is refused at the source-reviewed grade outright.
//
// The grade is never inferred from the model: a caller selects it explicitly
// (options.evidence_grade), the profile declares the grade it was calibrated at
// (profile.evidence_grade / profile.calibration.state), and a source-reviewed
// read additionally requires the model's own evidence set (manifest
// evidence_grade + evidence_set_id + records_sha256) and the profile's matching
// evidence_set_id. A disabled verified profile can therefore never be read at
// the source-reviewed grade and vice versa.
import {stable, profileReady} from './common.mjs';

export const VERIFIED = 'verified';
export const SOURCE_REVIEWED = 'source_reviewed';
export const EVIDENCE_GRADES = Object.freeze([VERIFIED, SOURCE_REVIEWED]);
export const DEFAULT_EVIDENCE_GRADE = VERIFIED;
const REVIEW_STATE = SOURCE_REVIEWED;
const REVIEW_EVIDENCE_KIND = 'field_review';
const REVIEW_EVIDENCE_PREFIX = 'ev.reviewed.';

// What a finished run of one grade may claim. A source-reviewed run never borrows
// the game-measured vocabulary.
export const PASS_STATUS = Object.freeze({verified: 'CERTIFIED', source_reviewed: 'SOURCE_REVIEWED_CERTIFIED'});

export function isEvidenceGrade(grade) {
  return typeof grade === 'string' && EVIDENCE_GRADES.includes(grade);
}

// The caller's explicit choice. Absent means the historical verified gate — an
// unselected grade never widens admission. A malformed selection is refused by
// the caller (null), never silently replaced by the default.
export function selectEvidenceGrade(requested) {
  if (requested === undefined || requested === null) return DEFAULT_EVIDENCE_GRADE;
  return isEvidenceGrade(requested) ? requested : null;
}

export function declaredGrade(profile) {
  const grade = profile?.evidence_grade;
  return grade === undefined ? DEFAULT_EVIDENCE_GRADE : grade;
}

export function passStatus(grade) {
  return isEvidenceGrade(grade) ? PASS_STATUS[grade] : null;
}

// A draw/case result counts as a pass only at its own grade: 'CERTIFIED' can only
// ever be a verified result and the source-reviewed status only a reviewed one.
export function isPassResult(result) {
  if (!result) return false;
  if (result.status === 'UNVERIFIED') return true;
  const grade = result.evidence_grade;
  return isEvidenceGrade(grade) && result.status === PASS_STATUS[grade];
}

// The reviewed build product declares its own evidence set. Without that
// declaration no row is source-reviewed-readable, even if the row looks right:
// the source version marker is part of the grade, not an optional annotation.
const SHA256 = /^[0-9a-f]{64}$/;
export function evidenceSetIdOf(recordsSha) {
  return 'reviewed:' + recordsSha;
}
export function reviewedEvidenceSet(model) {
  const manifest = model?.manifest || {};
  // The marker is the review-record set the rows were applied from, not a game build:
  // a reviewed product names it explicitly (newer builds) or carries the audited record
  // hash itself (the currently published reviewed model). Either way the id derives from
  // that hash, and a manifest that declares another grade or another set is refused.
  const records = manifest.records_sha256;
  if (typeof records !== 'string' || !SHA256.test(records)) return null;
  if (typeof manifest.input_model_hash !== 'string' || !SHA256.test(manifest.input_model_hash)) return null;
  const derived = evidenceSetIdOf(records);
  if (manifest.evidence_grade !== undefined && manifest.evidence_grade !== SOURCE_REVIEWED) return null;
  if (manifest.evidence_set_id !== undefined && manifest.evidence_set_id !== derived) return null;
  return {evidence_set_id: derived, records_sha256: records};
}

// One profile is admissible at one grade. Verified keeps the historical test
// (profileReady); source-reviewed needs the profile to declare that grade, to be
// bound to the model's own evidence set, and to make no game_build claim.
export function gradeGate(profile, grade, model) {
  if (!isEvidenceGrade(grade)) return {ok: false, reason: 'EVIDENCE_GRADE_UNKNOWN'};
  if (declaredGrade(profile) !== grade) return {ok: false, reason: 'PROFILE_EVIDENCE_GRADE_MISMATCH', declared: declaredGrade(profile), requested: grade};
  if (grade === VERIFIED) {
    if (!profileReady(profile)) return {ok: false, reason: 'PROFILE_NOT_CERTIFIED'};
    return {ok: true, pass_status: PASS_STATUS.verified, evidence_set_id: null};
  }
  // A source-reviewed profile is not build-bound, so the verified profileReady shape
  // (truthy game_build) cannot apply: it must be enabled and declare the grade.
  if (profile?.enabled !== true) return {ok: false, reason: 'PROFILE_NOT_CERTIFIED'};
  const set = reviewedEvidenceSet(model);
  if (!set) return {ok: false, reason: 'REVIEWED_EVIDENCE_SET_MISSING'};
  if (profile?.calibration?.state !== SOURCE_REVIEWED) return {ok: false, reason: 'PROFILE_EVIDENCE_GRADE_MISMATCH', declared: profile?.calibration?.state ?? null, requested: grade};
  if (profile?.game_build !== null) return {ok: false, reason: 'REVIEWED_GRADE_CLAIMS_BUILD'};
  if (profile?.evidence_set_id !== set.evidence_set_id) return {ok: false, reason: 'REVIEWED_EVIDENCE_SET_MISMATCH', evidence_set_id: profile?.evidence_set_id ?? null, expected: set.evidence_set_id};
  return {ok: true, pass_status: PASS_STATUS.source_reviewed, evidence_set_id: set.evidence_set_id};
}

export function profileReadyAtGrade(model, profile, grade) {
  return gradeGate(profile, grade, model).ok;
}

const evidenceIndex = model => new Map((model?.facts?.evidence || []).map(e => [e.id, e]));

// Structural binding of ONE applied review evidence row. It must be a field
// review, carry the review record id, the source url and either a revid or a
// fetch time, be graded source_reviewed itself, and claim no game_build at all.
export function reviewedEvidenceRow(row) {
  if (!row || typeof row !== 'object') return false;
  // Only a per-field review record closes a row. A synthetic/staging umbrella or a
  // not_closable marker that merely carries the reviewed state string is refused.
  if (row.kind !== REVIEW_EVIDENCE_KIND) return false;
  if (row.state !== REVIEW_STATE) return false;
  if (row.game_build !== null) return false;
  if (typeof row.record_id !== 'string' || !row.record_id) return false;
  if (row.evidence_level !== REVIEW_STATE) return false;
  if (typeof row.source_url !== 'string' || !/^https:\/\/\S+$/.test(row.source_url)) return false;
  const revid = row.revid != null && String(row.revid).length > 0;
  const observed = typeof row.observed_at === 'string' && row.observed_at.length > 0;
  if (!revid && !observed) return false;
  // The locator is the row position inside the cited source; without it the record
  // cannot be re-found, so it is part of the binding and not decoration.
  if (typeof row.locator !== 'string' || !row.locator.trim()) return false;
  return true;
}

// Subject binding of a reviewed fact: the row id and its component must both belong to
// the subject the record names, so a reviewed row cannot be re-labelled onto another
// equipment/target. When the target is published, a part row must also be one of that
// target's own published parts.
function subjectBound(model, fact) {
  const subject = fact?.subject_id;
  if (typeof subject !== 'string' || !subject) return false;
  const id = fact.id, component = fact.component_id;
  if (typeof id !== 'string') return false;
  const owned = value => value === subject || value.startsWith(subject + '.');
  if (!(owned(id) || id.startsWith('t.' + subject + '.'))) return false;
  if (component == null) return true;
  if (typeof component !== 'string' || !owned(component)) return false;
  const target = (model?.targets?.targets || []).find(t => t.id === subject);
  if (!target) return true;
  const parts = (target.parts || []).map(p => p.id);
  if (!parts.length) return true;
  return parts.includes(component) || parts.includes(subject + '.' + component.split('.').slice(1).join('.'));
}

// A source-reviewed read cites review rows plus ordinary provenance wrappers
// (e.g. the extracted item row). A wrapped row may carry no graded claim: no
// verified evidence, no synthetic/staging umbrella, no not_closable marker.
function sourceCited(row) {
  if (!row) return false;
  if (row.state === VERIFIED) return false;
  if (row.kind === 'synthetic' || row.kind === 'staging_candidate') return false;
  if (row.state === REVIEW_STATE) return row.kind !== 'not_closable';
  return true;
}

// Why a reviewed fact is not readable at the source-reviewed grade (null = readable).
// Kept explicit so checks and probes can name the exact refusal instead of a bare false.
export function reviewRefusal(model, fact) {
  if (!fact) return 'FACT_MISSING';
  if (fact.state !== REVIEW_STATE) return 'FACT_STATE_' + String(fact.state);
  if (fact.value == null) return 'FACT_VALUE_NULL';
  const records = Array.isArray(fact.review_ref) ? fact.review_ref : null;
  if (!records || !records.length) return 'REVIEW_RECORD_MISSING';
  if (records.some(r => typeof r !== 'string' || !r)) return 'REVIEW_RECORD_INVALID';
  const selections = (fact.reviewed_selections || []).filter(s => (s.condition ?? null) === null);
  if (selections.length !== 1) return 'REVIEW_BASE_SELECTION_MISSING';
  if (stable(selections[0].value) !== stable(fact.value)) return 'REVIEW_VALUE_MISMATCH';
  if (!records.includes(selections[0].record_id)) return 'REVIEW_RECORD_MISMATCH';
  const refs = fact.evidence_refs || [];
  for (const record of records) {
    const id = REVIEW_EVIDENCE_PREFIX + record;
    if (!refs.includes(id)) return 'REVIEW_EVIDENCE_MISSING';
    const row = evidenceIndex(model).get(id);
    if (!reviewedEvidenceRow(row)) return 'REVIEW_EVIDENCE_UNBOUND';
    if (row.record_id !== record) return 'REVIEW_EVIDENCE_RECORD_MISMATCH';
  }
  for (const ref of refs) {
    if (records.some(r => REVIEW_EVIDENCE_PREFIX + r === ref)) continue;
    if (!sourceCited(evidenceIndex(model).get(ref))) return 'REVIEW_FOREIGN_CLAIM';
  }
  if (!subjectBound(model, fact)) return 'REVIEW_SUBJECT_MISMATCH';
  // The review record's unit is the semantic unit (hp / rounds / s); the fact keeps the
  // source's own token ('Ballistic', 'seconds', null). Only a recognised-vs-recognised
  // disagreement is a mismatch — the build step already rejected those (s ≡ seconds).
  const reviewFamily = unitFamily(selections[0].unit), factFamily = unitFamily(fact.unit);
  if (reviewFamily && factFamily && reviewFamily !== factFamily) return 'REVIEW_UNIT_MISMATCH';
  return null;
}

// Semantic unit families. Unknown tokens (damage types, source-specific words) are not
// units: they carry no conflict, which mirrors the reviewed build step.
const UNIT_FAMILIES = new Map([
  ['s', 's'], ['sec', 's'], ['secs', 's'], ['second', 's'], ['seconds', 's'],
  ['ms', 'ms'], ['millisecond', 'ms'], ['milliseconds', 'ms'],
  ['hp', 'hp'],
  ['round', 'rounds'], ['rounds', 'rounds'],
  ['mag', 'magazines'], ['mags', 'magazines'], ['magazine', 'magazines'], ['magazines', 'magazines'],
  ['shell', 'shells'], ['shells', 'shells'],
  ['charge', 'charges'], ['charges', 'charges'],
  ['%', 'percent'], ['percent', 'percent'],
]);
export function unitFamily(unit) {
  if (unit == null) return null;
  return UNIT_FAMILIES.get(String(unit).trim().toLowerCase()) ?? null;
}

export function evidenceKnownAt(model, refs, build, grade = DEFAULT_EVIDENCE_GRADE) {
  if (!isEvidenceGrade(grade)) return false;
  if (!refs?.length) return false;
  const index = evidenceIndex(model);
  if (grade === VERIFIED) return refs.every(id => {
    const row = index.get(id);
    return row?.state === VERIFIED && row.game_build === build;
  });
  // A source-reviewed run is not build-bound: a build-bound request belongs to the
  // verified grade and never borrows reviewed rows.
  if (build !== null && build !== undefined) return false;
  if (!reviewedEvidenceSet(model)) return false;
  if (!refs.every(id => sourceCited(index.get(id)))) return false;
  return refs.some(id => index.get(id)?.state === REVIEW_STATE);
}

export function factAtGrade(model, id, build, grade = DEFAULT_EVIDENCE_GRADE) {
  const fact = (model?.facts?.facts || []).find(f => f.id === id);
  if (!fact || fact.value == null) return {known: false};
  if (!isEvidenceGrade(grade)) return {known: false};
  if (grade === VERIFIED) {
    return fact.state === VERIFIED && evidenceKnownAt(model, fact.evidence_refs, build, VERIFIED)
      ? {known: true, value: fact.value} : {known: false};
  }
  if (build !== null && build !== undefined) return {known: false};
  if (!reviewedEvidenceSet(model)) return {known: false};
  if (reviewRefusal(model, fact) !== null) return {known: false};
  return {known: true, value: fact.value};
}

// An action is admitted at the grade it declares: a reviewed run never accepts an
// action stamped 'verified' (that would be a game-measurement claim it cannot
// support), and the verified run keeps requiring exactly 'verified'.
export function actionGrades(grade) {
  return isEvidenceGrade(grade) ? [grade] : [];
}
