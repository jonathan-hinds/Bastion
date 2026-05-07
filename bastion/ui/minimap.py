from __future__ import annotations

from dataclasses import dataclass

import pygame

try:
    import pygame.surfarray as surfarray
except (ImportError, pygame.error):
    surfarray = None
try:
    import numpy as np
except ImportError:
    np = None


PAN_KEYS = {"w", "a", "s", "d", "up", "down", "left", "right"}


@dataclass(frozen=True, slots=True)
class MinimapColors:
    unexplored: tuple[int, int, int] = (0, 0, 0)
    border: tuple[int, int, int] = (95, 95, 95)
    camera: tuple[int, int, int] = (245, 245, 245)
    core: tuple[int, int, int] = (242, 242, 230)
    structure: tuple[int, int, int] = (178, 199, 182)
    troop: tuple[int, int, int] = (126, 194, 132)
    enemy: tuple[int, int, int] = (208, 96, 82)
    resource: tuple[int, int, int] = (201, 176, 105)


class MinimapPanel:
    terrain_colors = (
        (30, 34, 30),
        (52, 58, 48),
        (76, 76, 61),
        (96, 91, 72),
    )

    def __init__(self, colors: MinimapColors | None = None) -> None:
        self.colors = colors or MinimapColors()
        self.dragging_camera = False
        self.held_pan_keys: set[str] = set()
        self._terrain_key: tuple | None = None
        self._terrain_surface: pygame.Surface | None = None
        self._terrain_array = None
        self._fogged_key: tuple | None = None
        self._fogged_surface: pygame.Surface | None = None
        self._scaled_key: tuple | None = None
        self._scaled_surface: pygame.Surface | None = None

    def release_input(self) -> None:
        self.dragging_camera = False
        self.held_pan_keys.clear()

    def set_key(self, key: str, pressed: bool) -> bool:
        key = str(key).lower()
        if key not in PAN_KEYS:
            return False
        if pressed:
            self.held_pan_keys.add(key)
        else:
            self.held_pan_keys.discard(key)
        return True

    def pan_direction(self) -> pygame.Vector2:
        direction = pygame.Vector2(0, 0)
        if "a" in self.held_pan_keys or "left" in self.held_pan_keys:
            direction.x -= 1
        if "d" in self.held_pan_keys or "right" in self.held_pan_keys:
            direction.x += 1
        if "w" in self.held_pan_keys or "up" in self.held_pan_keys:
            direction.y -= 1
        if "s" in self.held_pan_keys or "down" in self.held_pan_keys:
            direction.y += 1
        if direction.length_squared() > 0:
            return direction.normalize()
        return direction

    def handle_mouse_down(self, pos: tuple[int, int], bounds: pygame.Rect, state) -> bool:
        if not self._focus_camera_at_pos(pos, bounds, state):
            return False
        self.dragging_camera = True
        return True

    def handle_mouse_motion(self, pos: tuple[int, int], bounds: pygame.Rect, state) -> bool:
        if not self.dragging_camera:
            return False
        return self._focus_camera_at_pos(pos, bounds, state, clamp=True)

    def handle_mouse_up(self) -> bool:
        if not self.dragging_camera:
            return False
        self.dragging_camera = False
        return True

    def draw(
        self,
        surface: pygame.Surface,
        bounds: pygame.Rect,
        state,
        camera,
        viewport: pygame.Rect,
        mouse_pos: tuple[int, int],
    ) -> None:
        grid = getattr(state, "grid", None)
        if grid is None or bounds.width <= 0 or bounds.height <= 0:
            return
        map_rect = self.map_rect(bounds, grid)
        pygame.draw.rect(surface, self.colors.unexplored, map_rect)
        source = self._fogged_surface_for(state)
        if source is not None:
            scaled = self._scaled_surface_for(source, map_rect.size)
            surface.blit(scaled, map_rect)
        self._draw_markers(surface, map_rect, state)
        tutorial = getattr(state, "tutorial", None)
        if tutorial is not None:
            tutorial.draw_minimap_guidance(surface, map_rect, grid)
        self._draw_camera_rect(surface, map_rect, grid, camera, viewport)
        pygame.draw.rect(surface, self.colors.camera if map_rect.collidepoint(mouse_pos) else self.colors.border, map_rect, 1)

    def map_rect(self, bounds: pygame.Rect, grid) -> pygame.Rect:
        world_width, world_height = grid.world_size
        aspect = world_width / max(1, world_height)
        width = bounds.width
        height = max(1, int(round(width / aspect)))
        if height > bounds.height:
            height = bounds.height
            width = max(1, int(round(height * aspect)))
        rect = pygame.Rect(0, 0, width, height)
        rect.center = bounds.center
        return rect

    def _focus_camera_at_pos(
        self,
        pos: tuple[int, int],
        bounds: pygame.Rect,
        state,
        *,
        clamp: bool = False,
    ) -> bool:
        grid = getattr(state, "grid", None)
        if grid is None:
            return False
        map_rect = self.map_rect(bounds, grid)
        if not map_rect.collidepoint(pos) and not clamp:
            return False
        x = min(map_rect.right - 1, max(map_rect.left, int(pos[0])))
        y = min(map_rect.bottom - 1, max(map_rect.top, int(pos[1])))
        x_ratio = (x - map_rect.left) / max(1, map_rect.width)
        y_ratio = (y - map_rect.top) / max(1, map_rect.height)
        state.pending_camera_focus = pygame.Vector2(
            x_ratio * grid.world_size[0],
            y_ratio * grid.world_size[1],
        )
        return True

    def _terrain_surface_for(self, grid) -> pygame.Surface:
        terrain = grid.terrain
        key = (id(terrain), grid.width, grid.height, terrain.max_elevation())
        if key == self._terrain_key and self._terrain_surface is not None:
            return self._terrain_surface

        surface = pygame.Surface((grid.width, grid.height))
        surface.lock()
        try:
            for x in range(grid.width):
                for y in range(grid.height):
                    elevation = max(0, terrain.elevation_at((x, y)))
                    color = self.terrain_colors[min(elevation, len(self.terrain_colors) - 1)]
                    surface.set_at((x, y), color)
        finally:
            surface.unlock()
        self._terrain_key = key
        self._terrain_surface = surface
        self._terrain_array = surfarray.array3d(surface) if surfarray is not None and np is not None else None
        self._fogged_key = None
        self._scaled_key = None
        return surface

    def _fogged_surface_for(self, state) -> pygame.Surface | None:
        grid = getattr(state, "grid", None)
        if grid is None:
            return None
        base = self._terrain_surface_for(grid)
        fog = getattr(state, "fog", None)
        fog_revision = getattr(fog, "_surface_revision", 0) if fog is not None else 0
        key = (self._terrain_key, id(fog), fog_revision, bool(getattr(fog, "enabled", False)))
        if key == self._fogged_key and self._fogged_surface is not None:
            return self._fogged_surface

        fogged = base.copy()
        if fog is not None and getattr(fog, "enabled", False):
            self._apply_fog(fogged, fog)
        self._fogged_key = key
        self._fogged_surface = fogged
        self._scaled_key = None
        return fogged

    def _apply_fog(self, surface: pygame.Surface, fog) -> None:
        threshold = fog.settings.explored_threshold
        if surfarray is not None and np is not None and hasattr(fog.explored, "shape") and self._terrain_array is not None:
            try:
                pixels = surfarray.pixels3d(surface)
            except (pygame.error, ValueError):
                pixels = None
            if pixels is not None:
                try:
                    explored = np.asarray(fog.explored, dtype=np.float32)
                    strength = (0.30 + np.clip(explored, 0.0, 1.0) * 0.70)[:, :, None]
                    pixels[:, :, :] = (self._terrain_array * strength).astype(np.uint8)
                    pixels[explored < threshold] = self.colors.unexplored
                    return
                finally:
                    del pixels

        surface.lock()
        try:
            for x in range(fog.width):
                for y in range(fog.height):
                    explored = max(0.0, min(1.0, float(fog.explored[x][y])))
                    if explored < threshold:
                        surface.set_at((x, y), self.colors.unexplored)
                        continue
                    color = surface.get_at((x, y))
                    strength = 0.30 + explored * 0.70
                    surface.set_at((x, y), (int(color.r * strength), int(color.g * strength), int(color.b * strength)))
        finally:
            surface.unlock()

    def _scaled_surface_for(self, source: pygame.Surface, size: tuple[int, int]) -> pygame.Surface:
        key = (self._fogged_key, size)
        if key == self._scaled_key and self._scaled_surface is not None:
            return self._scaled_surface
        scaled = pygame.transform.scale(source, size)
        self._scaled_key = key
        self._scaled_surface = scaled
        return scaled

    def _draw_markers(self, surface: pygame.Surface, map_rect: pygame.Rect, state) -> None:
        grid = state.grid
        for deposit in getattr(state, "resource_deposits", ()):
            if getattr(deposit, "active", True) and state.is_world_explored(deposit.pos, getattr(deposit, "radius", 12)):
                self._draw_world_marker(surface, map_rect, grid, deposit.pos, self.colors.resource, 2)
        for cell in getattr(grid, "walls", ()):
            if state.is_cell_explored(cell):
                self._draw_world_marker(surface, map_rect, grid, grid.world_center(cell), self.colors.structure, 1)
        for core in getattr(state, "core_targets", ()):
            if getattr(core, "alive", False) and state.is_world_explored(core.pos, getattr(core, "radius", grid.tile_size)):
                self._draw_world_marker(surface, map_rect, grid, core.pos, self.colors.core, 5)
        for structure in getattr(state, "buildings", ()):
            if getattr(structure, "alive", False) and state.is_world_explored(structure.pos, getattr(structure, "radius", grid.tile_size)):
                self._draw_world_marker(surface, map_rect, grid, structure.pos, self.colors.structure, 3)
        for structure in getattr(state, "towers", ()):
            if getattr(structure, "alive", False) and state.is_world_explored(structure.pos, getattr(structure, "radius", grid.tile_size)):
                self._draw_world_marker(surface, map_rect, grid, structure.pos, self.colors.structure, 3)
        for troop in getattr(state, "troops", ()):
            if getattr(troop, "alive", False) and state.is_world_explored(troop.pos, getattr(troop, "radius", 8)):
                self._draw_world_marker(surface, map_rect, grid, troop.pos, self.colors.troop, 3)
        for enemy in getattr(state, "enemies", ()):
            if getattr(enemy, "alive", False) and state.is_world_explored(enemy.pos, getattr(enemy, "radius", 8)):
                self._draw_world_marker(surface, map_rect, grid, enemy.pos, self.colors.enemy, 3)

    def _draw_world_marker(
        self,
        surface: pygame.Surface,
        map_rect: pygame.Rect,
        grid,
        world: pygame.Vector2 | tuple[float, float],
        color: tuple[int, int, int],
        size: int,
    ) -> None:
        point = self._world_to_map(map_rect, grid, world)
        marker = pygame.Rect(0, 0, max(1, size), max(1, size))
        marker.center = (round(point.x), round(point.y))
        if marker.colliderect(map_rect):
            pygame.draw.rect(surface, color, marker)

    def _draw_camera_rect(self, surface: pygame.Surface, map_rect: pygame.Rect, grid, camera, viewport: pygame.Rect) -> None:
        if camera is None:
            return
        world_width, world_height = grid.world_size
        left = map_rect.left + camera.offset.x / max(1, world_width) * map_rect.width
        top = map_rect.top + camera.offset.y / max(1, world_height) * map_rect.height
        width = viewport.width / max(0.001, camera.zoom) / max(1, world_width) * map_rect.width
        height = viewport.height / max(0.001, camera.zoom) / max(1, world_height) * map_rect.height
        rect = pygame.Rect(round(left), round(top), max(3, round(width)), max(3, round(height))).clip(map_rect)
        pygame.draw.rect(surface, self.colors.camera, rect, 2)

    def _world_to_map(self, map_rect: pygame.Rect, grid, world: pygame.Vector2 | tuple[float, float]) -> pygame.Vector2:
        point = pygame.Vector2(world)
        return pygame.Vector2(
            map_rect.left + point.x / max(1, grid.world_size[0]) * map_rect.width,
            map_rect.top + point.y / max(1, grid.world_size[1]) * map_rect.height,
        )
