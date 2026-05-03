from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


ELEMENTS = ("physical", "fire", "ice", "lightning", "holy")
DEFAULT_RESISTANCES = {element: 1.0 for element in ELEMENTS}


@dataclass(frozen=True)
class ElementalEffect:
    element: str
    duration: float = 0.0
    dot_dps: float = 0.0
    slow_multiplier: float = 1.0
    attack_slow_multiplier: float = 1.0
    stun_duration: float = 0.0
    spread_radius: float = 0.0
    spread_falloff: float = 0.5


def normalize_element(element: str | None) -> str:
    if element in ELEMENTS:
        return str(element)
    return "physical"


def normalize_resistances(raw: Mapping[str, object] | None = None) -> dict[str, float]:
    resistances = dict(DEFAULT_RESISTANCES)
    if not raw:
        return resistances
    for element in ELEMENTS:
        try:
            resistances[element] = max(0.0, float(raw.get(element, resistances[element])))
        except (TypeError, ValueError):
            resistances[element] = DEFAULT_RESISTANCES[element]
    return resistances


def damage_multiplier(target, element: str | None) -> float:
    element = normalize_element(element)
    resistances = normalize_resistances(getattr(target, "resistances", None))
    multiplier = resistances[element]
    if element == "holy" and getattr(target, "faction_type", "") == "undead":
        multiplier *= 2.0
    return max(0.0, multiplier)


def healing_multiplier(target, element: str | None) -> float:
    element = normalize_element(element)
    if element != "holy":
        return 1.0
    values = getattr(target, "healing_effectiveness", None)
    if isinstance(values, Mapping):
        try:
            return max(0.0, float(values.get("holy", 1.0)))
        except (TypeError, ValueError):
            return 1.0
    return 1.0
