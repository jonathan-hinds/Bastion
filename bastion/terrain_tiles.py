from __future__ import annotations

from dataclasses import dataclass


TERRAIN_TILE_SIZE = 32
TERRAIN_TILE_COLUMNS = 3

MAIN_CENTER = "main_center"
SINGLE_PATH_END_SOUTH_TILE = "single_path_end_south"
STAIR_SOUTH_TILE = "stairs_south"


@dataclass(frozen=True, slots=True)
class TerrainTileDefinition:
    name: str
    index: int
    group: str
    rect: tuple[int, int, int, int]
    use_case: str = ""

    @property
    def walkable(self) -> bool:
        return self.group != "unused" and "empty" not in self.name


def _rect(index: int) -> tuple[int, int, int, int]:
    col = index % TERRAIN_TILE_COLUMNS
    row = index // TERRAIN_TILE_COLUMNS
    return col * TERRAIN_TILE_SIZE, row * TERRAIN_TILE_SIZE, TERRAIN_TILE_SIZE, TERRAIN_TILE_SIZE


_TERRAIN_TILE_DATA = (
    (0, "main_outer_top_left_corner", "main_9", "Top-left outside/convex corner of a normal platform."),
    (1, "main_top_edge", "main_9", "Top edge of a normal platform."),
    (2, "main_outer_top_right_corner", "main_9", "Top-right outside/convex corner of a normal platform."),
    (3, "main_left_edge", "main_9", "Left edge of a normal platform."),
    (4, MAIN_CENTER, "main_9", "Fully walkable center/fill tile with no visible edge."),
    (5, "main_right_edge", "main_9", "Right edge of a normal platform."),
    (6, "main_outer_bottom_left_corner", "main_9", "Bottom-left outside/convex corner of a normal platform."),
    (7, "main_bottom_edge", "main_9", "Bottom edge of a normal platform."),
    (8, "main_outer_bottom_right_corner", "main_9", "Bottom-right outside/convex corner of a normal platform."),
    (9, "inner_cutout_top_left_surround", "inner_cutout_9", "Top-left surrounding tile around an interior hole."),
    (10, "inner_cutout_top_edge", "inner_cutout_9", "Tile directly above an interior hole."),
    (11, "inner_cutout_top_right_surround", "inner_cutout_9", "Top-right surrounding tile around an interior hole."),
    (12, "inner_cutout_left_edge", "inner_cutout_9", "Tile directly left of an interior hole."),
    (13, "inner_cutout_empty_center", "inner_cutout_9", "Empty/non-walkable center of an interior cutout."),
    (14, "inner_cutout_right_edge", "inner_cutout_9", "Tile directly right of an interior hole."),
    (15, "inner_cutout_bottom_left_surround", "inner_cutout_9", "Bottom-left surrounding tile around an interior hole."),
    (16, "inner_cutout_bottom_edge", "inner_cutout_9", "Tile directly below an interior hole."),
    (17, "inner_cutout_bottom_right_surround", "inner_cutout_9", "Bottom-right surrounding tile around an interior hole."),
    (18, "mixed_loop_top_left", "mixed_inner_outer_9", "Top-left tile of a 1-tile-wide loop/ring."),
    (19, "mixed_loop_top", "mixed_inner_outer_9", "Top tile of a 1-tile-wide loop/ring."),
    (20, "mixed_loop_top_right", "mixed_inner_outer_9", "Top-right tile of a 1-tile-wide loop/ring."),
    (21, "mixed_loop_left", "mixed_inner_outer_9", "Left tile of a 1-tile-wide loop/ring."),
    (22, "mixed_loop_empty_center", "mixed_inner_outer_9", "Empty/non-walkable center hole inside a loop/ring."),
    (23, "mixed_loop_right", "mixed_inner_outer_9", "Right tile of a 1-tile-wide loop/ring."),
    (24, "mixed_loop_bottom_left", "mixed_inner_outer_9", "Bottom-left tile of a 1-tile-wide loop/ring."),
    (25, "mixed_loop_bottom", "mixed_inner_outer_9", "Bottom tile of a 1-tile-wide loop/ring."),
    (26, "mixed_loop_bottom_right", "mixed_inner_outer_9", "Bottom-right tile of a 1-tile-wide loop/ring."),
    (27, "cliff_bottom_left", "cliff_bottom_3", "Left side of the bottom cliff/wall face."),
    (28, "cliff_bottom_center", "cliff_bottom_3", "Repeatable center piece of the bottom cliff/wall face."),
    (29, "cliff_bottom_right", "cliff_bottom_3", "Right side of the bottom cliff/wall face."),
    (30, "single_platform_dot", "single_paths_5", "Standalone 1-tile platform with outside edges on all sides."),
    (31, SINGLE_PATH_END_SOUTH_TILE, "single_paths_5", "End cap for a 1-tile-wide path heading south/down."),
    (32, "single_path_end_north", "single_paths_5", "End cap for a 1-tile-wide path heading north/up."),
    (33, "single_path_end_east", "single_paths_5", "End cap for a 1-tile-wide path heading east/right."),
    (34, "single_path_end_west", "single_paths_5", "End cap for a 1-tile-wide path heading west/left."),
    (35, STAIR_SOUTH_TILE, "stairs", "South-facing stairs for going up and down elevation levels."),
)

TERRAIN_TILE_DEFINITIONS = tuple(
    TerrainTileDefinition(name=name, index=index, group=group, rect=_rect(index), use_case=use_case)
    for index, name, group, use_case in _TERRAIN_TILE_DATA
)
TERRAIN_TILE_BY_NAME = {tile.name: tile for tile in TERRAIN_TILE_DEFINITIONS}
TERRAIN_TILE_RECTS = {tile.name: tile.rect for tile in TERRAIN_TILE_DEFINITIONS}


def terrain_tile_rects() -> dict[str, tuple[int, int, int, int]]:
    return dict(TERRAIN_TILE_RECTS)
