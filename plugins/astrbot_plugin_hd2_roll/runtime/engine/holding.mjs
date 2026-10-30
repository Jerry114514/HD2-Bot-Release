import {equipmentIDs, canOccupy} from './common.mjs';

// Constrained constructor for initial holdings: every legal choice is derived from the
// actual loadout and catalog capabilities, so no case can imply a mandatory card and no
// grenade can land in a backpack slot. The empty holding stays a legal choice.
export function holdingOptions(loadout, catalog) {
  const byID = new Map(catalog.equipment.map(e => [e.id, e]));
  const weapons = equipmentIDs(loadout).filter(id => canOccupy(byID.get(id), 'weapon'));
  const backpacks = equipmentIDs(loadout).filter(id => canOccupy(byID.get(id), 'backpack'));
  const options = [];
  for (const weapon of [undefined, ...weapons]) for (const backpack of [undefined, ...backpacks]) {
    const slots = {};
    if (weapon !== undefined) slots.weapon = weapon;
    if (backpack !== undefined) slots.backpack = backpack;
    options.push(slots);
  }
  return options;
}
