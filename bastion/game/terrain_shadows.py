from __future__ import annotations

from dataclasses import dataclass
import math

from bastion import config


CARDINAL_OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAGONAL_OFFSETS = ((-1, -1), (1, -1), (-1, 1), (1, 1))
FRONT_EXPOSURE_OFFSETS = ((-1, 1), (1, 1))


@dataclass(frozen=True, slots=True)
class TerrainShadowSettings:
    enabled: bool = config.TERRAIN_SHADOWS_ENABLED
    max_opacity: float = config.TERRAIN_SHADOW_MAX_OPACITY
    elevation_step_opacity: float = config.TERRAIN_SHADOW_ELEVATION_STEP_OPACITY
    cardinal_higher_opacity: float = config.TERRAIN_SHADOW_CARDINAL_HIGHER_OPACITY
    diagonal_higher_opacity: float = config.TERRAIN_SHADOW_DIAGONAL_HIGHER_OPACITY
    front_exposure_opacity: float = config.TERRAIN_SHADOW_FRONT_EXPOSURE_OPACITY
    depth_opacity: float = config.TERRAIN_SHADOW_DEPTH_OPACITY
    bands: int = config.TERRAIN_SHADOW_BANDS


class TerrainShadowCalculator:
    def __init__(self, settings: TerrainShadowSettings | None = None) -> None:
        self.settings = settings or TerrainShadowSettings()

    def reference_elevation(self, terrain) -> int:
        max_elevation = getattr(terrain, "max_elevation", None)
        if callable(max_elevation):
            return int(max_elevation())
        return max(
            (terrain.elevation_at((x, y)) for x in range(terrain.width) for y in range(terrain.height)),
            default=0,
        )

    def opacity_for(
        self,
        terrain,
        cell: tuple[int, int],
        *,
        reference_elevation: int | None = None,
    ) -> float:
        if not self.settings.enabled or not terrain.in_bounds(cell):
            return 0.0

        _, y = cell
        elevation = terrain.elevation_at(cell)
        reference = self.reference_elevation(terrain) if reference_elevation is None else int(reference_elevation)
        shadow = 0.0
        shadow += self._depth_opacity(y, terrain.height)
        shadow += max(0, reference - elevation) * self.settings.elevation_step_opacity
        shadow += self._higher_neighbor_opacity(terrain, cell, elevation, CARDINAL_OFFSETS, self.settings.cardinal_higher_opacity)
        shadow += self._higher_neighbor_opacity(terrain, cell, elevation, DIAGONAL_OFFSETS, self.settings.diagonal_higher_opacity)
        shadow -= self._front_exposure_opacity(terrain, cell, elevation)
        shadow = max(0.0, min(1.0, shadow))
        shadow = self._quantize(shadow)
        return max(0.0, min(1.0, shadow * self.settings.max_opacity))

    def _depth_opacity(self, y: int, height: int) -> float:
        if height <= 1 or self.settings.depth_opacity <= 0:
            return 0.0
        back_depth = 1.0 - max(0.0, min(1.0, y / float(height - 1)))
        return back_depth * self.settings.depth_opacity

    def _higher_neighbor_opacity(
        self,
        terrain,
        cell: tuple[int, int],
        elevation: int,
        offsets: tuple[tuple[int, int], ...],
        weight: float,
    ) -> float:
        if weight <= 0:
            return 0.0
        x, y = cell
        return sum(
            max(0, terrain.elevation_at((x + dx, y + dy)) - elevation) * weight
            for dx, dy in offsets
            if terrain.in_bounds((x + dx, y + dy))
        )

    def _front_exposure_opacity(self, terrain, cell: tuple[int, int], elevation: int) -> float:
        if self.settings.front_exposure_opacity <= 0:
            return 0.0
        x, y = cell
        return sum(
            max(0, elevation - terrain.elevation_at((x + dx, y + dy))) * self.settings.front_exposure_opacity
            for dx, dy in FRONT_EXPOSURE_OFFSETS
            if terrain.in_bounds((x + dx, y + dy))
        )

    def _quantize(self, opacity: float) -> float:
        bands = int(self.settings.bands)
        if bands <= 1:
            return opacity
        return math.floor(opacity * bands + 0.5) / bands
