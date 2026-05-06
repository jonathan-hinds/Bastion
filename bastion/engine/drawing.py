from __future__ import annotations

import pygame


_ALPHA_SURFACE_CACHE_LIMIT = 768
_alpha_surface_cache: dict[tuple, pygame.Surface] = {}


def _cached_alpha_surface(key: tuple, factory) -> pygame.Surface:
    cached = _alpha_surface_cache.get(key)
    if cached is not None:
        return cached
    if len(_alpha_surface_cache) >= _ALPHA_SURFACE_CACHE_LIMIT:
        _alpha_surface_cache.clear()
    surface = factory()
    _alpha_surface_cache[key] = surface
    return surface


def draw_circle_alpha(
    surface: pygame.Surface,
    center: tuple[float, float],
    radius: float,
    color: tuple[int, int, int],
    alpha: int,
    width: int = 0,
) -> None:
    radius_i = max(1, int(radius))
    size = radius_i * 2 + 6
    alpha = max(0, min(255, int(alpha)))
    width = max(0, int(width))

    def build() -> pygame.Surface:
        temp = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(temp, (*color, alpha), (size // 2, size // 2), radius_i, width)
        return temp

    temp = _cached_alpha_surface(("circle", radius_i, color, alpha, width), build)
    surface.blit(temp, (center[0] - size // 2, center[1] - size // 2))


def draw_line_alpha(
    surface: pygame.Surface,
    start: tuple[float, float],
    end: tuple[float, float],
    color: tuple[int, int, int],
    alpha: int,
    width: int = 1,
) -> None:
    left = min(start[0], end[0]) - width - 2
    top = min(start[1], end[1]) - width - 2
    right = max(start[0], end[0]) + width + 2
    bottom = max(start[1], end[1]) + width + 2
    temp = pygame.Surface((max(1, int(right - left)), max(1, int(bottom - top))), pygame.SRCALPHA)
    s = (start[0] - left, start[1] - top)
    e = (end[0] - left, end[1] - top)
    pygame.draw.line(temp, (*color, max(0, min(255, alpha))), s, e, width)
    surface.blit(temp, (left, top))


def draw_rect_alpha(
    surface: pygame.Surface,
    rect: pygame.Rect,
    color: tuple[int, int, int],
    alpha: int,
    width: int = 0,
) -> None:
    if rect.width <= 0 or rect.height <= 0:
        return
    alpha = max(0, min(255, int(alpha)))
    width = max(0, int(width))

    def build() -> pygame.Surface:
        temp = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(temp, (*color, alpha), temp.get_rect(), width)
        return temp

    temp = _cached_alpha_surface(("rect", rect.width, rect.height, color, alpha, width), build)
    surface.blit(temp, rect.topleft)


def draw_ellipse_alpha(
    surface: pygame.Surface,
    rect: pygame.Rect,
    color: tuple[int, int, int],
    alpha: int,
    width: int = 0,
) -> None:
    if rect.width <= 0 or rect.height <= 0:
        return
    alpha = max(0, min(255, int(alpha)))
    width = max(0, int(width))

    def build() -> pygame.Surface:
        temp = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.ellipse(temp, (*color, alpha), temp.get_rect(), width)
        return temp

    temp = _cached_alpha_surface(("ellipse", rect.width, rect.height, color, alpha, width), build)
    surface.blit(temp, rect.topleft)


def draw_soft_rect_alpha(
    surface: pygame.Surface,
    rect: pygame.Rect,
    color: tuple[int, int, int],
    alpha: int,
    feather: int = 6,
) -> None:
    feather = max(0, int(feather))
    alpha = max(0, min(255, int(alpha)))
    if rect.width <= 0 or rect.height <= 0:
        return
    if feather <= 0:
        draw_rect_alpha(surface, rect, color, alpha)
        return

    temp_rect = rect.inflate(feather * 2, feather * 2)

    def build() -> pygame.Surface:
        temp = pygame.Surface(temp_rect.size, pygame.SRCALPHA)
        local = pygame.Rect(feather, feather, rect.width, rect.height)
        for step in range(feather, 0, -1):
            ratio = 1.0 - (step - 1) / feather
            edge_alpha = int(alpha * 0.42 * ratio * ratio)
            pygame.draw.rect(temp, (*color, edge_alpha), local.inflate(step * 2, step * 2))
        pygame.draw.rect(temp, (*color, alpha), local)
        return temp

    temp = _cached_alpha_surface(("soft_rect", rect.width, rect.height, color, alpha, feather), build)
    surface.blit(temp, temp_rect.topleft)
