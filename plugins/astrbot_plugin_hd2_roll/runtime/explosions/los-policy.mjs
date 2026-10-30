// Per-target explosion line-of-sight VERIFICATION policy.
//
// The coordinator opened https://helldiversotl.com/research/explosion-line-of-sight
// directly on 2026-10-24 (page states Written / Code verified 7.1.0, Sep 30) and relayed:
//   All           — inside and outside the inner radius require LOS verification
//   Outer Radius  — inside the inner radius skips verification, outside requires it
//   None          — verification is skipped in both zones
// The same page lists controlled hit tests and collision filtering as still open, and
// gives per-target counts (Charger 12 All + 5 fallback, Impaler 18 All + 3 Outer +
// 2 fallback, Titan 26 All).
//
// Those counts do not match the row counts of our wiki snapshot panel (Charger 11 rows,
// Impaler 12, Bile Titan 12). The mismatch is preserved, not reconciled: the counts are a
// target-level source fact, the part-level mapping stays unresolved. This run did not
// fetch the page itself.

export const LOS_SOURCE = Object.freeze({
  id: 'otl_los',
  url: 'https://helldiversotl.com/research/explosion-line-of-sight',
  patch_claim: 'Written / Code verified 7.1.0, Sep 30',
  retrieved_via: 'coordinator direct page read on 2026-10-24; NOT fetched by this run',
  state: 'relayed_by_coordinator_after_direct_read',
  verification_modes: Object.freeze({
    all: 'inner and outer zone both require LOS verification',
    outer_radius: 'inner zone skips verification; outer zone requires it',
    none: 'verification skipped in both zones',
  }),
  open_items: Object.freeze(['collision filtering', 'controlled hit tests']),
});

// Target-level counts exactly as relayed. `panel_rows` comes from our snapshot.
export const TARGET_LOS_POLICY = Object.freeze({
  charger: Object.freeze({ all: 12, outer_radius: 0, fallback: 5, panel_rows: 11 }),
  impaler: Object.freeze({ all: 18, outer_radius: 3, fallback: 2, panel_rows: 12 }),
  bile_titan: Object.freeze({ all: 26, outer_radius: 0, fallback: 0, panel_rows: 12 }),
});

// Which verification modes the target-level policy rules out.
//   'all_parts_require_verification' — no Outer-Radius part exists, so no ordinary hit
//     may claim a skipped verification.
//   'mixed' — some parts may skip inside verification, but the part-level mapping is
//     unresolved, so a per-claim mode is accepted with a warning.
export function policyFor(targetId) {
  const entry = TARGET_LOS_POLICY[targetId];
  if (!entry) {
    return { status: 'UNKNOWN', reasons: ['NO_TARGET_LOS_POLICY'], source_refs: [LOS_SOURCE.id] };
  }
  const listed = entry.all + entry.outer_radius + entry.fallback;
  const restriction = entry.outer_radius === 0 ? 'all_parts_require_verification' : 'mixed';
  return {
    status: 'KNOWN',
    target_id: targetId,
    counts: { all: entry.all, outer_radius: entry.outer_radius, fallback: entry.fallback },
    restriction,
    part_level_mapping: entry.outer_radius === 0 && entry.fallback === 0 ? 'all_parts_all_mode' : 'unresolved_part_level',
    panel_mismatch: {
      source_entries: listed,
      panel_rows: entry.panel_rows,
      state: listed === entry.panel_rows ? 'consistent' : 'unresolved_count_mismatch',
      note: '来源按目标统计（含实例/子部位），本包面板是行级部位；未逐项对应。',
    },
    source_refs: [LOS_SOURCE.id],
    patch_claim: LOS_SOURCE.patch_claim,
  };
}

// Enforcement used by the event resolver: returns a rejection reason when a claim's
// declared verification mode contradicts the target-level source policy.
export function modeContradiction({ targetId, verification_mode }) {
  const policy = policyFor(targetId);
  if (policy.status !== 'KNOWN') return null;
  if (policy.restriction === 'all_parts_require_verification' && verification_mode === 'none') return 'VERIFICATION_MODE_CONTRADICTS_SOURCE_POLICY';
  return null;
}
