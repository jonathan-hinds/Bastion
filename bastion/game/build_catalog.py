from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from bastion import config
from bastion.game.abilities import AbilityCard, configure_tower_abilities
from bastion.game.grid import GameGrid
from bastion.game.resources import MineralExtractor
from bastion.game.tower_defs import BUILD_COSTS, MINERAL_BUILD_COSTS, TOWER_BLUEPRINTS, stats_for
from bastion.game.units import (
    Barracks,
    HOUSE_CAPACITY,
    House,
    Library,
    ResearchBuilding,
    ShieldGenerator,
    Torch,
    TrainingGrounds,
)


@dataclass(frozen=True)
class BuildCategory:
    category_id: str
    label: str
    description: str
    order: int


@dataclass(frozen=True)
class BuildAbilitySpec:
    ability_id: str
    name: str
    description: str
    details: tuple[str, ...] = ()
    passive: bool = True

    def card(self) -> AbilityCard:
        return AbilityCard(
            self.ability_id,
            self.name,
            self.description,
            self.details,
            passive=self.passive,
            state="PASSIVE" if self.passive else "",
        )


@dataclass(frozen=True)
class BuildMenuEntry:
    mode: str
    label: str
    category_id: str
    description: str
    abilities: tuple[BuildAbilitySpec, ...]
    details: tuple[str, ...] = ()

    @property
    def gold_cost(self) -> int:
        return BUILD_COSTS[self.mode]

    @property
    def mineral_cost(self) -> int:
        return MINERAL_BUILD_COSTS.get(self.mode, 0)

    @cached_property
    def ability_cards(self) -> tuple[AbilityCard, ...]:
        if self.category_id == "towers":
            return _tower_ability_cards(self.mode)
        return ()

    def cost_label(self) -> str:
        label = f"{self.gold_cost}G"
        if self.mineral_cost > 0:
            label += f"  {self.mineral_cost}M"
        return label

    def can_afford(self, gold: int, minerals: int) -> bool:
        return gold >= self.gold_cost and minerals >= self.mineral_cost

    def tooltip_card(self, category: BuildCategory) -> AbilityCard:
        details = (category.label, f"Cost {self.cost_label()}", *self.details)
        return AbilityCard(
            self.mode,
            self.label,
            self.description,
            details,
            passive=True,
            state=category.label.upper(),
        )


def _tower_attack(kind: str, name: str, description: str) -> BuildAbilitySpec:
    blueprint = TOWER_BLUEPRINTS[kind]
    details = (
        f"Range {int(blueprint.range)}",
        f"Damage {int(blueprint.damage)}",
        f"Rate {blueprint.fire_rate:0.2f}/s",
    )
    if blueprint.aoe > 0:
        details += (f"Area {int(blueprint.aoe)}",)
    return BuildAbilitySpec(f"{kind}_attack", name, description, details, passive=False)


class TowerBuildPreview:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.level = 1
        self.specialization = None
        self.installed_mods: list[str] = []
        self.cooldown = 0.0
        self.alive = True
        configure_tower_abilities(self)

    def stats(self, game=None) -> dict[str, float | str]:
        return stats_for(self.kind, self.level, self.specialization)

    def mod_effect(self, _effect: str, default: float = 1.0) -> float:
        return default


def _tower_ability_cards(kind: str) -> tuple[AbilityCard, ...]:
    if kind not in TOWER_BLUEPRINTS:
        return ()
    return tuple(TowerBuildPreview(kind).abilities.cards())


BUILD_CATEGORIES = (
    BuildCategory(
        "towers",
        "Towers",
        "Direct damage buildings that remove enemies before they reach the core.",
        10,
    ),
    BuildCategory(
        "defenses",
        "Defenses",
        "Path control, threat control, and mitigation tools for shaping enemy pressure.",
        20,
    ),
    BuildCategory(
        "infrastructure",
        "Infrastructure",
        "Economy, supply, and production buildings that keep the base operating.",
        30,
    ),
    BuildCategory(
        "advancement",
        "Advancement",
        "Long-term growth buildings that convert time and resources into stronger runs.",
        40,
    ),
)


BUILD_MENU_ENTRIES = (
    BuildMenuEntry(
        "archer",
        "Archer Tower",
        "towers",
        "Fast single-target pressure with reliable aim and high uptime.",
        (),
        ("HP 95",),
    ),
    BuildMenuEntry(
        "cannon",
        "Cannon Tower",
        "towers",
        "Slow, heavy shots that punish clustered enemies with explosive area damage.",
        (),
        ("HP 135",),
    ),
    BuildMenuEntry(
        "wizard",
        "Wizard Tower",
        "towers",
        "Arcane area damage that can later specialize into lightning or ice control.",
        (),
        ("HP 105",),
    ),
    BuildMenuEntry(
        "wall",
        "Wall",
        "defenses",
        "A one-tile blocker that connects into defensive lines while keeping paths legal.",
        (),
        (f"HP {int(GameGrid.wall_max_health)}", "Path blocker"),
    ),
    BuildMenuEntry(
        "torch",
        "Torch",
        "defenses",
        "A heavy aggro beacon that drags enemy attention into its radius.",
        (),
        (
            f"HP {int(Torch.max_health)}",
            f"Radius {int(Torch.aggro_radius)}",
            f"Threat {int(Torch.aggro_amount)}",
            f"Pulse {Torch.aggro_interval:0.2f}s",
        ),
    ),
    BuildMenuEntry(
        "shield_generator",
        "Shield Generator",
        "defenses",
        "Wraps connected structures in one shared shield pool.",
        (),
        (
            f"HP {int(ShieldGenerator.max_health)}",
            f"Base shield {int(ShieldGenerator.base_shield)}",
            f"+{int(ShieldGenerator.shield_per_structure)} per structure",
            f"Recharge {ShieldGenerator.recharge_duration:0.0f}s",
        ),
    ),
    BuildMenuEntry(
        "barracks",
        "Barracks",
        "infrastructure",
        "Trains stationable troops for harvesting, support, and melee control.",
        (),
        (f"HP {int(Barracks.max_health)}", f"Queue {Barracks.queue_limit}", "Unlocks Unit Roster"),
    ),
    BuildMenuEntry(
        "house",
        "House",
        "infrastructure",
        f"Adds {HOUSE_CAPACITY} troop supply to your base.",
        (),
        (f"HP {int(House.max_health)}", f"+{HOUSE_CAPACITY} capacity"),
    ),
    BuildMenuEntry(
        "extractor",
        "Extractor",
        "infrastructure",
        "Claims a gold or mineral deposit and anchors the route workers use for extraction.",
        (),
        (f"HP {int(MineralExtractor.max_health)}", "Requires deposit", "Worker route"),
    ),
    BuildMenuEntry(
        "training_grounds",
        "Training Grounds",
        "advancement",
        "Passively drills nearby troops so they can level up between fights.",
        (),
        (
            f"HP {int(TrainingGrounds.max_health)}",
            f"+{TrainingGrounds.xp_amount} XP",
            f"{TrainingGrounds.xp_interval:0.1f}s interval",
            f"{TrainingGrounds.max_trainees} troops",
        ),
    ),
    BuildMenuEntry(
        "research",
        "Research Lab",
        "advancement",
        "Unlocks repeatable research projects that scale towers, troops, and production.",
        (),
        (f"HP {int(ResearchBuilding.max_health)}", "Repeatable upgrades", "Auto research"),
    ),
    BuildMenuEntry(
        "library",
        "Library",
        "advancement",
        "Scribes random combat scrolls into your inventory.",
        (),
        (f"HP {int(Library.max_health)}", f"Scroll {Library.scroll_gold_cost}G", "Random item"),
    ),
    BuildMenuEntry(
        "core",
        "Core",
        "advancement",
        "A new powerhouse that feeds arcane circuits to nearby structures.",
        (),
        (
            f"Capacity {config.ARCANE_CORE_CAPACITY}",
            f"HP {config.TOWNHALL_MAX_HP}",
            "7x7 reserve",
        ),
    ),
)


BUILD_CATEGORY_BY_ID = {category.category_id: category for category in BUILD_CATEGORIES}
BUILD_ENTRY_BY_MODE = {entry.mode: entry for entry in BUILD_MENU_ENTRIES}


def build_entries_for_category(category_id: str) -> tuple[BuildMenuEntry, ...]:
    return tuple(entry for entry in BUILD_MENU_ENTRIES if entry.category_id == category_id)


def iter_build_categories() -> tuple[tuple[BuildCategory, tuple[BuildMenuEntry, ...]], ...]:
    return tuple(
        (category, entries)
        for category in sorted(BUILD_CATEGORIES, key=lambda item: item.order)
        if (entries := build_entries_for_category(category.category_id))
    )
