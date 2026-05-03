from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TowerModDefinition:
    id: str
    name: str
    description: str
    xp_cost: int
    effects: dict[str, object]


def load_tower_mods() -> dict[str, TowerModDefinition]:
    path = Path(__file__).resolve().parents[1] / "data" / "tower_mods.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    mods: dict[str, TowerModDefinition] = {}
    for entry in raw:
        definition = TowerModDefinition(
            id=str(entry["id"]),
            name=str(entry["name"]),
            description=str(entry.get("description", "")),
            xp_cost=int(entry.get("xp_cost", 0)),
            effects=dict(entry.get("effects", {})),
        )
        mods[definition.id] = definition
    return mods


TOWER_MODS = load_tower_mods()
