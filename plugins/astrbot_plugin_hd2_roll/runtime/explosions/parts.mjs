// Part panel normalisation from the wiki enemy snapshot.
//
// The snapshot stores raw display strings ("100%", "70%", "Yes", "-", null, and a few
// prose health values). This file converts them into typed values while keeping every
// unreadable / missing value as unknown. Nothing is defaulted towards a favourable
// number: a null pool stays unknown, a "-" main multiplier stays "not applicable"
// (never 100%), and an unrecognised overflow flag stays unresolved.
//
// Explosion reception, per-part LOS and ExDR do not exist in the snapshot at all;
// every part therefore starts with reception/exdr/los = 'unknown'.

export const PART_ROLE = Object.freeze({
  MAIN: 'main_health', // the unit's own pool; damage lands here directly
  ORDINARY: 'ordinary', // local pool plus transfer to main
  DETACHED: 'detached', // no main multiplier (e.g. a floating weapon); local damage only
});

const finite = x => typeof x === 'number' && Number.isFinite(x);

function parsePercent(text) {
  if (typeof text === 'number' && finite(text)) return { status: 'KNOWN', fraction: text };
  if (typeof text !== 'string') return { status: 'UNKNOWN', reason: 'PERCENT_TEXT_MISSING' };
  const trimmed = text.trim();
  if (trimmed === '-') return { status: 'NOT_APPLICABLE' };
  const match = /^(-?\d+(?:\.\d+)?)\s*%$/.exec(trimmed);
  if (!match) return { status: 'UNKNOWN', reason: 'PERCENT_TEXT_UNPARSED', text: trimmed };
  return { status: 'KNOWN', fraction: Number(match[1]) / 100 };
}

function parseHealth(value) {
  if (value === null || value === undefined) return { status: 'UNKNOWN', reason: 'NULL_IN_SNAPSHOT' };
  if (typeof value === 'number' && finite(value)) return value > 0 ? { status: 'KNOWN', value } : { status: 'UNKNOWN', reason: 'NON_POSITIVE_NUMBER' };
  if (typeof value === 'string') {
    const numeric = /^\s*(\d+(?:\.\d+)?)\s*$/.exec(value);
    if (numeric) return Number(numeric[1]) > 0 ? { status: 'KNOWN', value: Number(numeric[1]) } : { status: 'UNKNOWN', reason: 'NON_POSITIVE_NUMBER' };
    return { status: 'UNKNOWN', reason: 'PROSE_OR_CONDITIONAL_HEALTH', text: value };
  }
  return { status: 'UNKNOWN', reason: 'HEALTH_TYPE_UNEXPECTED' };
}

export function normalizePartRow(row, index = 0) {
  if (!row || typeof row !== 'object' || typeof row.part_id !== 'string' || !row.part_id) {
    return { status: 'INVALID_PART_ROW', reason: 'PART_ID_MISSING', index };
  }
  const mainMultiplier = parsePercent(row.percent_to_main);
  const role = row.part_id === 'main'
    ? PART_ROLE.MAIN
    : mainMultiplier.status === 'NOT_APPLICABLE' ? PART_ROLE.DETACHED : PART_ROLE.ORDINARY;
  const overflowRaw = typeof row.overflow_cap === 'string' ? row.overflow_cap.trim() : row.overflow_cap;
  const overflow = overflowRaw === 'Yes' ? 'yes'
    : overflowRaw === 'No' ? 'no'
      : overflowRaw === '-' ? 'not_applicable'
        : 'unknown';
  const count = Number.isInteger(row.count) && row.count > 0 ? row.count : null;
  return {
    status: 'KNOWN',
    part_id: row.part_id,
    name_zh: row.name || null,
    index,
    role,
    health: parseHealth(row.health),
    // English tier label from the snapshot (the zh label is kept alongside it); the
    // numeric value of this label is NOT known here and comes from an injected model.
    armor_text: typeof row.armor_level === 'string' ? row.armor_level : null,
    armor_level_zh: typeof row.armor_level_zh === 'string' ? row.armor_level_zh : null,
    location_text: typeof row.location === 'string' ? row.location : null,
    durable: parsePercent(row.durable),
    percent_to_main: mainMultiplier,
    overflow_cap: { raw: overflowRaw === undefined ? null : overflowRaw, state: overflow },
    fatal: typeof row.fatal === 'boolean' ? row.fatal : { status: 'UNKNOWN' },
    count: { value: count === null ? 1 : count, state: count === null ? 'null_treated_as_single' : 'declared' },
    // Absent from the snapshot by construction.
    reception: 'unknown',
    los: 'unknown',
    exdr: 'unknown',
    constitution_text: row.constitution ?? null,
  };
}

// Turn one enemy entry of HD2_Wiki/data/wiki/zh/enemies.json into a target.
export function normalizeTarget(enemyEntry) {
  if (!enemyEntry || typeof enemyEntry !== 'object' || typeof enemyEntry.id !== 'string') {
    return { status: 'INVALID_TARGET', reason: 'TARGET_ID_MISSING' };
  }
  const rows = Array.isArray(enemyEntry.body_parts) ? enemyEntry.body_parts : [];
  if (!rows.length) {
    return {
      status: 'KNOWN',
      id: enemyEntry.id,
      name_zh: enemyEntry.name_zh || null,
      parts: [],
      main: null,
      no_part_panel: true,
      note: 'no part panel: only the fallback / default-zone single application is available',
    };
  }
  const parts = [];
  const invalid = [];
  rows.forEach((row, index) => {
    const part = normalizePartRow(row, index);
    if (part.status === 'KNOWN') parts.push(part);
    else invalid.push(part);
  });
  const main = parts.find(part => part.role === PART_ROLE.MAIN) || null;
  return {
    status: 'KNOWN',
    id: enemyEntry.id,
    name_zh: enemyEntry.name_zh || null,
    health_total_text: enemyEntry.health_total ?? null,
    parts,
    main,
    no_part_panel: false,
    invalid_rows: invalid,
  };
}

export function findPart(target, partId, instance = 0) {
  if (!target || !Array.isArray(target.parts)) return null;
  const candidates = target.parts.filter(part => part.part_id === partId);
  if (!candidates.length) return null;
  const part = candidates[0];
  const declared = part.count.value;
  if (!Number.isInteger(instance) || instance < 0 || instance >= declared) {
    return { invalid_instance: true, part, declared_instances: declared };
  }
  return { part, instance, declared_instances: declared };
}
