from __future__ import annotations

from collections import deque
import heapq
import math
import random

import pygame

from bastion import config
from bastion.game.terrain import STAIR_SOUTH, ProceduralTerrainGenerator, TerrainMap


class GameGrid:
    wall_max_health = 90.0
    nav_padding = 4.0

    def __init__(
        self,
        width: int = config.MAP_WIDTH,
        height: int = config.MAP_HEIGHT,
        tile_size: int = config.TILE_SIZE,
        *,
        terrain: TerrainMap | None = None,
        terrain_seed: int | None = None,
        procedural_terrain: bool = True,
    ) -> None:
        self.width = width
        self.height = height
        self.tile_size = tile_size
        self.walls: set[tuple[int, int]] = set()
        self.wall_health: dict[tuple[int, int], float] = {}
        self.towers: dict[tuple[int, int], object] = {}
        self.townhall_cell = (width // 2, height // 2)
        if terrain is not None:
            self.terrain = terrain
        elif procedural_terrain:
            self.terrain = ProceduralTerrainGenerator().generate(width, height, self.townhall_cell, seed=terrain_seed)
        else:
            self.terrain = TerrainMap.flat(width, height)
        self.distances: list[list[int | None]] = [[None for _ in range(height)] for _ in range(width)]
        self.flow_vectors: list[list[pygame.Vector2]] = [[pygame.Vector2(0, 0) for _ in range(height)] for _ in range(width)]
        self.flow_targets: list[list[pygame.Vector2 | None]] = [[None for _ in range(height)] for _ in range(width)]
        self.nav_version = 0
        self._path_cache: dict[tuple[tuple[int, int], tuple[int, int], int], list[tuple[float, float]]] = {}
        self._cell_clear_cache: dict[tuple[tuple[int, int], int], bool] = {}
        self._neighbor_cache: dict[tuple[tuple[int, int], int], list[tuple[tuple[int, int], float]]] = {}
        self._radius_distance_cache: dict[int, list[list[int | None]]] = {}
        self._radius_flow_cache: dict[int, tuple[list[list[pygame.Vector2]], list[list[pygame.Vector2 | None]]]] = {}
        self._base_component_ids: list[list[int]] = []
        self.recompute_flow()

    @property
    def world_size(self) -> tuple[int, int]:
        return self.width * self.tile_size, self.height * self.tile_size

    @property
    def spawn_cells(self) -> list[tuple[int, int]]:
        return list(self.iter_spawn_cells())

    def iter_spawn_cells(self):
        for x in range(self.width):
            yield (x, 0)
            yield (x, self.height - 1)
        for y in range(1, self.height - 1):
            yield (0, y)
            yield (self.width - 1, y)

    def random_spawn_cell(self, radius: float = 0.0) -> tuple[int, int]:
        query_radius = self._radius_from_key(self._radius_key(radius))
        reachable = [cell for cell in self.spawn_cells if self.reachable_cell(cell, query_radius)]
        if reachable:
            return random.choice(reachable)
        fallback = self.random_reachable_cell(query_radius)
        if fallback is not None:
            return fallback
        edge = random.randrange(4)
        if edge == 0:
            return random.randrange(self.width), 0
        if edge == 1:
            return random.randrange(self.width), self.height - 1
        if edge == 2:
            return 0, random.randrange(self.height)
        return self.width - 1, random.randrange(self.height)

    def in_bounds(self, cell: tuple[int, int]) -> bool:
        x, y = cell
        return 0 <= x < self.width and 0 <= y < self.height

    def is_outer_ring(self, cell: tuple[int, int]) -> bool:
        x, y = cell
        return x < 2 or y < 2 or x >= self.width - 2 or y >= self.height - 2

    def is_townhall_reserve(self, cell: tuple[int, int]) -> bool:
        tx, ty = self.townhall_cell
        x, y = cell
        return max(abs(x - tx), abs(y - ty)) <= 3

    def blocked(self, cell: tuple[int, int]) -> bool:
        return cell in self.walls or cell in self.towers

    def passable(self, cell: tuple[int, int]) -> bool:
        return self.in_bounds(cell) and self.terrain.is_walkable(cell) and not self.blocked(cell)

    def buildable(self, cell: tuple[int, int]) -> bool:
        return (
            self.in_bounds(cell)
            and self.terrain.is_buildable(cell)
            and not self.is_outer_ring(cell)
            and not self.is_townhall_reserve(cell)
            and cell not in self.walls
            and cell not in self.towers
        )

    def world_center(self, cell: tuple[int, int]) -> pygame.Vector2:
        x, y = cell
        return pygame.Vector2((x + 0.5) * self.tile_size, (y + 0.5) * self.tile_size)

    def cell_rect(self, cell: tuple[int, int]) -> pygame.Rect:
        x, y = cell
        return pygame.Rect(x * self.tile_size, y * self.tile_size, self.tile_size, self.tile_size)

    def cell_from_world(self, world: pygame.Vector2 | tuple[float, float]) -> tuple[int, int]:
        point = pygame.Vector2(world)
        return int(point.x // self.tile_size), int(point.y // self.tile_size)

    def recompute_flow(self) -> None:
        self.distances = [[None for _ in range(self.height)] for _ in range(self.width)]
        start = self.townhall_cell
        queue: deque[tuple[int, int]] = deque()
        self.distances[start[0]][start[1]] = 0
        queue.append(start)
        while queue:
            cell = queue.popleft()
            current = self.distances[cell[0]][cell[1]]
            for neighbor in self._cardinal_navigation_neighbors(cell):
                nx, ny = neighbor
                if self.distances[nx][ny] is not None:
                    continue
                self.distances[nx][ny] = int(current or 0) + 1
                queue.append(neighbor)
        self._base_component_ids = self._build_base_navigation_components()
        self._recompute_flow_vectors()
        self.nav_version += 1
        self._path_cache.clear()
        self._cell_clear_cache.clear()
        self._neighbor_cache.clear()
        self._radius_distance_cache.clear()
        self._radius_flow_cache.clear()

    def all_spawns_reachable(self) -> bool:
        for sx, sy in self.spawn_cells:
            if self.distances[sx][sy] is None:
                return False
        return True

    def try_add_wall(self, cell: tuple[int, int]) -> tuple[bool, str]:
        if not self.buildable(cell):
            return False, "Blocked"
        self.walls.add(cell)
        self.wall_health[cell] = self.wall_max_health
        self.recompute_flow()
        if not self.all_spawns_reachable():
            self.walls.remove(cell)
            self.wall_health.pop(cell, None)
            self.recompute_flow()
            return False, "Path sealed"
        return True, ""

    def remove_wall(self, cell: tuple[int, int]) -> None:
        self.walls.discard(cell)
        self.wall_health.pop(cell, None)
        self.recompute_flow()

    def try_add_tower(self, cell: tuple[int, int], tower: object) -> tuple[bool, str]:
        if not self.buildable(cell):
            return False, "Blocked"
        self.towers[cell] = tower
        self.recompute_flow()
        if not self.all_spawns_reachable():
            self.towers.pop(cell, None)
            self.recompute_flow()
            return False, "Path sealed"
        return True, ""

    def remove_tower(self, cell: tuple[int, int]) -> None:
        self.towers.pop(cell, None)
        self.recompute_flow()

    def would_keep_paths_open(self, cell: tuple[int, int], blocker: str) -> bool:
        if not self.buildable(cell):
            return False
        return self._all_spawns_reachable_with_candidate(cell)

    def distance_at(self, cell: tuple[int, int]) -> int | None:
        if not self.in_bounds(cell):
            return None
        return self.distances[cell[0]][cell[1]]

    def navigation_distance_at(self, cell: tuple[int, int], radius: float = 0.0) -> int | None:
        if not self.in_bounds(cell):
            return None
        radius_key = self._radius_key(radius)
        if radius_key <= 0:
            return self.distance_at(cell)
        distances = self._radius_distances_for_key(radius_key)
        return distances[cell[0]][cell[1]]

    def reachable_cell(self, cell: tuple[int, int], radius: float = 0.0) -> bool:
        if not self.in_bounds(cell):
            return False
        radius_key = self._radius_key(radius)
        query_radius = self._radius_from_key(radius_key)
        if not self._cell_clear_for_radius(cell, query_radius):
            return False
        return self.navigation_distance_at(cell, query_radius) is not None

    def reachable_world(self, world: pygame.Vector2 | tuple[float, float], radius: float = 0.0) -> bool:
        point = pygame.Vector2(world)
        radius_key = self._radius_key(radius)
        query_radius = self._radius_from_key(radius_key)
        return self.circle_clear(point, query_radius) and self.reachable_cell(self.cell_from_world(point), query_radius)

    def random_reachable_cell(self, radius: float = 0.0) -> tuple[int, int] | None:
        radius_key = self._radius_key(radius)
        query_radius = self._radius_from_key(radius_key)
        if radius_key <= 0:
            cells = [
                (x, y)
                for x in range(self.width)
                for y in range(self.height)
                if self.passable((x, y)) and self.distance_at((x, y)) is not None
            ]
        else:
            distances = self._radius_distances_for_key(radius_key)
            cells = [
                (x, y)
                for x in range(self.width)
                for y in range(self.height)
                if distances[x][y] is not None and self._cell_clear_for_radius((x, y), query_radius)
            ]
        if not cells:
            return None
        non_reserve = [cell for cell in cells if not self.is_townhall_reserve(cell)]
        return random.choice(non_reserve or cells)

    def nearest_passable_cell(
        self,
        cell: tuple[int, int],
        radius: float = 0.0,
        max_radius: int = 8,
    ) -> tuple[int, int] | None:
        origin = self._clamp_cell(cell)
        if self._cell_clear_for_radius(origin, radius):
            return origin

        best: tuple[int, int] | None = None
        best_score = float("inf")
        ox, oy = origin
        for ring in range(1, max_radius + 1):
            for x in range(ox - ring, ox + ring + 1):
                for y in range(oy - ring, oy + ring + 1):
                    if max(abs(x - ox), abs(y - oy)) != ring:
                        continue
                    candidate = (x, y)
                    if not self._cell_clear_for_radius(candidate, radius):
                        continue
                    score = (x - cell[0]) ** 2 + (y - cell[1]) ** 2
                    if score < best_score:
                        best = candidate
                        best_score = score
            if best is not None:
                return best
        return None

    def nearest_clear_world(
        self,
        world: pygame.Vector2 | tuple[float, float],
        radius: float,
        max_radius: int = 8,
    ) -> pygame.Vector2:
        point = pygame.Vector2(world)
        if self.circle_clear(point, radius):
            return point
        sampled = self._nearest_clear_point(point, radius, max_distance=max_radius * self.tile_size)
        if sampled is not None:
            return sampled
        cell = self.nearest_passable_cell(self.cell_from_world(point), radius, max_radius)
        if cell is None:
            return self.world_center(self.townhall_cell)
        return self.world_center(cell)

    def nearest_reachable_world(
        self,
        world: pygame.Vector2 | tuple[float, float],
        radius: float,
        max_radius: int = 8,
    ) -> pygame.Vector2 | None:
        point = pygame.Vector2(world)
        radius_key = self._radius_key(radius)
        query_radius = self._radius_from_key(radius_key)
        if self.reachable_world(point, query_radius):
            return point

        sampled = self._nearest_clear_point(point, query_radius, max_distance=max_radius * self.tile_size)
        if sampled is not None and self.reachable_cell(self.cell_from_world(sampled), query_radius):
            return sampled

        origin = self._clamp_cell(self.cell_from_world(point))
        ox, oy = origin
        best: tuple[int, int] | None = None
        best_score = float("inf")
        for ring in range(0, max_radius + 1):
            for x in range(ox - ring, ox + ring + 1):
                for y in range(oy - ring, oy + ring + 1):
                    if max(abs(x - ox), abs(y - oy)) != ring:
                        continue
                    candidate = (x, y)
                    if not self.reachable_cell(candidate, query_radius):
                        continue
                    score = (x - origin[0]) ** 2 + (y - origin[1]) ** 2
                    if score < best_score:
                        best = candidate
                        best_score = score
            if best is not None:
                return self.world_center(best)

        return None

    def navigation_radius(self, radius: float) -> float:
        """Radius used for planning, inflated slightly like an agent navmesh."""
        if radius <= 0:
            return 0.0
        return min(self.tile_size * 0.46, float(radius) + min(self.nav_padding, self.tile_size * 0.125))

    def find_path(
        self,
        start_world: pygame.Vector2 | tuple[float, float],
        goal_world: pygame.Vector2 | tuple[float, float],
        radius: float = 0.0,
        max_goal_radius: int = 12,
    ) -> list[pygame.Vector2]:
        start_point = pygame.Vector2(start_world)
        goal_point = pygame.Vector2(goal_world)
        radius_key = self._radius_key(radius)
        query_radius = self._radius_from_key(radius_key)
        start = self.nearest_passable_cell(self.cell_from_world(start_point), query_radius, max_radius=5)
        goal = self.nearest_passable_cell(self.cell_from_world(goal_point), query_radius, max_radius=max_goal_radius)
        if start is None or goal is None:
            return []
        if start == goal:
            return [start_point, self.world_center(goal)]

        cache_key = (start, goal, radius_key)
        cached = self._path_cache.get(cache_key)
        if cached is not None:
            path = [pygame.Vector2(point) for point in cached]
            if path:
                path[0] = start_point
            return path

        if not self._same_base_navigation_component(start, goal):
            self._remember_path(cache_key, [])
            return []

        frontier: list[tuple[float, int, tuple[int, int]]] = []
        counter = 0
        heapq.heappush(frontier, (0.0, counter, start))
        came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        cost_so_far: dict[tuple[int, int], float] = {start: 0.0}

        while frontier:
            _, _, current = heapq.heappop(frontier)
            if current == goal:
                break

            for neighbor, step_cost in self._navigation_neighbors(current, query_radius):
                new_cost = cost_so_far[current] + step_cost
                if neighbor in cost_so_far and new_cost >= cost_so_far[neighbor]:
                    continue
                cost_so_far[neighbor] = new_cost
                counter += 1
                priority = new_cost + self._heuristic(neighbor, goal)
                heapq.heappush(frontier, (priority, counter, neighbor))
                came_from[neighbor] = current

        if goal not in came_from:
            self._remember_path(cache_key, [])
            return []

        cells: list[tuple[int, int]] = []
        current: tuple[int, int] | None = goal
        while current is not None:
            cells.append(current)
            current = came_from[current]
        cells.reverse()
        path = [self.world_center(cell) for cell in cells]
        if path:
            path[0] = pygame.Vector2(start_point)
        path = self.smooth_path(path, query_radius)
        self._remember_path(cache_key, [(point.x, point.y) for point in path])
        return [pygame.Vector2(point) for point in path]

    def _remember_path(
        self,
        cache_key: tuple[tuple[int, int], tuple[int, int], int],
        path: list[tuple[float, float]],
    ) -> None:
        if len(self._path_cache) > 2048:
            self._path_cache.clear()
        self._path_cache[cache_key] = path

    def smooth_path(self, path: list[pygame.Vector2], radius: float) -> list[pygame.Vector2]:
        if len(path) <= 2:
            return path
        smoothed = [pygame.Vector2(path[0])]
        index = 0
        while index < len(path) - 1:
            next_index = len(path) - 1
            while next_index > index + 1 and not self.line_clear(path[index], path[next_index], radius):
                next_index -= 1
            smoothed.append(pygame.Vector2(path[next_index]))
            index = next_index
        return smoothed

    def line_clear(
        self,
        start: pygame.Vector2 | tuple[float, float],
        end: pygame.Vector2 | tuple[float, float],
        radius: float,
    ) -> bool:
        a = pygame.Vector2(start)
        b = pygame.Vector2(end)
        delta = b - a
        length = delta.length()
        if length == 0:
            return self.circle_clear(a, radius)
        if not self.circle_clear(a, radius) or not self.circle_clear(b, radius):
            return False
        if not self._terrain_line_clear(a, b, radius):
            return False

        min_x = int((min(a.x, b.x) - radius) // self.tile_size)
        max_x = int((max(a.x, b.x) + radius) // self.tile_size)
        min_y = int((min(a.y, b.y) - radius) // self.tile_size)
        max_y = int((max(a.y, b.y) + radius) // self.tile_size)
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                cell = (x, y)
                if not self.in_bounds(cell) or not self.blocked(cell):
                    continue
                rect = self.cell_rect(cell)
                left = rect.left - radius
                top = rect.top - radius
                right = rect.right + radius
                bottom = rect.bottom + radius
                if self._segment_intersects_aabb(a, b, left, top, right, bottom):
                    return False
        return True

    def path_target_from_world(self, world: pygame.Vector2) -> pygame.Vector2:
        cell = self.cell_from_world(world)
        if not self.in_bounds(cell):
            return self.world_center(self.townhall_cell)

        cx, cy = cell
        current = self.distance_at(cell)
        best = cell
        best_distance = current if current is not None else 999999

        for neighbor in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            distance = self.distance_at(neighbor)
            if distance is None:
                continue
            if current is None or distance < best_distance:
                best = neighbor
                best_distance = distance

        if best == cell and current not in (None, 0):
            return self.world_center(cell)
        return self.world_center(best)

    def steering_direction_from_world(self, world: pygame.Vector2, radius: float = 0.0) -> pygame.Vector2:
        point = pygame.Vector2(world)
        cell = self.cell_from_world(point)
        if not self.in_bounds(cell):
            return self._safe_direction(self.world_center(self.townhall_cell) - point)

        radius_key = self._radius_key(radius)
        if radius_key > 0:
            vectors, targets = self._radius_flow_for_key(radius_key)
            cx, cy = cell
            current = self.navigation_distance_at(cell, self._radius_from_key(radius_key))
            if current == 0:
                return self._safe_direction(self.world_center(self.townhall_cell) - point)
            if current is not None:
                flow = pygame.Vector2(vectors[cx][cy])
                target = targets[cx][cy]
                if target is not None:
                    target_pull = target - point
                    if target_pull.length_squared() > 0:
                        flow += target_pull.normalize() * 0.35
                if flow.length_squared() > 0:
                    return flow.normalize()
            return pygame.Vector2(0, 0)

        cx, cy = cell
        current = self.distance_at(cell)
        if current == 0:
            return self._safe_direction(self.world_center(self.townhall_cell) - point)
        if current is not None:
            flow = pygame.Vector2(self.flow_vectors[cx][cy])
            target = self.flow_targets[cx][cy]
            if target is not None:
                target_pull = target - point
                if target_pull.length_squared() > 0:
                    flow += target_pull.normalize() * 0.35
            if flow.length_squared() > 0:
                return flow.normalize()

        flow = pygame.Vector2(0, 0)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neighbor = (cx + dx, cy + dy)
                distance = self.distance_at(neighbor)
                if distance is None:
                    continue
                if dx != 0 and dy != 0 and not self._can_step_diagonal(cell, dx, dy):
                    continue
                if current is None:
                    weight = 1.0 / max(1.0, float(distance))
                else:
                    progress = float(current - distance)
                    if progress < -0.25:
                        continue
                    weight = max(0.08, progress + 0.15)

                direction = self.world_center(neighbor) - point
                if direction.length_squared() > 0:
                    flow += direction.normalize() * weight

        if flow.length_squared() == 0:
            return self._safe_direction(self.path_target_from_world(point) - point)
        return flow.normalize()

    def obstacle_avoidance_from_world(self, world: pygame.Vector2, radius: float) -> pygame.Vector2:
        point = pygame.Vector2(world)
        push = pygame.Vector2(0, 0)
        detection = radius + self.tile_size * 0.20
        min_x = int((point.x - detection) // self.tile_size)
        max_x = int((point.x + detection) // self.tile_size)
        min_y = int((point.y - detection) // self.tile_size)
        max_y = int((point.y + detection) // self.tile_size)

        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                cell = (x, y)
                if not self.in_bounds(cell) or not self.blocked(cell):
                    continue
                rect = self.cell_rect(cell)
                closest = pygame.Vector2(
                    max(rect.left, min(point.x, rect.right)),
                    max(rect.top, min(point.y, rect.bottom)),
                )
                delta = point - closest
                point_outside_x = point.x < rect.left or point.x > rect.right
                point_outside_y = point.y < rect.top or point.y > rect.bottom
                if point_outside_x and point_outside_y and delta.length_squared() > radius * radius:
                    continue
                if delta.length_squared() == 0:
                    delta = point - pygame.Vector2(rect.center)
                if delta.length_squared() == 0:
                    angle = random.random() * math.tau
                    delta = pygame.Vector2(math.cos(angle), math.sin(angle))
                distance = max(0.001, delta.length())
                if distance < detection:
                    push += delta.normalize() * ((detection - distance) / detection)

        return push.normalize() if push.length_squared() > 0 else push

    def resolve_circle_blockers(
        self,
        world: pygame.Vector2,
        radius: float,
        previous_world: pygame.Vector2 | tuple[float, float] | None = None,
    ) -> tuple[pygame.Vector2, bool]:
        point = pygame.Vector2(world)
        collided = False
        if previous_world is not None:
            previous = pygame.Vector2(previous_world)
            if not self.circle_clear(previous, radius):
                recovered = self._nearest_clear_point(previous, radius, max_distance=self.tile_size * 3.0)
                if recovered is None:
                    recovered = self.nearest_clear_world(previous, radius)
                previous = recovered
                collided = True
            if self.circle_clear(point, radius) and self.cell_from_world(previous) == self.cell_from_world(point):
                return self._clamp_world(point, radius), collided
            if self.line_clear(previous, point, radius):
                return self._clamp_world(point, radius), collided
            slid = self._slide_circle(previous, point, radius)
            return self._clamp_world(slid, radius), True
        if self.circle_clear(point, radius):
            return self._clamp_world(point, radius), False
        min_x = int((point.x - radius) // self.tile_size)
        max_x = int((point.x + radius) // self.tile_size)
        min_y = int((point.y - radius) // self.tile_size)
        max_y = int((point.y + radius) // self.tile_size)

        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                cell = (x, y)
                if not self.in_bounds(cell) or not self.blocked(cell):
                    continue
                rect = self.cell_rect(cell)
                closest = pygame.Vector2(
                    max(rect.left, min(point.x, rect.right)),
                    max(rect.top, min(point.y, rect.bottom)),
                )
                delta = point - closest
                if delta.length_squared() == 0:
                    point = self._push_out_from_inside_rect(point, rect, radius)
                    collided = True
                    continue
                distance = delta.length()
                overlap = radius - distance
                if overlap > 0:
                    point += delta.normalize() * overlap
                    collided = True

        if not self.circle_clear(point, radius):
            recovered = self._nearest_clear_point(point, radius, max_distance=self.tile_size * 3.0)
            if recovered is not None:
                point = recovered
                collided = True

        return self._clamp_world(point, radius), collided

    def _slide_circle(self, start: pygame.Vector2, end: pygame.Vector2, radius: float) -> pygame.Vector2:
        if not self.circle_clear(start, radius):
            recovered = self._nearest_clear_point(start, radius, max_distance=self.tile_size * 3.0)
            return recovered if recovered is not None else start

        candidates = [self._last_clear_on_segment(start, end, radius)]
        for axes in (("x", "y"), ("y", "x")):
            pos = pygame.Vector2(start)
            for axis in axes:
                target = pygame.Vector2(end.x, pos.y) if axis == "x" else pygame.Vector2(pos.x, end.y)
                pos = self._last_clear_on_segment(pos, target, radius)
            candidates.append(pos)

        sampled = self._nearest_clear_point(end, radius, max_distance=self.tile_size * 1.5, origin=start)
        if sampled is not None:
            candidates.append(sampled)

        delta = end - start
        direction = delta.normalize() if delta.length_squared() > 0 else pygame.Vector2(0, 0)
        clear_candidates = [
            candidate
            for candidate in candidates
            if self.circle_clear(candidate, radius) and self.line_clear(start, candidate, radius)
        ]
        if not clear_candidates:
            return start
        return max(
            clear_candidates,
            key=lambda candidate: ((candidate - start).dot(direction), -(candidate - end).length_squared()),
        )

    def _last_clear_on_segment(self, start: pygame.Vector2, end: pygame.Vector2, radius: float) -> pygame.Vector2:
        if start.distance_to(end) <= 0.001:
            return pygame.Vector2(start)
        if self.line_clear(start, end, radius):
            return pygame.Vector2(end)

        low = 0.0
        high = 1.0
        for _ in range(10):
            mid = (low + high) * 0.5
            candidate = start.lerp(end, mid)
            if self.line_clear(start, candidate, radius):
                low = mid
            else:
                high = mid
        return start.lerp(end, low)

    def _nearest_clear_point(
        self,
        world: pygame.Vector2,
        radius: float,
        *,
        max_distance: float,
        origin: pygame.Vector2 | None = None,
    ) -> pygame.Vector2 | None:
        point = pygame.Vector2(world)
        if self.circle_clear(point, radius) and (origin is None or self.line_clear(origin, point, radius)):
            return point

        clamped = self._clamp_world(point, radius)
        if clamped != point and self.circle_clear(clamped, radius) and (origin is None or self.line_clear(origin, clamped, radius)):
            return clamped

        step = max(2.0, min(6.0, self.tile_size * 0.16))
        rings = max(1, int(math.ceil(max_distance / step)))
        for ring in range(1, rings + 1):
            distance = ring * step
            samples = max(8, min(64, int(math.ceil(math.tau * distance / step))))
            best: pygame.Vector2 | None = None
            best_score = float("inf")
            offset = (math.pi / samples) if ring % 2 else 0.0
            for index in range(samples):
                angle = offset + index * math.tau / samples
                candidate = point + pygame.Vector2(math.cos(angle), math.sin(angle)) * distance
                if not self.circle_clear(candidate, radius):
                    continue
                if origin is not None and not self.line_clear(origin, candidate, radius):
                    continue
                score = (candidate - point).length_squared()
                if score < best_score:
                    best = candidate
                    best_score = score
            if best is not None:
                return best
        return None

    def _clamp_world(self, world: pygame.Vector2, radius: float) -> pygame.Vector2:
        point = pygame.Vector2(world)
        world_width = self.width * self.tile_size
        world_height = self.height * self.tile_size
        point.x = max(radius, min(world_width - radius, point.x))
        point.y = max(radius, min(world_height - radius, point.y))
        return point

    def circle_clear(self, world: pygame.Vector2, radius: float) -> bool:
        point = pygame.Vector2(world)
        world_width = self.width * self.tile_size
        world_height = self.height * self.tile_size
        if not (radius <= point.x <= world_width - radius and radius <= point.y <= world_height - radius):
            return False
        center_cell = self.cell_from_world(point)
        if not self.passable(center_cell):
            return False

        min_x = int((point.x - radius) // self.tile_size)
        max_x = int((point.x + radius) // self.tile_size)
        min_y = int((point.y - radius) // self.tile_size)
        max_y = int((point.y + radius) // self.tile_size)

        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                cell = (x, y)
                if not self.in_bounds(cell) or not self._terrain_radius_compatible(center_cell, cell):
                    return False
                if not self.blocked(cell):
                    continue
                rect = self.cell_rect(cell)
                closest = pygame.Vector2(
                    max(rect.left, min(point.x, rect.right)),
                    max(rect.top, min(point.y, rect.bottom)),
                )
                if point.distance_to(closest) < radius:
                    return False
        return True

    def _push_out_from_inside_rect(self, point: pygame.Vector2, rect: pygame.Rect, radius: float) -> pygame.Vector2:
        distances = (
            (abs(point.x - rect.left), pygame.Vector2(-1, 0), rect.left - radius, "x"),
            (abs(rect.right - point.x), pygame.Vector2(1, 0), rect.right + radius, "x"),
            (abs(point.y - rect.top), pygame.Vector2(0, -1), rect.top - radius, "y"),
            (abs(rect.bottom - point.y), pygame.Vector2(0, 1), rect.bottom + radius, "y"),
        )
        _, _, coordinate, axis = min(distances, key=lambda item: item[0])
        pushed = pygame.Vector2(point)
        if axis == "x":
            pushed.x = coordinate
        else:
            pushed.y = coordinate
        return pushed

    def _can_step_diagonal(self, cell: tuple[int, int], dx: int, dy: int) -> bool:
        if dx == 0 or dy == 0:
            return self._can_move_between(cell, (cell[0] + dx, cell[1] + dy))
        x, y = cell
        candidate = (x + dx, y + dy)
        if not self.in_bounds(candidate) or candidate in self.walls or candidate in self.towers:
            return False
        if (x + dx, y) in self.walls or (x + dx, y) in self.towers or (x, y + dy) in self.walls or (x, y + dy) in self.towers:
            return False
        return candidate in self.terrain.linked_diagonal_neighbors(cell) or self.terrain.can_traverse(cell, candidate)

    def _all_spawns_reachable_with_candidate(self, blocked_cell: tuple[int, int]) -> bool:
        if blocked_cell == self.townhall_cell:
            return False
        queue: deque[tuple[int, int]] = deque([self.townhall_cell])
        visited = {self.townhall_cell}
        while queue:
            cell = queue.popleft()
            for neighbor in self._cardinal_navigation_neighbors(cell, blocked_cell=blocked_cell):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)
        return all(cell in visited for cell in self.spawn_cells)

    def _radius_distances_for_key(self, radius_key: int) -> list[list[int | None]]:
        cached = self._radius_distance_cache.get(radius_key)
        if cached is not None:
            return cached

        query_radius = self._radius_from_key(radius_key)
        distances: list[list[int | None]] = [[None for _ in range(self.height)] for _ in range(self.width)]
        start = self.nearest_passable_cell(self.townhall_cell, query_radius, max_radius=4)
        if start is None:
            self._radius_distance_cache[radius_key] = distances
            return distances

        queue: deque[tuple[int, int]] = deque([start])
        distances[start[0]][start[1]] = 0
        while queue:
            cell = queue.popleft()
            current = distances[cell[0]][cell[1]]
            for neighbor, _step_cost in self._navigation_neighbors(cell, query_radius):
                nx, ny = neighbor
                if distances[nx][ny] is not None:
                    continue
                distances[nx][ny] = int(current or 0) + 1
                queue.append(neighbor)

        self._radius_distance_cache[radius_key] = distances
        return distances

    def _radius_flow_for_key(
        self,
        radius_key: int,
    ) -> tuple[list[list[pygame.Vector2]], list[list[pygame.Vector2 | None]]]:
        cached = self._radius_flow_cache.get(radius_key)
        if cached is not None:
            return cached

        distances = self._radius_distances_for_key(radius_key)
        vectors: list[list[pygame.Vector2]] = [[pygame.Vector2(0, 0) for _ in range(self.height)] for _ in range(self.width)]
        targets: list[list[pygame.Vector2 | None]] = [[None for _ in range(self.height)] for _ in range(self.width)]
        for cx in range(self.width):
            for cy in range(self.height):
                current = distances[cx][cy]
                if current in (None, 0):
                    continue
                cell = (cx, cy)
                point = self.world_center(cell)
                flow = pygame.Vector2(0, 0)
                best_cell = cell
                best_distance = current
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        neighbor = (cx + dx, cy + dy)
                        if not self.in_bounds(neighbor):
                            continue
                        distance = distances[neighbor[0]][neighbor[1]]
                        if distance is None:
                            continue
                        if dx != 0 and dy != 0 and not self._can_step_diagonal(cell, dx, dy):
                            continue
                        if distance < best_distance:
                            best_cell = neighbor
                            best_distance = distance
                        progress = float(current - distance)
                        if progress < -0.25:
                            continue
                        weight = max(0.08, progress + 0.15)
                        direction = self.world_center(neighbor) - point
                        if direction.length_squared() > 0:
                            flow += direction.normalize() * weight
                if flow.length_squared() > 0:
                    vectors[cx][cy] = flow.normalize()
                if best_cell != cell:
                    targets[cx][cy] = self.world_center(best_cell)

        self._radius_flow_cache[radius_key] = (vectors, targets)
        return vectors, targets

    def _recompute_flow_vectors(self) -> None:
        vectors: list[list[pygame.Vector2]] = [[pygame.Vector2(0, 0) for _ in range(self.height)] for _ in range(self.width)]
        targets: list[list[pygame.Vector2 | None]] = [[None for _ in range(self.height)] for _ in range(self.width)]
        for cx in range(self.width):
            for cy in range(self.height):
                current = self.distances[cx][cy]
                if current in (None, 0):
                    continue
                point = self.world_center((cx, cy))
                flow = pygame.Vector2(0, 0)
                best_cell = (cx, cy)
                best_distance = current
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        neighbor = (cx + dx, cy + dy)
                        distance = self.distance_at(neighbor)
                        if distance is None:
                            continue
                        if dx != 0 and dy != 0 and not self._can_step_diagonal((cx, cy), dx, dy):
                            continue
                        if distance < best_distance:
                            best_cell = neighbor
                            best_distance = distance
                        progress = float(current - distance)
                        if progress < -0.25:
                            continue
                        weight = max(0.08, progress + 0.15)
                        direction = self.world_center(neighbor) - point
                        if direction.length_squared() > 0:
                            flow += direction.normalize() * weight
                if flow.length_squared() > 0:
                    vectors[cx][cy] = flow.normalize()
                if best_cell != (cx, cy):
                    targets[cx][cy] = self.world_center(best_cell)
        self.flow_vectors = vectors
        self.flow_targets = targets

    def _cardinal_navigation_neighbors(
        self,
        cell: tuple[int, int],
        *,
        blocked_cell: tuple[int, int] | None = None,
    ) -> list[tuple[int, int]]:
        x, y = cell
        neighbors: list[tuple[int, int]] = []
        for candidate in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if candidate == blocked_cell:
                continue
            if not self._can_move_between(cell, candidate):
                continue
            neighbors.append(candidate)
        return neighbors

    def _can_move_between(self, start: tuple[int, int], goal: tuple[int, int]) -> bool:
        if not self.in_bounds(start) or not self.in_bounds(goal) or goal in self.walls or goal in self.towers:
            return False
        if goal in self.terrain.linked_cardinal_neighbors(start):
            return True
        if goal in self.terrain.linked_diagonal_neighbors(start):
            return True
        return self.terrain.can_traverse(start, goal)

    def _terrain_radius_compatible(self, center_cell: tuple[int, int], cell: tuple[int, int]) -> bool:
        if not self.in_bounds(cell) or not self.terrain.is_walkable(cell):
            return False
        if cell == center_cell:
            return True
        center_elevation = self.terrain.elevation_at(center_cell)
        cell_elevation = self.terrain.elevation_at(cell)
        if center_elevation == cell_elevation:
            return True
        return (
            self.terrain.can_traverse(center_cell, cell)
            or self.terrain.can_traverse(cell, center_cell)
            or self._terrain_stair_overlap_compatible(center_cell, cell)
        )

    def _terrain_stair_overlap_compatible(self, a: tuple[int, int], b: tuple[int, int]) -> bool:
        a_elevation = self.terrain.elevation_at(a)
        b_elevation = self.terrain.elevation_at(b)
        if abs(a_elevation - b_elevation) != 1:
            return False
        low_elevation = min(a_elevation, b_elevation)
        high_elevation = max(a_elevation, b_elevation)
        min_x = min(a[0], b[0]) - 1
        max_x = max(a[0], b[0]) + 1
        min_y = min(a[1], b[1]) - 1
        max_y = max(a[1], b[1]) + 1
        for sx in range(min_x, max_x + 1):
            for sy in range(min_y, max_y + 1):
                stair = (sx, sy)
                if not self.in_bounds(stair) or self.terrain.cell(stair).feature != STAIR_SOUTH:
                    continue
                top = (sx, sy - 1)
                if self.terrain.elevation_at(stair) != low_elevation or self.terrain.elevation_at(top) != high_elevation:
                    continue
                if self._cell_in_stair_overlap_zone(stair, a, low_elevation, high_elevation) and self._cell_in_stair_overlap_zone(stair, b, low_elevation, high_elevation):
                    return True
        return False

    def _cell_in_stair_overlap_zone(
        self,
        stair: tuple[int, int],
        cell: tuple[int, int],
        low_elevation: int,
        high_elevation: int,
    ) -> bool:
        sx, sy = stair
        x, y = cell
        elevation = self.terrain.elevation_at(cell)
        if elevation == low_elevation:
            return y == sy and abs(x - sx) <= 1
        if elevation == high_elevation:
            return y == sy - 1 and abs(x - sx) <= 1
        return False

    def _terrain_line_clear(self, start: pygame.Vector2, end: pygame.Vector2, radius: float) -> bool:
        delta = end - start
        length = delta.length()
        if length == 0:
            return self.circle_clear(start, radius)

        steps = max(1, int(math.ceil(length / max(4.0, self.tile_size * 0.35))))
        previous_cell = self.cell_from_world(start)
        for index in range(1, steps + 1):
            point = start.lerp(end, index / steps)
            if not self.circle_clear(point, radius):
                return False
            cell = self.cell_from_world(point)
            if cell == previous_cell:
                continue
            dx = cell[0] - previous_cell[0]
            dy = cell[1] - previous_cell[1]
            if abs(dx) > 1 or abs(dy) > 1:
                return False
            if dx != 0 and dy != 0:
                if not self._can_step_diagonal(previous_cell, dx, dy):
                    return False
            elif not self._can_move_between(previous_cell, cell):
                return False
            previous_cell = cell
        return True

    def _navigation_neighbors(self, cell: tuple[int, int], radius: float) -> list[tuple[tuple[int, int], float]]:
        radius_key = self._radius_key(radius)
        cache_key = (cell, radius_key)
        cached = self._neighbor_cache.get(cache_key)
        if cached is not None:
            return cached

        query_radius = self._radius_from_key(radius_key)
        x, y = cell
        neighbors: list[tuple[tuple[int, int], float]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                candidate = (x + dx, y + dy)
                if not self._cell_clear_for_radius(candidate, query_radius):
                    continue
                if dx != 0 and dy != 0 and not self._can_step_diagonal(cell, dx, dy):
                    continue
                terrain_cost = self.terrain.movement_cost(cell, candidate)
                if math.isinf(terrain_cost):
                    continue
                neighbors.append((candidate, terrain_cost))
        self._neighbor_cache[cache_key] = neighbors
        return neighbors

    def _same_base_navigation_component(self, start: tuple[int, int], goal: tuple[int, int]) -> bool:
        if not self.in_bounds(start) or not self.in_bounds(goal) or not self._base_component_ids:
            return False
        start_component = self._base_component_ids[start[0]][start[1]]
        return start_component >= 0 and start_component == self._base_component_ids[goal[0]][goal[1]]

    def _build_base_navigation_components(self) -> list[list[int]]:
        components = [[-1 for _ in range(self.height)] for _ in range(self.width)]
        component_id = 0
        for x in range(self.width):
            for y in range(self.height):
                cell = (x, y)
                if components[x][y] >= 0 or not self.passable(cell):
                    continue
                pending = [cell]
                components[x][y] = component_id
                while pending:
                    current = pending.pop()
                    cx, cy = current
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            if dx == 0 and dy == 0:
                                continue
                            candidate = (cx + dx, cy + dy)
                            if dx != 0 and dy != 0:
                                if not self._can_step_diagonal(current, dx, dy):
                                    continue
                            elif not self._can_move_between(current, candidate):
                                continue
                            nx, ny = candidate
                            if components[nx][ny] >= 0:
                                continue
                            components[nx][ny] = component_id
                            pending.append(candidate)
                component_id += 1
        return components

    def _cell_clear_for_radius(self, cell: tuple[int, int], radius: float) -> bool:
        radius_key = self._radius_key(radius)
        cache_key = (cell, radius_key)
        cached = self._cell_clear_cache.get(cache_key)
        if cached is not None:
            return cached
        query_radius = self._radius_from_key(radius_key)
        clear = self.in_bounds(cell) and self.passable(cell) and (query_radius <= 0 or self.circle_clear(self.world_center(cell), query_radius))
        self._cell_clear_cache[cache_key] = clear
        return clear

    def _heuristic(self, a: tuple[int, int], b: tuple[int, int]) -> float:
        dx = abs(a[0] - b[0])
        dy = abs(a[1] - b[1])
        return max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy)

    def _clamp_cell(self, cell: tuple[int, int]) -> tuple[int, int]:
        x, y = cell
        return max(0, min(self.width - 1, x)), max(0, min(self.height - 1, y))

    def _radius_key(self, radius: float) -> int:
        return max(0, int(math.ceil(radius * 2.0)))

    def _radius_from_key(self, radius_key: int) -> float:
        return radius_key / 2.0

    def _segment_intersects_aabb(
        self,
        start: pygame.Vector2,
        end: pygame.Vector2,
        left: float,
        top: float,
        right: float,
        bottom: float,
    ) -> bool:
        dx = end.x - start.x
        dy = end.y - start.y
        t_min = 0.0
        t_max = 1.0
        for p, q in (
            (-dx, start.x - left),
            (dx, right - start.x),
            (-dy, start.y - top),
            (dy, bottom - start.y),
        ):
            if p == 0:
                if q < 0:
                    return False
                continue
            t = q / p
            if p < 0:
                if t > t_max:
                    return False
                if t > t_min:
                    t_min = t
            else:
                if t < t_min:
                    return False
                if t < t_max:
                    t_max = t
        return True

    def _safe_direction(self, vector: pygame.Vector2) -> pygame.Vector2:
        if vector.length_squared() == 0:
            return pygame.Vector2(0, 0)
        return vector.normalize()
