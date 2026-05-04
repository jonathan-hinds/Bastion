from __future__ import annotations

import math
import sys
from pathlib import Path

import pygame


FRAME_SIZE = 32
FRAMES_PER_ROW = 3
ENEMY_SPRITE_ROWS = 5
ROW_NORTH = 0
ROW_NORTH_EAST = 1
ROW_EAST = 2
ROW_SOUTH_EAST = 3
ROW_SOUTH = 4
ROW_IDLE = 5

TROOP_SPRITE_FILES = {
    "grunt": "grunt-sprite.png",
    "warrior": "knight-sprite.png",
    "archer": "archer-sprite.png",
    "cleric": "cleric-sprite.png",
    "engineer": "engineer-sprite.png",
    "wizard": "wizard-sprite.png",
    "rune_mage": "rune-mage-sprite.png",
}

ENEMY_SPRITE_FILES = {
    "small": ("Small-Raider.png", 32),
    "medium": ("Medium-Revenant.png", 32),
    "ranged": ("Ranged-Hexer.png", 32),
    "large": ("Large-Husk.png", 48),
}


def asset_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


SPRITE_DIR = asset_root() / "Sprites" / "Sprites"
ENEMY_SPRITE_DIR = asset_root() / "Sprites" / "Enemies" / "Sprites"


class TroopSpriteSheet:
    def __init__(self, path: Path, frame_size: int = FRAME_SIZE, row_count: int = 6) -> None:
        self.frame_size = int(frame_size)
        self.row_count = int(row_count)
        image = pygame.image.load(str(path))
        try:
            image = image.convert_alpha()
        except pygame.error:
            image = image.copy()

        self.frames: tuple[tuple[pygame.Surface, ...], ...] = tuple(
            tuple(self._copy_frame(image, row, col) for col in range(FRAMES_PER_ROW))
            for row in range(self.row_count)
        )
        self.flipped_frames: tuple[tuple[pygame.Surface, ...], ...] = tuple(
            tuple(pygame.transform.flip(frame, True, False) for frame in row_frames)
            for row_frames in self.frames
        )
        self.inverted_frames: tuple[tuple[pygame.Surface, ...], ...] = tuple(
            tuple(_inverted_frame(frame) for frame in row_frames)
            for row_frames in self.frames
        )
        self.flipped_inverted_frames: tuple[tuple[pygame.Surface, ...], ...] = tuple(
            tuple(pygame.transform.flip(frame, True, False) for frame in row_frames)
            for row_frames in self.inverted_frames
        )
        self._scaled_cache: dict[tuple[int, int, bool, bool, int], pygame.Surface] = {}

    def _copy_frame(self, image: pygame.Surface, row: int, col: int) -> pygame.Surface:
        rect = pygame.Rect(col * self.frame_size, row * self.frame_size, self.frame_size, self.frame_size)
        frame = pygame.Surface((self.frame_size, self.frame_size), pygame.SRCALPHA)
        frame.blit(image, (0, 0), rect)
        return frame

    def frame(self, row: int, col: int, flip_x: bool, size: int, inverted: bool = False) -> pygame.Surface:
        row = max(0, min(self.row_count - 1, int(row)))
        col = int(col) % FRAMES_PER_ROW
        size = max(1, int(size))
        cache_key = (row, col, bool(flip_x), bool(inverted), size)
        cached = self._scaled_cache.get(cache_key)
        if cached is not None:
            return cached

        if inverted:
            source_frames = self.flipped_inverted_frames if flip_x else self.inverted_frames
        else:
            source_frames = self.flipped_frames if flip_x else self.frames
        source = source_frames[row][col]
        if size == self.frame_size:
            scaled = source
        else:
            scaled = pygame.transform.scale(source, (size, size))
        self._scaled_cache[cache_key] = scaled
        return scaled


def _inverted_frame(frame: pygame.Surface) -> pygame.Surface:
    inverted = pygame.Surface(frame.get_size(), pygame.SRCALPHA)
    width, height = frame.get_size()
    for y in range(height):
        for x in range(width):
            r, g, b, a = frame.get_at((x, y))
            if a:
                inverted.set_at((x, y), (255 - r, 255 - g, 255 - b, a))
    return inverted


_troop_sprite_cache: dict[str, TroopSpriteSheet | None] = {}
_enemy_sprite_cache: dict[str, TroopSpriteSheet | None] = {}


def troop_sprite_sheet(kind: str) -> TroopSpriteSheet | None:
    if kind not in _troop_sprite_cache:
        filename = TROOP_SPRITE_FILES.get(kind)
        path = SPRITE_DIR / filename if filename else None
        if path is None or not path.exists():
            _troop_sprite_cache[kind] = None
        else:
            try:
                _troop_sprite_cache[kind] = TroopSpriteSheet(path)
            except (OSError, pygame.error, ValueError):
                _troop_sprite_cache[kind] = None
    return _troop_sprite_cache[kind]


def enemy_sprite_sheet(kind: str) -> TroopSpriteSheet | None:
    if kind not in _enemy_sprite_cache:
        record = ENEMY_SPRITE_FILES.get(kind)
        if record is None:
            _enemy_sprite_cache[kind] = None
        else:
            filename, frame_size = record
            path = ENEMY_SPRITE_DIR / filename
            if not path.exists():
                _enemy_sprite_cache[kind] = None
            else:
                try:
                    _enemy_sprite_cache[kind] = TroopSpriteSheet(path, frame_size, ENEMY_SPRITE_ROWS)
                except (OSError, pygame.error, ValueError):
                    _enemy_sprite_cache[kind] = None
    return _enemy_sprite_cache[kind]


def directional_row(vector: pygame.Vector2) -> tuple[int, bool]:
    if vector.length_squared() == 0:
        return ROW_IDLE, False

    angle = math.degrees(math.atan2(-vector.y, vector.x))
    if -22.5 <= angle < 22.5:
        return ROW_EAST, False
    if 22.5 <= angle < 67.5:
        return ROW_NORTH_EAST, False
    if 67.5 <= angle < 112.5:
        return ROW_NORTH, False
    if 112.5 <= angle < 157.5:
        return ROW_NORTH_EAST, True
    if angle >= 157.5 or angle < -157.5:
        return ROW_EAST, True
    if -157.5 <= angle < -112.5:
        return ROW_SOUTH_EAST, True
    if -112.5 <= angle < -67.5:
        return ROW_SOUTH, False
    return ROW_SOUTH_EAST, False
