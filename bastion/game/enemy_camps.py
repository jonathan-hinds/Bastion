from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Any

import pygame

from bastion import config
from bastion.engine.drawing import draw_circle_alpha
from bastion.engine.sprites import draw_building_sprite, draw_tower_sprite
from bastion.game.elements import DEFAULT_RESISTANCES
from bastion.game.enemy_defs import ENEMY_DATA


@dataclass(frozen=True)
class EnemyBaseCampSettings:
    enabled: bool = True
    max_active_camps: int = 5
    min_night_for_escalation: int = 2
    escalation_min_seconds: float = 75.0
    escalation_max_seconds: float = 165.0
    escalation_chance: float = 0.48
    safe_radius: float = 320.0
    visible_radius: float = 230.0
    worker_enemy_id: str = "small"
    starting_gold: int = 30
    starting_minerals: int = 12
    build_interval_min: float = 12.0
    build_interval_max: float = 28.0
    extractor_search_radius: float = 760.0
    max_extractors: int = 2
    max_towers: int = 5
    core_defense_towers: int = 1
    extractor_defense_towers: int = 1
    worker_carry_capacity: int = 5
    worker_gather_rate: float = 1.75
    worker_gather_radius: float = 90.0
    raid_size_min: int = 2
    raid_size_max: int = 5
    garrison_soft_cap: int = 12

    def roll_escalation_delay(self) -> float:
        return random.uniform(self.escalation_min_seconds, self.escalation_max_seconds)

    def roll_build_interval(self) -> float:
        return random.uniform(self.build_interval_min, self.build_interval_max)


def load_enemy_base_camp_settings(raw: Any) -> EnemyBaseCampSettings:
    data = raw if isinstance(raw, dict) else {}
    return EnemyBaseCampSettings(
        enabled=bool(data.get("enabled", True)),
        max_active_camps=max(0, int(data.get("max_active_camps", 5))),
        min_night_for_escalation=max(0, int(data.get("min_night_for_escalation", 2))),
        escalation_min_seconds=max(1.0, float(data.get("escalation_min_seconds", 75.0))),
        escalation_max_seconds=max(1.0, float(data.get("escalation_max_seconds", 165.0))),
        escalation_chance=max(0.0, min(1.0, float(data.get("escalation_chance", 0.48)))),
        safe_radius=max(0.0, float(data.get("safe_radius", 320.0))),
        visible_radius=max(0.0, float(data.get("visible_radius", 230.0))),
        worker_enemy_id=str(data.get("worker_enemy_id", "small")),
        starting_gold=max(0, int(data.get("starting_gold", 30))),
        starting_minerals=max(0, int(data.get("starting_minerals", 12))),
        build_interval_min=max(1.0, float(data.get("build_interval_min", 12.0))),
        build_interval_max=max(1.0, float(data.get("build_interval_max", 28.0))),
        extractor_search_radius=max(64.0, float(data.get("extractor_search_radius", 760.0))),
        max_extractors=max(1, int(data.get("max_extractors", 2))),
        max_towers=max(0, int(data.get("max_towers", 5))),
        core_defense_towers=max(0, int(data.get("core_defense_towers", 1))),
        extractor_defense_towers=max(0, int(data.get("extractor_defense_towers", 1))),
        worker_carry_capacity=max(1, int(data.get("worker_carry_capacity", 5))),
        worker_gather_rate=max(0.1, float(data.get("worker_gather_rate", 1.75))),
        worker_gather_radius=max(16.0, float(data.get("worker_gather_radius", 90.0))),
        raid_size_min=max(0, int(data.get("raid_size_min", 2))),
        raid_size_max=max(0, int(data.get("raid_size_max", 5))),
        garrison_soft_cap=max(1, int(data.get("garrison_soft_cap", 12))),
    )


STRUCTURE_SPECS = {
    "enemy_core": {"name": "Enemy Core", "health": 320.0, "radius": 0.72, "reward": 28},
    "enemy_extractor": {"name": "Enemy Extractor", "health": 145.0, "radius": 0.52, "reward": 10},
    "enemy_house": {"name": "Enemy Housing", "health": 155.0, "radius": 0.50, "reward": 8},
    "enemy_barracks": {"name": "Enemy Barracks", "health": 235.0, "radius": 0.54, "reward": 16},
    "enemy_tower": {"name": "Enemy Watchtower", "health": 175.0, "radius": 0.50, "reward": 14},
}

STRUCTURE_COSTS = {
    "enemy_extractor": {"gold": 18, "minerals": 0},
    "enemy_house": {"gold": 24, "minerals": 8},
    "enemy_barracks": {"gold": 38, "minerals": 16},
    "enemy_tower": {"gold": 28, "minerals": 14},
}

UNIT_COSTS = {
    "small": {"gold": 9, "minerals": 0},
    "medium": {"gold": 16, "minerals": 5},
    "ranged": {"gold": 20, "minerals": 9},
    "large": {"gold": 34, "minerals": 18},
}

HOUSE_CAPACITY = 4
CORE_CAPACITY = 3
ENEMY_BUILDING_SPRITE_WORLD_SIZE = config.TILE_SIZE * 1.125
ENEMY_CORE_SPRITE_WORLD_SIZE = 96.0


@dataclass(slots=True)
class EnemyArcaneLink:
    core: "EnemyCampStructure"
    structure: "EnemyCampStructure"
    path: list[tuple[int, int]]
    phase: float


@dataclass(frozen=True, slots=True)
class EnemyBuildIntent:
    kind: str
    anchor: pygame.Vector2 | None = None
    cell: tuple[int, int] | None = None
    deposit: object | None = None


class EnemyBaseBuildPlanner:
    def __init__(self, base: "EnemyBaseCamp") -> None:
        self.base = base

    def next_intent(self, game) -> EnemyBuildIntent | None:
        base = self.base
        if base.core is None:
            return None

        if base.core_defense_count() < base.settings.core_defense_towers:
            return EnemyBuildIntent("enemy_tower", anchor=base.center)

        if not base.live_structures("enemy_extractor"):
            deposit = base.deposit_for_extractor(game)
            return EnemyBuildIntent("enemy_extractor", cell=deposit.cell, deposit=deposit) if deposit is not None else None

        extractor = base.extractor_needing_tower()
        if extractor is not None and len(base.live_structures("enemy_tower")) < base.settings.max_towers:
            return EnemyBuildIntent("enemy_tower", anchor=extractor.pos)

        if not base.live_structures("enemy_house"):
            return EnemyBuildIntent("enemy_house", anchor=base.center)

        if not base.live_structures("enemy_barracks"):
            return EnemyBuildIntent("enemy_barracks", anchor=base.center)

        if base._unit_supply() >= base._unit_capacity() - 1:
            return EnemyBuildIntent("enemy_house", anchor=base.center)

        if len(base.live_structures("enemy_extractor")) < base.settings.max_extractors and random.random() < 0.28:
            deposit = base.deposit_for_extractor(game)
            if deposit is not None:
                return EnemyBuildIntent("enemy_extractor", cell=deposit.cell, deposit=deposit)

        if len(base.live_structures("enemy_tower")) < base._desired_tower_count():
            return EnemyBuildIntent("enemy_tower", anchor=base.center)

        return None


class EnemyCampStructure:
    target_class = "enemy_structure"
    faction_type = "enemy"
    combat_role = "structure"
    is_ranged = False
    shape = "square"
    tags = ("structure", "camp")
    loot: dict[str, float] = {"drop_chance": 0.02}

    def __init__(self, kind: str, cell: tuple[int, int], grid, base: "EnemyBaseCamp", deposit=None) -> None:
        spec = STRUCTURE_SPECS[kind]
        self.kind = kind
        self.display_name = str(spec["name"])
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.base = base
        self.deposit = deposit
        self.max_health = float(spec["health"])
        self.health = self.max_health
        self.radius = config.TILE_SIZE * float(spec["radius"])
        self.collision_radius = min(self.radius * 0.72, config.TILE_SIZE * 0.38)
        self.reward = int(spec["reward"])
        self.arcane_capacity = config.ARCANE_CORE_CAPACITY if kind == "enemy_core" else 0
        self.resistances = dict(DEFAULT_RESISTANCES)
        self.spawn_group = "enemy_camp"
        self.behavior = "structure"
        self.alive = True
        self.vel = pygame.Vector2(0, 0)
        self.mass = 8.0
        self.hit_flash = 0.0
        self.phase = random.random() * math.tau
        self.attack_cooldown = random.uniform(0.15, 0.8)
        self.attack_range = 215.0 if kind == "enemy_tower" else 0.0
        self.damage = 8.0 if kind == "enemy_tower" else 0.0
        self.fire_rate = 0.85
        self.visual_target = None
        self.visual_aim_direction = pygame.Vector2(1, 0)
        self.shoot_flash_timer = 0.0
        self.recoil_timer = 0.0
        self.recoil_duration = 0.12
        self.burn_time = 0.0
        self.burn_dps = 0.0
        self.burn_owner = None
        self.slow_time = 0.0
        self.slow_multiplier = 1.0
        self.attack_slow_time = 0.0
        self.attack_slow_multiplier = 1.0
        self.stun_time = 0.0
        self.taunt_target = None
        self.taunt_time = 0.0
        self.damage_vulnerability_time = 0.0
        self.damage_vulnerability_multiplier = 1.0
        self.damage_vulnerability_source_classes: set[str] = set()
        self.last_hit_by = None
        if self.deposit is not None:
            self.deposit.claimed_by = self

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.hit_flash = max(0.0, self.hit_flash - dt * 5.5)
        self.shoot_flash_timer = max(0.0, self.shoot_flash_timer - dt)
        self.recoil_timer = max(0.0, self.recoil_timer - dt)
        if self.burn_time > 0:
            self.burn_time = max(0.0, self.burn_time - dt)
            game.damage_enemy(self, self.burn_dps * dt, self.burn_owner, quiet=True, element="fire")
            if not self.alive:
                return
        if self.slow_time > 0:
            self.slow_time = max(0.0, self.slow_time - dt)
        else:
            self.slow_multiplier = 1.0
        if self.attack_slow_time > 0:
            self.attack_slow_time = max(0.0, self.attack_slow_time - dt)
        else:
            self.attack_slow_multiplier = 1.0
        self.stun_time = max(0.0, self.stun_time - dt)
        self.taunt_time = max(0.0, self.taunt_time - dt)
        if self.taunt_time <= 0 or not getattr(self.taunt_target, "alive", False):
            self.taunt_target = None
        if self.damage_vulnerability_time > 0:
            self.damage_vulnerability_time = max(0.0, self.damage_vulnerability_time - dt)
        else:
            self.damage_vulnerability_multiplier = 1.0
            self.damage_vulnerability_source_classes.clear()
        if self.kind == "enemy_tower" and self.stun_time <= 0:
            self._update_visual_target(game)
            self._update_tower(dt, game)

    def _update_tower(self, dt: float, game) -> None:
        self.attack_cooldown = max(0.0, self.attack_cooldown - dt * self.attack_slow_multiplier)
        if self.attack_cooldown > 0:
            return
        target = self._tower_target(game)
        if target is None:
            return
        self.attack_cooldown = 1.0 / max(0.05, self.fire_rate)
        self.signal_shot(target)
        from bastion.game.entities import EnemyProjectile

        game.enemy_projectiles.append(EnemyProjectile(pygame.Vector2(self.pos), target, 315.0, self.damage, owner=self))
        if hasattr(game, "spawn_hit"):
            game.spawn_hit(self.pos, 1)

    def signal_shot(self, target=None) -> None:
        self.visual_target = target
        if target is not None and getattr(target, "alive", False):
            direction = pygame.Vector2(target.pos) - self.pos
            if direction.length_squared() > 0.01:
                self.visual_aim_direction = direction
        self.shoot_flash_timer = 0.055
        self.recoil_timer = self.recoil_duration

    def _update_visual_target(self, game) -> None:
        target = self._tower_target(game)
        if target is None:
            self.visual_target = None
            return
        self.visual_target = target
        direction = pygame.Vector2(target.pos) - self.pos
        if direction.length_squared() > 0.01:
            self.visual_aim_direction = direction

    def _tower_target(self, game):
        if getattr(self.taunt_target, "alive", False) and self.taunt_target.pos.distance_to(self.pos) <= self.attack_range + getattr(self.taunt_target, "radius", 0.0):
            return self.taunt_target
        candidates = []
        point = pygame.Vector2(self.pos)
        for troop in getattr(game, "troops", []):
            if getattr(troop, "alive", False) and troop.pos.distance_to(point) <= self.attack_range + troop.radius:
                candidates.append(troop)
        structures = [tower for tower in getattr(game, "towers", []) if getattr(tower, "alive", False)]
        structures.extend(building for building in getattr(game, "buildings", []) if getattr(building, "alive", False))
        for structure in structures:
            if structure.pos.distance_to(point) <= self.attack_range + getattr(structure, "radius", 0.0):
                candidates.append(structure)
        for core in getattr(game, "core_targets", []):
            if getattr(core, "alive", False) and core.pos.distance_to(point) <= self.attack_range + core.radius:
                candidates.append(core)
        if not candidates:
            return None
        return min(candidates, key=lambda target: target.pos.distance_to(point))

    def take_damage(self, amount: float, owner=None) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        self.last_hit_by = owner
        self.hit_flash = 1.0
        return self.health <= 0

    def apply_knockback(self, amount: float, source_pos: pygame.Vector2) -> None:
        return

    def apply_slow(self, multiplier: float, duration: float, attack_multiplier: float | None = None) -> None:
        self.slow_multiplier = min(self.slow_multiplier, max(0.05, float(multiplier)))
        self.slow_time = max(self.slow_time, duration)
        if attack_multiplier is not None:
            self.attack_slow_multiplier = min(self.attack_slow_multiplier, max(0.05, float(attack_multiplier)))
            self.attack_slow_time = max(self.attack_slow_time, duration)

    def apply_stun(self, duration: float) -> None:
        self.stun_time = max(self.stun_time, duration)

    def apply_taunt(self, target, duration: float) -> None:
        if not getattr(target, "alive", False):
            return
        self.taunt_target = target
        self.taunt_time = max(self.taunt_time, duration)

    def apply_burn(
        self,
        dps: float,
        duration: float,
        owner,
        spread_radius: float = 0.0,
        spread_falloff: float = 0.5,
        can_spread: bool = True,
    ) -> None:
        self.burn_dps = max(self.burn_dps, dps)
        self.burn_time = max(self.burn_time, duration)
        self.burn_owner = owner

    def apply_damage_vulnerability(self, multiplier: float, duration: float, source_classes: tuple[str, ...] = ("troop", "tower")) -> None:
        self.damage_vulnerability_multiplier = max(self.damage_vulnerability_multiplier, multiplier)
        self.damage_vulnerability_time = max(self.damage_vulnerability_time, duration)
        self.damage_vulnerability_source_classes.update(source_classes)

    def damage_taken_multiplier(self, source) -> float:
        if self.damage_vulnerability_time <= 0:
            return 1.0
        source_class = "tower" if source.__class__.__name__ == "Tower" else str(getattr(source, "target_class", ""))
        return self.damage_vulnerability_multiplier if source_class in self.damage_vulnerability_source_classes else 1.0

    def on_killed(self, game, owner=None) -> None:
        self.release_placement(game)
        self.base.on_structure_killed(self, game)

    def destroy(self, game) -> None:
        if not self.alive:
            return
        self.alive = False
        self.release_placement(game)

    def release_placement(self, game) -> None:
        self.base.release_arcane_link(self)
        if getattr(self.deposit, "claimed_by", None) is self:
            self.deposit.claimed_by = None
        if game.grid.towers.get(self.cell) is self:
            game.grid.remove_tower(self.cell)

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        tile = config.TILE_SIZE * camera.zoom
        sprite_rect = self._draw_sprite_structure(surface, camera, viewport)
        if sprite_rect is not None:
            if self.health < self.max_health:
                bar = pygame.Rect(sprite_rect.left, sprite_rect.top - 6, sprite_rect.width, max(2, int(3 * camera.zoom)))
                pygame.draw.rect(surface, config.PALETTE.black, bar)
                health_fill = bar.copy()
                health_fill.width = int(bar.width * max(0.0, self.health / self.max_health))
                pygame.draw.rect(surface, config.PALETTE.white, health_fill)
            return

        visual_scale = 1.82 if self.kind == "enemy_core" else 0.9
        size = max(6, int(tile * visual_scale * (1.0 + self.hit_flash * 0.12)))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        gold_style = self.kind == "enemy_extractor" and getattr(self.deposit, "kind", "") == "gold"
        fill = config.PALETTE.white if gold_style or self.hit_flash > 0 else config.PALETTE.black
        mark = config.PALETTE.black if gold_style or self.hit_flash > 0 else config.PALETTE.white
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))
        if self.kind == "enemy_core":
            self._draw_core(surface, center, rect, mark, camera.zoom)
        elif self.kind == "enemy_extractor":
            self._draw_extractor(surface, center, rect, mark, camera.zoom)
        elif self.kind == "enemy_house":
            self._draw_house(surface, rect, mark, camera.zoom)
        elif self.kind == "enemy_barracks":
            inner = rect.inflate(-max(4, int(size * 0.34)), -max(4, int(size * 0.34)))
            pygame.draw.rect(surface, mark, inner, max(1, int(camera.zoom)))
            pygame.draw.line(surface, mark, rect.midleft, rect.midright, max(1, int(camera.zoom)))
        elif self.kind == "enemy_tower":
            draw_circle_alpha(surface, center, tile * 0.36, mark, 46, max(1, int(camera.zoom)))
            pygame.draw.line(surface, mark, rect.midtop, rect.midbottom, max(1, int(camera.zoom)))
            pygame.draw.line(surface, mark, rect.midleft, rect.midright, max(1, int(camera.zoom)))
        if self.health < self.max_health:
            bar = pygame.Rect(rect.left, rect.top - 6, rect.width, max(2, int(3 * camera.zoom)))
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            health_fill = bar.copy()
            health_fill.width = int(bar.width * max(0.0, self.health / self.max_health))
            pygame.draw.rect(surface, config.PALETTE.white, health_fill)

    def _draw_sprite_structure(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> pygame.Rect | None:
        if self.kind == "enemy_tower":
            target = self.visual_target if getattr(self.visual_target, "alive", False) else None
            recoil = self.recoil_timer / max(0.001, self.recoil_duration)
            return draw_tower_sprite(
                surface,
                camera,
                viewport,
                self,
                "enemy_tower",
                world_size=ENEMY_BUILDING_SPRITE_WORLD_SIZE,
                target_pos=getattr(target, "pos", None),
                recoil=recoil,
                flash=self.shoot_flash_timer > 0.0 or self.hit_flash > 0.0,
            )

        variant = None
        if self.kind == "enemy_extractor":
            variant = "gold" if getattr(self.deposit, "kind", "") == "gold" else "mineral"
        world_size = ENEMY_CORE_SPRITE_WORLD_SIZE if self.kind == "enemy_core" else ENEMY_BUILDING_SPRITE_WORLD_SIZE
        rect = draw_building_sprite(
            surface,
            camera,
            viewport,
            self,
            self.kind,
            variant=variant,
            world_size=world_size,
            white=self.hit_flash > 0.0,
        )
        if rect is not None and self.kind == "enemy_core":
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.004 + self.phase)
            draw_circle_alpha(surface, camera.world_to_screen(self.pos, viewport), rect.width * (0.40 + pulse * 0.05), config.PALETTE.white, 30, max(1, int(camera.zoom)))
        return rect

    def _draw_core(self, surface: pygame.Surface, center: pygame.Vector2, rect: pygame.Rect, mark, zoom: float) -> None:
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.004 + self.phase)
        draw_circle_alpha(surface, center, rect.width * (0.46 + pulse * 0.05), mark, 42, max(1, int(zoom)))
        diamond = [
            (rect.centerx, rect.top + rect.height * 0.16),
            (rect.right - rect.width * 0.16, rect.centery),
            (rect.centerx, rect.bottom - rect.height * 0.16),
            (rect.left + rect.width * 0.16, rect.centery),
        ]
        pygame.draw.polygon(surface, mark, diamond, max(1, int(2 * zoom)))

    def _draw_extractor(self, surface: pygame.Surface, center: pygame.Vector2, rect: pygame.Rect, mark, zoom: float) -> None:
        inner = rect.inflate(-max(5, int(rect.width * 0.34)), -max(5, int(rect.height * 0.34)))
        pygame.draw.rect(surface, mark, inner, max(1, int(zoom)))
        pygame.draw.line(surface, mark, rect.midleft, rect.midright, max(1, int(zoom)))
        pygame.draw.line(surface, mark, rect.midtop, rect.midbottom, max(1, int(zoom)))
        if getattr(self.deposit, "active", False):
            draw_circle_alpha(surface, center, rect.width * 0.42, mark, 28, 1)

    def _draw_house(self, surface: pygame.Surface, rect: pygame.Rect, mark, zoom: float) -> None:
        roof = [
            (rect.left + rect.width * 0.14, rect.centery),
            (rect.centerx, rect.top + rect.height * 0.16),
            (rect.right - rect.width * 0.14, rect.centery),
        ]
        pygame.draw.polygon(surface, mark, roof, max(1, int(2 * zoom)))
        door = pygame.Rect(0, 0, max(3, int(rect.width * 0.18)), max(5, int(rect.height * 0.27)))
        door.midbottom = (rect.centerx, rect.bottom - max(2, int(rect.height * 0.08)))
        pygame.draw.rect(surface, mark, door, max(1, int(zoom)))


class EnemyResourceHarvester:
    def __init__(self, base: "EnemyBaseCamp", settings: EnemyBaseCampSettings) -> None:
        self.base = base
        self.settings = settings
        self.carry_capacity = settings.worker_carry_capacity
        self.gather_rate = settings.worker_gather_rate
        self.cargo = 0
        self.cargo_kind: str | None = None
        self.progress = 0.0
        self.target_extractor: EnemyCampStructure | None = None
        self.work_angle = random.random() * math.tau
        self.fx_timer = 0.0

    def update(self, dt: float, game, enemy) -> bool:
        if getattr(enemy, "behavior", "") != "ambient":
            return False
        if not self.base.alive:
            return False
        target = enemy.aggro.choose_target(game) if hasattr(enemy, "aggro") else None
        if target is not None and getattr(target, "target_class", "") != "core" and enemy._target_within_leash(target):
            return False
        extractors = self.base.live_structures("enemy_extractor")
        if not extractors:
            return False
        self.fx_timer = max(0.0, self.fx_timer - dt)
        if self.cargo >= self.carry_capacity:
            self._deliver(dt, game, enemy)
            return True
        extractor = self._extractor(extractors)
        if extractor is None or getattr(extractor.deposit, "active", False) is False:
            return False
        self.target_extractor = extractor
        gather_point = self._gather_point(game, enemy, extractor)
        work_tolerance = max(18.0, min(self.settings.worker_gather_radius, enemy.radius * 2.8))
        if enemy.pos.distance_to(gather_point) > work_tolerance:
            enemy._move_to(gather_point, dt, game, arrival_radius=max(12.0, enemy.radius * 1.6))
            return True
        enemy._decelerate(dt)
        enemy._apply_velocity(dt, game)
        self.progress += self.gather_rate * dt
        gathered = min(int(self.progress), self.carry_capacity - self.cargo)
        if gathered <= 0:
            return True
        self.progress -= gathered
        taken = extractor.deposit.harvest(gathered)
        if taken <= 0:
            return True
        self.cargo += taken
        self.cargo_kind = getattr(extractor.deposit, "kind", "mineral")
        if self.fx_timer <= 0:
            self.fx_timer = 0.2
            game.spawn_hit(extractor.pos, 1)
        return True

    def _deliver(self, dt: float, game, enemy) -> None:
        core = self.base.core
        if core is None:
            return
        arrival = core.radius + enemy.radius + 8.0
        if enemy.pos.distance_to(core.pos) > arrival:
            enemy._move_to(core.pos, dt, game, arrival_radius=arrival)
            return
        enemy._decelerate(dt)
        enemy._apply_velocity(dt, game)
        if self.cargo <= 0:
            return
        self.base.add_resource(self.cargo_kind or "mineral", self.cargo, game, enemy.pos)
        self.cargo = 0
        self.cargo_kind = None
        self.progress = 0.0

    def _extractor(self, extractors: list[EnemyCampStructure]) -> EnemyCampStructure | None:
        valid = [extractor for extractor in extractors if getattr(extractor.deposit, "active", False) and extractor.deposit.amount > 0]
        if not valid:
            return None
        if self.target_extractor in valid:
            return self.target_extractor
        core = self.base.core
        anchor = core.pos if core is not None else self.base.center
        return min(valid, key=lambda extractor: extractor.pos.distance_to(anchor))

    def _gather_point(self, game, enemy, extractor: EnemyCampStructure) -> pygame.Vector2:
        ring = extractor.radius + enemy.radius + 8.0
        for step in range(10):
            offset = (step + 1) // 2
            sign = -1 if step % 2 == 0 else 1
            angle = self.work_angle + sign * offset * (math.tau / 10.0)
            direction = pygame.Vector2(math.cos(angle), math.sin(angle))
            point = extractor.pos + direction * ring
            if game.grid.circle_clear(point, enemy.collision_radius):
                return point
        return game.grid.nearest_clear_world(extractor.pos + pygame.Vector2(ring, 0), enemy.collision_radius, max_radius=5)


class EnemyBaseCamp:
    def __init__(self, center: pygame.Vector2, mob_camp, settings: EnemyBaseCampSettings, template_id: str = "") -> None:
        self.center = pygame.Vector2(center)
        self.mob_camp = mob_camp
        self.settings = settings
        self.template_id = template_id
        self.structures: list[EnemyCampStructure] = []
        self.arcane_links: list[EnemyArcaneLink] = []
        self.planner = EnemyBaseBuildPlanner(self)
        self.gold = settings.starting_gold
        self.minerals = settings.starting_minerals
        self.build_timer = settings.roll_build_interval()
        self.training_timer = random.uniform(5.0, 12.0)
        self.last_raid_night = 0
        self.active = False

    @property
    def alive(self) -> bool:
        return self.active and self.core is not None and self.core.alive

    @property
    def core(self) -> EnemyCampStructure | None:
        for structure in self.structures:
            if structure.kind == "enemy_core" and structure.alive:
                return structure
        return None

    def start(self, game) -> bool:
        core_cell = self._find_build_cell(game, self.center, max_radius=4)
        if core_cell is None:
            return False
        core = self._place_structure(game, "enemy_core", core_cell)
        if core is None:
            return False
        self.active = True
        self.center = pygame.Vector2(core.pos)
        self._assign_existing_worker(game)
        self._try_build_tower(game, self.center)
        game.spawn_burst(core.pos, 18, 76)
        return True

    def update(self, dt: float, game) -> None:
        if not self.active:
            return
        self._prune()
        if self.core is None:
            self.active = False
            return
        self._ensure_workers(game)
        self._update_construction(dt, game)
        self._update_training(dt, game)
        self._update_raids(game)

    def add_resource(self, kind: str, amount: int, game, pos: pygame.Vector2 | None = None) -> None:
        amount = max(0, int(amount))
        if amount <= 0:
            return
        if kind == "gold":
            self.gold += amount
        else:
            self.minerals += amount
        if pos is not None:
            from bastion.game.entities import FloatingText

            suffix = "G" if kind == "gold" else "M"
            game.texts.append(FloatingText(pygame.Vector2(pos), f"+{amount}{suffix}", 0.55))

    def live_structures(self, kind: str | None = None) -> list[EnemyCampStructure]:
        return [structure for structure in self.structures if structure.alive and (kind is None or structure.kind == kind)]

    def arcane_core_load(self, core: EnemyCampStructure | None = None) -> int:
        self._prune_arcane_links()
        source = self.core if core is None else core
        if source is None:
            return 0
        return sum(1 for link in self.arcane_links if link.core is source)

    def arcane_link_for(self, structure: EnemyCampStructure) -> EnemyArcaneLink | None:
        for link in self.arcane_links:
            if link.structure is structure:
                return link
        return None

    def release_arcane_link(self, structure: EnemyCampStructure) -> None:
        self.arcane_links = [link for link in self.arcane_links if link.structure is not structure]

    def reserve_arcane_link(self, structure: EnemyCampStructure, core: EnemyCampStructure, path: list[tuple[int, int]]) -> None:
        self.release_arcane_link(structure)
        self.arcane_links.append(EnemyArcaneLink(core, structure, path, random.random()))

    def arcane_source_for_cell(self, game, cell: tuple[int, int]) -> tuple[EnemyCampStructure | None, list[tuple[int, int]], str]:
        self._prune_arcane_links()
        core = self.core
        if core is None:
            return None, [], "NO CORE"
        if self.arcane_core_load(core) >= core.arcane_capacity:
            return None, [], "ARCANE FULL"
        path = game._arcane_path(core.cell, cell)
        if not path:
            return None, [], "NO ARCANE PATH"
        return core, path, ""

    def draw_arcane_network(self, surface: pygame.Surface, camera, viewport: pygame.Rect, game) -> None:
        self._prune_arcane_links()
        for link in self.arcane_links:
            if not link.core.alive or not link.structure.alive:
                continue
            game._draw_arcane_path_trace(surface, camera, viewport, link.path, link.phase)

    def on_structure_killed(self, structure: EnemyCampStructure, game) -> None:
        if structure.kind == "enemy_core":
            for other in list(self.structures):
                if other is not structure:
                    other.destroy(game)
            self.active = False
        self._prune()

    def _prune(self) -> None:
        self.structures = [structure for structure in self.structures if structure.alive]
        self._prune_arcane_links()
        self.mob_camp.enemies = [enemy for enemy in self.mob_camp.enemies if getattr(enemy, "alive", False)]

    def _prune_arcane_links(self) -> None:
        self.arcane_links = [
            link
            for link in self.arcane_links
            if link.core.alive and link.structure.alive and link.core in self.structures and link.structure in self.structures
        ]

    def _update_construction(self, dt: float, game) -> None:
        self.build_timer = max(0.0, self.build_timer - dt)
        if self.build_timer > 0.0:
            return
        self.build_timer = self.settings.roll_build_interval()
        self._build_next(game)

    def _build_next(self, game) -> bool:
        intent = self.planner.next_intent(game)
        if intent is None:
            return False
        if intent.kind == "enemy_extractor" and intent.cell is not None:
            return self._try_build_at(game, intent.kind, intent.cell, deposit=intent.deposit)
        if intent.anchor is None:
            return False
        if intent.kind == "enemy_tower":
            return self._try_build_tower(game, intent.anchor)
        return self._try_build_structure(game, intent.kind, intent.anchor)

    def _try_build_extractor(self, game) -> bool:
        deposit = self.deposit_for_extractor(game)
        if deposit is None:
            return False
        return self._try_build_at(game, "enemy_extractor", deposit.cell, deposit=deposit)

    def _try_build_structure(self, game, kind: str, anchor: pygame.Vector2) -> bool:
        cell = self._find_build_cell(game, anchor, max_radius=5)
        if cell is None:
            return False
        return self._try_build_at(game, kind, cell)

    def _try_build_tower(self, game, anchor: pygame.Vector2) -> bool:
        cell = self._find_build_cell(game, anchor, max_radius=4)
        if cell is None:
            return False
        return self._try_build_at(game, "enemy_tower", cell)

    def _try_build_at(self, game, kind: str, cell: tuple[int, int], deposit=None) -> bool:
        cost = STRUCTURE_COSTS.get(kind, {})
        gold_cost = int(cost.get("gold", 0))
        mineral_cost = int(cost.get("minerals", 0))
        if not self._can_pay(gold_cost, mineral_cost):
            return False
        arcane: tuple[EnemyCampStructure, list[tuple[int, int]]] | None = None
        if kind != "enemy_core":
            core, path, _reason = self.arcane_source_for_cell(game, cell)
            if core is None or not path:
                return False
            arcane = (core, path)
        structure = self._place_structure(game, kind, cell, deposit, arcane=arcane)
        if structure is None:
            return False
        self._pay(gold_cost, mineral_cost)
        game.spawn_burst(structure.pos, 10, 52)
        return True

    def _place_structure(
        self,
        game,
        kind: str,
        cell: tuple[int, int],
        deposit=None,
        arcane: tuple[EnemyCampStructure, list[tuple[int, int]]] | None = None,
    ) -> EnemyCampStructure | None:
        if kind != "enemy_core" and arcane is None:
            core, path, _reason = self.arcane_source_for_cell(game, cell)
            if core is None or not path:
                return None
            arcane = (core, path)
        structure = EnemyCampStructure(kind, cell, game.grid, self, deposit=deposit)
        ok, _reason = game.grid.try_add_tower(cell, structure)
        if not ok:
            structure.release_placement(game)
            return None
        self.structures.append(structure)
        if arcane is not None:
            core, path = arcane
            self.reserve_arcane_link(structure, core, path)
        game.enemies.append(structure)
        game._spatial_ready = False
        return structure

    def deposit_for_extractor(self, game):
        core = self.core
        anchor = core.pos if core is not None else self.center
        candidates = []
        for deposit in getattr(game, "resource_deposits", []):
            if not (
                deposit.active
                and deposit.amount > 0
                and getattr(deposit, "claimed_by", None) is None
                and not game.is_core_reserve(deposit.cell)
                and game.grid.buildable(deposit.cell)
                and deposit.pos.distance_to(anchor) <= self.settings.extractor_search_radius
            ):
                continue
            _core, path, _reason = self.arcane_source_for_cell(game, deposit.cell)
            if not path:
                continue
            candidates.append((deposit, path))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (len(item[1]), item[0].pos.distance_to(anchor), 0 if getattr(item[0], "kind", "") == "mineral" else 1))
        return candidates[0][0]

    def _find_build_cell(self, game, anchor: pygame.Vector2, max_radius: int = 5) -> tuple[int, int] | None:
        center = game.grid.cell_from_world(anchor)
        candidates: list[tuple[int, int]] = []
        for radius in range(1, max_radius + 1):
            ring = []
            cx, cy = center
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    cell = (cx + dx, cy + dy)
                    if not self._build_cell_ok(game, cell):
                        continue
                    ring.append(cell)
            random.shuffle(ring)
            candidates.extend(ring)
            if candidates:
                break
        if not candidates and self._build_cell_ok(game, center):
            candidates.append(center)
        candidates.sort(key=lambda cell: game.grid.world_center(cell).distance_to(anchor))
        return candidates[0] if candidates else None

    def _build_cell_ok(self, game, cell: tuple[int, int]) -> bool:
        return (
            game.grid.buildable(cell)
            and not game.is_core_reserve(cell)
            and game.active_resource_at(cell) is None
        )

    def core_defense_count(self) -> int:
        return sum(1 for tower in self.live_structures("enemy_tower") if tower.pos.distance_to(self.center) <= config.TILE_SIZE * 4.5)

    def extractor_needing_tower(self) -> EnemyCampStructure | None:
        towers = self.live_structures("enemy_tower")
        for extractor in self.live_structures("enemy_extractor"):
            nearby = sum(1 for tower in towers if tower.pos.distance_to(extractor.pos) <= config.TILE_SIZE * 4.5)
            if nearby < self.settings.extractor_defense_towers:
                return extractor
        return None

    def _desired_tower_count(self) -> int:
        count = self.settings.core_defense_towers + len(self.live_structures("enemy_extractor")) * self.settings.extractor_defense_towers
        if self.live_structures("enemy_barracks"):
            count += 1
        return min(self.settings.max_towers, count)

    def _ensure_workers(self, game) -> None:
        desired = self._worker_reserve_count()
        while len(self._workers()) < desired:
            if self._assign_existing_worker(game):
                continue
            if not self._spawn_worker(game):
                break

    def _assign_existing_worker(self, game) -> bool:
        worker_id = self._worker_id()
        for enemy in self.mob_camp.enemies:
            if (
                getattr(enemy, "alive", False)
                and getattr(enemy, "kind", "") == worker_id
                and getattr(enemy, "camp_worker", None) is None
                and getattr(enemy, "behavior", "") == "ambient"
            ):
                self._make_worker(enemy)
                return True
        return False

    def _spawn_worker(self, game) -> bool:
        if self._unit_supply() >= self._unit_capacity():
            return False
        worker_id = self._worker_id()
        cost = UNIT_COSTS.get(worker_id, UNIT_COSTS["small"])
        if not self._can_pay(cost["gold"], cost["minerals"]):
            return False
        enemy = self._spawn_unit(game, worker_id, behavior="ambient")
        if enemy is None:
            return False
        self._pay(cost["gold"], cost["minerals"])
        self._make_worker(enemy)
        return True

    def _make_worker(self, enemy) -> None:
        enemy.camp_worker = EnemyResourceHarvester(self, self.settings)
        enemy.camp_base = self
        enemy.home_pos = pygame.Vector2(self.center)
        enemy.leash_radius = max(float(getattr(enemy, "leash_radius", 0.0)), 520.0)
        enemy.spawn_group = "enemy_camp"
        enemy.behavior = "ambient"

    def _workers(self) -> list:
        return [
            enemy
            for enemy in self.mob_camp.enemies
            if getattr(enemy, "alive", False)
            and getattr(enemy, "camp_base", None) is self
            and getattr(enemy, "camp_worker", None) is not None
            and getattr(enemy, "behavior", "") == "ambient"
        ]

    def _worker_reserve_count(self) -> int:
        return min(2, max(1, len(self.live_structures("enemy_extractor"))))

    def _worker_id(self) -> str:
        return self.settings.worker_enemy_id if self.settings.worker_enemy_id in ENEMY_DATA else ("small" if "small" in ENEMY_DATA else next(iter(ENEMY_DATA)))

    def _update_training(self, dt: float, game) -> None:
        if not self.live_structures("enemy_barracks"):
            return
        wave_manager = getattr(game, "wave_manager", None)
        if wave_manager is not None and getattr(wave_manager, "is_night", False):
            return
        self.training_timer = max(0.0, self.training_timer - dt)
        if self.training_timer > 0.0:
            return
        self.training_timer = random.uniform(8.0, 17.0)
        if self._unit_supply() >= min(self._unit_capacity(), self.settings.garrison_soft_cap):
            return
        kind = self._pick_unit_kind(game)
        cost = UNIT_COSTS.get(kind, UNIT_COSTS["small"])
        if not self._can_pay(cost["gold"], cost["minerals"]):
            return
        enemy = self._spawn_unit(game, kind, behavior="ambient")
        if enemy is None:
            return
        self._pay(cost["gold"], cost["minerals"])

    def _update_raids(self, game) -> None:
        wave_manager = getattr(game, "wave_manager", None)
        if wave_manager is None or not getattr(wave_manager, "is_night", False):
            return
        night = max(1, int(getattr(wave_manager, "night_number", 1)))
        if self.last_raid_night == night:
            return
        self.last_raid_night = night
        if not self.live_structures("enemy_barracks"):
            return
        workers = self._workers()
        workers.sort(
            key=lambda enemy: (
                -int(getattr(getattr(enemy, "camp_worker", None), "cargo", 0)),
                enemy.pos.distance_to(self.center),
            )
        )
        worker_reserve = set(workers[: self._worker_reserve_count()])
        candidates = [
            enemy
            for enemy in self.mob_camp.enemies
            if getattr(enemy, "alive", False)
            and getattr(enemy, "camp_base", None) is self
            and getattr(enemy, "behavior", "") == "ambient"
            and enemy not in worker_reserve
        ]
        random.shuffle(candidates)
        for enemy in candidates:
            self._launch_unit(enemy)

    def _launch_unit(self, enemy) -> None:
        if getattr(enemy, "camp_worker", None) is not None:
            enemy.camp_worker = None
        enemy.behavior = "assault"
        enemy.spawn_group = "wave"
        if hasattr(enemy, "aggro"):
            enemy.aggro.current_target = None
            enemy.aggro.retarget_timer = 0.0

    def _spawn_unit(self, game, kind: str, behavior: str = "ambient"):
        if kind not in ENEMY_DATA:
            kind = "small" if "small" in ENEMY_DATA else next(iter(ENEMY_DATA))
        anchor = self._spawn_anchor()
        pos = self._spawn_position(game, anchor)
        if pos is None:
            return None
        wave = self._difficulty_wave(game)
        enemy = game.spawn_enemy_at(
            kind,
            pos,
            wave,
            behavior=behavior,
            home_pos=self.center,
            patrol_points=self._patrol_points(game),
            leash_radius=560.0,
            spawn_group="wave" if behavior == "assault" else "enemy_camp",
        )
        enemy.camp_base = self
        self.mob_camp.enemies.append(enemy)
        if behavior == "assault":
            self._launch_unit(enemy)
        return enemy

    def _spawn_anchor(self) -> pygame.Vector2:
        barracks = self.live_structures("enemy_barracks")
        if barracks:
            return barracks[0].pos
        core = self.core
        return core.pos if core is not None else self.center

    def _spawn_position(self, game, anchor: pygame.Vector2) -> pygame.Vector2 | None:
        radius = 11.0
        for _ in range(24):
            angle = random.random() * math.tau
            distance = random.uniform(config.TILE_SIZE * 0.8, config.TILE_SIZE * 2.7)
            pos = pygame.Vector2(anchor.x + math.cos(angle) * distance, anchor.y + math.sin(angle) * distance)
            pos = game.grid.nearest_clear_world(pos, radius, max_radius=6)
            if game.grid.circle_clear(pos, radius):
                return pos
        return game.grid.nearest_clear_world(anchor, radius, max_radius=8)

    def _patrol_points(self, game) -> list[pygame.Vector2]:
        points = [pygame.Vector2(self.center)]
        for index in range(3):
            angle = random.random() * math.tau + index * math.tau / 3
            distance = random.uniform(70.0, 180.0)
            points.append(game.grid.nearest_clear_world(self.center + pygame.Vector2(math.cos(angle), math.sin(angle)) * distance, 10.0, max_radius=6))
        return points

    def _unit_capacity(self) -> int:
        return CORE_CAPACITY + len(self.live_structures("enemy_house")) * HOUSE_CAPACITY

    def _unit_supply(self) -> int:
        return sum(
            1
            for enemy in self.mob_camp.enemies
            if getattr(enemy, "alive", False) and getattr(enemy, "camp_base", None) is self
        )

    def _can_pay(self, gold_cost: int, mineral_cost: int) -> bool:
        return self.gold + self.minerals >= max(0, gold_cost) + max(0, mineral_cost)

    def _pay(self, gold_cost: int, mineral_cost: int) -> None:
        remaining_gold = max(0, gold_cost)
        paid = min(self.gold, remaining_gold)
        self.gold -= paid
        remaining_gold -= paid
        if remaining_gold > 0:
            self.minerals = max(0, self.minerals - remaining_gold)

        remaining_minerals = max(0, mineral_cost)
        paid = min(self.minerals, remaining_minerals)
        self.minerals -= paid
        remaining_minerals -= paid
        if remaining_minerals > 0:
            self.gold = max(0, self.gold - remaining_minerals)

    def _pick_unit_kind(self, game) -> str:
        wave = self._difficulty_wave(game)
        choices: list[tuple[str, float]] = [("small", max(1.0, 5.0 - wave * 0.08))]
        if "medium" in ENEMY_DATA:
            choices.append(("medium", 2.2 + wave * 0.07))
        if "ranged" in ENEMY_DATA and wave >= 2:
            choices.append(("ranged", 1.0 + wave * 0.055))
        if "large" in ENEMY_DATA and wave >= 4:
            choices.append(("large", max(0.4, (wave - 3) * 0.08)))
        choices = [(kind, weight) for kind, weight in choices if kind in ENEMY_DATA and weight > 0]
        if not choices:
            return next(iter(ENEMY_DATA))
        total = sum(weight for _kind, weight in choices)
        roll = random.uniform(0.0, total)
        upto = 0.0
        for kind, weight in choices:
            upto += weight
            if roll <= upto:
                return kind
        return choices[-1][0]

    def _difficulty_wave(self, game) -> int:
        wave_manager = getattr(game, "wave_manager", None)
        if wave_manager is None:
            return 1
        night = int(getattr(wave_manager, "night_number", getattr(wave_manager, "wave_number", 0)))
        if not getattr(wave_manager, "is_night", False):
            night += 1
        return max(1, night)
