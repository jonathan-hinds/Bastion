from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import random
from typing import Any


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "round_events.json"


@dataclass(frozen=True)
class RoundEventDefinition:
    id: str
    title: str
    description: str
    omens: tuple[str, ...]
    effect_type: str
    params: dict[str, Any]
    repeatable: bool = True


@dataclass(frozen=True)
class RoundEventChoice:
    definition: RoundEventDefinition
    omen: str

    @property
    def id(self) -> str:
        return self.definition.id


class RoundEventEffect:
    effect_type = ""

    def apply(self, game, params: dict[str, Any]) -> str:
        raise NotImplementedError


class ScaleFutureWaveEnemiesEffect(RoundEventEffect):
    effect_type = "scale_future_wave_enemies"

    def apply(self, game, params: dict[str, Any]) -> str:
        multiplier = max(0.1, float(params.get("multiplier", 1.0)))
        game.wave_manager.enemy_count_multiplier *= multiplier
        return f"Future nights x{game.wave_manager.enemy_count_multiplier:g} enemies"


class AddRandomCoreEffect(RoundEventEffect):
    effect_type = "add_random_core"

    def apply(self, game, params: dict[str, Any]) -> str:
        health = float(params.get("health", game.core_target.max_health))
        core = game.add_random_core(health)
        if core is None:
            return "No open location for another core"
        return "New core online"


class LoseGoldFractionEffect(RoundEventEffect):
    effect_type = "lose_gold_fraction"

    def apply(self, game, params: dict[str, Any]) -> str:
        fraction = max(0.0, min(1.0, float(params.get("fraction", 0.5))))
        lost = int(game.gold * fraction)
        game.gold = max(0, game.gold - lost)
        return f"Lost {lost} gold"


class DestroyWallFractionEffect(RoundEventEffect):
    effect_type = "destroy_wall_fraction"

    def apply(self, game, params: dict[str, Any]) -> str:
        fraction = max(0.0, min(1.0, float(params.get("fraction", 0.5))))
        destroyed = game.destroy_random_walls(fraction)
        return f"{destroyed} walls destroyed"


class SkipNextWaveKillTroopsEffect(RoundEventEffect):
    effect_type = "skip_next_wave_kill_troops"

    def apply(self, game, params: dict[str, Any]) -> str:
        defeated = game.kill_all_troops()
        bonus = game.wave_manager.skip_next_wave(game)
        return f"Skipped night +{bonus}, {defeated} troops lost"


EFFECTS: dict[str, RoundEventEffect] = {
    effect.effect_type: effect
    for effect in (
        ScaleFutureWaveEnemiesEffect(),
        AddRandomCoreEffect(),
        LoseGoldFractionEffect(),
        DestroyWallFractionEffect(),
        SkipNextWaveKillTroopsEffect(),
    )
}
EFFECTS["scale_future_night_enemies"] = EFFECTS["scale_future_wave_enemies"]
EFFECTS["skip_next_night_kill_troops"] = EFFECTS["skip_next_wave_kill_troops"]


def load_round_event_data(path: Path | None = None) -> tuple[dict[str, Any], list[RoundEventDefinition]]:
    path = DATA_PATH if path is None else path
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Round event data must be a JSON object.")
    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}
    records = raw.get("events", [])
    if not isinstance(records, list):
        raise ValueError("Round event data requires an 'events' list.")

    definitions: list[RoundEventDefinition] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Every round event must be an object.")
        event_id = str(record.get("id", "")).strip()
        if not event_id:
            raise ValueError("Round events require a non-empty id.")
        effect = record.get("effect", {})
        if not isinstance(effect, dict):
            raise ValueError(f"Round event '{event_id}' has invalid effect data.")
        effect_type = str(effect.get("type", "")).strip()
        if effect_type not in EFFECTS:
            raise ValueError(f"Round event '{event_id}' uses unknown effect '{effect_type}'.")
        params = {key: value for key, value in effect.items() if key != "type"}
        omens = tuple(str(omen) for omen in record.get("omens", []) if str(omen).strip())
        if not omens:
            omens = ("A nameless door waits.",)
        definitions.append(
            RoundEventDefinition(
                id=event_id,
                title=str(record.get("title", event_id.replace("_", " ").title())),
                description=str(record.get("description", "")),
                omens=omens,
                effect_type=effect_type,
                params=params,
                repeatable=bool(record.get("repeatable", True)),
            )
        )
    return settings, definitions


class RoundEventManager:
    def __init__(self, path: Path | None = None) -> None:
        settings, definitions = load_round_event_data(path)
        chance = settings.get("chance_between_nights", settings.get("chance_between_waves", 0.0))
        self.chance_between_waves = max(0.0, min(1.0, float(chance)))
        self.choice_count = max(1, int(settings.get("choices", 3)))
        self.definitions = definitions
        self.current_choices: list[RoundEventChoice | RoundEventDefinition] = []
        self.applied_event_ids: set[str] = set()

    @property
    def awaiting_choice(self) -> bool:
        return bool(self.current_choices)

    def maybe_offer(self, game) -> bool:
        if self.awaiting_choice or not self.definitions:
            return False
        if random.random() > self.chance_between_waves:
            return False
        pool = [event for event in self.definitions if event.repeatable or event.id not in self.applied_event_ids]
        if not pool:
            return False
        random.shuffle(pool)
        self.current_choices = [
            RoundEventChoice(event, random.choice(event.omens))
            for event in pool[: min(self.choice_count, len(pool))]
        ]
        game.paused = False
        game.message("CHOOSE AN OMEN")
        return True

    def choose(self, event_id: str, game) -> bool:
        choice = next((choice for choice in self.current_choices if choice.id == event_id), None)
        if choice is None:
            return False
        event = choice.definition if isinstance(choice, RoundEventChoice) else choice
        self.current_choices = []
        self.applied_event_ids.add(event.id)
        result = EFFECTS[event.effect_type].apply(game, event.params)
        game.message(result.upper())
        return True
