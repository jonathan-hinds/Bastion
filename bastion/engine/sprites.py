from __future__ import annotations

import math
import sys
from pathlib import Path

import pygame

from bastion.engine.drawing import draw_circle_alpha


FRAME_SIZE = 32
FRAMES_PER_ROW = 3
ENEMY_SPRITE_ROWS = 5
BOSS_FRAME_SIZE = 64
ATTACK_FRAMES_PER_ROW = 3
ATTACK_SPRITE_ROWS = 3
ROW_NORTH = 0
ROW_NORTH_EAST = 1
ROW_EAST = 2
ROW_SOUTH_EAST = 3
ROW_SOUTH = 4
ROW_IDLE = 5
ATTACK_ROW_NORTH = 0
ATTACK_ROW_EAST = 1
ATTACK_ROW_SOUTH = 2

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
    "boss_lightning": ("stormcaller.png", BOSS_FRAME_SIZE, "boss"),
    "boss_fire": ("fire.png", BOSS_FRAME_SIZE, "boss"),
    "boss_ice": ("frost.png", BOSS_FRAME_SIZE, "boss"),
}

ENEMY_ATTACK_SPRITE_FILES = {
    "boss_lightning": ("stormcaller-attack2.png", BOSS_FRAME_SIZE, "boss"),
    "boss_fire": ("fire-attack.png", BOSS_FRAME_SIZE, "boss"),
    "boss_ice": ("frost-attack2.png", BOSS_FRAME_SIZE, "boss"),
}


def asset_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


SPRITE_DIR = asset_root() / "Sprites" / "Sprites"
ENEMY_SPRITE_DIR = asset_root() / "Sprites" / "Enemies" / "Sprites"
BOSS_SPRITE_DIR = asset_root() / "Sprites" / "Enemies" / "Bosses"
BUILDING_SPRITE_DIR = asset_root() / "Sprites" / "Buildings"
TOWER_SPRITE_DIR = BUILDING_SPRITE_DIR / "Towers"

BUILDING_FRAME_COUNT = 5
BUILDING_FRAME_SIZE = 64
CORE_BUILDING_FRAME_SIZE = 96
BUILDING_FRAME_SECONDS = 0.16
UTILITY_PULSE_SECONDS = 1.05
UTILITY_BOUNCE_SECONDS = 1.85

BUILDING_SPRITE_SLUGS = {
    "barracks": "barracks",
    "enemy_barracks": "barracks",
    "core": "core",
    "enemy_core": "core",
    "expedition_campsite": "expedition-campsite",
    "extractor": "extractor-mineral",
    "enemy_extractor": "extractor-mineral",
    "extractor_gold": "extractor-gold",
    "extractor_mineral": "extractor-mineral",
    "hero_hall": "hero-hall",
    "house": "house",
    "enemy_house": "house",
    "library": "library",
    "research": "research-lab",
    "research_lab": "research-lab",
    "training_grounds": "training-grounds",
    "wall": "wall",
}

TOWER_HEAD_SLUGS = {
    "archer": "archer",
    "cannon": "cannon",
    "wizard": "wizard",
    "torch": "torch",
    "shield_generator": "shield-generator",
    "enemy_tower": "archer",
}


class SpriteFrameSequence:
    def __init__(self, paths: tuple[Path, ...], frame_size: int) -> None:
        self.frame_size = int(frame_size)
        self.frames = tuple(_load_image_frame(path, self.frame_size) for path in paths)
        self.white_frames = tuple(_white_sprite_frame(frame) for frame in self.frames)
        self._scaled_cache: dict[tuple[int, int, bool], pygame.Surface] = {}

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def frame(self, index: int, size: int, white: bool = False) -> pygame.Surface:
        size = max(1, int(size))
        index = int(index) % max(1, self.frame_count)
        cache_key = (index, size, bool(white))
        cached = self._scaled_cache.get(cache_key)
        if cached is not None:
            return cached

        source = self.white_frames[index] if white else self.frames[index]
        if size == self.frame_size:
            scaled = source
        else:
            scaled = pygame.transform.scale(source, (size, size))
        self._scaled_cache[cache_key] = scaled
        return scaled


_building_sequence_cache: dict[tuple[str, str | None], SpriteFrameSequence | None] = {}
_tower_head_sequence_cache: dict[str, SpriteFrameSequence | None] = {}
_tower_base_sequence_cache: SpriteFrameSequence | None | bool = False


def building_sprite_sequence(kind: str, variant: str | None = None) -> SpriteFrameSequence | None:
    cache_key = (kind, variant)
    if cache_key not in _building_sequence_cache:
        slug = _building_slug(kind, variant)
        frame_size = CORE_BUILDING_FRAME_SIZE if slug == "core" else BUILDING_FRAME_SIZE
        _building_sequence_cache[cache_key] = _load_sprite_sequence(BUILDING_SPRITE_DIR, slug, frame_size) if slug else None
    return _building_sequence_cache[cache_key]


def tower_head_sprite_sequence(kind: str) -> SpriteFrameSequence | None:
    if kind not in _tower_head_sequence_cache:
        slug = TOWER_HEAD_SLUGS.get(kind, kind)
        _tower_head_sequence_cache[kind] = _load_sprite_sequence(TOWER_SPRITE_DIR, slug, BUILDING_FRAME_SIZE) if slug else None
    return _tower_head_sequence_cache[kind]


def tower_base_sprite_sequence() -> SpriteFrameSequence | None:
    global _tower_base_sequence_cache
    if _tower_base_sequence_cache is False:
        _tower_base_sequence_cache = _load_sprite_sequence(TOWER_SPRITE_DIR, "base", BUILDING_FRAME_SIZE)
    return _tower_base_sequence_cache if isinstance(_tower_base_sequence_cache, SpriteFrameSequence) else None


def draw_building_sprite(
    surface: pygame.Surface,
    camera,
    viewport: pygame.Rect,
    owner,
    kind: str,
    *,
    variant: str | None = None,
    world_size: float = 40.0,
    scale: float = 1.0,
    white: bool = False,
) -> pygame.Rect | None:
    return draw_building_sprite_at(
        surface,
        camera,
        viewport,
        owner.pos,
        owner,
        kind,
        variant=variant,
        world_size=world_size,
        scale=scale,
        white=white,
    )


def draw_building_sprite_at(
    surface: pygame.Surface,
    camera,
    viewport: pygame.Rect,
    pos: pygame.Vector2 | tuple[float, float],
    phase_owner,
    kind: str,
    *,
    variant: str | None = None,
    world_size: float = 40.0,
    scale: float = 1.0,
    white: bool = False,
) -> pygame.Rect | None:
    sequence = building_sprite_sequence(kind, variant)
    if sequence is None:
        return None

    center = camera.world_to_screen(pos, viewport)
    size = max(1, int(round(world_size * camera.zoom * scale)))
    frame_index = animated_sprite_frame(phase_owner, sequence.frame_count)
    image = sequence.frame(frame_index, size, white)
    rect = image.get_rect(center=(int(round(center.x)), int(round(center.y))))
    if rect.colliderect(viewport):
        surface.blit(image, rect)
    return rect


def draw_tower_sprite(
    surface: pygame.Surface,
    camera,
    viewport: pygame.Rect,
    owner,
    head_kind: str,
    *,
    world_size: float = 40.0,
    scale: float = 1.0,
    target_pos: pygame.Vector2 | tuple[float, float] | None = None,
    recoil: float = 0.0,
    flash: bool = False,
    bounce: bool = False,
    pulse: bool = False,
) -> pygame.Rect | None:
    base_sequence = tower_base_sprite_sequence()
    head_sequence = tower_head_sprite_sequence(head_kind)
    if base_sequence is None or head_sequence is None:
        return None

    center = camera.world_to_screen(owner.pos, viewport)
    size = max(1, int(round(world_size * camera.zoom * scale)))
    frame_index = animated_sprite_frame(owner, base_sequence.frame_count)
    base_image = base_sequence.frame(frame_index, size)
    base_rect = base_image.get_rect(center=(int(round(center.x)), int(round(center.y))))
    if base_rect.colliderect(viewport):
        surface.blit(base_image, base_rect)

    head_frame_index = animated_sprite_frame(owner, head_sequence.frame_count)
    head_image = head_sequence.frame(head_frame_index, size, flash)
    head_center = pygame.Vector2(center)

    if bounce:
        phase = pygame.time.get_ticks() * 0.001 + _owner_phase(owner)
        head_center.y -= size * 0.5
        bounce_y = math.sin(phase * math.tau / UTILITY_BOUNCE_SECONDS) * 2.8 * camera.zoom * scale
        head_center.y += bounce_y
        if pulse:
            pulse_progress = (phase % UTILITY_PULSE_SECONDS) / UTILITY_PULSE_SECONDS
            pulse_radius = (10.0 + pulse_progress * 18.0) * camera.zoom * scale
            pulse_alpha = int(80 * (1.0 - pulse_progress))
            draw_circle_alpha(surface, head_center, pulse_radius, (245, 245, 245), pulse_alpha, max(1, int(camera.zoom)))
        head_rect = head_image.get_rect(center=(int(round(head_center.x)), int(round(head_center.y))))
        if head_rect.colliderect(viewport):
            surface.blit(head_image, head_rect)
        return base_rect.union(head_rect)

    direction = _tower_aim_direction(owner, target_pos)
    if direction.length_squared() > 0:
        direction = direction.normalize()
    angle = -math.degrees(math.atan2(direction.x, -direction.y)) if direction.length_squared() > 0 else 0.0
    if recoil > 0 and direction.length_squared() > 0:
        head_center -= direction * (4.2 * max(0.0, min(1.0, recoil)) * camera.zoom * scale)
    rotated = pygame.transform.rotate(head_image, angle)
    head_rect = rotated.get_rect(center=(int(round(head_center.x)), int(round(head_center.y))))
    if head_rect.colliderect(viewport):
        surface.blit(rotated, head_rect)
    return base_rect.union(head_rect)


def animated_sprite_frame(owner, frame_count: int = BUILDING_FRAME_COUNT, frame_seconds: float = BUILDING_FRAME_SECONDS) -> int:
    frame_count = max(1, int(frame_count))
    frame_seconds = max(0.001, float(frame_seconds))
    seconds = pygame.time.get_ticks() * 0.001 + _owner_phase(owner)
    return int(seconds / frame_seconds) % frame_count


def _building_slug(kind: str, variant: str | None = None) -> str | None:
    if kind in {"extractor", "enemy_extractor"} and variant in {"gold", "mineral"}:
        return f"extractor-{variant}"
    return BUILDING_SPRITE_SLUGS.get(kind)


def _load_sprite_sequence(directory: Path, slug: str, frame_size: int) -> SpriteFrameSequence | None:
    paths = tuple(directory / f"{slug}{index}.png" for index in range(1, BUILDING_FRAME_COUNT + 1))
    if all(path.exists() for path in paths):
        try:
            return SpriteFrameSequence(paths, frame_size)
        except (OSError, pygame.error, ValueError):
            return None

    single_path = directory / f"{slug}.png"
    if single_path.exists():
        try:
            return SpriteFrameSequence((single_path,), frame_size)
        except (OSError, pygame.error, ValueError):
            return None
    return None


def _load_image_frame(path: Path, frame_size: int) -> pygame.Surface:
    image = pygame.image.load(str(path))
    try:
        image = image.convert_alpha()
    except pygame.error:
        image = image.copy()
    if image.get_size() == (frame_size, frame_size):
        return image

    frame = pygame.Surface((frame_size, frame_size), pygame.SRCALPHA)
    rect = image.get_rect(center=frame.get_rect().center)
    frame.blit(image, rect)
    return frame


def _white_sprite_frame(frame: pygame.Surface) -> pygame.Surface:
    white = frame.copy()
    white.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGB_ADD)
    return white


def _owner_phase(owner) -> float:
    if isinstance(owner, tuple) and len(owner) == 2:
        return ((int(owner[0]) * 37 + int(owner[1]) * 19) % 97) / 17.0
    for attr in ("sprite_phase", "pulse", "phase", "sprite_anim_time"):
        value = getattr(owner, attr, None)
        if isinstance(value, (int, float)):
            return float(value)
    cell = getattr(owner, "cell", None)
    if isinstance(cell, tuple) and len(cell) == 2:
        return ((cell[0] * 37 + cell[1] * 19) % 97) / 17.0
    index = getattr(owner, "index", None)
    if isinstance(index, int):
        return index * 0.37
    return 0.0


def _tower_aim_direction(owner, target_pos: pygame.Vector2 | tuple[float, float] | None = None) -> pygame.Vector2:
    if target_pos is not None:
        direction = pygame.Vector2(target_pos) - pygame.Vector2(owner.pos)
        if direction.length_squared() > 0.01:
            setattr(owner, "visual_aim_direction", pygame.Vector2(direction))
            return direction
    stored = getattr(owner, "visual_aim_direction", None)
    if stored is not None:
        direction = pygame.Vector2(stored)
        if direction.length_squared() > 0.01:
            return direction
    return pygame.Vector2(1, 0)


class TroopSpriteSheet:
    def __init__(self, path: Path, frame_size: int = FRAME_SIZE, row_count: int = 6, frames_per_row: int = FRAMES_PER_ROW) -> None:
        self.frame_size = int(frame_size)
        self.row_count = int(row_count)
        self.frames_per_row = int(frames_per_row)
        image = pygame.image.load(str(path))
        try:
            image = image.convert_alpha()
        except pygame.error:
            image = image.copy()

        self.frames: tuple[tuple[pygame.Surface, ...], ...] = tuple(
            tuple(self._copy_frame(image, row, col) for col in range(self.frames_per_row))
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
        col = int(col) % self.frames_per_row
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
_enemy_attack_sprite_cache: dict[str, TroopSpriteSheet | None] = {}


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
            filename = record[0]
            frame_size = record[1]
            directory = BOSS_SPRITE_DIR if len(record) > 2 and record[2] == "boss" else ENEMY_SPRITE_DIR
            path = directory / filename
            if not path.exists():
                _enemy_sprite_cache[kind] = None
            else:
                try:
                    _enemy_sprite_cache[kind] = TroopSpriteSheet(path, frame_size, ENEMY_SPRITE_ROWS)
                except (OSError, pygame.error, ValueError):
                    _enemy_sprite_cache[kind] = None
    return _enemy_sprite_cache[kind]


def enemy_attack_sprite_sheet(kind: str) -> TroopSpriteSheet | None:
    if kind not in _enemy_attack_sprite_cache:
        record = ENEMY_ATTACK_SPRITE_FILES.get(kind)
        if record is None:
            _enemy_attack_sprite_cache[kind] = None
        else:
            filename = record[0]
            frame_size = record[1]
            directory = BOSS_SPRITE_DIR if len(record) > 2 and record[2] == "boss" else ENEMY_SPRITE_DIR
            path = directory / filename
            if not path.exists():
                _enemy_attack_sprite_cache[kind] = None
            else:
                try:
                    _enemy_attack_sprite_cache[kind] = TroopSpriteSheet(path, frame_size, ATTACK_SPRITE_ROWS, ATTACK_FRAMES_PER_ROW)
                except (OSError, pygame.error, ValueError):
                    _enemy_attack_sprite_cache[kind] = None
    return _enemy_attack_sprite_cache[kind]


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


def attack_directional_row(vector: pygame.Vector2) -> tuple[int, bool]:
    if vector.length_squared() == 0:
        return ATTACK_ROW_SOUTH, False

    if abs(vector.x) > abs(vector.y):
        return ATTACK_ROW_EAST, vector.x < 0
    if vector.y < 0:
        return ATTACK_ROW_NORTH, False
    return ATTACK_ROW_SOUTH, False
