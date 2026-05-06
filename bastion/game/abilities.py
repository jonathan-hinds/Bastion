from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Iterable

import pygame

from bastion import config
from bastion.engine.drawing import draw_circle_alpha, draw_line_alpha
from bastion.game.combat import MeleeAttackController
from bastion.game.elements import ElementalEffect
from bastion.game.tower_mods import TOWER_MODS


def _tactical_preview_alpha(ready: bool = True) -> int:
    return config.TACTICAL_OVERLAY_ALPHA if ready else config.TACTICAL_OVERLAY_SOFT_ALPHA


def _draw_tactical_preview_circle(
    surface: pygame.Surface,
    screen: pygame.Vector2,
    radius: float,
    alpha: int | None = None,
    width: int = 1,
) -> None:
    draw_circle_alpha(
        surface,
        screen,
        radius,
        config.TACTICAL_OVERLAY_COLOR,
        config.TACTICAL_OVERLAY_ALPHA if alpha is None else alpha,
        width,
    )


@dataclass(frozen=True)
class AbilityCard:
    ability_id: str
    name: str
    description: str
    details: tuple[str, ...]
    passive: bool = False
    state: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AbilityDefinition:
    request_number: int
    ability_id: str
    name: str
    description: str
    passive: bool
    factory: Callable[[object | None], "GameplayAbility"]

    def create(self, owner=None) -> "GameplayAbility":
        return self.factory(owner)


class AbilitySystemComponent:
    def __init__(self, owner) -> None:
        self.owner = owner
        self.abilities: list[GameplayAbility] = []

    def clear(self) -> None:
        self.abilities.clear()

    def add(self, ability: "GameplayAbility") -> None:
        ability.owner = self.owner
        self.abilities.append(ability)

    def add_many(self, abilities: Iterable["GameplayAbility"]) -> None:
        for ability in abilities:
            self.add(ability)

    def update(self, dt: float, game) -> None:
        for ability in list(self.abilities):
            ability.update(dt, game)

    @property
    def active_abilities(self) -> list["GameplayAbility"]:
        return [ability for ability in self.abilities if not ability.passive]

    @property
    def passive_abilities(self) -> list["GameplayAbility"]:
        return [ability for ability in self.abilities if ability.passive]

    def primary_attack(self) -> "GameplayAbility | None":
        for ability in self.active_abilities:
            if ability.primary_attack:
                return ability
        return None

    def target_priority_key(self, target, fallback_key: tuple) -> tuple:
        key = fallback_key
        for ability in self.passive_abilities:
            key = ability.target_priority_key(target, key)
        return key

    def has_target_priority(self) -> bool:
        return any(getattr(ability, "changes_target_priority", False) for ability in self.passive_abilities)

    def cards(self, game=None) -> list[AbilityCard]:
        return [ability.card(game) for ability in self.abilities]

    def passive_multiplier(self, effect: str, default: float = 1.0) -> tuple[float, bool]:
        value = default
        found = False
        for ability in self.passive_abilities:
            multiplier = ability.effect_multiplier(effect)
            if multiplier is None:
                continue
            value *= multiplier
            found = True
        return value, found

    def modify_outgoing_damage(self, target, amount: float, element: str, game) -> float:
        value = amount
        for ability in self.abilities:
            value = ability.modify_outgoing_damage(target, value, element, game)
        return max(0.0, value)

    def modify_incoming_damage(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> float:
        value = amount
        for ability in self.abilities:
            value = ability.modify_incoming_damage(value, source, source_pos, element, game)
            if value <= 0:
                return 0.0
        return max(0.0, value)

    def on_owner_damaged(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> None:
        for ability in list(self.abilities):
            ability.on_owner_damaged(amount, source, source_pos, element, game)

    def on_ability_activated(self, activated: "GameplayAbility", game) -> None:
        for ability in list(self.abilities):
            if ability is activated:
                continue
            ability.on_owner_ability_activated(activated, game)

    def has_status(self, status: str) -> bool:
        return any(ability.has_status(status) for ability in self.abilities)


class GameplayAbility:
    ability_id = "ability"
    name = "Ability"
    description = "Performs a gameplay action."
    passive = False
    primary_attack = False

    def __init__(self, owner=None, cooldown: float = 0.0) -> None:
        self.owner = owner
        self.cooldown = cooldown
        self.cooldown_remaining = 0.0

    @property
    def ready(self) -> bool:
        return self.remaining_cooldown() <= 0.0

    def remaining_cooldown(self) -> float:
        return max(0.0, self.cooldown_remaining)

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        if self.ready and self.should_auto_activate(game):
            self.activate(game)

    def should_auto_activate(self, game) -> bool:
        return False

    def effective_cooldown(self, game) -> float:
        return self.cooldown * _troop_cooldown_multiplier(game, self.owner)

    def activate(self, game, target=None) -> bool:
        if not self.ready:
            return False
        self.cooldown_remaining = self.effective_cooldown(game)
        self._notify_activated(game)
        return True

    def _notify_activated(self, game) -> None:
        if hasattr(game, "record_ability_activation"):
            game.record_ability_activation(self.owner, self)
        component = getattr(self.owner, "abilities", None)
        if component is not None:
            component.on_ability_activated(self, game)

    def display_name(self, game=None) -> str:
        return self.name

    def detail_lines(self, game=None) -> list[str]:
        lines = []
        cooldown = self.effective_cooldown(game) if game is not None else self.cooldown
        if cooldown > 0:
            lines.append(f"Cooldown {_format_seconds(cooldown)}")
        return lines

    def state_label(self, game=None) -> str:
        remaining = self.remaining_cooldown()
        if self.passive:
            return "PASSIVE"
        return "READY" if remaining <= 0 else _format_seconds(remaining)

    def card(self, game=None) -> AbilityCard:
        return AbilityCard(
            self.ability_id,
            self.display_name(game),
            self.description,
            tuple(self.detail_lines(game)),
            self.passive,
            self.state_label(game),
            tuple(getattr(self, "tags", ())),
        )

    def target_priority_key(self, target, fallback_key: tuple) -> tuple:
        return fallback_key

    def effect_multiplier(self, effect: str) -> float | None:
        return None

    def modify_outgoing_damage(self, target, amount: float, element: str, game) -> float:
        return amount

    def modify_incoming_damage(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> float:
        return amount

    def on_owner_damaged(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> None:
        return

    def on_owner_ability_activated(self, activated: "GameplayAbility", game) -> None:
        return

    def has_status(self, status: str) -> bool:
        return False

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        return


class PassiveAbility(GameplayAbility):
    passive = True

    def update(self, dt: float, game) -> None:
        return

    def effective_cooldown(self, game) -> float:
        return 0.0


class CooldownDrivenAttack(GameplayAbility):
    primary_attack = True

    def __init__(self, owner=None, cooldown_attr: str = "cooldown") -> None:
        super().__init__(owner, cooldown=0.0)
        self.cooldown_attr = cooldown_attr

    @property
    def ready(self) -> bool:
        return self.remaining_cooldown() <= 0.0

    def remaining_cooldown(self) -> float:
        return max(0.0, float(getattr(self.owner, self.cooldown_attr, 0.0)))

    def set_cooldown(self, value: float) -> None:
        setattr(self.owner, self.cooldown_attr, max(0.0, value))

    def update_owner_cooldown(self, dt: float) -> None:
        self.set_cooldown(self.remaining_cooldown() - dt)

    def attack_cooldown(self, game=None) -> float:
        return 0.85

    def state_label(self, game=None) -> str:
        remaining = self.remaining_cooldown()
        return "READY" if remaining <= 0 else _format_seconds(remaining)


class MeleeAttackAbility(CooldownDrivenAttack):
    ability_id = "melee_attack"
    name = "Melee"
    description = "Strikes a nearby target using the owner's combat stats."

    def __init__(
        self,
        owner=None,
        *,
        name: str = "Melee",
        description: str | None = None,
        target_side: str = "enemy",
        element: str = "physical",
        cooldown_attr: str = "cooldown",
        cooldown: float | None = None,
        use_fire_rate: bool = True,
        particle_count: int = 0,
    ) -> None:
        super().__init__(owner, cooldown_attr)
        self.name = name
        self.description = description or self.description
        self.target_side = target_side
        self.element = element
        self.fixed_cooldown = cooldown
        self.use_fire_rate = use_fire_rate
        self.particle_count = particle_count

    def effective_damage(self, game=None) -> float:
        if hasattr(self.owner, "stats"):
            stats = self.owner.stats(game)
            return float(stats.get("damage", getattr(self.owner, "damage", 0.0)))
        return float(getattr(self.owner, "damage", 0.0))

    def effective_range(self, game=None) -> float:
        if hasattr(self.owner, "stats"):
            stats = self.owner.stats(game)
            return float(stats.get("range", getattr(self.owner, "attack_range", 0.0)))
        return float(getattr(self.owner, "attack_range", 0.0))

    def attack_cooldown(self, game=None) -> float:
        if self.fixed_cooldown is not None:
            return self.fixed_cooldown
        if self.use_fire_rate and hasattr(self.owner, "stats"):
            stats = self.owner.stats(game)
            return 1.0 / max(0.05, float(stats.get("fire_rate", 1.0)))
        return 0.85

    def activate(self, game, target=None) -> bool:
        if target is None or not self.ready:
            return False
        controller = getattr(self.owner, "melee", None)
        if controller is None:
            controller = MeleeAttackController(self.owner, self.cooldown_attr)
        reach = self.effective_range(game)
        if not controller.can_reach(target, reach):
            return False
        damage = self.effective_damage(game)
        hit = controller.strike(target, damage, reach, self.attack_cooldown(game), self._damage_callback(game))
        if hit and self.particle_count > 0:
            self._spawn_swing_particles(game)
        if hit:
            self._notify_activated(game)
        return hit

    def _damage_callback(self, game):
        if self.target_side == "friendly":
            return lambda target, amount, source_pos: game.damage_friendly(target, amount, source_pos=source_pos, element=self.element, source=self.owner)
        return lambda target, amount, source_pos: game.damage_enemy(target, amount, self.owner, source_pos=source_pos, element=self.element)

    def _spawn_swing_particles(self, game) -> None:
        from bastion.game.entities import Particle

        direction = pygame.Vector2(getattr(self.owner, "swing_dir", (1, 0)))
        if direction.length_squared() == 0:
            direction = pygame.Vector2(1, 0)
        tangent = pygame.Vector2(-direction.y, direction.x)
        reach = float(getattr(self.owner, "swing_reach", self.effective_range(game)))
        radius = float(getattr(self.owner, "radius", 8.0))
        for _ in range(self.particle_count):
            vel = direction * random.uniform(28, 64) + tangent * random.uniform(-34, 34)
            origin = self.owner.pos + direction * (radius + random.uniform(2, reach * 0.65))
            game.particles.append(Particle(pygame.Vector2(origin), vel, random.uniform(0.10, 0.22), random.uniform(1.2, 2.8)))

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Damage {_format_number(self.effective_damage(game))} {self.element}",
            f"Range {int(self.effective_range(game))}",
            f"Cooldown {_format_seconds(self.attack_cooldown(game))}",
        ]


class MultiShotAttackAbility(CooldownDrivenAttack):
    ability_id = "archer_multi_shot"
    name = "Multi Shot"
    description = "Fires at several enemies in range, using target priority passives when choosing shots."

    def __init__(self, owner=None, max_targets: int = 3) -> None:
        super().__init__(owner, "cooldown")
        self.max_targets = max_targets

    def effective_stats(self, game=None) -> dict[str, float]:
        return self.owner.stats(game) if hasattr(self.owner, "stats") else {}

    def attack_cooldown(self, game=None) -> float:
        stats = self.effective_stats(game)
        return 1.0 / max(0.05, float(stats.get("fire_rate", 1.0)))

    def activate(self, game, target=None) -> bool:
        if target is None or not self.ready:
            return False
        stats = self.effective_stats(game)
        attack_range = float(stats.get("range", getattr(self.owner, "base_range", 0.0)))
        controller = getattr(self.owner, "melee", MeleeAttackController(self.owner))
        if not controller.can_reach(target, attack_range):
            return False

        self.set_cooldown(self.attack_cooldown(game))
        controller.start_swing(target, attack_range)
        damage = float(stats.get("damage", getattr(self.owner, "base_damage", 0.0)))
        enemies = game.targetable_enemies_near(self.owner.pos, attack_range + 36) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(self.owner.pos, attack_range + 36) if hasattr(game, "nearby_enemies") else game.enemies
        candidates = [
            enemy
            for enemy in enemies
            if enemy.alive
            and self.owner._enemy_inside_station(enemy)
            and enemy.pos.distance_to(self.owner.pos) <= attack_range + enemy.radius
        ]
        candidates.sort(key=lambda enemy: (enemy is not target,) + self.owner.abilities.target_priority_key(enemy, (enemy.pos.distance_to(self.owner.pos),)))
        targets = candidates[: self.max_targets]
        if not targets:
            return False

        from bastion.game.entities import Beam

        for index, enemy in enumerate(targets):
            origin = self.owner.pos + pygame.Vector2(self.owner.swing_dir).rotate((index - (len(targets) - 1) / 2) * 6.0) * self.owner.radius
            game.beams.append(Beam(pygame.Vector2(origin), pygame.Vector2(enemy.pos), 0.11, 1))
            game.damage_enemy(enemy, damage, self.owner, source_pos=pygame.Vector2(origin), element="physical")
            game.show_damage_impact(enemy.pos, "multi" if len(targets) > 1 else "single", 0.0)
        self.owner.support_pulse = 0.20
        self._notify_activated(game)
        return True

    def detail_lines(self, game=None) -> list[str]:
        stats = self.effective_stats(game)
        return [
            f"Damage {_format_number(float(stats.get('damage', 0.0)))} physical",
            f"Targets up to {self.max_targets}",
            f"Range {int(float(stats.get('range', 0.0)))}",
            f"Cooldown {_format_seconds(self.attack_cooldown(game))}",
        ]


class AreaElementalAttackAbility(CooldownDrivenAttack):
    ability_id = "rune_mage_ice_attack"
    name = "Ice Attack"
    description = "Detonates elemental damage around the target and applies a slowing effect."

    def __init__(
        self,
        owner=None,
        *,
        radius: float = 42.0,
        element: str = "ice",
        duration: float = 1.55,
        slow_multiplier: float = 0.62,
        attack_slow_multiplier: float = 0.76,
    ) -> None:
        super().__init__(owner, "cooldown")
        self.radius = radius
        self.element = element
        self.duration = duration
        self.slow_multiplier = slow_multiplier
        self.attack_slow_multiplier = attack_slow_multiplier

    def effective_stats(self, game=None) -> dict[str, float]:
        return self.owner.stats(game) if hasattr(self.owner, "stats") else {}

    def attack_cooldown(self, game=None) -> float:
        stats = self.effective_stats(game)
        return 1.0 / max(0.05, float(stats.get("fire_rate", 1.0)))

    def activate(self, game, target=None) -> bool:
        if target is None or not self.ready:
            return False
        stats = self.effective_stats(game)
        attack_range = float(stats.get("range", getattr(self.owner, "base_range", 0.0)))
        controller = getattr(self.owner, "melee", MeleeAttackController(self.owner))
        if not controller.can_reach(target, attack_range):
            return False

        self.set_cooldown(self.attack_cooldown(game))
        controller.start_swing(target, attack_range)
        center = pygame.Vector2(target.pos)
        damage = float(stats.get("damage", getattr(self.owner, "base_damage", 0.0)))
        effect = ElementalEffect(self.element, duration=self.duration, slow_multiplier=self.slow_multiplier, attack_slow_multiplier=self.attack_slow_multiplier)
        game.show_damage_impact(center, "aoe", self.radius)
        hit_any = False
        enemies = game.targetable_enemies_near(center, self.radius + 34) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(center, self.radius + 34) if hasattr(game, "nearby_enemies") else game.enemies
        for enemy in enemies:
            if not enemy.alive:
                continue
            distance = enemy.pos.distance_to(center)
            if distance > self.radius + enemy.radius:
                continue
            falloff = 1.0 - min(0.45, distance / max(1.0, self.radius) * 0.35)
            game.damage_enemy(enemy, damage * falloff, self.owner, source_pos=center, element=self.element)
            game.apply_elemental_effect(enemy, effect, self.owner, center)
            hit_any = True
        if hit_any:
            self.owner.support_pulse = 0.24
            self._notify_activated(game)
        return hit_any

    def detail_lines(self, game=None) -> list[str]:
        stats = self.effective_stats(game)
        slow_pct = int((1.0 - self.slow_multiplier) * 100)
        return [
            f"Damage {_format_number(float(stats.get('damage', 0.0)))} {self.element}",
            f"Area {int(self.radius)}",
            f"Slow {slow_pct}% for {_format_seconds(self.duration)}",
            f"Cooldown {_format_seconds(self.attack_cooldown(game))}",
        ]


class EnemyRangedAttackAbility(CooldownDrivenAttack):
    ability_id = "enemy_ranged_attack"
    name = "Ranged Attack"
    description = "Fires a projectile at a friendly target."

    def __init__(self, owner=None) -> None:
        super().__init__(owner, "attack_cooldown")

    def effective_stats(self, game=None) -> dict[str, float]:
        if hasattr(self.owner, "stats"):
            return self.owner.stats(game)
        return {
            "damage": float(getattr(self.owner, "damage", 0.0)),
            "range": float(getattr(self.owner, "attack_range", 0.0)),
            "fire_rate": float(getattr(self.owner, "fire_rate", 1.0)),
        }

    def attack_cooldown(self, game=None) -> float:
        stats = self.effective_stats(game)
        return 1.0 / max(0.05, float(stats.get("fire_rate", 1.0)))

    def activate(self, game, target=None) -> bool:
        if target is None or not self.ready:
            return False
        stats = self.effective_stats(game)
        if self.owner.pos.distance_to(target.pos) > float(stats.get("range", getattr(self.owner, "attack_range", 0.0))) + float(getattr(target, "radius", 0.0)):
            return False
        from bastion.game.entities import EnemyProjectile

        self.set_cooldown(self.attack_cooldown(game))
        if hasattr(self.owner, "start_attack_animation"):
            self.owner.start_attack_animation(target=target, phase="full", duration=0.38)
        game.enemy_projectiles.append(
            EnemyProjectile(
                pos=pygame.Vector2(self.owner.pos),
                target=target,
                speed=float(getattr(self.owner, "projectile_speed", 260.0)),
                damage=float(stats.get("damage", getattr(self.owner, "damage", 0.0))),
                owner=self.owner,
            )
        )
        game.spawn_hit(self.owner.pos, 2)
        self._notify_activated(game)
        return True

    def detail_lines(self, game=None) -> list[str]:
        stats = self.effective_stats(game)
        return [
            f"Damage {_format_number(float(stats.get('damage', 0.0)))} physical",
            f"Range {int(float(stats.get('range', 0.0)))}",
            f"Cooldown {_format_seconds(self.attack_cooldown(game))}",
        ]


class BossTelegraphedAbility(GameplayAbility):
    target_radius = 0.0

    def __init__(
        self,
        owner=None,
        *,
        ability_id: str,
        name: str,
        element: str,
        damage: float,
        radius: float,
        cooldown: float,
        cast_time: float,
        status_duration: float = 0.0,
        slow_multiplier: float = 1.0,
        knockback: float = 0.0,
    ) -> None:
        super().__init__(owner, cooldown)
        self.ability_id = ability_id
        self.name = name
        self.element = element
        self.damage = float(damage)
        self.radius = float(radius)
        self.cast_time = max(0.05, float(cast_time))
        self.status_duration = max(0.0, float(status_duration))
        self.slow_multiplier = max(0.05, float(slow_multiplier))
        self.knockback = max(0.0, float(knockback))
        self.cast_remaining = 0.0
        self.cast_total = self.cast_time
        self.cast_pos: pygame.Vector2 | None = None

    @property
    def casting(self) -> bool:
        return self.cast_remaining > 0.0 and self.cast_pos is not None

    def effective_cooldown(self, game) -> float:
        return self.cooldown

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        if self.casting:
            self.cast_remaining = max(0.0, self.cast_remaining - dt)
            if self.cast_remaining <= 0.0:
                self.finish_cast(game)
            return
        if self.ready and self.should_auto_activate(game):
            self.activate(game)

    def should_auto_activate(self, game) -> bool:
        return self._find_target(game) is not None

    def activate(self, game, target=None) -> bool:
        if not self.ready:
            return False
        target = target or self._find_target(game)
        if target is None:
            return False
        self.cast_pos = pygame.Vector2(getattr(target, "pos", target))
        self.cast_remaining = self.cast_time
        self.cast_total = self.cast_time
        self.cooldown_remaining = self.effective_cooldown(game)
        self._notify_activated(game)
        self._start_attack_windup(game, target)
        if hasattr(game, "spawn_hit"):
            game.spawn_hit(getattr(self.owner, "pos", self.cast_pos), 2)
        return True

    def finish_cast(self, game) -> None:
        self.cast_remaining = 0.0

    def _find_target(self, game):
        troops = _nearby_troops(game, self.owner.pos, max(1.0, _owner_range(self.owner, game, 220.0)) + 80.0)
        candidates = [troop for troop in troops if getattr(troop, "alive", False)]
        if not candidates:
            return None
        return min(candidates, key=lambda troop: troop.pos.distance_to(self.owner.pos))

    def _start_attack_windup(self, game, target=None) -> None:
        if hasattr(self.owner, "start_attack_animation"):
            self.owner.start_attack_animation(target=target, target_pos=self.cast_pos, phase="windup", duration=self.cast_time)

    def _start_attack_impact(self, game, *, target=None, target_pos: pygame.Vector2 | None = None, duration: float = 0.24) -> None:
        if hasattr(self.owner, "start_attack_animation"):
            self.owner.start_attack_animation(target=target, target_pos=target_pos, phase="impact", duration=duration)

    def _nearest_animation_target(self, game, radius: float | None = None):
        search_radius = radius if radius is not None else max(1.0, _owner_range(self.owner, game, 220.0)) + 80.0
        return _nearest_troop(game, self.owner.pos, search_radius)

    def _apply_status(self, target, center: pygame.Vector2) -> None:
        if self.element == "fire" and self.status_duration > 0 and hasattr(target, "apply_burn"):
            target.apply_burn(max(1.0, self.damage * 0.22), self.status_duration, self.owner)
        elif self.element == "ice" and self.status_duration > 0 and hasattr(target, "apply_slow"):
            target.apply_slow(self.slow_multiplier, self.status_duration, self.slow_multiplier)
        elif self.element == "lightning" and self.status_duration > 0 and hasattr(target, "apply_stun"):
            target.apply_stun(self.status_duration)
        if self.knockback > 0 and hasattr(target, "vel"):
            direction = target.pos - center
            if direction.length_squared() == 0:
                direction = pygame.Vector2(1, 0)
            target.vel += direction.normalize() * self.knockback

    def detail_lines(self, game=None) -> list[str]:
        lines = [
            f"Damage {_format_number(self.damage)} {self.element}",
            f"Tell {_format_seconds(self.cast_time)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]
        if self.radius > 0:
            lines.insert(1, f"Area {int(self.radius)}")
        return lines

    def state_label(self, game=None) -> str:
        if self.casting:
            return "CAST"
        return super().state_label(game)

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if not self.casting or self.cast_pos is None:
            return
        progress = 1.0 - self.cast_remaining / max(0.01, self.cast_total)
        radius = max(10.0, self.radius if self.radius > 0 else self.target_radius)
        screen = camera.world_to_screen(self.cast_pos, viewport)
        alpha = int(42 + 118 * progress)
        draw_circle_alpha(surface, screen, radius * camera.zoom, config.PALETTE.white, alpha, max(1, int(2 * camera.zoom)))
        draw_circle_alpha(surface, screen, radius * (0.30 + progress * 0.46) * camera.zoom, config.PALETTE.white, int(alpha * 0.55), 1)


class BossTelegraphedStrikeAbility(BossTelegraphedAbility):
    description = "Marks a party member before striking the target area."
    target_radius = 20.0

    def __init__(self, owner=None, **kwargs) -> None:
        self.targeting = str(kwargs.pop("targeting", "nearest"))
        super().__init__(owner, **kwargs)

    def _find_target(self, game):
        radius = max(1.0, _owner_range(self.owner, game, 230.0)) + 80.0
        candidates = [troop for troop in _nearby_troops(game, self.owner.pos, radius) if getattr(troop, "alive", False)]
        if not candidates:
            return None
        if self.targeting == "random":
            return random.choice(candidates)
        if self.targeting == "weakest":
            return min(candidates, key=lambda troop: (troop.health / max(1.0, troop.max_health), troop.pos.distance_to(self.owner.pos)))
        return min(candidates, key=lambda troop: troop.pos.distance_to(self.owner.pos))

    def finish_cast(self, game) -> None:
        center = pygame.Vector2(self.cast_pos) if self.cast_pos is not None else pygame.Vector2(self.owner.pos)
        self.cast_pos = None
        self.cast_remaining = 0.0
        self._start_attack_impact(game, target_pos=center)
        hit_radius = max(8.0, self.radius)
        if hasattr(game, "show_damage_impact"):
            game.show_damage_impact(center, "aoe" if hit_radius > 18 else "single", hit_radius)
        for troop in _nearby_troops(game, center, hit_radius + 24.0):
            if not getattr(troop, "alive", False) or troop.pos.distance_to(center) > hit_radius + troop.radius:
                continue
            game.damage_friendly(troop, self.damage, source_pos=center, element=self.element, source=self.owner)
            self._apply_status(troop, center)


class BossPartyPulseAbility(BossTelegraphedAbility):
    description = "Telegraphs a radial burst around the boss."

    def should_auto_activate(self, game) -> bool:
        return any(
            getattr(troop, "alive", False) and troop.pos.distance_to(self.owner.pos) <= self.radius + troop.radius
            for troop in _nearby_troops(game, self.owner.pos, self.radius + 32.0)
        )

    def activate(self, game, target=None) -> bool:
        if not self.ready or not self.should_auto_activate(game):
            return False
        target = target or self._nearest_animation_target(game, self.radius + 32.0)
        self.cast_pos = pygame.Vector2(self.owner.pos)
        self.cast_remaining = self.cast_time
        self.cast_total = self.cast_time
        self.cooldown_remaining = self.effective_cooldown(game)
        self._notify_activated(game)
        self._start_attack_windup(game, target)
        return True

    def finish_cast(self, game) -> None:
        center = pygame.Vector2(self.owner.pos)
        self.cast_pos = None
        self.cast_remaining = 0.0
        self._start_attack_impact(game, target=self._nearest_animation_target(game, self.radius + 32.0))
        if hasattr(game, "show_damage_impact"):
            game.show_damage_impact(center, "aoe", self.radius)
        for troop in _nearby_troops(game, center, self.radius + 32.0):
            if not getattr(troop, "alive", False) or troop.pos.distance_to(center) > self.radius + troop.radius:
                continue
            game.damage_friendly(troop, self.damage, source_pos=center, element=self.element, source=self.owner)
            self._apply_status(troop, center)

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if self.casting:
            self.cast_pos = pygame.Vector2(self.owner.pos)
        super().draw_preview(surface, camera, viewport)


class BossProjectileAbility(BossTelegraphedAbility):
    description = "Casts a hostile projectile with optional area and ground effects."

    def __init__(
        self,
        owner=None,
        *,
        speed: float = 180.0,
        ground_duration: float = 0.0,
        burn_dps: float = 0.0,
        burn_duration: float = 0.0,
        **kwargs,
    ) -> None:
        super().__init__(owner, **kwargs)
        self.speed = float(speed)
        self.ground_duration = max(0.0, float(ground_duration))
        self.burn_dps = max(0.0, float(burn_dps))
        self.burn_duration = max(0.0, float(burn_duration))

    def finish_cast(self, game) -> None:
        target_pos = pygame.Vector2(self.cast_pos) if self.cast_pos is not None else pygame.Vector2(self.owner.pos)
        self.cast_pos = None
        self.cast_remaining = 0.0
        self._start_attack_impact(game, target_pos=target_pos)
        from bastion.game.entities import HostileAoeProjectile

        game.enemy_projectiles.append(
            HostileAoeProjectile(
                pygame.Vector2(self.owner.pos),
                target_pos,
                self.speed,
                self.damage,
                self.owner,
                element=self.element,
                radius=self.radius,
                burn_dps=self.burn_dps,
                burn_duration=self.burn_duration,
                slow_multiplier=self.slow_multiplier,
                status_duration=self.status_duration,
                ground_duration=self.ground_duration,
            )
        )


class BossRadialProjectileAbility(BossProjectileAbility):
    description = "Telegraphs and releases hostile projectiles in all directions."

    def __init__(self, owner=None, *, projectiles: int = 8, **kwargs) -> None:
        super().__init__(owner, **kwargs)
        self.projectiles = max(1, int(projectiles))

    def should_auto_activate(self, game) -> bool:
        return any(getattr(troop, "alive", False) for troop in _nearby_troops(game, self.owner.pos, max(1.0, _owner_range(self.owner, game, 260.0))))

    def activate(self, game, target=None) -> bool:
        if not self.ready or not self.should_auto_activate(game):
            return False
        target = target or self._nearest_animation_target(game, max(1.0, _owner_range(self.owner, game, 260.0)))
        self.cast_pos = pygame.Vector2(self.owner.pos)
        self.cast_remaining = self.cast_time
        self.cast_total = self.cast_time
        self.cooldown_remaining = self.effective_cooldown(game)
        self._notify_activated(game)
        self._start_attack_windup(game, target)
        return True

    def finish_cast(self, game) -> None:
        center = pygame.Vector2(self.owner.pos)
        self.cast_pos = None
        self.cast_remaining = 0.0
        self._start_attack_impact(game, target=self._nearest_animation_target(game, max(1.0, _owner_range(self.owner, game, 260.0))))
        from bastion.game.entities import HostileAoeProjectile

        if hasattr(game, "show_damage_impact"):
            game.show_damage_impact(center, "aoe", max(50.0, self.radius))
        for index in range(self.projectiles):
            angle = index / self.projectiles * math.tau
            direction = pygame.Vector2(math.cos(angle), math.sin(angle))
            game.enemy_projectiles.append(
                HostileAoeProjectile(
                    center + direction * max(8.0, getattr(self.owner, "radius", 12.0)),
                    center + direction * 420.0,
                    self.speed,
                    self.damage,
                    self.owner,
                    element=self.element,
                    radius=self.radius,
                    burn_dps=self.burn_dps,
                    burn_duration=self.burn_duration,
                    slow_multiplier=self.slow_multiplier,
                    status_duration=self.status_duration,
                    ground_duration=self.ground_duration,
                    max_distance=420.0,
                )
            )


class BossDashAbility(PassiveAbility):
    ability_id = "boss_dash"
    name = "Boss Dash"
    description = "Occasionally repositions away from the party."

    def __init__(
        self,
        owner=None,
        *,
        ability_id: str = "boss_dash",
        name: str = "Boss Dash",
        distance: float = 150.0,
        cooldown: float = 6.0,
        trigger_distance: float = 145.0,
        chance: float = 0.35,
        teleport: bool = False,
    ) -> None:
        super().__init__(owner)
        self.ability_id = ability_id
        self.name = name
        self.distance = float(distance)
        self.cooldown = float(cooldown)
        self.cooldown_remaining = random.uniform(0.0, self.cooldown)
        self.trigger_distance = float(trigger_distance)
        self.chance = max(0.0, min(1.0, float(chance)))
        self.teleport = bool(teleport)

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        if self.cooldown_remaining > 0 or not getattr(self.owner, "alive", False):
            return
        target = _nearest_troop(game, self.owner.pos, self.trigger_distance + 80.0)
        if target is None or target.pos.distance_to(self.owner.pos) > self.trigger_distance + target.radius:
            return
        if random.random() > self.chance:
            self.cooldown_remaining = self.cooldown * 0.45
            return
        away = self.owner.pos - target.pos
        if away.length_squared() == 0:
            away = pygame.Vector2(1, 0)
        tangent = pygame.Vector2(-away.y, away.x)
        direction = (away.normalize() * 0.82 + tangent.normalize() * random.uniform(-0.42, 0.42))
        if direction.length_squared() == 0:
            direction = away
        direction = direction.normalize()
        if self.teleport:
            self._teleport(game, direction)
        else:
            self.owner.vel += direction * self.distance
        self.cooldown_remaining = self.cooldown
        if hasattr(game, "spawn_burst"):
            game.spawn_burst(self.owner.pos, 10, 84)

    def _teleport(self, game, direction: pygame.Vector2) -> None:
        destination = pygame.Vector2(self.owner.pos)
        for scale in (1.0, 0.78, 0.56, 0.34):
            candidate = self.owner.pos + direction * self.distance * scale
            if game.grid.circle_clear(candidate, getattr(self.owner, "collision_radius", getattr(self.owner, "radius", 12.0))):
                destination = candidate
                break
        if hasattr(game, "beams"):
            from bastion.game.entities import Beam

            game.beams.append(Beam(pygame.Vector2(self.owner.pos), pygame.Vector2(destination), 0.16, 2))
        self.owner.pos = destination
        self.owner.vel.update(0, 0)

    def detail_lines(self, game=None) -> list[str]:
        verb = "Teleport" if self.teleport else "Dash"
        return [f"{verb} {int(self.distance)}", f"Cooldown {_format_seconds(self.cooldown)}"]


class BossContactBurnPassive(PassiveAbility):
    ability_id = "boss_contact_burn"
    name = "Molten Skin"
    description = "Melee attackers are set on fire."

    def __init__(
        self,
        owner=None,
        *,
        ability_id: str = "boss_contact_burn",
        name: str = "Molten Skin",
        damage_per_second: float = 4.0,
        duration: float = 3.0,
        cooldown: float = 0.75,
        radius: float = 44.0,
    ) -> None:
        super().__init__(owner)
        self.ability_id = ability_id
        self.name = name
        self.damage_per_second = float(damage_per_second)
        self.duration = float(duration)
        self.cooldown = float(cooldown)
        self.cooldown_remaining = 0.0
        self.radius = float(radius)

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)

    def on_owner_damaged(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> None:
        if amount <= 0 or self.cooldown_remaining > 0 or source is None or not hasattr(source, "apply_burn"):
            return
        if not getattr(source, "alive", False) or source.pos.distance_to(self.owner.pos) > self.radius + getattr(source, "radius", 0.0):
            return
        source.apply_burn(self.damage_per_second, self.duration, self.owner)
        self.cooldown_remaining = self.cooldown
        if hasattr(game, "beams"):
            from bastion.game.entities import Beam

            game.beams.append(Beam(pygame.Vector2(self.owner.pos), pygame.Vector2(source.pos), 0.12, 1))

    def detail_lines(self, game=None) -> list[str]:
        return [f"Burn {_format_number(self.damage_per_second)}/s", f"Duration {_format_seconds(self.duration)}"]


class BossSlowAuraPassive(PassiveAbility):
    ability_id = "boss_slowing_presence"
    name = "Slowing Presence"
    description = "Slows troops that stay too close."

    def __init__(
        self,
        owner=None,
        *,
        ability_id: str = "boss_slowing_presence",
        name: str = "Slowing Presence",
        radius: float = 140.0,
        slow_multiplier: float = 0.72,
        attack_slow_multiplier: float = 0.84,
        interval: float = 0.25,
    ) -> None:
        super().__init__(owner)
        self.ability_id = ability_id
        self.name = name
        self.radius = float(radius)
        self.slow_multiplier = float(slow_multiplier)
        self.attack_slow_multiplier = float(attack_slow_multiplier)
        self.interval = max(0.05, float(interval))
        self.timer = random.uniform(0.0, self.interval)

    def update(self, dt: float, game) -> None:
        self.timer -= dt
        if self.timer > 0 or not getattr(self.owner, "alive", False):
            return
        self.timer += self.interval
        for troop in _nearby_troops(game, self.owner.pos, self.radius + 24.0):
            if getattr(troop, "alive", False) and troop.pos.distance_to(self.owner.pos) <= self.radius + troop.radius and hasattr(troop, "apply_slow"):
                troop.apply_slow(self.slow_multiplier, self.interval * 2.4, self.attack_slow_multiplier)

    def detail_lines(self, game=None) -> list[str]:
        slow_pct = int((1.0 - self.slow_multiplier) * 100)
        return [f"Slow {slow_pct}%", f"Radius {int(self.radius)}"]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        draw_circle_alpha(surface, screen, self.radius * camera.zoom, config.PALETTE.white, 12, 1)


def create_boss_ability_from_definition(spec, owner=None) -> GameplayAbility:
    values = dict(getattr(spec, "values", {}))
    values.setdefault("ability_id", getattr(spec, "ability_id", "boss_ability"))
    values.setdefault("name", getattr(spec, "name", "Boss Ability"))
    kind = str(getattr(spec, "kind", values.get("kind", "telegraphed_strike")))
    if kind == "telegraphed_strike":
        return BossTelegraphedStrikeAbility(owner, **_boss_cast_values(values))
    if kind == "party_pulse":
        return BossPartyPulseAbility(owner, **_boss_cast_values(values))
    if kind == "projectile":
        return BossProjectileAbility(owner, **_boss_projectile_values(values))
    if kind == "radial_projectiles":
        return BossRadialProjectileAbility(owner, **_boss_projectile_values(values))
    if kind == "dash":
        return BossDashAbility(owner, **_boss_passive_values(values))
    if kind == "contact_burn":
        return BossContactBurnPassive(owner, **_boss_passive_values(values))
    if kind == "slow_aura":
        return BossSlowAuraPassive(owner, **_boss_passive_values(values))
    raise KeyError(f"Unknown boss ability kind '{kind}'.")


def _boss_cast_values(values: dict) -> dict:
    allowed = {
        "ability_id",
        "name",
        "element",
        "damage",
        "radius",
        "cooldown",
        "cast_time",
        "targeting",
        "status_duration",
        "slow_multiplier",
        "knockback",
    }
    result = {key: values[key] for key in allowed if key in values}
    result.setdefault("element", "physical")
    result.setdefault("damage", 1.0)
    result.setdefault("radius", 0.0)
    result.setdefault("cooldown", 1.0)
    result.setdefault("cast_time", 0.5)
    return result


def _boss_projectile_values(values: dict) -> dict:
    result = _boss_cast_values(values)
    for key in ("speed", "ground_duration", "burn_dps", "burn_duration", "projectiles"):
        if key in values:
            result[key] = values[key]
    return result


def _boss_passive_values(values: dict) -> dict:
    return {key: value for key, value in values.items() if key not in {"kind"}}


class TowerProjectileAttackAbility(CooldownDrivenAttack):
    ability_id = "tower_projectile_attack"
    description = "Fires the tower's configured projectile using tower stats, research, items, and installed passive modifiers."

    def __init__(self, owner=None) -> None:
        super().__init__(owner, "cooldown")

    def update(self, dt: float, game) -> None:
        self.update_owner_cooldown(dt)
        if not self.ready:
            return
        target = self.find_target(game)
        if target is not None:
            self.activate(game, target)

    def effective_stats(self, game=None) -> dict[str, float | str]:
        return self.owner.stats(game) if hasattr(self.owner, "stats") else {}

    def attack_cooldown(self, game=None) -> float:
        stats = self.effective_stats(game)
        return 1.0 / max(0.05, float(stats.get("fire_rate", 1.0)))

    def projectile_count(self) -> int:
        return max(1, int(round(self.owner.mod_effect("projectile_count_multiplier", 1.0)))) if hasattr(self.owner, "mod_effect") else 1

    def find_target(self, game):
        stats = self.effective_stats(game)
        attack_range = float(stats.get("range", 0.0))
        enemies = game.targetable_enemies_near(self.owner.pos, attack_range + 36) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(self.owner.pos, attack_range + 36) if hasattr(game, "nearby_enemies") else game.enemies
        townhall_pos = game.grid.world_center(game.grid.townhall_cell)
        candidates = [
            enemy
            for enemy in enemies
            if enemy.alive and enemy.pos.distance_to(self.owner.pos) <= attack_range + enemy.radius
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda enemy: self.owner.abilities.target_priority_key(enemy, (enemy.pos.distance_to(townhall_pos),)))

    def activate(self, game, target=None) -> bool:
        if target is None or not self.ready:
            return False
        stats = self.effective_stats(game)
        self.set_cooldown(self.attack_cooldown(game))
        if hasattr(game, "play_tower_sound"):
            game.play_tower_sound(self.owner.kind)
        if hasattr(self.owner, "signal_shot"):
            self.owner.signal_shot(target)

        from bastion.game.entities import Projectile

        projectile_count = self.projectile_count()
        impact_kind = damage_impact_kind(float(stats.get("aoe", 0.0)), projectile_count, str(stats.get("effect", "")))
        attack_range = float(stats.get("range", 0.0))
        for index in range(projectile_count):
            projectile = Projectile(
                pos=pygame.Vector2(self.owner.pos),
                target=target,
                speed=float(stats.get("projectile_speed", 0.0)),
                damage=float(stats.get("damage", 0.0)),
                owner=self.owner,
                kind=self.owner.kind,
                aoe=float(stats.get("aoe", 0.0)),
                effect=str(stats.get("effect", "")),
                impact_kind=impact_kind,
                accuracy=float(stats.get("accuracy", 0.9)),
                max_range=attack_range + target.radius + 32.0,
            )
            if projectile_count > 1:
                offset = pygame.Vector2(0, -1).rotate((index - (projectile_count - 1) / 2) * 16)
                projectile.pos += offset * 3
                projectile.trail = [pygame.Vector2(projectile.pos)]
            game.projectiles.append(projectile)
        spawn_launch_fx(game, self.owner, projectile_count, impact_kind)
        self._notify_activated(game)
        return True

    def display_name(self, game=None) -> str:
        stats = self.effective_stats(game)
        effect = str(stats.get("effect", ""))
        kind = getattr(self.owner, "kind", "")
        if effect == "burn":
            return "Flaming Arrow"
        if effect == "chain":
            return "Chain Lightning"
        if effect == "slow":
            return "Ice Burst"
        if kind == "cannon":
            return "Cannon Shot"
        if kind == "wizard":
            return "Arcane Blast"
        return "Arrow Shot"

    def detail_lines(self, game=None) -> list[str]:
        stats = self.effective_stats(game)
        effect = str(stats.get("effect", ""))
        lines = [
            f"Damage {_format_number(float(stats.get('damage', 0.0)))} {projectile_element(effect)}",
            f"Range {int(float(stats.get('range', 0.0)))}",
            f"Cooldown {_format_seconds(self.attack_cooldown(game))}",
            f"Accuracy {int(float(stats.get('accuracy', 0.0)) * 100)}%",
        ]
        projectile_count = self.projectile_count()
        if projectile_count > 1:
            lines.append(f"Projectiles {projectile_count}")
        aoe = float(stats.get("aoe", 0.0))
        if aoe > 0:
            lines.append(f"Area {int(aoe)}")
        effect_line = projectile_effect_line(effect, self.owner, game)
        if effect_line:
            lines.append(effect_line)
        return lines


class TauntAbility(GameplayAbility):
    ability_id = "taunt"
    name = "Taunt"
    description = "Forces nearby enemies to focus this unit for a short duration."

    def __init__(self, owner=None, radius: float = 135.0, duration: float = 3.7, cooldown: float = 8.0) -> None:
        super().__init__(owner, cooldown)
        self.radius = radius
        self.duration = duration

    def should_auto_activate(self, game) -> bool:
        if not self.owner.alive or not getattr(self.owner, "attack_enabled", True):
            return False
        enemies = game.targetable_enemies_near(self.owner.pos, self.radius + 24) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(self.owner.pos, self.radius + 24) if hasattr(game, "nearby_enemies") else game.enemies
        for enemy in enemies:
            if enemy.alive and enemy.pos.distance_to(self.owner.pos) <= self.radius + enemy.radius:
                return True
        return False

    def activate(self, game, target=None) -> bool:
        affected = []
        enemies = game.targetable_enemies_near(self.owner.pos, self.radius + 24) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(self.owner.pos, self.radius + 24) if hasattr(game, "nearby_enemies") else game.enemies
        for enemy in enemies:
            if enemy.alive and enemy.pos.distance_to(self.owner.pos) <= self.radius + enemy.radius:
                if hasattr(enemy, "aggro"):
                    enemy.aggro.add_threat(self.owner, 40.0, "taunt")
                enemy.apply_taunt(self.owner, self.duration)
                affected.append(enemy)

        if not affected or not super().activate(game):
            return False

        from bastion.game.entities import Beam

        self.owner.taunt_pulse = 0.55
        for enemy in affected:
            game.beams.append(Beam(pygame.Vector2(self.owner.pos), pygame.Vector2(enemy.pos), 0.16, 1))
        for _ in range(14):
            game.spawn_hit(self.owner.pos, 1)
        return True

    def effective_cooldown(self, game) -> float:
        research = getattr(game, "research", None)
        cooldown = self.cooldown
        if research is not None:
            cooldown *= research.inverse_multiplier("warrior_taunt_cooldown")
        return cooldown * _troop_cooldown_multiplier(game, self.owner)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Radius {int(self.radius)}",
            f"Duration {_format_seconds(self.duration)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.radius * camera.zoom, _tactical_preview_alpha(self.ready), 1)


class ChainLightningAbility(GameplayAbility):
    ability_id = "chain_lightning"
    name = "Chain Lightning"
    description = "Arcs lightning through clustered enemies, dealing reduced damage after each jump."

    def __init__(
        self,
        owner=None,
        radius: float = 92.0,
        damage: float = 13.0,
        jumps: int = 4,
        chain_radius: float = 92.0,
        cooldown: float = 2.35,
    ) -> None:
        super().__init__(owner, cooldown)
        self.radius = radius
        self.damage = damage
        self.jumps = jumps
        self.chain_radius = chain_radius

    def effective_radius(self, game=None) -> float:
        return max(self.radius, _owner_range(self.owner, game, self.radius))

    def effective_jumps(self) -> int:
        bonus = 0
        if hasattr(self.owner, "hero_effect_total"):
            bonus = int(self.owner.hero_effect_total("chain_lightning_jumps"))
        return max(1, self.jumps + bonus)

    def should_auto_activate(self, game) -> bool:
        if not getattr(self.owner, "attack_enabled", True):
            return False
        return self._find_target(game) is not None

    def _find_target(self, game):
        if not self.owner.alive or not getattr(self.owner, "attack_enabled", True):
            return None
        radius = self.effective_radius(game)
        enemies = game.targetable_enemies_near(self.owner.pos, radius + 24) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(self.owner.pos, radius + 24) if hasattr(game, "nearby_enemies") else game.enemies
        candidates = [
            enemy
            for enemy in enemies
            if enemy.alive
            and enemy.pos.distance_to(self.owner.pos) <= radius + enemy.radius
            and self.owner._enemy_inside_station(enemy)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda enemy: enemy.pos.distance_to(self.owner.pos))

    def effective_damage(self, game=None) -> float:
        research = getattr(game, "research", None)
        damage_bonus = research.multiplier("wizard_lightning_damage") if research is not None else 1.0
        item_damage_bonus = game.item_multiplier("troop_damage_multiplier") if hasattr(game, "item_multiplier") else 1.0
        magic_damage = self.owner.stats(game).get("magic_damage", self.damage) if hasattr(self.owner, "stats") else self.damage
        return max(self.damage, float(magic_damage) * 1.05) * damage_bonus * item_damage_bonus

    def activate(self, game, target=None) -> bool:
        target = self._find_target(game)
        if target is None or not super().activate(game):
            return False
        hits = game.chain_lightning(
            pygame.Vector2(self.owner.pos),
            target,
            self.effective_damage(game),
            self.owner,
            jumps=self.effective_jumps(),
            radius=self.chain_radius,
            falloff=0.68,
            stun=0.18,
        )
        if hits <= 0:
            self.cooldown_remaining = 0.0
            return False
        self.owner.support_pulse = 0.28
        game.spawn_hit(self.owner.pos, 3)
        return True

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Damage {_format_number(self.effective_damage(game))} lightning",
            f"Jumps {self.effective_jumps()}",
            f"Arc {int(self.chain_radius)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.effective_radius(None) * camera.zoom, _tactical_preview_alpha(self.ready), 1)


class SupportOverTimeAbility(GameplayAbility):
    target_class = ""
    reason = "heal"
    element = "physical"

    def __init__(self, owner=None, radius: float = 100.0, rate: float = 1.0, cooldown: float = 0.0, tick_interval: float = 0.0) -> None:
        super().__init__(owner, cooldown)
        self.radius = radius
        self.rate = rate
        self.tick_interval = tick_interval
        self.tick_timer = 0.0
        self.target = None
        self.fx_timer = 0.0
        self.xp_bank = 0.0

    def effective_rate(self, game=None) -> float:
        value = self.rate
        key = {
            "heal": "healing_amount_multiplier",
            "repair": "repair_amount_multiplier",
            "shield": "shield_repair_amount_multiplier",
        }.get(self.reason)
        if key is not None and hasattr(self.owner, "hero_multiplier"):
            value *= self.owner.hero_multiplier(key)
        return value

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        self.fx_timer = max(0.0, self.fx_timer - dt)
        self.tick_timer = max(0.0, self.tick_timer - dt)
        if not self.owner.alive:
            self.target = None
            return

        target = self.find_target(game)
        self.target = target
        if target is None:
            return

        interval = self.effective_tick_interval(game)
        if interval > 0:
            if self.tick_timer > 0:
                return
            self.tick_timer = interval
            amount = self.effective_rate(game) * self.tick_interval
        else:
            amount = self.effective_rate(game) * dt

        actual = game.restore_friendly(target, amount, self.owner, self.reason, element=self.element)
        if actual <= 0:
            return

        self._award_support_xp(game, actual)
        self._show_support_fx(game, target)

    def _award_support_xp(self, game, actual: float) -> None:
        from bastion.game.entities import FloatingText

        self.xp_bank += actual
        while self.xp_bank >= 18.0:
            self.xp_bank -= 18.0
            if self.owner.add_xp(4):
                game.texts.append(FloatingText(pygame.Vector2(self.owner.pos), "READY", 0.9))

    def _show_support_fx(self, game, target) -> None:
        from bastion.game.entities import Beam

        self.owner.support_pulse = 0.35
        self.owner.support_target = target
        if self.fx_timer <= 0:
            self.fx_timer = 0.14
            game.beams.append(Beam(pygame.Vector2(self.owner.pos), pygame.Vector2(target.pos), 0.16, 1))
            game.spawn_hit(target.pos, 1)

    def find_target(self, game):
        return None

    def effective_tick_interval(self, game) -> float:
        return self.tick_interval * _troop_cooldown_multiplier(game, self.owner)

    def detail_lines(self, game=None) -> list[str]:
        lines = [
            f"Rate {_format_number(self.effective_rate(game))}/s",
            f"Radius {int(self.radius)}",
        ]
        interval = self.effective_tick_interval(game) if game is not None else self.tick_interval
        if interval > 0:
            lines.append(f"Tick {_format_seconds(interval)}")
        return lines

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.radius * camera.zoom)


class HealTroopAbility(SupportOverTimeAbility):
    ability_id = "heal_troop"
    name = "Heal"
    description = "Restores health to the most injured nearby troop."
    reason = "heal"
    element = "holy"

    def __init__(self, owner=None, radius: float = 128.0, rate: float = 10.0) -> None:
        super().__init__(owner, radius, rate, tick_interval=0.58)

    def effective_tick_interval(self, game) -> float:
        research = getattr(game, "research", None)
        interval = self.tick_interval
        if research is not None:
            interval *= research.inverse_multiplier("cleric_healing_cooldown")
        return interval * _troop_cooldown_multiplier(game, self.owner)

    def find_target(self, game):
        troops = game.nearby_troops(self.owner.pos, self.radius + 24) if hasattr(game, "nearby_troops") else game.troops
        candidates = [
            troop
            for troop in troops
            if troop.alive
            and troop.health < troop.max_health
            and troop.pos.distance_to(self.owner.pos) <= self.radius + troop.radius
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda troop: (troop.health / max(1.0, troop.max_health), troop.pos.distance_to(self.owner.pos)))


class RepairTowerAbility(SupportOverTimeAbility):
    ability_id = "repair_tower"
    name = "Repair"
    description = "Repairs the most damaged nearby tower or structure."
    reason = "repair"

    def __init__(self, owner=None, radius: float = 118.0, rate: float = 8.0) -> None:
        super().__init__(owner, radius, rate)

    def find_target(self, game):
        candidates = [
            tower
            for tower in game.towers
            if tower.alive
            and tower.health < tower.max_health
            and tower.pos.distance_to(self.owner.pos) <= self.radius + tower.radius
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda tower: (tower.health / max(1.0, tower.max_health), tower.pos.distance_to(self.owner.pos)))


class RechargeShieldAbility(SupportOverTimeAbility):
    ability_id = "recharge_shield"
    name = "Shield Recharge"
    description = "Channels rune energy into nearby shield generators."
    reason = "shield"

    def __init__(self, owner=None, radius: float = 132.0, rate: float = 12.0) -> None:
        super().__init__(owner, radius, rate, tick_interval=0.50)

    def find_target(self, game):
        generators = [
            generator
            for generator in getattr(game, "shield_generators", lambda: [])()
            if generator.alive
            and generator.shield < generator.shield_max
            and generator.pos.distance_to(self.owner.pos) <= self.radius + generator.radius
        ]
        if not generators:
            return None
        return min(generators, key=lambda generator: (generator.shield / max(1.0, generator.shield_max), generator.pos.distance_to(self.owner.pos)))

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        self.fx_timer = max(0.0, self.fx_timer - dt)
        self.tick_timer = max(0.0, self.tick_timer - dt)
        if not self.owner.alive:
            self.target = None
            return
        target = self.find_target(game)
        self.target = target
        if target is None:
            return
        if self.tick_timer > 0:
            return
        self.tick_timer = self.effective_tick_interval(game)
        actual = target.restore_shield(self.effective_rate(game) * self.tick_interval)
        if actual <= 0:
            return
        self._award_support_xp(game, actual)
        self._show_support_fx(game, target)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Recharge {_format_number(self.effective_rate(game))}/s",
            f"Radius {int(self.radius)}",
            f"Tick {_format_seconds(self.effective_tick_interval(game) if game is not None else self.tick_interval)}",
        ]


class MissingHealthDamageBoostPassive(PassiveAbility):
    ability_id = "catalog_bloodied_fury"
    name = "Bloodied Fury"
    description = "Deals increased damage equal to this unit's missing health percent."

    def modify_outgoing_damage(self, target, amount: float, element: str, game) -> float:
        max_health = max(1.0, float(getattr(self.owner, "max_health", 1.0)))
        health = max(0.0, min(max_health, float(getattr(self.owner, "health", max_health))))
        missing_ratio = 1.0 - health / max_health
        return amount * (1.0 + missing_ratio)

    def detail_lines(self, game=None) -> list[str]:
        max_health = max(1.0, float(getattr(self.owner, "max_health", 1.0)))
        health = max(0.0, min(max_health, float(getattr(self.owner, "health", max_health))))
        bonus = int(round((1.0 - health / max_health) * 100))
        return [f"Damage +{bonus}% now", "Scales with missing health"]


class GuardianInterceptAbility(GameplayAbility):
    ability_id = "catalog_guardian_intercept"
    name = "Guardian Intercept"
    description = "Redirects fatal damage from a nearby ally to this unit and pulls enemy aggro."

    def __init__(self, owner=None, radius: float = 132.0, cooldown: float = 120.0, taunt_duration: float = 3.0) -> None:
        super().__init__(owner, cooldown)
        self.radius = radius
        self.taunt_duration = taunt_duration

    def try_intercept_fatal_damage(
        self,
        game,
        target,
        amount: float,
        source=None,
        source_pos: pygame.Vector2 | None = None,
        element: str = "physical",
    ):
        if not self.ready or self.owner is target or not getattr(self.owner, "alive", False):
            return None
        if not getattr(target, "alive", False) or not hasattr(target, "health"):
            return None
        if amount < float(getattr(target, "health", 0.0)):
            return None
        if self.owner.pos.distance_to(target.pos) > self.radius + float(getattr(target, "radius", 0.0)):
            return None
        if not super().activate(game):
            return None

        self._transfer_aggro(game, target, amount)
        self.owner.support_pulse = max(getattr(self.owner, "support_pulse", 0.0), 0.45)
        if hasattr(game, "spawn_burst"):
            game.spawn_burst(self.owner.pos, 12, 64)
        if hasattr(game, "spawn_hit"):
            game.spawn_hit(target.pos, 3)
        return self.owner

    def _transfer_aggro(self, game, protected, amount: float) -> None:
        from bastion.game.entities import Beam

        enemies = _nearby_enemies(game, protected.pos, self.radius + 340.0)
        for enemy in enemies:
            aggro = getattr(enemy, "aggro", None)
            if aggro is None:
                continue
            entry = aggro.threat.pop(protected, None)
            inherited = 0.0 if entry is None else entry.score
            aggro.add_threat(self.owner, max(inherited, amount * 3.0 + 55.0), "taunt")
            if aggro.current_target is protected:
                aggro.current_target = self.owner
                aggro.retarget_timer = 0.0
            if getattr(enemy, "taunt_target", None) is protected:
                enemy.apply_taunt(self.owner, self.taunt_duration)
            if hasattr(game, "beams") and enemy.alive:
                game.beams.append(Beam(pygame.Vector2(self.owner.pos), pygame.Vector2(enemy.pos), 0.14, 1))

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Radius {int(self.radius)}",
            "Triggers on fatal ally damage",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.radius * camera.zoom, _tactical_preview_alpha(self.ready), 1)


class DamageBlockAbility(GameplayAbility):
    ability_id = "catalog_damage_block"
    name = "Perfect Guard"
    description = "Blocks all incoming damage during a short guard window."

    def __init__(self, owner=None, block_duration: float = 1.0, cooldown: float = 5.0) -> None:
        super().__init__(owner, cooldown)
        self.block_duration = block_duration
        self.block_remaining = 0.0

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        self.block_remaining = max(0.0, self.block_remaining - dt)

    def activate(self, game, target=None) -> bool:
        if not super().activate(game, target):
            return False
        self.block_remaining = self.block_duration
        self.owner.support_pulse = max(getattr(self.owner, "support_pulse", 0.0), 0.25)
        if hasattr(game, "spawn_hit"):
            game.spawn_hit(self.owner.pos, 2)
        return True

    def modify_incoming_damage(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> float:
        if amount <= 0 or not getattr(self.owner, "alive", False):
            return amount
        if self.block_remaining > 0:
            return 0.0
        if self.ready and self.activate(game):
            return 0.0
        return amount

    def has_status(self, status: str) -> bool:
        return status == "damage_block" and self.block_remaining > 0

    def state_label(self, game=None) -> str:
        if self.block_remaining > 0:
            return "BLOCK"
        return super().state_label(game)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Window {_format_seconds(self.block_duration)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]


class VisionMarkConeAbility(GameplayAbility):
    ability_id = "catalog_vision_mark"
    name = "Hunter's Mark"
    description = "Marks enemies in a forward cone, making troops and towers deal 20% more damage to them."

    def __init__(
        self,
        owner=None,
        angle_degrees: float = 58.0,
        duration: float = 6.0,
        damage_multiplier: float = 1.20,
        cooldown: float = 18.0,
    ) -> None:
        super().__init__(owner, cooldown)
        self.angle_degrees = angle_degrees
        self.duration = duration
        self.damage_multiplier = damage_multiplier

    def should_auto_activate(self, game) -> bool:
        return getattr(self.owner, "attack_enabled", True) and self._active_target(game) is not None

    def _active_target(self, game):
        target = getattr(self.owner, "target", None)
        if target is not None and getattr(target, "alive", False):
            return target
        return None

    def activate(self, game, target=None) -> bool:
        target = target or self._active_target(game)
        if target is None or not self.ready:
            return False
        attack_range = _owner_range(self.owner, game, 215.0)
        affected = _enemies_in_cone(game, self.owner, target, attack_range, self.angle_degrees)
        if not affected or not super().activate(game, target):
            return False
        for enemy in affected:
            if hasattr(enemy, "apply_damage_vulnerability"):
                enemy.apply_damage_vulnerability(self.damage_multiplier, self.duration, source_classes=("troop", "tower"))
            if hasattr(game, "spawn_hit"):
                game.spawn_hit(enemy.pos, 1)
        self.owner.support_pulse = max(getattr(self.owner, "support_pulse", 0.0), 0.28)
        return True

    def detail_lines(self, game=None) -> list[str]:
        return [
            "Troop/tower damage +20%",
            f"Cone {int(self.angle_degrees)} deg",
            f"Duration {_format_seconds(self.duration)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        target = getattr(self.owner, "target", None)
        if target is None:
            return
        _draw_cone_preview(surface, camera, viewport, self.owner, target, _owner_range(self.owner, None, 215.0), self.angle_degrees, _tactical_preview_alpha(self.ready))


class OutOfCombatRegenerationPassive(PassiveAbility):
    ability_id = "catalog_out_of_combat_regeneration"
    name = "Field Recovery"
    description = "Regenerates all health over two minutes while out of combat."

    def __init__(self, owner=None, full_heal_duration: float = 120.0, combat_grace: float = 5.0) -> None:
        super().__init__(owner)
        self.full_heal_duration = full_heal_duration
        self.combat_grace = combat_grace
        self.combat_timer = 0.0

    def update(self, dt: float, game) -> None:
        if not getattr(self.owner, "alive", False):
            return
        self.combat_timer = max(0.0, self.combat_timer - dt)
        if self._owner_has_combat_pressure(game):
            self.combat_timer = self.combat_grace
        if self.combat_timer > 0:
            return
        amount = float(getattr(self.owner, "max_health", 0.0)) / max(1.0, self.full_heal_duration) * dt
        game.restore_friendly(self.owner, amount, source=None, reason="regeneration", element="holy")

    def on_owner_damaged(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> None:
        if amount > 0:
            self.combat_timer = self.combat_grace

    def _owner_has_combat_pressure(self, game) -> bool:
        target = getattr(self.owner, "target", None)
        if getattr(target, "alive", False):
            return True
        for enemy in _nearby_enemies(game, self.owner.pos, 520.0):
            aggro = getattr(enemy, "aggro", None)
            if aggro is None:
                continue
            if aggro.current_target is self.owner or self.owner in aggro.threat:
                return True
        return False

    def detail_lines(self, game=None) -> list[str]:
        return [f"Full heal in {_format_seconds(self.full_heal_duration)}", "Only out of combat"]


class PersistentAreaEffect:
    def __init__(
        self,
        owner,
        pos: pygame.Vector2,
        radius: float,
        duration: float,
        enemy_dps: float = 0.0,
        ally_hps: float = 0.0,
        enemy_element: str = "physical",
        ally_element: str = "holy",
        fx_interval: float = 0.42,
    ) -> None:
        self.owner = owner
        self.pos = pygame.Vector2(pos)
        self.radius = radius
        self.duration = duration
        self.life = duration
        self.enemy_dps = enemy_dps
        self.ally_hps = ally_hps
        self.enemy_element = enemy_element
        self.ally_element = ally_element
        self.fx_interval = fx_interval
        self.fx_timer = 0.0
        self.alive = True

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.life -= dt
        if self.life <= 0:
            self.alive = False
            return
        if self.enemy_dps > 0:
            for enemy in _nearby_enemies(game, self.pos, self.radius + 32.0):
                if enemy.alive and enemy.pos.distance_to(self.pos) <= self.radius + enemy.radius:
                    game.damage_enemy(enemy, self.enemy_dps * dt, self.owner, quiet=True, source_pos=self.pos, element=self.enemy_element)
        if self.ally_hps > 0:
            for troop in _nearby_troops(game, self.pos, self.radius + 24.0):
                if troop.alive and troop.pos.distance_to(self.pos) <= self.radius + troop.radius:
                    game.restore_friendly(troop, self.ally_hps * dt, self.owner, reason="heal", element=self.ally_element)
        self.fx_timer -= dt
        if self.fx_timer <= 0:
            self.fx_timer = self.fx_interval
            if hasattr(game, "show_damage_impact"):
                game.show_damage_impact(self.pos, "aoe", self.radius)

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if not self.alive:
            return
        screen = camera.world_to_screen(self.pos, viewport)
        t = max(0.0, self.life / max(0.01, self.duration))
        alpha = int(18 + 34 * t)
        draw_circle_alpha(surface, screen, self.radius * camera.zoom, config.PALETTE.white, alpha, 1)
        draw_circle_alpha(surface, screen, self.radius * 0.45 * camera.zoom, config.PALETTE.white, int(alpha * 0.55), 1)


class ConsecrationAbility(GameplayAbility):
    ability_id = "catalog_consecration"
    name = "Consecration"
    description = "Creates holy ground that damages enemies and heals allies for five seconds."

    def __init__(self, owner=None, radius: float = 88.0, duration: float = 5.0, cooldown: float = 18.0) -> None:
        super().__init__(owner, cooldown)
        self.radius = radius
        self.duration = duration

    def should_auto_activate(self, game) -> bool:
        if not getattr(self.owner, "attack_enabled", True):
            return False
        enemies = _nearby_enemies(game, self.owner.pos, self.radius + 34.0)
        if any(enemy.alive and enemy.pos.distance_to(self.owner.pos) <= self.radius + enemy.radius for enemy in enemies):
            return True
        troops = _nearby_troops(game, self.owner.pos, self.radius + 24.0)
        return any(troop.alive and troop.health < troop.max_health and troop.pos.distance_to(self.owner.pos) <= self.radius + troop.radius for troop in troops)

    def activate(self, game, target=None) -> bool:
        if not super().activate(game, target):
            return False
        damage = max(4.0, _owner_damage(self.owner, game, prefer_magic=True) * 0.55)
        healing = max(3.0, _owner_damage(self.owner, game, prefer_magic=True) * 0.42)
        zone = PersistentAreaEffect(self.owner, self.owner.pos, self.radius, self.duration, damage, healing, enemy_element="holy", ally_element="holy")
        if hasattr(game, "ability_zones"):
            game.ability_zones.append(zone)
        else:
            zone.update(0.0, game)
        self.owner.support_pulse = max(getattr(self.owner, "support_pulse", 0.0), 0.35)
        return True

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Damage {_format_number(max(4.0, _owner_damage(self.owner, game, True) * 0.55))}/s holy",
            f"Heal {_format_number(max(3.0, _owner_damage(self.owner, game, True) * 0.42))}/s",
            f"Radius {int(self.radius)}",
            f"Duration {_format_seconds(self.duration)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.radius * camera.zoom, _tactical_preview_alpha(self.ready), 1)


class HolyAuraPassive(PassiveAbility):
    ability_id = "catalog_holy_aura"
    name = "Holy Aura"
    description = "Heals nearby allies over two minutes."

    def __init__(self, owner=None, radius: float = 82.0, full_heal_duration: float = 120.0, interval: float = 0.5) -> None:
        super().__init__(owner)
        self.radius = radius
        self.full_heal_duration = full_heal_duration
        self.interval = interval
        self.timer = random.uniform(0.0, interval)

    def update(self, dt: float, game) -> None:
        if not getattr(self.owner, "alive", False):
            return
        self.timer -= dt
        if self.timer > 0:
            return
        self.timer += max(0.05, self.interval)
        healed = False
        for troop in _nearby_troops(game, self.owner.pos, self.radius + 24.0):
            if not troop.alive or troop.pos.distance_to(self.owner.pos) > self.radius + troop.radius:
                continue
            amount = troop.max_health / max(1.0, self.full_heal_duration) * self.interval
            healed = game.restore_friendly(troop, amount, self.owner, reason="heal", element="holy") > 0 or healed
        if healed:
            self.owner.support_pulse = max(getattr(self.owner, "support_pulse", 0.0), 0.18)

    def detail_lines(self, game=None) -> list[str]:
        return [f"Radius {int(self.radius)}", f"Full heal in {_format_seconds(self.full_heal_duration)}"]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.radius * camera.zoom)


class InnerFireRetaliationAbility(GameplayAbility):
    ability_id = "catalog_inner_fire"
    name = "Inner Fire"
    description = "Enemies that strike this unit are burned by retaliatory fire."

    def __init__(self, owner=None, duration: float = 4.0, cooldown: float = 6.0) -> None:
        super().__init__(owner, cooldown)
        self.duration = duration

    def on_owner_damaged(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> None:
        if amount <= 0 or source is None or not getattr(source, "alive", False):
            return
        self.activate(game, source)

    def activate(self, game, target=None) -> bool:
        if target is None or not getattr(target, "alive", False) or not super().activate(game, target):
            return False
        dps = self.effective_dps(game)
        if hasattr(target, "apply_burn"):
            target.apply_burn(dps, self.duration, self.owner, spread_radius=0.0)
        from bastion.game.entities import Beam

        if hasattr(game, "beams"):
            game.beams.append(Beam(pygame.Vector2(self.owner.pos), pygame.Vector2(target.pos), 0.12, 1))
        return True

    def effective_dps(self, game=None) -> float:
        return max(4.0, _owner_damage(self.owner, game, prefer_magic=True) * 0.45)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Burn {_format_number(self.effective_dps(game))}/s",
            f"Duration {_format_seconds(self.duration)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]


class VanishAbility(GameplayAbility):
    ability_id = "catalog_vanish"
    name = "Vanish"
    description = "Drops enemy aggro and briefly leaves perception when threat gets too high."

    def __init__(self, owner=None, duration: float = 3.0, cooldown: float = 35.0, threat_threshold: float = 85.0) -> None:
        super().__init__(owner, cooldown)
        self.duration = duration
        self.threat_threshold = threat_threshold

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        if hasattr(self.owner, "stealth_time"):
            self.owner.stealth_time = max(0.0, self.owner.stealth_time - dt)
        if self.ready and self.should_auto_activate(game):
            self.activate(game)

    def should_auto_activate(self, game) -> bool:
        return getattr(self.owner, "alive", False) and self._aggro_pressure(game) >= self.threat_threshold

    def _aggro_pressure(self, game) -> float:
        pressure = 0.0
        for enemy in _nearby_enemies(game, self.owner.pos, 620.0):
            aggro = getattr(enemy, "aggro", None)
            if aggro is None:
                continue
            entry = aggro.threat.get(self.owner)
            if entry is not None:
                pressure += entry.score
            if aggro.current_target is self.owner:
                pressure += 28.0
        return pressure

    def activate(self, game, target=None) -> bool:
        if not super().activate(game, target):
            return False
        self.owner.stealth_time = self.duration
        self.owner.target = None
        navigator = getattr(self.owner, "navigator", None)
        if navigator is not None and hasattr(navigator, "clear"):
            navigator.clear()
        for enemy in _nearby_enemies(game, self.owner.pos, 720.0):
            aggro = getattr(enemy, "aggro", None)
            if aggro is None:
                continue
            aggro.threat.pop(self.owner, None)
            if aggro.current_target is self.owner:
                aggro.current_target = None
                aggro.retarget_timer = 0.0
            if getattr(enemy, "taunt_target", None) is self.owner:
                enemy.taunt_target = None
                enemy.taunt_time = 0.0
        if hasattr(game, "spawn_burst"):
            game.spawn_burst(self.owner.pos, 10, 72)
        return True

    def has_status(self, status: str) -> bool:
        return status == "stealth" and float(getattr(self.owner, "stealth_time", 0.0)) > 0

    def state_label(self, game=None) -> str:
        if float(getattr(self.owner, "stealth_time", 0.0)) > 0:
            return "VANISH"
        return super().state_label(game)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Stealth {_format_seconds(self.duration)}",
            f"Threat trigger {int(self.threat_threshold)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]


class WarMachineAbility(GameplayAbility):
    ability_id = "catalog_war_machine"
    name = "War Machine"
    description = "Transforms into a rapid, slightly inaccurate machine gun platform."

    def __init__(self, owner=None, duration: float = 20.0, cooldown: float = 60.0, fire_interval: float = 0.12, accuracy: float = 0.82) -> None:
        super().__init__(owner, cooldown)
        self.duration = duration
        self.fire_interval = fire_interval
        self.accuracy = accuracy
        self.active_remaining = 0.0
        self.fire_timer = 0.0

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        self.active_remaining = max(0.0, self.active_remaining - dt)
        if self.active_remaining > 0:
            self.fire_timer -= dt
            while self.fire_timer <= 0 and self.active_remaining > 0:
                self.fire_timer += self.fire_interval
                self._fire_round(game)
            return
        if self.ready and self.should_auto_activate(game):
            self.activate(game)

    def should_auto_activate(self, game) -> bool:
        return self._find_target(game) is not None

    def activate(self, game, target=None) -> bool:
        if not super().activate(game, target):
            return False
        self.active_remaining = self.duration
        self.fire_timer = 0.0
        self.owner.support_pulse = max(getattr(self.owner, "support_pulse", 0.0), 0.35)
        return True

    def _find_target(self, game):
        attack_range = _owner_range(self.owner, game, 180.0)
        target = getattr(self.owner, "target", None)
        if getattr(target, "alive", False) and self.owner.pos.distance_to(target.pos) <= attack_range + target.radius:
            return target
        candidates = [
            enemy
            for enemy in _nearby_enemies(game, self.owner.pos, attack_range + 36.0)
            if enemy.alive and enemy.pos.distance_to(self.owner.pos) <= attack_range + enemy.radius
        ]
        return min(candidates, key=lambda enemy: enemy.pos.distance_to(self.owner.pos)) if candidates else None

    def _fire_round(self, game) -> None:
        target = self._find_target(game)
        if target is None:
            return
        from bastion.game.entities import Beam

        origin = pygame.Vector2(self.owner.pos)
        hit = random.random() <= self.accuracy
        end = pygame.Vector2(target.pos)
        if not hit:
            end += pygame.Vector2(random.uniform(-18, 18), random.uniform(-18, 18))
        if hasattr(game, "beams"):
            game.beams.append(Beam(origin, end, 0.06, 1))
        if hit:
            damage = max(1.0, _owner_damage(self.owner, game) * 0.32)
            game.damage_enemy(target, damage, self.owner, source_pos=origin, element="physical")
        elif hasattr(game, "spawn_burst"):
            game.spawn_burst(end, 2, 18)

    def state_label(self, game=None) -> str:
        if self.active_remaining > 0:
            return "ACTIVE"
        return super().state_label(game)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Duration {_format_seconds(self.duration)}",
            f"Fire every {_format_seconds(self.fire_interval)}",
            f"Accuracy {int(self.accuracy * 100)}%",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]


class AggroFadeOnAbilityUsePassive(PassiveAbility):
    ability_id = "catalog_aggro_fade"
    name = "Quiet Casting"
    description = "Using abilities steadily bleeds off enemy aggro."

    def __init__(self, owner=None, fade_duration: float = 3.0, threat_decay_per_second: float = 24.0, immediate_fraction: float = 0.18) -> None:
        super().__init__(owner)
        self.fade_duration = fade_duration
        self.threat_decay_per_second = threat_decay_per_second
        self.immediate_fraction = immediate_fraction
        self.fade_remaining = 0.0

    def on_owner_ability_activated(self, activated: GameplayAbility, game) -> None:
        if activated.passive:
            return
        self.fade_remaining = self.fade_duration
        self._reduce_threat(game, fraction=self.immediate_fraction, flat=8.0)

    def update(self, dt: float, game) -> None:
        if self.fade_remaining <= 0:
            return
        self.fade_remaining = max(0.0, self.fade_remaining - dt)
        self._reduce_threat(game, fraction=0.0, flat=self.threat_decay_per_second * dt)

    def _reduce_threat(self, game, fraction: float, flat: float) -> None:
        for enemy in _nearby_enemies(game, self.owner.pos, 680.0):
            aggro = getattr(enemy, "aggro", None)
            if aggro is None:
                continue
            entry = aggro.threat.get(self.owner)
            if entry is None:
                continue
            entry.score = max(0.0, entry.score * (1.0 - fraction) - flat)
            if entry.score <= 0:
                aggro.threat.pop(self.owner, None)
                if aggro.current_target is self.owner:
                    aggro.current_target = None
                    aggro.retarget_timer = 0.0

    def detail_lines(self, game=None) -> list[str]:
        return [f"Fade {_format_seconds(self.fade_duration)}", f"Threat -{_format_number(self.threat_decay_per_second)}/s"]


class ElectricJoltPassive(PassiveAbility):
    ability_id = "catalog_electric_jolt"
    name = "Static Jolt"
    description = "When struck, releases a burst that stuns nearby enemies."

    def __init__(self, owner=None, radius: float = 96.0, stun_duration: float = 2.0, recovery: float = 8.0) -> None:
        super().__init__(owner)
        self.radius = radius
        self.stun_duration = stun_duration
        self.recovery = recovery
        self.recovery_remaining = 0.0

    def update(self, dt: float, game) -> None:
        self.recovery_remaining = max(0.0, self.recovery_remaining - dt)

    def on_owner_damaged(self, amount: float, source, source_pos: pygame.Vector2 | None, element: str, game) -> None:
        if amount <= 0 or self.recovery_remaining > 0:
            return
        affected = [
            enemy
            for enemy in _nearby_enemies(game, self.owner.pos, self.radius + 32.0)
            if enemy.alive and enemy.pos.distance_to(self.owner.pos) <= self.radius + enemy.radius
        ]
        if not affected:
            return
        from bastion.game.entities import Beam

        self.recovery_remaining = self.recovery
        for enemy in affected:
            enemy.apply_stun(self.stun_duration)
            if hasattr(game, "record_stun"):
                game.record_stun(self.owner, enemy, self.stun_duration)
            if hasattr(game, "beams"):
                game.beams.append(Beam(pygame.Vector2(self.owner.pos), pygame.Vector2(enemy.pos), 0.12, 1))
        if hasattr(game, "show_damage_impact"):
            game.show_damage_impact(self.owner.pos, "aoe", self.radius)

    def detail_lines(self, game=None) -> list[str]:
        return [f"Radius {int(self.radius)}", f"Stun {_format_seconds(self.stun_duration)}", f"Recovery {_format_seconds(self.recovery)}"]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.radius * camera.zoom)


class FrostNovaAbility(GameplayAbility):
    ability_id = "catalog_frost_nova"
    name = "Frost Nova"
    description = "Pushes close enemies away and freezes them in place."

    def __init__(self, owner=None, radius: float = 86.0, stun_duration: float = 2.0, cooldown: float = 14.0, knockback: float = 190.0) -> None:
        super().__init__(owner, cooldown)
        self.radius = radius
        self.stun_duration = stun_duration
        self.knockback = knockback

    def should_auto_activate(self, game) -> bool:
        return any(enemy.alive and enemy.pos.distance_to(self.owner.pos) <= self.radius + enemy.radius for enemy in _nearby_enemies(game, self.owner.pos, self.radius + 32.0))

    def activate(self, game, target=None) -> bool:
        affected = [
            enemy
            for enemy in _nearby_enemies(game, self.owner.pos, self.radius + 32.0)
            if enemy.alive and enemy.pos.distance_to(self.owner.pos) <= self.radius + enemy.radius
        ]
        if not affected or not super().activate(game, target):
            return False
        for enemy in affected:
            direction = enemy.pos - self.owner.pos
            if direction.length_squared() == 0:
                direction = pygame.Vector2(1, 0)
            enemy.vel += direction.normalize() * (self.knockback / max(0.5, float(getattr(enemy, "mass", 1.0))))
            enemy.apply_stun(self.stun_duration)
            if hasattr(game, "record_stun"):
                game.record_stun(self.owner, enemy, self.stun_duration)
            enemy.apply_slow(0.35, self.stun_duration, 0.55)
        if hasattr(game, "show_damage_impact"):
            game.show_damage_impact(self.owner.pos, "aoe", self.radius)
        return True

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Radius {int(self.radius)}",
            f"Freeze {_format_seconds(self.stun_duration)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.radius * camera.zoom, _tactical_preview_alpha(self.ready), 1)


class DragonBreathAbility(GameplayAbility):
    ability_id = "catalog_dragon_breath"
    name = "Dragon Breath"
    description = "Exhales a cone of flame that damages and burns enemies."

    def __init__(self, owner=None, range_override: float | None = None, angle_degrees: float = 64.0, cooldown: float = 12.0, burn_duration: float = 3.5) -> None:
        super().__init__(owner, cooldown)
        self.range_override = range_override
        self.angle_degrees = angle_degrees
        self.burn_duration = burn_duration

    def should_auto_activate(self, game) -> bool:
        return getattr(self.owner, "attack_enabled", True) and getattr(getattr(self.owner, "target", None), "alive", False)

    def activate(self, game, target=None) -> bool:
        target = target or getattr(self.owner, "target", None)
        if target is None or not getattr(target, "alive", False) or not self.ready:
            return False
        reach = self.range_override if self.range_override is not None else max(120.0, _owner_range(self.owner, game, 120.0))
        affected = _enemies_in_cone(game, self.owner, target, reach, self.angle_degrees)
        if not affected or not super().activate(game, target):
            return False
        base_damage = max(5.0, _owner_damage(self.owner, game, prefer_magic=True) * 1.25)
        burn_dps = max(3.0, _owner_damage(self.owner, game, prefer_magic=True) * 0.32)
        for enemy in affected:
            distance = enemy.pos.distance_to(self.owner.pos)
            falloff = 1.0 - min(0.35, distance / max(1.0, reach) * 0.35)
            game.damage_enemy(enemy, base_damage * falloff, self.owner, source_pos=self.owner.pos, element="fire")
            if enemy.alive:
                enemy.apply_burn(burn_dps, self.burn_duration, self.owner, spread_radius=0.0)
        if hasattr(game, "show_damage_impact"):
            game.show_damage_impact(self.owner.pos, "aoe", min(reach, 110.0))
        return True

    def detail_lines(self, game=None) -> list[str]:
        reach = self.range_override if self.range_override is not None else max(120.0, _owner_range(self.owner, game, 120.0))
        return [
            f"Damage {_format_number(max(5.0, _owner_damage(self.owner, game, True) * 1.25))} fire",
            f"Cone {int(self.angle_degrees)} deg",
            f"Range {int(reach)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        target = getattr(self.owner, "target", None)
        if target is None:
            return
        reach = self.range_override if self.range_override is not None else max(120.0, _owner_range(self.owner, None, 120.0))
        _draw_cone_preview(surface, camera, viewport, self.owner, target, reach, self.angle_degrees, _tactical_preview_alpha(self.ready))


class AttackRangeSlowAuraPassive(PassiveAbility):
    ability_id = "catalog_slowing_presence"
    name = "Slowing Presence"
    description = "Enemies inside this unit's attack range are slowed."

    def __init__(self, owner=None, slow_multiplier: float = 0.70, attack_slow_multiplier: float = 0.86, interval: float = 0.22) -> None:
        super().__init__(owner)
        self.slow_multiplier = slow_multiplier
        self.attack_slow_multiplier = attack_slow_multiplier
        self.interval = interval
        self.timer = random.uniform(0.0, interval)

    def update(self, dt: float, game) -> None:
        if not getattr(self.owner, "alive", False):
            return
        self.timer -= dt
        if self.timer > 0:
            return
        self.timer += max(0.05, self.interval)
        radius = _owner_range(self.owner, game, 96.0)
        for enemy in _nearby_enemies(game, self.owner.pos, radius + 32.0):
            if enemy.alive and enemy.pos.distance_to(self.owner.pos) <= radius + enemy.radius:
                enemy.apply_slow(self.slow_multiplier, self.interval * 2.2, self.attack_slow_multiplier)

    def detail_lines(self, game=None) -> list[str]:
        slow_pct = int((1.0 - self.slow_multiplier) * 100)
        return [f"Slow {slow_pct}%", f"Radius {int(_owner_range(self.owner, game, 0.0))}"]


class SiphonLifeAbility(GameplayAbility):
    ability_id = "catalog_siphon_life"
    name = "Siphon Life"
    description = "Drains life from all nearby enemies and heals this unit."

    def __init__(self, owner=None, radius: float = 118.0, duration: float = 20.0, cooldown: float = 60.0, tick_interval: float = 0.4) -> None:
        super().__init__(owner, cooldown)
        self.radius = radius
        self.duration = duration
        self.tick_interval = tick_interval
        self.active_remaining = 0.0
        self.tick_timer = 0.0

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        self.active_remaining = max(0.0, self.active_remaining - dt)
        if self.active_remaining > 0:
            self.tick_timer -= dt
            while self.tick_timer <= 0 and self.active_remaining > 0:
                self.tick_timer += self.tick_interval
                self._tick(game)
            return
        if self.ready and self.should_auto_activate(game):
            self.activate(game)

    def should_auto_activate(self, game) -> bool:
        return any(enemy.alive and enemy.pos.distance_to(self.owner.pos) <= self.radius + enemy.radius for enemy in _nearby_enemies(game, self.owner.pos, self.radius + 32.0))

    def activate(self, game, target=None) -> bool:
        if not super().activate(game, target):
            return False
        self.active_remaining = self.duration
        self.tick_timer = 0.0
        return True

    def _tick(self, game) -> None:
        dps = self.effective_dps(game)
        total_drained = 0.0
        from bastion.game.entities import Beam

        for enemy in _nearby_enemies(game, self.owner.pos, self.radius + 32.0):
            if not enemy.alive or enemy.pos.distance_to(self.owner.pos) > self.radius + enemy.radius:
                continue
            before = float(enemy.health)
            game.damage_enemy(enemy, dps * self.tick_interval, self.owner, quiet=True, source_pos=self.owner.pos, element="holy")
            total_drained += max(0.0, before - float(getattr(enemy, "health", before)))
            if hasattr(game, "beams"):
                game.beams.append(Beam(pygame.Vector2(enemy.pos), pygame.Vector2(self.owner.pos), 0.10, 1))
        if total_drained > 0:
            game.restore_friendly(self.owner, total_drained, source=None, reason="lifesteal", element="holy")
            self.owner.support_pulse = max(getattr(self.owner, "support_pulse", 0.0), 0.22)

    def effective_dps(self, game=None) -> float:
        return max(4.0, _owner_damage(self.owner, game, prefer_magic=True) * 0.36)

    def state_label(self, game=None) -> str:
        if self.active_remaining > 0:
            return "ACTIVE"
        return super().state_label(game)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Drain {_format_number(self.effective_dps(game))}/s",
            f"Radius {int(self.radius)}",
            f"Duration {_format_seconds(self.duration)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        _draw_tactical_preview_circle(surface, screen, self.radius * camera.zoom, _tactical_preview_alpha(self.ready), 1)


class ArcaneFocusAbility(GameplayAbility):
    ability_id = "catalog_arcane_focus"
    name = "Arcane Focus"
    description = "Channels a beam into one target for three seconds, ramping damage exponentially."

    def __init__(self, owner=None, duration: float = 3.0, cooldown: float = 16.0, tick_interval: float = 0.18, growth: float = 2.15) -> None:
        super().__init__(owner, cooldown)
        self.duration = duration
        self.tick_interval = tick_interval
        self.growth = growth
        self.channel_remaining = 0.0
        self.elapsed = 0.0
        self.tick_timer = 0.0
        self.channel_target = None

    def update(self, dt: float, game) -> None:
        self.cooldown_remaining = max(0.0, self.cooldown_remaining - dt)
        if self.channel_remaining > 0:
            self.channel_remaining = max(0.0, self.channel_remaining - dt)
            self.elapsed += dt
            if not getattr(self.channel_target, "alive", False) or self.owner.pos.distance_to(self.channel_target.pos) > _owner_range(self.owner, game, 150.0) + self.channel_target.radius:
                self.channel_remaining = 0.0
                self.channel_target = None
                return
            self.tick_timer -= dt
            while self.tick_timer <= 0 and self.channel_remaining > 0:
                self.tick_timer += self.tick_interval
                self._tick(game)
            return
        if self.ready and self.should_auto_activate(game):
            self.activate(game)

    def should_auto_activate(self, game) -> bool:
        return self._find_target(game) is not None

    def _find_target(self, game):
        attack_range = _owner_range(self.owner, game, 150.0)
        target = getattr(self.owner, "target", None)
        if getattr(target, "alive", False) and self.owner.pos.distance_to(target.pos) <= attack_range + target.radius:
            return target
        candidates = [
            enemy
            for enemy in _nearby_enemies(game, self.owner.pos, attack_range + 32.0)
            if enemy.alive and enemy.pos.distance_to(self.owner.pos) <= attack_range + enemy.radius
        ]
        return min(candidates, key=lambda enemy: enemy.pos.distance_to(self.owner.pos)) if candidates else None

    def activate(self, game, target=None) -> bool:
        target = target or self._find_target(game)
        if target is None or not super().activate(game, target):
            return False
        self.channel_target = target
        self.channel_remaining = self.duration
        self.elapsed = 0.0
        self.tick_timer = 0.0
        return True

    def _tick(self, game) -> None:
        if self.channel_target is None:
            return
        from bastion.game.entities import Beam

        dps = self.effective_starting_dps(game) * (self.growth ** (self.elapsed / max(0.01, self.duration) * 2.2))
        game.damage_enemy(self.channel_target, dps * self.tick_interval, self.owner, quiet=True, source_pos=self.owner.pos, element="lightning")
        if hasattr(game, "beams") and getattr(self.channel_target, "alive", False):
            game.beams.append(Beam(pygame.Vector2(self.owner.pos), pygame.Vector2(self.channel_target.pos), 0.10, 2))

    def effective_starting_dps(self, game=None) -> float:
        return max(5.0, _owner_damage(self.owner, game, prefer_magic=True) * 0.58)

    def state_label(self, game=None) -> str:
        if self.channel_remaining > 0:
            return "FOCUS"
        return super().state_label(game)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Starts {_format_number(self.effective_starting_dps(game))}/s",
            f"Duration {_format_seconds(self.duration)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]


class TargetPriorityPassive(PassiveAbility):
    ability_id = "target_ranged_priority"
    name = "Ranged Priority"
    description = "Prioritizes ranged enemies before other valid targets."
    changes_target_priority = True

    def __init__(self, owner=None, *, ability_id: str = "target_ranged_priority", name: str = "Ranged Priority", description: str | None = None) -> None:
        super().__init__(owner)
        self.ability_id = ability_id
        self.name = name
        if description is not None:
            self.description = description

    def target_priority_key(self, target, fallback_key: tuple) -> tuple:
        return (not getattr(target, "is_ranged", False),) + tuple(fallback_key)

    def detail_lines(self, game=None) -> list[str]:
        return ["Targeting ranged enemies first"]


class ThreatAuraPassive(PassiveAbility):
    ability_id = "passive_threat_aura"
    name = "Noisy"
    description = "Continuously creates threat on enemies inside attack range."

    def __init__(self, owner=None, *, threat_per_second: float = 42.0, interval: float = 0.45) -> None:
        super().__init__(owner)
        self.threat_per_second = threat_per_second
        self.interval = interval
        self.timer = random.uniform(0.05, max(0.06, interval))

    def update(self, dt: float, game) -> None:
        if self.threat_per_second <= 0 or not getattr(self.owner, "alive", False):
            return
        self.timer -= dt
        if self.timer > 0:
            return
        self.timer += max(0.05, self.interval)
        stats = self.owner.stats(game) if hasattr(self.owner, "stats") else {}
        attack_range = float(stats.get("range", 0.0))
        enemies = game.targetable_enemies_near(self.owner.pos, attack_range + 36) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(self.owner.pos, attack_range + 36) if hasattr(game, "nearby_enemies") else game.enemies
        amount = self.threat_per_second * self.interval
        affected = 0
        for enemy in enemies:
            if not enemy.alive or not hasattr(enemy, "aggro"):
                continue
            distance = enemy.pos.distance_to(self.owner.pos)
            if distance > attack_range + enemy.radius:
                continue
            falloff = 1.0 - min(0.45, distance / max(1.0, attack_range) * 0.45)
            enemy.aggro.add_threat(self.owner, amount * falloff, "noise")
            affected += 1
        if affected:
            self.owner.pulse = pygame.time.get_ticks() * 0.001

    def detail_lines(self, game=None) -> list[str]:
        stats = self.owner.stats(game) if game is not None and hasattr(self.owner, "stats") else {}
        lines = [
            f"Threat {_format_number(self.threat_per_second)}/s",
            f"Pulse every {_format_seconds(self.interval)}",
        ]
        if "range" in stats:
            lines.append(f"Radius {int(float(stats['range']))}")
        return lines


class AggroSuppressionAuraPassive(PassiveAbility):
    ability_id = "cover_fire"
    name = "Cover Fire"
    description = "Friendly troops inside this tower's attack range create no incidental aggro from damage, healing, or repairs."

    def suppresses_aggro(self, source, game) -> bool:
        if source is None or not getattr(source, "alive", False):
            return False
        stats = self.owner.stats(game) if hasattr(self.owner, "stats") else {}
        radius = float(stats.get("range", 0.0))
        return source.pos.distance_to(self.owner.pos) <= radius + float(getattr(source, "radius", 0.0))

    def detail_lines(self, game=None) -> list[str]:
        stats = self.owner.stats(game) if game is not None and hasattr(self.owner, "stats") else {}
        radius = int(float(stats.get("range", 0.0))) if "range" in stats else 0
        return [f"Suppresses troop aggro in {radius} range"] if radius else ["Suppresses nearby troop aggro"]


class ItemPassiveAbility(PassiveAbility):
    ability_id = "item_passive"
    name = "Item Passive"
    description = "A passive effect granted by equipment."

    def __init__(
        self,
        owner=None,
        *,
        ability_id: str,
        name: str,
        description: str,
        effects: dict[str, object],
        tags: tuple[str, ...] = (),
    ) -> None:
        super().__init__(owner)
        self.ability_id = ability_id
        self.name = name
        self.description = description
        self.effects = effects
        self.tags = tags

    def detail_lines(self, game=None) -> list[str]:
        lines = []
        labels = {
            "movement_speed_multiplier": "Move speed",
            "experience_multiplier": "Experience",
            "heal_self_max_hp_on_attack": "Attack heal",
            "max_health_per_stamina_bonus": "HP per stamina",
        }
        attribute_labels = {
            "stamina": "STM",
            "intellect": "INT",
            "strength": "STR",
            "agility": "AGI",
            "cunning": "CUN",
        }
        raw_bonuses = self.effects.get("attribute_bonuses")
        if isinstance(raw_bonuses, dict):
            for attribute, amount in raw_bonuses.items():
                if isinstance(amount, (int, float)):
                    label = attribute_labels.get(str(attribute), str(attribute).upper()[:3])
                    lines.append(f"+{int(amount)} {label}")
        for key, label in labels.items():
            raw = self.effects.get(key)
            if not isinstance(raw, (int, float)):
                continue
            value = float(raw)
            if "multiplier" in key:
                lines.append(f"{label} x{value:0.2f}")
            elif key == "heal_self_max_hp_on_attack":
                lines.append(f"{label} {int(value * 100)}% max HP")
            else:
                lines.append(f"{label} +{_format_number(value)}")
        return lines or ["Equipment passive"]

    def effect_multiplier(self, effect: str) -> float | None:
        raw = self.effects.get(effect)
        if isinstance(raw, (int, float)) and "multiplier" in effect:
            return float(raw)
        return None


class ItemThreatAuraPassive(ItemPassiveAbility):
    def __init__(
        self,
        owner=None,
        *,
        ability_id: str,
        name: str,
        description: str,
        effects: dict[str, object],
        tags: tuple[str, ...] = (),
    ) -> None:
        super().__init__(owner, ability_id=ability_id, name=name, description=description, effects=effects, tags=tags)
        self.tick = 0.0

    def update(self, dt: float, game) -> None:
        if self.owner is None or not getattr(self.owner, "alive", False):
            return
        interval = float(self.effects.get("passive_threat_interval", 0.5))
        self.tick -= dt
        if self.tick > 0:
            return
        self.tick = max(0.05, interval)
        amount = float(self.effects.get("passive_threat_per_second", 0.0)) * self.tick
        radius = float(self.effects.get("passive_threat_radius", 180.0))
        if amount > 0 and hasattr(game, "emit_aggro"):
            game.emit_aggro(self.owner, amount, "item_aura", radius=radius)

    def detail_lines(self, game=None) -> list[str]:
        lines = super().detail_lines(game)
        radius = int(float(self.effects.get("passive_threat_radius", 180.0)))
        threat = float(self.effects.get("passive_threat_per_second", 0.0))
        lines.append(f"Threat {_format_number(threat)}/s in {radius}")
        return lines


class StatModifierPassive(PassiveAbility):
    ability_id = "stat_modifier"
    name = "Modifier"
    description = "Changes this unit's combat stats."

    def __init__(self, owner=None, *, mod_id: str, name: str, description: str, effects: dict[str, object]) -> None:
        super().__init__(owner)
        self.ability_id = mod_id
        self.name = name
        self.description = description
        self.effects = effects

    def detail_lines(self, game=None) -> list[str]:
        lines = []
        labels = {
            "projectile_count_multiplier": "Projectiles",
            "damage_multiplier": "Damage",
            "fire_rate_multiplier": "Fire rate",
            "range_multiplier": "Range",
            "accuracy_multiplier": "Accuracy",
            "aggro_multiplier": "Aggro",
        }
        for key, label in labels.items():
            raw = self.effects.get(key)
            if isinstance(raw, (int, float)):
                lines.append(f"{label} x{float(raw):0.2f}")
        return lines or ["Passive stat modifier"]

    def effect_multiplier(self, effect: str) -> float | None:
        raw = self.effects.get(effect)
        if isinstance(raw, (int, float)):
            return float(raw)
        return None


def _catalog_definition(request_number: int, ability_cls: type[GameplayAbility]) -> AbilityDefinition:
    return AbilityDefinition(
        request_number=request_number,
        ability_id=str(getattr(ability_cls, "ability_id", "")),
        name=str(getattr(ability_cls, "name", ability_cls.__name__)),
        description=str(getattr(ability_cls, "description", "")),
        passive=bool(getattr(ability_cls, "passive", False)),
        factory=lambda owner=None, cls=ability_cls: cls(owner),
    )


CATALOG_ABILITY_DEFINITIONS: dict[int, AbilityDefinition] = {
    1: _catalog_definition(1, MissingHealthDamageBoostPassive),
    2: _catalog_definition(2, GuardianInterceptAbility),
    3: _catalog_definition(3, DamageBlockAbility),
    4: _catalog_definition(4, VisionMarkConeAbility),
    5: _catalog_definition(5, OutOfCombatRegenerationPassive),
    7: _catalog_definition(7, ConsecrationAbility),
    8: _catalog_definition(8, HolyAuraPassive),
    9: _catalog_definition(9, InnerFireRetaliationAbility),
    10: _catalog_definition(10, VanishAbility),
    11: _catalog_definition(11, WarMachineAbility),
    12: _catalog_definition(12, AggroFadeOnAbilityUsePassive),
    13: _catalog_definition(13, ElectricJoltPassive),
    14: _catalog_definition(14, FrostNovaAbility),
    15: _catalog_definition(15, DragonBreathAbility),
    16: _catalog_definition(16, AttackRangeSlowAuraPassive),
    17: _catalog_definition(17, SiphonLifeAbility),
    18: _catalog_definition(18, ArcaneFocusAbility),
}


def catalog_ability_definitions() -> dict[int, AbilityDefinition]:
    return dict(CATALOG_ABILITY_DEFINITIONS)


def create_catalog_ability(request_number: int, owner=None) -> GameplayAbility:
    definition = CATALOG_ABILITY_DEFINITIONS[request_number]
    return definition.create(owner)


def create_hero_ability(ability_id: str, owner=None) -> GameplayAbility:
    if ability_id == MultiShotAttackAbility.ability_id:
        return MultiShotAttackAbility(owner)
    for definition in CATALOG_ABILITY_DEFINITIONS.values():
        if definition.ability_id == ability_id:
            return definition.create(owner)
    raise KeyError(f"Unknown hero ability '{ability_id}'.")


def configure_troop_abilities(owner) -> AbilitySystemComponent:
    component = getattr(owner, "abilities", None)
    if component is None:
        component = AbilitySystemComponent(owner)
        owner.abilities = component
    else:
        component.clear()

    kind = getattr(owner, "kind", "")
    unlocked_hero_abilities = tuple(getattr(owner, "hero_unlocked_ability_ids", lambda: ())())
    unlocked_hero_ability_set = set(unlocked_hero_abilities)
    archer_has_multishot = MultiShotAttackAbility.ability_id in unlocked_hero_ability_set
    if kind == "archer":
        if archer_has_multishot:
            component.add(MultiShotAttackAbility(owner))
        else:
            component.add(
                MeleeAttackAbility(
                    owner,
                    name="Single Shot",
                    description="Fires one precise arrow at the current target.",
                    element="physical",
                    particle_count=0,
                )
            )
        component.add(TargetPriorityPassive(owner, ability_id="archer_ranged_priority", name="Ranged Priority", description="Prioritizes ranged enemies before other valid targets."))
    elif kind == "rune_mage":
        component.add(AreaElementalAttackAbility(owner))
        component.add(RechargeShieldAbility(owner))
    else:
        element = "lightning" if getattr(owner, "attack_stat", "") == "intellect" else "physical"
        melee_name = "Lightning Strike" if element == "lightning" else "Melee"
        particle_count = 5 if kind == "warrior" else 3
        component.add(MeleeAttackAbility(owner, name=melee_name, element=element, particle_count=particle_count))
        if kind == "warrior":
            component.add(TauntAbility(owner))
        elif kind == "cleric":
            component.add(HealTroopAbility(owner))
        elif kind == "engineer":
            component.add(RepairTowerAbility(owner))
        elif kind == "wizard":
            component.add(ChainLightningAbility(owner))

    for ability_id in unlocked_hero_abilities:
        if kind == "archer" and ability_id == MultiShotAttackAbility.ability_id:
            continue
        component.add(create_hero_ability(ability_id, owner))

    for ability in getattr(owner, "equipment_passive_abilities", lambda: [])():
        component.add(ability)
    return component


def configure_enemy_abilities(owner) -> AbilitySystemComponent:
    component = getattr(owner, "abilities", None)
    if component is None:
        component = AbilitySystemComponent(owner)
        owner.abilities = component
    else:
        component.clear()
    if getattr(owner, "is_ranged", False):
        component.add(EnemyRangedAttackAbility(owner))
    else:
        component.add(
            MeleeAttackAbility(
                owner,
                name="Melee Attack",
                target_side="friendly",
                cooldown_attr="attack_cooldown",
                cooldown=0.85,
                use_fire_rate=False,
            )
        )
    return component


def configure_tower_abilities(owner) -> AbilitySystemComponent:
    component = getattr(owner, "abilities", None)
    if component is None:
        component = AbilitySystemComponent(owner)
        owner.abilities = component
    else:
        component.clear()
    component.add(TowerProjectileAttackAbility(owner))
    for mod_id in getattr(owner, "installed_mods", []):
        definition = TOWER_MODS.get(mod_id)
        if definition is None:
            continue
        effects = definition.effects
        if effects.get("target_ranged_priority"):
            component.add(TargetPriorityPassive(owner, ability_id=definition.id, name=definition.name, description=definition.description))
        elif effects.get("passive_threat_per_second"):
            component.add(
                ThreatAuraPassive(
                    owner,
                    threat_per_second=float(effects.get("passive_threat_per_second", 42.0)),
                    interval=float(effects.get("passive_threat_interval", 0.45)),
                )
            )
        elif effects.get("suppress_troop_aggro"):
            component.add(AggroSuppressionAuraPassive(owner))
        elif _is_combat_stat_modifier(effects):
            component.add(StatModifierPassive(owner, mod_id=definition.id, name=definition.name, description=definition.description, effects=effects))
    return component


def projectile_element(effect: str) -> str:
    if effect == "burn":
        return "fire"
    if effect == "slow":
        return "ice"
    if effect == "chain":
        return "lightning"
    return "physical"


def elemental_effect_for_projectile(effect: str, owner) -> ElementalEffect | None:
    if effect == "burn":
        return ElementalEffect(
            "fire",
            duration=2.8,
            dot_dps=5.5 + owner.level * 2.2,
            spread_radius=74,
            spread_falloff=0.45,
        )
    if effect == "slow":
        return ElementalEffect(
            "ice",
            duration=(1.9 + owner.level * 0.08) * _research_multiplier(owner, "wizard_freeze_duration"),
            slow_multiplier=0.48,
            attack_slow_multiplier=0.66,
        )
    return None


def projectile_effect_line(effect: str, owner, game=None) -> str:
    elemental = elemental_effect_for_projectile(effect, owner)
    if elemental is None:
        if effect == "chain":
            arc = 125.0 * _research_multiplier(owner, "wizard_lightning_arc")
            return f"Chains up to 4 targets in {int(arc)}"
        return ""
    if elemental.element == "fire":
        return f"Burn {_format_number(elemental.dot_dps)}/s for {_format_seconds(elemental.duration)}"
    if elemental.element == "ice":
        slow_pct = int((1.0 - elemental.slow_multiplier) * 100)
        return f"Slow {slow_pct}% for {_format_seconds(elemental.duration)}"
    return ""


def damage_impact_kind(aoe: float, projectile_count: int, effect: str = "") -> str:
    if aoe > 0:
        return "aoe"
    if projectile_count > 1 or effect == "chain":
        return "multi"
    return "single"


def spawn_launch_fx(game, owner, projectile_count: int, impact_kind: str) -> None:
    from bastion.game.entities import Particle

    count = 3 if impact_kind == "single" else (5 if impact_kind == "multi" else 7)
    spread = 18 if impact_kind == "single" else (36 if impact_kind == "multi" else 54)
    for index in range(count):
        angle = (index / max(1, count)) * math.tau + random.uniform(-0.18, 0.18)
        speed = random.uniform(10, spread)
        vel = pygame.Vector2(math.cos(angle), math.sin(angle)) * speed
        game.particles.append(Particle(pygame.Vector2(owner.pos), vel, 0.16, 2.0 if projectile_count <= 1 else 2.4))


def _owner_stats(owner, game=None) -> dict[str, float]:
    if owner is not None and hasattr(owner, "stats"):
        return owner.stats(game)
    return {}


def _owner_damage(owner, game=None, prefer_magic: bool = False) -> float:
    stats = _owner_stats(owner, game)
    if prefer_magic and "magic_damage" in stats:
        return float(stats.get("magic_damage", 0.0))
    if "damage" in stats:
        return float(stats.get("damage", 0.0))
    return float(getattr(owner, "damage", getattr(owner, "base_damage", 0.0)))


def _owner_range(owner, game=None, default: float = 0.0) -> float:
    stats = _owner_stats(owner, game)
    if "range" in stats:
        return float(stats.get("range", default))
    for attr in ("attack_range", "base_range", "station_range"):
        if hasattr(owner, attr):
            return float(getattr(owner, attr))
    return default


def _nearby_enemies(game, pos: pygame.Vector2, radius: float):
    if hasattr(game, "targetable_enemies_near"):
        return game.targetable_enemies_near(pos, radius)
    if hasattr(game, "nearby_enemies"):
        return game.nearby_enemies(pos, radius)
    return getattr(game, "enemies", [])


def _nearby_troops(game, pos: pygame.Vector2, radius: float):
    if hasattr(game, "nearby_troops"):
        return game.nearby_troops(pos, radius)
    return getattr(game, "troops", [])


def _nearest_troop(game, pos: pygame.Vector2, radius: float):
    point = pygame.Vector2(pos)
    candidates = [
        troop
        for troop in _nearby_troops(game, point, radius)
        if getattr(troop, "alive", False) and troop.pos.distance_to(point) <= radius + getattr(troop, "radius", 0.0)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda troop: troop.pos.distance_to(point))


def _enemies_in_cone(game, owner, target, reach: float, angle_degrees: float):
    origin = pygame.Vector2(owner.pos)
    direction = pygame.Vector2(target.pos) - origin
    if direction.length_squared() == 0:
        direction = pygame.Vector2(getattr(owner, "swing_dir", (1, 0)))
    if direction.length_squared() == 0:
        direction = pygame.Vector2(1, 0)
    direction = direction.normalize()
    half_cos = math.cos(math.radians(angle_degrees) * 0.5)
    affected = []
    for enemy in _nearby_enemies(game, origin, reach + 36.0):
        if not enemy.alive:
            continue
        if hasattr(owner, "_enemy_inside_station") and not owner._enemy_inside_station(enemy):
            continue
        offset = pygame.Vector2(enemy.pos) - origin
        distance = offset.length()
        if distance > reach + enemy.radius:
            continue
        if distance <= max(float(getattr(owner, "radius", 0.0)), enemy.radius):
            affected.append(enemy)
            continue
        if offset.normalize().dot(direction) >= half_cos:
            affected.append(enemy)
    return affected


def _draw_cone_preview(
    surface: pygame.Surface,
    camera,
    viewport: pygame.Rect,
    owner,
    target,
    reach: float,
    angle_degrees: float,
    alpha: int,
) -> None:
    origin = pygame.Vector2(owner.pos)
    direction = pygame.Vector2(target.pos) - origin
    if direction.length_squared() == 0:
        direction = pygame.Vector2(1, 0)
    direction = direction.normalize()
    left = direction.rotate(-angle_degrees * 0.5)
    right = direction.rotate(angle_degrees * 0.5)
    start = camera.world_to_screen(origin, viewport)
    a = camera.world_to_screen(origin + left * reach, viewport)
    b = camera.world_to_screen(origin + right * reach, viewport)
    draw_line_alpha(surface, start, a, config.TACTICAL_OVERLAY_COLOR, alpha, 1)
    draw_line_alpha(surface, start, b, config.TACTICAL_OVERLAY_COLOR, alpha, 1)
    draw_line_alpha(surface, a, b, config.TACTICAL_OVERLAY_COLOR, max(config.TACTICAL_OVERLAY_SOFT_ALPHA, int(alpha * 0.6)), 1)


def _is_combat_stat_modifier(effects: dict[str, object]) -> bool:
    return any(
        key in effects
        for key in (
            "projectile_count_multiplier",
            "damage_multiplier",
            "fire_rate_multiplier",
            "range_multiplier",
            "accuracy_multiplier",
            "aggro_multiplier",
        )
    )


def _troop_cooldown_multiplier(game, owner=None) -> float:
    value = 1.0
    if hasattr(game, "item_multiplier"):
        value *= game.item_multiplier("troop_cooldown_multiplier")
    if owner is not None and hasattr(owner, "ability_cooldown_multiplier"):
        value *= owner.ability_cooldown_multiplier()
    return value


def _research_multiplier(owner, research_id: str) -> float:
    research = getattr(owner, "research", None)
    return research.multiplier(research_id) if research is not None else 1.0


def _format_seconds(value: float) -> str:
    return f"{max(0.0, value):0.1f}s"


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:0.1f}"
