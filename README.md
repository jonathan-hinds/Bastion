# Bastion of the Core

Freeform endless tower defense prototype based on `Game.txt`.

## Run

```powershell
python -m pip install -r requirements.txt
python run_game.py
```

## Package For Windows

Build a shareable Windows folder with `BastionOfTheCore.exe`:

```powershell
powershell -ExecutionPolicy Bypass -File .\package_windows.ps1
```

The script creates:

- `dist\BastionOfTheCore\BastionOfTheCore.exe`
- `dist\BastionOfTheCore-windows.zip`

Send the zip to friends. They should unzip it and run `BastionOfTheCore.exe` from inside the unzipped folder. Keep the `_internal` folder next to the exe; it contains the bundled runtime, data files, and sounds.

## Controls

- Left click the world to build or select.
- Drag left mouse over troops to box-select a group.
- Escape: open the pause menu.
- Backspace / Delete: cancel build or selection.
- Right click with troop(s) selected: set station point.
- Select troop(s), then use HOLD FIRE / ENGAGE in the side panel to toggle whether they attack.
- WASD / arrows: pan the large map.
- Mouse wheel: zoom.
- Space / P: pause or unpause.
- 1/2/3/4/5/6/7/8/9: choose Wall/Archer/Cannon/Wizard/Barracks/House/Research/Library/Core. Open Build for Extractor, Torch, Training Grounds, Shield Generator, and the full palette.
- Click an occupied inventory slot on the bottom grid to use that item. Hover a slot to inspect it.

The current build is intentionally shape-based and monochrome. It includes day/night survival cycles, freeform walls, flow-field pathfinding, tower XP, specialization buttons, houses for troop capacity, torch aggro beacons, passive training grounds, melee barracks troop training, Warrior taunt abilities, resource harvesting, repeatable research, fog-of-war exploration, respawning ambient enemy camps, day timing, speed controls, inventory scrolls, hit reactions, projectile trails, and lightweight contextual panels.

Tower hits use a shared visual language: single-target impacts get a tight cross-ring, multi-target attacks like Scatter get split impact rings, and AoE attacks draw expanding area rings. Default Wizard, Ice Wizard, Cannon, and Mortar attacks use AoE radius damage; Lightning Wizard remains a chain/multi-target behavior instead of radius damage.

Sound effects live in `Sounds/`. Archer, Cannon, and Mage/Wizard towers play their own fire sounds with subtle random pitch variation. Hovering interactable UI or selectable world objects plays the hover sound, clicking/selecting interactables plays the select sound, and towers ready to level up blink in sync with `LevelUpBlinking.wav`.
Music tracks are loaded from `Sounds/Songs/` at startup and rescanned while the game runs; add `.wav`, `.ogg`, or `.mp3` files there to mix them into the pause-menu player and background playlist.

## UI Feedback Rule

Anything that triggers `menu_hover` must visibly use the shared hover feedback: invert its black/white colors, slightly enlarge, and play the quick pulse started through `bastion.engine.hover_feedback`. New UI controls, hidden card buttons, panel-window controls, slots, and selectable world objects should register a stable hover target and draw with the same helper so the sound and visual response never drift apart.

Tower mods are installed from selected tower panels by spending that tower's XP. Towers bank XP over their level-up threshold instead of leveling automatically; ready towers blink until the player selects them and presses LEVEL UP.
Troops now bank XP the same way. Ready troops appear in the Level Up panel, can be focused from there, and spend each manual level on 2 attribute points across Stamina, Intellect, Strength, Agility, and Cunning.
New tower mods are data-driven in `bastion/data/tower_mods.json`: Noisy emits passive threat inside tower range, Air Support prioritizes ranged enemies, and Cover Fire prevents nearby troops from creating incidental aggro through damage, healing, or repairs.

## Resources

Gold remains the general build and training currency. Minerals are an additional currency required for towers on top of their gold cost. Grunts are workers now: station them near mineral or gold deposits and they gather up to 5 resources, haul them to the nearest core, then return to continue mining. Extractors can be built directly over either deposit type to claim the site and draw the arcane core-to-resource route grunts will work from. Mineral deposits pay out minerals, gold deposits pay out gold. Depleted unclaimed deposits respawn elsewhere after a few minutes; claimed extractor deposits recharge in place.

## Research

Build a Research structure to run one timed upgrade at a time. Each completed rank grants another 10% bonus and can be repeated forever; gold and mineral costs rise by rank, and research times rise by rank. Research cards can be toggled to auto-repeat, spending the next rank's resources and starting again whenever an idle lab can afford it. The Research Time upgrade reduces future research durations.

The Scroll Production Time research reduces Library scribing duration. Libraries spend gold to produce a random scroll over time; completed scrolls enter the inventory and trigger a bottom loot banner.

## Data

- Enemy definitions live in `bastion/data/enemies.json` and can be edited with `python enemy_data_editor.py`.
- Fog vision profiles live in `bastion/data/fog.json`.
- Ambient enemy camp templates live in `bastion/data/ambient_mobs.json`.
- Item and scroll definitions live in `bastion/data/items.json`.
- Tower mod definitions live in `bastion/data/tower_mods.json`.
- Between-night omen events live in `bastion/data/round_events.json`. New events can be added by creating JSON entries that reference one of the registered effect types in `bastion/game/round_events.py`.
