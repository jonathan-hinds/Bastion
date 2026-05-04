from __future__ import annotations

from dataclasses import dataclass
from math import floor


ATTRIBUTE_ORDER = ("stamina", "intellect", "strength", "agility", "cunning")

HP_PER_STAMINA = 10.0
MELEE_DAMAGE_PER_STRENGTH = 1.2
MAGIC_DAMAGE_PER_INTELLECT = 1.25
ATTACK_SPEED_BASE = 0.42
ATTACK_SPEED_PER_AGILITY = 0.045
COOLDOWN_REDUCTION_PER_CUNNING = 0.025
COOLDOWN_MULTIPLIER_FLOOR = 0.45


@dataclass(frozen=True)
class CombatAttributes:
    stamina: int
    intellect: int
    strength: int
    agility: int
    cunning: int

    def total(self) -> int:
        return sum(int(getattr(self, key)) for key in ATTRIBUTE_ORDER)


def max_health_from_stamina(stamina: int) -> float:
    return max(1.0, stamina * HP_PER_STAMINA)


def melee_damage_from_strength(strength: int) -> float:
    return max(1.0, strength * MELEE_DAMAGE_PER_STRENGTH)


def magic_damage_from_intellect(intellect: int) -> float:
    return max(1.0, intellect * MAGIC_DAMAGE_PER_INTELLECT)


def attack_speed_from_agility(agility: int) -> float:
    return max(0.10, ATTACK_SPEED_BASE + agility * ATTACK_SPEED_PER_AGILITY)


def cooldown_multiplier_from_cunning(cunning: int) -> float:
    return max(COOLDOWN_MULTIPLIER_FLOOR, 1.0 - cunning * COOLDOWN_REDUCTION_PER_CUNNING)


def allocate_attribute_budget(
    budget: int | float,
    weights: dict[str, float],
    *,
    minimum: int = 1,
) -> CombatAttributes:
    total_budget = max(len(ATTRIBUTE_ORDER) * minimum, int(round(budget)))
    remaining = total_budget - len(ATTRIBUTE_ORDER) * minimum
    weight_total = sum(max(0.0, float(weights.get(key, 0.0))) for key in ATTRIBUTE_ORDER)
    if weight_total <= 0.0:
        weights = {key: 1.0 for key in ATTRIBUTE_ORDER}
        weight_total = float(len(ATTRIBUTE_ORDER))

    values = {key: minimum for key in ATTRIBUTE_ORDER}
    remainders: list[tuple[float, str]] = []
    assigned = 0
    for key in ATTRIBUTE_ORDER:
        share = remaining * max(0.0, float(weights.get(key, 0.0))) / weight_total
        points = int(floor(share))
        values[key] += points
        assigned += points
        remainders.append((share - points, key))

    for _remainder, key in sorted(remainders, reverse=True)[: max(0, remaining - assigned)]:
        values[key] += 1

    return CombatAttributes(**values)
