from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchDefinition:
    id: str
    name: str
    code: str
    description: str
    base_gold: int
    base_minerals: int
    base_time: float
    cost_growth: float = 1.32
    mineral_growth: float = 1.28
    time_growth: float = 1.20
    increment: float = 0.10


@dataclass
class ResearchOrder:
    research_id: str
    remaining: float
    total: float


RESEARCH_DEFINITIONS: dict[str, ResearchDefinition] = {
    "archer_attack_speed": ResearchDefinition("archer_attack_speed", "Archer Attack Speed", "A.SPD", "Archer towers fire faster.", 24, 5, 16.0),
    "cannon_damage": ResearchDefinition("cannon_damage", "Cannon Damage", "C.DMG", "Cannon towers hit harder.", 30, 8, 20.0),
    "wizard_tower_range": ResearchDefinition("wizard_tower_range", "Wizard Tower Range", "W.RNG", "Wizard towers can target farther.", 30, 8, 21.0),
    "wizard_lightning_arc": ResearchDefinition("wizard_lightning_arc", "Wizard Arc Length", "W.ARC", "Lightning chains across a wider area.", 28, 7, 18.0),
    "wizard_freeze_duration": ResearchDefinition("wizard_freeze_duration", "Wizard Freeze Duration", "W.ICE", "Ice effects last longer on enemies.", 26, 7, 18.0),
    "warrior_taunt_cooldown": ResearchDefinition("warrior_taunt_cooldown", "Knight Taunt Cooldown", "T.CD", "Warriors can taunt more often.", 24, 0, 17.0),
    "wizard_lightning_damage": ResearchDefinition("wizard_lightning_damage", "Wizard Lightning Damage", "WIZ.D", "Wizard troop lightning deals more damage.", 30, 5, 19.0),
    "cleric_healing_cooldown": ResearchDefinition("cleric_healing_cooldown", "Cleric Healing Cooldown", "C.HEAL", "Clerics pulse healing more often.", 26, 0, 17.0),
    "grunt_carry_capacity": ResearchDefinition("grunt_carry_capacity", "Grunt Carry Capacity", "G.CAP", "Grunts carry more minerals per trip.", 22, 0, 15.0),
    "grunt_work_speed": ResearchDefinition("grunt_work_speed", "Grunt Work Speed", "G.SPD", "Grunts move faster while working.", 26, 0, 16.0),
    "research_time": ResearchDefinition("research_time", "Research Time", "R.TIME", "Future research finishes faster.", 34, 6, 22.0, time_growth=1.18),
    "scroll_production_time": ResearchDefinition("scroll_production_time", "Scroll Production Time", "S.TIME", "Libraries scribe random scrolls faster.", 32, 5, 20.0, time_growth=1.18),
}


class ResearchManager:
    def __init__(self) -> None:
        self.levels: dict[str, int] = {research_id: 0 for research_id in RESEARCH_DEFINITIONS}
        self.auto_research: set[str] = set()

    def level(self, research_id: str) -> int:
        return self.levels.get(research_id, 0)

    def multiplier(self, research_id: str) -> float:
        definition = RESEARCH_DEFINITIONS[research_id]
        return 1.0 + definition.increment * self.level(research_id)

    def inverse_multiplier(self, research_id: str) -> float:
        return 1.0 / max(0.01, self.multiplier(research_id))

    def bonus_percent(self, research_id: str) -> int:
        definition = RESEARCH_DEFINITIONS[research_id]
        return int(round(definition.increment * self.level(research_id) * 100))

    def cost(self, research_id: str) -> tuple[int, int]:
        definition = RESEARCH_DEFINITIONS[research_id]
        level = self.level(research_id)
        gold = _round_up(definition.base_gold * (definition.cost_growth ** level), 5)
        minerals = 0
        if definition.base_minerals > 0:
            minerals = _round_up(definition.base_minerals * (definition.mineral_growth ** level), 5)
        return gold, minerals

    def time(self, research_id: str) -> float:
        definition = RESEARCH_DEFINITIONS[research_id]
        level = self.level(research_id)
        base = definition.base_time * (definition.time_growth ** level)
        base *= self.inverse_multiplier("research_time")
        return max(4.0, base)

    def can_afford(self, game, research_id: str) -> bool:
        gold, minerals = self.cost(research_id)
        return game.gold >= gold and game.minerals >= minerals

    def begin(self, game, research_id: str) -> ResearchOrder | None:
        if research_id not in RESEARCH_DEFINITIONS or not self.can_afford(game, research_id):
            return None
        gold, minerals = self.cost(research_id)
        game.gold -= gold
        game.minerals -= minerals
        total = self.time(research_id)
        return ResearchOrder(research_id, total, total)

    def complete(self, research_id: str) -> None:
        if research_id in RESEARCH_DEFINITIONS:
            self.levels[research_id] = self.level(research_id) + 1

    def auto_enabled(self, research_id: str) -> bool:
        return research_id in self.auto_research

    def toggle_auto(self, research_id: str) -> bool:
        if research_id not in RESEARCH_DEFINITIONS:
            return False
        if research_id in self.auto_research:
            self.auto_research.remove(research_id)
            return False
        self.auto_research.add(research_id)
        return True


def _round_up(value: float, step: int) -> int:
    return max(step, int(math.ceil(value / step) * step))
