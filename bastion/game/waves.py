from __future__ import annotations

import random

from bastion import config
from bastion.game.enemy_defs import enemy_collision_radius, enemy_ids_by_role, enemy_ids_with_tag


class WaveManager:
    def __init__(self, grid) -> None:
        self.grid = grid
        self.wave_number = 0
        self.active = False
        self.day_duration = config.BASE_DAY_DURATION
        self.intermission = self.day_duration
        self.elapsed = 0.0
        self.spawn_events: list[tuple[float, str, tuple[int, int]]] = []
        self.enemy_count_multiplier = 1.0

    @property
    def night_number(self) -> int:
        return self.wave_number

    @property
    def is_night(self) -> bool:
        return self.active

    def start_next_wave(self, game) -> None:
        if self.active or getattr(game.round_events, "awaiting_choice", False):
            return
        self.wave_number += 1
        self.active = True
        self.elapsed = 0.0
        self.day_duration = self._day_duration(game)
        self.intermission = self.day_duration
        self.spawn_events = self._build_spawn_events(self.wave_number)
        game.message(f"NIGHT {self.wave_number}")

    def update(self, dt: float, game) -> None:
        if game.game_over:
            return
        if getattr(game.round_events, "awaiting_choice", False):
            return
        if not self.active:
            self._sync_day_duration(game)
            self.intermission = max(0.0, self.intermission - dt)
            if self.intermission <= 0:
                self.start_next_wave(game)
            return

        self.elapsed += dt
        while self.spawn_events and self.spawn_events[0][0] <= self.elapsed:
            _, kind, spawn = self.spawn_events.pop(0)
            game.spawn_enemy(kind, spawn, self.wave_number)

        if not self.spawn_events and not any(enemy.alive and getattr(enemy, "spawn_group", "wave") == "wave" for enemy in game.enemies):
            self.complete_wave(game)

    def complete_wave(self, game) -> None:
        self.active = False
        self.day_duration = self._day_duration(game)
        self.intermission = self.day_duration
        bonus = 11 + self.wave_number * 3
        game.gold += bonus
        game.message(f"DAWN +{bonus}")
        game.offer_round_event()

    def skip_next_wave(self, game) -> int:
        if self.active:
            return 0
        self.wave_number += 1
        self.elapsed = 0.0
        self.spawn_events = []
        self.day_duration = self._day_duration(game)
        self.intermission = self.day_duration
        bonus = 11 + self.wave_number * 3
        game.gold += bonus
        return bonus

    def _day_duration(self, game) -> float:
        duration = game.day_duration_seconds() if hasattr(game, "day_duration_seconds") else config.BASE_DAY_DURATION
        return max(10.0, float(duration))

    def _sync_day_duration(self, game) -> None:
        duration = self._day_duration(game)
        if abs(duration - self.day_duration) < 0.01:
            return
        progress = 1.0 - self.intermission / max(1.0, self.day_duration)
        self.day_duration = duration
        self.intermission = max(0.0, self.day_duration * (1.0 - max(0.0, min(1.0, progress))))

    def _build_spawn_events(self, wave: int) -> list[tuple[float, str, tuple[int, int]]]:
        count = max(1, int(round((5.0 + wave * 1.8) * self.enemy_count_multiplier)))
        duration = 17.0 + wave * 2.6
        spacing = duration / max(1, count - 1)
        jitter = min(0.5, spacing * 0.25)
        events = []
        for index in range(count):
            t = (index / max(1, count - 1)) * duration + random.uniform(0.0, jitter)
            kind = self._pick_kind(wave)
            spawn = self.grid.random_spawn_cell(self.grid.navigation_radius(enemy_collision_radius(kind)))
            events.append((t, kind, spawn))
        events.extend(self._scheduled_boss_events(wave, duration))
        events.sort(key=lambda item: item[0])
        return events

    def _scheduled_boss_events(self, wave: int, duration: float) -> list[tuple[float, str, tuple[int, int]]]:
        if wave < 4 or wave % 4 != 0:
            return []
        bosses = enemy_ids_with_tag("boss")
        if not bosses:
            return []
        boss_count = 1 if wave < 12 else 2
        events = []
        for index in range(boss_count):
            progress = 0.62 + index * 0.18
            t = min(duration, max(0.0, duration * progress + random.uniform(-0.8, 0.8)))
            kind = random.choice(bosses)
            spawn = self.grid.random_spawn_cell(self.grid.navigation_radius(enemy_collision_radius(kind)))
            events.append((t, kind, spawn))
        return events

    def _pick_kind(self, wave: int) -> str:
        roll = random.random()
        ranged_chance = min(0.24, max(0.0, (wave - 1) * 0.035))
        large_chance = min(0.30, max(0.0, (wave - 3) * 0.035))
        medium_chance = min(0.58, 0.38 + wave * 0.02)
        ranged = enemy_ids_by_role("ranged") or ["ranged"]
        melee = enemy_ids_by_role("melee") or ["small", "medium", "large"]
        if roll < ranged_chance:
            return random.choice(ranged)
        if roll < ranged_chance + large_chance:
            return "large" if "large" in melee else random.choice(melee)
        if roll < ranged_chance + large_chance + medium_chance:
            return "medium" if "medium" in melee else random.choice(melee)
        return "small" if "small" in melee else random.choice(melee)
