from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable

import pygame

from bastion import config
from bastion.engine.drawing import draw_circle_alpha
from bastion.game.combat import MeleeAttackController
from bastion.game.elements import ElementalEffect
from bastion.game.tower_mods import TOWER_MODS


@dataclass(frozen=True)
class AbilityCard:
    ability_id: str
    name: str
    description: str
    details: tuple[str, ...]
    passive: bool = False
    state: str = ""
    tags: tuple[str, ...] = ()


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
        return True

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
        return hit

    def _damage_callback(self, game):
        if self.target_side == "friendly":
            return lambda target, amount, source_pos: game.damage_friendly(target, amount, source_pos=source_pos, element=self.element)
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

    def attack_cooldown(self, game=None) -> float:
        return 1.0 / max(0.05, float(getattr(self.owner, "fire_rate", 1.0)))

    def activate(self, game, target=None) -> bool:
        if target is None or not self.ready:
            return False
        if self.owner.pos.distance_to(target.pos) > float(getattr(self.owner, "attack_range", 0.0)) + float(getattr(target, "radius", 0.0)):
            return False
        from bastion.game.entities import EnemyProjectile

        self.set_cooldown(self.attack_cooldown(game))
        game.enemy_projectiles.append(
            EnemyProjectile(
                pos=pygame.Vector2(self.owner.pos),
                target=target,
                speed=float(getattr(self.owner, "projectile_speed", 260.0)),
                damage=float(getattr(self.owner, "damage", 0.0)),
            )
        )
        game.spawn_hit(self.owner.pos, 2)
        return True

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Damage {_format_number(float(getattr(self.owner, 'damage', 0.0)))} physical",
            f"Range {int(float(getattr(self.owner, 'attack_range', 0.0)))}",
            f"Cooldown {_format_seconds(self.attack_cooldown(game))}",
        ]


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
        alpha = 34 if self.ready else 14
        draw_circle_alpha(surface, screen, self.radius * camera.zoom, config.PALETTE.white, alpha, 1)


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

    def should_auto_activate(self, game) -> bool:
        if not getattr(self.owner, "attack_enabled", True):
            return False
        return self._find_target(game) is not None

    def _find_target(self, game):
        if not self.owner.alive or not getattr(self.owner, "attack_enabled", True):
            return None
        enemies = game.targetable_enemies_near(self.owner.pos, self.radius + 24) if hasattr(game, "targetable_enemies_near") else game.nearby_enemies(self.owner.pos, self.radius + 24) if hasattr(game, "nearby_enemies") else game.enemies
        candidates = [
            enemy
            for enemy in enemies
            if enemy.alive
            and enemy.pos.distance_to(self.owner.pos) <= self.radius + enemy.radius
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
            jumps=self.jumps,
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
            f"Jumps {self.jumps}",
            f"Arc {int(self.chain_radius)}",
            f"Cooldown {_format_seconds(self.effective_cooldown(game))}",
        ]

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        alpha = 30 if self.ready else 12
        draw_circle_alpha(surface, screen, self.radius * camera.zoom, config.PALETTE.white, alpha, 1)


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
            amount = self.rate * self.tick_interval
        else:
            amount = self.rate * dt

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
            f"Rate {_format_number(self.rate)}/s",
            f"Radius {int(self.radius)}",
        ]
        interval = self.effective_tick_interval(game) if game is not None else self.tick_interval
        if interval > 0:
            lines.append(f"Tick {_format_seconds(interval)}")
        return lines

    def draw_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        screen = camera.world_to_screen(self.owner.pos, viewport)
        draw_circle_alpha(surface, screen, self.radius * camera.zoom, config.PALETTE.white, 22, 1)


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
        actual = target.restore_shield(self.rate * self.tick_interval)
        if actual <= 0:
            return
        self._award_support_xp(game, actual)
        self._show_support_fx(game, target)

    def detail_lines(self, game=None) -> list[str]:
        return [
            f"Recharge {_format_number(self.rate)}/s",
            f"Radius {int(self.radius)}",
            f"Tick {_format_seconds(self.effective_tick_interval(game) if game is not None else self.tick_interval)}",
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


def configure_troop_abilities(owner) -> AbilitySystemComponent:
    component = getattr(owner, "abilities", None)
    if component is None:
        component = AbilitySystemComponent(owner)
        owner.abilities = component
    else:
        component.clear()

    kind = getattr(owner, "kind", "")
    if kind == "archer":
        component.add(MultiShotAttackAbility(owner))
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
