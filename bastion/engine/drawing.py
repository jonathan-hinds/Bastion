from __future__ import annotations

import pygame


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
    temp = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(temp, (*color, max(0, min(255, alpha))), (size // 2, size // 2), radius_i, width)
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
    temp = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(temp, (*color, max(0, min(255, alpha))), temp.get_rect(), width)
    surface.blit(temp, rect.topleft)
