# Published loadout runtime

Source: local site commit `0dd7ab5cf0c9da4c3f609adc9b4b0eb3ba11b450` (not pushed; waiting for this iteration to be finalized).

The catalog, assessment model and 55 transitive runtime modules/data files are pinned together. `snapshot.json` records their SHA256 digests. Source bytes are preserved, including line endings; the bridge verifies every entry before returning a result. The bot does not maintain a separate scoring implementation. Initial balanced draws use bounded seven-slot candidate repair, separate routine/additive bottleneck ranking, and complete six-dimension/physical admission checks; ranking alone never admits a result.

Repair rewards only remaining score deficits in every dimension. Ablation loss, candidate sampling, routine/additive nominations and retained trials grant no credit above the actual pass thresholds. Physical failures remain separate checks rather than invented score deficits; already passing strong loadouts are still returned immediately.

Initial pure and balanced draws now share one 20% admission decision for all mechs and tanks. A successful admission only makes them eligible; internal retries and local repair retain the same decision. Recon and supply vehicles remain in the normal pool, and the single-mech limit still applies. No scores or admission details are exposed by the bridge.

Endurance weights each gun by (role ability + endurance) / sum(role ability + endurance), excluding guns whose ability in that role is zero. Unknown capability or endurance remains unknown. Chaff/close-defense/medium means still combine at 50%/35%/15%. Grenades add each role's displayed contribution multiplied by grenade endurance / 100 without entering gun denominators. Supply packs/vehicles retain the final floor of 100. Final endurance must reach 50; the other five scores still require 60, with unchanged physical gates. Local repair reads the actual thresholds as well. Individual equipment scores and the published manual calibration are unchanged.

Regenerate from an existing clone without changing its checkout:

```powershell
python scripts/update_loadout_runtime.py --source-repo <site-clone> --ref <commit>
```

Update all files as one deployment and reload the plugins. The bot's process boundary returns equipment IDs only. It exposes whole-loadout assessment/pure draws; replacement, locking, manual equipment selection and explanation interfaces are not enabled. Shared upstream modules retain their original exports for reproducible updates.
