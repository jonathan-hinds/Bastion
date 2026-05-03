from __future__ import annotations

import pygame

from bastion import config


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class Camera:
    def __init__(self, world_size: tuple[int, int]) -> None:
        self.world_size = pygame.Vector2(world_size)
        self.offset = pygame.Vector2(0, 0)
        self.zoom = 1.0

    def world_to_screen(self, world: pygame.Vector2 | tuple[float, float], viewport: pygame.Rect) -> pygame.Vector2:
        point = pygame.Vector2(world)
        return pygame.Vector2(
            viewport.x + (point.x - self.offset.x) * self.zoom,
            viewport.y + (point.y - self.offset.y) * self.zoom,
        )

    def screen_to_world(self, screen: pygame.Vector2 | tuple[float, float], viewport: pygame.Rect) -> pygame.Vector2:
        point = pygame.Vector2(screen)
        return pygame.Vector2(
            (point.x - viewport.x) / self.zoom + self.offset.x,
            (point.y - viewport.y) / self.zoom + self.offset.y,
        )

    def pan_screen_delta(self, delta: pygame.Vector2, viewport: pygame.Rect) -> None:
        self.offset -= delta / self.zoom
        self.clamp_to_world(viewport)

    def pan_world_delta(self, delta: pygame.Vector2, viewport: pygame.Rect) -> None:
        self.offset += delta
        self.clamp_to_world(viewport)

    def center_on(self, world: pygame.Vector2 | tuple[float, float], viewport: pygame.Rect) -> None:
        point = pygame.Vector2(world)
        self.offset = point - pygame.Vector2(viewport.size) / (2 * self.zoom)
        self.clamp_to_world(viewport)

    def zoom_at(self, amount: float, mouse_screen: tuple[int, int], viewport: pygame.Rect) -> None:
        before = self.screen_to_world(mouse_screen, viewport)
        self.zoom = clamp(self.zoom * amount, config.ZOOM_MIN, config.ZOOM_MAX)
        after = self.screen_to_world(mouse_screen, viewport)
        self.offset += before - after
        self.clamp_to_world(viewport)

    def clamp_to_world(self, viewport: pygame.Rect) -> None:
        max_x = max(0.0, self.world_size.x - viewport.width / self.zoom)
        max_y = max(0.0, self.world_size.y - viewport.height / self.zoom)
        self.offset.x = clamp(self.offset.x, 0.0, max_x)
        self.offset.y = clamp(self.offset.y, 0.0, max_y)

    def visible_tile_bounds(
        self,
        viewport: pygame.Rect,
        tile_size: int,
        map_width: int,
        map_height: int,
    ) -> tuple[int, int, int, int]:
        top_left = self.screen_to_world(viewport.topleft, viewport)
        bottom_right = self.screen_to_world(viewport.bottomright, viewport)
        x0 = max(0, int(top_left.x // tile_size) - 1)
        y0 = max(0, int(top_left.y // tile_size) - 1)
        x1 = min(map_width, int(bottom_right.x // tile_size) + 2)
        y1 = min(map_height, int(bottom_right.y // tile_size) + 2)
        return x0, y0, x1, y1
