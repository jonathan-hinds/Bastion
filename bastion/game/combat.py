from __future__ import annotations

import math
import random
from typing import Callable

import pygame


DamageCallback = Callable[[object, float, pygame.Vector2], None]


class MeleeAttackController:
    def __init__(self, owner, cooldown_attr: str = "cooldown") -> None:
        self.owner = owner
        self.cooldown_attr = cooldown_attr

    def can_reach(self, target, reach: float) -> bool:
        return self.owner.pos.distance_to(target.pos) <= self.attack_distance(target, reach)

    def attack_distance(self, target, reach: float) -> float:
        return reach + float(getattr(target, "radius", 0.0))

    def strike(
        self,
        target,
        damage: float,
        reach: float,
        cooldown: float,
        apply_damage: DamageCallback,
    ) -> bool:
        if getattr(self.owner, self.cooldown_attr, 0.0) > 0 or target is None:
            return False

        setattr(self.owner, self.cooldown_attr, cooldown)
        self.start_swing(target, reach)
        apply_damage(target, damage, pygame.Vector2(self.owner.pos))
        return True

    def start_swing(self, target, reach: float) -> None:
        direction = target.pos - self.owner.pos
        if direction.length_squared() == 0:
            angle = random.random() * math.tau
            direction = pygame.Vector2(math.cos(angle), math.sin(angle))
        direction = direction.normalize()
        distance = self.owner.pos.distance_to(target.pos)
        self.owner.swing_dir = direction
        self.owner.swing_reach = min(reach + float(getattr(target, "radius", 0.0)), distance + float(getattr(target, "radius", 0.0)))
        self.owner.swing_time = self.owner.swing_duration
