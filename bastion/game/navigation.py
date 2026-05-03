from __future__ import annotations

import random

import pygame

from bastion import config


class PathNavigator:
    def __init__(self, owner, radius_attr: str = "radius", repath_interval: float = 0.42) -> None:
        self.owner = owner
        self.radius_attr = radius_attr
        self.base_repath_interval = max(0.48, repath_interval)
        self.repath_timer = random.uniform(0.0, repath_interval)
        self.lookahead_timer = random.uniform(0.0, 0.10)
        self.path: list[pygame.Vector2] = []
        self.path_index = 0
        self.goal = pygame.Vector2(owner.pos)
        self.grid_version = -1
        self.last_pos = pygame.Vector2(owner.pos)
        self.stuck_time = 0.0
        self.separation_timer = random.uniform(0.0, 0.10)
        self.cached_separation = pygame.Vector2(0, 0)
        self.side_bias = random.choice((-1.0, 1.0)) * random.uniform(0.015, 0.055)

    def clear(self) -> None:
        self.path.clear()
        self.path_index = 0
        self.repath_timer = 0.0
        self.lookahead_timer = 0.0
        self.grid_version = -1
        self.stuck_time = 0.0
        self.separation_timer = 0.0
        self.cached_separation.update(0, 0)

    def steer_to(
        self,
        goal: pygame.Vector2 | tuple[float, float],
        dt: float,
        game,
        *,
        speed: float | None = None,
        acceleration: float | None = None,
        radius: float | None = None,
        arrival_radius: float = 10.0,
        neighbors: list | None = None,
        separation_strength: float = 0.55,
        max_velocity: float | None = None,
    ) -> None:
        if dt <= 0:
            return

        target = pygame.Vector2(goal)
        radius = float(radius if radius is not None else getattr(self.owner, self.radius_attr))
        speed = float(speed if speed is not None else getattr(self.owner, "speed"))
        acceleration = float(acceleration if acceleration is not None else getattr(self.owner, "acceleration"))

        self._update_stuck_timer(dt, target, arrival_radius)
        self._ensure_path(target, radius, dt, game.grid, arrival_radius)

        waypoint = self._current_waypoint(radius, game.grid, dt)
        to_waypoint = waypoint - self.owner.pos
        to_goal = target - self.owner.pos
        if to_waypoint.length_squared() == 0:
            desired_dir = pygame.Vector2(0, 0)
        else:
            desired_dir = to_waypoint.normalize()

        avoid = game.grid.obstacle_avoidance_from_world(self.owner.pos, radius)
        separation = self._separation_for_frame(neighbors, radius, dt)
        if desired_dir.length_squared() > 0:
            tangent = pygame.Vector2(-desired_dir.y, desired_dir.x)
            desired_dir += tangent * self.side_bias
        desired_dir += avoid * 0.90 + separation * separation_strength

        if desired_dir.length_squared() > 0:
            desired_dir = desired_dir.normalize()

        desired_speed = speed
        distance_to_goal = to_goal.length()
        if distance_to_goal < arrival_radius * 3.2:
            desired_speed *= max(0.20, min(1.0, distance_to_goal / max(1.0, arrival_radius * 3.2)))

        desired_velocity = desired_dir * desired_speed
        steering = desired_velocity - self.owner.vel
        max_steering = acceleration * dt
        if steering.length() > max_steering:
            steering.scale_to_length(max_steering)
        self.owner.vel += steering

        velocity_limit = max_velocity if max_velocity is not None else speed * 1.18
        if self.owner.vel.length() > velocity_limit:
            self.owner.vel.scale_to_length(velocity_limit)

    def steer_direction(
        self,
        direction: pygame.Vector2 | tuple[float, float],
        dt: float,
        game,
        *,
        goal: pygame.Vector2 | tuple[float, float] | None = None,
        speed: float | None = None,
        acceleration: float | None = None,
        radius: float | None = None,
        neighbors: list | None = None,
        separation_strength: float = 0.55,
        max_velocity: float | None = None,
    ) -> None:
        if dt <= 0:
            return

        desired_dir = pygame.Vector2(direction)
        if desired_dir.length_squared() == 0:
            return

        radius = float(radius if radius is not None else getattr(self.owner, self.radius_attr))
        speed = float(speed if speed is not None else getattr(self.owner, "speed"))
        acceleration = float(acceleration if acceleration is not None else getattr(self.owner, "acceleration"))
        goal_point = pygame.Vector2(goal) if goal is not None else self.owner.pos + desired_dir
        self._update_stuck_timer(dt, goal_point, max(radius * 2.0, 10.0))

        desired_dir = desired_dir.normalize()
        avoid = game.grid.obstacle_avoidance_from_world(self.owner.pos, radius)
        separation = self._separation_for_frame(neighbors, radius, dt)
        tangent = pygame.Vector2(-desired_dir.y, desired_dir.x)
        desired_dir += tangent * self.side_bias + avoid * 0.90 + separation * separation_strength
        if desired_dir.length_squared() > 0:
            desired_dir = desired_dir.normalize()

        desired_velocity = desired_dir * speed
        steering = desired_velocity - self.owner.vel
        max_steering = acceleration * dt
        if steering.length() > max_steering:
            steering.scale_to_length(max_steering)
        self.owner.vel += steering

        velocity_limit = max_velocity if max_velocity is not None else speed * 1.18
        if self.owner.vel.length() > velocity_limit:
            self.owner.vel.scale_to_length(velocity_limit)

    def _ensure_path(self, goal: pygame.Vector2, radius: float, dt: float, grid, arrival_radius: float) -> None:
        self.repath_timer -= dt
        timer_expired = self.repath_timer <= 0
        goal_repath_distance = max(config.TILE_SIZE * 0.85, arrival_radius * 0.75)
        goal_moved = goal.distance_to(self.goal) > goal_repath_distance
        if goal_moved and self.path and self.path[-1].distance_to(goal) <= max(arrival_radius * 0.85, config.TILE_SIZE * 0.55):
            goal_moved = False
        grid_changed = self.grid_version != grid.nav_version
        should_validate = timer_expired or grid_changed or self.stuck_time > 0.55
        blocked_waypoint = bool(
            should_validate
            and self.path
            and not grid.line_clear(self.owner.pos, self.path[min(self.path_index, len(self.path) - 1)], radius)
        )
        needs_path = (
            not self.path
            or self.path_index >= len(self.path)
            or goal_moved
            or grid_changed
            or self.stuck_time > 0.55
            or blocked_waypoint
        )
        if not needs_path:
            if timer_expired:
                self.repath_timer = self.base_repath_interval * random.uniform(0.90, 1.35)
                if grid.line_clear(self.owner.pos, goal, radius):
                    self.path = [pygame.Vector2(goal)]
                    self.path_index = 0
            return

        self.goal = pygame.Vector2(goal)
        self.grid_version = grid.nav_version
        self.repath_timer = self.base_repath_interval * random.uniform(0.85, 1.25)
        self.stuck_time = 0.0

        if grid.line_clear(self.owner.pos, goal, radius):
            self.path = [pygame.Vector2(goal)]
            self.path_index = 0
            return

        path = grid.find_path(self.owner.pos, goal, radius)
        if not path:
            self.path = [grid.nearest_clear_world(goal, radius)]
            self.path_index = 0
            return

        self.path = [point for point in path[1:] if point.distance_to(self.owner.pos) > max(2.0, radius * 0.35)]
        if not self.path:
            self.path = [pygame.Vector2(path[-1])]
        self.path_index = 0

    def _current_waypoint(self, radius: float, grid, dt: float) -> pygame.Vector2:
        if not self.path:
            return pygame.Vector2(self.goal)

        reach = max(radius * 1.4, 8.0)
        while self.path_index < len(self.path) - 1 and self.owner.pos.distance_to(self.path[self.path_index]) <= reach:
            self.path_index += 1

        self.lookahead_timer -= dt
        if self.lookahead_timer > 0:
            return pygame.Vector2(self.path[self.path_index])

        self.lookahead_timer = random.uniform(0.08, 0.14)
        furthest = self.path_index
        lookahead_end = min(len(self.path) - 1, self.path_index + 4)
        for index in range(lookahead_end, self.path_index, -1):
            if grid.line_clear(self.owner.pos, self.path[index], radius):
                furthest = index
                break
        self.path_index = furthest
        return pygame.Vector2(self.path[self.path_index])

    def _update_stuck_timer(self, dt: float, goal: pygame.Vector2, arrival_radius: float) -> None:
        moved = self.owner.pos.distance_to(self.last_pos)
        wants_movement = self.owner.pos.distance_to(goal) > arrival_radius * 1.25
        if wants_movement and moved < 0.55:
            self.stuck_time += dt
        else:
            self.stuck_time = max(0.0, self.stuck_time - dt * 2.0)
        self.last_pos = pygame.Vector2(self.owner.pos)

    def _separation_for_frame(self, neighbors, radius: float, dt: float) -> pygame.Vector2:
        if neighbors is None:
            self.cached_separation.update(0, 0)
            return self.cached_separation
        self.separation_timer -= dt
        if self.separation_timer <= 0:
            self.separation_timer = random.uniform(0.07, 0.13)
            source = neighbors() if callable(neighbors) else neighbors
            self.cached_separation = self._separation(source, radius)
        return self.cached_separation

    def _separation(self, neighbors: list, radius: float) -> pygame.Vector2:
        push = pygame.Vector2(0, 0)
        checked = 0
        for other in neighbors:
            if other is self.owner or not getattr(other, "alive", True):
                continue
            other_pos = getattr(other, "pos", None)
            if other_pos is None:
                continue
            other_radius = float(getattr(other, "collision_radius", getattr(other, "radius", radius)))
            desired_gap = radius + other_radius + 4.0
            delta = self.owner.pos - other_pos
            distance_sq = delta.length_squared()
            if distance_sq == 0:
                delta = pygame.Vector2(random.uniform(-1.0, 1.0), random.uniform(-1.0, 1.0))
                distance_sq = delta.length_squared()
            if distance_sq > desired_gap * desired_gap:
                continue
            distance = max(0.001, distance_sq**0.5)
            push += delta.normalize() * ((desired_gap - distance) / desired_gap)
            checked += 1
            if checked >= 8:
                break
        if push.length_squared() > 1:
            push.scale_to_length(1)
        return push
