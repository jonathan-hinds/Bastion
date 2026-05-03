# Enemy Data Editor

`enemy_data_editor.py` is a small Tkinter tool for editing the game's enemy definitions in `bastion/data/enemies.json`.

Run it from the project root:

```powershell
python enemy_data_editor.py
```

Validate the data without opening the UI:

```powershell
python enemy_data_editor.py --check
```

Enemy fields:

- `id`: stable key used by waves and saves. Keep existing IDs like `small`, `medium`, `large`, and `ranged` if you want the current wave logic to preserve its behavior.
- `type`: faction/type such as `goblin`, `undead`, or `human`. Holy damage is doubled against `undead`.
- `combat_role`: `melee` enemies walk into attack range and use the shared melee combat system. `ranged` enemies look for structures and fire projectiles.
- `shape`: simple renderer hint for the current placeholder art.
- `stats`: base combat and movement values before wave scaling.
- `resistances`: damage multipliers per element. `1.0` is normal damage, `0.5` is half damage, and `1.5` is extra damage.

Use the editor controls like this:

- `Create Enemy`: adds a new editable enemy draft with a unique ID.
- `Duplicate`: copies the selected enemy into a new editable draft.
- `Apply Edit`: updates the list from the form without writing the file yet.
- `Save JSON`: applies the current form and writes `bastion/data/enemies.json`.
- `Delete`: removes the selected enemy from the list. Press `Save JSON` afterward to persist it.
- `Reload`: discards unsaved in-memory changes and reloads the file.

Restart the game after changing enemy data so the loader picks up the new definitions.
