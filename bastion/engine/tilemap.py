from __future__ import annotations

import math

import pygame

from bastion import config
from bastion.engine.drawing import draw_rect_alpha
from bastion.engine.sprites import TERRAIN_FRAME_COUNT, TERRAIN_FRAME_SECONDS, terrain_sprite_atlas


class TerrainChunkRenderer:
    def __init__(self, chunk_tiles: int = 16, cache_limit: int = 256) -> None:
        self.chunk_tiles = max(4, int(chunk_tiles))
        self.cache_limit = max(16, int(cache_limit))
        self._cache: dict[tuple[int, int, int, int], pygame.Surface] = {}

    def clear(self) -> None:
        self._cache.clear()

    def draw(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        grid,
        shadow_opacity: list[list[float]] | None,
        frame_offsets: list[list[int]],
        layer_shadow_opacity: list[list[tuple[float, ...]]] | None = None,
    ) -> bool:
        atlas = terrain_sprite_atlas()
        if atlas is None:
            return False

        x0, y0, x1, y1 = camera.visible_tile_bounds(viewport, grid.tile_size, grid.width, grid.height)
        if x1 <= x0 or y1 <= y0:
            return True

        tile_px = max(1, int(round(grid.tile_size * camera.zoom)))
        frame_count = max(1, min(TERRAIN_FRAME_COUNT, atlas.frame_count))
        base_frame = int((pygame.time.get_ticks() * 0.001) / max(0.001, TERRAIN_FRAME_SECONDS)) % frame_count

        chunk_x0 = x0 // self.chunk_tiles
        chunk_y0 = y0 // self.chunk_tiles
        chunk_x1 = (x1 - 1) // self.chunk_tiles
        chunk_y1 = (y1 - 1) // self.chunk_tiles

        for chunk_y in range(chunk_y0, chunk_y1 + 1):
            for chunk_x in range(chunk_x0, chunk_x1 + 1):
                chunk = self._chunk_surface(
                    atlas,
                    grid,
                    shadow_opacity,
                    frame_offsets,
                    chunk_x,
                    chunk_y,
                    base_frame,
                    frame_count,
                    tile_px,
                    layer_shadow_opacity,
                )
                world_x = chunk_x * self.chunk_tiles * grid.tile_size
                world_y = chunk_y * self.chunk_tiles * grid.tile_size
                screen_x = viewport.x + (world_x - camera.offset.x) * camera.zoom
                screen_y = viewport.y + (world_y - camera.offset.y) * camera.zoom
                surface.blit(chunk, (math.floor(screen_x), math.floor(screen_y)))
        return True

    def prewarm(
        self,
        camera,
        viewport: pygame.Rect,
        grid,
        shadow_opacity: list[list[float]] | None,
        frame_offsets: list[list[int]],
        layer_shadow_opacity: list[list[tuple[float, ...]]] | None = None,
    ) -> None:
        atlas = terrain_sprite_atlas()
        if atlas is None:
            return
        x0, y0, x1, y1 = camera.visible_tile_bounds(viewport, grid.tile_size, grid.width, grid.height)
        if x1 <= x0 or y1 <= y0:
            return
        tile_px = max(1, int(round(grid.tile_size * camera.zoom)))
        frame_count = max(1, min(TERRAIN_FRAME_COUNT, atlas.frame_count))
        chunk_x0 = x0 // self.chunk_tiles
        chunk_y0 = y0 // self.chunk_tiles
        chunk_x1 = (x1 - 1) // self.chunk_tiles
        chunk_y1 = (y1 - 1) // self.chunk_tiles
        for base_frame in range(frame_count):
            for chunk_y in range(chunk_y0, chunk_y1 + 1):
                for chunk_x in range(chunk_x0, chunk_x1 + 1):
                    self._chunk_surface(
                        atlas,
                        grid,
                        shadow_opacity,
                        frame_offsets,
                        chunk_x,
                        chunk_y,
                        base_frame,
                        frame_count,
                        tile_px,
                        layer_shadow_opacity,
                    )

    def _chunk_surface(
        self,
        atlas,
        grid,
        shadow_opacity: list[list[float]] | None,
        frame_offsets: list[list[int]],
        chunk_x: int,
        chunk_y: int,
        base_frame: int,
        frame_count: int,
        tile_px: int,
        layer_shadow_opacity: list[list[tuple[float, ...]]] | None = None,
    ) -> pygame.Surface:
        cache_key = (chunk_x, chunk_y, base_frame, tile_px)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        start_x = chunk_x * self.chunk_tiles
        start_y = chunk_y * self.chunk_tiles
        width_tiles = max(0, min(self.chunk_tiles, grid.width - start_x))
        height_tiles = max(0, min(self.chunk_tiles, grid.height - start_y))
        chunk = pygame.Surface((max(1, width_tiles * tile_px), max(1, height_tiles * tile_px)), pygame.SRCALPHA)

        for local_y in range(height_tiles):
            y = start_y + local_y
            for local_x in range(width_tiles):
                x = start_x + local_x
                terrain_cell = grid.terrain.cells[x][y]
                frame_index = (base_frame + frame_offsets[x][y]) % frame_count
                dest = pygame.Rect(local_x * tile_px, local_y * tile_px, tile_px, tile_px)
                layer_tile_names = terrain_cell.layer_tile_names or (terrain_cell.tile_name,)
                layer_opacities = layer_shadow_opacity[x][y] if layer_shadow_opacity is not None else (shadow_opacity[x][y] if shadow_opacity is not None else 0.0,)
                for layer_index, tile_name in enumerate(layer_tile_names):
                    opacity = layer_opacities[layer_index] if layer_index < len(layer_opacities) else (shadow_opacity[x][y] if shadow_opacity is not None else 0.0)
                    layer_alpha = int(round(opacity * 255))
                    image = atlas.shaded_frame(tile_name, frame_index, (tile_px, tile_px), layer_alpha)
                    if image is None:
                        if layer_index == 0:
                            shade = max(18, min(68, 28 + layer_index * 15))
                            pygame.draw.rect(chunk, (shade, shade, shade), dest)
                        if layer_alpha > 0:
                            draw_rect_alpha(chunk, dest, config.PALETTE.black, layer_alpha)
                    else:
                        chunk.blit(image, dest)

        for local_y in range(height_tiles):
            y = start_y + local_y
            for local_x in range(width_tiles):
                x = start_x + local_x
                terrain_cell = grid.terrain.cells[x][y]
                if terrain_cell.cliff_tile_name is None:
                    continue
                frame_index = (base_frame + frame_offsets[x][y]) % frame_count
                image = atlas.frame(terrain_cell.cliff_tile_name, frame_index, (tile_px, tile_px))
                if image is not None:
                    chunk.blit(image, (local_x * tile_px, local_y * tile_px))

        for local_y in range(height_tiles):
            y = start_y + local_y
            for local_x in range(width_tiles):
                x = start_x + local_x
                terrain_cell = grid.terrain.cells[x][y]
                if terrain_cell.feature_tile_name is None:
                    continue
                frame_index = (base_frame + frame_offsets[x][y]) % frame_count
                image = atlas.frame(terrain_cell.feature_tile_name, frame_index, (tile_px, tile_px))
                if image is not None:
                    chunk.blit(image, (local_x * tile_px, local_y * tile_px))

        if len(self._cache) >= self.cache_limit:
            self._cache.clear()
        self._cache[cache_key] = chunk
        return chunk
