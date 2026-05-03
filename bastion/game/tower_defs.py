from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TowerBlueprint:
    cost: int
    range: float
    damage: float
    fire_rate: float
    projectile_speed: float
    accuracy: float
    aoe: float = 0.0
    effect: str = ""


BUILD_COSTS = {
    "wall": 3,
    "archer": 16,
    "cannon": 26,
    "wizard": 34,
    "barracks": 36,
    "house": 22,
    "extractor": 24,
    "torch": 32,
    "training_grounds": 46,
    "research": 42,
    "library": 44,
    "shield_generator": 52,
    "core": 120,
}

MINERAL_BUILD_COSTS = {
    "archer": 10,
    "cannon": 18,
    "wizard": 26,
    "extractor": 8,
    "shield_generator": 30,
    "core": 42,
}

TOWER_BLUEPRINTS = {
    "archer": TowerBlueprint(cost=16, range=260, damage=13, fire_rate=2.05, projectile_speed=540, accuracy=0.94),
    "cannon": TowerBlueprint(cost=26, range=172, damage=48, fire_rate=0.58, projectile_speed=290, accuracy=0.82, aoe=48),
    "wizard": TowerBlueprint(cost=34, range=215, damage=40, fire_rate=0.92, projectile_speed=400, accuracy=0.88, aoe=60),
}

TOWER_NAMES = {
    "archer": "Archer",
    "cannon": "Cannon",
    "wizard": "Wizard",
}

SPECIALIZATIONS = {
    "archer": {
        "flaming": "Flaming Archer",
        "quick_draw": "Quick Draw",
    },
    "wizard": {
        "lightning": "Lightning Wizard",
        "ice": "Ice Wizard",
    },
    "cannon": {
        "barrage": "Barrage Cannon",
        "mortar": "Mortar Cannon",
    },
}


def tower_name(kind: str, specialization: str | None = None) -> str:
    if specialization:
        return SPECIALIZATIONS.get(kind, {}).get(specialization, TOWER_NAMES.get(kind, kind.title()))
    return TOWER_NAMES.get(kind, kind.title())


def xp_needed(level: int) -> int:
    return 42 + level * 28


def stats_for(kind: str, level: int, specialization: str | None = None) -> dict[str, float | str]:
    base = TOWER_BLUEPRINTS[kind]
    level_bonus = max(0, level - 1)
    stats: dict[str, float | str] = {
        "range": base.range * (1.0 + 0.025 * level_bonus),
        "damage": base.damage * (1.0 + 0.10 * level_bonus),
        "fire_rate": base.fire_rate * (1.0 + 0.04 * level_bonus),
        "projectile_speed": base.projectile_speed,
        "accuracy": min(0.99, base.accuracy + 0.012 * level_bonus),
        "aoe": base.aoe,
        "effect": base.effect,
    }

    if specialization == "flaming":
        stats["damage"] = float(stats["damage"]) * 1.05
        stats["effect"] = "burn"
    elif specialization == "quick_draw":
        stats["fire_rate"] = float(stats["fire_rate"]) * 1.75
        stats["damage"] = float(stats["damage"]) * 0.82
        stats["accuracy"] = min(0.99, float(stats["accuracy"]) + 0.03)
    elif specialization == "lightning":
        stats["damage"] = float(stats["damage"]) * 0.92
        stats["aoe"] = 0.0
        stats["effect"] = "chain"
        stats["accuracy"] = min(0.99, float(stats["accuracy"]) + 0.06)
    elif specialization == "ice":
        stats["fire_rate"] = float(stats["fire_rate"]) * 0.92
        stats["aoe"] = max(float(stats["aoe"]), 62)
        stats["effect"] = "slow"
    elif specialization == "barrage":
        stats["fire_rate"] = float(stats["fire_rate"]) * 1.85
        stats["damage"] = float(stats["damage"]) * 0.72
        stats["aoe"] = max(float(stats["aoe"]), 28)
    elif specialization == "mortar":
        stats["range"] = float(stats["range"]) * 1.45
        stats["damage"] = float(stats["damage"]) * 1.55
        stats["fire_rate"] = float(stats["fire_rate"]) * 0.52
        stats["projectile_speed"] = float(stats["projectile_speed"]) * 0.76
        stats["accuracy"] = max(0.72, float(stats["accuracy"]) - 0.07)
        stats["aoe"] = max(float(stats["aoe"]), 86)

    return stats
