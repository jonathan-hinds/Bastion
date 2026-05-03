from __future__ import annotations

from dataclasses import dataclass, field

import pygame


@dataclass
class AggroEntry:
    target: object
    score: float
    reason: str = "event"


class AggroRule:
    def score(self, component: "AggroComponent", enemy, target, game) -> float:
        return 0.0


@dataclass(frozen=True)
class ThreatMemoryRule(AggroRule):
    weight: float = 1.0

    def score(self, component: "AggroComponent", enemy, target, game) -> float:
        entry = component.threat.get(target)
        return 0.0 if entry is None else entry.score * self.weight


@dataclass(frozen=True)
class CoreObjectiveRule(AggroRule):
    weight: float

    def score(self, component: "AggroComponent", enemy, target, game) -> float:
        return self.weight if target_class(target) == "core" else 0.0


@dataclass(frozen=True)
class TargetClassRule(AggroRule):
    weights: dict[str, float]

    def score(self, component: "AggroComponent", enemy, target, game) -> float:
        return self.weights.get(target_class(target), 0.0)


@dataclass(frozen=True)
class ProximityRule(AggroRule):
    radius: float
    weight: float

    def score(self, component: "AggroComponent", enemy, target, game) -> float:
        if target_class(target) == "core":
            return 0.0
        distance = enemy.pos.distance_to(target.pos)
        if distance > self.radius + getattr(target, "radius", 0):
            return 0.0
        return self.weight * (1.0 - min(1.0, distance / max(1.0, self.radius)))


@dataclass(frozen=True)
class AggroProfile:
    perception_radius: float
    memory_radius: float
    decay_per_second: float
    retarget_interval: float
    minimum_score: float
    threat_weights: dict[str, float]
    rules: tuple[AggroRule, ...] = field(default_factory=tuple)

    def threat_weight(self, reason: str) -> float:
        return self.threat_weights.get(reason, self.threat_weights.get("default", 1.0))


class AggroComponent:
    def __init__(self, owner, profile: AggroProfile) -> None:
        self.owner = owner
        self.profile = profile
        self.threat: dict[object, AggroEntry] = {}
        self.current_target = None
        self.retarget_timer = 0.0

    def update(self, dt: float) -> None:
        self.retarget_timer = max(0.0, self.retarget_timer - dt)
        if not self.threat:
            return
        expired = []
        for target, entry in self.threat.items():
            if not target_alive(target):
                expired.append(target)
                continue
            entry.score = max(0.0, entry.score - self.profile.decay_per_second * dt)
            if entry.score <= 0.0:
                expired.append(target)
        for target in expired:
            self.threat.pop(target, None)

    def add_threat(self, target, amount: float, reason: str = "default") -> None:
        if amount <= 0 or target is None or not target_alive(target):
            return
        entry = self.threat.get(target)
        if entry is None:
            entry = AggroEntry(target, 0.0, reason)
            self.threat[target] = entry
        entry.score += amount * self.profile.threat_weight(reason)
        entry.reason = reason
        self.retarget_timer = 0.0

    def choose_target(self, game):
        if self.retarget_timer > 0 and target_alive(self.current_target):
            return self.current_target

        candidates = list(game.aggro_candidates(self.owner.pos, self.profile.perception_radius))
        for target in self.threat:
            if not target_alive(target) or target in candidates:
                continue
            if self.owner.pos.distance_to(target.pos) <= self.profile.memory_radius + getattr(target, "radius", 0):
                candidates.append(target)

        best = None
        best_score = self.profile.minimum_score
        for target in candidates:
            if target is self.owner or not target_alive(target):
                continue
            score = sum(rule.score(self, self.owner, target, game) for rule in self.profile.rules)
            if score > best_score:
                best = target
                best_score = score

        self.current_target = best
        self.retarget_timer = self.profile.retarget_interval
        return best


def melee_aggro_profile() -> AggroProfile:
    return AggroProfile(
        perception_radius=245.0,
        memory_radius=520.0,
        decay_per_second=5.2,
        retarget_interval=0.24,
        minimum_score=0.5,
        threat_weights={"damage": 1.35, "heal": 2.2, "repair": 2.4, "taunt": 4.0, "default": 1.0},
        rules=(
            CoreObjectiveRule(22.0),
            TargetClassRule({"structure": 14.0, "troop": 6.0, "support": 9.0, "worker": 7.0}),
            ProximityRule(128.0, 24.0),
            ThreatMemoryRule(1.0),
        ),
    )


def ranged_aggro_profile() -> AggroProfile:
    return AggroProfile(
        perception_radius=360.0,
        memory_radius=620.0,
        decay_per_second=4.8,
        retarget_interval=0.28,
        minimum_score=0.5,
        threat_weights={"damage": 1.0, "heal": 2.0, "repair": 2.5, "taunt": 4.0, "default": 1.0},
        rules=(
            CoreObjectiveRule(9.0),
            TargetClassRule({"structure": 24.0, "troop": 2.0, "support": 7.0, "worker": 3.0}),
            ProximityRule(170.0, 7.0),
            ThreatMemoryRule(1.05),
        ),
    )


def ambient_melee_aggro_profile() -> AggroProfile:
    return AggroProfile(
        perception_radius=245.0,
        memory_radius=520.0,
        decay_per_second=5.8,
        retarget_interval=0.28,
        minimum_score=0.5,
        threat_weights={"damage": 1.55, "heal": 2.2, "repair": 2.4, "taunt": 4.2, "default": 1.0},
        rules=(
            TargetClassRule({"troop": 13.0, "support": 15.0, "worker": 12.0, "structure": 7.0}),
            ProximityRule(155.0, 24.0),
            ThreatMemoryRule(1.15),
        ),
    )


def ambient_ranged_aggro_profile() -> AggroProfile:
    return AggroProfile(
        perception_radius=315.0,
        memory_radius=600.0,
        decay_per_second=5.2,
        retarget_interval=0.30,
        minimum_score=0.5,
        threat_weights={"damage": 1.25, "heal": 2.0, "repair": 2.4, "taunt": 4.0, "default": 1.0},
        rules=(
            TargetClassRule({"troop": 9.0, "support": 14.0, "worker": 9.0, "structure": 10.0}),
            ProximityRule(190.0, 13.0),
            ThreatMemoryRule(1.2),
        ),
    )


def target_alive(target) -> bool:
    return bool(target is not None and getattr(target, "alive", True))


def target_class(target) -> str:
    return str(getattr(target, "target_class", ""))
