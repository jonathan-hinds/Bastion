from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bastion.game.elements import normalize_resistances


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "enemies.json"

STAT_DEFAULTS: dict[str, float] = {
    "health": 25.0,
    "speed": 70.0,
    "radius": 9.0,
    "reward": 5.0,
    "damage": 4.0,
    "accel": 500.0,
    "mass": 1.0,
    "attack_range": 32.0,
    "fire_rate": 0.9,
    "projectile_speed": 260.0,
}


def load_enemy_definitions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = DATA_PATH if path is None else path
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    records = raw.get("enemies", raw) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError("Enemy data must be a list or an object with an 'enemies' list.")

    definitions: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Every enemy definition must be an object.")
        enemy_id = str(record.get("id", "")).strip()
        if not enemy_id:
            raise ValueError("Enemy definitions require a non-empty 'id'.")

        stats = record.get("stats", {})
        if not isinstance(stats, dict):
            raise ValueError(f"Enemy '{enemy_id}' has invalid stats.")

        data: dict[str, Any] = {
            "id": enemy_id,
            "name": str(record.get("name", enemy_id.title())),
            "faction_type": str(record.get("type", record.get("faction_type", "goblin"))),
            "combat_role": str(record.get("combat_role", "melee")),
            "shape": str(record.get("shape", enemy_id)),
            "tags": [str(tag) for tag in record.get("tags", []) if str(tag)],
            "resistances": normalize_resistances(record.get("resistances", {})),
            "loot": dict(record.get("loot", {})) if isinstance(record.get("loot", {}), dict) else {},
        }
        for key, default in STAT_DEFAULTS.items():
            try:
                data[key] = float(stats.get(key, record.get(key, default)))
            except (TypeError, ValueError):
                data[key] = float(default)

        if data["combat_role"] not in ("melee", "ranged"):
            data["combat_role"] = "melee"
        definitions[enemy_id] = data

    if not definitions:
        raise ValueError("Enemy data must contain at least one enemy definition.")
    return definitions


ENEMY_DATA = load_enemy_definitions()


def get_enemy_def(kind: str) -> dict[str, Any]:
    try:
        return ENEMY_DATA[kind]
    except KeyError as exc:
        known = ", ".join(sorted(ENEMY_DATA))
        raise KeyError(f"Unknown enemy kind '{kind}'. Known kinds: {known}") from exc


def enemy_ids_by_role(role: str, *, include_bosses: bool = False) -> list[str]:
    return [
        enemy_id
        for enemy_id, data in ENEMY_DATA.items()
        if data.get("combat_role") == role and (include_bosses or "boss" not in data.get("tags", ()))
    ]


def enemy_ids_with_tag(tag: str) -> list[str]:
    return [enemy_id for enemy_id, data in ENEMY_DATA.items() if tag in data.get("tags", ())]
