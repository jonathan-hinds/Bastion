from __future__ import annotations

from dataclasses import dataclass
import math
import random

import pygame

from bastion import config
from bastion.engine.drawing import draw_circle_alpha, draw_line_alpha
from bastion.engine import hover_feedback


@dataclass
class MineralDeposit:
    cell: tuple[int, int]
    amount: int = config.MINERAL_DEPOSIT_AMOUNT
    max_amount: int = config.MINERAL_DEPOSIT_AMOUNT
    respawn_time: float = 0.0
    kind: str = "mineral"
    display_name: str = "Minerals"
    resource_suffix: str = "M"
    inverted_colors: bool = False
    radius = config.TILE_SIZE * 0.58

    def __post_init__(self) -> None:
        self.pos = pygame.Vector2(0, 0)
        self.active = self.amount > 0
        self.harvest_enabled = False
        self.phase = random.random() * math.tau
        self.claimed_by = None

    def place(self, cell: tuple[int, int], grid) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.amount = self.max_amount
        self.respawn_time = 0.0
        self.active = True
        self.phase = random.random() * math.tau

    def deplete(self) -> None:
        self.amount = 0
        self.active = False
        self.respawn_time = random.uniform(config.MINERAL_RESPAWN_MIN, config.MINERAL_RESPAWN_MAX)

    def harvest(self, amount: int) -> int:
        if not self.active or amount <= 0:
            return 0
        taken = min(amount, self.amount)
        self.amount -= taken
        if self.amount <= 0:
            self.deplete()
        return taken

    def update(self, dt: float, game) -> None:
        if self.active:
            return
        self.respawn_time = max(0.0, self.respawn_time - dt)
        if self.respawn_time <= 0:
            respawn = getattr(game, "respawn_resource_deposit", None) or game.respawn_mineral_deposit
            respawn(self)

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, selected: bool = False) -> None:
        if not self.active:
            return
        screen = camera.world_to_screen(self.pos, viewport)
        if not viewport.inflate(40, 40).collidepoint(screen.x, screen.y):
            return

        zoom = camera.zoom
        r = max(5, int(self.radius * zoom))
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.003 + self.phase)
        enabled = bool(getattr(self, "harvest_enabled", False))
        draw_circle_alpha(surface, screen, r * (0.8 + pulse * (0.08 if enabled else 0.03)), config.PALETTE.white, 22 if enabled else 10, 1)

        stones = (
            (-0.42, 0.10, 0.38),
            (-0.08, -0.28, 0.46),
            (0.32, 0.08, 0.34),
            (0.06, 0.34, 0.28),
        )
        stone_fill = config.PALETTE.white if self.inverted_colors else config.PALETTE.black
        stone_outline = config.PALETTE.black if self.inverted_colors else (config.PALETTE.white if enabled else config.PALETTE.line_bright)
        disabled_line = config.PALETTE.black if self.inverted_colors else config.PALETTE.line_bright
        for ox, oy, scale in stones:
            center = screen + pygame.Vector2(ox * r, oy * r)
            size = max(3, int(r * scale))
            points = [
                (center.x, center.y - size),
                (center.x + size * 0.85, center.y - size * 0.10),
                (center.x + size * 0.35, center.y + size * 0.80),
                (center.x - size * 0.70, center.y + size * 0.55),
                (center.x - size * 0.85, center.y - size * 0.20),
            ]
            pygame.draw.polygon(surface, stone_fill, points)
            pygame.draw.polygon(surface, stone_outline, points, max(1, int(zoom)))

        if not enabled:
            pygame.draw.line(surface, disabled_line, (screen.x - r * 0.7, screen.y - r * 0.7), (screen.x + r * 0.7, screen.y + r * 0.7), max(1, int(zoom)))
            pygame.draw.line(surface, disabled_line, (screen.x + r * 0.7, screen.y - r * 0.7), (screen.x - r * 0.7, screen.y + r * 0.7), max(1, int(zoom)))

        if selected:
            draw_circle_alpha(surface, screen, r + 7 * zoom, config.PALETTE.white, 54, 1)

        if self.amount < self.max_amount:
            bar = pygame.Rect(0, 0, max(14, int(r * 1.7)), max(2, int(3 * zoom)))
            bar.center = (screen.x, screen.y - r - 6)
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, self.amount / max(1, self.max_amount)))
            pygame.draw.rect(surface, config.PALETTE.white, fill)


class GoldDeposit(MineralDeposit):
    def __init__(self, cell: tuple[int, int]) -> None:
        super().__init__(
            cell,
            amount=config.GOLD_DEPOSIT_AMOUNT,
            max_amount=config.GOLD_DEPOSIT_AMOUNT,
            kind="gold",
            display_name="Gold",
            resource_suffix="G",
            inverted_colors=True,
        )


class ResourceHarvester:
    def __init__(
        self,
        owner,
        carry_capacity: int = 5,
        gather_rate: float = 2.1,
        gather_radius: float = 105.0,
        haul_speed_multiplier: float = 0.68,
    ) -> None:
        self.owner = owner
        self.carry_capacity = carry_capacity
        self.gather_rate = gather_rate
        self.gather_radius = gather_radius
        self.haul_speed_multiplier = haul_speed_multiplier
        self.current_capacity = carry_capacity
        self.cargo = 0
        self.cargo_kind: str | None = None
        self.progress = 0.0
        self.target_deposit: MineralDeposit | None = None
        self.last_deposit: MineralDeposit | None = None
        self.state = "idle"
        self.fx_timer = 0.0
        self.work_angle = random.random() * math.tau
        self.route_key = None
        self.route_index = 0

    def update(self, dt: float, game) -> None:
        self.fx_timer = max(0.0, self.fx_timer - dt)
        capacity = self.carry_capacity_for(game)
        self.current_capacity = capacity

        if self.cargo >= capacity:
            self._deliver(dt, game)
            return

        if self.cargo > 0 and not self._valid_deposit(self.target_deposit, game):
            self._deliver(dt, game)
            return

        deposit = self._find_deposit(game)
        if deposit is None:
            self.state = "idle"
            self.target_deposit = None
            self.progress = 0.0
            if self.owner.pos.distance_to(self.owner.station) > 8:
                self.owner._move_towards(self.owner.station, dt, game)
            else:
                self.owner._decelerate(dt)
            return

        self.target_deposit = deposit
        self.last_deposit = deposit
        gather_point = self._gather_point(game, deposit)
        gather_arrival = self._gather_arrival_radius()
        if not self._can_gather_from_here(game, deposit):
            self.state = "to_mine"
            self._move_along_extractor_route(
                dt,
                game,
                deposit,
                toward_deposit=True,
                arrival_radius=gather_arrival,
                speed_multiplier=self.work_speed_for(game),
            )
            return

        self.state = "gathering"
        self.owner._decelerate(dt)
        self.progress += self.gather_rate * dt
        available_space = capacity - self.cargo
        gathered = min(int(self.progress), available_space)
        if gathered <= 0:
            return
        self.progress -= gathered
        taken = deposit.harvest(gathered)
        self.cargo += taken
        if taken > 0:
            self.cargo_kind = deposit.kind
        self.owner.support_pulse = 0.22
        if self.fx_timer <= 0:
            self.fx_timer = 0.18
            game.spawn_hit(deposit.pos, 1)
            game.beams.append(_beam(deposit.pos, self.owner.pos))
        if self.cargo >= capacity:
            self.progress = 0.0

    def _deliver(self, dt: float, game) -> None:
        deposit = self.target_deposit or self.last_deposit
        core = self._delivery_core(game, deposit)
        distance = self.owner.pos.distance_to(core.pos)
        if distance > core.radius + self.owner.radius + 7:
            self.state = "hauling"
            self._move_along_extractor_route(
                dt,
                game,
                deposit,
                toward_deposit=False,
                arrival_radius=core.radius + self.owner.radius + 7,
                speed_multiplier=self.haul_speed_multiplier * self.work_speed_for(game),
            )
            return

        if self.cargo > 0:
            delivered = self.cargo
            cargo_kind = self.cargo_kind or getattr(deposit, "kind", "mineral")
            self.cargo = 0
            self.cargo_kind = None
            self.progress = 0.0
            if hasattr(game, "add_resource"):
                game.add_resource(cargo_kind, delivered, self.owner)
            else:
                game.add_minerals(delivered, self.owner)
            self.owner.support_pulse = 0.35
            game.spawn_hit(core.pos, 2)
        self.state = "returning"

    def _find_deposit(self, game) -> MineralDeposit | None:
        if self._valid_deposit(self.target_deposit, game):
            assert self.target_deposit is not None
            if self.target_deposit.pos.distance_to(self.owner.station) <= self.gather_radius + self.target_deposit.radius:
                return self.target_deposit
        find_deposit = getattr(game, "find_resource_near", None) or game.find_mineral_near
        return find_deposit(self.owner.station, self.gather_radius)

    def _valid_deposit(self, deposit: MineralDeposit | None, game) -> bool:
        if deposit is None or not deposit.active or deposit.amount <= 0:
            return False
        resource_is_connected = getattr(game, "resource_is_connected", None) or getattr(game, "mineral_is_connected", None)
        if callable(resource_is_connected):
            return bool(resource_is_connected(deposit))
        extractor = getattr(deposit, "claimed_by", None)
        return extractor is not None and getattr(extractor, "alive", False)

    def _delivery_core(self, game, deposit: MineralDeposit | None):
        route = self._extractor_route(game, deposit, toward_deposit=False)
        if route:
            extractor = getattr(deposit, "claimed_by", None)
            link = game.arcane_link_for(extractor) if extractor is not None and hasattr(game, "arcane_link_for") else None
            if link is not None and getattr(link.core, "alive", False):
                return link.core
        return game.core_target_for(self.owner.pos)

    def _move_along_extractor_route(
        self,
        dt: float,
        game,
        deposit: MineralDeposit | None,
        *,
        toward_deposit: bool,
        arrival_radius: float,
        speed_multiplier: float,
    ) -> None:
        route = self._extractor_route(game, deposit, toward_deposit)
        if not route:
            self.route_key = None
            self.route_index = 0
            target = self._gather_point(game, deposit) if toward_deposit and deposit is not None else game.core_target_for(self.owner.pos).pos
            self.owner._move_towards(
                target,
                dt,
                game,
                arrival_radius=arrival_radius,
                speed_multiplier=speed_multiplier,
                separation_strength=0.0,
            )
            return

        index = self._route_waypoint_index(route, deposit, toward_deposit)
        final_index = len(route) - 1
        waypoint = route[index]
        self.owner._move_towards(
            waypoint,
            dt,
            game,
            arrival_radius=arrival_radius if index == final_index else max(8.0, self.owner.radius * 1.25),
            speed_multiplier=speed_multiplier,
            separation_strength=0.0,
        )

    def _route_waypoint_index(self, route: list[pygame.Vector2], deposit: MineralDeposit | None, toward_deposit: bool) -> int:
        key = (id(deposit), toward_deposit, len(route))
        if self.route_key != key:
            self.route_key = key
            self.route_index = min(range(len(route)), key=lambda index: route[index].distance_to(self.owner.pos))

        nearest = min(
            range(self.route_index, len(route)),
            key=lambda index: route[index].distance_to(self.owner.pos),
        )
        if nearest > self.route_index:
            self.route_index = nearest

        reach = max(config.TILE_SIZE * 0.42, self.owner.radius * 2.0)
        while self.route_index < len(route) - 1 and self.owner.pos.distance_to(route[self.route_index]) <= reach:
            self.route_index += 1
        return self.route_index

    def _extractor_route(self, game, deposit: MineralDeposit | None, toward_deposit: bool) -> list[pygame.Vector2]:
        extractor = getattr(deposit, "claimed_by", None) if deposit is not None else None
        if extractor is None or not getattr(extractor, "alive", False):
            return []
        if hasattr(game, "has_arcane_power") and not game.has_arcane_power(extractor):
            return []
        link = game.arcane_link_for(extractor) if hasattr(game, "arcane_link_for") else None
        if link is None or not getattr(link.core, "alive", False) or not link.path:
            return []

        cells = link.path if toward_deposit else list(reversed(link.path))
        points = [game.grid.world_center(cell) for cell in cells]
        gather_point = self._gather_point(game, deposit)
        if toward_deposit:
            points[-1] = gather_point
        else:
            points[0] = gather_point
            points[-1] = pygame.Vector2(link.core.pos)
        return points

    def _gather_arrival_radius(self) -> float:
        return max(10.0, self.owner.radius * 1.35)

    def _can_gather_from_here(self, game, deposit: MineralDeposit) -> bool:
        work_point = self._gather_point(game, deposit)
        tolerance = max(28.0, self.owner.radius * 3.0)
        return self.owner.pos.distance_to(work_point) <= tolerance

    def _gather_point(self, game, deposit: MineralDeposit | None) -> pygame.Vector2:
        if deposit is None:
            return pygame.Vector2(self.owner.station)
        extractor = getattr(deposit, "claimed_by", None)
        center = pygame.Vector2(getattr(extractor, "pos", deposit.pos))
        extractor_radius = float(getattr(extractor, "radius", deposit.radius))
        ring_radius = extractor_radius + self.owner.radius + 8.0

        candidates: list[pygame.Vector2] = []
        for step in range(12):
            offset_index = (step + 1) // 2
            sign = -1 if step % 2 == 0 else 1
            angle = self.work_angle + sign * offset_index * (math.tau / 12.0)
            direction = pygame.Vector2(math.cos(angle), math.sin(angle))
            candidates.append(center + direction * ring_radius)

        for point in candidates:
            if game.grid.circle_clear(point, self.owner.radius):
                return point
        return game.grid.nearest_clear_world(candidates[0], self.owner.radius, max_radius=4)

    def carry_capacity_for(self, game) -> int:
        research = getattr(game, "research", None)
        multiplier = research.multiplier("grunt_carry_capacity") if research is not None else 1.0
        return max(1, math.ceil(self.carry_capacity * multiplier))

    def work_speed_for(self, game) -> float:
        research = getattr(game, "research", None)
        return research.multiplier("grunt_work_speed") if research is not None else 1.0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if self.target_deposit is not None and self.target_deposit.active:
            start = camera.world_to_screen(self.owner.pos, viewport)
            end = camera.world_to_screen(self.target_deposit.pos, viewport)
            draw_line_alpha(surface, start, end, config.PALETTE.white, 32, 1)


def _beam(start: pygame.Vector2, end: pygame.Vector2):
    from bastion.game.entities import Beam

    return Beam(pygame.Vector2(start), pygame.Vector2(end), 0.08, 1)


class MineralExtractor:
    kind = "extractor"
    display_name = "Extractor"
    target_class = "structure"
    radius = config.TILE_SIZE * 0.53
    max_health = 175.0

    def __init__(self, cell: tuple[int, int], grid, deposit: MineralDeposit) -> None:
        self.cell = cell
        self.pos = grid.world_center(cell)
        self.deposit = deposit
        self.health = self.max_health
        self.alive = True
        self.pulse = random.random() * math.tau
        self.deposit.claimed_by = self

    def update(self, dt: float, game) -> None:
        if self.deposit.cell != self.cell:
            self.deposit.cell = self.cell
            self.deposit.pos = game.grid.world_center(self.cell)

    def release_deposit(self) -> None:
        if getattr(self.deposit, "claimed_by", None) is self:
            self.deposit.claimed_by = None

    def take_damage(self, amount: float) -> bool:
        if not self.alive:
            return False
        self.health -= amount
        return self.health <= 0

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, selected: bool = False, hovered: bool = False) -> None:
        center = camera.world_to_screen(self.pos, viewport)
        tile = config.TILE_SIZE * camera.zoom * hover_feedback.hover_scale(hovered)
        size = int(tile * 0.88)
        rect = pygame.Rect(0, 0, size, size)
        rect.center = center
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.004 + self.pulse)
        fill, mark = hover_feedback.inverted_pair(hovered)
        if getattr(self.deposit, "kind", "") == "gold":
            fill, mark = mark, fill

        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, max(1, int(2 * camera.zoom)))
        inner = rect.inflate(-max(5, int(size * 0.34)), -max(5, int(size * 0.34)))
        pygame.draw.rect(surface, mark, inner, max(1, int(camera.zoom)))
        pygame.draw.line(surface, mark, rect.midleft, rect.midright, max(1, int(camera.zoom)))
        pygame.draw.line(surface, mark, rect.midtop, rect.midbottom, max(1, int(camera.zoom)))

        if self.deposit.active:
            draw_circle_alpha(surface, center, tile * (0.46 + pulse * 0.05), config.PALETTE.white, 34, 1)
        else:
            pygame.draw.line(surface, config.PALETTE.line_bright, rect.topleft, rect.bottomright, max(1, int(camera.zoom)))
            pygame.draw.line(surface, config.PALETTE.line_bright, rect.topright, rect.bottomleft, max(1, int(camera.zoom)))

        if selected:
            draw_circle_alpha(surface, center, tile * 0.72, config.PALETTE.white, 58, 1)

        if self.health < self.max_health:
            bar = pygame.Rect(rect.left, rect.top - 6, rect.width, max(2, int(3 * camera.zoom)))
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, self.health / self.max_health))
            pygame.draw.rect(surface, config.PALETTE.white, fill)

        if self.deposit.active and self.deposit.amount < self.deposit.max_amount:
            bar = pygame.Rect(rect.left, rect.bottom + 4, rect.width, max(2, int(3 * camera.zoom)))
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, self.deposit.amount / max(1, self.deposit.max_amount)))
            pygame.draw.rect(surface, config.PALETTE.white, fill)
