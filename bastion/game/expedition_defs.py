from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "expeditions.json"


@dataclass(frozen=True)
class DungeonDefinition:
    width: int
    height: int
    main_rooms: int
    side_rooms: int
    room_min_size: int
    room_max_size: int
    hallway_half_width: int


@dataclass(frozen=True)
class EncounterDefinition:
    room_type: str
    enemies: tuple[str, ...]


@dataclass(frozen=True)
class LootRoomDefinition:
    gold_min: int
    gold_max: int
    item_rolls: int


@dataclass(frozen=True)
class BossAbilityDefinition:
    ability_id: str
    kind: str
    name: str
    values: dict[str, Any]


@dataclass(frozen=True)
class BossDefinition:
    boss_id: str
    enemy_kind: str
    name: str
    element: str
    weight: float
    abilities: tuple[BossAbilityDefinition, ...]


@dataclass(frozen=True)
class ExpeditionRewardsDefinition:
    enemy_xp_multiplier: float
    enemy_gold_multiplier: float
    enemy_item_drop_multiplier: float
    boss_gold: int
    completion_gold: int
    completion_xp: int


@dataclass(frozen=True)
class ExpeditionDefinition:
    expedition_id: str
    name: str
    max_party_size: int
    seed_salt: int
    enemy_stat_budget_multiplier: float
    dungeon: DungeonDefinition
    normal_encounters: tuple[EncounterDefinition, ...]
    loot_rooms: LootRoomDefinition
    boss_wave_counts: tuple[int, ...]
    bosses: tuple[BossDefinition, ...]
    guaranteed_boss_items: int
    rewards: ExpeditionRewardsDefinition


def load_expedition_definitions(path: Path | None = None) -> dict[str, ExpeditionDefinition]:
    path = DATA_PATH if path is None else path
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    records = raw.get("expeditions", raw) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError("Expedition data must be a list or an object with an 'expeditions' list.")

    definitions: dict[str, ExpeditionDefinition] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Every expedition definition must be an object.")
        expedition_id = str(record.get("id", "")).strip()
        if not expedition_id:
            raise ValueError("Expedition definitions require a non-empty 'id'.")

        dungeon = _dungeon_definition(record.get("dungeon", {}))
        encounters = tuple(_encounter_definition(entry) for entry in record.get("normal_encounters", ()))
        bosses = tuple(_boss_definition(entry) for entry in record.get("bosses", ()))
        if not bosses:
            raise ValueError(f"Expedition '{expedition_id}' requires at least one boss.")

        rewards_data = record.get("rewards", {})
        if not isinstance(rewards_data, dict):
            rewards_data = {}

        definition = ExpeditionDefinition(
            expedition_id=expedition_id,
            name=str(record.get("name", expedition_id.replace("_", " ").title())),
            max_party_size=max(1, int(record.get("max_party_size", 5))),
            seed_salt=int(record.get("seed_salt", 0)),
            enemy_stat_budget_multiplier=max(0.25, float(record.get("enemy_stat_budget_multiplier", 1.0))),
            dungeon=dungeon,
            normal_encounters=encounters,
            loot_rooms=_loot_room_definition(record.get("loot_rooms", {})),
            boss_wave_counts=tuple(max(1, int(value)) for value in record.get("boss_wave_counts", (5, 6, 7))),
            bosses=bosses,
            guaranteed_boss_items=max(0, int(record.get("guaranteed_boss_items", 5))),
            rewards=ExpeditionRewardsDefinition(
                enemy_xp_multiplier=float(rewards_data.get("enemy_xp_multiplier", 1.0)),
                enemy_gold_multiplier=max(0.0, float(rewards_data.get("enemy_gold_multiplier", 1.0))),
                enemy_item_drop_multiplier=max(0.0, float(rewards_data.get("enemy_item_drop_multiplier", 0.35))),
                boss_gold=max(0, int(rewards_data.get("boss_gold", 0))),
                completion_gold=max(0, int(rewards_data.get("completion_gold", 0))),
                completion_xp=max(0, int(rewards_data.get("completion_xp", 0))),
            ),
        )
        definitions[definition.expedition_id] = definition

    if not definitions:
        raise ValueError("Expedition data must contain at least one expedition definition.")
    return definitions


def default_expedition_definition() -> ExpeditionDefinition:
    return next(iter(EXPEDITION_DEFINITIONS.values()))


def _dungeon_definition(raw: object) -> DungeonDefinition:
    data = raw if isinstance(raw, dict) else {}
    room_min = max(5, int(data.get("room_min_size", 7)))
    room_max = max(room_min, int(data.get("room_max_size", 13)))
    return DungeonDefinition(
        width=max(36, int(data.get("width", 84))),
        height=max(28, int(data.get("height", 64))),
        main_rooms=max(3, int(data.get("main_rooms", 8))),
        side_rooms=max(0, int(data.get("side_rooms", 4))),
        room_min_size=room_min,
        room_max_size=room_max,
        hallway_half_width=max(0, int(data.get("hallway_half_width", 1))),
    )


def _encounter_definition(raw: object) -> EncounterDefinition:
    data = raw if isinstance(raw, dict) else {}
    enemies = tuple(str(enemy_id) for enemy_id in data.get("enemies", ()) if str(enemy_id))
    return EncounterDefinition(str(data.get("room_type", "combat")), enemies)


def _loot_room_definition(raw: object) -> LootRoomDefinition:
    data = raw if isinstance(raw, dict) else {}
    gold_min = max(0, int(data.get("gold_min", 8)))
    gold_max = max(gold_min, int(data.get("gold_max", 24)))
    return LootRoomDefinition(gold_min, gold_max, max(0, int(data.get("item_rolls", 1))))


def _boss_definition(raw: object) -> BossDefinition:
    data = raw if isinstance(raw, dict) else {}
    boss_id = str(data.get("id", "boss")).strip() or "boss"
    abilities = tuple(_boss_ability_definition(entry) for entry in data.get("abilities", ()))
    return BossDefinition(
        boss_id=boss_id,
        enemy_kind=str(data.get("enemy_kind", boss_id)),
        name=str(data.get("name", boss_id.replace("_", " ").title())),
        element=str(data.get("element", "physical")),
        weight=max(0.0, float(data.get("weight", 1.0))),
        abilities=abilities,
    )


def _boss_ability_definition(raw: object) -> BossAbilityDefinition:
    data = dict(raw) if isinstance(raw, dict) else {}
    ability_id = str(data.pop("id", data.get("kind", "boss_ability")))
    kind = str(data.pop("kind", "telegraphed_strike"))
    name = str(data.pop("name", ability_id.replace("_", " ").title()))
    return BossAbilityDefinition(ability_id, kind, name, data)


EXPEDITION_DEFINITIONS = load_expedition_definitions()
