// A declared, single-role pressure scenario. No equipment score is converted into
// kills: the caller supplies effective throughput, ammunition and operating time.
// The model is a fluid estimate, not a game simulation or a validated pass gate.
const finite = n => typeof n === 'number' && Number.isFinite(n);

export function assessCrowdPressure({present, arrivalsPerSecond, effectiveKillsPerSecond, availableKills, safeWindowSeconds, immediateKills = 0} = {}) {
  const fields = {present, arrivalsPerSecond, effectiveKillsPerSecond, availableKills, safeWindowSeconds, immediateKills};
  for (const [key,value] of Object.entries(fields)) {
    if (!finite(value) || value < 0) return {status:'UNKNOWN', reason:'MISSING_OR_INVALID_INPUT', field:key};
  }
  if (safeWindowSeconds === 0) return {status:'UNKNOWN',reason:'NO_SAFE_WINDOW'};
  // Burst is already delivered in the stated initial scenario, and must be
  // disjoint from the separately declared sustained ammunition inventory.
  const burst = Math.min(present, immediateKills);
  const remaining = present - burst;
  if (remaining === 0) return {status:'CONTROLLED', reason:'INITIAL_BURST_CLEARS', clearSeconds:0, remaining, burst};
  const net = effectiveKillsPerSecond - arrivalsPerSecond;
  if (net <= 0) return {status:'OVERWHELMED',reason:'NO_NET_CLEARANCE', clearSeconds:null, remaining, burst, netKillsPerSecond:net};
  const clearSeconds = remaining / net;
  // Sustained weapon must also handle arrivals before the initial group is cleared.
  const requiredKills = effectiveKillsPerSecond * clearSeconds;
  if (requiredKills > availableKills + 1e-9) return {status:'OVERWHELMED',reason:'AMMUNITION_EXHAUSTED',clearSeconds,remaining,burst,requiredKills,netKillsPerSecond:net};
  if (clearSeconds > safeWindowSeconds) return {status:'OVERWHELMED',reason:'SAFE_WINDOW_EXCEEDED',clearSeconds,remaining,burst,requiredKills,netKillsPerSecond:net};
  return {status:'CONTROLLED',reason:'CLEARS_WITHIN_WINDOW',clearSeconds,remaining,burst,requiredKills,netKillsPerSecond:net};
}
