# Published loadout runtime

Source: https://github.com/Leeroysunfury/Jerry114514.github.io/tree/476389fb46e284fd2cb9b9359a5d5d80f628beb6/shared/hd2-loadout

The catalog, assessment model and 55 transitive runtime modules/data files are pinned together. `snapshot.json` records their SHA256 digests. Source bytes are preserved, including line endings; the bridge verifies every entry before returning a result. The bot does not maintain a separate scoring implementation. Initial balanced draws use bounded seven-slot candidate repair, separate routine/additive bottleneck ranking, and complete six-dimension/physical admission checks; ranking alone never admits a result.

Initial pure and balanced draws now share one 20% admission decision for all mechs and tanks. A successful admission only makes them eligible; internal retries and local repair retain the same decision. Recon and supply vehicles remain in the normal pool, and the single-mech limit still applies. No scores or admission details are exposed by the bridge.

Endurance computes each gun role with 50% original ability proportions plus 50% normalized ability-times-endurance proportions, then combines chaff/close-defense/medium means at 50%/35%/15%. Low-endurance guns retain a nonzero role share; unknown data never becomes a zero-weight shortcut. Grenades add each role's displayed contribution multiplied by grenade endurance / 100 without entering gun denominators. Supply packs/vehicles retain the final floor of 100. Final endurance must reach 55; the other five scores still require 60, with unchanged physical gates. Local repair reads the actual thresholds as well. Individual equipment scores and the published manual calibration are unchanged.

Regenerate from an existing clone without changing its checkout:

```powershell
python scripts/update_loadout_runtime.py --source-repo <site-clone> --ref <commit>
```

Update all files as one deployment and reload the plugins. The bot's process boundary returns equipment IDs only. It exposes whole-loadout assessment/pure draws; replacement, locking, manual equipment selection and explanation interfaces are not enabled. Shared upstream modules retain their original exports for reproducible updates.
