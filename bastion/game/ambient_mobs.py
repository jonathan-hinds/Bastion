from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import random
from typing import Any, TypeVar

import pygame

from bastion.game.enemy_camps import EnemyBaseCamp, EnemyBaseCampSettings, load_enemy_base_camp_settings
from bastion.game.enemy_defs import ENEMY_DATA


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "ambient_mobs.json"
T = TypeVar("T")


@dataclass(frozen=True)
class MobSpawnSettings:
    min_core_distance: float
    min_camp_distance: float
    max_attempts: int


@dataclass(frozen=True)
class MobRespawnSettings:
    min_seconds: float
    max_seconds: float
    blocked_retry_seconds: float
    safe_radius: float


@dataclass(frozen=True)
class MobTemplate:
    template_id: str
    weight: float
    count_min: int
    count_max: int
    cluster_radius: float
    patrol_radius: float
    leash_radius: float
    enemy_weights: dict[str, float]

    def pick_enemy(self) -> str:
        choices = [(kind, weight) for kind, weight in self.enemy_weights.items() if kind in ENEMY_DATA and weight > 0]
        if not choices:
            return "small" if "small" in ENEMY_DATA else next(iter(ENEMY_DATA))
        return _weighted_choice(choices)


@dataclass(frozen=True)
class AmbientMobSettings:
    initial_camps: int
    spawn: MobSpawnSettings
    respawn: MobRespawnSettings
    base_camps: EnemyBaseCampSettings
    templates: tuple[MobTemplate, ...]

    def pick_template(self) -> MobTemplate:
        return _weighted_choice([(template, template.weight) for template in self.templates])


@dataclass
class MobCamp:
    center: pygame.Vector2
    template: MobTemplate
    enemies: list[object] = field(default_factory=list)
    respawn_timer: float = 0.0
    base: EnemyBaseCamp | None = None
    escalation_timer: float = 0.0
    escalation_delay: float = 0.0

    def update(self, dt: float, game, manager: "AmbientMobManager") -> None:
        self.enemies = [enemy for enemy in self.enemies if getattr(enemy, "alive", False)]
        if self.base is not None:
            self.base.update(dt, game)
            if not self.base.alive:
                self.base = None
                self.escalation_timer = 0.0
                self.escalation_delay = manager.roll_base_escalation_delay()
            return

        if self.enemies:
            manager.update_base_escalation(dt, game, self)
            return

        if self.respawn_timer <= 0.0:
            self.respawn_timer = manager.roll_respawn_delay()
            return

        self.respawn_timer = max(0.0, self.respawn_timer - dt)
        if self.respawn_timer > 0.0:
            return

        if manager.respawn_blocked(game, self):
            self.respawn_timer = manager.settings.respawn.blocked_retry_seconds
            return
        self.spawn(game, manager)

    def spawn(self, game, manager: "AmbientMobManager") -> None:
        self.enemies = []
        count = random.randint(self.template.count_min, self.template.count_max)
        night = max(1, int(getattr(game.wave_manager, "night_number", getattr(game.wave_manager, "wave_number", 0))))
        patrol_points = manager.patrol_points(game, self.center, self.template.patrol_radius)
        for _ in range(count):
            kind = self.template.pick_enemy()
            pos = manager.spawn_position_near(game, self.center, self.template.cluster_radius)
            if pos is None:
                continue
            enemy = game.spawn_enemy_at(
                kind,
                pos,
                night,
                behavior="ambient",
                home_pos=self.center,
                patrol_points=patrol_points,
                leash_radius=self.template.leash_radius,
                spawn_group="ambient",
            )
            self.enemies.append(enemy)
        self.respawn_timer = 0.0
        self.escalation_timer = 0.0
        self.escalation_delay = manager.roll_base_escalation_delay()


class AmbientMobManager:
    def __init__(self, grid, settings: AmbientMobSettings | None = None) -> None:
        self.grid = grid
        self.settings = load_ambient_mob_settings() if settings is None else settings
        self.camps: list[MobCamp] = []

    def seed_initial_camps(self, game) -> None:
        self.camps = []
        for _ in range(self.settings.initial_camps):
            center = self.find_camp_center(game)
            if center is None:
                break
            camp = MobCamp(center=center, template=self.settings.pick_template())
            camp.spawn(game, self)
            self.camps.append(camp)

    def update(self, dt: float, game) -> None:
        for camp in self.camps:
            camp.update(dt, game, self)

    def active_enemy_count(self) -> int:
        return sum(1 for camp in self.camps for enemy in camp.enemies if getattr(enemy, "alive", False))

    def active_base_count(self) -> int:
        return sum(1 for camp in self.camps if camp.base is not None and camp.base.alive)

    def draw_base_arcane_networks(self, surface: pygame.Surface, camera, viewport: pygame.Rect, game) -> None:
        for camp in self.camps:
            if camp.base is not None and camp.base.alive:
                camp.base.draw_arcane_network(surface, camera, viewport, game)

    def roll_respawn_delay(self) -> float:
        settings = self.settings.respawn
        return random.uniform(settings.min_seconds, settings.max_seconds)

    def roll_base_escalation_delay(self) -> float:
        return self.settings.base_camps.roll_escalation_delay()

    def update_base_escalation(self, dt: float, game, camp: MobCamp) -> None:
        settings = self.settings.base_camps
        if not settings.enabled or camp.base is not None:
            return
        wave_manager = getattr(game, "wave_manager", None)
        current_night = int(getattr(wave_manager, "night_number", getattr(wave_manager, "wave_number", 0))) if wave_manager is not None else 0
        if current_night < settings.min_night_for_escalation:
            return
        if self.active_base_count() >= settings.max_active_camps:
            return
        if not camp.enemies:
            return
        if self.base_escalation_blocked(game, camp):
            camp.escalation_timer = max(0.0, camp.escalation_timer - dt * 0.45)
            if camp.escalation_delay <= 0.0:
                camp.escalation_delay = self.roll_base_escalation_delay()
            return
        if camp.escalation_delay <= 0.0:
            camp.escalation_delay = self.roll_base_escalation_delay()
        camp.escalation_timer += dt
        if camp.escalation_timer < camp.escalation_delay:
            return
        camp.escalation_timer = 0.0
        camp.escalation_delay = self.roll_base_escalation_delay()
        if random.random() > settings.escalation_chance:
            return
        base = EnemyBaseCamp(camp.center, camp, settings, template_id=camp.template.template_id)
        if base.start(game):
            camp.base = base

    def base_escalation_blocked(self, game, camp: MobCamp) -> bool:
        settings = self.settings.base_camps
        fog = getattr(game, "fog", None)
        if fog is not None and getattr(fog, "enabled", False) and fog.is_visible_world(camp.center, settings.visible_radius):
            return True
        radius = settings.safe_radius
        for troop in getattr(game, "troops", []):
            if getattr(troop, "alive", False) and troop.pos.distance_to(camp.center) <= radius + troop.radius:
                return True
        structures = [tower for tower in getattr(game, "towers", []) if getattr(tower, "alive", False)]
        structures.extend(building for building in getattr(game, "buildings", []) if getattr(building, "alive", False))
        for structure in structures:
            if structure.pos.distance_to(camp.center) <= radius + getattr(structure, "radius", 0):
                return True
        return False

    def respawn_blocked(self, game, camp: MobCamp) -> bool:
        radius = self.settings.respawn.safe_radius
        for troop in getattr(game, "troops", []):
            if getattr(troop, "alive", False) and troop.pos.distance_to(camp.center) <= radius + troop.radius:
                return True
        for core in getattr(game, "core_targets", []):
            if getattr(core, "alive", False) and core.pos.distance_to(camp.center) <= radius + getattr(core, "radius", 0):
                return True
        structures = [tower for tower in getattr(game, "towers", []) if getattr(tower, "alive", False)]
        structures.extend(building for building in getattr(game, "buildings", []) if getattr(building, "alive", False))
        for structure in structures:
            if structure.pos.distance_to(camp.center) <= radius + getattr(structure, "radius", 0):
                return True
        return False

    def find_camp_center(self, game) -> pygame.Vector2 | None:
        spawn_settings = self.settings.spawn
        for _ in range(spawn_settings.max_attempts):
            cell = (
                random.randrange(4, max(5, self.grid.width - 4)),
                random.randrange(4, max(5, self.grid.height - 4)),
            )
            if not self.grid.passable(cell):
                continue
            if getattr(game, "active_resource_at", lambda item: None)(cell) is not None:
                continue
            center = self.grid.world_center(cell)
            if not self._far_from_cores(game, center, spawn_settings.min_core_distance):
                continue
            if not self._far_from_camps(center, spawn_settings.min_camp_distance):
                continue
            if not self.grid.circle_clear(center, 18):
                continue
            return center
        return None

    def spawn_position_near(self, game, center: pygame.Vector2, radius: float) -> pygame.Vector2 | None:
        for _ in range(32):
            angle = random.random() * math.tau
            distance = radius * math.sqrt(random.random())
            pos = pygame.Vector2(center.x + math.cos(angle) * distance, center.y + math.sin(angle) * distance)
            pos = self.grid.nearest_clear_world(pos, 14, max_radius=6)
            if not self.grid.circle_clear(pos, 14):
                continue
            if any(troop.alive and troop.pos.distance_to(pos) < troop.radius + 22 for troop in getattr(game, "troops", [])):
                continue
            return pos
        return self.grid.nearest_clear_world(center, 14, max_radius=8)

    def patrol_points(self, game, center: pygame.Vector2, radius: float) -> list[pygame.Vector2]:
        points = [pygame.Vector2(center)]
        for index in range(3):
            angle = random.random() * math.tau + index * math.tau / 3
            distance = random.uniform(radius * 0.45, radius)
            candidate = pygame.Vector2(center.x + math.cos(angle) * distance, center.y + math.sin(angle) * distance)
            points.append(self.grid.nearest_clear_world(candidate, 14, max_radius=8))
        return points

    def _far_from_cores(self, game, center: pygame.Vector2, min_distance: float) -> bool:
        for core in getattr(game, "core_targets", []):
            if getattr(core, "alive", True) and core.pos.distance_to(center) < min_distance:
                return False
        return True

    def _far_from_camps(self, center: pygame.Vector2, min_distance: float) -> bool:
        return all(camp.center.distance_to(center) >= min_distance for camp in self.camps)


def load_ambient_mob_settings(path: Path | None = None) -> AmbientMobSettings:
    path = DATA_PATH if path is None else path
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Ambient mob data must be a JSON object.")

    spawn_data = _as_dict(raw.get("spawn"))
    respawn_data = _as_dict(raw.get("respawn"))
    templates = tuple(_load_templates(raw.get("templates", [])))
    if not templates:
        templates = (
            MobTemplate(
                template_id="default",
                weight=1.0,
                count_min=3,
                count_max=5,
                cluster_radius=96.0,
                patrol_radius=180.0,
                leash_radius=420.0,
                enemy_weights={"small": 4.0, "medium": 2.0},
            ),
        )

    return AmbientMobSettings(
        initial_camps=max(0, int(raw.get("initial_camps", 10))),
        spawn=MobSpawnSettings(
            min_core_distance=max(0.0, float(spawn_data.get("min_core_distance", 700.0))),
            min_camp_distance=max(0.0, float(spawn_data.get("min_camp_distance", 340.0))),
            max_attempts=max(1, int(spawn_data.get("max_attempts", 800))),
        ),
        respawn=MobRespawnSettings(
            min_seconds=max(1.0, float(respawn_data.get("min_seconds", 90.0))),
            max_seconds=max(1.0, float(respawn_data.get("max_seconds", 150.0))),
            blocked_retry_seconds=max(1.0, float(respawn_data.get("blocked_retry_seconds", 8.0))),
            safe_radius=max(0.0, float(respawn_data.get("safe_radius", 300.0))),
        ),
        base_camps=load_enemy_base_camp_settings(raw.get("base_camps", {})),
        templates=templates,
    )


def _load_templates(records: Any) -> list[MobTemplate]:
    if not isinstance(records, list):
        return []
    templates: list[MobTemplate] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        enemy_weights = _as_dict(record.get("enemy_weights"))
        weights: dict[str, float] = {}
        for kind, weight in enemy_weights.items():
            try:
                weights[str(kind)] = float(weight)
            except (TypeError, ValueError):
                continue
        templates.append(
            MobTemplate(
                template_id=str(record.get("id", f"mob_{len(templates)}")),
                weight=max(0.0, float(record.get("weight", 1.0))),
                count_min=max(1, int(record.get("count_min", 3))),
                count_max=max(1, int(record.get("count_max", 6))),
                cluster_radius=max(16.0, float(record.get("cluster_radius", 96.0))),
                patrol_radius=max(16.0, float(record.get("patrol_radius", 180.0))),
                leash_radius=max(64.0, float(record.get("leash_radius", 420.0))),
                enemy_weights=weights,
            )
        )
    for index, template in enumerate(templates):
        if template.count_max < template.count_min:
            templates[index] = MobTemplate(
                template.template_id,
                template.weight,
                template.count_max,
                template.count_min,
                template.cluster_radius,
                template.patrol_radius,
                template.leash_radius,
                template.enemy_weights,
            )
    return [template for template in templates if template.weight > 0]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _weighted_choice(choices: list[tuple[T, float]]) -> T:
    total = sum(max(0.0, weight) for _item, weight in choices)
    if total <= 0:
        return choices[0][0]
    roll = random.uniform(0.0, total)
    upto = 0.0
    for item, weight in choices:
        upto += max(0.0, weight)
        if roll <= upto:
            return item
    return choices[-1][0]
