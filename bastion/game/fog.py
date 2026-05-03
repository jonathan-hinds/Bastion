from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable

import pygame


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "fog.json"


@dataclass(frozen=True)
class VisionProfile:
    radius: float
    hardness: float = 0.70


@dataclass(frozen=True)
class VisionSource:
    pos: pygame.Vector2
    profile: VisionProfile


@dataclass(frozen=True)
class FogSettings:
    enabled: bool
    reveal_speed: float
    hide_speed: float
    visibility_threshold: float
    explored_threshold: float
    source_interval: float
    profiles: dict[str, VisionProfile]

    def profile(self, key: str, fallback: str = "structure") -> VisionProfile:
        return self.profiles.get(key) or self.profiles.get(fallback) or VisionProfile(220.0)


def load_fog_settings(path: Path | None = None) -> FogSettings:
    path = DATA_PATH if path is None else path
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Fog data must be a JSON object.")

    global_data = raw.get("global", {})
    if not isinstance(global_data, dict):
        global_data = {}
    profile_records = raw.get("profiles", {})
    if not isinstance(profile_records, dict):
        profile_records = {}

    profiles: dict[str, VisionProfile] = {}
    for key, record in profile_records.items():
        if not isinstance(record, dict):
            continue
        try:
            radius = float(record.get("radius", 220.0))
            hardness = float(record.get("hardness", 0.70))
        except (TypeError, ValueError):
            radius = 220.0
            hardness = 0.70
        profiles[str(key)] = VisionProfile(max(1.0, radius), max(0.05, min(0.98, hardness)))

    return FogSettings(
        enabled=bool(global_data.get("enabled", True)),
        reveal_speed=max(0.1, float(global_data.get("reveal_speed", 4.8))),
        hide_speed=max(0.1, float(global_data.get("hide_speed", 6.5))),
        visibility_threshold=max(0.01, min(0.95, float(global_data.get("visibility_threshold", 0.18)))),
        explored_threshold=max(0.01, min(0.95, float(global_data.get("explored_threshold", 0.16)))),
        source_interval=max(0.0, float(global_data.get("source_interval", 0.08))),
        profiles=profiles,
    )


FOG_SETTINGS = load_fog_settings()


class FogOfWar:
    def __init__(self, grid, settings: FogSettings = FOG_SETTINGS) -> None:
        self.grid = grid
        self.settings = settings
        self.width = grid.width
        self.height = grid.height
        self.enabled = settings.enabled
        self.visible = [[0.0 for _ in range(self.height)] for _ in range(self.width)]
        self.explored = [[0.0 for _ in range(self.height)] for _ in range(self.width)]
        self._target = [[0.0 for _ in range(self.height)] for _ in range(self.width)]
        self._surface = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self._source_timer = 0.0
        self._cached_sources: list[VisionSource] = []

    def update(self, dt: float, sources: Iterable[VisionSource], immediate: bool = False) -> None:
        if not self.enabled:
            return
        if immediate:
            self._cached_sources = list(sources)
            self._source_timer = 0.0
        else:
            self._source_timer -= dt
            if self._source_timer <= 0.0:
                self._cached_sources = list(sources)
                self._source_timer = self.settings.source_interval

        self._clear_target()
        for source in self._cached_sources:
            self._paint_source(source)
        self._blend_visibility(dt, immediate)

    def reveal_now(self, sources: Iterable[VisionSource]) -> None:
        self.update(0.0, sources, immediate=True)

    def profile(self, key: str, fallback: str = "structure") -> VisionProfile:
        return self.settings.profile(key, fallback)

    def is_visible_cell(self, cell: tuple[int, int], threshold: float | None = None) -> bool:
        if not self.enabled:
            return True
        x, y = cell
        if not self.grid.in_bounds(cell):
            return False
        limit = self.settings.visibility_threshold if threshold is None else threshold
        return self.visible[x][y] >= limit

    def is_explored_cell(self, cell: tuple[int, int], threshold: float | None = None) -> bool:
        if not self.enabled:
            return True
        x, y = cell
        if not self.grid.in_bounds(cell):
            return False
        limit = self.settings.explored_threshold if threshold is None else threshold
        return self.explored[x][y] >= limit

    def is_visible_world(self, pos: pygame.Vector2 | tuple[float, float], radius: float = 0.0) -> bool:
        if not self.enabled:
            return True
        return self._world_query(self.visible, pos, radius, self.settings.visibility_threshold)

    def is_explored_world(self, pos: pygame.Vector2 | tuple[float, float], radius: float = 0.0) -> bool:
        if not self.enabled:
            return True
        return self._world_query(self.explored, pos, radius, self.settings.explored_threshold)

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if not self.enabled:
            return
        self._refresh_surface()

        world_left = max(0.0, camera.screen_to_world(viewport.topleft, viewport).x)
        world_top = max(0.0, camera.screen_to_world(viewport.topleft, viewport).y)
        world_right = min(float(self.grid.world_size[0]), camera.screen_to_world(viewport.bottomright, viewport).x)
        world_bottom = min(float(self.grid.world_size[1]), camera.screen_to_world(viewport.bottomright, viewport).y)
        if world_right <= world_left or world_bottom <= world_top:
            return

        margin = 2
        left = max(0, int(world_left // self.grid.tile_size) - margin)
        top = max(0, int(world_top // self.grid.tile_size) - margin)
        right = min(self.width, int(math.ceil(world_right / self.grid.tile_size)) + margin)
        bottom = min(self.height, int(math.ceil(world_bottom / self.grid.tile_size)) + margin)
        if right <= left or bottom <= top:
            return

        source_rect = pygame.Rect(left, top, right - left, bottom - top)
        world_start = (left * self.grid.tile_size, top * self.grid.tile_size)
        world_end = (right * self.grid.tile_size, bottom * self.grid.tile_size)
        screen_start = camera.world_to_screen(world_start, viewport)
        screen_end = camera.world_to_screen(world_end, viewport)
        target_rect = pygame.Rect(
            math.floor(screen_start.x),
            math.floor(screen_start.y),
            max(1, math.ceil(screen_end.x - screen_start.x)),
            max(1, math.ceil(screen_end.y - screen_start.y)),
        )
        fog_view = self._surface.subsurface(source_rect)
        scaled = pygame.transform.smoothscale(fog_view, target_rect.size)
        surface.blit(scaled, target_rect.topleft)

    def _clear_target(self) -> None:
        for x in range(self.width):
            column = self._target[x]
            for y in range(self.height):
                column[y] = 0.0

    def _paint_source(self, source: VisionSource) -> None:
        radius = source.profile.radius
        if radius <= 0:
            return
        hardness_radius = radius * source.profile.hardness
        edge_span = max(1.0, radius - hardness_radius)
        tile_size = self.grid.tile_size
        center = pygame.Vector2(source.pos)
        min_x = max(0, int((center.x - radius) // tile_size) - 1)
        max_x = min(self.width - 1, int((center.x + radius) // tile_size) + 1)
        min_y = max(0, int((center.y - radius) // tile_size) - 1)
        max_y = min(self.height - 1, int((center.y + radius) // tile_size) + 1)
        radius_sq = radius * radius

        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                cell_center = self.grid.world_center((x, y))
                distance_sq = cell_center.distance_squared_to(center)
                if distance_sq > radius_sq:
                    continue
                distance = math.sqrt(distance_sq)
                if distance <= hardness_radius:
                    strength = 1.0
                else:
                    t = max(0.0, min(1.0, (distance - hardness_radius) / edge_span))
                    strength = 1.0 - (t * t * (3.0 - 2.0 * t))
                if strength > self._target[x][y]:
                    self._target[x][y] = strength

    def _blend_visibility(self, dt: float, immediate: bool) -> None:
        reveal_step = 1.0 if immediate else self.settings.reveal_speed * dt
        hide_step = 1.0 if immediate else self.settings.hide_speed * dt
        for x in range(self.width):
            visible_column = self.visible[x]
            explored_column = self.explored[x]
            target_column = self._target[x]
            for y in range(self.height):
                target = target_column[y]
                current = visible_column[y]
                if target >= current:
                    current = min(target, current + reveal_step)
                else:
                    current = max(target, current - hide_step)
                visible_column[y] = current
                if current > explored_column[y]:
                    explored_column[y] = current

    def _refresh_surface(self) -> None:
        self._surface.lock()
        try:
            for x in range(self.width):
                column = self.explored[x]
                for y in range(self.height):
                    alpha = int(255 * (1.0 - max(0.0, min(1.0, column[y]))))
                    self._surface.set_at((x, y), (0, 0, 0, alpha))
        finally:
            self._surface.unlock()

    def _world_query(
        self,
        field: list[list[float]],
        pos: pygame.Vector2 | tuple[float, float],
        radius: float,
        threshold: float,
    ) -> bool:
        point = pygame.Vector2(pos)
        center_cell = self.grid.cell_from_world(point)
        if radius <= 0:
            if not self.grid.in_bounds(center_cell):
                return False
            return field[center_cell[0]][center_cell[1]] >= threshold

        tile_radius = max(1, int(math.ceil(radius / self.grid.tile_size)))
        cx, cy = center_cell
        for x in range(max(0, cx - tile_radius), min(self.width, cx + tile_radius + 1)):
            for y in range(max(0, cy - tile_radius), min(self.height, cy + tile_radius + 1)):
                if field[x][y] < threshold:
                    continue
                if self.grid.world_center((x, y)).distance_to(point) <= radius + self.grid.tile_size * 0.72:
                    return True
        return False
