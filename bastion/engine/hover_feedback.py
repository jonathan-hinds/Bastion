from __future__ import annotations

import math

import pygame


BASE_HOVER_SCALE = 1.035
PULSE_SCALE = 0.055
PULSE_DURATION_MS = 180

_current_target: object | None = None
_pulse_started_ms = 0


def set_hover_target(target: object | None) -> None:
    global _current_target, _pulse_started_ms
    if target == _current_target:
        return
    _current_target = target
    if target is not None:
        _pulse_started_ms = pygame.time.get_ticks()


def hover_scale(active: bool) -> float:
    if not active:
        return 1.0
    elapsed = max(0, pygame.time.get_ticks() - _pulse_started_ms)
    pulse = 0.0
    if elapsed < PULSE_DURATION_MS:
        progress = elapsed / PULSE_DURATION_MS
        pulse = math.sin(progress * math.pi) * PULSE_SCALE
    return BASE_HOVER_SCALE + pulse


def scaled_rect(rect: pygame.Rect, active: bool) -> pygame.Rect:
    if not active:
        return rect
    scale = hover_scale(True)
    scaled = pygame.Rect(0, 0, max(1, round(rect.width * scale)), max(1, round(rect.height * scale)))
    scaled.center = rect.center
    return scaled


def inverted_pair(active: bool) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if active:
        return (245, 245, 245), (0, 0, 0)
    return (0, 0, 0), (245, 245, 245)
