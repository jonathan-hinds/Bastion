from __future__ import annotations

import math
import random
from dataclasses import dataclass

import pygame

from bastion import config
from bastion.engine.drawing import draw_circle_alpha, draw_line_alpha, draw_rect_alpha
from bastion.engine import hover_feedback
from bastion.game.abilities import AbilitySystemComponent, ItemPassiveAbility, ItemThreatAuraPassive, configure_troop_abilities
from bastion.game.combat import MeleeAttackController
from bastion.game.entities import FloatingText
from bastion.game.items import ActiveItemBuff, ITEM_DEFINITIONS, Inventory, InventorySlot
from bastion.game.navigation import PathNavigator
from bastion.game.research import RESEARCH_DEFINITIONS, ResearchOrder
from bastion.game.resources import ResourceHarvester
from bastion.game.tower_defs import xp_needed


@dataclass(frozen=True)
class TroopAttributes:
    stamina: int
    intellect: int
    strength: int
    agility: int
    cunning: int

    def copy(self) -> "TroopAttributes":
        return TroopAttributes(self.stamina, self.intellect, self.strength, self.agility, self.cunning)


ATTRIBUTE_ORDER = ("stamina", "intellect", "strength", "agility", "cunning")
ATTRIBUTE_LABELS = {
    "stamina": "Stamina",
    "intellect": "Intellect",
    "strength": "Strength",
    "agility": "Agility",
    "cunning": "Cunning",
}
ATTRIBUTE_SHORT_LABELS = {
    "stamina": "STA",
    "intellect": "INT",
    "strength": "STR",
    "agility": "AGI",
    "cunning": "CUN",
}

HP_PER_STAMINA = 10.0
MELEE_DAMAGE_PER_STRENGTH = 1.2
MAGIC_DAMAGE_PER_INTELLECT = 1.25
ATTACK_SPEED_BASE = 0.42
ATTACK_SPEED_PER_AGILITY = 0.045
COOLDOWN_REDUCTION_PER_CUNNING = 0.025
COOLDOWN_MULTIPLIER_FLOOR = 0.45


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


@dataclass(frozen=True)
class TroopBlueprint:
    cost: int
    train_time: float
    attributes: TroopAttributes
    speed: float
    acceleration: float
    radius: float
    attack_range: float
    station_range: float
    projectile_speed: float
    attack_stat: str = "strength"

    @property
    def health(self) -> float:
        return max_health_from_stamina(self.attributes.stamina)

    @property
    def damage(self) -> float:
        if self.attack_stat == "intellect":
            return magic_damage_from_intellect(self.attributes.intellect)
        return melee_damage_from_strength(self.attributes.strength)

    @property
    def fire_rate(self) -> float:
        return attack_speed_from_agility(self.attributes.agility)


TROOP_DATA = {
    "grunt": TroopBlueprint(
        cost=8,
        train_time=2.7,
        attributes=TroopAttributes(stamina=8, intellect=2, strength=6, agility=9, cunning=4),
        speed=86,
        acceleration=520,
        radius=8,
        attack_range=34,
        station_range=165,
        projectile_speed=360,
    ),
    "warrior": TroopBlueprint(
        cost=16,
        train_time=4.5,
        attributes=TroopAttributes(stamina=14, intellect=2, strength=10, agility=7, cunning=7),
        speed=66,
        acceleration=420,
        radius=10,
        attack_range=38,
        station_range=145,
        projectile_speed=330,
    ),
    "archer": TroopBlueprint(
        cost=18,
        train_time=4.8,
        attributes=TroopAttributes(stamina=7, intellect=3, strength=8, agility=10, cunning=8),
        speed=82,
        acceleration=500,
        radius=8,
        attack_range=215,
        station_range=250,
        projectile_speed=430,
    ),
    "cleric": TroopBlueprint(
        cost=14,
        train_time=4.0,
        attributes=TroopAttributes(stamina=8, intellect=9, strength=3, agility=6, cunning=9),
        speed=78,
        acceleration=480,
        radius=8,
        attack_range=28,
        station_range=160,
        projectile_speed=300,
    ),
    "engineer": TroopBlueprint(
        cost=16,
        train_time=4.6,
        attributes=TroopAttributes(stamina=10, intellect=5, strength=5, agility=6, cunning=8),
        speed=72,
        acceleration=450,
        radius=9,
        attack_range=30,
        station_range=150,
        projectile_speed=300,
    ),
    "wizard": TroopBlueprint(
        cost=20,
        train_time=5.2,
        attributes=TroopAttributes(stamina=7, intellect=11, strength=3, agility=6, cunning=9),
        speed=74,
        acceleration=460,
        radius=8,
        attack_range=68,
        station_range=175,
        projectile_speed=0,
        attack_stat="intellect",
    ),
    "rune_mage": TroopBlueprint(
        cost=22,
        train_time=5.6,
        attributes=TroopAttributes(stamina=8, intellect=10, strength=3, agility=5, cunning=10),
        speed=72,
        acceleration=455,
        radius=8,
        attack_range=74,
        station_range=175,
        projectile_speed=0,
        attack_stat="intellect",
    ),
}

TROOP_NAMES = {
    "grunt": "Grunt",
    "warrior": "Warrior",
    "archer": "Archer",
    "cleric": "Cleric",
    "engineer": "Engineer",
    "wizard": "Wizard",
    "rune_mage": "Rune Mage",
}


class TroopAbilityPreview:
    def __init__(self, kind: str) -> None:
        data = TROOP_DATA[kind]
        self.kind = kind
        self.display_name = TROOP_NAMES[kind]
        self.attributes = data.attributes.copy()
        self.attack_stat = data.attack_stat
        self.base_range = data.attack_range
        self.station_range = data.station_range
        self.base_damage = data.damage
        self.base_fire_rate = data.fire_rate
        self.projectile_speed = data.projectile_speed
        self.radius = data.radius
        self.level = 1
        self.cooldown = 0.0
        self.alive = True
        self.attack_enabled = True
        self.pos = pygame.Vector2(0, 0)
        self.station = pygame.Vector2(0, 0)
        self.melee = MeleeAttackController(self)
        self.abilities = AbilitySystemComponent(self)
        configure_troop_abilities(self)

    def stats(self, game=None) -> dict[str, float]:
        melee_damage = melee_damage_from_strength(self.attributes.strength)
        magic_damage = magic_damage_from_intellect(self.attributes.intellect)
        damage = magic_damage if self.attack_stat == "intellect" else melee_damage
        fire_rate = attack_speed_from_agility(self.attributes.agility)
        stats = {
            "range": self.base_range,
            "damage": damage,
            "melee_damage": melee_damage,
            "magic_damage": magic_damage,
            "fire_rate": fire_rate,
            "ability_cooldown": self.ability_cooldown_multiplier(),
        }
        if game is not None and hasattr(game, "item_multiplier"):
            stats["damage"] *= game.item_multiplier("troop_damage_multiplier")
            cooldown_multiplier = game.item_multiplier("troop_cooldown_multiplier")
            stats["fire_rate"] *= 1.0 / max(0.05, cooldown_multiplier)
        return stats

    def ability_cooldown_multiplier(self) -> float:
        return cooldown_multiplier_from_cunning(self.attributes.cunning)

    def _enemy_inside_station(self, enemy) -> bool:
        return True


def troop_ability_cards(kind: str, game=None):
    if kind not in TROOP_DATA:
        return []
    return TroopAbilityPreview(kind).abilities.cards(game)


HOUSE_CAPACITY = 5


@dataclass
class TrainingOrder:
    kind: str
    remaining: float
    total: float


@dataclass
class ScrollOrder:
    remaining: float
    total: float
    ready_item_id: str | None = None


class Barracks:
    kind = "barracks"
    display_name = "Barracks"
    target_class = "structure"
    radius = config.TILE_SIZE * 0.52
    max_health = 260.0
    queue_limit = 5

    def __init__(self, cell: tuple[int, int], grid) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.health = self.max_health
        self.alive = True
        self.train_queue: list[TrainingOrder] = []
        self.pulse = random.random() * math.tau

    def can_queue(self) -> bool:
        return self.alive and len(self.train_queue) < self.queue_limit

    def queue_train(self, kind: str) -> bool:
        if kind not in TROOP_DATA or not self.can_queue():
            return False
        data = TROOP_DATA[kind]
        self.train_queue.append(TrainingOrder(kind, data.train_time, data.train_time))
        return True

    def update(self, dt: float, game) -> None:
        if not self.alive or not self.train_queue:
            return
        order = self.train_queue[0]
        order.remaining -= dt
        if order.remaining <= 0:
            if game.spawn_troop(order.kind, self):
                self.train_queue.pop(0)
            else:
                order.remaining = 0.35

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, selected: bool = False, hovered: bool = False) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        size = int(config.TILE_SIZE * camera.zoom * 0.94 * hover_feedback.hover_scale(hovered))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        fill, mark = hover_feedback.inverted_pair(hovered)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))
        inset = rect.inflate(-max(4, int(size * 0.32)), -max(4, int(size * 0.32)))
        pygame.draw.rect(surface, mark, inset, max(1, int(camera.zoom)))
        if selected:
            draw_circle_alpha(surface, center, size * 0.72, config.PALETTE.white, 58, 1)

        if self.train_queue:
            order = self.train_queue[0]
            progress = 1.0 - max(0.0, order.remaining / order.total)
            bar = pygame.Rect(rect.left, rect.bottom + 4, rect.width, max(2, int(4 * camera.zoom)))
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * progress)
            pygame.draw.rect(surface, config.PALETTE.white, fill)

        self._draw_health(surface, rect)

    def _draw_health(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        if self.health >= self.max_health:
            return
        bar = pygame.Rect(rect.left, rect.top - 6, rect.width, 3)
        pygame.draw.rect(surface, config.PALETTE.black, bar)
        fill = bar.copy()
        fill.width = int(bar.width * max(0.0, self.health / self.max_health))
        pygame.draw.rect(surface, config.PALETTE.white, fill)


class House:
    kind = "house"
    display_name = "House"
    target_class = "structure"
    radius = config.TILE_SIZE * 0.50
    max_health = 165.0
    capacity = HOUSE_CAPACITY

    def __init__(self, cell: tuple[int, int], grid) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.health = self.max_health
        self.alive = True
        self.pulse = random.random() * math.tau

    def update(self, dt: float, game) -> None:
        return

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, selected: bool = False, hovered: bool = False) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        size = int(config.TILE_SIZE * camera.zoom * 0.90 * hover_feedback.hover_scale(hovered))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        fill, mark = hover_feedback.inverted_pair(hovered)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))

        roof = [
            (rect.left + rect.width * 0.12, rect.centery),
            (rect.centerx, rect.top + rect.height * 0.12),
            (rect.right - rect.width * 0.12, rect.centery),
        ]
        pygame.draw.polygon(surface, mark, roof, max(1, int(2 * camera.zoom)))
        door = pygame.Rect(0, 0, max(3, int(size * 0.18)), max(5, int(size * 0.28)))
        door.midbottom = (rect.centerx, rect.bottom - max(2, int(size * 0.08)))
        pygame.draw.rect(surface, mark, door, max(1, int(camera.zoom)))

        if selected:
            draw_circle_alpha(surface, center, size * 0.70, config.PALETTE.white, 58, 1)

        if self.health < self.max_health:
            bar = pygame.Rect(rect.left, rect.top - 6, rect.width, 3)
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, self.health / self.max_health))
            pygame.draw.rect(surface, config.PALETTE.white, fill)


class Torch:
    kind = "torch"
    display_name = "Torch"
    target_class = "structure"
    radius = config.TILE_SIZE * 0.48
    max_health = 420.0
    aggro_radius = 300.0
    aggro_amount = 82.0
    aggro_interval = 0.24

    def __init__(self, cell: tuple[int, int], grid) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.health = self.max_health
        self.alive = True
        self.pulse = random.random() * math.tau
        self.aggro_timer = random.uniform(0.0, self.aggro_interval)

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.aggro_timer -= dt
        if self.aggro_timer > 0:
            return
        self.aggro_timer = self.aggro_interval
        game.emit_aggro(self, self.aggro_amount, "taunt", radius=self.aggro_radius)

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, selected: bool = False, hovered: bool = False) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        size = int(config.TILE_SIZE * camera.zoom * 0.88 * hover_feedback.hover_scale(hovered))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        fill, mark = hover_feedback.inverted_pair(hovered)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))

        pole_top = pygame.Vector2(rect.centerx, rect.top + size * 0.18)
        pole_bottom = pygame.Vector2(rect.centerx, rect.bottom - size * 0.16)
        pygame.draw.line(surface, mark, pole_top, pole_bottom, max(1, int(2 * camera.zoom)))
        phase = pygame.time.get_ticks() * 0.006 + self.pulse
        flame_r = max(3, int(size * (0.13 + 0.03 * math.sin(phase))))
        flame = pygame.Vector2(rect.centerx, rect.top + size * 0.24)
        pygame.draw.circle(surface, mark, flame, flame_r, max(1, int(camera.zoom)))
        pygame.draw.line(surface, mark, flame + pygame.Vector2(-flame_r, flame_r), flame + pygame.Vector2(flame_r, -flame_r), max(1, int(camera.zoom)))

        aura_alpha = 34 if selected else 18
        draw_circle_alpha(surface, center, self.aggro_radius * camera.zoom, config.PALETTE.white, aura_alpha, 1)
        if selected:
            draw_circle_alpha(surface, center, size * 0.70, config.PALETTE.white, 58, 1)

        self._draw_health(surface, rect)

    def _draw_health(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        if self.health >= self.max_health:
            return
        bar = pygame.Rect(rect.left, rect.top - 6, rect.width, 3)
        pygame.draw.rect(surface, config.PALETTE.black, bar)
        fill = bar.copy()
        fill.width = int(bar.width * max(0.0, self.health / self.max_health))
        pygame.draw.rect(surface, config.PALETTE.white, fill)


class TrainingGrounds:
    kind = "training_grounds"
    display_name = "Training Grounds"
    target_class = "structure"
    radius = config.TILE_SIZE * 0.54
    max_health = 240.0
    training_radius = 185.0
    max_trainees = 5
    xp_interval = 2.0
    xp_amount = 4

    def __init__(self, cell: tuple[int, int], grid) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.health = self.max_health
        self.alive = True
        self.pulse = random.random() * math.tau
        self.xp_timer = self.xp_interval

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.xp_timer -= dt
        if self.xp_timer > 0:
            return
        self.xp_timer = self.xp_interval
        trainees = [
            troop
            for troop in game.nearby_troops(self.pos, self.training_radius)
            if troop.alive and troop.pos.distance_to(self.pos) <= self.training_radius + troop.radius
        ]
        trainees.sort(key=lambda troop: troop.pos.distance_to(self.pos))
        awarded = False
        for troop in trainees[: self.max_trainees]:
            if troop.add_xp(self.xp_amount):
                game.texts.append(FloatingText(pygame.Vector2(troop.pos), "READY", 0.85))
            game.texts.append(FloatingText(pygame.Vector2(troop.pos), f"+{self.xp_amount}XP", 0.55))
            awarded = True
        if awarded:
            game.texts.append(FloatingText(pygame.Vector2(self.pos), "DRILL", 0.65))

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, selected: bool = False, hovered: bool = False) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        size = int(config.TILE_SIZE * camera.zoom * 0.94 * hover_feedback.hover_scale(hovered))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        fill, mark = hover_feedback.inverted_pair(hovered)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))

        phase = pygame.time.get_ticks() * 0.004 + self.pulse
        for offset in (-0.24, 0.24):
            x = rect.centerx + size * offset
            pygame.draw.line(surface, mark, (x, rect.top + size * 0.24), (x, rect.bottom - size * 0.18), max(1, int(camera.zoom)))
        pygame.draw.line(surface, mark, (rect.left + size * 0.20, rect.centery), (rect.right - size * 0.20, rect.centery), max(1, int(2 * camera.zoom)))
        marker = center + pygame.Vector2(math.cos(phase), math.sin(phase)) * size * 0.20
        pygame.draw.circle(surface, mark, marker, max(2, int(2.5 * camera.zoom)))

        draw_circle_alpha(surface, center, self.training_radius * camera.zoom, config.PALETTE.white, 32 if selected else 14, 1)
        if selected:
            draw_circle_alpha(surface, center, size * 0.72, config.PALETTE.white, 58, 1)

        self._draw_health(surface, rect)

    def _draw_health(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        if self.health >= self.max_health:
            return
        bar = pygame.Rect(rect.left, rect.top - 6, rect.width, 3)
        pygame.draw.rect(surface, config.PALETTE.black, bar)
        fill = bar.copy()
        fill.width = int(bar.width * max(0.0, self.health / self.max_health))
        pygame.draw.rect(surface, config.PALETTE.white, fill)


class ResearchBuilding:
    kind = "research"
    display_name = "Research"
    target_class = "structure"
    radius = config.TILE_SIZE * 0.51
    max_health = 210.0

    def __init__(self, cell: tuple[int, int], grid) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.health = self.max_health
        self.alive = True
        self.active_order: ResearchOrder | None = None
        self.pulse = random.random() * math.tau

    def can_research(self) -> bool:
        return self.alive and self.active_order is None

    def start_research(self, research_id: str, game, quiet: bool = False) -> bool:
        if not self.can_research():
            if not quiet:
                game.message("RESEARCH BUSY")
            return False
        order = game.research.begin(game, research_id)
        if order is None:
            if not quiet:
                game.message("CANNOT RESEARCH")
            return False
        self.active_order = order
        name = RESEARCH_DEFINITIONS[research_id].name
        if not quiet:
            game.message(f"RESEARCH {name.upper()}")
        return True

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        if self.active_order is None:
            self._start_auto_research(game)
            return
        self.active_order.remaining -= dt
        if self.active_order.remaining > 0:
            return
        research_id = self.active_order.research_id
        self.active_order = None
        game.research.complete(research_id)
        definition = RESEARCH_DEFINITIONS[research_id]
        level = game.research.level(research_id)
        game.message(f"{definition.name.upper()} {level}")
        game.spawn_burst(self.pos, 24, 78)
        game.texts.append(FloatingText(pygame.Vector2(self.pos), "RESEARCH", 0.85))
        self._start_auto_research(game)

    def _start_auto_research(self, game) -> bool:
        if not self.can_research():
            return False
        for research_id in list(game.research.auto_research):
            if self.start_research(research_id, game, quiet=True):
                return True
        return False

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, selected: bool = False, hovered: bool = False) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        size = int(config.TILE_SIZE * camera.zoom * 0.92 * hover_feedback.hover_scale(hovered))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        fill, mark = hover_feedback.inverted_pair(hovered)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))

        ring_r = max(4, int(size * 0.22))
        pygame.draw.circle(surface, mark, center, ring_r, max(1, int(camera.zoom)))
        pygame.draw.line(surface, mark, (rect.left + size * 0.18, rect.bottom - size * 0.24), (rect.right - size * 0.18, rect.top + size * 0.24), max(1, int(camera.zoom)))
        pygame.draw.line(surface, mark, (rect.left + size * 0.28, rect.top + size * 0.28), (rect.right - size * 0.28, rect.bottom - size * 0.28), max(1, int(camera.zoom)))

        if selected:
            draw_circle_alpha(surface, center, size * 0.72, config.PALETTE.white, 58, 1)

        if self.active_order is not None:
            progress = 1.0 - max(0.0, self.active_order.remaining / max(0.01, self.active_order.total))
            bar = pygame.Rect(rect.left, rect.bottom + 4, rect.width, max(2, int(4 * camera.zoom)))
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * progress)
            pygame.draw.rect(surface, config.PALETTE.white, fill)
            tick = pygame.Vector2(math.cos(pygame.time.get_ticks() * 0.008 + self.pulse), math.sin(pygame.time.get_ticks() * 0.008 + self.pulse))
            pygame.draw.circle(surface, mark, center + tick * ring_r, max(2, int(2.5 * camera.zoom)))

        if self.health < self.max_health:
            bar = pygame.Rect(rect.left, rect.top - 6, rect.width, 3)
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, self.health / self.max_health))
            pygame.draw.rect(surface, config.PALETTE.white, fill)


class Library:
    kind = "library"
    display_name = "Library"
    target_class = "structure"
    radius = config.TILE_SIZE * 0.51
    max_health = 195.0
    scroll_gold_cost = 30

    def __init__(self, cell: tuple[int, int], grid) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.health = self.max_health
        self.alive = True
        self.active_order: ScrollOrder | None = None
        self.inventory_notice_timer = 0.0
        self.pulse = random.random() * math.tau

    def can_produce(self) -> bool:
        return self.alive and self.active_order is None

    def start_scroll(self, game) -> bool:
        if not self.can_produce():
            game.message("LIBRARY BUSY")
            return False
        if game.gold < self.scroll_gold_cost:
            game.message("NO GOLD")
            return False
        game.gold -= self.scroll_gold_cost
        total = game.roll_scroll_production_time()
        self.active_order = ScrollOrder(total, total)
        game.message("SCRIBE SCROLL")
        return True

    def update(self, dt: float, game) -> None:
        if not self.alive or self.active_order is None:
            return
        order = self.active_order
        if order.ready_item_id is not None:
            self._deliver_ready_scroll(dt, game)
            return
        order.remaining -= dt
        if order.remaining > 0:
            return
        order.ready_item_id = game.roll_scroll_item()
        game.spawn_burst(self.pos, 14, 64)
        self._deliver_ready_scroll(dt, game)

    def _deliver_ready_scroll(self, dt: float, game) -> None:
        order = self.active_order
        if order is None or order.ready_item_id is None:
            return
        if game.add_item(order.ready_item_id):
            self.active_order = None
            game.spawn_burst(self.pos, 16, 66)
            game.texts.append(FloatingText(pygame.Vector2(self.pos), "SCROLL", 0.85))
            return
        self.inventory_notice_timer = max(0.0, self.inventory_notice_timer - dt)
        if self.inventory_notice_timer <= 0:
            self.inventory_notice_timer = 1.4
            game.message("INVENTORY FULL")

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, selected: bool = False, hovered: bool = False) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        size = int(config.TILE_SIZE * camera.zoom * 0.92 * hover_feedback.hover_scale(hovered))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        fill, mark = hover_feedback.inverted_pair(hovered)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))

        shelf_y = rect.top + size * 0.32
        pygame.draw.line(surface, mark, (rect.left + size * 0.18, shelf_y), (rect.right - size * 0.18, shelf_y), max(1, int(camera.zoom)))
        pygame.draw.line(surface, mark, (rect.left + size * 0.18, rect.bottom - size * 0.28), (rect.right - size * 0.18, rect.bottom - size * 0.28), max(1, int(camera.zoom)))
        for index in range(3):
            x = rect.left + size * (0.28 + index * 0.22)
            y = rect.top + size * (0.45 + (index % 2) * 0.15)
            scroll = pygame.Rect(0, 0, max(4, int(size * 0.13)), max(3, int(size * 0.22)))
            scroll.center = (x, y)
            pygame.draw.rect(surface, mark, scroll, max(1, int(camera.zoom)))
            pygame.draw.circle(surface, mark, (scroll.centerx, scroll.top), max(1, int(2 * camera.zoom)), max(1, int(camera.zoom)))

        if selected:
            draw_circle_alpha(surface, center, size * 0.72, config.PALETTE.white, 58, 1)

        if self.active_order is not None:
            progress = 1.0
            if self.active_order.ready_item_id is None:
                progress = 1.0 - max(0.0, self.active_order.remaining / max(0.01, self.active_order.total))
            bar = pygame.Rect(rect.left, rect.bottom + 4, rect.width, max(2, int(4 * camera.zoom)))
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * progress)
            pygame.draw.rect(surface, config.PALETTE.white, fill)
            if self.active_order.ready_item_id is not None:
                tick = pygame.Vector2(math.cos(pygame.time.get_ticks() * 0.01 + self.pulse), math.sin(pygame.time.get_ticks() * 0.01 + self.pulse))
                pygame.draw.circle(surface, mark, center + tick * size * 0.24, max(2, int(2.5 * camera.zoom)))

        if self.health < self.max_health:
            bar = pygame.Rect(rect.left, rect.top - 6, rect.width, 3)
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, self.health / self.max_health))
            pygame.draw.rect(surface, config.PALETTE.white, fill)


class ShieldGenerator:
    kind = "shield_generator"
    display_name = "Shield Generator"
    target_class = "structure"
    radius = config.TILE_SIZE * 0.54
    max_health = 185.0
    shield_per_structure = 56.0
    base_shield = 90.0
    recharge_duration = 20.0
    recharge_health_cost_ratio = 0.34

    def __init__(self, cell: tuple[int, int], grid) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.health = self.max_health
        self.alive = True
        self.pulse = random.random() * math.tau
        self.shield = self.base_shield
        self.shield_max = self.base_shield
        self.recharge_remaining = 0.0
        self.recharge_total = self.recharge_duration
        self.recharging = False
        self.network_cells: set[tuple[int, int]] = {cell}

    @property
    def shield_active(self) -> bool:
        return self.alive and not self.recharging and self.shield > 0

    def set_network(self, cells: set[tuple[int, int]]) -> None:
        self.network_cells = set(cells) if cells else {self.cell}
        previous_max = max(1.0, self.shield_max)
        self.shield_max = self.base_shield + max(1, len(self.network_cells)) * self.shield_per_structure
        if not self.recharging:
            ratio = min(1.0, self.shield / previous_max)
            self.shield = min(self.shield_max, max(self.shield, self.shield_max * ratio))

    def absorb_damage(self, amount: float) -> float:
        if amount <= 0 or not self.shield_active:
            return amount
        absorbed = min(self.shield, amount)
        self.shield -= absorbed
        overflow = amount - absorbed
        if self.shield <= 0:
            self.shield = 0.0
            self.begin_recharge()
        return overflow

    def begin_recharge(self) -> None:
        if not self.alive:
            return
        self.recharging = True
        self.recharge_remaining = self.recharge_duration
        self.recharge_total = self.recharge_duration

    def restore_shield(self, amount: float) -> float:
        if amount <= 0 or not self.alive:
            return 0.0
        missing = max(0.0, self.shield_max - self.shield)
        actual = min(missing, amount)
        if actual <= 0:
            return 0.0
        self.shield += actual
        if self.shield >= self.shield_max * 0.96:
            self.shield = self.shield_max
            self.recharging = False
            self.recharge_remaining = 0.0
        return actual

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.set_network(game.connected_structure_cells(self.cell))
        if not self.recharging:
            return
        self.recharge_remaining = max(0.0, self.recharge_remaining - dt)
        health_cost = self.max_health * self.recharge_health_cost_ratio / self.recharge_duration * dt
        shield_gain = self.shield_max / self.recharge_duration * dt
        self.health = max(0.0, self.health - health_cost)
        self.shield = min(self.shield_max, self.shield + shield_gain)
        if self.health <= 0:
            game.destroy_structure(self)
            return
        if self.recharge_remaining <= 0:
            self.shield = self.shield_max
            self.recharging = False
            game.spawn_burst(self.pos, 18, 70)
            game.texts.append(FloatingText(pygame.Vector2(self.pos), "SHIELD", 0.8))

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, selected: bool = False, hovered: bool = False) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        size = int(config.TILE_SIZE * camera.zoom * 0.94 * hover_feedback.hover_scale(hovered))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        fill, mark = hover_feedback.inverted_pair(hovered)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))

        ring = max(5, int(size * 0.29))
        pygame.draw.circle(surface, mark, center, ring, max(1, int(camera.zoom)))
        phase = pygame.time.get_ticks() * 0.005 + self.pulse
        for index in range(4):
            angle = phase + index * math.tau / 4
            p = center + pygame.Vector2(math.cos(angle), math.sin(angle)) * ring
            pygame.draw.circle(surface, mark, p, max(1, int(2.0 * camera.zoom)))

        if self.shield_active or self.recharging:
            alpha = 34 if self.shield_active else 18
            draw_circle_alpha(surface, center, size * (0.92 + 0.04 * math.sin(phase * 2.0)), config.PALETTE.white, alpha, 1)

        if selected:
            draw_circle_alpha(surface, center, size * 0.74, config.PALETTE.white, 58, 1)

        self._draw_bars(surface, rect)

    def _draw_bars(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        if self.health < self.max_health:
            bar = pygame.Rect(rect.left, rect.top - 7, rect.width, 3)
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, self.health / self.max_health))
            pygame.draw.rect(surface, config.PALETTE.white, fill)
        if self.shield_max > 0:
            bar = pygame.Rect(rect.left, rect.bottom + 4, rect.width, 3)
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, min(1.0, self.shield / self.shield_max)))
            pygame.draw.rect(surface, config.PALETTE.white, fill)
            if self.recharging:
                draw_rect_alpha(surface, bar.inflate(2, 2), config.PALETTE.white, 34)


class Troop:
    target_class = "troop"

    def __init__(self, kind: str, pos: pygame.Vector2, station: pygame.Vector2) -> None:
        data = TROOP_DATA[kind]
        self.kind = kind
        self.display_name = TROOP_NAMES[kind]
        self.faction_type = "human"
        self.resistances = {
            "physical": 1.0,
            "fire": 1.0,
            "ice": 1.0,
            "lightning": 1.0,
            "holy": 1.0,
        }
        self.healing_effectiveness = {"holy": 1.0}
        self.pos = pygame.Vector2(pos)
        self.station = pygame.Vector2(station)
        self.attributes = data.attributes.copy()
        self.attribute_points = 0
        self.attack_stat = data.attack_stat
        self.max_health = max_health_from_stamina(self.attributes.stamina)
        self.health = self.max_health
        self.speed = data.speed
        self.acceleration = data.acceleration
        self.radius = data.radius
        self.base_range = data.attack_range
        self.station_range = data.station_range
        self.base_damage = data.damage
        self.base_fire_rate = data.fire_rate
        self.projectile_speed = data.projectile_speed
        self.vel = pygame.Vector2(0, 0)
        self.level = 1
        self.xp = 0
        self.kills = 0
        self.cooldown = random.uniform(0.0, 0.35)
        self.target = None
        self.alive = True
        self.attack_enabled = True
        self.hit_flash = 0.0
        self.taunt_pulse = 0.0
        self.stealth_time = 0.0
        self.swing_time = 0.0
        self.swing_duration = {"grunt": 0.18, "warrior": 0.24, "archer": 0.22, "cleric": 0.20, "engineer": 0.22, "wizard": 0.24}.get(kind, 0.20)
        self.swing_dir = pygame.Vector2(1, 0)
        self.swing_reach = self.radius + self.base_range
        self.support_pulse = 0.0
        self.support_target = None
        self.target_class = "worker" if kind == "grunt" else ("support" if kind in ("cleric", "engineer") else "troop")
        self.melee = MeleeAttackController(self)
        self.harvester = ResourceHarvester(self) if kind == "grunt" else None
        self.navigator = PathNavigator(self, "radius", random.uniform(0.28, 0.44))
        self.inventory = Inventory(capacity=5)
        self.equipment_slots: list[InventorySlot | None] = [None, None, None]
        self.active_item_buffs: list[ActiveItemBuff] = []
        self.abilities = AbilitySystemComponent(self)
        configure_troop_abilities(self)

    def stats(self, game=None) -> dict[str, float]:
        attributes = self.effective_attributes()
        melee_damage = melee_damage_from_strength(attributes.strength)
        magic_damage = magic_damage_from_intellect(attributes.intellect)
        damage = magic_damage if self.attack_stat == "intellect" else melee_damage
        fire_rate = attack_speed_from_agility(attributes.agility)
        movement_speed = self.speed * self.passive_multiplier("movement_speed_multiplier")
        stats = {
            "range": self.base_range,
            "damage": damage,
            "melee_damage": melee_damage,
            "magic_damage": magic_damage,
            "fire_rate": fire_rate,
            "ability_cooldown": self.ability_cooldown_multiplier(),
            "movement_speed": movement_speed,
        }
        if game is not None and hasattr(game, "item_multiplier"):
            stats["damage"] *= game.item_multiplier("troop_damage_multiplier")
            cooldown_multiplier = game.item_multiplier("troop_cooldown_multiplier")
            stats["fire_rate"] *= 1.0 / max(0.05, cooldown_multiplier)
        return stats

    def ability_cooldown_multiplier(self) -> float:
        return cooldown_multiplier_from_cunning(self.effective_attributes().cunning)

    def attribute_value(self, attribute: str) -> int:
        if attribute not in ATTRIBUTE_ORDER:
            raise ValueError(f"Unknown troop attribute '{attribute}'.")
        return int(getattr(self.effective_attributes(), attribute))

    def effective_attributes(self) -> TroopAttributes:
        values = {key: getattr(self.attributes, key) for key in ATTRIBUTE_ORDER}
        for effects in self.equipment_effects():
            bonuses = effects.get("attribute_bonuses")
            if not isinstance(bonuses, dict):
                continue
            for key, amount in bonuses.items():
                if key in values and isinstance(amount, (int, float)):
                    values[key] += int(amount)
        return TroopAttributes(**values)

    def equipment_effects(self) -> list[dict[str, object]]:
        effects: list[dict[str, object]] = []
        for slot in self.equipment_slots:
            if slot is None:
                continue
            definition = ITEM_DEFINITIONS.get(slot.item_id)
            if definition is not None:
                effects.append(definition.effects)
        return effects

    def equipment_passive_abilities(self):
        abilities = []
        for slot in self.equipment_slots:
            if slot is None:
                continue
            definition = ITEM_DEFINITIONS.get(slot.item_id)
            if definition is None:
                continue
            ability_id = f"equipment_{definition.id}"
            if definition.effects.get("passive_threat_per_second"):
                abilities.append(
                    ItemThreatAuraPassive(
                        self,
                        ability_id=ability_id,
                        name=definition.name,
                        description=definition.description,
                        effects=definition.effects,
                        tags=definition.tags,
                    )
                )
            else:
                abilities.append(
                    ItemPassiveAbility(
                        self,
                        ability_id=ability_id,
                        name=definition.name,
                        description=definition.description,
                        effects=definition.effects,
                        tags=definition.tags,
                    )
                )
        return abilities

    def passive_multiplier(self, effect: str, default: float = 1.0) -> float:
        component = getattr(self, "abilities", None)
        if component is not None:
            value, found = component.passive_multiplier(effect, default)
            if found:
                return value
        value = default
        for effects in self.equipment_effects():
            raw = effects.get(effect)
            if isinstance(raw, (int, float)) and "multiplier" in effect:
                value *= float(raw)
        return value

    def passive_value(self, effect: str, default: float = 0.0) -> float:
        value = default
        for effects in self.equipment_effects():
            raw = effects.get(effect)
            if isinstance(raw, (int, float)):
                value += float(raw)
        return value

    def max_health_from_effective_stats(self) -> float:
        stamina = self.effective_attributes().stamina
        base = max_health_from_stamina(stamina)
        bonus_per_stamina = self.passive_value("max_health_per_stamina_bonus", 0.0)
        return max(1.0, base + stamina * bonus_per_stamina)

    def add_xp(self, amount: int) -> bool:
        was_ready = self.can_level_up()
        gained = max(0, int(round(amount * self.experience_multiplier())))
        self.xp += gained
        return not was_ready and self.can_level_up()

    def can_level_up(self) -> bool:
        return self.xp >= xp_needed(self.level)

    def level_up(self) -> bool:
        cost = xp_needed(self.level)
        if self.xp < cost:
            return False
        self.xp -= cost
        self.level += 1
        self.attribute_points += 2
        return True

    def allocate_attribute(self, attribute: str) -> bool:
        if self.attribute_points <= 0 or attribute not in ATTRIBUTE_ORDER:
            return False
        old_max = self.max_health
        values = {key: getattr(self.attributes, key) for key in ATTRIBUTE_ORDER}
        values[attribute] += 1
        self.attributes = TroopAttributes(**values)
        self.attribute_points -= 1
        self._refresh_derived_stats(old_max)
        return True

    def _refresh_derived_stats(self, old_max_health: float | None = None) -> None:
        old_max = self.max_health if old_max_health is None else old_max_health
        self.max_health = self.max_health_from_effective_stats()
        if self.max_health > old_max:
            self.health += self.max_health - old_max
        if self.health > self.max_health:
            self.health = self.max_health

    def experience_multiplier(self) -> float:
        value = self.passive_multiplier("experience_multiplier")
        for buff in self.active_item_buffs:
            raw = buff.effects.get("experience_multiplier")
            if isinstance(raw, (int, float)):
                value *= float(raw)
        return value

    def consume_inventory_item(self, index: int, game) -> bool:
        slot = self.inventory.slot(index)
        if slot is None:
            return False
        definition = ITEM_DEFINITIONS.get(slot.item_id)
        if definition is None or definition.type != "consumable":
            return False

        applied = False
        instant_heal = definition.effects.get("instant_heal")
        if isinstance(instant_heal, (int, float)):
            restored = game.restore_friendly(self, float(instant_heal), source=None, reason="item", element="holy")
            applied = applied or restored > 0

        timed_effects = {
            key: value
            for key, value in definition.effects.items()
            if key not in {"instant_heal", "death_save_heal"}
        }
        if definition.duration > 0 and timed_effects:
            self.active_item_buffs.append(
                ActiveItemBuff(
                    definition.id,
                    definition.name,
                    definition.duration,
                    definition.duration,
                    timed_effects,
                    glyph=definition.glyph,
                    tags=definition.tags,
                )
            )
            applied = True

        if not applied:
            return False
        self.inventory.consume_slot(index)
        self.support_pulse = max(self.support_pulse, 0.35)
        game.spawn_hit(self.pos, 3)
        game.texts.append(FloatingText(pygame.Vector2(self.pos), definition.glyph.upper()[:5], 0.75))
        return True

    def equip_inventory_item(self, inventory_index: int, equipment_index: int | None = None) -> bool:
        slot = self.inventory.slot(inventory_index)
        if slot is None:
            return False
        definition = ITEM_DEFINITIONS.get(slot.item_id)
        if definition is None or definition.type != "equipment":
            return False
        if equipment_index is None:
            equipment_index = self.first_open_equipment_slot()
        if equipment_index is None or not 0 <= equipment_index < len(self.equipment_slots):
            return False
        if self.equipment_slots[equipment_index] is not None:
            return False
        item_id = self.inventory.consume_slot(inventory_index)
        if item_id is None:
            return False
        old_max = self.max_health
        self.equipment_slots[equipment_index] = InventorySlot(item_id, 1)
        configure_troop_abilities(self)
        self._refresh_derived_stats(old_max)
        return True

    def unequip_item(self, equipment_index: int, inventory_index: int | None = None) -> bool:
        if not 0 <= equipment_index < len(self.equipment_slots):
            return False
        slot = self.equipment_slots[equipment_index]
        if slot is None:
            return False
        if inventory_index is None:
            if not self.inventory.has_space_for(slot.item_id):
                return False
            target_added = self.inventory.add_item(slot.item_id, slot.quantity)
        else:
            if not self.inventory.has_space_for_at(inventory_index, slot.item_id):
                return False
            target_added = self.inventory.add_item_to_slot(inventory_index, slot.item_id, slot.quantity)
        if not target_added:
            return False
        old_max = self.max_health
        self.equipment_slots[equipment_index] = None
        configure_troop_abilities(self)
        self._refresh_derived_stats(old_max)
        return True

    def first_open_equipment_slot(self) -> int | None:
        for index, slot in enumerate(self.equipment_slots):
            if slot is None:
                return index
        return None

    def update_item_buffs(self, dt: float, game) -> None:
        for buff in self.active_item_buffs:
            regen = buff.effects.get("regenerate_hp_percent_per_second")
            if isinstance(regen, (int, float)) and regen > 0:
                game.restore_friendly(self, self.max_health * float(regen) * dt, source=None, reason="item_regen", element="holy")
            buff.update(dt)
        self.active_item_buffs = [buff for buff in self.active_item_buffs if buff.alive]

    def try_consume_death_save(self, game) -> bool:
        for index, slot in enumerate(self.inventory.slots):
            if slot is None:
                continue
            definition = ITEM_DEFINITIONS.get(slot.item_id)
            if definition is None:
                continue
            heal = definition.effects.get("death_save_heal")
            if not isinstance(heal, (int, float)) or heal <= 0:
                continue
            self.inventory.consume_slot(index)
            self.health = min(self.max_health, max(1.0, float(heal)))
            self.support_pulse = max(self.support_pulse, 0.55)
            game.spawn_burst(self.pos, 16, 72)
            game.texts.append(FloatingText(pygame.Vector2(self.pos), "POTION", 0.9))
            return True
        return False

    def on_damage_dealt(self, amount: float, target, game) -> None:
        if amount <= 0 or not self.alive:
            return
        heal_percent = self.passive_value("heal_self_max_hp_on_attack", 0.0)
        if heal_percent <= 0:
            return
        restored = game.restore_friendly(self, self.max_health * heal_percent, source=None, reason="lifesteal", element="holy")
        if restored > 0:
            self.support_pulse = max(self.support_pulse, 0.18)

    def set_station(self, station: pygame.Vector2, grid) -> None:
        self.station = grid.nearest_clear_world(pygame.Vector2(station), self.radius, max_radius=10)
        self.target = None
        self.navigator.clear()

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.update_item_buffs(dt, game)
        self.hit_flash = max(0.0, self.hit_flash - dt * 5.5)
        self.taunt_pulse = max(0.0, self.taunt_pulse - dt)
        self.support_pulse = max(0.0, self.support_pulse - dt)
        self.swing_time = max(0.0, self.swing_time - dt)
        self.cooldown -= dt
        self.abilities.update(dt, game)

        if self.harvester is not None:
            self.harvester.update(dt, game)
            self._finish_movement(dt, game)
            return

        if not self.attack_enabled:
            self.target = None
            if self.pos.distance_to(self.station) > 8:
                self._move_towards(self.station, dt, game, arrival_radius=8.0)
            else:
                self._decelerate(dt)
            self._finish_movement(dt, game)
            return

        self.target = self._choose_target(game)
        stats = self.stats(game)
        if self.target is not None:
            attack_range = float(stats["range"])
            if self.melee.can_reach(self.target, attack_range):
                self._decelerate(dt)
                self._attack(game, stats)
            else:
                self._move_towards(self.target.pos, dt, game, arrival_radius=self.melee.attack_distance(self.target, attack_range))
        elif self.pos.distance_to(self.station) > 8:
            self._move_towards(self.station, dt, game, arrival_radius=8.0)
        else:
            self._decelerate(dt)

        self._finish_movement(dt, game)

    def _finish_movement(self, dt: float, game) -> None:
        self.pos += self.vel * dt
        self.pos, collided = game.grid.resolve_circle_blockers(self.pos, self.radius)
        if collided:
            self.vel *= 0.35

    def _choose_target(self, game):
        if not self.abilities.has_target_priority() and self.target and self.target.alive and self._enemy_inside_station(self.target):
            return self.target

        enemies = game.targetable_enemies_near(self.station, self.station_range + 36) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(self.station, self.station_range + 36) if hasattr(game, "nearby_enemies") else game.enemies
        candidates = [
            enemy
            for enemy in enemies
            if enemy.alive and self._enemy_inside_station(enemy) and enemy.pos.distance_to(self.pos) <= self.station_range
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda enemy: self.abilities.target_priority_key(enemy, (enemy.pos.distance_to(self.pos),)))

    def _enemy_inside_station(self, enemy) -> bool:
        return enemy.pos.distance_to(self.station) <= self.station_range + enemy.radius

    def _attack(self, game, stats: dict[str, float]) -> None:
        if self.target is None:
            return
        ability = self.abilities.primary_attack()
        if ability is not None:
            ability.activate(game, self.target)

    def _move_towards(
        self,
        target: pygame.Vector2,
        dt: float,
        game,
        arrival_radius: float = 8.0,
        speed_multiplier: float = 1.0,
        separation_strength: float = 0.58,
    ) -> None:
        neighbors = None
        if separation_strength > 0:
            neighbors = (lambda: game.nearby_troops(self.pos, 52)) if hasattr(game, "nearby_troops") else game.troops
        move_speed = self.speed * self.passive_multiplier("movement_speed_multiplier")
        self.navigator.steer_to(
            target,
            dt,
            game,
            speed=move_speed * speed_multiplier,
            acceleration=self.acceleration,
            radius=self.radius,
            arrival_radius=arrival_radius,
            neighbors=neighbors,
            separation_strength=separation_strength,
            max_velocity=move_speed * speed_multiplier * 1.16,
        )

    def _decelerate(self, dt: float) -> None:
        if self.vel.length_squared() == 0:
            return
        drop = self.acceleration * 1.4 * dt
        if self.vel.length() <= drop:
            self.vel.update(0, 0)
        else:
            self.vel.scale_to_length(self.vel.length() - drop)

    def take_damage(self, amount: float, game=None) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        self.hit_flash = 1.0
        if self.health <= 0 and game is not None and self.try_consume_death_save(game):
            return False
        return self.health <= 0

    def draw_station(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        station = camera.world_to_screen(self.station, viewport)
        draw_circle_alpha(surface, station, self.station_range * camera.zoom, config.PALETTE.white, 24, 1)
        pos = camera.world_to_screen(self.pos, viewport)
        draw_line_alpha(surface, pos, station, config.PALETTE.white, 50, 1)

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, selected: bool = False, hovered: bool = False) -> None:
        screen = camera.world_to_screen(self.pos, viewport)
        scale = hover_feedback.hover_scale(hovered)
        r = max(3, int(self.radius * camera.zoom * scale * (1.0 + self.hit_flash * 0.16)))
        inverted = hovered or self.hit_flash > 0
        fill = config.PALETTE.white if inverted else config.PALETTE.black
        outline = config.PALETTE.black if inverted else config.PALETTE.white

        if selected:
            self.draw_station(surface, camera, viewport)
            for ability in self.abilities.abilities:
                ability.draw_preview(surface, camera, viewport)

        if self.can_level_up():
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.010)
            draw_circle_alpha(surface, screen, (r + 7 + pulse * 4) * camera.zoom, config.PALETTE.white, 44 + int(46 * pulse), 1)

        if self.taunt_pulse > 0:
            t = self.taunt_pulse / 0.55
            draw_circle_alpha(surface, screen, (36 + 82 * (1 - t)) * camera.zoom, config.PALETTE.white, int(90 * t), 1)

        if self.support_pulse > 0:
            t = self.support_pulse / 0.35
            draw_circle_alpha(surface, screen, (r + 18 * (1 - t)) * camera.zoom, config.PALETTE.white, int(88 * t), 1)
        if self.stealth_time > 0:
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.018)
            draw_circle_alpha(surface, screen, (r + 9 + pulse * 3) * camera.zoom, config.PALETTE.white, 38, 1)
        if not self.attack_enabled:
            draw_circle_alpha(surface, screen, (r + 8) * camera.zoom, config.PALETTE.white, 46, 1)
            draw_line_alpha(
                surface,
                screen + pygame.Vector2(-r * 0.75, r * 0.75),
                screen + pygame.Vector2(r * 0.75, -r * 0.75),
                config.PALETTE.white,
                170,
                max(1, int(camera.zoom)),
            )

        if self.kind == "grunt":
            pygame.draw.circle(surface, fill, screen, r)
            pygame.draw.circle(surface, outline, screen, r, max(1, int(camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x - r * 0.8, screen.y), (screen.x + r * 0.8, screen.y), max(1, int(camera.zoom)))
        elif self.kind == "warrior":
            points = [
                (screen.x, screen.y - r),
                (screen.x + r, screen.y - r * 0.2),
                (screen.x + r * 0.55, screen.y + r),
                (screen.x - r * 0.55, screen.y + r),
                (screen.x - r, screen.y - r * 0.2),
            ]
            pygame.draw.polygon(surface, fill, points)
            pygame.draw.polygon(surface, outline, points, max(1, int(camera.zoom)))
        elif self.kind == "archer":
            bow_r = r * 0.92
            pygame.draw.circle(surface, fill, screen, r)
            pygame.draw.circle(surface, outline, screen, r, max(1, int(camera.zoom)))
            pygame.draw.arc(
                surface,
                outline,
                pygame.Rect(screen.x - bow_r, screen.y - bow_r, bow_r * 1.35, bow_r * 2),
                -math.pi * 0.48,
                math.pi * 0.48,
                max(1, int(2 * camera.zoom)),
            )
            pygame.draw.line(surface, outline, (screen.x - r * 0.38, screen.y - r * 0.78), (screen.x - r * 0.38, screen.y + r * 0.78), max(1, int(camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x - r * 0.70, screen.y), (screen.x + r * 0.62, screen.y), max(1, int(camera.zoom)))
        elif self.kind == "cleric":
            pygame.draw.circle(surface, fill, screen, r)
            pygame.draw.circle(surface, outline, screen, r, max(1, int(camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x - r * 0.65, screen.y), (screen.x + r * 0.65, screen.y), max(1, int(2 * camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x, screen.y - r * 0.65), (screen.x, screen.y + r * 0.65), max(1, int(2 * camera.zoom)))
        elif self.kind == "rune_mage":
            points = [
                (screen.x, screen.y - r),
                (screen.x + r * 0.88, screen.y - r * 0.12),
                (screen.x + r * 0.42, screen.y + r),
                (screen.x - r * 0.42, screen.y + r),
                (screen.x - r * 0.88, screen.y - r * 0.12),
            ]
            pygame.draw.polygon(surface, fill, points)
            pygame.draw.polygon(surface, outline, points, max(1, int(camera.zoom)))
            inner = max(2, int(r * 0.42))
            pygame.draw.rect(surface, outline, pygame.Rect(screen.x - inner, screen.y - inner, inner * 2, inner * 2), max(1, int(camera.zoom)))
        elif self.kind == "wizard":
            points = [
                (screen.x, screen.y - r),
                (screen.x + r * 0.82, screen.y),
                (screen.x, screen.y + r),
                (screen.x - r * 0.82, screen.y),
            ]
            pygame.draw.polygon(surface, fill, points)
            pygame.draw.polygon(surface, outline, points, max(1, int(camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x - r * 0.45, screen.y + r * 0.35), (screen.x + r * 0.10, screen.y - r * 0.10), max(1, int(camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x + r * 0.10, screen.y - r * 0.10), (screen.x + r * 0.55, screen.y - r * 0.42), max(1, int(camera.zoom)))
        else:
            rect = pygame.Rect(0, 0, r * 2, r * 2)
            rect.center = screen
            pygame.draw.rect(surface, fill, rect)
            pygame.draw.rect(surface, outline, rect, max(1, int(camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x - r * 0.65, screen.y + r * 0.35), (screen.x + r * 0.65, screen.y - r * 0.35), max(1, int(2 * camera.zoom)))
            pygame.draw.circle(surface, outline, (int(screen.x + r * 0.58), int(screen.y - r * 0.42)), max(2, int(2.5 * camera.zoom)), max(1, int(camera.zoom)))

        self._draw_swing(surface, camera, viewport, screen, r)
        if self.harvester is not None:
            if selected:
                self.harvester.draw(surface, camera, viewport)
            self._draw_cargo(surface, screen, r, camera.zoom)
        self._draw_health(surface, screen, r)

    def _draw_cargo(self, surface: pygame.Surface, screen: pygame.Vector2, radius: int, zoom: float) -> None:
        if self.harvester is None or self.harvester.cargo <= 0:
            return
        pip_radius = max(1, int(1.7 * zoom))
        total = self.harvester.current_capacity
        start_x = screen.x - (total - 1) * pip_radius * 1.7 / 2
        y = screen.y + radius + 6 * zoom
        for index in range(total):
            x = start_x + index * pip_radius * 3.4
            color = config.PALETTE.white if index < self.harvester.cargo else config.PALETTE.line_bright
            pygame.draw.circle(surface, color, (int(x), int(y)), pip_radius, 0 if index < self.harvester.cargo else 1)

    def _draw_swing(self, surface: pygame.Surface, camera, viewport: pygame.Rect, screen: pygame.Vector2, radius: int) -> None:
        if self.swing_time <= 0:
            return
        progress = 1.0 - self.swing_time / self.swing_duration
        alpha = int(230 * (1.0 - progress))
        reach = (self.radius + self.swing_reach) * camera.zoom
        direction = pygame.Vector2(self.swing_dir)
        tangent = pygame.Vector2(-direction.y, direction.x)
        start = screen + direction * radius * 0.65

        if self.kind == "grunt":
            end = screen + direction * reach
            draw_line_alpha(surface, start, end, config.PALETTE.white, alpha, max(1, int(3 * camera.zoom)))
            tip = end - direction * 6 * camera.zoom
            pygame.draw.circle(surface, config.PALETTE.white, end, max(2, int(2.5 * camera.zoom)))
            draw_line_alpha(surface, tip + tangent * 4 * camera.zoom, end, config.PALETTE.white, alpha, 1)
            draw_line_alpha(surface, tip - tangent * 4 * camera.zoom, end, config.PALETTE.white, alpha, 1)
            return

        if self.kind == "archer":
            end = screen + direction * reach
            draw_line_alpha(surface, start, end, config.PALETTE.white, alpha, max(1, int(2 * camera.zoom)))
            for offset in (-0.10, 0.10):
                ghost = screen + (direction + tangent * offset).normalize() * reach * 0.88
                draw_line_alpha(surface, start, ghost, config.PALETTE.white, int(alpha * 0.42), 1)
            return

        if self.kind == "wizard":
            mid = screen + direction * reach * 0.56 + tangent * math.sin(progress * math.tau) * 7 * camera.zoom
            end = screen + direction * reach
            draw_line_alpha(surface, start, mid, config.PALETTE.white, alpha, max(1, int(2 * camera.zoom)))
            draw_line_alpha(surface, mid, end, config.PALETTE.white, alpha, max(1, int(2 * camera.zoom)))
            draw_circle_alpha(surface, end, 8 * camera.zoom, config.PALETTE.white, int(alpha * 0.35), 1)
            return

        sweep = (progress - 0.5) * 1.35
        blade_dir = (direction + tangent * sweep)
        if blade_dir.length_squared() > 0:
            blade_dir = blade_dir.normalize()
        outer = screen + blade_dir * reach
        inner = screen + (direction - tangent * sweep * 0.45).normalize() * radius
        draw_line_alpha(surface, inner, outer, config.PALETTE.white, alpha, max(1, int(4 * camera.zoom)))
        ghost_a = screen + (blade_dir + tangent * 0.16).normalize() * reach * 0.92
        ghost_b = screen + (blade_dir - tangent * 0.16).normalize() * reach * 0.92
        draw_line_alpha(surface, start, ghost_a, config.PALETTE.white, int(alpha * 0.45), 1)
        draw_line_alpha(surface, start, ghost_b, config.PALETTE.white, int(alpha * 0.35), 1)

    def _draw_health(self, surface: pygame.Surface, screen: pygame.Vector2, radius: int) -> None:
        if self.health >= self.max_health:
            return
        bar = pygame.Rect(0, 0, max(14, radius * 2), 3)
        bar.center = (screen.x, screen.y - radius - 7)
        pygame.draw.rect(surface, config.PALETTE.black, bar)
        fill = bar.copy()
        fill.width = int(bar.width * max(0.0, self.health / self.max_health))
        pygame.draw.rect(surface, config.PALETTE.white, fill)
