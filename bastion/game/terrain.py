from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import random

from bastion import config
from bastion.terrain_tiles import (
    MAIN_CENTER,
    SINGLE_PATH_END_SOUTH_TILE,
    STAIR_SOUTH_TILE,
    TERRAIN_TILE_BY_NAME,
    TerrainTileDefinition,
)


STAIR_SOUTH = "stair_south"

CARDINAL_DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))
StairRun = tuple[tuple[int, int], ...]


class TerrainRules:
    def __init__(self, tiles: dict[str, TerrainTileDefinition]) -> None:
        self.tiles = dict(tiles)

    @classmethod
    def load_default(cls) -> "TerrainRules":
        return cls(TERRAIN_TILE_BY_NAME)

    @classmethod
    def fallback(cls) -> "TerrainRules":
        return cls(TERRAIN_TILE_BY_NAME)

    def has_tile(self, name: str) -> bool:
        return name in self.tiles

    def tile_name(self, name: str) -> str:
        return name if self.has_tile(name) else MAIN_CENTER


@dataclass(slots=True)
class TerrainCell:
    elevation: int = 0
    tile_name: str = MAIN_CENTER
    layer_tile_names: tuple[str, ...] = ()
    feature: str | None = None
    feature_tile_name: str | None = None
    cliff_tile_name: str | None = None
    walkable: bool = True
    buildable: bool = True


class TerrainMap:
    def __init__(
        self,
        width: int,
        height: int,
        cells: list[list[TerrainCell]],
        *,
        seed: int | None = None,
        rules: TerrainRules | None = None,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.cells = cells
        self.seed = seed
        self.rules = rules or TerrainRules.load_default()
        self._max_elevation = self._calculate_max_elevation()
        self._cardinal_links: list[list[tuple[tuple[int, int], ...]]] = []
        self._diagonal_links: list[list[tuple[tuple[int, int], ...]]] = []
        self._outer_void_by_level: dict[int, set[tuple[int, int]]] = {}

    @classmethod
    def flat(cls, width: int, height: int, *, elevation: int = 0) -> "TerrainMap":
        cells = [[TerrainCell(elevation=elevation) for _ in range(height)] for _ in range(width)]
        terrain = cls(width, height, cells)
        terrain.reclassify()
        return terrain

    @classmethod
    def from_elevations(
        cls,
        elevations: list[list[int]],
        features: dict[tuple[int, int], str] | None = None,
        *,
        seed: int | None = None,
        rules: TerrainRules | None = None,
    ) -> "TerrainMap":
        width = len(elevations)
        height = len(elevations[0]) if width else 0
        features = cls._normalized_features(elevations, features or {}, config.TERRAIN_STAIR_WIDTH)
        cells = [
            [
                TerrainCell(elevation=max(0, int(elevations[x][y])), feature=features.get((x, y)))
                for y in range(height)
            ]
            for x in range(width)
        ]
        terrain = cls(width, height, cells, seed=seed, rules=rules)
        terrain.reclassify()
        return terrain

    @staticmethod
    def _normalized_features(
        elevations: list[list[int]],
        features: dict[tuple[int, int], str],
        stair_width: int,
    ) -> dict[tuple[int, int], str]:
        normalized = {cell: feature for cell, feature in features.items() if feature != STAIR_SOUTH}
        assigned_stairs: set[tuple[int, int]] = set()
        width = max(1, int(stair_width))
        for cell, feature in sorted(features.items()):
            if feature != STAIR_SOUTH or cell in assigned_stairs:
                continue
            stair_run = TerrainMap._stair_run_containing_cell(elevations, cell, width)
            if stair_run is None:
                continue
            for stair_cell in stair_run:
                normalized[stair_cell] = STAIR_SOUTH
                assigned_stairs.add(stair_cell)
        return normalized

    @staticmethod
    def _stair_run_containing_cell(
        elevations: list[list[int]],
        cell: tuple[int, int],
        stair_width: int,
    ) -> StairRun | None:
        x, y = cell
        width = max(1, int(stair_width))
        for offset in range(width):
            stair_run = TerrainMap._stair_run_cells((x - offset, y), width)
            if TerrainMap._valid_south_stair_run(elevations, stair_run):
                return stair_run
        return None

    @staticmethod
    def _stair_run_cells(anchor: tuple[int, int], stair_width: int) -> StairRun:
        x, y = anchor
        return tuple((x + offset, y) for offset in range(max(1, int(stair_width))))

    @staticmethod
    def _valid_south_stair_run(elevations: list[list[int]], stair_run: StairRun) -> bool:
        width = len(elevations)
        height = len(elevations[0]) if width else 0
        lower_level: int | None = None
        for x, y in stair_run:
            if not (0 <= x < width and 0 < y < height):
                return False
            lower = int(elevations[x][y])
            upper = int(elevations[x][y - 1])
            if upper - lower != 1:
                return False
            if lower_level is None:
                lower_level = lower
            elif lower != lower_level:
                return False
        return bool(stair_run)

    def in_bounds(self, cell: tuple[int, int]) -> bool:
        x, y = cell
        return 0 <= x < self.width and 0 <= y < self.height

    def cell(self, cell: tuple[int, int]) -> TerrainCell:
        x, y = cell
        return self.cells[x][y]

    def elevation_at(self, cell: tuple[int, int]) -> int:
        if not self.in_bounds(cell):
            return -1
        return self.cell(cell).elevation

    def max_elevation(self) -> int:
        return self._max_elevation

    def is_walkable(self, cell: tuple[int, int]) -> bool:
        return self.in_bounds(cell) and self.cell(cell).walkable

    def is_buildable(self, cell: tuple[int, int]) -> bool:
        return self.in_bounds(cell) and self.cell(cell).buildable

    def can_traverse(self, start: tuple[int, int], goal: tuple[int, int]) -> bool:
        sx, sy = start
        gx, gy = goal
        dx = gx - sx
        dy = gy - sy
        if abs(dx) > 1 or abs(dy) > 1 or (dx == 0 and dy == 0):
            return False
        if dx != 0 and dy != 0:
            if not self.in_bounds(start) or not self.in_bounds(goal) or self.elevation_at(start) != self.elevation_at(goal):
                return False
            return bool(self._diagonal_links) and goal in self._diagonal_links[sx][sy]
        if not self.in_bounds(start) or not self.in_bounds(goal):
            return False
        if self._cardinal_links:
            return goal in self._cardinal_links[sx][sy]
        return self._can_traverse_cardinal_uncached(start, goal)

    def movement_cost(self, start: tuple[int, int], goal: tuple[int, int]) -> float:
        if not self.can_traverse(start, goal):
            return math.inf
        sx, sy = start
        gx, gy = goal
        base = math.sqrt(2) if sx != gx and sy != gy else 1.0
        return base + abs(self.elevation_at(start) - self.elevation_at(goal)) * 0.35

    def cardinal_neighbors(self, cell: tuple[int, int]) -> list[tuple[int, int]]:
        if not self.in_bounds(cell):
            return []
        x, y = cell
        if self._cardinal_links:
            return list(self._cardinal_links[x][y])
        return [
            neighbor
            for dx, dy in CARDINAL_DIRECTIONS
            if self._can_traverse_cardinal_uncached(cell, (neighbor := (x + dx, y + dy)))
        ]

    def linked_cardinal_neighbors(self, cell: tuple[int, int]) -> tuple[tuple[int, int], ...]:
        if not self.in_bounds(cell) or not self._cardinal_links:
            return tuple(self.cardinal_neighbors(cell))
        x, y = cell
        return self._cardinal_links[x][y]

    def linked_diagonal_neighbors(self, cell: tuple[int, int]) -> tuple[tuple[int, int], ...]:
        if not self.in_bounds(cell) or not self._diagonal_links:
            return tuple()
        x, y = cell
        return self._diagonal_links[x][y]

    def reclassify(self) -> None:
        self._max_elevation = self._calculate_max_elevation()
        self._outer_void_by_level = self._build_outer_void_by_level()
        for x in range(self.width):
            for y in range(self.height):
                cell = self.cells[x][y]
                cell.walkable = True
                cell.feature_tile_name = STAIR_SOUTH_TILE if cell.feature == STAIR_SOUTH else None
                cell.layer_tile_names = self._base_layer_tile_names((x, y))
                cell.tile_name = cell.layer_tile_names[-1] if cell.layer_tile_names else MAIN_CENTER
                cell.cliff_tile_name = self._cliff_tile_name((x, y))
                cell.buildable = self._flat_buildable((x, y))
        self._rebuild_navigation_links()

    def _calculate_max_elevation(self) -> int:
        return max((self.cells[x][y].elevation for x in range(self.width) for y in range(self.height)), default=0)

    def _rebuild_navigation_links(self) -> None:
        links: list[list[tuple[tuple[int, int], ...]]] = [[tuple() for _ in range(self.height)] for _ in range(self.width)]
        for x in range(self.width):
            for y in range(self.height):
                cell = (x, y)
                links[x][y] = tuple(
                    neighbor
                    for dx, dy in CARDINAL_DIRECTIONS
                    if self._can_traverse_cardinal_uncached(cell, (neighbor := (x + dx, y + dy)))
                )
        self._cardinal_links = links
        diagonal_links: list[list[tuple[tuple[int, int], ...]]] = [[tuple() for _ in range(self.height)] for _ in range(self.width)]
        for x in range(self.width):
            for y in range(self.height):
                elevation = self.elevation_at((x, y))
                neighbors: list[tuple[int, int]] = []
                for dx in (-1, 1):
                    for dy in (-1, 1):
                        diagonal = (x + dx, y + dy)
                        if not self.is_walkable(diagonal) or self.elevation_at(diagonal) != elevation:
                            continue
                        if (x + dx, y) in links[x][y] and (x, y + dy) in links[x][y]:
                            neighbors.append(diagonal)
                diagonal_links[x][y] = tuple(neighbors)
        self._diagonal_links = diagonal_links

    def _can_traverse_cardinal_uncached(self, start: tuple[int, int], goal: tuple[int, int]) -> bool:
        if not self.is_walkable(start) or not self.is_walkable(goal):
            return False
        sx, sy = start
        gx, gy = goal
        dx = gx - sx
        dy = gy - sy
        if abs(dx) + abs(dy) != 1:
            return False

        start_elevation = self.elevation_at(start)
        goal_elevation = self.elevation_at(goal)
        delta = goal_elevation - start_elevation
        if delta == 0:
            return True
        if abs(delta) > 1 or dx != 0:
            return False

        if delta == 1:
            return dy == -1 and self.cell(start).feature == STAIR_SOUTH
        return dy == 1 and self.cell(goal).feature == STAIR_SOUTH

    def _build_outer_void_by_level(self) -> dict[int, set[tuple[int, int]]]:
        max_level = max((self.cells[x][y].elevation for x in range(self.width) for y in range(self.height)), default=0)
        outer_by_level: dict[int, set[tuple[int, int]]] = {}
        for level in range(1, max_level + 1):
            visited: set[tuple[int, int]] = set()
            queue: deque[tuple[int, int]] = deque()
            for x in range(self.width):
                for y in (0, self.height - 1):
                    if self.elevation_at((x, y)) < level and (x, y) not in visited:
                        visited.add((x, y))
                        queue.append((x, y))
            for y in range(1, self.height - 1):
                for x in (0, self.width - 1):
                    if self.elevation_at((x, y)) < level and (x, y) not in visited:
                        visited.add((x, y))
                        queue.append((x, y))
            while queue:
                x, y = queue.popleft()
                for dx, dy in CARDINAL_DIRECTIONS:
                    neighbor = (x + dx, y + dy)
                    if neighbor in visited or not self.in_bounds(neighbor) or self.elevation_at(neighbor) >= level:
                        continue
                    visited.add(neighbor)
                    queue.append(neighbor)
            outer_by_level[level] = visited
        return outer_by_level

    def _void_kind(self, cell: tuple[int, int], level: int) -> str | None:
        if self.elevation_at(cell) >= level:
            return None
        if not self.in_bounds(cell) or cell in self._outer_void_by_level.get(level, set()):
            return "outer"
        return "inner"

    def _base_layer_tile_names(self, cell: tuple[int, int]) -> tuple[str, ...]:
        elevation = max(0, self.elevation_at(cell))
        return tuple(self._base_tile_name_at_level(cell, level) for level in range(elevation + 1))

    def _base_tile_name(self, cell: tuple[int, int]) -> str:
        return self._base_tile_name_at_level(cell, self.elevation_at(cell))

    def _base_tile_name_at_level(self, cell: tuple[int, int], level: int) -> str:
        x, y = cell
        n = self._void_kind((x, y - 1), level)
        s = self._void_kind((x, y + 1), level)
        w = self._void_kind((x - 1, y), level)
        e = self._void_kind((x + 1, y), level)
        nw = self._void_kind((x - 1, y - 1), level)
        ne = self._void_kind((x + 1, y - 1), level)
        sw = self._void_kind((x - 1, y + 1), level)
        se = self._void_kind((x + 1, y + 1), level)
        open_count = sum(kind is not None for kind in (n, s, w, e))

        if open_count == 0:
            if se == "inner":
                return self.rules.tile_name("inner_cutout_top_left_surround")
            if sw == "inner":
                return self.rules.tile_name("inner_cutout_top_right_surround")
            if ne == "inner":
                return self.rules.tile_name("inner_cutout_bottom_left_surround")
            if nw == "inner":
                return self.rules.tile_name("inner_cutout_bottom_right_surround")
            return MAIN_CENTER

        if open_count == 1:
            if n is not None:
                return self.rules.tile_name("inner_cutout_bottom_edge" if n == "inner" else "main_top_edge")
            if s is not None:
                return self.rules.tile_name("inner_cutout_top_edge" if s == "inner" else "main_bottom_edge")
            if w is not None:
                return self.rules.tile_name("inner_cutout_right_edge" if w == "inner" else "main_left_edge")
            return self.rules.tile_name("inner_cutout_left_edge" if e == "inner" else "main_right_edge")

        if open_count == 2:
            if n is not None and w is not None:
                if n == "inner" and w == "inner":
                    return self.rules.tile_name("inner_cutout_bottom_right_surround")
                if se == "inner":
                    return self.rules.tile_name("mixed_loop_top_left")
                return self.rules.tile_name("main_outer_top_left_corner")
            if n is not None and e is not None:
                if n == "inner" and e == "inner":
                    return self.rules.tile_name("inner_cutout_bottom_left_surround")
                if sw == "inner":
                    return self.rules.tile_name("mixed_loop_top_right")
                return self.rules.tile_name("main_outer_top_right_corner")
            if s is not None and w is not None:
                if s == "inner" and w == "inner":
                    return self.rules.tile_name("inner_cutout_top_right_surround")
                if ne == "inner":
                    return self.rules.tile_name("mixed_loop_bottom_left")
                return self.rules.tile_name("main_outer_bottom_left_corner")
            if s is not None and e is not None:
                if s == "inner" and e == "inner":
                    return self.rules.tile_name("inner_cutout_top_left_surround")
                if nw == "inner":
                    return self.rules.tile_name("mixed_loop_bottom_right")
                return self.rules.tile_name("main_outer_bottom_right_corner")
            if n is not None and s is not None:
                if n == "outer" and s == "inner":
                    return self.rules.tile_name("mixed_loop_top")
                if s == "outer" and n == "inner":
                    return self.rules.tile_name("mixed_loop_bottom")
                return self.rules.tile_name("mixed_loop_top" if n == "outer" else "mixed_loop_bottom")
            if w is not None and e is not None:
                if w == "outer" and e == "inner":
                    return self.rules.tile_name("mixed_loop_left")
                if e == "outer" and w == "inner":
                    return self.rules.tile_name("mixed_loop_right")
                return self.rules.tile_name("mixed_loop_left" if w == "outer" else "mixed_loop_right")

        if open_count == 3:
            if n is None:
                return self.rules.tile_name(SINGLE_PATH_END_SOUTH_TILE)
            if s is None:
                return self.rules.tile_name("single_path_end_north")
            if w is None:
                return self.rules.tile_name("single_path_end_east")
            return self.rules.tile_name("single_path_end_west")

        return self.rules.tile_name("single_platform_dot")

    def _cliff_tile_name(self, cell: tuple[int, int]) -> str | None:
        x, y = cell
        if self.cell(cell).feature == STAIR_SOUTH:
            return None
        elevation = self.elevation_at(cell)
        north_elevation = self.elevation_at((x, y - 1))
        if north_elevation <= elevation:
            return None
        level = north_elevation

        left_x = x
        while self._is_cliff_face_cell((left_x - 1, y), level):
            left_x -= 1
        right_x = x
        while self._is_cliff_face_cell((right_x + 1, y), level):
            right_x += 1

        if x == left_x == right_x:
            north_west = self.elevation_at((x - 1, y - 1)) >= level
            north_east = self.elevation_at((x + 1, y - 1)) >= level
            if north_east and not north_west:
                return self.rules.tile_name("cliff_bottom_left")
            if north_west and not north_east:
                return self.rules.tile_name("cliff_bottom_right")
            return self.rules.tile_name("cliff_bottom_center")
        if x == left_x:
            return self.rules.tile_name("cliff_bottom_left")
        if x == right_x:
            return self.rules.tile_name("cliff_bottom_right")
        return self.rules.tile_name("cliff_bottom_center")

    def _is_cliff_face_cell(self, cell: tuple[int, int], level: int) -> bool:
        if not self.in_bounds(cell) or self.cell(cell).feature == STAIR_SOUTH:
            return False
        x, y = cell
        return self.elevation_at((x, y)) < level and self.elevation_at((x, y - 1)) >= level

    def _flat_buildable(self, cell: tuple[int, int]) -> bool:
        if not self.is_walkable(cell) or self.cell(cell).feature is not None:
            return False
        elevation = self.elevation_at(cell)
        x, y = cell
        for dx, dy in CARDINAL_DIRECTIONS:
            if self.elevation_at((x + dx, y + dy)) != elevation:
                return False
        return True


@dataclass(frozen=True, slots=True)
class TerrainGeneratorSettings:
    max_elevation: int = config.TERRAIN_MAX_ELEVATION
    starting_elevation: int = config.TERRAIN_STARTING_ELEVATION
    starting_flat_radius: int = config.TERRAIN_STARTING_FLAT_RADIUS
    low_border: int = config.TERRAIN_LOW_BORDER
    stair_spacing: int = config.TERRAIN_STAIR_SPACING
    stair_width: int = config.TERRAIN_STAIR_WIDTH
    attempts: int = 8


class ProceduralTerrainGenerator:
    def __init__(self, settings: TerrainGeneratorSettings | None = None, rules: TerrainRules | None = None) -> None:
        self.settings = settings or TerrainGeneratorSettings()
        self.rules = rules or TerrainRules.load_default()

    def generate(
        self,
        width: int,
        height: int,
        townhall_cell: tuple[int, int],
        *,
        seed: int | None = None,
    ) -> TerrainMap:
        seed_value = self._seed_from_random_state() if seed is None else int(seed)
        for attempt in range(self.settings.attempts):
            attempt_seed = seed_value + attempt * 9973
            rng = random.Random(attempt_seed)
            elevations = self._elevations(width, height, townhall_cell, rng, attempt_seed)
            self._normalize_elevation_steps(elevations)
            features = self._place_stairs(elevations, townhall_cell)
            terrain = TerrainMap.from_elevations(elevations, features, seed=seed_value, rules=self.rules)
            if self._all_spawns_reachable(terrain, townhall_cell):
                return terrain
        return self._fallback(width, height, townhall_cell, seed_value)

    def _elevations(
        self,
        width: int,
        height: int,
        townhall_cell: tuple[int, int],
        rng: random.Random,
        seed: int,
    ) -> list[list[int]]:
        elevations = [[0 for _ in range(height)] for _ in range(width)]
        self._scatter_mesas(elevations, rng, seed, townhall_cell)
        self._raise_starting_region(elevations, townhall_cell, seed)
        self._smooth(elevations, townhall_cell)
        self._apply_world_constraints(elevations, townhall_cell)
        return elevations

    def _scatter_mesas(
        self,
        elevations: list[list[int]],
        rng: random.Random,
        seed: int,
        townhall_cell: tuple[int, int],
    ) -> None:
        width = len(elevations)
        height = len(elevations[0])
        count = max(12, int(width * height / 1050))
        cx, cy = townhall_cell
        for index in range(count):
            mx = rng.randrange(self.settings.low_border + 6, max(self.settings.low_border + 7, width - self.settings.low_border - 6))
            my = rng.randrange(self.settings.low_border + 6, max(self.settings.low_border + 7, height - self.settings.low_border - 6))
            if math.hypot(mx - cx, my - cy) < self.settings.starting_flat_radius + 7:
                continue
            rx = rng.uniform(4.5, 15.0)
            ry = rng.uniform(4.5, 12.5)
            level = 2 if rng.random() < 0.22 else 1
            for x in range(max(0, int(mx - rx - 2)), min(width, int(mx + rx + 3))):
                for y in range(max(0, int(my - ry - 2)), min(height, int(my + ry + 3))):
                    dx = (x - mx) / rx
                    dy = (y - my) / ry
                    noise = self._hash_noise(seed + index * 31, x, y) * 0.16
                    if dx * dx + dy * dy + noise <= 1.0:
                        elevations[x][y] = max(elevations[x][y], level)

    def _raise_starting_region(self, elevations: list[list[int]], townhall_cell: tuple[int, int], seed: int) -> None:
        width = len(elevations)
        height = len(elevations[0])
        cx, cy = townhall_cell
        flat_radius = self.settings.starting_flat_radius
        terrace_radius = flat_radius + 9
        for x in range(width):
            for y in range(height):
                dx = (x - cx) * 0.92
                dy = (y - cy) * 1.08
                distance = math.hypot(dx, dy)
                noise = self._hash_noise(seed + 401, x // 2, y // 2) * 2.4
                if distance <= flat_radius:
                    elevations[x][y] = self.settings.starting_elevation
                elif distance + noise <= terrace_radius:
                    elevations[x][y] = max(elevations[x][y], self.settings.starting_elevation)
                elif distance + noise <= terrace_radius + 6 and self._hash_noise(seed + 907, x, y) > 0.52:
                    elevations[x][y] = max(elevations[x][y], self.settings.starting_elevation)

    def _smooth(self, elevations: list[list[int]], townhall_cell: tuple[int, int]) -> None:
        width = len(elevations)
        height = len(elevations[0])
        cx, cy = townhall_cell
        protected = self.settings.starting_flat_radius + 1
        for _ in range(2):
            current = [column[:] for column in elevations]
            for x in range(1, width - 1):
                for y in range(1, height - 1):
                    if max(abs(x - cx), abs(y - cy)) <= protected:
                        continue
                    value = current[x][y]
                    neighbors = [
                        current[nx][ny]
                        for nx in range(x - 1, x + 2)
                        for ny in range(y - 1, y + 2)
                        if (nx, ny) != (x, y)
                    ]
                    if value > 0 and sum(1 for item in neighbors if item < value) >= 6:
                        elevations[x][y] = value - 1
                    elif value < self.settings.max_elevation and sum(1 for item in neighbors if item > value) >= 6:
                        elevations[x][y] = value + 1

    def _apply_world_constraints(self, elevations: list[list[int]], townhall_cell: tuple[int, int]) -> None:
        width = len(elevations)
        height = len(elevations[0])
        cx, cy = townhall_cell
        border = self.settings.low_border
        for x in range(width):
            for y in range(height):
                if x < border or y < border or x >= width - border or y >= height - border:
                    elevations[x][y] = 0
                if max(abs(x - cx), abs(y - cy)) <= self.settings.starting_flat_radius:
                    elevations[x][y] = self.settings.starting_elevation

    def _normalize_elevation_steps(self, elevations: list[list[int]]) -> None:
        width = len(elevations)
        height = len(elevations[0])
        for _ in range(self.settings.max_elevation + 1):
            changed = False
            for x in range(width):
                for y in range(height):
                    value = elevations[x][y]
                    if value <= 0:
                        continue
                    lowest_neighbor = min(
                        elevations[nx][ny]
                        for dx, dy in CARDINAL_DIRECTIONS
                        if 0 <= (nx := x + dx) < width and 0 <= (ny := y + dy) < height
                    )
                    if value > lowest_neighbor + 1:
                        elevations[x][y] = lowest_neighbor + 1
                        changed = True
            if not changed:
                break

    def _place_stairs(self, elevations: list[list[int]], townhall_cell: tuple[int, int]) -> dict[tuple[int, int], str]:
        features: dict[tuple[int, int], str] = {}
        max_level = max(max(column) for column in elevations) if elevations else 0
        for level in range(1, max_level + 1):
            for component in self._components_at_or_above(elevations, level):
                candidates = self._stair_candidates(elevations, component, level, townhall_cell)
                if not candidates:
                    carved = self._carve_south_landing(elevations, component, level, townhall_cell)
                    candidates = [carved] if carved is not None else []
                if not candidates:
                    continue
                selected = self._select_stairs(candidates, component, townhall_cell)
                for stair_run in selected:
                    for cell in stair_run:
                        features[cell] = STAIR_SOUTH
        return features

    def _components_at_or_above(self, elevations: list[list[int]], level: int) -> list[set[tuple[int, int]]]:
        width = len(elevations)
        height = len(elevations[0])
        visited: set[tuple[int, int]] = set()
        components: list[set[tuple[int, int]]] = []
        for x in range(width):
            for y in range(height):
                if elevations[x][y] < level or (x, y) in visited:
                    continue
                component: set[tuple[int, int]] = set()
                queue = deque([(x, y)])
                visited.add((x, y))
                while queue:
                    current = queue.popleft()
                    component.add(current)
                    cx, cy = current
                    for dx, dy in CARDINAL_DIRECTIONS:
                        neighbor = (cx + dx, cy + dy)
                        nx, ny = neighbor
                        if not (0 <= nx < width and 0 <= ny < height):
                            continue
                        if neighbor in visited or elevations[nx][ny] < level:
                            continue
                        visited.add(neighbor)
                        queue.append(neighbor)
                components.append(component)
        return components

    def _stair_candidates(
        self,
        elevations: list[list[int]],
        component: set[tuple[int, int]],
        level: int,
        townhall_cell: tuple[int, int],
    ) -> list[StairRun]:
        candidates: set[StairRun] = set()
        stair_width = max(1, int(self.settings.stair_width))
        for x, y in component:
            lower_y = y + 1
            for anchor_x in range(x - stair_width + 1, x + 1):
                stair_run = TerrainMap._stair_run_cells((anchor_x, lower_y), stair_width)
                if self._valid_stair_candidate_run(elevations, stair_run, component, level, townhall_cell):
                    candidates.add(stair_run)
        return sorted(candidates)

    def _carve_south_landing(
        self,
        elevations: list[list[int]],
        component: set[tuple[int, int]],
        level: int,
        townhall_cell: tuple[int, int],
    ) -> StairRun | None:
        cx, cy = townhall_cell
        stair_width = max(1, int(self.settings.stair_width))
        for high in sorted(component, key=lambda item: (-item[1], abs(item[0] - cx))):
            lower_y = high[1] + 1
            stair_runs = [
                TerrainMap._stair_run_cells((anchor_x, lower_y), stair_width)
                for anchor_x in range(high[0] - stair_width + 1, high[0] + 1)
            ]
            stair_runs.sort(key=lambda stair_run: (self._stair_run_distance_to_cell(stair_run, townhall_cell), stair_run))
            for stair_run in stair_runs:
                if not self._carvable_stair_run(elevations, stair_run, component, townhall_cell):
                    continue
                for lx, ly in stair_run:
                    elevations[lx][ly] = level - 1
                return stair_run
        return None

    def _valid_stair_candidate_run(
        self,
        elevations: list[list[int]],
        stair_run: StairRun,
        component: set[tuple[int, int]],
        level: int,
        townhall_cell: tuple[int, int],
    ) -> bool:
        width = len(elevations)
        height = len(elevations[0]) if width else 0
        cx, cy = townhall_cell
        for lx, ly in stair_run:
            if not (0 <= lx < width and 0 <= ly < height):
                return False
            if (lx, ly - 1) not in component:
                return False
            if elevations[lx][ly] != level - 1:
                return False
            if max(abs(lx - cx), abs(ly - cy)) <= self.settings.starting_flat_radius - 1:
                return False
        return True

    def _carvable_stair_run(
        self,
        elevations: list[list[int]],
        stair_run: StairRun,
        component: set[tuple[int, int]],
        townhall_cell: tuple[int, int],
    ) -> bool:
        width = len(elevations)
        height = len(elevations[0]) if width else 0
        cx, cy = townhall_cell
        for lx, ly in stair_run:
            if not (self.settings.low_border <= lx < width - self.settings.low_border):
                return False
            if not (self.settings.low_border <= ly < height - self.settings.low_border):
                return False
            if (lx, ly - 1) not in component:
                return False
            if max(abs(lx - cx), abs(ly - cy)) <= self.settings.starting_flat_radius - 1:
                return False
        return True

    def _select_stairs(
        self,
        candidates: list[StairRun],
        component: set[tuple[int, int]],
        townhall_cell: tuple[int, int],
    ) -> list[StairRun]:
        count = max(1, min(4, len(component) // max(1, self.settings.stair_spacing * self.settings.stair_spacing) + 1))
        selected: list[StairRun] = []
        remaining = list(dict.fromkeys(candidates))
        if not remaining:
            return selected
        first = min(remaining, key=lambda stair_run: self._stair_run_distance_to_cell(stair_run, townhall_cell))
        selected.append(first)
        remaining = self._without_overlapping_stair_runs(remaining, first)
        while remaining and len(selected) < count:
            next_run = max(
                remaining,
                key=lambda stair_run: min(
                    self._stair_run_distance(stair_run, selected_run)
                    for selected_run in selected
                ),
            )
            selected.append(next_run)
            remaining = self._without_overlapping_stair_runs(remaining, next_run)
        return selected

    def _without_overlapping_stair_runs(self, candidates: list[StairRun], selected: StairRun) -> list[StairRun]:
        selected_cells = set(selected)
        return [stair_run for stair_run in candidates if selected_cells.isdisjoint(stair_run)]

    def _stair_run_distance_to_cell(self, stair_run: StairRun, cell: tuple[int, int]) -> float:
        cx, cy = cell
        run_x, run_y = self._stair_run_center(stair_run)
        return abs(run_x - cx) + abs(run_y - cy)

    def _stair_run_distance(self, a: StairRun, b: StairRun) -> float:
        ax, ay = self._stair_run_center(a)
        bx, by = self._stair_run_center(b)
        return abs(ax - bx) + abs(ay - by)

    def _stair_run_center(self, stair_run: StairRun) -> tuple[float, float]:
        return (
            sum(x for x, _y in stair_run) / len(stair_run),
            sum(y for _x, y in stair_run) / len(stair_run),
        )

    def _all_spawns_reachable(self, terrain: TerrainMap, townhall_cell: tuple[int, int]) -> bool:
        if not terrain.is_walkable(townhall_cell):
            return False
        queue = deque([townhall_cell])
        visited = {townhall_cell}
        while queue:
            cell = queue.popleft()
            for neighbor in terrain.cardinal_neighbors(cell):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)
        for x in range(terrain.width):
            if (x, 0) not in visited or (x, terrain.height - 1) not in visited:
                return False
        for y in range(1, terrain.height - 1):
            if (0, y) not in visited or (terrain.width - 1, y) not in visited:
                return False
        return True

    def _fallback(self, width: int, height: int, townhall_cell: tuple[int, int], seed: int) -> TerrainMap:
        elevations = [[0 for _ in range(height)] for _ in range(width)]
        cx, cy = townhall_cell
        radius = self.settings.starting_flat_radius + 5
        for x in range(width):
            for y in range(height):
                if math.hypot(x - cx, y - cy) <= radius:
                    elevations[x][y] = self.settings.starting_elevation
        self._apply_world_constraints(elevations, townhall_cell)
        features = self._place_stairs(elevations, townhall_cell)
        return TerrainMap.from_elevations(elevations, features, seed=seed, rules=self.rules)

    def _hash_noise(self, seed: int, x: int, y: int) -> float:
        n = (int(seed) * 374761393 + int(x) * 668265263 + int(y) * 2147483647) & 0xFFFFFFFF
        n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
        return ((n ^ (n >> 16)) / 0xFFFFFFFF) * 2.0 - 1.0

    def _seed_from_random_state(self) -> int:
        state = random.getstate()[1]
        if not isinstance(state, tuple) or len(state) < 4:
            return 1
        seed = 0x45D9F3B
        for index in (0, 1, 2, len(state) // 2, len(state) - 2):
            seed = (seed * 1103515245 + int(state[index]) + 12345) & 0x7FFFFFFF
        return seed or 1
