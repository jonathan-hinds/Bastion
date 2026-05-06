from __future__ import annotations

from dataclasses import dataclass, field
import math
import random

import pygame

from bastion import config
from bastion.engine.camera import Camera
from bastion.engine.drawing import draw_circle_alpha, draw_line_alpha, draw_rect_alpha
from bastion.game.abilities import AbilitySystemComponent, create_boss_ability_from_definition
from bastion.game.combat_stats import ATTRIBUTE_ORDER
from bastion.game.elements import ElementalEffect, damage_multiplier, healing_multiplier
from bastion.game.entities import Beam, DamagePulse, Enemy, FloatingText, Particle
from bastion.game.enemy_defs import get_enemy_def
from bastion.game.expedition_defs import ExpeditionDefinition, default_expedition_definition
from bastion.game.fog import FogOfWar, VisionProfile, VisionSource
from bastion.game.grid import GameGrid
from bastion.game.items import DroppedItem, ITEM_DEFINITIONS, random_drop_item_id
from bastion.game.units import Troop


PARTY_MOVE_SPEED = 128.0
PARTY_FORMATION_RADIUS = 42.0
WHISP_IDLE_INTERVAL = 0.085
WHISP_MOVING_INTERVAL = 0.075
EXPEDITION_TILE_SIZE = max(8, config.TILE_SIZE // 2)
EXPEDITION_LAYOUT_SCALE = max(1, int(round(config.TILE_SIZE / EXPEDITION_TILE_SIZE)))
EXPEDITION_WHISP_SCALE = 0.68

EXPEDITION_METRIC_OPTIONS: tuple[str, ...] = (
    "damage_done",
    "damage_taken",
    "healing_done",
    "dps",
    "hps",
    "criticals",
    "stuns",
    "abilities_fired",
    "aggro",
    "blocks",
    "kills",
    "deaths",
)

EXPEDITION_METRIC_LABELS: dict[str, str] = {
    "damage_done": "Damage Done",
    "damage_taken": "Damage Taken",
    "healing_done": "Healing Done",
    "dps": "DPS",
    "hps": "HPS",
    "criticals": "Criticals",
    "stuns": "Stuns",
    "abilities_fired": "Abilities Fired",
    "aggro": "Aggro",
    "blocks": "Blocks",
    "kills": "Kills",
    "deaths": "Deaths",
}


@dataclass
class ExpeditionTroopMetrics:
    troop: Troop
    slot_index: int
    damage_done: float = 0.0
    damage_taken: float = 0.0
    healing_done: float = 0.0
    aggro: float = 0.0
    blocks: float = 0.0
    criticals: int = 0
    stuns: int = 0
    kills: int = 0
    deaths: int = 0
    ability_counts: dict[str, int] = field(default_factory=dict)

    @property
    def abilities_fired(self) -> int:
        return sum(self.ability_counts.values())

    def ability_summary(self) -> list[str]:
        if not self.ability_counts:
            return ["No abilities fired"]
        pairs = sorted(self.ability_counts.items(), key=lambda item: (-item[1], item[0]))
        return [f"{name}: {count}" for name, count in pairs[:5]]


class ExpeditionDustCloud:
    def __init__(self, pos: pygame.Vector2, vel: pygame.Vector2, life: float, radius: float) -> None:
        self.pos = pygame.Vector2(pos)
        self.vel = pygame.Vector2(vel)
        self.life = float(life)
        self.max_life = max(0.001, float(life))
        self.radius = float(radius)

    def update(self, dt: float) -> None:
        self.pos += self.vel * dt
        self.vel *= 0.84 ** (dt * 60)
        self.life -= dt

    def draw(self, surface: pygame.Surface, camera: Camera, viewport: pygame.Rect) -> None:
        if self.life <= 0:
            return
        t = max(0.0, self.life / self.max_life)
        screen = camera.world_to_screen(self.pos, viewport)
        radius = self.radius * (1.1 + (1.0 - t) * 0.9) * camera.zoom
        draw_circle_alpha(surface, screen, radius, config.PALETTE.white, int(58 * t), 1)


@dataclass(frozen=True)
class ExpeditionPartySnapshot:
    troop: Troop
    original_pos: pygame.Vector2
    original_station: pygame.Vector2
    original_attack_enabled: bool
    original_harvester: object | None
    slot_index: int


@dataclass(frozen=True)
class ExpeditionResult:
    victory: bool
    reason: str
    definition_name: str
    boss_name: str
    party: tuple[Troop, ...]
    gold: int
    items: tuple[str, ...]
    xp_by_troop_id: dict[int, int]
    dead_troop_ids: frozenset[int]


@dataclass(frozen=True)
class DungeonRoom:
    rect: pygame.Rect
    room_type: str
    tile_size: int = config.TILE_SIZE

    @property
    def center(self) -> pygame.Vector2:
        return pygame.Vector2(self.rect.centerx * self.tile_size, self.rect.centery * self.tile_size)


@dataclass(frozen=True)
class DungeonLayout:
    grid: GameGrid
    rooms: tuple[DungeonRoom, ...]
    start_room: DungeonRoom
    boss_room: DungeonRoom
    floor_cells: frozenset[tuple[int, int]]
    exit_cell: tuple[int, int]


class ExpeditionHazard:
    def __init__(self, owner, pos: pygame.Vector2, radius: float, duration: float, dps: float, element: str = "fire") -> None:
        self.owner = owner
        self.pos = pygame.Vector2(pos)
        self.radius = float(radius)
        self.duration = max(0.1, float(duration))
        self.life = self.duration
        self.dps = float(dps)
        self.element = element
        self.fx_timer = random.uniform(0.0, 0.18)
        self.alive = True

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.life -= dt
        if self.life <= 0:
            self.alive = False
            return
        for troop in game.nearby_troops(self.pos, self.radius + 24.0):
            if not troop.alive or troop.pos.distance_to(self.pos) > self.radius + troop.radius:
                continue
            game.damage_friendly(troop, self.dps * dt, source_pos=self.pos, element=self.element, source=self.owner)
            if self.element == "fire" and hasattr(troop, "apply_burn"):
                troop.apply_burn(max(1.0, self.dps * 0.45), 1.2, self.owner)
        self.fx_timer -= dt
        if self.fx_timer <= 0:
            self.fx_timer = 0.32
            game.spawn_hit(self.pos + pygame.Vector2(random.uniform(-self.radius, self.radius), random.uniform(-self.radius, self.radius)) * 0.45, 1)

    def draw(self, surface: pygame.Surface, camera: Camera, viewport: pygame.Rect) -> None:
        if not self.alive:
            return
        screen = camera.world_to_screen(self.pos, viewport)
        t = max(0.0, self.life / self.duration)
        alpha = int(18 + 40 * t)
        draw_circle_alpha(surface, screen, self.radius * camera.zoom, config.PALETTE.white, alpha, 1)
        for index in range(6):
            angle = index / 6 * math.tau + pygame.time.get_ticks() * 0.004
            point = screen + pygame.Vector2(math.cos(angle), math.sin(angle)) * self.radius * camera.zoom * 0.48
            pygame.draw.circle(surface, config.PALETTE.white, point, max(1, int(2.0 * camera.zoom)))


class ExpeditionBossEnemy(Enemy):
    def __init__(self, boss_def, pos: pygame.Vector2) -> None:
        super().__init__(boss_def.enemy_kind, pygame.Vector2(pos), 0, behavior="expedition", spawn_group="boss")
        data = get_enemy_def(boss_def.enemy_kind)
        self.boss_id = boss_def.boss_id
        self.display_name = boss_def.name
        self.element = boss_def.element
        self.max_health = float(data["health"])
        self.health = self.max_health
        self.reward = int(data["reward"])
        self.abilities = AbilitySystemComponent(self)
        has_dash = False
        for spec in boss_def.abilities:
            ability = create_boss_ability_from_definition(spec, self)
            has_dash = has_dash or ability.__class__.__name__ == "BossDashAbility"
            self.abilities.add(ability)
        if not has_dash:
            from bastion.game.abilities import BossDashAbility

            self.abilities.add(BossDashAbility(self, distance=150.0, cooldown=6.25, trigger_distance=135.0, chance=0.42))

    def update(self, dt: float, game) -> None:
        self.abilities.update(dt, game)
        super().update(dt, game)
        if self.alive:
            self._boss_personality_motion(dt, game)

    def _boss_personality_motion(self, dt: float, game) -> None:
        target = game.find_enemy_attack_target(self.pos)
        if target is None:
            return
        offset = self.pos - target.pos
        if offset.length_squared() == 0:
            return
        distance = offset.length()
        desired = pygame.Vector2(0, 0)
        if distance < self.attack_range * 0.55:
            desired += offset.normalize()
        elif distance > self.attack_range * 0.92:
            desired -= offset.normalize() * 0.38
        tangent = pygame.Vector2(-offset.y, offset.x)
        if tangent.length_squared() > 0:
            desired += tangent.normalize() * math.sin(pygame.time.get_ticks() * 0.002 + self.phase) * 0.32
        if desired.length_squared() > 0:
            self.vel += desired.normalize() * self.acceleration * 0.16 * dt


class ExpeditionDungeonGenerator:
    def __init__(self, definition: ExpeditionDefinition, rng: random.Random) -> None:
        self.definition = definition
        self.rng = rng
        self.dungeon = definition.dungeon
        self.tile_size = EXPEDITION_TILE_SIZE
        self.scale = EXPEDITION_LAYOUT_SCALE
        self.width = self.dungeon.width * self.scale
        self.height = self.dungeon.height * self.scale
        self.room_min_size = self._scaled(self.dungeon.room_min_size)
        self.room_max_size = self._scaled(self.dungeon.room_max_size)
        self.hallway_half_width = max(1, self.dungeon.hallway_half_width * self.scale)
        self.margin = self._scaled(3)
        self.floor: set[tuple[int, int]] = set()
        self.rooms: list[DungeonRoom] = []

    def generate(self) -> DungeonLayout:
        main_rooms = self._main_room_chain()
        side_rooms = self._side_rooms(main_rooms)
        self.rooms = main_rooms + side_rooms
        for room in self.rooms:
            self._carve_room(room.rect)
        for left, right in zip(main_rooms, main_rooms[1:]):
            self._carve_corridor(left.rect.center, right.rect.center)
        for side in side_rooms:
            anchor = min(main_rooms, key=lambda room: _cell_distance(room.rect.center, side.rect.center))
            self._carve_corridor(anchor.rect.center, side.rect.center)
        self._add_floor_detail()

        grid = GameGrid(self.width, self.height, self.tile_size, procedural_terrain=False)
        grid.townhall_cell = main_rooms[0].rect.center
        walls = {
            (x, y)
            for x in range(self.width)
            for y in range(self.height)
            if (x, y) not in self.floor
        }
        grid.walls = walls
        grid.wall_health = {cell: grid.wall_max_health for cell in walls}
        grid.towers = {}
        grid.nav_version += 1
        start = main_rooms[0]
        boss = main_rooms[-1]
        exit_cell = (boss.rect.right - 2, boss.rect.centery)
        return DungeonLayout(grid, tuple(self.rooms), start, boss, frozenset(self.floor), exit_cell)

    def _scaled(self, value: int | float) -> int:
        return max(1, int(round(value * self.scale)))

    def _main_room_chain(self) -> list[DungeonRoom]:
        count = self.dungeon.main_rooms
        rooms: list[DungeonRoom] = []
        min_size = self.room_min_size
        max_size = self.room_max_size
        for index in range(count):
            progress = index / max(1, count - 1)
            width = self.rng.randint(min_size, max_size)
            height = self.rng.randint(min_size, max_size)
            if index == 0:
                width, height = self._scaled(11), self._scaled(11)
            elif index == count - 1:
                width, height = self._scaled(17), self._scaled(15)
            x = int(self._scaled(4) + progress * (self.width - self._scaled(24)))
            y_center = self.height // 2 + int(math.sin(progress * math.tau * 1.35) * self._scaled(13)) + self.rng.randint(-self._scaled(5), self._scaled(5))
            x = max(self.margin, min(self.width - width - self.margin, x))
            y = max(self.margin, min(self.height - height - self.margin, y_center - height // 2))
            room_type = "start" if index == 0 else ("boss" if index == count - 1 else self._main_room_type(index))
            rooms.append(DungeonRoom(pygame.Rect(x, y, width, height), room_type, self.tile_size))
        return rooms

    def _main_room_type(self, index: int) -> str:
        cycle = ("combat", "loot", "trap", "large_combat")
        return cycle[(index - 1) % len(cycle)]

    def _side_rooms(self, main_rooms: list[DungeonRoom]) -> list[DungeonRoom]:
        rooms: list[DungeonRoom] = []
        anchors = main_rooms[1:-1] or main_rooms
        for index in range(self.dungeon.side_rooms):
            anchor = self.rng.choice(anchors)
            width = self.rng.randint(self._scaled(6), max(self._scaled(7), self.room_min_size + self._scaled(2)))
            height = self.rng.randint(self._scaled(6), max(self._scaled(7), self.room_min_size + self._scaled(2)))
            direction = self.rng.choice((pygame.Vector2(0, -1), pygame.Vector2(0, 1), pygame.Vector2(-1, 0), pygame.Vector2(1, 0)))
            center = pygame.Vector2(anchor.rect.center) + direction * self.rng.randint(self._scaled(9), self._scaled(15))
            x = max(self.margin, min(self.width - width - self.margin, int(center.x - width // 2)))
            y = max(self.margin, min(self.height - height - self.margin, int(center.y - height // 2)))
            room_type = "loot" if index % 2 == 0 else self.rng.choice(("combat", "trap"))
            rooms.append(DungeonRoom(pygame.Rect(x, y, width, height), room_type, self.tile_size))
        return rooms

    def _carve_room(self, rect: pygame.Rect) -> None:
        for x in range(rect.left, rect.right):
            for y in range(rect.top, rect.bottom):
                self._add_floor_cell((x, y))
        self._carve_room_edge_noise(rect)

    def _carve_corridor(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        sx, sy = start
        ex, ey = end
        if self.rng.random() < 0.5 and abs(ex - sx) > self._scaled(8):
            bend_x = self.rng.randint(min(sx, ex) + self._scaled(3), max(sx, ex) - self._scaled(3))
            points = [(sx, sy), (bend_x, sy), (bend_x, ey), (ex, ey)]
        elif abs(ey - sy) > self._scaled(8):
            bend_y = self.rng.randint(min(sy, ey) + self._scaled(3), max(sy, ey) - self._scaled(3))
            points = [(sx, sy), (sx, bend_y), (ex, bend_y), (ex, ey)]
        elif self.rng.random() < 0.5:
            points = [(sx, sy), (ex, sy), (ex, ey)]
        else:
            points = [(sx, sy), (sx, ey), (ex, ey)]
        for left, right in zip(points, points[1:]):
            self._carve_hall_line(left, right)

    def _carve_hall_line(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        sx, sy = start
        ex, ey = end
        dx = 0 if ex == sx else (1 if ex > sx else -1)
        dy = 0 if ey == sy else (1 if ey > sy else -1)
        x, y = sx, sy
        half = self.hallway_half_width
        while (x, y) != (ex, ey):
            self._carve_hall_cell(x, y, half)
            if self.rng.random() < 0.075:
                self._carve_hall_alcove(x, y, dx, dy, half)
            x += dx
            y += dy
        self._carve_hall_cell(ex, ey, half)

    def _carve_hall_cell(self, x: int, y: int, half: int) -> None:
        for ox in range(-half, half + 1):
            for oy in range(-half, half + 1):
                self._add_floor_cell((x + ox, y + oy))

    def _carve_room_edge_noise(self, rect: pygame.Rect) -> None:
        min_span = max(2, self.scale)
        max_span = max(min_span, self._scaled(4))
        max_depth = max(1, self.scale + 1)
        for side in ("top", "bottom"):
            passes = max(2, rect.width // self._scaled(5))
            for _ in range(passes):
                if self.rng.random() > 0.42:
                    continue
                span = self.rng.randint(min_span, max_span)
                depth = self.rng.randint(1, max_depth)
                start_x = self.rng.randint(rect.left + 1, max(rect.left + 1, rect.right - span - 1))
                y_base = rect.top - 1 if side == "top" else rect.bottom
                y_step = -1 if side == "top" else 1
                for ox in range(span):
                    for d in range(depth):
                        self._add_floor_cell((start_x + ox, y_base + y_step * d))
        for side in ("left", "right"):
            passes = max(2, rect.height // self._scaled(5))
            for _ in range(passes):
                if self.rng.random() > 0.42:
                    continue
                span = self.rng.randint(min_span, max_span)
                depth = self.rng.randint(1, max_depth)
                start_y = self.rng.randint(rect.top + 1, max(rect.top + 1, rect.bottom - span - 1))
                x_base = rect.left - 1 if side == "left" else rect.right
                x_step = -1 if side == "left" else 1
                for oy in range(span):
                    for d in range(depth):
                        self._add_floor_cell((x_base + x_step * d, start_y + oy))

    def _carve_hall_alcove(self, x: int, y: int, dx: int, dy: int, half: int) -> None:
        if dx == 0 and dy == 0:
            return
        if dx != 0:
            perp = (0, self.rng.choice((-1, 1)))
            along = (dx, 0)
        else:
            perp = (self.rng.choice((-1, 1)), 0)
            along = (0, dy)
        depth = self.rng.randint(1, max(1, self.scale + 1))
        width = self.rng.randint(0, 1)
        for step in range(half + 1, half + depth + 1):
            base = (x + perp[0] * step, y + perp[1] * step)
            for offset in range(-width, width + 1):
                self._add_floor_cell((base[0] + along[0] * offset, base[1] + along[1] * offset))

    def _add_floor_detail(self) -> None:
        edge_sources = list(self.floor)
        if not edge_sources:
            return
        attempts = max(28, len(edge_sources) // 30)
        directions = ((1, 0), (-1, 0), (0, 1), (0, -1))
        for _ in range(attempts):
            x, y = self.rng.choice(edge_sources)
            ordered = list(directions)
            self.rng.shuffle(ordered)
            for dx, dy in ordered:
                if (x + dx, y + dy) in self.floor:
                    continue
                depth = self.rng.randint(1, max(1, self.scale + 1))
                width = self.rng.randint(0, 1)
                if dx != 0:
                    side = (0, 1)
                else:
                    side = (1, 0)
                for step in range(1, depth + 1):
                    for offset in range(-width, width + 1):
                        self._add_floor_cell((x + dx * step + side[0] * offset, y + dy * step + side[1] * offset))
                break

    def _add_floor_cell(self, cell: tuple[int, int]) -> None:
        if 1 <= cell[0] < self.width - 1 and 1 <= cell[1] < self.height - 1:
            self.floor.add(cell)


class ExpeditionRun:
    def __init__(
        self,
        main_state,
        party: list[Troop],
        definition: ExpeditionDefinition | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.main_state = main_state
        self.definition = definition or default_expedition_definition()
        self.rng = rng or random.Random(random.randrange(1, 9999999) + self.definition.seed_salt)
        self.layout = ExpeditionDungeonGenerator(self.definition, self.rng).generate()
        self.grid = self.layout.grid
        self.fog = FogOfWar(self.grid)
        self.camera = Camera(self.grid.world_size)
        self.party_snapshots = tuple(
            ExpeditionPartySnapshot(troop, pygame.Vector2(troop.pos), pygame.Vector2(troop.station), bool(troop.attack_enabled), getattr(troop, "harvester", None), index)
            for index, troop in enumerate(party)
        )
        self.party = tuple(snapshot.troop for snapshot in self.party_snapshots)
        self.troops = list(self.party)
        self.towers: list[object] = []
        self.buildings: list[object] = []
        self.enemy_stat_budget = self._party_average_stat_budget()
        self.metrics_elapsed = 0.0
        self.metrics_by_troop_id = {
            id(snapshot.troop): ExpeditionTroopMetrics(snapshot.troop, snapshot.slot_index)
            for snapshot in self.party_snapshots
        }
        self.enemies: list[Enemy] = []
        self.projectiles = []
        self.enemy_projectiles = []
        self.ability_zones = []
        self.hazards: list[ExpeditionHazard] = []
        self.dropped_items: list[DroppedItem] = []
        self.particles: list[Particle] = []
        self.dust_clouds: list[ExpeditionDustCloud] = []
        self.damage_pulses: list[DamagePulse] = []
        self.beams: list[Beam] = []
        self.texts: list[FloatingText] = []
        self.active_item_buffs = getattr(main_state, "active_item_buffs", [])
        self.research = getattr(main_state, "research", None)
        self.expedition_movement_authoritative = True
        self.expedition_player_moving = False
        self.spatial_cell_size = 64
        self._enemy_bins: dict[tuple[int, int], list[Enemy]] = {}
        self._troop_bins: dict[tuple[int, int], list[Troop]] = {}
        self._spatial_ready = False
        self.party_center = self.layout.start_room.center
        self.facing_angle = -math.pi / 2
        self.whisp_emit_timer = 0.0
        self.whisp_motion_intensity = 0.0
        self.spawned_rooms: set[int] = set()
        self.reward_gold = 0
        self.reward_items: list[str] = []
        self.pending_xp: dict[int, int] = {}
        self.boss_def = self._choose_boss()
        self.boss: ExpeditionBossEnemy | None = None
        self.phase = "explore"
        self.phase_timer = 0.0
        self.boss_wave_index = 0
        self.finished_result: ExpeditionResult | None = None
        self.return_button_rect = pygame.Rect(0, 0, 180, 38)
        self.core_target = self.party[0] if self.party else None
        self._place_party_at_start()
        self.update_fog(0.0, immediate=True)

    @property
    def alive_troops(self) -> list[Troop]:
        return [troop for troop in self.troops if troop.alive]

    def find_troop_at(self, world: pygame.Vector2) -> Troop | None:
        point = pygame.Vector2(world)
        candidates = [
            troop
            for troop in self.troops
            if troop.alive and troop.pos.distance_to(point) <= max(18.0, troop.radius * 2.2)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda troop: troop.pos.distance_to(point))

    def _party_average_stat_budget(self) -> float:
        if not self.party:
            return 25.0
        totals = []
        for troop in self.party:
            attributes = troop.effective_attributes() if hasattr(troop, "effective_attributes") else getattr(troop, "attributes", None)
            if attributes is None:
                continue
            totals.append(sum(int(getattr(attributes, key, 0)) for key in ATTRIBUTE_ORDER))
        if not totals:
            return 25.0
        return sum(totals) / len(totals)

    def _choose_boss(self):
        bosses = self.definition.bosses
        total = sum(max(0.0, boss.weight) for boss in bosses)
        if total <= 0:
            return bosses[0]
        roll = self.rng.uniform(0.0, total)
        cursor = 0.0
        for boss in bosses:
            cursor += max(0.0, boss.weight)
            if roll <= cursor:
                return boss
        return bosses[-1]

    def _place_party_at_start(self) -> None:
        self.party_center = self.layout.start_room.center
        for index, troop in enumerate(self.party):
            offset = _formation_offsets(len(self.party), PARTY_FORMATION_RADIUS)[index]
            troop.pos = self.party_center + offset
            troop.station = pygame.Vector2(troop.pos)
            troop.vel.update(0, 0)
            troop.target = None
            troop.attack_enabled = True
            if hasattr(troop, "harvester"):
                troop.harvester = None
            if getattr(troop, "navigator", None) is not None:
                troop.navigator.clear()

    def handle_event(self, event: pygame.event.Event, viewport: pygame.Rect) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.phase == "defeat":
            if self.return_button_rect.collidepoint(event.pos):
                self.finished_result = self._build_result(False, "Party defeated")
                return True
        if event.type == pygame.MOUSEBUTTONDOWN and viewport.collidepoint(event.pos):
            if event.button == 1:
                world = self.camera.screen_to_world(event.pos, viewport)
                troop = self.find_troop_at(world)
                if troop is not None and hasattr(self.main_state, "select_troop"):
                    self.main_state.select_troop(troop)
                elif hasattr(self.main_state, "clear_selection"):
                    self.main_state.clear_selection()
                return True
            if event.button in (2, 3):
                return True
        return False

    def close_as_loss(self) -> None:
        for troop in self.alive_troops:
            self.damage_friendly(troop, troop.health + troop.max_health, source=None, source_pos=troop.pos)
        self.finished_result = self._build_result(False, "Expedition abandoned")

    def update(self, dt: float, keys, mouse_pos: tuple[int, int], viewport: pygame.Rect) -> None:
        if self.finished_result is not None:
            return
        dt = min(0.05, dt)
        self._update_fx(dt)
        if hasattr(self.main_state, "update_item_buffs"):
            self.main_state.update_item_buffs(dt)
            self.active_item_buffs = getattr(self.main_state, "active_item_buffs", self.active_item_buffs)
        if self.phase == "defeat":
            return
        self.metrics_elapsed += dt
        self._update_party_control(dt, keys, mouse_pos, viewport)
        self.update_fog(dt)
        self.rebuild_spatial_index()
        self._trigger_rooms()
        self._update_boss_phase(dt)

        for zone in list(self.ability_zones):
            zone.update(dt, self)
        for hazard in list(self.hazards):
            hazard.update(dt, self)
        for dropped_item in list(self.dropped_items):
            dropped_item.update(dt, self)
        for troop in list(self.troops):
            if troop.alive:
                previous_pos = pygame.Vector2(troop.pos)
                troop.update(dt, self)
                self._emit_motion_dust(previous_pos, troop.pos, troop.radius)
        for enemy in list(self.enemies):
            previous_pos = pygame.Vector2(enemy.pos)
            enemy.update(dt, self)
            self._emit_motion_dust(previous_pos, enemy.pos, enemy.radius)
        for projectile in list(self.projectiles):
            projectile.update(dt, self)
        for projectile in list(self.enemy_projectiles):
            projectile.update(dt, self)

        self.ability_zones = [zone for zone in self.ability_zones if getattr(zone, "alive", False)]
        self.hazards = [hazard for hazard in self.hazards if hazard.alive]
        self.dropped_items = [item for item in self.dropped_items if item.alive]
        self.enemies = [enemy for enemy in self.enemies if enemy.alive]
        self.projectiles = [projectile for projectile in self.projectiles if projectile.alive]
        self.enemy_projectiles = [projectile for projectile in self.enemy_projectiles if projectile.alive]
        self._spatial_ready = False

        if not self.alive_troops:
            self.phase = "defeat"
            self.phase_timer = 0.0

    def _update_party_control(self, dt: float, keys, mouse_pos: tuple[int, int], viewport: pygame.Rect) -> None:
        movement = pygame.Vector2(0, 0)
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            movement.x -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            movement.x += 1
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            movement.y -= 1
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            movement.y += 1
        previous_center = pygame.Vector2(self.party_center)
        moving = movement.length_squared() > 0
        self.expedition_player_moving = moving
        if moving:
            movement = movement.normalize()
            self.party_center += movement * PARTY_MOVE_SPEED * dt
            self.party_center, _ = self.grid.resolve_circle_blockers(self.party_center, 18.0)

        mouse_world = self.camera.screen_to_world(mouse_pos, viewport)
        aim = mouse_world - self.party_center
        if aim.length_squared() > 16:
            self.facing_angle = math.atan2(aim.y, aim.x)

        offsets = _formation_offsets(len(self.party), PARTY_FORMATION_RADIUS)
        for troop, offset in zip(self.party, offsets):
            if not troop.alive:
                continue
            rotated = offset.rotate_rad(self.facing_angle + math.pi / 2)
            troop.station = self.grid.nearest_clear_world(self.party_center + rotated, self.grid.navigation_radius(troop.radius), max_radius=5)
        self._emit_party_whisp(dt, previous_center, moving)

    def _trigger_rooms(self) -> None:
        center_cell = self.grid.cell_from_world(self.party_center)
        for index, room in enumerate(self.layout.rooms):
            if index in self.spawned_rooms or not room.rect.collidepoint(center_cell):
                continue
            self.spawned_rooms.add(index)
            if room.room_type in {"combat", "large_combat", "trap"}:
                self._spawn_room_encounter(room)
            if room.room_type == "loot":
                self._spawn_loot_room(room)
            if room.room_type == "trap":
                self._spawn_trap_room(room)

    def _spawn_room_encounter(self, room: DungeonRoom) -> None:
        encounters = [entry for entry in self.definition.normal_encounters if entry.room_type == room.room_type]
        if not encounters:
            encounters = list(self.definition.normal_encounters)
        encounter = self.rng.choice(encounters) if encounters else None
        enemies = encounter.enemies if encounter is not None else ("small", "medium")
        for enemy_id in enemies:
            self.spawn_enemy_in_room(enemy_id, room, spawn_group="room")

    def _spawn_loot_room(self, room: DungeonRoom) -> None:
        gold = self.rng.randint(self.definition.loot_rooms.gold_min, self.definition.loot_rooms.gold_max)
        self.reward_gold += gold
        self.texts.append(FloatingText(room.center, f"+{gold}G CACHE", 1.0))
        for _ in range(self.definition.loot_rooms.item_rolls):
            item_id = random_drop_item_id(self.rng)
            if item_id is not None:
                self.drop_item_at(item_id, self._random_point_in_room(room))

    def _spawn_trap_room(self, room: DungeonRoom) -> None:
        for _ in range(2):
            self.add_hazard(None, self._random_point_in_room(room), 34.0, 6.0, 3.6, "fire")

    def _update_boss_phase(self, dt: float) -> None:
        boss_cell = self.grid.cell_from_world(self.party_center)
        if self.phase == "explore" and self.layout.boss_room.rect.collidepoint(boss_cell):
            self.phase = "boss_intro"
            self.phase_timer = 1.45
            self.texts.append(FloatingText(self.layout.boss_room.center, "SEALED", 1.2))
            return

        if self.phase in {"boss_intro", "boss_appear"}:
            self.phase_timer -= dt
            if self.phase_timer > 0:
                return
            if self.phase == "boss_intro":
                self.phase = "boss_waves"
                self.boss_wave_index = 0
                self._spawn_next_boss_wave()
            else:
                self._spawn_boss()
            return

        if self.phase == "boss_waves":
            live_wave = [enemy for enemy in self.enemies if enemy.alive and enemy.spawn_group == "boss_wave"]
            if live_wave:
                return
            if self.boss_wave_index < len(self.definition.boss_wave_counts):
                self._spawn_next_boss_wave()
            else:
                self.phase = "boss_appear"
                self.phase_timer = 1.35
                self.texts.append(FloatingText(self.layout.boss_room.center, self.boss_def.name.upper(), 1.2))
            return

        if self.phase == "exit_open":
            exit_pos = self.grid.world_center(self.layout.exit_cell)
            if self.party_center.distance_to(exit_pos) <= 38.0:
                self.finished_result = self._build_result(True, "Expedition complete")

    def _spawn_next_boss_wave(self) -> None:
        count = self.definition.boss_wave_counts[self.boss_wave_index]
        self.boss_wave_index += 1
        choices = ("small", "medium", "medium", "ranged", "large")
        for _ in range(count):
            self.spawn_enemy_in_room(self.rng.choice(choices), self.layout.boss_room, spawn_group="boss_wave")
        self.texts.append(FloatingText(self.layout.boss_room.center, f"WAVE {self.boss_wave_index}", 1.0))

    def _spawn_boss(self) -> None:
        self.boss = ExpeditionBossEnemy(self.boss_def, self.layout.boss_room.center)
        self.enemies.append(self.boss)
        self.phase = "boss_fight"
        self.spawn_burst(self.boss.pos, 34, 120)

    def spawn_enemy_in_room(self, kind: str, room: DungeonRoom, spawn_group: str = "room") -> Enemy:
        return self.spawn_enemy_at(kind, self._random_point_in_room(room), spawn_group=spawn_group)

    def spawn_enemy_at(self, kind: str, pos: pygame.Vector2, spawn_group: str = "room") -> Enemy:
        enemy = Enemy(kind, pygame.Vector2(pos), 1, behavior="expedition", home_pos=pygame.Vector2(pos), leash_radius=9999.0, spawn_group=spawn_group)
        enemy.apply_expedition_stat_budget(self.enemy_stat_budget * self.definition.enemy_stat_budget_multiplier)
        self.enemies.append(enemy)
        self._spatial_ready = False
        return enemy

    def _random_point_in_room(self, room: DungeonRoom) -> pygame.Vector2:
        cell = (
            self.rng.randint(room.rect.left + 1, room.rect.right - 2),
            self.rng.randint(room.rect.top + 1, room.rect.bottom - 2),
        )
        return self.grid.world_center(cell)

    def rebuild_spatial_index(self) -> None:
        self._enemy_bins = {}
        self._troop_bins = {}
        for enemy in self.enemies:
            if enemy.alive:
                self._enemy_bins.setdefault(self._spatial_key(enemy.pos), []).append(enemy)
        for troop in self.troops:
            if troop.alive:
                self._troop_bins.setdefault(self._spatial_key(troop.pos), []).append(troop)
        self._spatial_ready = True

    def nearby_enemies(self, pos: pygame.Vector2, radius: float) -> list[Enemy]:
        if not self._spatial_ready:
            self.rebuild_spatial_index()
        return self._nearby_from_bins(self._enemy_bins, pos, radius)

    def targetable_enemies_near(self, pos: pygame.Vector2, radius: float) -> list[Enemy]:
        return [enemy for enemy in self.nearby_enemies(pos, radius) if enemy.alive and self.is_world_explored(enemy.pos, enemy.radius)]

    def nearby_troops(self, pos: pygame.Vector2, radius: float) -> list[Troop]:
        if not self._spatial_ready:
            self.rebuild_spatial_index()
        return self._nearby_from_bins(self._troop_bins, pos, radius)

    def _nearby_from_bins(self, bins: dict[tuple[int, int], list], pos: pygame.Vector2, radius: float) -> list:
        point = pygame.Vector2(pos)
        min_x = int((point.x - radius) // self.spatial_cell_size)
        max_x = int((point.x + radius) // self.spatial_cell_size)
        min_y = int((point.y - radius) // self.spatial_cell_size)
        max_y = int((point.y + radius) // self.spatial_cell_size)
        radius_sq = radius * radius
        results = []
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                for item in bins.get((x, y), ()):
                    if getattr(item, "alive", True) and item.pos.distance_squared_to(point) <= radius_sq:
                        results.append(item)
        return results

    def _spatial_key(self, pos: pygame.Vector2) -> tuple[int, int]:
        return int(pos.x // self.spatial_cell_size), int(pos.y // self.spatial_cell_size)

    def update_fog(self, dt: float, immediate: bool = False) -> None:
        self.fog.update(dt, self._fog_sources(), immediate=immediate)

    def _fog_sources(self) -> list[VisionSource]:
        profile = VisionProfile(260.0, 0.72)
        return [VisionSource(troop.pos, profile) for troop in self.alive_troops]

    def is_world_visible(self, pos: pygame.Vector2 | tuple[float, float], radius: float = 0.0) -> bool:
        return self.is_world_explored(pos, radius)

    def is_world_explored(self, pos: pygame.Vector2 | tuple[float, float], radius: float = 0.0) -> bool:
        return self.fog.is_explored_world(pos, radius)

    def find_enemy_attack_target(self, pos: pygame.Vector2):
        troops = self.alive_troops
        if not troops:
            return None
        point = pygame.Vector2(pos)
        return min(troops, key=lambda troop: troop.pos.distance_to(point))

    def core_target_for(self, pos: pygame.Vector2):
        return self.find_enemy_attack_target(pos) or self.core_target

    def find_enemy_wall_target(self, enemy: Enemy, desired_target):
        return None

    def aggro_candidates(self, pos: pygame.Vector2, radius: float):
        for troop in self.nearby_troops(pos, radius):
            if troop.alive and float(getattr(troop, "stealth_time", 0.0)) <= 0:
                yield troop

    def aggro_suppressed_by_cover_fire(self, source) -> bool:
        return False

    def metric_for(self, entity) -> ExpeditionTroopMetrics | None:
        if not isinstance(entity, Troop):
            return None
        return self.metrics_by_troop_id.get(id(entity))

    def record_ability_activation(self, owner, ability) -> None:
        metrics = self.metric_for(owner)
        if metrics is None:
            return
        name = str(getattr(ability, "name", getattr(ability, "ability_id", "Ability")))
        metrics.ability_counts[name] = metrics.ability_counts.get(name, 0) + 1

    def record_stun(self, owner, target=None, duration: float = 0.0) -> None:
        metrics = self.metric_for(owner)
        if metrics is None:
            return
        metrics.stuns += 1

    def record_aggro(self, owner, amount: float) -> None:
        metrics = self.metric_for(owner)
        if metrics is None or amount <= 0:
            return
        metrics.aggro += float(amount)

    def record_blocks(self, target, amount: float) -> None:
        metrics = self.metric_for(target)
        if metrics is None or amount <= 0:
            return
        metrics.blocks += float(amount)

    def metric_rows(self, metric_id: str) -> list[dict[str, object]]:
        elapsed = max(0.1, self.metrics_elapsed)
        rows: list[dict[str, object]] = []
        for metrics in self.metrics_by_troop_id.values():
            if metric_id == "dps":
                value = metrics.damage_done / elapsed
            elif metric_id == "hps":
                value = metrics.healing_done / elapsed
            elif metric_id == "abilities_fired":
                value = float(metrics.abilities_fired)
            else:
                value = float(getattr(metrics, metric_id, 0.0))
            rows.append({"troop": metrics.troop, "metrics": metrics, "value": value})
        rows.sort(key=lambda row: (-float(row["value"]), getattr(row["troop"], "display_name", "")))
        total = sum(max(0.0, float(row["value"])) for row in rows)
        for row in rows:
            row["percent"] = 0.0 if total <= 0 else max(0.0, float(row["value"])) / total
        return rows

    def emit_aggro(self, source, amount: float, reason: str, radius: float = 260.0, source_pos: pygame.Vector2 | None = None) -> None:
        if source is None or amount <= 0:
            return
        origin = pygame.Vector2(source_pos if source_pos is not None else source.pos)
        for enemy in self.nearby_enemies(origin, radius):
            if enemy.alive and hasattr(enemy, "aggro"):
                distance = enemy.pos.distance_to(origin)
                falloff = 1.0 - min(0.55, distance / max(1.0, radius) * 0.55)
                threat = amount * falloff
                enemy.aggro.add_threat(source, threat, reason)
                self.record_aggro(source, threat)

    def damage_enemy(
        self,
        enemy: Enemy,
        amount: float,
        owner=None,
        quiet: bool = False,
        source_pos: pygame.Vector2 | None = None,
        element: str = "physical",
        effect: ElementalEffect | None = None,
    ) -> None:
        if not enemy.alive or amount <= 0:
            return
        if owner is not None and hasattr(owner, "abilities"):
            amount = owner.abilities.modify_outgoing_damage(enemy, amount, element, self)
        critical = isinstance(owner, Troop) and owner.roll_critical_hit()
        if critical:
            amount *= owner.critical_damage_multiplier()
        actual_amount = amount * damage_multiplier(enemy, element)
        if hasattr(enemy, "damage_taken_multiplier"):
            actual_amount *= enemy.damage_taken_multiplier(owner)
        if actual_amount <= 0:
            return
        if (
            owner is not None
            and hasattr(enemy, "aggro")
            and getattr(owner, "alive", True)
            and float(getattr(owner, "stealth_time", 0.0)) <= 0
            and not self.aggro_suppressed_by_cover_fire(owner)
        ):
            threat = actual_amount * (0.35 if quiet else 1.25)
            if isinstance(owner, Troop):
                threat *= owner.aggro_generation_multiplier()
            enemy.aggro.add_threat(owner, threat, "damage")
            self.record_aggro(owner, threat)
        metrics = self.metric_for(owner)
        if metrics is not None:
            metrics.damage_done += actual_amount
            if critical:
                metrics.criticals += 1
        killed = enemy.take_damage(actual_amount, owner)
        if effect is not None and enemy.alive:
            self.apply_elemental_effect(enemy, effect, owner, source_pos)
        if hasattr(enemy, "abilities"):
            enemy.abilities.on_owner_damaged(actual_amount, owner, source_pos, element, self)
        if not quiet and actual_amount >= 2:
            hit_source = source_pos or (pygame.Vector2(owner.pos) if owner is not None else None)
            if hit_source is not None and hasattr(enemy, "apply_knockback"):
                enemy.apply_knockback(actual_amount, hit_source)
            self.spawn_hit(enemy.pos, min(8, 2 + int(actual_amount / 12)))
            if critical:
                self.texts.append(FloatingText(pygame.Vector2(enemy.pos), "CRIT", 0.55))
        if owner is not None and hasattr(owner, "on_damage_dealt"):
            owner.on_damage_dealt(actual_amount, enemy, self)
        if killed:
            self.kill_enemy(enemy, owner or getattr(enemy, "last_hit_by", None))

    def kill_enemy(self, enemy: Enemy, owner=None) -> None:
        if not enemy.alive:
            return
        enemy.alive = False
        gold = max(0, int(round(enemy.reward * self.definition.rewards.enemy_gold_multiplier)))
        self.reward_gold += gold
        if gold > 0:
            self.texts.append(FloatingText(pygame.Vector2(enemy.pos), f"+{gold}G", 0.7))
        self.spawn_death_explosion(enemy)
        if isinstance(owner, Troop):
            xp = max(6, int(enemy.reward * 2 * self.definition.rewards.enemy_xp_multiplier))
            self.pending_xp[id(owner)] = self.pending_xp.get(id(owner), 0) + xp
            metrics = self.metric_for(owner)
            if metrics is not None:
                metrics.kills += 1
        if getattr(enemy, "spawn_group", "") == "boss":
            self.reward_gold += self.definition.rewards.boss_gold
            self._drop_boss_loot(enemy)
            self.phase = "exit_open"
            self.texts.append(FloatingText(pygame.Vector2(enemy.pos), "BOSS DOWN", 1.2))
        else:
            self.maybe_drop_enemy_loot(enemy)

    def maybe_drop_enemy_loot(self, enemy: Enemy) -> None:
        loot = getattr(enemy, "loot", {})
        chance = float(loot.get("drop_chance", 0.18)) if isinstance(loot, dict) else 0.18
        chance *= self.definition.rewards.enemy_item_drop_multiplier
        if self.rng.random() > chance:
            return
        item_id = random_drop_item_id(self.rng)
        if item_id is not None:
            self.drop_item_at(item_id, pygame.Vector2(enemy.pos))

    def _drop_boss_loot(self, enemy: Enemy) -> None:
        for _ in range(self.definition.guaranteed_boss_items):
            item_id = random_drop_item_id(self.rng)
            if item_id is not None:
                offset = pygame.Vector2(self.rng.uniform(-28, 28), self.rng.uniform(-28, 28))
                self.drop_item_at(item_id, pygame.Vector2(enemy.pos) + offset)

    def damage_friendly(
        self,
        target,
        amount: float,
        source_pos: pygame.Vector2 | None = None,
        element: str = "physical",
        source=None,
    ) -> None:
        if not getattr(target, "alive", False) or amount <= 0:
            return
        if isinstance(target, Troop) and self.item_flag("troop_invincible"):
            self.spawn_hit(target.pos, 2)
            return
        actual_amount = amount * damage_multiplier(target, element)
        blocked = 0.0
        if isinstance(target, Troop):
            before_armor = actual_amount
            actual_amount = target.reduce_damage_by_armor(actual_amount)
            blocked += max(0.0, before_armor - actual_amount)
        if actual_amount <= 0:
            self.record_blocks(target, blocked)
            return
        if hasattr(target, "abilities"):
            before_abilities = actual_amount
            actual_amount = target.abilities.modify_incoming_damage(actual_amount, source, source_pos, element, self)
            blocked += max(0.0, before_abilities - actual_amount)
        if actual_amount <= 0:
            self.record_blocks(target, blocked)
            self.spawn_hit(target.pos, 2)
            return
        redirected_target = self._redirect_fatal_friendly_damage(target, actual_amount, source, source_pos, element)
        if redirected_target is not target:
            target = redirected_target
            if hasattr(target, "abilities"):
                before_abilities = actual_amount
                actual_amount = target.abilities.modify_incoming_damage(actual_amount, source, source_pos, element, self)
                blocked += max(0.0, before_abilities - actual_amount)
            if isinstance(target, Troop):
                before_armor = actual_amount
                actual_amount = target.reduce_damage_by_armor(actual_amount)
                blocked += max(0.0, before_armor - actual_amount)
            if actual_amount <= 0:
                self.record_blocks(target, blocked)
                self.spawn_hit(target.pos, 2)
                return
        self.record_blocks(target, blocked)
        metrics = self.metric_for(target)
        if metrics is not None:
            metrics.damage_taken += actual_amount
        killed = target.take_damage(actual_amount, self) if isinstance(target, Troop) else target.take_damage(actual_amount)
        if hasattr(target, "abilities"):
            target.abilities.on_owner_damaged(actual_amount, source, source_pos, element, self)
        self.spawn_hit(target.pos, min(9, 3 + int(actual_amount / 8)))
        if killed and isinstance(target, Troop):
            self.kill_troop(target)

    def _redirect_fatal_friendly_damage(self, target, amount: float, source, source_pos: pygame.Vector2 | None, element: str):
        if amount <= 0 or not getattr(target, "alive", False) or not hasattr(target, "health"):
            return target
        if amount < float(getattr(target, "health", 0.0)):
            return target
        candidates = [
            troop
            for troop in self.nearby_troops(target.pos, 180.0)
            if troop.alive and troop is not target and hasattr(troop, "abilities")
        ]
        candidates.sort(key=lambda troop: troop.pos.distance_to(target.pos))
        for troop in candidates:
            for ability in troop.abilities.abilities:
                if not hasattr(ability, "try_intercept_fatal_damage"):
                    continue
                redirected = ability.try_intercept_fatal_damage(self, target, amount, source=source, source_pos=source_pos, element=element)
                if redirected is not None:
                    return redirected
        return target

    def kill_troop(self, troop: Troop) -> None:
        if not troop.alive:
            return
        troop.alive = False
        troop.target = None
        metrics = self.metric_for(troop)
        if metrics is not None:
            metrics.deaths = 1
        self.spawn_burst(troop.pos, 18, 85)
        self.texts.append(FloatingText(pygame.Vector2(troop.pos), "DOWN", 0.75))

    def restore_friendly(self, target, amount: float, source=None, reason: str = "heal", element: str = "physical") -> float:
        if amount <= 0 or not getattr(target, "alive", False):
            return 0.0
        if not hasattr(target, "health") or not hasattr(target, "max_health"):
            return 0.0
        amount *= healing_multiplier(target, element)
        missing = max(0.0, float(target.max_health) - float(target.health))
        actual = min(amount, missing)
        if actual <= 0:
            return 0.0
        target.health = min(float(target.max_health), float(target.health) + actual)
        heal_owner = source if isinstance(source, Troop) else (target if isinstance(target, Troop) else None)
        metrics = self.metric_for(heal_owner)
        if metrics is not None:
            metrics.healing_done += actual
        if source is not None:
            self.emit_aggro(source, actual * 2.0 + 2.0, reason, radius=280.0)
        return actual

    def item_multiplier(self, effect_key: str, default: float = 1.0) -> float:
        return self.main_state.item_multiplier(effect_key, default) if hasattr(self.main_state, "item_multiplier") else default

    def item_flag(self, effect_key: str) -> bool:
        return self.main_state.item_flag(effect_key) if hasattr(self.main_state, "item_flag") else False

    def shield_generators(self) -> list:
        return []

    def heal_towers_and_buildings_full(self) -> int:
        return 0

    def heal_troops_full(self) -> int:
        healed = 0
        for troop in self.alive_troops:
            if troop.health < troop.max_health:
                healed += 1
            troop.health = troop.max_health
            self.spawn_hit(troop.pos, 2)
        return healed

    def grant_tower_xp_all(self, minimum: int, maximum: int) -> int:
        return 0

    def grant_troop_xp_all(self, minimum: int, maximum: int) -> int:
        awarded = 0
        for troop in self.alive_troops:
            amount = self.rng.randint(minimum, maximum)
            if troop.add_xp(amount):
                self.texts.append(FloatingText(pygame.Vector2(troop.pos), "READY", 0.9))
            awarded += amount
            self.texts.append(FloatingText(pygame.Vector2(troop.pos), f"+{amount}XP", 0.7))
        return awarded

    def add_item(self, item_id: str, quantity: int = 1) -> bool:
        if item_id not in ITEM_DEFINITIONS:
            return False
        for _ in range(max(1, quantity)):
            self.reward_items.append(item_id)
        definition = ITEM_DEFINITIONS[item_id]
        self.texts.append(FloatingText(pygame.Vector2(self.party_center), definition.glyph.upper()[:8], 0.85))
        return True

    def drop_item_at(self, item_id: str, pos: pygame.Vector2) -> bool:
        if item_id not in ITEM_DEFINITIONS:
            return False
        self.dropped_items.append(DroppedItem(item_id, pygame.Vector2(pos), magnet_radius=72.0))
        return True

    def add_hazard(self, owner, pos: pygame.Vector2, radius: float, duration: float, dps: float, element: str = "fire") -> None:
        self.hazards.append(ExpeditionHazard(owner, pygame.Vector2(pos), radius, duration, dps, element))

    def apply_elemental_effect(
        self,
        enemy: Enemy,
        effect: ElementalEffect,
        owner=None,
        source_pos: pygame.Vector2 | None = None,
    ) -> None:
        if not enemy.alive:
            return
        if effect.element == "fire" and effect.duration > 0 and effect.dot_dps > 0:
            enemy.apply_burn(effect.dot_dps, effect.duration, owner, spread_radius=effect.spread_radius, spread_falloff=effect.spread_falloff)
        elif effect.element == "ice" and effect.duration > 0:
            enemy.apply_slow(effect.slow_multiplier, effect.duration, effect.attack_slow_multiplier)
        elif effect.element == "lightning" and effect.stun_duration > 0:
            enemy.apply_stun(effect.stun_duration)
            self.record_stun(owner, enemy, effect.stun_duration)
        if source_pos is not None and effect.element != "physical":
            self.beams.append(Beam(pygame.Vector2(source_pos), pygame.Vector2(enemy.pos), 0.08, 1))

    def spread_burn(self, source: Enemy) -> None:
        radius = float(getattr(source, "burn_spread_radius", 0.0))
        if radius <= 0 or not source.alive:
            return
        candidates = [
            enemy
            for enemy in self.nearby_enemies(source.pos, radius + 24)
            if enemy is not source and enemy.alive and enemy.burn_time <= 0 and enemy.pos.distance_to(source.pos) <= radius + enemy.radius
        ]
        candidates.sort(key=lambda enemy: enemy.pos.distance_to(source.pos))
        for enemy in candidates[:2]:
            enemy.apply_burn(source.burn_dps * 0.72, max(0.75, source.burn_time * 0.72), source.burn_owner, can_spread=False)
            self.beams.append(Beam(pygame.Vector2(source.pos), pygame.Vector2(enemy.pos), 0.11, 1))

    def chain_lightning(
        self,
        start_pos: pygame.Vector2,
        first_target: Enemy | None,
        damage: float,
        owner=None,
        jumps: int = 4,
        radius: float = 120.0,
        falloff: float = 0.65,
        stun: float = 0.14,
    ) -> int:
        current = first_target
        current_damage = damage
        previous_pos = pygame.Vector2(start_pos)
        hit: set[Enemy] = set()
        for _ in range(jumps):
            if current is None or not current.alive or current in hit:
                break
            hit.add(current)
            self.beams.append(Beam(previous_pos, pygame.Vector2(current.pos), 0.14, 2))
            self.damage_enemy(current, current_damage, owner, source_pos=pygame.Vector2(previous_pos), element="lightning")
            if current.alive:
                current.apply_stun(stun)
                self.record_stun(owner, current, stun)
            previous_pos = pygame.Vector2(current.pos)
            current_damage *= falloff
            choices = [
                enemy
                for enemy in self.targetable_enemies_near(previous_pos, radius + 24)
                if enemy.alive and enemy not in hit and enemy.pos.distance_to(previous_pos) <= radius + enemy.radius
            ]
            current = min(choices, key=lambda enemy: enemy.pos.distance_to(previous_pos)) if choices else None
        return len(hit)

    def play_sound(self, sound_id: str, random_pitch: bool = False) -> None:
        if hasattr(self.main_state, "play_sound"):
            self.main_state.play_sound(sound_id, random_pitch=random_pitch)

    def _emit_party_whisp(self, dt: float, previous_center: pygame.Vector2, moving: bool) -> None:
        target = 1.0 if moving else 0.0
        self.whisp_motion_intensity += (target - self.whisp_motion_intensity) * min(1.0, dt * 9.0)
        if moving and previous_center.distance_squared_to(self.party_center) > 1.0:
            self.whisp_emit_timer -= dt
            while self.whisp_emit_timer <= 0.0:
                self.whisp_emit_timer += WHISP_MOVING_INTERVAL
                self._emit_motion_dust(previous_center, self.party_center, 16.0 * EXPEDITION_WHISP_SCALE, chance_scale=1.0, force=True)
        else:
            self.whisp_emit_timer = min(self.whisp_emit_timer, WHISP_IDLE_INTERVAL)

    def _emit_motion_dust(
        self,
        previous_pos: pygame.Vector2,
        current_pos: pygame.Vector2,
        radius: float,
        chance_scale: float = 0.055,
        force: bool = False,
    ) -> None:
        delta = pygame.Vector2(current_pos) - pygame.Vector2(previous_pos)
        distance = delta.length()
        if distance < 1.15:
            return
        chance = min(0.85, distance * chance_scale)
        if not force and self.rng.random() > chance:
            return
        backward = -delta.normalize()
        tangent = pygame.Vector2(-backward.y, backward.x)
        origin = pygame.Vector2(current_pos) + backward * self.rng.uniform(radius * 0.12, radius * 0.58)
        origin += tangent * self.rng.uniform(-radius * 0.28, radius * 0.28)
        velocity = backward * self.rng.uniform(4.0, 18.0) + tangent * self.rng.uniform(-7.0, 7.0)
        self.dust_clouds.append(ExpeditionDustCloud(origin, velocity, self.rng.uniform(0.24, 0.48), self.rng.uniform(2.0, 5.0)))

    def spawn_hit(self, pos: pygame.Vector2, count: int) -> None:
        for _ in range(count):
            angle = self.rng.random() * math.tau
            speed = self.rng.uniform(18, 70)
            vel = pygame.Vector2(math.cos(angle), math.sin(angle)) * speed
            self.particles.append(Particle(pygame.Vector2(pos), vel, self.rng.uniform(0.12, 0.28), self.rng.uniform(1.3, 3.0)))

    def spawn_burst(self, pos: pygame.Vector2, count: int, speed: float) -> None:
        for _ in range(count):
            angle = self.rng.random() * math.tau
            vel = pygame.Vector2(math.cos(angle), math.sin(angle)) * self.rng.uniform(speed * 0.25, speed)
            self.particles.append(Particle(pygame.Vector2(pos), vel, self.rng.uniform(0.18, 0.45), self.rng.uniform(1.5, 4.5)))

    def show_damage_impact(self, pos: pygame.Vector2, kind: str, radius: float = 0.0) -> None:
        center = pygame.Vector2(pos)
        if kind == "aoe":
            radius = max(18.0, float(radius))
            self.damage_pulses.append(DamagePulse(center, "aoe", radius, life=0.42, max_life=0.42))
            count = min(18, max(8, int(radius / 7)))
            for index in range(count):
                angle = index / count * math.tau + self.rng.uniform(-0.09, 0.09)
                direction = pygame.Vector2(math.cos(angle), math.sin(angle))
                origin = center + direction * self.rng.uniform(radius * 0.42, radius * 0.94)
                velocity = direction * self.rng.uniform(26, 82)
                self.particles.append(Particle(origin, velocity, self.rng.uniform(0.16, 0.34), self.rng.uniform(1.1, 2.8)))
            return
        self.damage_pulses.append(DamagePulse(center, "single", 24.0, life=0.24, max_life=0.24))
        self.spawn_burst(center, 6, 42)

    def spawn_death_explosion(self, enemy: Enemy) -> None:
        count = 42 if getattr(enemy, "spawn_group", "") == "boss" else 20
        speed = 130 if getattr(enemy, "spawn_group", "") == "boss" else 95
        center = pygame.Vector2(enemy.pos)
        for index in range(count):
            angle = (index / count) * math.tau + self.rng.uniform(-0.18, 0.18)
            outward = pygame.Vector2(math.cos(angle), math.sin(angle))
            vel = outward * self.rng.uniform(speed * 0.35, speed)
            self.particles.append(Particle(pygame.Vector2(center), vel, self.rng.uniform(0.24, 0.62), self.rng.uniform(1.8, 4.8)))

    def _update_fx(self, dt: float) -> None:
        for collection in (self.particles, self.dust_clouds, self.damage_pulses, self.beams, self.texts):
            for item in list(collection):
                item.update(dt)
        self.particles = [particle for particle in self.particles if particle.life > 0]
        self.dust_clouds = [cloud for cloud in self.dust_clouds if cloud.life > 0]
        self.damage_pulses = [pulse for pulse in self.damage_pulses if pulse.life > 0]
        self.beams = [beam for beam in self.beams if beam.life > 0]
        self.texts = [text for text in self.texts if text.life > 0]

    def restore_party_to_base(self) -> None:
        for snapshot in self.party_snapshots:
            troop = snapshot.troop
            if not troop.alive:
                continue
            troop.pos = pygame.Vector2(snapshot.original_pos)
            troop.station = pygame.Vector2(snapshot.original_station)
            troop.attack_enabled = snapshot.original_attack_enabled
            if hasattr(troop, "harvester"):
                troop.harvester = snapshot.original_harvester
            troop.vel.update(0, 0)
            troop.target = None
            if getattr(troop, "navigator", None) is not None:
                troop.navigator.clear()

    def _build_result(self, victory: bool, reason: str) -> ExpeditionResult:
        xp = dict(self.pending_xp)
        if victory:
            survivors = self.alive_troops
            if survivors:
                completion_xp = max(0, int(self.definition.rewards.completion_xp))
                base_share = completion_xp // len(survivors)
                remainder = completion_xp % len(survivors)
                for index, troop in enumerate(survivors):
                    share = base_share + (1 if index < remainder else 0)
                    if share > 0:
                        xp[id(troop)] = xp.get(id(troop), 0) + share
        return ExpeditionResult(
            victory=victory,
            reason=reason,
            definition_name=self.definition.name,
            boss_name=self.boss_def.name,
            party=self.party,
            gold=self.reward_gold + (self.definition.rewards.completion_gold if victory else 0),
            items=tuple(self.reward_items if victory else ()),
            xp_by_troop_id=xp if victory else {},
            dead_troop_ids=frozenset(id(troop) for troop in self.party if not troop.alive),
        )

    def draw(self, surface: pygame.Surface, viewport: pygame.Rect, fonts: dict[str, pygame.font.Font], mouse_pos: tuple[int, int]) -> None:
        self.camera.center_on(self.party_center, viewport)
        self.camera.clamp_to_world(viewport)
        previous_clip = surface.get_clip()
        surface.set_clip(viewport)
        pygame.draw.rect(surface, config.PALETTE.black, viewport)
        self._draw_dungeon(surface, viewport)
        self._draw_exit(surface, viewport, fonts)
        for hazard in self.hazards:
            if self.is_world_explored(hazard.pos, hazard.radius):
                hazard.draw(surface, self.camera, viewport)
        for cloud in self.dust_clouds:
            if self.is_world_explored(cloud.pos, cloud.radius):
                cloud.draw(surface, self.camera, viewport)
        for dropped_item in self.dropped_items:
            if self.is_world_explored(dropped_item.pos, 12):
                dropped_item.draw(surface, self.camera, viewport)
        for zone in self.ability_zones:
            if self.is_world_explored(zone.pos, zone.radius):
                zone.draw(surface, self.camera, viewport)
        self._draw_party_leashes(surface, viewport)
        selected_troops = set(getattr(self.main_state, "selected_troops", ()))
        hovered_troop = None
        if viewport.collidepoint(mouse_pos):
            hovered_troop = self.find_troop_at(self.camera.screen_to_world(mouse_pos, viewport))
        for troop in self.troops:
            if troop.alive:
                troop.draw(surface, self.camera, viewport, troop in selected_troops, troop is hovered_troop)
        for enemy in self.enemies:
            if self.is_world_explored(enemy.pos, enemy.radius):
                enemy.draw(surface, self.camera, viewport)
                for ability in getattr(enemy, "abilities", AbilitySystemComponent(enemy)).abilities:
                    ability.draw_preview(surface, self.camera, viewport)
        for projectile in self.projectiles:
            if self.is_world_explored(projectile.pos, 8):
                projectile.draw(surface, self.camera, viewport)
        for projectile in self.enemy_projectiles:
            if self.is_world_explored(projectile.pos, 8):
                projectile.draw(surface, self.camera, viewport)
        for pulse in self.damage_pulses:
            if self.is_world_explored(pulse.pos, pulse.radius):
                pulse.draw(surface, self.camera, viewport)
        for beam in self.beams:
            if self.is_world_explored(beam.start, 4) and self.is_world_explored(beam.end, 4):
                beam.draw(surface, self.camera, viewport)
        for particle in self.particles:
            if self.is_world_explored(particle.pos, particle.radius):
                particle.draw(surface, self.camera, viewport)
        for text in self.texts:
            if self.is_world_explored(text.pos, 8):
                text.draw(surface, self.camera, viewport, fonts["tiny"])
        self._draw_party_whisp(surface, viewport)
        self.fog.draw(surface, self.camera, viewport)
        self._draw_expedition_hud(surface, viewport, fonts)
        if self.phase in {"boss_intro", "boss_appear"}:
            self._draw_cinematic(surface, viewport, fonts)
        if self.phase == "defeat":
            self._draw_death(surface, viewport, fonts)
        surface.set_clip(previous_clip)
        pygame.draw.rect(surface, config.PALETTE.line_bright, viewport, 1)

    def _draw_dungeon(self, surface: pygame.Surface, viewport: pygame.Rect) -> None:
        world_top_left = self.camera.screen_to_world(viewport.topleft, viewport)
        world_bottom_right = self.camera.screen_to_world(viewport.bottomright, viewport)
        min_x = max(0, int(world_top_left.x // self.grid.tile_size) - 1)
        max_x = min(self.grid.width - 1, int(world_bottom_right.x // self.grid.tile_size) + 1)
        min_y = max(0, int(world_top_left.y // self.grid.tile_size) - 1)
        max_y = min(self.grid.height - 1, int(world_bottom_right.y // self.grid.tile_size) + 1)
        surface.fill(config.PALETTE.black, viewport)
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                cell = (x, y)
                rect = self._cell_screen_rect(cell, viewport)
                if cell in self.layout.floor_cells:
                    pygame.draw.rect(surface, config.PALETTE.black, rect)
                else:
                    pygame.draw.rect(surface, config.PALETTE.white, rect)

    def _touches_floor(self, cell: tuple[int, int]) -> bool:
        x, y = cell
        return any((x + dx, y + dy) in self.layout.floor_cells for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))

    def _cell_screen_rect(self, cell: tuple[int, int], viewport: pygame.Rect) -> pygame.Rect:
        top_left = self.camera.world_to_screen((cell[0] * self.grid.tile_size, cell[1] * self.grid.tile_size), viewport)
        bottom_right = self.camera.world_to_screen(((cell[0] + 1) * self.grid.tile_size, (cell[1] + 1) * self.grid.tile_size), viewport)
        return pygame.Rect(math.floor(top_left.x), math.floor(top_left.y), max(1, math.ceil(bottom_right.x - top_left.x)), max(1, math.ceil(bottom_right.y - top_left.y)))

    def _draw_party_leashes(self, surface: pygame.Surface, viewport: pygame.Rect) -> None:
        center = self.camera.world_to_screen(self.party_center, viewport)
        for troop in self.alive_troops:
            troop_screen = self.camera.world_to_screen(troop.pos, viewport)
            draw_line_alpha(surface, center, troop_screen, config.TACTICAL_OVERLAY_COLOR, config.TACTICAL_OVERLAY_ALPHA, max(1, int(self.camera.zoom)))
            draw_circle_alpha(surface, troop_screen, max(3.0, 5.0 * self.camera.zoom), config.TACTICAL_OVERLAY_COLOR, config.TACTICAL_OVERLAY_NODE_ALPHA, 1)

    def _draw_party_whisp(self, surface: pygame.Surface, viewport: pygame.Rect) -> None:
        center = self.camera.world_to_screen(self.party_center, viewport)
        zoom = self.camera.zoom
        now = pygame.time.get_ticks() * 0.001
        pulse = 0.5 + 0.5 * math.sin(now * 2.8)
        moving = self.whisp_motion_intensity
        visual_scale = EXPEDITION_WHISP_SCALE
        draw_circle_alpha(surface, center, (8.0 + pulse * 2.0 + moving * 2.0) * visual_scale * zoom, config.PALETTE.white, int(46 + moving * 24), 1)
        draw_circle_alpha(surface, center, (2.2 + pulse * 0.8 + moving * 0.8) * visual_scale * zoom, config.PALETTE.white, int(128 + moving * 48))
        direction = pygame.Vector2(math.cos(self.facing_angle), math.sin(self.facing_angle))
        nose = center + direction * (9.0 + pulse * 2.0) * visual_scale * zoom
        draw_circle_alpha(surface, nose, (2.0 + moving * 0.8) * visual_scale * zoom, config.PALETTE.white, int(68 + moving * 40))
        for index in range(4):
            angle = now * (0.7 + index * 0.09) + index * math.tau / 4
            distance = (5.0 + index * 2.1 + pulse * 1.0) * visual_scale * zoom
            point = center + pygame.Vector2(math.cos(angle), math.sin(angle)) * distance
            alpha = int((34 + moving * 24) * (0.75 + 0.25 * math.sin(now * 1.4 + index)))
            draw_circle_alpha(surface, point, max(1.0, (1.0 + index * 0.25) * visual_scale * zoom), config.PALETTE.white, alpha)

    def _draw_exit(self, surface: pygame.Surface, viewport: pygame.Rect, fonts: dict[str, pygame.font.Font]) -> None:
        if self.phase != "exit_open":
            return
        pos = self.grid.world_center(self.layout.exit_cell)
        screen = self.camera.world_to_screen(pos, viewport)
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.008)
        rect = pygame.Rect(0, 0, int(34 * self.camera.zoom), int(44 * self.camera.zoom))
        rect.center = screen
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, max(1, int(2 * self.camera.zoom)))
        draw_circle_alpha(surface, screen, (42 + pulse * 8) * self.camera.zoom, config.PALETTE.white, 42, 1)
        label = fonts["tiny"].render("EXIT", True, config.PALETTE.white)
        surface.blit(label, label.get_rect(center=(screen.x, rect.bottom + 10)))

    def _draw_expedition_hud(self, surface: pygame.Surface, viewport: pygame.Rect, fonts: dict[str, pygame.font.Font]) -> None:
        palette = config.PALETTE
        panel = pygame.Rect(viewport.left + 14, viewport.top + 12, 260, 58)
        draw_rect_alpha(surface, panel, palette.black, 210)
        pygame.draw.rect(surface, palette.line_bright, panel, 1)
        title = fonts["small"].render(self.definition.name.upper(), True, palette.white)
        surface.blit(title, (panel.left + 10, panel.top + 8))
        hp_max = sum(troop.max_health for troop in self.party)
        hp = sum(max(0.0, troop.health) for troop in self.party if troop.alive)
        bar = pygame.Rect(panel.left + 10, panel.bottom - 17, panel.width - 20, 7)
        pygame.draw.rect(surface, palette.black, bar)
        fill = bar.copy()
        fill.width = int(bar.width * (hp / max(1.0, hp_max)))
        pygame.draw.rect(surface, palette.white, fill)
        status = f"{len(self.alive_troops)}/{len(self.party)} PARTY  {self.reward_gold}G  {len(self.reward_items)} ITEMS"
        surface.blit(fonts["tiny"].render(status, True, palette.text_dim), (panel.left + 10, panel.top + 30))
        if self.boss is not None and self.boss.alive:
            boss_bar = pygame.Rect(viewport.centerx - 160, viewport.top + 16, 320, 10)
            pygame.draw.rect(surface, palette.black, boss_bar)
            fill = boss_bar.copy()
            fill.width = int(boss_bar.width * max(0.0, self.boss.health / max(1.0, self.boss.max_health)))
            pygame.draw.rect(surface, palette.white, fill)
            pygame.draw.rect(surface, palette.line_bright, boss_bar, 1)
            label = fonts["tiny"].render(self.boss.display_name.upper(), True, palette.text)
            surface.blit(label, label.get_rect(midbottom=(boss_bar.centerx, boss_bar.top - 2)))

    def _draw_cinematic(self, surface: pygame.Surface, viewport: pygame.Rect, fonts: dict[str, pygame.font.Font]) -> None:
        draw_rect_alpha(surface, viewport, config.PALETTE.black, 92)
        top = pygame.Rect(viewport.left, viewport.top, viewport.width, 48)
        bottom = pygame.Rect(viewport.left, viewport.bottom - 48, viewport.width, 48)
        pygame.draw.rect(surface, config.PALETTE.black, top)
        pygame.draw.rect(surface, config.PALETTE.black, bottom)
        label = "THE DOOR SEALS" if self.phase == "boss_intro" else f"{self.boss_def.name.upper()} APPEARS"
        image = fonts["large"].render(label, True, config.PALETTE.white)
        surface.blit(image, image.get_rect(center=viewport.center))

    def _draw_death(self, surface: pygame.Surface, viewport: pygame.Rect, fonts: dict[str, pygame.font.Font]) -> None:
        draw_rect_alpha(surface, viewport, config.PALETTE.black, 188)
        title = fonts["large"].render("YOU DIED", True, config.PALETTE.white)
        surface.blit(title, title.get_rect(center=(viewport.centerx, viewport.centery - 48)))
        self.return_button_rect = pygame.Rect(0, 0, 190, 38)
        self.return_button_rect.center = (viewport.centerx, viewport.centery + 34)
        hover = self.return_button_rect.collidepoint(pygame.mouse.get_pos())
        fill = config.PALETTE.white if hover else config.PALETTE.black
        mark = config.PALETTE.black if hover else config.PALETTE.white
        pygame.draw.rect(surface, fill, self.return_button_rect)
        pygame.draw.rect(surface, config.PALETTE.white, self.return_button_rect, 1)
        label = fonts["small"].render("RETURN TO GAME", True, mark)
        surface.blit(label, label.get_rect(center=self.return_button_rect.center))


def _formation_offsets(count: int, radius: float) -> list[pygame.Vector2]:
    if count <= 1:
        return [pygame.Vector2(0, 0)]
    pentagon = [
        pygame.Vector2(0, -radius),
        pygame.Vector2(radius * 0.95, -radius * 0.30),
        pygame.Vector2(radius * 0.58, radius * 0.88),
        pygame.Vector2(-radius * 0.58, radius * 0.88),
        pygame.Vector2(-radius * 0.95, -radius * 0.30),
    ]
    if count >= 5:
        return pentagon[:count]
    if count == 2:
        return [pentagon[0], pygame.Vector2(0, radius * 0.74)]
    if count == 3:
        return [pentagon[0], pentagon[2], pentagon[3]]
    return [pentagon[0], pentagon[1], pentagon[3], pygame.Vector2(0, radius * 0.78)]


def _cell_distance(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
