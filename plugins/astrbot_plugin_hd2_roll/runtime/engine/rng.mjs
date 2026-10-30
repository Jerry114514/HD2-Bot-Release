export function uniformInt(n, next = cryptoUint32) {
  if (!Number.isSafeInteger(n) || n < 1 || n > 0x100000000) throw new RangeError('uint32 range required');
  const limit = 0x100000000 - 0x100000000 % n;
  let x;
  do { x = next(); if (!Number.isInteger(x) || x < 0 || x >= 0x100000000) throw new RangeError('invalid random word'); } while (x >= limit);
  return x % n;
}
export function cryptoUint32() { return crypto.getRandomValues(new Uint32Array(1))[0]; }
// Explicitly test-only: callers persist this state with a paused request.
export function seeded(seed = 1) {
  let state = seed >>> 0;
  const next = () => { state = (state + 0x6D2B79F5) >>> 0; let x = state; x = Math.imul(x ^ x >>> 15, x | 1); x ^= x + Math.imul(x ^ x >>> 7, x | 61); return (x ^ x >>> 14) >>> 0; };
  next.snapshot = () => state;
  next.restore = value => { if (!Number.isInteger(value)) throw new TypeError('invalid RNG state'); state = value >>> 0; };
  return next;
}
export function sample(items, count, next) {
  if (count > items.length) throw new RangeError('pool too small');
  const copy = [...items];
  for (let i = 0; i < count; i++) { const j = i + uniformInt(copy.length - i, next); [copy[i], copy[j]] = [copy[j], copy[i]]; }
  return copy.slice(0, count);
}
export function taskRng(task, next) {
  if (next) {
    if (task.rng_kind === 'seeded') {
      if (!next.restore || !next.snapshot) throw Error('RNG_STATE_REQUIRED');
      next.restore(task.rng_state);
    }
    if (!task.rng_kind) task.rng_kind = next.snapshot && next.restore ? 'seeded' : 'external';
    return {next, save() { task.rng_state = next.snapshot ? next.snapshot() : null; }};
  }
  if (task.rng_kind && task.rng_kind !== 'crypto') throw Error('RNG_STATE_REQUIRED');
  task.rng_kind = 'crypto'; task.rng_state ||= {words:[],offset:0};
  const state=task.rng_state;
  return {next() { if(state.offset===state.words.length){state.words=[...crypto.getRandomValues(new Uint32Array(64))];state.offset=0;}return state.words[state.offset++]; },save(){}};
}
