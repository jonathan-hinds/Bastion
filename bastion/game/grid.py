from __future__ import annotations

from collections import deque
import heapq
import math
import random

import pygame

from bastion import config


class GameGrid:
    wall_max_health = 90.0

    def __init__(self, width: int = config.MAP_WIDTH, height: int = config.MAP_HEIGHT, tile_size: int = config.TILE_SIZE) -> None:
        self.width = width
        self.height = height
        self.tile_size = tile_size
        self.walls: set[tuple[int, int]] = set()
        self.wall_health: dict[tuple[int, int], float] = {}
        self.towers: dict[tuple[int, int], object] = {}
        self.townhall_cell = (width // 2, height // 2)
        self.distances: list[list[int | None]] = [[None for _ in range(height)] for _ in range(width)]
        self.flow_vectors: list[list[pygame.Vector2]] = [[pygame.Vector2(0, 0) for _ in range(height)] for _ in range(width)]
        self.flow_targets: list[list[pygame.Vector2 | None]] = [[None for _ in range(height)] for _ in range(width)]
        self.nav_version = 0
        self._path_cache: dict[tuple[tuple[int, int], tuple[int, int], int], list[tuple[float, float]]] = {}
        self._cell_clear_cache: dict[tuple[tuple[int, int], int], bool] = {}
        self._neighbor_cache: dict[tuple[tuple[int, int], int], list[tuple[tuple[int, int], float]]] = {}
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

    def random_spawn_cell(self) -> tuple[int, int]:
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
        return self.in_bounds(cell) and not self.blocked(cell)

    def buildable(self, cell: tuple[int, int]) -> bool:
        return (
            self.in_bounds(cell)
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
            x, y = cell
            current = self.distances[x][y]
            for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                nx, ny = neighbor
                if not self.passable(neighbor):
                    continue
                if self.distances[nx][ny] is not None:
                    continue
                self.distances[nx][ny] = int(current or 0) + 1
                queue.append(neighbor)
        self._recompute_flow_vectors()
        self.nav_version += 1
        self._path_cache.clear()
        self._cell_clear_cache.clear()
        self._neighbor_cache.clear()

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
        cell = self.nearest_passable_cell(self.cell_from_world(point), radius, max_radius)
        if cell is None:
            resolved, _ = self.resolve_circle_blockers(point, radius)
            return resolved
        return self.world_center(cell)

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
        if len(self._path_cache) > 2048:
            self._path_cache.clear()
        self._path_cache[cache_key] = [(point.x, point.y) for point in path]
        return [pygame.Vector2(point) for point in path]

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

    def steering_direction_from_world(self, world: pygame.Vector2) -> pygame.Vector2:
        point = pygame.Vector2(world)
        cell = self.cell_from_world(point)
        if not self.in_bounds(cell):
            return self._safe_direction(self.world_center(self.townhall_cell) - point)

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
                if distance is None or not self._can_step_diagonal(cell, dx, dy):
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

    def resolve_circle_blockers(self, world: pygame.Vector2, radius: float) -> tuple[pygame.Vector2, bool]:
        point = pygame.Vector2(world)
        collided = False
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

        world_width = self.width * self.tile_size
        world_height = self.height * self.tile_size
        point.x = max(radius, min(world_width - radius, point.x))
        point.y = max(radius, min(world_height - radius, point.y))
        return point, collided

    def circle_clear(self, world: pygame.Vector2, radius: float) -> bool:
        point = pygame.Vector2(world)
        world_width = self.width * self.tile_size
        world_height = self.height * self.tile_size
        if not (radius <= point.x <= world_width - radius and radius <= point.y <= world_height - radius):
            return False
        if not self.passable(self.cell_from_world(point)):
            return False

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
            return True
        x, y = cell
        return self.passable((x + dx, y)) and self.passable((x, y + dy))

    def _all_spawns_reachable_with_candidate(self, blocked_cell: tuple[int, int]) -> bool:
        if blocked_cell == self.townhall_cell:
            return False
        queue: deque[tuple[int, int]] = deque([self.townhall_cell])
        visited = {self.townhall_cell}
        while queue:
            x, y = queue.popleft()
            for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbor in visited or neighbor == blocked_cell or not self.passable(neighbor):
                    continue
                visited.add(neighbor)
                queue.append(neighbor)
        return all(cell in visited for cell in self.spawn_cells)

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
                        if distance is None or not self._can_step_diagonal((cx, cy), dx, dy):
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
                neighbors.append((candidate, math.sqrt(2) if dx != 0 and dy != 0 else 1.0))
        self._neighbor_cache[cache_key] = neighbors
        return neighbors

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
