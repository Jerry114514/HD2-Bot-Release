# Published loadout runtime

Source: https://github.com/Jerry114514/Jerry114514.github.io/tree/c79b56067783a9b8d7257b3c2f205003fa2188d7/shared/hd2-loadout

The catalog, assessment model and 54 transitive runtime modules/data files are pinned together. `snapshot.json` records their SHA256 digests. Source bytes are preserved, including line endings; the bridge verifies every entry before returning a result. The bot does not maintain a separate scoring implementation.

Regenerate from an existing clone without changing its checkout:

```powershell
python scripts/update_loadout_runtime.py --source-repo <site-clone> --ref <commit>
```

Update all files as one deployment and reload the plugins. The bot's process boundary returns equipment IDs only. It exposes whole-loadout assessment/pure draws; replacement, locking, manual equipment selection and explanation interfaces are not enabled. Shared upstream modules retain their original exports for reproducible updates.
