// Rescale final contributions, after all physical/CD/class calculations. The
// low branch is linear so old component sums of 65 become exactly 60. Preserve
// zero and the upper-end 100/105 landmarks instead of lowering the ceiling.
export const REVIEW_SCALE={id:'review-65-as-60',previous_pass:65,display_pass:60,
  upper_anchor:100,ceiling:105,basis:'USER_DISPLAY_RESCALE; PHYSICAL_REQUIREMENTS_UNCHANGED'};
export function rescaleScore(value,scale) {
  if(!Number.isFinite(value))return null;
  if(scale?.id!==REVIEW_SCALE.id)return value;
  return value<=65?value*60/65:value<100?60+(value-65)*40/35:value;
}
export function originalScore(value,scale) {
  if(!Number.isFinite(value))return null;
  if(scale?.id!==REVIEW_SCALE.id)return value;
  return value<=60?value*65/60:value<100?65+(value-60)*35/40:value;
}
export const contributionScore=(value,origin,scale)=>['MANUAL_CALIBRATED','CURRENT_DISPLAY_SCALE'].includes(origin)?value:rescaleScore(value,scale);
