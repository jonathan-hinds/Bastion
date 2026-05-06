from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import pygame

from bastion import config
from bastion.engine.sprites import ROW_SOUTH, attack_directional_row, directional_row, enemy_attack_sprite_sheet, enemy_sprite_sheet
from bastion.engine.drawing import draw_circle_alpha, draw_line_alpha, draw_rect_alpha
from bastion.engine import hover_feedback
from bastion.game.abilities import (
    AbilitySystemComponent,
    configure_enemy_abilities,
    configure_tower_abilities,
    elemental_effect_for_projectile,
)
from bastion.game.aggro import (
    AggroComponent,
    ambient_melee_aggro_profile,
    ambient_ranged_aggro_profile,
    melee_aggro_profile,
    ranged_aggro_profile,
)
from bastion.game.combat import MeleeAttackController
from bastion.game.combat_stats import (
    CombatAttributes,
    allocate_attribute_budget,
    attack_speed_from_agility,
    cooldown_multiplier_from_cunning,
    magic_damage_from_intellect,
    max_health_from_stamina,
    melee_damage_from_strength,
)
from bastion.game.elements import ElementalEffect
from bastion.game.enemy_defs import ENEMY_DATA, get_enemy_def
from bastion.game.navigation import PathNavigator
from bastion.game.tower_defs import SPECIALIZATIONS, stats_for, tower_name, xp_needed
from bastion.game.tower_mods import TOWER_MODS


ENEMY_ATTRIBUTE_WEIGHTS: dict[str, dict[str, float]] = {
    "small": {"stamina": 0.22, "intellect": 0.08, "strength": 0.24, "agility": 0.30, "cunning": 0.16},
    "medium": {"stamina": 0.34, "intellect": 0.08, "strength": 0.30, "agility": 0.14, "cunning": 0.14},
    "large": {"stamina": 0.46, "intellect": 0.04, "strength": 0.34, "agility": 0.08, "cunning": 0.08},
    "ranged": {"stamina": 0.20, "intellect": 0.34, "strength": 0.08, "agility": 0.18, "cunning": 0.20},
}

FIRE_OUTER = (255, 86, 30)
FIRE_CORE = (255, 220, 122)
ICE_OUTER = (92, 214, 255)
ICE_CORE = (232, 255, 255)


@dataclass
class Particle:
    pos: pygame.Vector2
    vel: pygame.Vector2
    life: float
    radius: float
    color: tuple[int, int, int] = config.PALETTE.white
    max_life: float = field(init=False)

    def __post_init__(self) -> None:
        self.max_life = self.life

    def update(self, dt: float) -> None:
        self.pos += self.vel * dt
        self.vel *= 0.90 ** (dt * 60)
        self.life -= dt

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if self.life <= 0:
            return
        t = max(0.0, self.life / self.max_life)
        screen = camera.world_to_screen(self.pos, viewport)
        draw_circle_alpha(surface, screen, self.radius * camera.zoom * (0.6 + t), self.color, int(210 * t))


@dataclass
class Beam:
    start: pygame.Vector2
    end: pygame.Vector2
    life: float = 0.12
    width: int = 2
    max_life: float = 0.12

    def update(self, dt: float) -> None:
        self.life -= dt

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if self.life <= 0:
            return
        alpha = int(230 * max(0.0, self.life / self.max_life))
        start = camera.world_to_screen(self.start, viewport)
        end = camera.world_to_screen(self.end, viewport)
        draw_line_alpha(surface, start, end, config.PALETTE.white, alpha, max(1, int(self.width * camera.zoom)))


@dataclass
class FloatingText:
    pos: pygame.Vector2
    text: str
    life: float = 0.8
    vel: pygame.Vector2 = field(default_factory=lambda: pygame.Vector2(0, -34))
    max_life: float = 0.8

    def update(self, dt: float) -> None:
        self.pos += self.vel * dt
        self.life -= dt

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font) -> None:
        if self.life <= 0:
            return
        alpha = int(255 * max(0.0, self.life / self.max_life))
        image = font.render(self.text, True, config.PALETTE.white)
        image.set_alpha(alpha)
        screen = camera.world_to_screen(self.pos, viewport)
        surface.blit(image, image.get_rect(center=(int(screen.x), int(screen.y))))


@dataclass
class DamagePulse:
    pos: pygame.Vector2
    kind: str
    radius: float
    life: float = 0.34
    max_life: float = 0.34

    def update(self, dt: float) -> None:
        self.life -= dt

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if self.life <= 0:
            return
        progress = 1.0 - max(0.0, self.life / self.max_life)
        alpha = int(210 * max(0.0, self.life / self.max_life))
        screen = camera.world_to_screen(self.pos, viewport)
        zoom = camera.zoom
        if self.kind == "aoe":
            outer = self.radius * (0.82 + progress * 0.18) * zoom
            inner = max(8.0, self.radius * 0.34 * zoom)
            draw_circle_alpha(surface, screen, outer, config.PALETTE.white, alpha, max(1, int(2 * zoom)))
            draw_circle_alpha(surface, screen, inner, config.PALETTE.white, int(alpha * 0.42), 1)
            for index in range(8):
                angle = index / 8 * math.tau + progress * 0.22
                direction = pygame.Vector2(math.cos(angle), math.sin(angle))
                start = screen + direction * outer * 0.74
                end = screen + direction * outer
                draw_line_alpha(surface, start, end, config.PALETTE.white, int(alpha * 0.72), max(1, int(zoom)))
            return

        if self.kind == "multi":
            orbit = (10.0 + progress * 9.0) * zoom
            for index in range(3):
                angle = index / 3 * math.tau + progress * 0.8
                center = screen + pygame.Vector2(math.cos(angle), math.sin(angle)) * orbit
                draw_circle_alpha(surface, center, (9 + progress * 6) * zoom, config.PALETTE.white, int(alpha * 0.74), 1)
                draw_line_alpha(surface, screen, center, config.PALETTE.white, int(alpha * 0.34), 1)
            draw_circle_alpha(surface, screen, (13 + progress * 7) * zoom, config.PALETTE.white, int(alpha * 0.55), 1)
            return

        size = (13.0 + progress * 8.0) * zoom
        draw_circle_alpha(surface, screen, size, config.PALETTE.white, alpha, max(1, int(2 * zoom)))
        draw_line_alpha(surface, screen + pygame.Vector2(-size, 0), screen + pygame.Vector2(size, 0), config.PALETTE.white, int(alpha * 0.72), 1)
        draw_line_alpha(surface, screen + pygame.Vector2(0, -size), screen + pygame.Vector2(0, size), config.PALETTE.white, int(alpha * 0.72), 1)


class Enemy:
    def __init__(
        self,
        kind: str,
        pos: pygame.Vector2,
        wave: int,
        behavior: str = "assault",
        home_pos: pygame.Vector2 | None = None,
        patrol_points: list[pygame.Vector2] | None = None,
        leash_radius: float | None = None,
        spawn_group: str = "wave",
    ) -> None:
        data = get_enemy_def(kind)
        scale = 1.14 + wave * 0.22
        self.kind = kind
        self.behavior = behavior
        self.spawn_group = spawn_group
        self.display_name = data["name"]
        self.faction_type = data["faction_type"]
        self.combat_role = data["combat_role"]
        self.attack_stat = "intellect" if self.combat_role == "ranged" else "strength"
        self.shape = data["shape"]
        self.tags = list(data.get("tags", ()))
        self.resistances = dict(data["resistances"])
        self.pos = pygame.Vector2(pos)
        self.max_health = float(data["health"] * scale)
        self.health = self.max_health
        self.speed = float(data["speed"] * (1.0 + min(0.18, wave * 0.006)))
        self.acceleration = float(data["accel"])
        self.mass = float(data["mass"])
        self.radius = float(data["radius"])
        self.collision_radius = min(self.radius * 0.72, config.TILE_SIZE * 0.38)
        self.reward = max(1, int(round(data["reward"] * 1.65 + wave * 0.35)))
        self.loot = dict(data.get("loot", {}))
        self.attributes: CombatAttributes | None = None
        self.damage = int(data["damage"] + wave * 0.5)
        self.attack_range = float(data.get("attack_range", 0))
        self.fire_rate = float(data.get("fire_rate", 0.9))
        self.projectile_speed = float(data.get("projectile_speed", 260))
        self.attack_cooldown = random.uniform(0.0, 0.4)
        self.vel = pygame.Vector2(0, 0)
        self.alive = True
        self.hit_flash = 0.0
        self.slow_time = 0.0
        self.slow_multiplier = 1.0
        self.attack_slow_time = 0.0
        self.attack_slow_multiplier = 1.0
        self.stun_time = 0.0
        self.burn_time = 0.0
        self.burn_dps = 0.0
        self.burn_owner = None
        self.burn_can_spread = False
        self.burn_spread_timer = 0.0
        self.burn_spread_radius = 0.0
        self.burn_spread_falloff = 0.5
        self.damage_vulnerability_time = 0.0
        self.damage_vulnerability_multiplier = 1.0
        self.damage_vulnerability_source_classes: set[str] = set()
        self.last_hit_by = None
        self.taunt_target = None
        self.taunt_time = 0.0
        self.phase = random.random() * math.tau
        self.home_pos = pygame.Vector2(home_pos if home_pos is not None else pos)
        points = patrol_points if patrol_points else [pygame.Vector2(self.home_pos)]
        self.patrol_points = [pygame.Vector2(point) for point in points] or [pygame.Vector2(self.home_pos)]
        self.patrol_index = random.randrange(len(self.patrol_points))
        self.patrol_wait = random.uniform(0.1, 1.2)
        self.leash_radius = float(leash_radius if leash_radius is not None else 480.0)
        self.swing_time = 0.0
        self.swing_duration = {"diamond": 0.16, "square": 0.20, "octagon": 0.28}.get(self.shape, 0.18)
        self.swing_dir = pygame.Vector2(1, 0)
        self.swing_reach = self.radius + self.attack_range
        self.sprite_anim_time = random.random() * 0.6
        self.sprite_facing = pygame.Vector2(0, 1)
        self.sprite_target = None
        self.attack_anim_phase = ""
        self.attack_anim_time = 0.0
        self.attack_anim_duration = 0.0
        self.attack_anim_direction = pygame.Vector2(0, 1)
        self.melee = MeleeAttackController(self, "attack_cooldown")
        self.navigator = PathNavigator(self, "collision_radius", random.uniform(0.28, 0.46))
        self.aggro = AggroComponent(self, self._aggro_profile())
        self.abilities = AbilitySystemComponent(self)
        configure_enemy_abilities(self)

    @property
    def is_ranged(self) -> bool:
        return self.combat_role == "ranged"

    @property
    def is_ambient(self) -> bool:
        return self.behavior == "ambient"

    def stats(self, game=None) -> dict[str, float]:
        if self.attributes is None:
            return {
                "range": self.attack_range,
                "damage": float(self.damage),
                "melee_damage": float(self.damage),
                "magic_damage": float(self.damage),
                "fire_rate": float(self.fire_rate),
                "ability_cooldown": 1.0,
            }

        melee_damage = melee_damage_from_strength(self.attributes.strength)
        magic_damage = magic_damage_from_intellect(self.attributes.intellect)
        damage = magic_damage if self.attack_stat == "intellect" else melee_damage
        return {
            "range": self.attack_range,
            "damage": damage,
            "melee_damage": melee_damage,
            "magic_damage": magic_damage,
            "fire_rate": attack_speed_from_agility(self.attributes.agility),
            "ability_cooldown": cooldown_multiplier_from_cunning(self.attributes.cunning),
        }

    def apply_expedition_stat_budget(self, budget: int | float) -> None:
        weights = ENEMY_ATTRIBUTE_WEIGHTS.get(self.kind)
        if weights is None:
            weights = ENEMY_ATTRIBUTE_WEIGHTS["ranged"] if self.is_ranged else ENEMY_ATTRIBUTE_WEIGHTS["medium"]
        self.attributes = allocate_attribute_budget(budget, weights)
        self.attack_stat = "intellect" if self.is_ranged else "strength"
        health_fraction = max(0.0, min(1.0, self.health / max(1.0, self.max_health)))
        self.max_health = max(self.max_health, max_health_from_stamina(self.attributes.stamina))
        self.health = max(1.0, self.max_health * health_fraction)
        stats = self.stats()
        self.damage = float(stats["damage"])
        self.fire_rate = float(stats["fire_rate"])
        for ability in getattr(self.abilities, "abilities", ()):
            if getattr(ability, "ability_id", "") == "melee_attack":
                ability.fixed_cooldown = None
                ability.use_fire_rate = True

    def _aggro_profile(self):
        if self.is_ambient:
            return ambient_ranged_aggro_profile() if self.is_ranged else ambient_melee_aggro_profile()
        return ranged_aggro_profile() if self.is_ranged else melee_aggro_profile()

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.sprite_target = None

        if self.burn_time > 0:
            self.burn_time -= dt
            game.damage_enemy(self, self.burn_dps * dt, self.burn_owner, quiet=True, element="fire")
            if not self.alive:
                return
            self.burn_spread_timer -= dt
            if self.burn_can_spread and self.burn_spread_timer <= 0:
                self.burn_can_spread = False
                if hasattr(game, "spread_burn"):
                    game.spread_burn(self)

        self.hit_flash = max(0.0, self.hit_flash - dt * 5.5)
        if self.slow_time > 0:
            self.slow_time -= dt
        else:
            self.slow_multiplier = 1.0
        if self.attack_slow_time > 0:
            self.attack_slow_time -= dt
        else:
            self.attack_slow_multiplier = 1.0

        self.stun_time = max(0.0, self.stun_time - dt)
        if self.damage_vulnerability_time > 0:
            self.damage_vulnerability_time -= dt
        else:
            self.damage_vulnerability_multiplier = 1.0
            self.damage_vulnerability_source_classes.clear()
        self.attack_cooldown = max(0.0, self.attack_cooldown - dt * self.attack_slow_multiplier)
        self.taunt_time = max(0.0, self.taunt_time - dt)
        self.swing_time = max(0.0, self.swing_time - dt)
        self.aggro.update(dt)
        self._update_attack_animation(dt)
        self._update_sprite_animation(dt)

        if self.stun_time > 0:
            self._decelerate(dt)
            self._apply_velocity(dt, game)
            return

        taunt_target = self.active_taunt_target()
        if taunt_target is not None:
            if self.is_ranged:
                self._update_ranged_assault(dt, game, taunt_target)
            else:
                self._update_melee_assault(dt, game, taunt_target)
            return

        camp_worker = getattr(self, "camp_worker", None)
        if camp_worker is not None and camp_worker.update(dt, game, self):
            return

        if self.is_ambient:
            self._update_ambient(dt, game)
            return

        aggro_target = self.aggro.choose_target(game)
        if self.is_ranged:
            target = aggro_target if aggro_target is not None and getattr(aggro_target, "target_class", "") != "core" else game.find_enemy_attack_target(self.pos)
            if target is None:
                target = aggro_target or game.core_target_for(self.pos)
            if target is not None:
                self._update_ranged_assault(dt, game, target)
                return

        target = aggro_target or game.core_target_for(self.pos)
        if getattr(target, "target_class", "") == "core":
            self._update_core_assault(dt, game, target)
        else:
            self._update_melee_assault(dt, game, target)

    def _update_ambient(self, dt: float, game) -> None:
        target = self.aggro.choose_target(game)
        if target is not None and getattr(target, "target_class", "") != "core":
            if self._target_within_leash(target):
                if self.is_ranged:
                    self._update_ranged_assault(dt, game, target)
                else:
                    self._update_melee_assault(dt, game, target)
                return
            self.aggro.threat.pop(target, None)
            self.aggro.current_target = None

        self._update_patrol(dt, game)

    def _target_within_leash(self, target) -> bool:
        return target.pos.distance_to(self.home_pos) <= self.leash_radius + getattr(target, "radius", 0.0)

    def _update_patrol(self, dt: float, game) -> None:
        if self.pos.distance_to(self.home_pos) > self.leash_radius * 1.08:
            self._move_to(self.home_pos, dt, game, arrival_radius=18.0)
            return

        if not self.patrol_points:
            self._decelerate(dt)
            self._apply_velocity(dt, game)
            return

        target = self.patrol_points[self.patrol_index % len(self.patrol_points)]
        if self.pos.distance_to(target) <= 18.0:
            self.patrol_wait = max(self.patrol_wait, random.uniform(0.65, 1.65))
            self.patrol_index = (self.patrol_index + 1) % len(self.patrol_points)
        if self.patrol_wait > 0.0:
            self.patrol_wait = max(0.0, self.patrol_wait - dt)
            self._decelerate(dt)
            self._apply_velocity(dt, game)
            return
        self._move_to(target, dt, game, arrival_radius=14.0)

    def _update_core_assault(self, dt: float, game, target) -> None:
        wall_target = game.find_enemy_wall_target(self, target) if hasattr(game, "find_enemy_wall_target") else None
        if wall_target is not None:
            self._update_melee_assault(dt, game, wall_target)
            return
        self.sprite_target = target
        if self.melee.can_reach(target, self.attack_range):
            self._decelerate(dt)
            ability = self.abilities.primary_attack()
            if ability is not None:
                ability.activate(game, target)
        else:
            self._move_to_core(target, dt, game)

    def _update_melee_assault(self, dt: float, game, target) -> None:
        wall_target = game.find_enemy_wall_target(self, target) if hasattr(game, "find_enemy_wall_target") else None
        if wall_target is not None:
            target = wall_target
        self.sprite_target = target
        if self.melee.can_reach(target, self.attack_range):
            self._decelerate(dt)
            ability = self.abilities.primary_attack()
            if ability is not None:
                ability.activate(game, target)
        else:
            self._move_to(target.pos, dt, game, arrival_radius=self.melee.attack_distance(target, self.attack_range))

    def _update_ranged_assault(self, dt: float, game, target) -> None:
        self.sprite_target = target
        distance = self.pos.distance_to(target.pos)
        if distance <= self.attack_range + target.radius:
            self._decelerate(dt)
            ability = self.abilities.primary_attack()
            if ability is not None:
                ability.activate(game, target)
        else:
            self._move_to(target.pos, dt, game, arrival_radius=max(18.0, self.attack_range * 0.42))

    def _move_to(self, target: pygame.Vector2, dt: float, game, arrival_radius: float = 10.0) -> None:
        current_speed = self.speed * self.slow_multiplier
        max_velocity = max(self.speed * 1.35, current_speed + 130 / self.mass)
        neighbors = (lambda: game.nearby_enemies(self.pos, 52)) if hasattr(game, "nearby_enemies") else game.enemies
        self.navigator.steer_to(
            target,
            dt,
            game,
            speed=current_speed,
            acceleration=self.acceleration,
            radius=self.collision_radius,
            arrival_radius=arrival_radius,
            neighbors=neighbors,
            separation_strength=0.42,
            max_velocity=max_velocity,
        )
        self._apply_velocity(dt, game)

    def _move_to_core(self, target, dt: float, game) -> None:
        if target is not game.core_target:
            self._move_to(target.pos, dt, game, arrival_radius=self.radius + 25)
            return

        flow = game.grid.steering_direction_from_world(self.pos)
        if flow.length_squared() == 0 or self.navigator.stuck_time > 0.85:
            self._move_to(target.pos, dt, game, arrival_radius=self.radius + 25)
            return

        current_speed = self.speed * self.slow_multiplier
        max_velocity = max(self.speed * 1.35, current_speed + 130 / self.mass)
        neighbors = (lambda: game.nearby_enemies(self.pos, 52)) if hasattr(game, "nearby_enemies") else game.enemies
        self.navigator.steer_direction(
            flow,
            dt,
            game,
            goal=target.pos,
            speed=current_speed,
            acceleration=self.acceleration,
            radius=self.collision_radius,
            neighbors=neighbors,
            separation_strength=0.42,
            max_velocity=max_velocity,
        )
        self._apply_velocity(dt, game)

    def _steer_with_direction(self, direction: pygame.Vector2, dt: float, game) -> None:
        desired = pygame.Vector2(direction)
        if desired.length_squared() == 0:
            self._decelerate(dt)
            return
        self._move_to(self.pos + desired.normalize() * config.TILE_SIZE * 3, dt, game)

    def _apply_velocity(self, dt: float, game) -> None:
        self.pos += self.vel * dt
        self.pos, collided = game.grid.resolve_circle_blockers(self.pos, self.collision_radius)
        if collided:
            self.vel *= 0.35

    def _decelerate(self, dt: float) -> None:
        if self.vel.length_squared() == 0:
            return
        drop = self.acceleration * 1.35 * dt
        if self.vel.length() <= drop:
            self.vel.update(0, 0)
        else:
            self.vel.scale_to_length(self.vel.length() - drop)

    def active_taunt_target(self):
        if self.taunt_time <= 0 or not getattr(self.taunt_target, "alive", False):
            self.taunt_target = None
            self.taunt_time = 0.0
            return None
        return self.taunt_target

    def apply_taunt(self, target, duration: float) -> None:
        if not getattr(target, "alive", False):
            return
        self.taunt_target = target
        self.taunt_time = max(self.taunt_time, duration)

    def take_damage(self, amount: float, owner=None) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        if owner is not None:
            self.last_hit_by = owner
        self.hit_flash = 1.0
        return self.health <= 0

    def apply_knockback(self, amount: float, source_pos: pygame.Vector2) -> None:
        direction = self.pos - source_pos
        if direction.length_squared() == 0:
            angle = random.random() * math.tau
            direction = pygame.Vector2(math.cos(angle), math.sin(angle))
        direction = direction.normalize()
        damage_fraction = max(0.0, amount / max(1.0, self.max_health))
        if damage_fraction < 0.035:
            return
        scaled_fraction = min(1.0, damage_fraction)
        impulse = (scaled_fraction ** 0.85) * 128.0 / self.mass
        if amount >= self.health:
            impulse *= 1.2
        self.vel += direction * impulse

    def apply_slow(self, multiplier: float, duration: float, attack_multiplier: float | None = None) -> None:
        self.slow_multiplier = min(self.slow_multiplier, multiplier)
        self.slow_time = max(self.slow_time, duration)
        if attack_multiplier is not None:
            self.attack_slow_multiplier = min(self.attack_slow_multiplier, attack_multiplier)
            self.attack_slow_time = max(self.attack_slow_time, duration)

    def apply_stun(self, duration: float) -> None:
        self.stun_time = max(self.stun_time, duration)

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
        self.burn_spread_radius = max(self.burn_spread_radius, spread_radius)
        self.burn_spread_falloff = spread_falloff
        self.burn_can_spread = can_spread and spread_radius > 0
        self.burn_spread_timer = min(self.burn_spread_timer if self.burn_spread_timer > 0 else 0.35, 0.35)

    def apply_damage_vulnerability(self, multiplier: float, duration: float, source_classes: tuple[str, ...] = ("troop", "tower")) -> None:
        self.damage_vulnerability_multiplier = max(self.damage_vulnerability_multiplier, multiplier)
        self.damage_vulnerability_time = max(self.damage_vulnerability_time, duration)
        self.damage_vulnerability_source_classes.update(source_classes)

    def damage_taken_multiplier(self, source) -> float:
        if self.damage_vulnerability_time <= 0:
            return 1.0
        source_class = "tower" if source.__class__.__name__ == "Tower" else str(getattr(source, "target_class", ""))
        if source_class in self.damage_vulnerability_source_classes:
            return self.damage_vulnerability_multiplier
        return 1.0

    def _sprite_pose_vector(self) -> pygame.Vector2:
        if self.attack_anim_phase and self.attack_anim_direction.length_squared() > 0.01:
            self.sprite_facing = self.attack_anim_direction.normalize()
            return pygame.Vector2(self.sprite_facing)

        direction = pygame.Vector2(0, 0)
        if self.vel.length_squared() > 64.0:
            direction = pygame.Vector2(self.vel)
        elif getattr(self.sprite_target, "alive", True) and hasattr(self.sprite_target, "pos"):
            direction = pygame.Vector2(self.sprite_target.pos) - self.pos
        elif self.swing_time > 0 and self.swing_dir.length_squared() > 0:
            direction = pygame.Vector2(self.swing_dir)
        elif self.vel.length_squared() > 4.0:
            direction = pygame.Vector2(self.vel)

        if direction.length_squared() > 0.01:
            self.sprite_facing = direction.normalize()
        return pygame.Vector2(self.sprite_facing)

    def start_attack_animation(
        self,
        *,
        target=None,
        target_pos: pygame.Vector2 | None = None,
        direction: pygame.Vector2 | None = None,
        phase: str = "full",
        duration: float = 0.36,
    ) -> bool:
        if enemy_attack_sprite_sheet(self.kind) is None:
            return False

        facing = self._attack_animation_direction(target=target, target_pos=target_pos, direction=direction)
        if facing.length_squared() > 0.01:
            facing = facing.normalize()
            self.attack_anim_direction = facing
            self.sprite_facing = pygame.Vector2(facing)

        self.attack_anim_phase = phase if phase in {"windup", "impact", "full"} else "full"
        self.attack_anim_time = 0.0
        self.attack_anim_duration = max(0.05, float(duration))
        return True

    def _attack_animation_direction(self, *, target=None, target_pos: pygame.Vector2 | None = None, direction: pygame.Vector2 | None = None) -> pygame.Vector2:
        if direction is not None:
            vector = pygame.Vector2(direction)
        elif target is not None and hasattr(target, "pos"):
            vector = pygame.Vector2(target.pos) - self.pos
        elif target_pos is not None:
            vector = pygame.Vector2(target_pos) - self.pos
        elif self.sprite_target is not None and hasattr(self.sprite_target, "pos"):
            vector = pygame.Vector2(self.sprite_target.pos) - self.pos
        else:
            vector = pygame.Vector2(self.sprite_facing)
        if vector.length_squared() <= 0.01:
            vector = pygame.Vector2(self.sprite_facing)
        if vector.length_squared() <= 0.01:
            vector = pygame.Vector2(0, 1)
        return vector

    def _update_attack_animation(self, dt: float) -> None:
        if not self.attack_anim_phase:
            return
        self.attack_anim_time += max(0.0, dt)
        if self.attack_anim_time >= self.attack_anim_duration:
            self.attack_anim_phase = ""
            self.attack_anim_time = 0.0
            self.attack_anim_duration = 0.0

    def _update_sprite_animation(self, dt: float) -> None:
        if dt <= 0:
            return
        speed_ratio = 0.65
        if self.vel.length_squared() > 1.0:
            speed_ratio = self.vel.length() / max(1.0, self.speed)
        elif self.sprite_target is not None or self.swing_time > 0:
            speed_ratio = 1.0
        self.sprite_anim_time += dt * max(0.65, min(1.55, speed_ratio))

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if not self.alive:
            return
        screen = camera.world_to_screen(self.pos, viewport)
        r = max(3, int(self.radius * camera.zoom * (1.0 + self.hit_flash * 0.16)))
        fill = config.PALETTE.white if self.hit_flash > 0 else config.PALETTE.dark
        outline = config.PALETTE.black if self.hit_flash > 0 else config.PALETTE.white

        if self.slow_time > 0 or self.attack_slow_time > 0:
            draw_circle_alpha(surface, screen, r + 6 * camera.zoom, config.PALETTE.white, 55, 1)
        if self.burn_time > 0:
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.02 + self.phase)
            draw_circle_alpha(surface, screen, r + 4 + pulse * 4, config.PALETTE.white, 40, 1)
        if self.stun_time > 0:
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.035 + self.phase)
            draw_circle_alpha(surface, screen, r + 8 + pulse * 3, config.PALETTE.white, 64, 1)
        if self.damage_vulnerability_time > 0:
            draw_circle_alpha(surface, screen, r + 11 * camera.zoom, config.PALETTE.white, 58, 1)

        sprite_radius = self._draw_sprite_body(surface, camera, screen)
        if sprite_radius is not None:
            if self.taunt_time > 0:
                draw_circle_alpha(surface, screen, sprite_radius + 8 * camera.zoom, config.PALETTE.white, 70, 1)
            self._draw_swing(surface, screen, sprite_radius, camera.zoom)
            self._draw_health_bar(surface, screen, sprite_radius, camera.zoom)
            if self.damage_vulnerability_time > 0:
                self._draw_vulnerability_marker(surface, screen, sprite_radius, camera.zoom)
            return

        if self.shape == "diamond":
            points = [(screen.x, screen.y - r), (screen.x + r, screen.y), (screen.x, screen.y + r), (screen.x - r, screen.y)]
            pygame.draw.polygon(surface, fill, points)
            pygame.draw.polygon(surface, outline, points, max(1, int(camera.zoom)))
        elif self.shape == "square":
            rect = pygame.Rect(0, 0, r * 2, r * 2)
            rect.center = (screen.x, screen.y)
            pygame.draw.rect(surface, fill, rect)
            pygame.draw.rect(surface, outline, rect, max(1, int(camera.zoom)))
        elif self.shape == "octagon":
            points = []
            for i in range(8):
                ang = i / 8 * math.tau + math.pi / 8
                points.append((screen.x + math.cos(ang) * r, screen.y + math.sin(ang) * r))
            pygame.draw.polygon(surface, fill, points)
            pygame.draw.polygon(surface, outline, points, max(1, int(camera.zoom)))
        else:
            pygame.draw.circle(surface, fill, screen, r)
            pygame.draw.circle(surface, outline, screen, r, max(1, int(camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x - r * 1.25, screen.y), (screen.x + r * 1.25, screen.y), max(1, int(2 * camera.zoom)))
            pygame.draw.line(surface, outline, (screen.x, screen.y - r * 1.25), (screen.x, screen.y + r * 1.25), max(1, int(2 * camera.zoom)))

        if self.taunt_time > 0:
            draw_circle_alpha(surface, screen, r + 8 * camera.zoom, config.PALETTE.white, 70, 1)

        if self.damage_vulnerability_time > 0:
            self._draw_vulnerability_marker(surface, screen, r, camera.zoom)

        self._draw_swing(surface, screen, r, camera.zoom)
        self._draw_health_bar(surface, screen, r, camera.zoom)

    def _draw_vulnerability_marker(self, surface: pygame.Surface, screen: pygame.Vector2, radius: float, zoom: float) -> None:
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.010 + self.phase)
        size = max(5.0, 6.0 * zoom + pulse * 2.0 * zoom)
        center = screen + pygame.Vector2(0, -radius - (15.0 + pulse * 3.0) * zoom)
        points = [
            (center.x, center.y - size),
            (center.x + size, center.y),
            (center.x, center.y + size),
            (center.x - size, center.y),
        ]
        draw_circle_alpha(surface, center, size * 1.65, config.PALETTE.white, 30, 1)
        pygame.draw.polygon(surface, config.PALETTE.black, points)
        pygame.draw.polygon(surface, config.PALETTE.white, points, max(1, int(2 * zoom)))
        inner = size * 0.42
        pygame.draw.line(surface, config.PALETTE.white, (center.x - inner, center.y), (center.x + inner, center.y), max(1, int(zoom)))
        pygame.draw.line(surface, config.PALETTE.white, (center.x, center.y - inner), (center.x, center.y + inner), max(1, int(zoom)))

    def _draw_sprite_body(self, surface: pygame.Surface, camera, screen: pygame.Vector2) -> int | None:
        if self.attack_anim_phase:
            attack_radius = self._draw_attack_sprite_body(surface, camera, screen)
            if attack_radius is not None:
                return attack_radius

        sheet = enemy_sprite_sheet(self.kind)
        if sheet is None:
            return None

        direction = self._sprite_pose_vector()
        if direction.length_squared() > 0.01:
            row, flip_x = directional_row(direction)
        else:
            row, flip_x = ROW_SOUTH, False

        frame = int(self.sprite_anim_time / 0.14) % 3
        size = max(1, int(round(sheet.frame_size * camera.zoom)))
        image = sheet.frame(row, frame, flip_x, size)
        rect = image.get_rect(center=(int(round(screen.x)), int(round(screen.y))))
        surface.blit(image, rect)

        if self.hit_flash > 0:
            flash = image.copy()
            flash.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGB_ADD)
            flash.set_alpha(int(120 * self.hit_flash))
            surface.blit(flash, rect)

        return max(3, int(size * 0.5))

    def _draw_attack_sprite_body(self, surface: pygame.Surface, camera, screen: pygame.Vector2) -> int | None:
        sheet = enemy_attack_sprite_sheet(self.kind)
        if sheet is None:
            return None

        row, flip_x = attack_directional_row(self._sprite_pose_vector())
        frame = self._attack_animation_frame()
        size = max(1, int(round(sheet.frame_size * camera.zoom)))
        image = sheet.frame(row, frame, flip_x, size)
        rect = image.get_rect(center=(int(round(screen.x)), int(round(screen.y))))
        surface.blit(image, rect)

        if self.hit_flash > 0:
            flash = image.copy()
            flash.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGB_ADD)
            flash.set_alpha(int(120 * self.hit_flash))
            surface.blit(flash, rect)

        return max(3, int(size * 0.5))

    def _attack_animation_frame(self) -> int:
        progress = self.attack_anim_time / max(0.01, self.attack_anim_duration)
        progress = max(0.0, min(0.999, progress))
        if self.attack_anim_phase == "windup":
            return 0 if progress < 0.50 else 1
        if self.attack_anim_phase == "impact":
            return 2
        return min(2, int(progress * 3))

    def _draw_health_bar(self, surface: pygame.Surface, screen: pygame.Vector2, radius: int, zoom: float) -> None:
        bar_w = max(14, int(radius * 2.2))
        bar_h = max(2, int(3 * zoom))
        bar = pygame.Rect(0, 0, bar_w, bar_h)
        bar.center = (screen.x, screen.y - radius - 7)
        pygame.draw.rect(surface, config.PALETTE.black, bar)
        fill_rect = bar.copy()
        fill_rect.width = int(bar.width * max(0.0, self.health / self.max_health))
        pygame.draw.rect(surface, config.PALETTE.white, fill_rect)

    def _draw_swing(self, surface: pygame.Surface, screen: pygame.Vector2, radius: int, zoom: float) -> None:
        if self.swing_time <= 0 or self.is_ranged:
            return
        progress = 1.0 - self.swing_time / self.swing_duration
        alpha = int(215 * (1.0 - progress))
        direction = pygame.Vector2(self.swing_dir)
        if direction.length_squared() == 0:
            return
        tangent = pygame.Vector2(-direction.y, direction.x)
        reach = (self.radius + self.swing_reach) * zoom
        start = screen + direction * radius * 0.62

        if self.shape == "octagon":
            sweep = (progress - 0.5) * 1.25
            strike_dir = (direction + tangent * sweep)
            if strike_dir.length_squared() > 0:
                strike_dir = strike_dir.normalize()
            end = screen + strike_dir * reach
            draw_line_alpha(surface, start, end, config.PALETTE.white, alpha, max(1, int(4 * zoom)))
            draw_line_alpha(surface, start, screen + (strike_dir - tangent * 0.18).normalize() * reach * 0.88, config.PALETTE.white, int(alpha * 0.35), 1)
            return

        end = screen + direction * reach
        draw_line_alpha(surface, start, end, config.PALETTE.white, alpha, max(1, int(3 * zoom)))
        draw_line_alpha(surface, end - direction * 7 * zoom + tangent * 4 * zoom, end, config.PALETTE.white, alpha, 1)
        draw_line_alpha(surface, end - direction * 7 * zoom - tangent * 4 * zoom, end, config.PALETTE.white, alpha, 1)


class Tower:
    target_class = "structure"

    def __init__(self, kind: str, cell: tuple[int, int], grid, research=None) -> None:
        self.kind = kind
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.research = research
        self.radius = config.TILE_SIZE * 0.46
        self.base_max_health = {"archer": 95.0, "cannon": 135.0, "wizard": 105.0}.get(kind, 100.0)
        self.max_health = self.base_max_health
        self.health = self.max_health
        self.alive = True
        self.level = 1
        self.xp = 0
        self.kills = 0
        self.cooldown = random.uniform(0.0, 0.35)
        self.specialization: str | None = None
        self.installed_mods: list[str] = []
        self.pulse = random.random() * math.tau
        self.passive_mod_timer = random.uniform(0.05, 0.35)
        self.level_blink_period = 1.18
        self.level_blink_duration = 0.24
        self.level_blink_timer = random.uniform(0.0, self.level_blink_period)
        self.level_blink_flash = 0.0
        self.abilities = AbilitySystemComponent(self)
        configure_tower_abilities(self)

    @property
    def display_name(self) -> str:
        return tower_name(self.kind, self.specialization)

    def stats(self, game=None) -> dict[str, float | str]:
        stats = stats_for(self.kind, self.level, self.specialization)
        if self.research is not None:
            if self.kind == "archer":
                stats["fire_rate"] = float(stats["fire_rate"]) * self.research.multiplier("archer_attack_speed")
            elif self.kind == "cannon":
                stats["damage"] = float(stats["damage"]) * self.research.multiplier("cannon_damage")
            elif self.kind == "wizard":
                stats["range"] = float(stats["range"]) * self.research.multiplier("wizard_tower_range")
        stats["damage"] = float(stats["damage"]) * self.mod_multiplier("damage_multiplier")
        stats["fire_rate"] = float(stats["fire_rate"]) * self.mod_multiplier("fire_rate_multiplier")
        stats["range"] = float(stats["range"]) * self.mod_multiplier("range_multiplier")
        stats["accuracy"] = min(0.995, max(0.0, float(stats["accuracy"]) * self.mod_multiplier("accuracy_multiplier")))
        if game is not None and hasattr(game, "item_multiplier"):
            stats["damage"] = float(stats["damage"]) * game.item_multiplier("tower_damage_multiplier")
            stats["fire_rate"] = float(stats["fire_rate"]) * game.item_multiplier("tower_fire_rate_multiplier")
        return stats

    def has_mod(self, mod_id: str) -> bool:
        return mod_id in self.installed_mods

    def mod_effect(self, effect: str, default: float = 1.0) -> float:
        component = getattr(self, "abilities", None)
        if component is not None:
            value, found = component.passive_multiplier(effect, default)
            if found:
                return value
        value = default
        for mod_id in self.installed_mods:
            definition = TOWER_MODS.get(mod_id)
            if definition is None or effect not in definition.effects:
                continue
            raw = definition.effects[effect]
            if isinstance(raw, (int, float)):
                value *= float(raw)
        return value

    def mod_multiplier(self, effect: str) -> float:
        return self.mod_effect(effect, 1.0)

    def mod_value(self, effect: str, default=None):
        for mod_id in self.installed_mods:
            definition = TOWER_MODS.get(mod_id)
            if definition is not None and effect in definition.effects:
                return definition.effects[effect]
        return default

    def mod_flag(self, effect: str) -> bool:
        return any(bool(TOWER_MODS[mod_id].effects.get(effect)) for mod_id in self.installed_mods if mod_id in TOWER_MODS)

    def can_install_mod(self, mod_id: str) -> bool:
        definition = TOWER_MODS.get(mod_id)
        return definition is not None and mod_id not in self.installed_mods and self.xp >= definition.xp_cost

    def install_mod(self, mod_id: str, game) -> bool:
        if not self.can_install_mod(mod_id):
            return False
        definition = TOWER_MODS[mod_id]
        self.xp -= definition.xp_cost
        self.installed_mods.append(mod_id)
        configure_tower_abilities(self)
        self.recalculate_mod_health(game)
        return True

    def recalculate_mod_health(self, game) -> None:
        old_max = self.max_health
        bonus = 0.0
        if self.has_mod("life_link") and hasattr(game, "adjacent_towers"):
            bonus = sum(tower.base_max_health for tower in game.adjacent_towers(self) if tower.alive)
        self.max_health = max(self.base_max_health, self.base_max_health + bonus)
        if self.max_health > old_max:
            self.health += self.max_health - old_max
        elif self.health > self.max_health:
            self.health = self.max_health

    def can_specialize(self) -> bool:
        return self.level >= 4 and self.specialization is None

    def specialization_options(self) -> dict[str, str]:
        return SPECIALIZATIONS.get(self.kind, {})

    def specialize(self, option: str) -> bool:
        if option in self.specialization_options() and self.can_specialize():
            self.specialization = option
            configure_tower_abilities(self)
            return True
        return False

    def can_level_up(self) -> bool:
        return self.xp >= xp_needed(self.level)

    def add_xp(self, amount: int) -> bool:
        was_ready = self.can_level_up()
        self.xp += amount
        return not was_ready and self.can_level_up()

    def level_up(self) -> bool:
        cost = xp_needed(self.level)
        if self.xp < cost:
            return False
        self.xp -= cost
        self.level += 1
        self.base_max_health += 10
        self.max_health += 10
        self.health = min(self.max_health, self.health + 18)
        self.level_blink_flash = 0.0
        self.level_blink_timer = 0.18 if self.can_level_up() else self.level_blink_period
        return True

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.recalculate_mod_health(game)
        self._update_level_ready_feedback(dt, game)
        self.abilities.update(dt, game)

    def find_target(self, enemies: list[Enemy], attack_range: float, townhall_pos: pygame.Vector2) -> Enemy | None:
        candidates = [
            enemy for enemy in enemies if enemy.alive and enemy.pos.distance_to(self.pos) <= attack_range + enemy.radius
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda enemy: self.abilities.target_priority_key(enemy, (enemy.pos.distance_to(townhall_pos),)))

    def _update_level_ready_feedback(self, dt: float, game) -> None:
        self.level_blink_flash = max(0.0, self.level_blink_flash - dt)
        if not self.can_level_up():
            self.level_blink_timer = min(self.level_blink_timer, self.level_blink_period)
            self.level_blink_flash = 0.0
            return
        self.level_blink_timer -= dt
        if self.level_blink_timer > 0:
            return
        self.level_blink_timer += self.level_blink_period
        self.level_blink_flash = self.level_blink_duration
        if hasattr(game, "play_sound"):
            game.play_sound("level_up_blink")

    def draw_range(self, surface: pygame.Surface, camera, viewport: pygame.Rect, game=None) -> None:
        stats = self.stats(game)
        screen = camera.world_to_screen(self.pos, viewport)
        draw_circle_alpha(surface, screen, float(stats["range"]) * camera.zoom, config.PALETTE.white, 32, 1)

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, selected: bool = False, hovered: bool = False) -> None:
        scale = hover_feedback.hover_scale(hovered)
        tile = config.TILE_SIZE * camera.zoom * scale
        center = camera.world_to_screen(self.pos, viewport)
        rect = pygame.Rect(0, 0, int(tile * 0.82), int(tile * 0.82))
        rect.center = (center.x, center.y)
        fill, mark = hover_feedback.inverted_pair(hovered)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))
        if self.can_level_up():
            ready_alpha = 42
            if self.level_blink_flash > 0:
                ready_alpha = int(76 + 130 * (self.level_blink_flash / max(0.01, self.level_blink_duration)))
            draw_rect_alpha(surface, rect.inflate(max(4, int(5 * camera.zoom)), max(4, int(5 * camera.zoom))), config.PALETTE.white, ready_alpha, max(1, int(2 * camera.zoom)))
            if self.level_blink_flash > 0:
                draw_circle_alpha(surface, center, tile * 0.62, config.PALETTE.white, min(180, ready_alpha), max(1, int(2 * camera.zoom)))
        if selected:
            glow = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.012)
            draw_circle_alpha(surface, center, tile * (0.52 + glow * 0.08), config.PALETTE.white, 65, 1)

        if self.kind == "archer":
            size = int(tile * 0.26)
            points = [(center.x, center.y - size), (center.x + size, center.y + size), (center.x - size, center.y + size)]
            pygame.draw.polygon(surface, mark, points, max(1, int(2 * camera.zoom)))
        elif self.kind == "cannon":
            pygame.draw.circle(surface, mark, center, max(2, int(tile * 0.18)), max(1, int(2 * camera.zoom)))
            pygame.draw.line(surface, mark, center, (center.x + tile * 0.26, center.y), max(1, int(3 * camera.zoom)))
        else:
            r = tile * 0.23
            points = [(center.x, center.y - r), (center.x + r, center.y), (center.x, center.y + r), (center.x - r, center.y)]
            pygame.draw.polygon(surface, mark, points, max(1, int(2 * camera.zoom)))
            pygame.draw.line(surface, mark, (center.x - r, center.y), (center.x + r, center.y), max(1, int(camera.zoom)))

        if self.level > 1:
            for i in range(min(5, self.level - 1)):
                dot = pygame.Vector2(rect.left + 5 + i * 5 * camera.zoom, rect.bottom - 5)
                pygame.draw.circle(surface, mark, dot, max(1, int(1.4 * camera.zoom)))
        if self.installed_mods:
            mod_y = rect.top + 5
            for i in range(min(5, len(self.installed_mods))):
                dot = pygame.Rect(0, 0, max(2, int(4 * camera.zoom)), max(2, int(4 * camera.zoom)))
                dot.topright = (rect.right - 5, mod_y + i * max(4, int(6 * camera.zoom)))
                pygame.draw.rect(surface, mark, dot)

        if self.health < self.max_health:
            bar = pygame.Rect(rect.left, rect.top - 6, rect.width, max(2, int(3 * camera.zoom)))
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill_rect = bar.copy()
            fill_rect.width = int(bar.width * max(0.0, self.health / self.max_health))
            pygame.draw.rect(surface, config.PALETTE.white, fill_rect)


class Projectile:
    def __init__(
        self,
        pos: pygame.Vector2,
        target: Enemy,
        speed: float,
        damage: float,
        owner: Tower,
        kind: str,
        aoe: float,
        effect: str,
        impact_kind: str = "single",
        accuracy: float = 0.9,
        max_range: float | None = None,
    ) -> None:
        self.pos = pygame.Vector2(pos)
        self.target = target
        self.speed = speed
        self.damage = damage
        self.owner = owner
        self.kind = kind
        self.aoe = aoe
        self.effect = effect
        self.impact_kind = impact_kind
        self.accuracy = max(0.0, min(1.0, accuracy))
        self.max_range = max_range
        self.destination = self._aim_destination(target)
        delta = self.destination - self.pos
        if delta.length_squared() == 0:
            angle = random.random() * math.tau
            delta = pygame.Vector2(math.cos(angle), math.sin(angle))
            self.destination = self.pos + delta
        self.direction = delta.normalize()
        travel_distance = float(max_range) if max_range is not None else delta.length()
        if self.aoe <= 0:
            self.destination = self.pos + self.direction * max(travel_distance, delta.length())
        self.trail: list[pygame.Vector2] = [pygame.Vector2(pos)]
        self.phase = random.random() * math.tau
        self.life = 4.0
        self.alive = True

    def _aim_destination(self, target: Enemy) -> pygame.Vector2:
        current = pygame.Vector2(target.pos)
        intercept = _intercept_point(self.pos, current, pygame.Vector2(getattr(target, "vel", (0, 0))), self.speed)
        predicted = current.lerp(intercept, self.accuracy)
        if self.accuracy < 0.995:
            miss_scale = (1.0 - self.accuracy) * max(8.0, target.radius * 1.2)
            predicted += pygame.Vector2(random.uniform(-miss_scale, miss_scale), random.uniform(-miss_scale, miss_scale))
        return predicted

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.life -= dt
        if self.life <= 0:
            self.alive = False
            return

        previous = pygame.Vector2(self.pos)
        delta = self.destination - self.pos
        distance = max(0.0, delta.dot(self.direction))
        step = self.speed * dt

        if self.target.alive and _segment_distance(previous, self.pos + self.direction * min(step, distance), self.target.pos) <= self.target.radius + self.hit_radius():
            self.pos = pygame.Vector2(self.target.pos)
            self.impact(game)
            self.alive = False
            return

        if distance <= step:
            self.pos = pygame.Vector2(self.destination)
            self.impact(game) if self.should_impact_at_destination() else self.show_miss(game)
            self.alive = False
            return

        self.pos += self.direction * step
        self.trail.append(pygame.Vector2(self.pos))
        max_trail = 14 if self.element_kind() in {"fire", "ice"} else 9
        if len(self.trail) > max_trail:
            self.trail.pop(0)

    def hit_radius(self) -> float:
        return 4.5 if self.kind != "cannon" else 6.5

    def should_impact_at_destination(self) -> bool:
        return self.aoe > 0 or (self.target.alive and self.target.pos.distance_to(self.pos) <= self.target.radius + self.hit_radius())

    def show_miss(self, game) -> None:
        game.spawn_burst(self.pos, 5, 28)

    def impact(self, game) -> None:
        effect = self.elemental_effect()
        element = effect.element if effect is not None else "physical"
        if self.aoe > 0:
            self.show_impact(game, "aoe", self.aoe)
            enemies = game.targetable_enemies_near(self.pos, self.aoe + 34) if hasattr(game, "targetable_enemies_near") else game.enemies
            for enemy in list(enemies):
                if not enemy.alive:
                    continue
                distance = enemy.pos.distance_to(self.pos)
                if distance <= self.aoe + enemy.radius:
                    falloff = 1.0 - min(0.45, distance / max(1.0, self.aoe) * 0.35)
                    game.damage_enemy(enemy, self.damage * falloff, self.owner, source_pos=pygame.Vector2(self.pos), element=element)
                    if effect is not None:
                        game.apply_elemental_effect(enemy, effect, self.owner, pygame.Vector2(self.pos))
        elif self.effect == "chain":
            self.show_impact(game, "multi", 0.0)
            self.chain_impact(game)
        else:
            if not self.target.alive:
                self.show_miss(game)
                return
            game.damage_enemy(self.target, self.damage, self.owner, source_pos=pygame.Vector2(self.pos), element=element)
            if effect is not None:
                game.apply_elemental_effect(self.target, effect, self.owner, pygame.Vector2(self.pos))
            self.show_impact(game, self.impact_kind, 0.0)

    def show_impact(self, game, kind: str, radius: float) -> None:
        if hasattr(game, "show_damage_impact"):
            game.show_damage_impact(pygame.Vector2(self.pos), kind, radius)
            return
        game.spawn_burst(self.pos, 18 if kind == "aoe" else 7, 84 if kind == "aoe" else 42)

    def chain_impact(self, game) -> None:
        arc_radius = 125.0 * _research_multiplier(self.owner, "wizard_lightning_arc")
        if hasattr(game, "chain_lightning"):
            game.chain_lightning(
                pygame.Vector2(self.owner.pos),
                self.target,
                self.damage,
                self.owner,
                jumps=4,
                radius=arc_radius,
                falloff=0.62,
                stun=0.16,
            )
        else:
            current = self.target
            damage = self.damage
            hit: set[Enemy] = set()
            previous_pos = pygame.Vector2(self.owner.pos)
            for _ in range(4):
                if current is None or not current.alive or current in hit:
                    break
                hit.add(current)
                game.beams.append(Beam(previous_pos, pygame.Vector2(current.pos), 0.14, 2))
                game.damage_enemy(current, damage, self.owner, source_pos=pygame.Vector2(previous_pos), element="lightning")
                current.apply_stun(0.16)
                previous_pos = pygame.Vector2(current.pos)
                damage *= 0.62
                enemies = game.targetable_enemies_near(previous_pos, arc_radius + 24) if hasattr(game, "targetable_enemies_near") else game.enemies
                choices = [
                    enemy
                    for enemy in enemies
                    if enemy.alive and enemy not in hit and enemy.pos.distance_to(previous_pos) <= arc_radius
                ]
                current = min(choices, key=lambda enemy: enemy.pos.distance_to(previous_pos)) if choices else None
        game.spawn_burst(self.pos, 10, 56)

    def elemental_effect(self) -> ElementalEffect | None:
        return elemental_effect_for_projectile(self.effect, self.owner)

    def element_kind(self) -> str:
        if self.effect == "burn":
            return "fire"
        if self.effect == "slow":
            return "ice"
        if self.effect == "chain":
            return "lightning"
        return "physical"

    def apply_effect(self, enemy: Enemy, game) -> None:
        if not enemy.alive:
            return
        effect = self.elemental_effect()
        if effect is not None:
            game.apply_elemental_effect(enemy, effect, self.owner, pygame.Vector2(self.pos))

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if not self.alive:
            return
        element = self.element_kind()
        if element == "fire":
            self._draw_fire_projectile(surface, camera, viewport)
            return
        if element == "ice":
            self._draw_ice_projectile(surface, camera, viewport)
            return
        for i, point in enumerate(self.trail):
            t = (i + 1) / max(1, len(self.trail))
            screen = camera.world_to_screen(point, viewport)
            if self.impact_kind == "aoe":
                radius = (2.4 + 4.8 * t) * camera.zoom
                draw_circle_alpha(surface, screen, radius, config.PALETTE.white, int(26 + 92 * t), 1)
                draw_circle_alpha(surface, screen, radius * 0.46, config.PALETTE.white, int(20 + 70 * t))
            elif self.impact_kind == "multi":
                draw_circle_alpha(surface, screen, (2.0 + 3.2 * t) * camera.zoom, config.PALETTE.white, int(28 + 92 * t), 1)
            else:
                draw_circle_alpha(surface, screen, (2.0 + 3.5 * t) * camera.zoom, config.PALETTE.white, int(34 + 105 * t))

        screen = camera.world_to_screen(self.pos, viewport)
        radius = 4.5 if self.kind != "cannon" else 6.5
        if self.impact_kind == "aoe":
            radius += 1.5
            draw_circle_alpha(surface, screen, (radius + 6) * camera.zoom, config.PALETTE.white, 42, 1)
        elif self.impact_kind == "multi":
            for index in range(3):
                angle = index / 3 * math.tau + pygame.time.get_ticks() * 0.012
                offset = pygame.Vector2(math.cos(angle), math.sin(angle)) * 5 * camera.zoom
                pygame.draw.circle(surface, config.PALETTE.white, screen + offset, max(1, int(2.0 * camera.zoom)))
        if self.effect == "chain":
            draw_circle_alpha(surface, screen, 12 * camera.zoom, config.PALETTE.white, 46, 1)
        pygame.draw.circle(surface, config.PALETTE.white, screen, max(2, int(radius * camera.zoom)))

    def _draw_fire_projectile(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        now = pygame.time.get_ticks() * 0.001
        tangent = pygame.Vector2(-self.direction.y, self.direction.x)
        for i, point in enumerate(self.trail):
            t = (i + 1) / max(1, len(self.trail))
            flicker = math.sin(now * 24.0 + self.phase + i * 0.9)
            jitter = tangent * flicker * (1.0 - t) * 5.0
            screen = camera.world_to_screen(point + jitter, viewport)
            radius = (2.2 + 7.0 * t + max(0.0, flicker) * 1.8) * camera.zoom
            draw_circle_alpha(surface, screen, radius, FIRE_OUTER, int(26 + 118 * t), 1)
            draw_circle_alpha(surface, screen, radius * 0.48, FIRE_CORE, int(22 + 126 * t))
            if i > 0 and i % 2 == 0:
                previous = camera.world_to_screen(self.trail[i - 1], viewport)
                draw_line_alpha(surface, previous, screen, FIRE_OUTER, int(26 + 70 * t), max(1, int(2 * camera.zoom)))

        screen = camera.world_to_screen(self.pos, viewport)
        forward = self.direction
        side = tangent
        length = (12.0 + 3.0 * math.sin(now * 18.0 + self.phase)) * camera.zoom
        width = 6.5 * camera.zoom
        points = [
            screen + forward * length,
            screen - forward * length * 0.75 + side * width,
            screen - forward * length * 1.10,
            screen - forward * length * 0.75 - side * width,
        ]
        draw_circle_alpha(surface, screen - forward * 4.0 * camera.zoom, 14 * camera.zoom, FIRE_OUTER, 42, 1)
        pygame.draw.polygon(surface, FIRE_OUTER, points)
        pygame.draw.circle(surface, FIRE_CORE, screen, max(2, int(4.2 * camera.zoom)))
        pygame.draw.circle(surface, config.PALETTE.white, screen + forward * 2.5, max(1, int(2.0 * camera.zoom)))

    def _draw_ice_projectile(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        now = pygame.time.get_ticks() * 0.001
        tangent = pygame.Vector2(-self.direction.y, self.direction.x)
        for i, point in enumerate(self.trail):
            t = (i + 1) / max(1, len(self.trail))
            shard_offset = tangent * math.sin(self.phase + i * 1.7) * (1.0 - t) * 3.0
            screen = camera.world_to_screen(point + shard_offset, viewport)
            radius = (1.4 + 4.0 * t) * camera.zoom
            draw_circle_alpha(surface, screen, radius + 2.0 * camera.zoom, ICE_OUTER, int(20 + 84 * t), 1)
            if i % 2 == 0:
                shard = max(2.0, radius)
                points = [
                    screen + self.direction * shard * 1.7,
                    screen + tangent * shard * 0.72,
                    screen - self.direction * shard * 1.3,
                    screen - tangent * shard * 0.72,
                ]
                pygame.draw.polygon(surface, ICE_OUTER, points, max(1, int(camera.zoom)))

        screen = camera.world_to_screen(self.pos, viewport)
        spin = math.sin(now * 8.0 + self.phase) * 0.22
        direction = self.direction.rotate_rad(spin)
        tangent = pygame.Vector2(-direction.y, direction.x)
        length = 11.5 * camera.zoom
        width = 5.5 * camera.zoom
        shard = [
            screen + direction * length,
            screen + tangent * width,
            screen - direction * length * 0.72,
            screen - tangent * width,
        ]
        draw_circle_alpha(surface, screen, 13 * camera.zoom, ICE_OUTER, 38, 1)
        pygame.draw.polygon(surface, config.PALETTE.black, shard)
        pygame.draw.polygon(surface, ICE_OUTER, shard, max(1, int(2 * camera.zoom)))
        pygame.draw.line(surface, ICE_CORE, screen - direction * length * 0.45, screen + direction * length * 0.72, max(1, int(camera.zoom)))
        pygame.draw.circle(surface, ICE_CORE, screen, max(1, int(2.1 * camera.zoom)))


class EnemyProjectile:
    def __init__(self, pos: pygame.Vector2, target, speed: float, damage: float, owner=None) -> None:
        self.pos = pygame.Vector2(pos)
        self.target = target
        self.speed = speed
        self.damage = damage
        self.owner = owner
        self.destination = pygame.Vector2(target.pos)
        delta = self.destination - self.pos
        if delta.length_squared() == 0:
            angle = random.random() * math.tau
            delta = pygame.Vector2(math.cos(angle), math.sin(angle))
            self.destination = self.pos + delta
        self.direction = delta.normalize()
        self.trail: list[pygame.Vector2] = [pygame.Vector2(pos)]
        self.life = 4.0
        self.alive = True

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.life -= dt
        if self.life <= 0:
            self.alive = False
            return

        previous = pygame.Vector2(self.pos)
        delta = self.destination - self.pos
        distance = max(0.0, delta.dot(self.direction))
        step = self.speed * dt

        if getattr(self.target, "alive", False) and _segment_distance(previous, self.pos + self.direction * min(step, distance), self.target.pos) <= self.target.radius + 4.0:
            self.pos = pygame.Vector2(self.target.pos)
            game.damage_friendly(self.target, self.damage, source_pos=pygame.Vector2(self.pos), source=self.owner)
            game.spawn_burst(self.pos, 6, 38)
            self.alive = False
            return

        if distance <= step:
            self.pos = pygame.Vector2(self.destination)
            game.spawn_burst(self.pos, 4, 26)
            self.alive = False
            return

        self.pos += self.direction * step
        self.trail.append(pygame.Vector2(self.pos))
        if len(self.trail) > 8:
            self.trail.pop(0)

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if not self.alive:
            return
        for i, point in enumerate(self.trail):
            t = (i + 1) / max(1, len(self.trail))
            screen = camera.world_to_screen(point, viewport)
            draw_circle_alpha(surface, screen, (1.5 + 3.0 * t) * camera.zoom, config.PALETTE.mid, int(36 + 100 * t))
        screen = camera.world_to_screen(self.pos, viewport)
        pygame.draw.circle(surface, config.PALETTE.mid, screen, max(2, int(4.0 * camera.zoom)))
        pygame.draw.circle(surface, config.PALETTE.white, screen, max(3, int(6.0 * camera.zoom)), 1)


class HostileAoeProjectile:
    def __init__(
        self,
        pos: pygame.Vector2,
        target_pos: pygame.Vector2,
        speed: float,
        damage: float,
        owner=None,
        *,
        element: str = "physical",
        radius: float = 0.0,
        burn_dps: float = 0.0,
        burn_duration: float = 0.0,
        slow_multiplier: float = 1.0,
        status_duration: float = 0.0,
        ground_duration: float = 0.0,
        max_distance: float | None = None,
    ) -> None:
        self.pos = pygame.Vector2(pos)
        self.origin = pygame.Vector2(pos)
        self.destination = pygame.Vector2(target_pos)
        delta = self.destination - self.pos
        if delta.length_squared() == 0:
            angle = random.random() * math.tau
            delta = pygame.Vector2(math.cos(angle), math.sin(angle))
            self.destination = self.pos + delta * 64
        self.direction = delta.normalize()
        if max_distance is not None:
            self.destination = self.origin + self.direction * float(max_distance)
        self.speed = float(speed)
        self.damage = float(damage)
        self.owner = owner
        self.element = element
        self.radius = float(radius)
        self.burn_dps = float(burn_dps)
        self.burn_duration = float(burn_duration)
        self.slow_multiplier = float(slow_multiplier)
        self.status_duration = float(status_duration)
        self.ground_duration = float(ground_duration)
        self.trail: list[pygame.Vector2] = [pygame.Vector2(pos)]
        self.phase = random.random() * math.tau
        self.life = 5.0
        self.alive = True

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.life -= dt
        if self.life <= 0:
            self.alive = False
            return

        previous = pygame.Vector2(self.pos)
        delta = self.destination - self.pos
        distance = max(0.0, delta.dot(self.direction))
        step = self.speed * dt
        next_pos = self.pos + self.direction * min(step, distance)
        hit_target = self._segment_hit_target(previous, next_pos, game)
        if hit_target is not None:
            self.pos = pygame.Vector2(hit_target.pos)
            self.impact(game)
            self.alive = False
            return
        if distance <= step:
            self.pos = pygame.Vector2(self.destination)
            self.impact(game)
            self.alive = False
            return
        self.pos = next_pos
        self.trail.append(pygame.Vector2(self.pos))
        max_trail = 14 if self.element in {"fire", "ice"} else 9
        if len(self.trail) > max_trail:
            self.trail.pop(0)

    def _segment_hit_target(self, start: pygame.Vector2, end: pygame.Vector2, game):
        hit_radius = max(5.0, self.radius * 0.18)
        candidates = game.nearby_troops(self.pos, max(48.0, self.radius + 32.0)) if hasattr(game, "nearby_troops") else getattr(game, "troops", [])
        for troop in candidates:
            if getattr(troop, "alive", False) and _segment_distance(start, end, troop.pos) <= troop.radius + hit_radius:
                return troop
        return None

    def impact(self, game) -> None:
        radius = max(0.0, self.radius)
        if radius > 0 and hasattr(game, "show_damage_impact"):
            game.show_damage_impact(self.pos, "aoe", radius)
        else:
            game.spawn_burst(self.pos, 6, 38)

        targets = game.nearby_troops(self.pos, radius + 34.0) if radius > 0 and hasattr(game, "nearby_troops") else getattr(game, "troops", [])
        if radius <= 0:
            targets = [
                troop
                for troop in targets
                if getattr(troop, "alive", False) and troop.pos.distance_to(self.pos) <= troop.radius + 8.0
            ]
        for troop in list(targets):
            if not getattr(troop, "alive", False):
                continue
            distance = troop.pos.distance_to(self.pos)
            if radius > 0 and distance > radius + troop.radius:
                continue
            falloff = 1.0 if radius <= 0 else 1.0 - min(0.45, distance / max(1.0, radius) * 0.35)
            game.damage_friendly(troop, self.damage * falloff, source_pos=pygame.Vector2(self.pos), element=self.element, source=self.owner)
            self._apply_status(troop)

        if self.ground_duration > 0 and hasattr(game, "add_hazard"):
            dps = max(self.burn_dps, self.damage * 0.18)
            game.add_hazard(self.owner, self.pos, max(22.0, radius), self.ground_duration, dps, self.element)

    def _apply_status(self, target) -> None:
        if self.element == "fire" and self.burn_dps > 0 and hasattr(target, "apply_burn"):
            target.apply_burn(self.burn_dps, max(0.1, self.burn_duration), self.owner)
        elif self.element == "ice" and self.status_duration > 0 and hasattr(target, "apply_slow"):
            target.apply_slow(self.slow_multiplier, self.status_duration, self.slow_multiplier)
        elif self.element == "lightning" and self.status_duration > 0 and hasattr(target, "apply_stun"):
            target.apply_stun(self.status_duration)

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if not self.alive:
            return
        if self.element == "fire":
            self._draw_fire(surface, camera, viewport)
            return
        if self.element == "ice":
            self._draw_ice(surface, camera, viewport)
            return
        for i, point in enumerate(self.trail):
            t = (i + 1) / max(1, len(self.trail))
            screen = camera.world_to_screen(point, viewport)
            draw_circle_alpha(surface, screen, (2.0 + 4.8 * t) * camera.zoom, config.PALETTE.white, int(26 + 96 * t), 1)
        screen = camera.world_to_screen(self.pos, viewport)
        radius = 5.5 if self.element != "fire" else 7.0
        if self.radius > 0:
            draw_circle_alpha(surface, screen, (radius + 6) * camera.zoom, config.PALETTE.white, 42, 1)
        pygame.draw.circle(surface, config.PALETTE.white, screen, max(2, int(radius * camera.zoom)))

    def _draw_fire(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        now = pygame.time.get_ticks() * 0.001
        tangent = pygame.Vector2(-self.direction.y, self.direction.x)
        for i, point in enumerate(self.trail):
            t = (i + 1) / max(1, len(self.trail))
            curl = math.sin(now * 18.0 + self.phase + i * 0.8)
            screen = camera.world_to_screen(point + tangent * curl * (1.0 - t) * 6.0, viewport)
            radius = (2.6 + 7.4 * t) * camera.zoom
            draw_circle_alpha(surface, screen, radius, FIRE_OUTER, int(30 + 110 * t), 1)
            draw_circle_alpha(surface, screen, radius * 0.46, FIRE_CORE, int(24 + 104 * t))
        screen = camera.world_to_screen(self.pos, viewport)
        draw_circle_alpha(surface, screen, (13.0 + self.radius * 0.10) * camera.zoom, FIRE_OUTER, 52, 1)
        pygame.draw.circle(surface, FIRE_OUTER, screen, max(3, int(7.5 * camera.zoom)))
        pygame.draw.circle(surface, FIRE_CORE, screen + self.direction * 3 * camera.zoom, max(2, int(3.6 * camera.zoom)))
        flame_tip = screen + self.direction * 12 * camera.zoom
        tail = screen - self.direction * 12 * camera.zoom
        side = tangent * 6 * camera.zoom
        pygame.draw.polygon(surface, FIRE_OUTER, [flame_tip, tail + side, tail - side])

    def _draw_ice(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        tangent = pygame.Vector2(-self.direction.y, self.direction.x)
        for i, point in enumerate(self.trail):
            t = (i + 1) / max(1, len(self.trail))
            screen = camera.world_to_screen(point, viewport)
            radius = (1.8 + 4.7 * t) * camera.zoom
            draw_circle_alpha(surface, screen, radius + 2 * camera.zoom, ICE_OUTER, int(22 + 82 * t), 1)
            if i % 2 == 1:
                shard = max(2.0, radius)
                points = [
                    screen + self.direction * shard * 1.5,
                    screen + tangent * shard * 0.7,
                    screen - self.direction * shard * 1.1,
                    screen - tangent * shard * 0.7,
                ]
                pygame.draw.polygon(surface, ICE_OUTER, points, max(1, int(camera.zoom)))
        screen = camera.world_to_screen(self.pos, viewport)
        length = (12.0 + self.radius * 0.05) * camera.zoom
        width = 6.5 * camera.zoom
        shard = [
            screen + self.direction * length,
            screen + tangent * width,
            screen - self.direction * length * 0.80,
            screen - tangent * width,
        ]
        draw_circle_alpha(surface, screen, (12.0 + self.radius * 0.08) * camera.zoom, ICE_OUTER, 42, 1)
        pygame.draw.polygon(surface, config.PALETTE.black, shard)
        pygame.draw.polygon(surface, ICE_OUTER, shard, max(1, int(2 * camera.zoom)))
        pygame.draw.line(surface, ICE_CORE, screen - self.direction * length * 0.45, screen + self.direction * length * 0.70, max(1, int(camera.zoom)))


def _research_multiplier(owner, research_id: str) -> float:
    research = getattr(owner, "research", None)
    return research.multiplier(research_id) if research is not None else 1.0


def _intercept_point(origin: pygame.Vector2, target_pos: pygame.Vector2, target_vel: pygame.Vector2, projectile_speed: float) -> pygame.Vector2:
    relative = target_pos - origin
    speed = max(1.0, projectile_speed)
    a = target_vel.length_squared() - speed * speed
    b = 2.0 * relative.dot(target_vel)
    c = relative.length_squared()

    if abs(a) < 0.0001:
        if abs(b) < 0.0001:
            return pygame.Vector2(target_pos)
        t = -c / b
        return target_pos + target_vel * max(0.0, t)

    discriminant = b * b - 4.0 * a * c
    if discriminant < 0:
        return pygame.Vector2(target_pos)

    root = math.sqrt(discriminant)
    candidates = [(-b - root) / (2.0 * a), (-b + root) / (2.0 * a)]
    future_times = [time for time in candidates if time > 0]
    if not future_times:
        return pygame.Vector2(target_pos)
    return target_pos + target_vel * min(future_times)


def _segment_distance(start: pygame.Vector2, end: pygame.Vector2, point: pygame.Vector2) -> float:
    segment = end - start
    length_squared = segment.length_squared()
    if length_squared == 0:
        return point.distance_to(start)
    t = max(0.0, min(1.0, (point - start).dot(segment) / length_squared))
    closest = start + segment * t
    return point.distance_to(closest)
