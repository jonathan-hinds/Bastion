from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import random

import pygame

from bastion import config
from bastion.engine.drawing import draw_circle_alpha, draw_ellipse_alpha, draw_line_alpha, draw_rect_alpha
from bastion.engine import hover_feedback
from bastion.engine.sprites import draw_building_sprite, draw_building_sprite_at, draw_terrain_shadow_overlay, draw_terrain_tile, draw_tower_sprite, terrain_sprite_frame
from bastion.game.elements import ElementalEffect, damage_multiplier, healing_multiplier
from bastion.game.ambient_mobs import AmbientMobManager
from bastion.game.entities import Beam, DamagePulse, Enemy, FloatingText, Particle, Tower
from bastion.game.fog import FogOfWar, VisionProfile, VisionSource
from bastion.game.grid import GameGrid
from bastion.game.items import ActiveItemBuff, DroppedItem, ITEM_DEFINITIONS, Inventory, apply_item, random_drop_item_id, random_scroll_id
from bastion.game.research import ResearchManager
from bastion.game.resources import GoldDeposit, MineralDeposit, MineralExtractor
from bastion.game.round_events import RoundEventManager
from bastion.game.terrain_shadows import TerrainShadowCalculator
from bastion.game.tower_defs import BUILD_COSTS, MINERAL_BUILD_COSTS, TOWER_BLUEPRINTS, stats_for, tower_name, xp_needed
from bastion.game.units import Barracks, ExpeditionCampsite, HOUSE_CAPACITY, HeroHall, House, Library, ResearchBuilding, ShieldGenerator, TROOP_DATA, Torch, TrainingGrounds, Troop
from bastion.game.waves import WaveManager
from bastion.game.expeditions import ExpeditionResult, ExpeditionRun


class CoreTarget:
    kind = "core"
    target_class = "core"
    radius = config.TILE_SIZE * 1.58
    arcane_capacity = config.ARCANE_CORE_CAPACITY

    def __init__(
        self,
        game: "GameState",
        cell: tuple[int, int],
        index: int = 1,
        max_health: float = float(config.TOWNHALL_MAX_HP),
        primary: bool = False,
    ) -> None:
        self.game = game
        self.cell = cell
        self.index = index
        self.display_name = "Core" if index == 1 else f"Core {index}"
        self.max_health = float(max_health)
        self.primary = primary
        self._health = self.max_health

    @property
    def pos(self) -> pygame.Vector2:
        return self.game.grid.world_center(self.cell)

    @property
    def health(self) -> float:
        if self.primary:
            return float(self.game.townhall_hp)
        return self._health

    @health.setter
    def health(self, value: float) -> None:
        if self.primary:
            self.game.townhall_hp = max(0, int(value))
        else:
            self._health = max(0.0, float(value))

    @property
    def alive(self) -> bool:
        return self.health > 0 and not self.game.game_over


@dataclass(slots=True)
class ArcaneLink:
    core: CoreTarget
    structure: object
    path: list[tuple[int, int]]
    phase: float


class WallTarget:
    kind = "wall"
    display_name = "Wall"
    target_class = "wall"
    radius = config.TILE_SIZE * 0.48

    def __init__(self, game: "GameState", cell: tuple[int, int]) -> None:
        self.game = game
        self.cell = cell

    @property
    def pos(self) -> pygame.Vector2:
        return self.game.grid.world_center(self.cell)

    @property
    def max_health(self) -> float:
        return self.game.grid.wall_max_health

    @property
    def health(self) -> float:
        return self.game.grid.wall_health.get(self.cell, self.max_health)

    @health.setter
    def health(self, value: float) -> None:
        self.game.grid.wall_health[self.cell] = max(0.0, min(self.max_health, float(value)))

    @property
    def alive(self) -> bool:
        return self.cell in self.game.grid.walls


BUILDING_SPRITE_WORLD_SIZE = config.TILE_SIZE * 1.125
WALL_SPRITE_WORLD_SIZE = float(config.TILE_SIZE)
CORE_SPRITE_WORLD_SIZE = 96.0


class GameState:
    def __init__(self) -> None:
        self.audio = None
        self.reset()

    def reset(self) -> None:
        self.grid = GameGrid()
        self.terrain_shadows = TerrainShadowCalculator()
        self.fog = None
        self.wave_manager = WaveManager(self.grid)
        self.ambient_mobs = AmbientMobManager(self.grid)
        self.round_events = RoundEventManager()
        self.research = ResearchManager()
        self.inventory = Inventory(capacity=16)
        self.active_item_buffs: list[ActiveItemBuff] = []
        self.dropped_items: list[DroppedItem] = []
        self.loot_banner_item_id: str | None = None
        self.loot_banner_timer = 0.0
        self.loot_banner_total = 4.0
        self.gold = config.STARTING_GOLD
        self.minerals = config.STARTING_MINERALS
        self.townhall_hp = config.TOWNHALL_MAX_HP
        self.core_target = CoreTarget(self, self.grid.townhall_cell, primary=True)
        self.core_targets: list[CoreTarget] = [self.core_target]
        self.towers: list[Tower] = []
        self.buildings: list[Barracks | House | MineralExtractor | Torch | TrainingGrounds | ExpeditionCampsite | HeroHall | ResearchBuilding | Library | ShieldGenerator] = []
        self.arcane_links: list[ArcaneLink] = []
        self.troops: list[Troop] = []
        self.enemies: list[Enemy] = []
        self.resource_deposits: list[MineralDeposit] = []
        self._resource_spawn_pool: list[tuple[int, int]] | None = None
        self.mineral_deposits = self.resource_deposits
        self.projectiles = []
        self.enemy_projectiles = []
        self.particles: list[Particle] = []
        self.damage_pulses: list[DamagePulse] = []
        self.ability_zones = []
        self.beams: list[Beam] = []
        self.texts: list[FloatingText] = []
        self.build_mode: str | None = "wall"
        self.selected_tower: Tower | None = None
        self.selected_barracks: Barracks | None = None
        self.selected_house: House | None = None
        self.selected_extractor: MineralExtractor | None = None
        self.selected_torch: Torch | None = None
        self.selected_training_grounds: TrainingGrounds | None = None
        self.selected_expedition_campsite: ExpeditionCampsite | None = None
        self.selected_hero_hall: HeroHall | None = None
        self.selected_research: ResearchBuilding | None = None
        self.selected_library: Library | None = None
        self.selected_shield: ShieldGenerator | None = None
        self.selected_troop: Troop | None = None
        self.selected_troops: list[Troop] = []
        self.control_groups: list[list[Troop]] = [[] for _ in range(5)]
        self.expedition_setup_party: list[Troop] = []
        self.expedition_setup_group: int | None = None
        self.expedition_run: ExpeditionRun | None = None
        self.expedition_recap: ExpeditionResult | None = None
        self.selected_wall: tuple[int, int] | None = None
        self.pending_camera_focus: pygame.Vector2 | None = None
        self.station_mode = False
        self.time_scale = 1.0
        self.day_duration_multiplier = 1.0
        self.paused = False
        self.game_over = False
        self.shake = 0.0
        self.notice = ""
        self.notice_timer = 0.0
        self.core_hit_text_timer = 0.0
        self.spatial_cell_size = 64
        self._enemy_bins: dict[tuple[int, int], list[Enemy]] = {}
        self._troop_bins: dict[tuple[int, int], list[Troop]] = {}
        self._spatial_ready = False
        self._spawn_initial_house_and_grunts()
        self._spawn_initial_minerals()
        self._spawn_initial_mineral_extractor()
        self._spawn_initial_gold_deposits()
        self.ambient_mobs.seed_initial_camps(self)
        self.fog = FogOfWar(self.grid)
        self.update_fog(0.0, immediate=True)

    def message(self, text: str) -> None:
        self.notice = text
        self.notice_timer = 2.0

    def play_sound(self, sound_id: str, random_pitch: bool = False) -> None:
        if self.audio is not None:
            self.audio.play(sound_id, random_pitch=random_pitch)

    def play_tower_sound(self, tower_kind: str) -> None:
        sound_id = {
            "archer": "tower_archer",
            "cannon": "tower_cannon",
            "wizard": "tower_mage",
        }.get(tower_kind)
        if sound_id is not None:
            self.play_sound(sound_id, random_pitch=True)

    def update_item_buffs(self, dt: float) -> None:
        for buff in self.active_item_buffs:
            buff.update(dt)
        self.active_item_buffs = [buff for buff in self.active_item_buffs if buff.alive]

    def item_multiplier(self, effect_key: str, default: float = 1.0) -> float:
        value = default
        for buff in self.active_item_buffs:
            raw = buff.effects.get(effect_key)
            if isinstance(raw, (int, float)):
                value *= float(raw)
        return value

    def item_flag(self, effect_key: str) -> bool:
        return any(bool(buff.effects.get(effect_key)) for buff in self.active_item_buffs)

    def day_duration_seconds(self) -> float:
        duration = config.BASE_DAY_DURATION * self.day_duration_multiplier
        return max(10.0, self.item_multiplier("day_duration_multiplier", duration))

    def set_day_duration_multiplier(self, multiplier: float) -> None:
        self.day_duration_multiplier = max(0.1, float(multiplier))

    def roll_scroll_item(self) -> str:
        return random_scroll_id()

    def roll_scroll_production_time(self) -> float:
        base = random.uniform(60.0, 120.0)
        return max(12.0, base * self.research.inverse_multiplier("scroll_production_time"))

    def add_item(self, item_id: str, quantity: int = 1) -> bool:
        if item_id not in ITEM_DEFINITIONS:
            return False
        if not self.inventory.add_item(item_id, quantity):
            return False
        definition = ITEM_DEFINITIONS[item_id]
        self.loot_banner_item_id = item_id
        self.loot_banner_timer = self.loot_banner_total
        self.message(f"LOOT {definition.name.upper()[:24]}")
        return True

    def use_inventory_slot(self, index: int) -> bool:
        slot = self.inventory.slot(index)
        if slot is None:
            return False
        item_id = slot.item_id
        definition = ITEM_DEFINITIONS.get(item_id)
        if definition is None:
            return False
        if definition.type != "scroll":
            self.message("ASSIGN TO TROOP")
            return False
        if not apply_item(self, item_id):
            self.message("ITEM FAILED")
            return False
        self.inventory.consume_slot(index)
        self.spawn_burst(self.core_target.pos, 24, 82)
        self.message(f"USED {definition.name.upper()[:24]}")
        return True

    def move_player_item_to_selected_troop(self, player_index: int, troop_index: int | None = None) -> bool:
        troop = self.selected_troop
        slot = self.inventory.slot(player_index)
        if troop is None or slot is None:
            return False
        definition = ITEM_DEFINITIONS.get(slot.item_id)
        if definition is None or definition.type == "scroll":
            self.message("TROOP ITEM ONLY")
            return False
        if troop_index is None:
            if not troop.inventory.has_space_for(slot.item_id):
                self.message("TROOP BAG FULL")
                return False
            added = troop.inventory.add_item(slot.item_id, 1)
        else:
            if not troop.inventory.has_space_for_at(troop_index, slot.item_id):
                self.message("SLOT FULL")
                return False
            added = troop.inventory.add_item_to_slot(troop_index, slot.item_id, 1)
        if not added:
            return False
        self.inventory.consume_slot(player_index)
        self.message(f"{troop.display_name.upper()} TOOK {definition.name.upper()[:14]}")
        return True

    def move_selected_troop_item_to_player(self, troop_index: int) -> bool:
        troop = self.selected_troop
        if troop is None:
            return False
        slot = troop.inventory.slot(troop_index)
        if slot is None:
            return False
        if not self.inventory.has_space_for(slot.item_id):
            self.message("INVENTORY FULL")
            return False
        item_id = troop.inventory.consume_slot(troop_index)
        if item_id is None:
            return False
        if not self.inventory.add_item(item_id, 1):
            troop.inventory.add_item_to_slot(troop_index, item_id, 1)
            return False
        definition = ITEM_DEFINITIONS.get(item_id)
        if definition is not None:
            self.message(f"STORED {definition.name.upper()[:20]}")
        return True

    def consume_selected_troop_item(self, troop_index: int) -> bool:
        troop = self.selected_troop
        if troop is None:
            return False
        slot = troop.inventory.slot(troop_index)
        definition = ITEM_DEFINITIONS.get(slot.item_id) if slot is not None else None
        if definition is None or definition.type != "consumable":
            self.message("NOT CONSUMABLE")
            return False
        if troop.consume_inventory_item(troop_index, self._troop_item_effect_context(troop)):
            self.message(f"{troop.display_name.upper()} USED {definition.name.upper()[:14]}")
            return True
        self.message("ITEM FAILED")
        return False

    def _troop_item_effect_context(self, troop: Troop):
        run = self.expedition_run
        if run is not None and troop in getattr(run, "troops", ()):
            return run
        return self

    def equip_selected_troop_item(self, troop_index: int, equipment_index: int | None = None) -> bool:
        troop = self.selected_troop
        if troop is None:
            return False
        slot = troop.inventory.slot(troop_index)
        definition = ITEM_DEFINITIONS.get(slot.item_id) if slot is not None else None
        if definition is None or definition.type != "equipment":
            self.message("NOT EQUIPMENT")
            return False
        if troop.equip_inventory_item(troop_index, equipment_index):
            self.message(f"EQUIPPED {definition.name.upper()[:18]}")
            self.spawn_hit(troop.pos, 3)
            return True
        self.message("GEAR SLOTS FULL")
        return False

    def unequip_selected_troop_item(self, equipment_index: int, troop_index: int | None = None) -> bool:
        troop = self.selected_troop
        if troop is None:
            return False
        slot = troop.equipment_slots[equipment_index] if 0 <= equipment_index < len(troop.equipment_slots) else None
        definition = ITEM_DEFINITIONS.get(slot.item_id) if slot is not None else None
        if definition is None:
            return False
        if troop.unequip_item(equipment_index, troop_index):
            self.message(f"UNEQUIPPED {definition.name.upper()[:16]}")
            self.spawn_hit(troop.pos, 2)
            return True
        self.message("TROOP BAG FULL")
        return False

    def equip_player_item_to_selected_troop(self, player_index: int, equipment_index: int | None = None) -> bool:
        if self.selected_troop is None:
            return False
        if equipment_index is not None and not 0 <= equipment_index < len(self.selected_troop.equipment_slots):
            return False
        if equipment_index is not None and self.selected_troop.equipment_slots[equipment_index] is not None:
            self.message("GEAR SLOT FULL")
            return False
        slot = self.inventory.slot(player_index)
        definition = ITEM_DEFINITIONS.get(slot.item_id) if slot is not None else None
        if definition is None or definition.type != "equipment":
            self.message("NOT EQUIPMENT")
            return False
        if not self.move_player_item_to_selected_troop(player_index):
            return False
        target_index = next((index for index, troop_slot in enumerate(self.selected_troop.inventory.slots) if troop_slot is not None and troop_slot.item_id == definition.id), None)
        if target_index is None:
            return False
        return self.equip_selected_troop_item(target_index, equipment_index)

    def consume_player_item_on_selected_troop(self, player_index: int) -> bool:
        if self.selected_troop is None:
            return False
        slot = self.inventory.slot(player_index)
        definition = ITEM_DEFINITIONS.get(slot.item_id) if slot is not None else None
        if definition is None or definition.type != "consumable":
            self.message("NOT CONSUMABLE")
            return False
        if not self.move_player_item_to_selected_troop(player_index):
            return False
        target_index = next((index for index, troop_slot in enumerate(self.selected_troop.inventory.slots) if troop_slot is not None and troop_slot.item_id == definition.id), None)
        if target_index is None:
            return False
        return self.consume_selected_troop_item(target_index)

    def heal_towers_and_buildings_full(self) -> int:
        self.refresh_tower_mod_health()
        targets = [tower for tower in self.towers if tower.alive] + [building for building in self.buildings if building.alive]
        healed = 0
        for target in targets:
            if getattr(target, "health", 0) < getattr(target, "max_health", 0):
                healed += 1
            target.health = target.max_health
            self.spawn_hit(target.pos, 2)
        return healed

    def heal_troops_full(self) -> int:
        healed = 0
        for troop in self.troops:
            if not troop.alive:
                continue
            if troop.health < troop.max_health:
                healed += 1
            troop.health = troop.max_health
            self.spawn_hit(troop.pos, 2)
        return healed

    def grant_tower_xp_all(self, minimum: int, maximum: int) -> int:
        awarded = 0
        for tower in self.towers:
            if not tower.alive:
                continue
            amount = random.randint(minimum, maximum)
            if tower.add_xp(amount):
                self.texts.append(FloatingText(pygame.Vector2(tower.pos), "READY", 0.9))
            awarded += amount
            self.texts.append(FloatingText(pygame.Vector2(tower.pos), f"+{amount}XP", 0.7))
        self.refresh_tower_mod_health()
        return awarded

    def grant_troop_xp_all(self, minimum: int, maximum: int) -> int:
        awarded = 0
        for troop in self.troops:
            if not troop.alive:
                continue
            amount = random.randint(minimum, maximum)
            if troop.add_xp(amount):
                self.texts.append(FloatingText(pygame.Vector2(troop.pos), "READY", 0.9))
            awarded += amount
            self.texts.append(FloatingText(pygame.Vector2(troop.pos), f"+{amount}XP", 0.7))
        return awarded

    def update(self, dt: float) -> None:
        self.notice_timer = max(0.0, self.notice_timer - dt)
        self.core_hit_text_timer = max(0.0, self.core_hit_text_timer - dt)
        self.loot_banner_timer = max(0.0, self.loot_banner_timer - dt)
        self.shake = max(0.0, self.shake - dt * 4.0)
        if self.paused or self.game_over:
            self.update_fog(dt)
            self._update_fx(dt)
            return

        self.update_item_buffs(dt)
        self.wave_manager.update(dt, self)
        self.ambient_mobs.update(dt, self)
        for building in list(self.buildings):
            if self.has_arcane_power(building):
                building.update(dt, self)
        for deposit in list(self.resource_deposits):
            deposit.update(dt, self)
        self.update_fog(dt)
        self.rebuild_spatial_index()
        for zone in list(self.ability_zones):
            zone.update(dt, self)
        for troop in list(self.troops):
            troop.update(dt, self)
        for dropped_item in list(self.dropped_items):
            dropped_item.update(dt, self)
        for tower in list(self.towers):
            if self.has_arcane_power(tower):
                tower.update(dt, self)
        for projectile in list(self.projectiles):
            projectile.update(dt, self)
        for enemy in list(self.enemies):
            enemy.update(dt, self)
        for projectile in list(self.enemy_projectiles):
            projectile.update(dt, self)

        self.projectiles = [projectile for projectile in self.projectiles if projectile.alive]
        self.enemy_projectiles = [projectile for projectile in self.enemy_projectiles if projectile.alive]
        self.ability_zones = [zone for zone in self.ability_zones if getattr(zone, "alive", False)]
        self.dropped_items = [dropped_item for dropped_item in self.dropped_items if dropped_item.alive]
        self.enemies = [enemy for enemy in self.enemies if enemy.alive]
        self.troops = [troop for troop in self.troops if troop.alive]
        self.towers = [tower for tower in self.towers if tower.alive]
        self.buildings = [building for building in self.buildings if building.alive]
        self._prune_arcane_links()
        self.selected_troops = [troop for troop in self.selected_troops if troop.alive]
        if self.selected_troop and not self.selected_troop.alive:
            self.selected_troop = self.selected_troops[0] if self.selected_troops else None
        self._prune_control_groups()
        if self.selected_barracks and not self.selected_barracks.alive:
            self.selected_barracks = None
        if self.selected_house and not self.selected_house.alive:
            self.selected_house = None
        if self.selected_extractor and not self.selected_extractor.alive:
            self.selected_extractor = None
        if self.selected_torch and not self.selected_torch.alive:
            self.selected_torch = None
        if self.selected_training_grounds and not self.selected_training_grounds.alive:
            self.selected_training_grounds = None
        if self.selected_expedition_campsite and not self.selected_expedition_campsite.alive:
            self.selected_expedition_campsite = None
        if self.selected_hero_hall and not self.selected_hero_hall.alive:
            self.selected_hero_hall = None
        if self.selected_research and not self.selected_research.alive:
            self.selected_research = None
        if self.selected_library and not self.selected_library.alive:
            self.selected_library = None
        if self.selected_shield and not self.selected_shield.alive:
            self.selected_shield = None
        self._spatial_ready = False
        self._update_fx(dt)

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
        return [
            enemy
            for enemy in self.nearby_enemies(pos, radius)
            if enemy.alive and self.is_world_explored(enemy.pos, enemy.radius)
        ]

    def nearby_troops(self, pos: pygame.Vector2, radius: float) -> list[Troop]:
        if not self._spatial_ready:
            self.rebuild_spatial_index()
        return self._nearby_from_bins(self._troop_bins, pos, radius)

    def update_fog(self, dt: float, immediate: bool = False) -> None:
        fog = getattr(self, "fog", None)
        if fog is None:
            return
        fog.update(dt, self._fog_sources(), immediate=immediate)

    def is_world_visible(self, pos: pygame.Vector2 | tuple[float, float], radius: float = 0.0) -> bool:
        return self.is_world_explored(pos, radius)

    def is_world_explored(self, pos: pygame.Vector2 | tuple[float, float], radius: float = 0.0) -> bool:
        fog = getattr(self, "fog", None)
        return True if fog is None else fog.is_explored_world(pos, radius)

    def is_cell_visible(self, cell: tuple[int, int]) -> bool:
        return self.is_cell_explored(cell)

    def is_cell_explored(self, cell: tuple[int, int]) -> bool:
        fog = getattr(self, "fog", None)
        return True if fog is None else fog.is_explored_cell(cell)

    def _fog_sources(self):
        fog = getattr(self, "fog", None)
        if fog is None:
            return []
        sources: list[VisionSource] = []
        for core in self.core_targets:
            if core.alive:
                sources.append(VisionSource(core.pos, fog.profile("core")))
        for troop in self.troops:
            if troop.alive:
                profile = fog.profile(getattr(troop, "target_class", "troop"), "troop")
                visibility = getattr(troop, "hero_multiplier", lambda _effect: 1.0)("visibility_range_multiplier")
                sources.append(VisionSource(troop.pos, VisionProfile(profile.radius * visibility, profile.hardness)))
        for tower in self.towers:
            if tower.alive and self.has_arcane_power(tower):
                sources.append(VisionSource(tower.pos, fog.profile("tower")))
        for building in self.buildings:
            if building.alive and self.has_arcane_power(building):
                sources.append(VisionSource(building.pos, fog.profile(getattr(building, "kind", "structure"), "structure")))
        return sources

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

    def aggro_candidates(self, pos: pygame.Vector2, radius: float):
        for core in self.core_targets:
            if core.alive:
                yield core
        for troop in self.nearby_troops(pos, radius):
            if troop.alive and float(getattr(troop, "stealth_time", 0.0)) <= 0:
                yield troop
        point = pygame.Vector2(pos)
        for structure in [tower for tower in self.towers if tower.alive] + [building for building in self.buildings if building.alive]:
            if structure.pos.distance_to(point) <= radius + structure.radius:
                yield structure

    def wall_target(self, cell: tuple[int, int]) -> WallTarget | None:
        if cell not in self.grid.walls:
            return None
        self.grid.wall_health.setdefault(cell, self.grid.wall_max_health)
        return WallTarget(self, cell)

    def find_enemy_wall_target(self, enemy: Enemy, desired_target):
        if getattr(enemy, "is_ranged", False) or not getattr(desired_target, "alive", False):
            return None

        direction = pygame.Vector2(enemy.vel)
        if direction.length_squared() < 64:
            direction = desired_target.pos - enemy.pos
        if direction.length_squared() > 0:
            direction = direction.normalize()
            for distance in (
                enemy.collision_radius + self.grid.tile_size * 0.35,
                enemy.collision_radius + self.grid.tile_size * 0.70,
                enemy.collision_radius + self.grid.tile_size * 1.05,
            ):
                cell = self.grid.cell_from_world(enemy.pos + direction * distance)
                obstruction = self.attackable_blocker_at(cell)
                if obstruction is not None:
                    return obstruction

        if enemy.navigator.stuck_time < 0.42:
            return None
        current = self.grid.cell_from_world(enemy.pos)
        choices = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                cell = (current[0] + dx, current[1] + dy)
                if cell in self.grid.walls or getattr(self.grid.towers.get(cell), "alive", False):
                    choices.append(cell)
        if not choices:
            return None
        return self.attackable_blocker_at(min(choices, key=lambda cell: self.grid.world_center(cell).distance_to(desired_target.pos)))

    def attackable_blocker_at(self, cell: tuple[int, int]):
        if cell in self.grid.walls:
            return self.wall_target(cell)
        structure = self.grid.towers.get(cell)
        if getattr(structure, "alive", False):
            return structure
        return None

    def emit_aggro(
        self,
        source,
        amount: float,
        reason: str,
        radius: float = 260.0,
        source_pos: pygame.Vector2 | None = None,
    ) -> None:
        if source is None or amount <= 0:
            return
        if hasattr(source, "aggro_generation_multiplier"):
            amount *= source.aggro_generation_multiplier()
            if amount <= 0:
                return
        origin = pygame.Vector2(source_pos if source_pos is not None else source.pos)
        for enemy in self.nearby_enemies(origin, radius):
            if enemy.alive and hasattr(enemy, "aggro"):
                distance = enemy.pos.distance_to(origin)
                falloff = 1.0 - min(0.55, distance / max(1.0, radius) * 0.55)
                enemy.aggro.add_threat(source, amount * falloff, reason)

    def aggro_suppressed_by_cover_fire(self, source) -> bool:
        if not isinstance(source, Troop) or not getattr(source, "alive", False):
            return False
        for tower in self.towers:
            if not tower.alive or not hasattr(tower, "abilities"):
                continue
            for ability in tower.abilities.passive_abilities:
                if hasattr(ability, "suppresses_aggro") and ability.suppresses_aggro(source, self):
                    return True
        return False

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
        if source is not None and not self.aggro_suppressed_by_cover_fire(source):
            self.emit_aggro(source, actual * 2.0 + 2.0, reason, radius=280.0)
        return actual

    def damage_wall(self, cell: tuple[int, int], amount: float, source_pos: pygame.Vector2 | None = None) -> bool:
        if cell not in self.grid.walls:
            return False
        health = self.grid.wall_health.get(cell, self.grid.wall_max_health) - amount
        self.grid.wall_health[cell] = health
        self.spawn_hit(self.grid.world_center(cell), min(8, 2 + int(amount / 8)))
        if health > 0:
            return False
        self.grid.remove_wall(cell)
        if self.selected_wall == cell:
            self.selected_wall = None
        self.spawn_burst(self.grid.world_center(cell), 18, 78)
        self.texts.append(FloatingText(self.grid.world_center(cell), "BREACH", 0.75))
        return True

    def _update_fx(self, dt: float) -> None:
        for collection in (self.particles, self.damage_pulses, self.beams, self.texts):
            for item in list(collection):
                item.update(dt)
        self.particles = [particle for particle in self.particles if particle.life > 0]
        self.damage_pulses = [pulse for pulse in self.damage_pulses if pulse.life > 0]
        self.beams = [beam for beam in self.beams if beam.life > 0]
        self.texts = [text for text in self.texts if text.life > 0]

    def spawn_enemy(self, kind: str, spawn_cell: tuple[int, int], wave: int) -> Enemy:
        return self.spawn_enemy_at(kind, self.grid.world_center(spawn_cell), wave)

    def spawn_enemy_at(
        self,
        kind: str,
        pos: pygame.Vector2,
        wave: int,
        behavior: str = "assault",
        home_pos: pygame.Vector2 | None = None,
        patrol_points: list[pygame.Vector2] | None = None,
        leash_radius: float | None = None,
        spawn_group: str = "wave",
    ) -> Enemy:
        enemy = Enemy(
            kind,
            pygame.Vector2(pos),
            wave,
            behavior=behavior,
            home_pos=home_pos,
            patrol_points=patrol_points,
            leash_radius=leash_radius,
            spawn_group=spawn_group,
        )
        self.enemies.append(enemy)
        self._spatial_ready = False
        return enemy

    def offer_round_event(self) -> bool:
        return self.round_events.maybe_offer(self)

    def choose_round_event(self, event_id: str) -> bool:
        return self.round_events.choose(event_id, self)

    def core_target_for(self, pos: pygame.Vector2) -> CoreTarget:
        live_cores = [core for core in self.core_targets if core.alive]
        if not live_cores:
            return self.core_target
        point = pygame.Vector2(pos)
        return min(live_cores, key=lambda core: core.pos.distance_to(point))

    def is_core_reserve(self, cell: tuple[int, int]) -> bool:
        for core in self.core_targets:
            cx, cy = core.cell
            if max(abs(cell[0] - cx), abs(cell[1] - cy)) <= 3:
                return True
        return False

    def can_build_on(self, cell: tuple[int, int]) -> bool:
        return self.grid.buildable(cell) and self.is_cell_explored(cell) and not self.is_core_reserve(cell) and self.active_resource_at(cell) is None

    def can_build_extractor_on(self, cell: tuple[int, int]) -> bool:
        deposit = self.resource_for_extractor_cell(cell)
        if deposit is None:
            return False
        cell = deposit.cell
        if not self.grid.buildable(cell) or not self.is_world_explored(deposit.pos, deposit.radius) or self.is_core_reserve(cell):
            return False
        claim = getattr(deposit, "claimed_by", None)
        return claim is None or not getattr(claim, "alive", False)

    def arcane_usage(self) -> tuple[int, int]:
        self._prune_arcane_links()
        live_cores = [core for core in self.core_targets if core.alive]
        return len(self.arcane_links), len(live_cores) * config.ARCANE_CORE_CAPACITY

    def arcane_core_load(self, core: CoreTarget) -> int:
        self._prune_arcane_links()
        return sum(1 for link in self.arcane_links if link.core is core)

    def has_arcane_power(self, structure) -> bool:
        if getattr(structure, "target_class", "") == "core":
            return True
        link = self.arcane_link_for(structure)
        return link is not None and link.core.alive

    def arcane_link_for(self, structure) -> ArcaneLink | None:
        for link in self.arcane_links:
            if link.structure is structure:
                return link
        return None

    def release_arcane_link(self, structure) -> None:
        self.arcane_links = [link for link in self.arcane_links if link.structure is not structure]

    def _prune_arcane_links(self) -> None:
        self.arcane_links = [
            link
            for link in self.arcane_links
            if getattr(link.structure, "alive", False) and link.core.alive
        ]

    def _reserve_arcane_link(self, structure, core: CoreTarget, path: list[tuple[int, int]]) -> None:
        self.release_arcane_link(structure)
        self.arcane_links.append(ArcaneLink(core, structure, path, random.random()))

    def arcane_source_for_cell(self, cell: tuple[int, int]) -> tuple[CoreTarget | None, list[tuple[int, int]], str]:
        self._prune_arcane_links()
        live_cores = [core for core in self.core_targets if core.alive]
        if not live_cores:
            return None, [], "NO CORE"

        saw_capacity = False
        saw_path = False
        for core in sorted(live_cores, key=lambda item: (abs(item.cell[0] - cell[0]) + abs(item.cell[1] - cell[1]), item.index)):
            if self.arcane_core_load(core) >= core.arcane_capacity:
                saw_capacity = True
                continue
            path = self._arcane_path(core.cell, cell)
            if path:
                return core, path, ""
            saw_path = True

        if saw_capacity and not any(self.arcane_core_load(core) < core.arcane_capacity for core in live_cores):
            return None, [], "ARCANE FULL"
        if saw_path:
            return None, [], "NO ARCANE PATH"
        return None, [], "ARCANE FULL"

    def _arcane_path(self, start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
        if not self.grid.in_bounds(start) or not self.grid.in_bounds(goal):
            return []
        if start == goal:
            return [start]

        queue: deque[tuple[int, int]] = deque([start])
        came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        gx, gy = goal
        while queue:
            current = queue.popleft()
            if current == goal:
                break
            x, y = current
            neighbors = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
            neighbors.sort(key=lambda item: abs(item[0] - gx) + abs(item[1] - gy))
            for neighbor in neighbors:
                if neighbor in came_from or not self.grid.in_bounds(neighbor):
                    continue
                if not self.grid.terrain.can_traverse(current, neighbor):
                    continue
                if neighbor not in (start, goal) and self.grid.blocked(neighbor):
                    continue
                came_from[neighbor] = current
                queue.append(neighbor)

        if goal not in came_from:
            return []

        path = [goal]
        current = goal
        while came_from[current] is not None:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path

    def _spawn_initial_minerals(self) -> None:
        for _ in range(config.MINERAL_DEPOSIT_COUNT):
            cell = self.random_mineral_cell()
            if cell is None:
                break
            deposit = MineralDeposit(cell)
            deposit.place(cell, self.grid)
            self.resource_deposits.append(deposit)

    def _spawn_initial_mineral_extractor(self) -> None:
        deposits = [
            deposit
            for deposit in self.resource_deposits
            if deposit.active and getattr(deposit, "kind", "") == "mineral" and getattr(deposit, "claimed_by", None) is None
        ]
        deposits.sort(key=lambda deposit: deposit.pos.distance_to(self.core_target.pos))
        for deposit in deposits:
            cell = deposit.cell
            if not self.grid.buildable(cell) or self.is_core_reserve(cell):
                continue
            core, path, _reason = self.arcane_source_for_cell(cell)
            if core is None or not path:
                continue
            extractor = MineralExtractor(cell, self.grid, deposit)
            ok, _reason = self.grid.try_add_tower(cell, extractor)
            if not ok:
                extractor.release_deposit()
                continue
            self._reserve_arcane_link(extractor, core, path)
            self.buildings.append(extractor)
            return

    def _spawn_initial_gold_deposits(self) -> None:
        for _ in range(config.GOLD_DEPOSIT_COUNT):
            cell = self.random_resource_deposit_cell()
            if cell is None:
                break
            deposit = GoldDeposit(cell)
            deposit.place(cell, self.grid)
            self.resource_deposits.append(deposit)

    def _spawn_initial_house_and_grunts(self) -> None:
        cell = self._random_starting_house_cell()
        if cell is None:
            return
        core, path, _reason = self.arcane_source_for_cell(cell)
        if core is None:
            return
        house = House(cell, self.grid)
        ok, _reason = self.grid.try_add_tower(cell, house)
        if not ok:
            return
        self._reserve_arcane_link(house, core, path)
        self.buildings.append(house)
        self.selected_house = house

        for _ in range(3):
            if not self.spawn_free_troop_near("grunt", house.cell):
                break

    def _random_starting_house_cell(self) -> tuple[int, int] | None:
        cx, cy = self.grid.townhall_cell
        max_radius = max(self.grid.width, self.grid.height)
        for radius in range(1, max_radius + 1):
            candidates = []
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    cell = (cx + dx, cy + dy)
                    if not self.can_build_on(cell):
                        continue
                    core, path, _reason = self.arcane_source_for_cell(cell)
                    if core is not None and path:
                        candidates.append(cell)
            if candidates:
                return random.choice(candidates)
        return None

    def random_resource_deposit_cell(self) -> tuple[int, int] | None:
        if not self._resource_spawn_pool:
            self._resource_spawn_pool = [
                (x, y)
                for x in range(3, self.grid.width - 3)
                for y in range(3, self.grid.height - 3)
                if self.grid.buildable((x, y)) and not self.is_core_reserve((x, y))
            ]
            random.shuffle(self._resource_spawn_pool)

        while self._resource_spawn_pool:
            cell = self._resource_spawn_pool.pop()
            if not self.grid.buildable(cell) or self.is_core_reserve(cell) or self.active_resource_at(cell) is not None:
                continue
            center = self.grid.world_center(cell)
            if any(core.pos.distance_to(center) < self.grid.tile_size * 8 for core in self.core_targets):
                continue
            if any(deposit.active and deposit.pos.distance_to(center) < self.grid.tile_size * 8 for deposit in self.resource_deposits):
                continue
            return cell
        return None

    def random_mineral_cell(self) -> tuple[int, int] | None:
        return self.random_resource_deposit_cell()

    def respawn_resource_deposit(self, deposit: MineralDeposit) -> None:
        claim = getattr(deposit, "claimed_by", None)
        if getattr(claim, "alive", False):
            deposit.place(claim.cell, self.grid)
            self.spawn_burst(deposit.pos, 14, 55)
            return
        cell = self.random_resource_deposit_cell()
        if cell is None:
            deposit.respawn_time = 15.0
            return
        deposit.place(cell, self.grid)
        self.spawn_burst(deposit.pos, 14, 55)

    def respawn_mineral_deposit(self, deposit: MineralDeposit) -> None:
        self.respawn_resource_deposit(deposit)

    def active_resource_at(self, cell: tuple[int, int]) -> MineralDeposit | None:
        for deposit in self.resource_deposits:
            if deposit.active and deposit.cell == cell:
                return deposit
        return None

    def active_mineral_at(self, cell: tuple[int, int]) -> MineralDeposit | None:
        return self.active_resource_at(cell)

    def resource_for_extractor_cell(self, cell: tuple[int, int]) -> MineralDeposit | None:
        direct = self.active_resource_at(cell)
        if direct is not None and self.is_world_explored(direct.pos, direct.radius):
            return direct
        if not self.grid.in_bounds(cell):
            return None

        center = self.grid.world_center(cell)
        reach = self.grid.tile_size * 0.72
        candidates = [
            deposit
            for deposit in self.resource_deposits
            if deposit.active
            and deposit.amount > 0
            and self.is_world_explored(deposit.pos, deposit.radius)
            and deposit.pos.distance_to(center) <= deposit.radius + reach
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda deposit: deposit.pos.distance_to(center))

    def mineral_for_extractor_cell(self, cell: tuple[int, int]) -> MineralDeposit | None:
        return self.resource_for_extractor_cell(cell)

    def resource_is_connected(self, deposit: MineralDeposit | None) -> bool:
        if deposit is None or not deposit.active or deposit.amount <= 0:
            return False
        extractor = getattr(deposit, "claimed_by", None)
        return isinstance(extractor, MineralExtractor) and extractor.alive and self.has_arcane_power(extractor)

    def mineral_is_connected(self, deposit: MineralDeposit | None) -> bool:
        return self.resource_is_connected(deposit)

    def find_resource_near(self, pos: pygame.Vector2, radius: float) -> MineralDeposit | None:
        point = pygame.Vector2(pos)
        candidates = [
            deposit
            for deposit in self.resource_deposits
            if self.resource_is_connected(deposit)
            and self.is_world_explored(deposit.pos, deposit.radius)
            and deposit.pos.distance_to(point) <= radius + deposit.radius
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda deposit: deposit.pos.distance_to(point))

    def find_mineral_near(self, pos: pygame.Vector2, radius: float) -> MineralDeposit | None:
        return self.find_resource_near(pos, radius)

    def add_resource(self, kind: str, amount: int, source=None) -> None:
        if kind == "gold":
            self.add_gold(amount, source)
        else:
            self.add_minerals(amount, source)

    def add_minerals(self, amount: int, source=None) -> None:
        if amount <= 0:
            return
        self.minerals += amount
        origin = pygame.Vector2(source.pos) if source is not None else self.core_target.pos
        self.texts.append(FloatingText(origin, f"+{amount}M", 0.7))

    def add_gold(self, amount: int, source=None) -> None:
        if amount <= 0:
            return
        self.gold += amount
        origin = pygame.Vector2(source.pos) if source is not None else self.core_target.pos
        self.texts.append(FloatingText(origin, f"+{amount}G", 0.7))

    def add_random_core(self, max_health: float = float(config.TOWNHALL_MAX_HP)) -> CoreTarget | None:
        cells = [
            (x, y)
            for x in range(2, self.grid.width - 2)
            for y in range(2, self.grid.height - 2)
            if not self.is_core_reserve((x, y)) and self.grid.buildable((x, y)) and self.active_resource_at((x, y)) is None
        ]
        random.shuffle(cells)
        for cell in cells[:500]:
            core = CoreTarget(self, cell, len(self.core_targets) + 1, max_health=max_health)
            ok, _ = self.grid.try_add_tower(cell, core)
            if not ok:
                continue
            self.core_targets.append(core)
            self.spawn_burst(core.pos, 36, 105)
            self.texts.append(FloatingText(pygame.Vector2(core.pos), "CORE", 0.9))
            return core
        return None

    def destroy_random_walls(self, fraction: float) -> int:
        walls = list(self.grid.walls)
        if not walls:
            return 0
        random.shuffle(walls)
        count = max(1, math.ceil(len(walls) * fraction))
        for cell in walls[:count]:
            self.grid.remove_wall(cell)
            self.spawn_burst(self.grid.world_center(cell), 12, 74)
            if self.selected_wall == cell:
                self.selected_wall = None
        return min(count, len(walls))

    def kill_all_troops(self) -> int:
        living = [troop for troop in self.troops if troop.alive]
        for troop in living:
            self.kill_troop(troop)
        self.troops = [troop for troop in self.troops if troop.alive]
        self.selected_troops = []
        self.selected_troop = None
        self.station_mode = False
        return len(living)

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
            if isinstance(owner, Tower):
                threat *= owner.mod_effect("aggro_multiplier", 1.0)
            elif isinstance(owner, Troop):
                threat *= owner.aggro_generation_multiplier()
            enemy.aggro.add_threat(owner, threat, "damage")
        killed = enemy.take_damage(actual_amount, owner)
        if effect is not None and enemy.alive:
            self.apply_elemental_effect(enemy, effect, owner, source_pos)
        if hasattr(enemy, "abilities"):
            enemy.abilities.on_owner_damaged(actual_amount, owner, source_pos, element, self)
        if not quiet and actual_amount >= 2:
            hit_source = source_pos
            if hit_source is None and owner is not None:
                hit_source = pygame.Vector2(owner.pos)
            if hit_source is not None:
                enemy.apply_knockback(actual_amount, hit_source)
            self.spawn_hit(enemy.pos, min(8, 2 + int(actual_amount / 12)))
            if critical:
                self.texts.append(FloatingText(pygame.Vector2(enemy.pos), "CRIT", 0.55))
        if owner is not None and hasattr(owner, "on_damage_dealt"):
            owner.on_damage_dealt(actual_amount, enemy, self)
        if killed:
            self.kill_enemy(enemy, owner or enemy.last_hit_by)

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
            enemy.apply_burn(
                effect.dot_dps,
                effect.duration,
                owner,
                spread_radius=effect.spread_radius,
                spread_falloff=effect.spread_falloff,
            )
        elif effect.element == "ice" and effect.duration > 0:
            enemy.apply_slow(effect.slow_multiplier, effect.duration, effect.attack_slow_multiplier)
        elif effect.element == "lightning" and effect.stun_duration > 0:
            enemy.apply_stun(effect.stun_duration)
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
        if not candidates:
            return
        candidates.sort(key=lambda enemy: enemy.pos.distance_to(source.pos))
        for enemy in candidates[:2]:
            distance = enemy.pos.distance_to(source.pos)
            falloff = 1.0 - min(0.75, distance / max(1.0, radius) * source.burn_spread_falloff)
            enemy.apply_burn(
                source.burn_dps * falloff,
                max(0.75, source.burn_time * 0.72),
                source.burn_owner,
                can_spread=False,
            )
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
            self.damage_enemy(
                current,
                current_damage,
                owner,
                source_pos=pygame.Vector2(previous_pos),
                element="lightning",
            )
            if current.alive:
                current.apply_stun(stun)
            previous_pos = pygame.Vector2(current.pos)
            current_damage *= falloff
            choices = [
                enemy
                for enemy in self.targetable_enemies_near(previous_pos, radius + 24)
                if enemy.alive and enemy not in hit and enemy.pos.distance_to(previous_pos) <= radius + enemy.radius
            ]
            current = min(choices, key=lambda enemy: enemy.pos.distance_to(previous_pos)) if choices else None
        return len(hit)

    def kill_enemy(self, enemy: Enemy, owner: Tower | None = None) -> None:
        if not enemy.alive:
            return
        enemy.alive = False
        if hasattr(enemy, "on_killed"):
            enemy.on_killed(self, owner)
        self.gold += enemy.reward
        self.texts.append(FloatingText(pygame.Vector2(enemy.pos), f"+{enemy.reward}", 0.7))
        self.spawn_death_explosion(enemy)
        self.maybe_drop_enemy_loot(enemy)
        if owner is not None:
            owner.kills += 1
            xp = max(8, enemy.reward * 2)
            if isinstance(owner, Tower):
                for tower in self.award_tower_xp(owner, xp):
                    self.texts.append(FloatingText(pygame.Vector2(tower.pos), "READY", 0.9))
            elif owner.add_xp(xp):
                self.texts.append(FloatingText(pygame.Vector2(owner.pos), "READY", 0.9))

    def maybe_drop_enemy_loot(self, enemy: Enemy) -> None:
        loot = getattr(enemy, "loot", {})
        chance = float(loot.get("drop_chance", 0.18)) if isinstance(loot, dict) else 0.18
        if random.random() > chance:
            return
        item_id = random_drop_item_id()
        if item_id is not None:
            self.drop_item_at(item_id, pygame.Vector2(enemy.pos))

    def drop_item_at(self, item_id: str, pos: pygame.Vector2) -> bool:
        if item_id not in ITEM_DEFINITIONS:
            return False
        self.dropped_items.append(DroppedItem(item_id, pygame.Vector2(pos)))
        return True

    def enemy_reaches_core(self, enemy: Enemy) -> None:
        if not enemy.alive:
            return
        enemy.alive = False
        self.damage_core(enemy.damage, pygame.Vector2(enemy.pos), self.core_target_for(enemy.pos))
        if self.game_over:
            return
        self.shake = max(self.shake, 1.0)
        self.spawn_burst(enemy.pos, 18, 95)
        self.texts.append(FloatingText(pygame.Vector2(enemy.pos), f"-{enemy.damage}", 0.8))

    def damage_core(
        self,
        amount: float,
        source_pos: pygame.Vector2 | None = None,
        target: CoreTarget | None = None,
    ) -> None:
        target = self.core_target if target is None else target
        if self.game_over or target.health <= 0:
            return
        target.health = max(0, target.health - amount)
        self.shake = max(self.shake, 1.0)
        self.spawn_hit(target.pos, min(10, 3 + int(amount / 5)))
        if self.core_hit_text_timer <= 0:
            self.core_hit_text_timer = 0.35
            self.texts.append(FloatingText(pygame.Vector2(target.pos), f"-{int(amount)}", 0.65))
        if target.health <= 0:
            target.health = 0
            self.game_over = True
            self.spawn_burst(target.pos, 42, 115)
            self.message(f"BASE DESTROYED - NIGHT {self.wave_manager.night_number}")

    def spawn_hit(self, pos: pygame.Vector2, count: int) -> None:
        for _ in range(count):
            angle = random.random() * math.tau
            speed = random.uniform(18, 70)
            vel = pygame.Vector2(math.cos(angle), math.sin(angle)) * speed
            self.particles.append(Particle(pygame.Vector2(pos), vel, random.uniform(0.12, 0.28), random.uniform(1.3, 3.0)))

    def spawn_burst(self, pos: pygame.Vector2, count: int, speed: float) -> None:
        for _ in range(count):
            angle = random.random() * math.tau
            vel = pygame.Vector2(math.cos(angle), math.sin(angle)) * random.uniform(speed * 0.25, speed)
            self.particles.append(Particle(pygame.Vector2(pos), vel, random.uniform(0.18, 0.45), random.uniform(1.5, 4.5)))

    def show_damage_impact(self, pos: pygame.Vector2, kind: str, radius: float = 0.0) -> None:
        center = pygame.Vector2(pos)
        if kind == "aoe":
            radius = max(18.0, float(radius))
            self.damage_pulses.append(DamagePulse(center, "aoe", radius, life=0.42, max_life=0.42))
            count = min(18, max(8, int(radius / 7)))
            for index in range(count):
                angle = index / count * math.tau + random.uniform(-0.09, 0.09)
                direction = pygame.Vector2(math.cos(angle), math.sin(angle))
                origin = center + direction * random.uniform(radius * 0.42, radius * 0.94)
                velocity = direction * random.uniform(26, 82)
                self.particles.append(Particle(origin, velocity, random.uniform(0.16, 0.34), random.uniform(1.1, 2.8)))
            return

        if kind == "multi":
            self.damage_pulses.append(DamagePulse(center, "multi", 30.0, life=0.32, max_life=0.32))
            for index in range(6):
                angle = index / 6 * math.tau + random.uniform(-0.2, 0.2)
                direction = pygame.Vector2(math.cos(angle), math.sin(angle))
                self.particles.append(Particle(center + direction * 4, direction * random.uniform(38, 88), random.uniform(0.11, 0.25), random.uniform(1.0, 2.4)))
            return

        self.damage_pulses.append(DamagePulse(center, "single", 24.0, life=0.24, max_life=0.24))
        self.spawn_burst(center, 6, 42)

    def spawn_death_explosion(self, enemy: Enemy) -> None:
        count = {"small": 18, "medium": 26, "large": 42}.get(enemy.kind, 22)
        speed = {"small": 145, "medium": 118, "large": 92}.get(enemy.kind, 110)
        center = pygame.Vector2(enemy.pos)
        for index in range(count):
            angle = (index / count) * math.tau + random.uniform(-0.18, 0.18)
            outward = pygame.Vector2(math.cos(angle), math.sin(angle))
            vel = outward * random.uniform(speed * 0.35, speed)
            life = random.uniform(0.24, 0.62)
            radius = random.uniform(1.8, 4.8) * (1.0 + enemy.radius / 24)
            self.particles.append(Particle(pygame.Vector2(center), vel, life, radius))

        for _ in range(7):
            angle = random.random() * math.tau
            outward = pygame.Vector2(math.cos(angle), math.sin(angle))
            length = random.uniform(enemy.radius * 1.2, enemy.radius * 2.6)
            self.beams.append(Beam(center, center + outward * length, random.uniform(0.08, 0.16), 1))

    def set_build_mode(self, mode: str | None) -> None:
        self.build_mode = mode
        self.station_mode = False
        self.clear_selection()

    def cancel(self) -> None:
        self.build_mode = None
        self.station_mode = False
        self.clear_selection()

    def clear_selection(self) -> None:
        self.selected_tower = None
        self.selected_barracks = None
        self.selected_house = None
        self.selected_extractor = None
        self.selected_torch = None
        self.selected_training_grounds = None
        self.selected_expedition_campsite = None
        self.selected_hero_hall = None
        self.selected_research = None
        self.selected_library = None
        self.selected_shield = None
        self.selected_troop = None
        self.selected_troops = []
        self.selected_wall = None

    def handle_world_click(self, world: pygame.Vector2, button: int) -> None:
        if self.round_events.awaiting_choice:
            return
        if button == 3:
            if self.selected_troops and not self.build_mode:
                self.set_selected_troops_station(world)
                return
            self.cancel()
            return
        if button != 1 or self.game_over:
            return

        cell = self.grid.cell_from_world(world)
        if self.station_mode and self.selected_troops:
            self.set_selected_troops_station(world)
            return
        if self.build_mode:
            self.try_build(cell)
        else:
            troop = self.find_troop_at(world)
            if troop is not None:
                self.select_troop(troop)
            else:
                self.select_cell(cell)

    def select_cell(self, cell: tuple[int, int]) -> None:
        self.clear_selection()
        structure = self.grid.towers.get(cell)
        if isinstance(structure, Tower):
            self.selected_tower = structure
        elif isinstance(structure, Barracks):
            self.selected_barracks = structure
        elif isinstance(structure, House):
            self.selected_house = structure
        elif isinstance(structure, MineralExtractor):
            self.selected_extractor = structure
        elif isinstance(structure, Torch):
            self.selected_torch = structure
        elif isinstance(structure, TrainingGrounds):
            self.selected_training_grounds = structure
        elif isinstance(structure, ExpeditionCampsite):
            self.selected_expedition_campsite = structure
        elif isinstance(structure, HeroHall):
            self.selected_hero_hall = structure
        elif isinstance(structure, ResearchBuilding):
            self.selected_research = structure
        elif isinstance(structure, Library):
            self.selected_library = structure
        elif isinstance(structure, ShieldGenerator):
            self.selected_shield = structure
        self.selected_wall = cell if cell in self.grid.walls else None
        if (
            self.selected_tower is not None
            or self.selected_barracks is not None
            or self.selected_house is not None
            or self.selected_extractor is not None
            or self.selected_torch is not None
            or self.selected_training_grounds is not None
            or self.selected_expedition_campsite is not None
            or self.selected_hero_hall is not None
            or self.selected_research is not None
            or self.selected_library is not None
            or self.selected_shield is not None
            or self.selected_wall is not None
        ):
            self.play_sound("menu_select")

    def select_troop(self, troop: Troop) -> None:
        self.clear_selection()
        self.build_mode = None
        self.station_mode = False
        self.selected_troop = troop
        self.selected_troops = [troop]
        self.play_sound("menu_select")

    def select_troops(self, troops: list[Troop]) -> None:
        self.clear_selection()
        self.build_mode = None
        self.station_mode = False
        self.selected_troops = [troop for troop in troops if troop.alive]
        self.selected_troop = self.selected_troops[0] if self.selected_troops else None
        if self.selected_troops:
            self.play_sound("menu_select")

    def select_troops_by_kind(self, kind: str) -> None:
        troops = [troop for troop in self.troops if troop.alive and troop.kind == kind]
        self.select_troops(troops)
        if troops:
            self.message(f"{len(troops)} {kind.upper()}")

    def select_troops_in_rect(self, world_rect: pygame.Rect) -> None:
        troops = [
            troop
            for troop in self.troops
            if troop.alive and world_rect.collidepoint(troop.pos.x, troop.pos.y)
        ]
        self.select_troops(troops)
        if len(troops) > 1:
            self.message(f"{len(troops)} TROOPS")

    def _prune_control_groups(self) -> None:
        live_ids = {id(troop) for troop in self.troops if troop.alive}
        for index, group in enumerate(self.control_groups):
            self.control_groups[index] = [troop for troop in group if troop.alive and id(troop) in live_ids]

    def control_group_troops(self, index: int) -> list[Troop]:
        if index < 0 or index >= len(self.control_groups):
            return []
        self._prune_control_groups()
        return list(self.control_groups[index])

    def assign_control_group(self, index: int) -> bool:
        if index < 0 or index >= len(self.control_groups):
            return False
        troops = [troop for troop in self.selected_troops if troop.alive]
        if not troops:
            return False
        self.control_groups[index] = list(troops)
        self.message(f"GROUP {index + 1} SET")
        return True

    def clear_control_group(self, index: int) -> bool:
        if index < 0 or index >= len(self.control_groups):
            return False
        if not self.control_groups[index]:
            return False
        self.control_groups[index] = []
        self.message(f"GROUP {index + 1} CLEARED")
        return True

    def focus_control_group(self, index: int) -> bool:
        troops = self.control_group_troops(index)
        if not troops:
            self.message(f"GROUP {index + 1} EMPTY")
            return False
        center = pygame.Vector2(0, 0)
        for troop in troops:
            center += troop.pos
        center /= len(troops)
        self.pending_camera_focus = center
        self.message(f"GROUP {index + 1}")
        return True

    def select_control_group(self, index: int) -> bool:
        troops = self.control_group_troops(index)
        if not troops:
            return False
        self.select_troops(troops)
        self.message(f"GROUP {index + 1}")
        return True

    def find_troop_at(self, world: pygame.Vector2) -> Troop | None:
        point = pygame.Vector2(world)
        candidates = [
            troop
            for troop in self.troops
            if troop.alive and troop.pos.distance_to(point) <= max(18, troop.radius * 2.2)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda troop: troop.pos.distance_to(point))

    def try_build(self, cell: tuple[int, int]) -> None:
        mode = self.build_mode
        if mode is None or self.round_events.awaiting_choice:
            return
        cost = BUILD_COSTS[mode]
        if self.gold < cost:
            self.message("NO GOLD")
            return
        mineral_cost = MINERAL_BUILD_COSTS.get(mode, 0)
        if self.minerals < mineral_cost:
            self.message("NO MINERALS")
            return

        if mode == "core":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core = CoreTarget(self, cell, len(self.core_targets) + 1)
            ok, reason = self.grid.try_add_tower(cell, core)
            if not ok:
                self.message(reason.upper())
                return
            self.core_targets.append(core)
            self.gold -= cost
            self.minerals -= mineral_cost
            self.clear_selection()
            self.spawn_burst(core.pos, 36, 105)
            self.texts.append(FloatingText(pygame.Vector2(core.pos), "CORE ONLINE", 0.9))
            self.message("CORE ONLINE")
            self.play_sound("menu_select")
            return

        if mode == "wall":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            ok, reason = self.grid.try_add_wall(cell)
            if not ok:
                self.message(reason.upper())
                return
            self.gold -= cost
            self.selected_wall = cell
            self.selected_tower = None
            self.selected_barracks = None
            self.selected_house = None
            self.selected_extractor = None
            self.selected_torch = None
            self.selected_training_grounds = None
            self.selected_expedition_campsite = None
            self.selected_hero_hall = None
            self.selected_research = None
            self.selected_library = None
            self.selected_shield = None
            self.selected_troop = None
            self.play_sound("menu_select")
            return

        if mode in TOWER_BLUEPRINTS:
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            tower = Tower(mode, cell, self.grid, self.research)
            ok, reason = self.grid.try_add_tower(cell, tower)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(tower, core, path)
            self.towers.append(tower)
            self.gold -= cost
            self.minerals -= mineral_cost
            self.selected_tower = tower
            self.selected_barracks = None
            self.selected_house = None
            self.selected_extractor = None
            self.selected_torch = None
            self.selected_training_grounds = None
            self.selected_expedition_campsite = None
            self.selected_hero_hall = None
            self.selected_research = None
            self.selected_library = None
            self.selected_shield = None
            self.selected_troop = None
            self.selected_wall = None
            self.play_sound("menu_select")

        if mode == "barracks":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            barracks = Barracks(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, barracks)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(barracks, core, path)
            self.buildings.append(barracks)
            self.gold -= cost
            self.clear_selection()
            self.selected_barracks = barracks
            self.play_sound("menu_select")

        if mode == "house":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            house = House(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, house)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(house, core, path)
            self.buildings.append(house)
            self.gold -= cost
            self.clear_selection()
            self.selected_house = house
            self.play_sound("menu_select")

        if mode == "extractor":
            deposit = self.resource_for_extractor_cell(cell)
            if deposit is None:
                self.message("NO DEPOSIT")
                return
            cell = deposit.cell
            if not self.can_build_extractor_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            extractor = MineralExtractor(cell, self.grid, deposit)
            ok, reason = self.grid.try_add_tower(cell, extractor)
            if not ok:
                extractor.release_deposit()
                self.message(reason.upper())
                return
            self._reserve_arcane_link(extractor, core, path)
            self.buildings.append(extractor)
            self.gold -= cost
            self.minerals -= mineral_cost
            self.clear_selection()
            self.selected_extractor = extractor
            self.message("EXTRACTOR ONLINE")
            self.play_sound("menu_select")

        if mode == "torch":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            torch = Torch(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, torch)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(torch, core, path)
            self.buildings.append(torch)
            self.gold -= cost
            self.clear_selection()
            self.selected_torch = torch
            self.play_sound("menu_select")

        if mode == "training_grounds":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            training_grounds = TrainingGrounds(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, training_grounds)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(training_grounds, core, path)
            self.buildings.append(training_grounds)
            self.gold -= cost
            self.clear_selection()
            self.selected_training_grounds = training_grounds
            self.play_sound("menu_select")

        if mode == "expedition_campsite":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            campsite = ExpeditionCampsite(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, campsite)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(campsite, core, path)
            self.buildings.append(campsite)
            self.gold -= cost
            self.minerals -= mineral_cost
            self.clear_selection()
            self.selected_expedition_campsite = campsite
            self.message("EXPEDITION CAMP ONLINE")
            self.play_sound("menu_select")

        if mode == "hero_hall":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            hero_hall = HeroHall(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, hero_hall)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(hero_hall, core, path)
            self.buildings.append(hero_hall)
            self.gold -= cost
            self.minerals -= mineral_cost
            self.clear_selection()
            self.selected_hero_hall = hero_hall
            self.message("HERO HALL ONLINE")
            self.play_sound("menu_select")

        if mode == "research":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            research = ResearchBuilding(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, research)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(research, core, path)
            self.buildings.append(research)
            self.gold -= cost
            self.clear_selection()
            self.selected_research = research
            self.play_sound("menu_select")

        if mode == "library":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            library = Library(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, library)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(library, core, path)
            self.buildings.append(library)
            self.gold -= cost
            self.clear_selection()
            self.selected_library = library
            self.play_sound("menu_select")

        if mode == "shield_generator":
            if not self.can_build_on(cell):
                self.message("BLOCKED")
                return
            core, path, reason = self.arcane_source_for_cell(cell)
            if core is None:
                self.message(reason)
                return
            shield = ShieldGenerator(cell, self.grid)
            ok, reason = self.grid.try_add_tower(cell, shield)
            if not ok:
                self.message(reason.upper())
                return
            self._reserve_arcane_link(shield, core, path)
            self.buildings.append(shield)
            self.gold -= cost
            self.minerals -= mineral_cost
            self.clear_selection()
            self.selected_shield = shield
            self.play_sound("menu_select")

    def sell_selected(self) -> None:
        if self.selected_tower is not None:
            tower = self.selected_tower
            base_cost = TOWER_BLUEPRINTS[tower.kind].cost
            refund = int(base_cost * 0.58)
            self.gold += refund
            mineral_refund = int(MINERAL_BUILD_COSTS.get(tower.kind, 0) * 0.5)
            self.minerals += mineral_refund
            self.release_arcane_link(tower)
            self.grid.remove_tower(tower.cell)
            if tower in self.towers:
                self.towers.remove(tower)
            label = f"+{refund}" if mineral_refund <= 0 else f"+{refund}/+{mineral_refund}M"
            self.texts.append(FloatingText(pygame.Vector2(tower.pos), label, 0.7))
            self.selected_tower = None
        elif self.selected_barracks is not None:
            barracks = self.selected_barracks
            refund = int(BUILD_COSTS["barracks"] * 0.55)
            self.gold += refund
            self.destroy_structure(barracks, quiet=True)
            self.texts.append(FloatingText(pygame.Vector2(barracks.pos), f"+{refund}", 0.7))
        elif self.selected_house is not None:
            house = self.selected_house
            refund = int(BUILD_COSTS["house"] * 0.55)
            self.gold += refund
            self.destroy_structure(house, quiet=True)
            self.texts.append(FloatingText(pygame.Vector2(house.pos), f"+{refund}", 0.7))
        elif self.selected_extractor is not None:
            extractor = self.selected_extractor
            refund = int(BUILD_COSTS["extractor"] * 0.55)
            self.gold += refund
            mineral_refund = int(MINERAL_BUILD_COSTS.get("extractor", 0) * 0.5)
            self.minerals += mineral_refund
            self.destroy_structure(extractor, quiet=True)
            label = f"+{refund}" if mineral_refund <= 0 else f"+{refund}/+{mineral_refund}M"
            self.texts.append(FloatingText(pygame.Vector2(extractor.pos), label, 0.7))
        elif self.selected_torch is not None:
            torch = self.selected_torch
            refund = int(BUILD_COSTS["torch"] * 0.55)
            self.gold += refund
            self.destroy_structure(torch, quiet=True)
            self.texts.append(FloatingText(pygame.Vector2(torch.pos), f"+{refund}", 0.7))
        elif self.selected_training_grounds is not None:
            training_grounds = self.selected_training_grounds
            refund = int(BUILD_COSTS["training_grounds"] * 0.55)
            self.gold += refund
            self.destroy_structure(training_grounds, quiet=True)
            self.texts.append(FloatingText(pygame.Vector2(training_grounds.pos), f"+{refund}", 0.7))
        elif self.selected_expedition_campsite is not None:
            campsite = self.selected_expedition_campsite
            refund = int(BUILD_COSTS["expedition_campsite"] * 0.55)
            self.gold += refund
            mineral_refund = int(MINERAL_BUILD_COSTS.get("expedition_campsite", 0) * 0.5)
            self.minerals += mineral_refund
            self.destroy_structure(campsite, quiet=True)
            label = f"+{refund}" if mineral_refund <= 0 else f"+{refund}/+{mineral_refund}M"
            self.texts.append(FloatingText(pygame.Vector2(campsite.pos), label, 0.7))
        elif self.selected_hero_hall is not None:
            hero_hall = self.selected_hero_hall
            refund = int(BUILD_COSTS["hero_hall"] * 0.55)
            self.gold += refund
            mineral_refund = int(MINERAL_BUILD_COSTS.get("hero_hall", 0) * 0.5)
            self.minerals += mineral_refund
            self.destroy_structure(hero_hall, quiet=True)
            label = f"+{refund}" if mineral_refund <= 0 else f"+{refund}/+{mineral_refund}M"
            self.texts.append(FloatingText(pygame.Vector2(hero_hall.pos), label, 0.7))
        elif self.selected_research is not None:
            research = self.selected_research
            refund = int(BUILD_COSTS["research"] * 0.55)
            self.gold += refund
            self.destroy_structure(research, quiet=True)
            self.texts.append(FloatingText(pygame.Vector2(research.pos), f"+{refund}", 0.7))
        elif self.selected_library is not None:
            library = self.selected_library
            refund = int(BUILD_COSTS["library"] * 0.55)
            self.gold += refund
            self.destroy_structure(library, quiet=True)
            self.texts.append(FloatingText(pygame.Vector2(library.pos), f"+{refund}", 0.7))
        elif self.selected_shield is not None:
            shield = self.selected_shield
            refund = int(BUILD_COSTS["shield_generator"] * 0.55)
            self.gold += refund
            mineral_refund = int(MINERAL_BUILD_COSTS.get("shield_generator", 0) * 0.5)
            self.minerals += mineral_refund
            self.destroy_structure(shield, quiet=True)
            label = f"+{refund}" if mineral_refund <= 0 else f"+{refund}/+{mineral_refund}M"
            self.texts.append(FloatingText(pygame.Vector2(shield.pos), label, 0.7))
        elif self.selected_wall is not None:
            cell = self.selected_wall
            self.grid.remove_wall(cell)
            refund = max(1, int(BUILD_COSTS["wall"] * 0.5))
            self.gold += refund
            self.texts.append(FloatingText(self.grid.world_center(cell), f"+{refund}", 0.7))
            self.selected_wall = None

    def train_selected(self, kind: str) -> None:
        if self.selected_barracks is None:
            return
        self.train_at_barracks(self.selected_barracks, kind)

    def train_at_barracks(self, barracks: Barracks | None, kind: str) -> None:
        if barracks is None or not barracks.alive or kind not in TROOP_DATA:
            return
        data = TROOP_DATA[kind]
        if self.gold < data.cost:
            self.message("NO GOLD")
            return
        if self.troop_supply_committed() >= self.troop_capacity():
            self.message("NO HOUSING")
            return
        if not barracks.can_queue():
            self.message("QUEUE FULL")
            return
        if barracks.queue_train(kind):
            self.gold -= data.cost
            self.message(f"TRAIN {kind.upper()}")

    def start_research_selected(self, research_id: str) -> None:
        if self.selected_research is None:
            return
        self.start_research_at(self.selected_research, research_id)

    def start_research_at(self, research: ResearchBuilding | None, research_id: str) -> None:
        if research is None or not research.alive:
            return
        research.start_research(research_id, self)

    def toggle_auto_research(self, research_id: str) -> None:
        enabled = self.research.toggle_auto(research_id)
        self.message(("AUTO " if enabled else "MANUAL ") + research_id.upper()[:18])

    def start_library_scroll_selected(self) -> None:
        if self.selected_library is None:
            return
        self.selected_library.start_scroll(self)

    def install_mod_selected(self, mod_id: str) -> None:
        if self.selected_tower is None:
            return
        if self.selected_tower.install_mod(mod_id, self):
            self.refresh_tower_mod_health()
            self.message("MOD INSTALLED")
            self.spawn_burst(self.selected_tower.pos, 18, 64)
        else:
            self.message("NEED XP")

    def level_up_selected_tower(self) -> None:
        if self.selected_tower is None:
            return
        if self.selected_tower.level_up():
            self.refresh_tower_mod_health()
            self.message(f"{self.selected_tower.display_name.upper()} LVL {self.selected_tower.level}")
            self.spawn_burst(self.selected_tower.pos, 26, 86)
            self.texts.append(FloatingText(pygame.Vector2(self.selected_tower.pos), "LEVEL UP", 0.9))
        else:
            self.message("NEED XP")

    def level_up_selected_troop(self) -> None:
        if self.selected_troop is None:
            return
        old_orbs = self.selected_troop.hero_orbs
        if self.selected_troop.level_up():
            gained_orb = self.selected_troop.hero_orbs > old_orbs
            suffix = " +ORB" if gained_orb else ""
            self.message(f"{self.selected_troop.display_name.upper()} LVL {self.selected_troop.level}{suffix}")
            self.spawn_burst(self.selected_troop.pos, 18, 72)
            self.texts.append(FloatingText(pygame.Vector2(self.selected_troop.pos), "+2 ATTR" + (" +ORB" if gained_orb else ""), 0.9))
        else:
            self.message("NEED XP")

    def allocate_selected_troop_attribute(self, attribute: str) -> None:
        if self.selected_troop is None:
            return
        if self.selected_troop.allocate_attribute(attribute):
            label = attribute.upper()[:8]
            self.message(f"{self.selected_troop.display_name.upper()} {label} +1")
            self.spawn_hit(self.selected_troop.pos, 3)
        else:
            self.message("NO POINTS")

    def ready_level_towers(self) -> list[Tower]:
        return [tower for tower in self.towers if tower.alive and tower.can_level_up()]

    def ready_level_troops(self) -> list[Troop]:
        return [troop for troop in self.troops if troop.alive and troop.can_level_up()]

    def tower_level_up_count(self, tower: Tower) -> int:
        xp = int(tower.xp)
        level = int(tower.level)
        count = 0
        while xp >= xp_needed(level):
            xp -= xp_needed(level)
            level += 1
            count += 1
        return count

    def select_tower_for_upgrade(self, tower: Tower | None) -> bool:
        if tower is None or not tower.alive:
            return False
        self.build_mode = None
        self.station_mode = False
        self.clear_selection()
        self.selected_tower = tower
        self.pending_camera_focus = pygame.Vector2(tower.pos)
        self.message(f"{tower.display_name.upper()} READY")
        return True

    def select_troop_for_upgrade(self, troop: Troop | None) -> bool:
        if troop is None or not troop.alive:
            return False
        self.build_mode = None
        self.station_mode = False
        self.clear_selection()
        self.selected_troop = troop
        self.selected_troops = [troop]
        self.pending_camera_focus = pygame.Vector2(troop.pos)
        self.message(f"{troop.display_name.upper()} READY")
        return True

    def consume_camera_focus(self) -> pygame.Vector2 | None:
        focus = self.pending_camera_focus
        self.pending_camera_focus = None
        return focus

    def level_up_all_ready_towers(self) -> tuple[int, int]:
        towers = self.ready_level_towers()
        tower_count = 0
        level_count = 0
        for tower in towers:
            gained = 0
            while tower.alive and tower.level_up():
                gained += 1
            if gained <= 0:
                continue
            tower_count += 1
            level_count += gained
            self.spawn_burst(tower.pos, 16, 76)
            label = "LEVEL UP" if gained == 1 else f"+{gained} LVL"
            self.texts.append(FloatingText(pygame.Vector2(tower.pos), label, 0.9))

        if level_count <= 0:
            self.message("NO READY TOWERS")
            return 0, 0

        self.refresh_tower_mod_health()
        self.message(f"{tower_count} TOWERS +{level_count} LVL")
        return tower_count, level_count

    def troop_capacity(self) -> int:
        return sum(HOUSE_CAPACITY for building in self.buildings if isinstance(building, House) and building.alive)

    def queued_troop_count(self) -> int:
        return sum(len(building.train_queue) for building in self.buildings if isinstance(building, Barracks) and building.alive)

    def troop_supply_committed(self) -> int:
        return sum(1 for troop in self.troops if troop.alive) + self.queued_troop_count()

    def spawn_troop(self, kind: str, barracks: Barracks) -> bool:
        if sum(1 for troop in self.troops if troop.alive) >= self.troop_capacity():
            return False
        data = TROOP_DATA[kind]
        nav_radius = self.grid.navigation_radius(data.radius)
        candidates = self._spawn_cells_around(barracks.cell, max_radius=3)
        for cell in candidates:
            pos = self.grid.world_center(cell)
            if not self.grid.circle_clear(pos, nav_radius):
                continue
            if any(troop.alive and troop.pos.distance_to(pos) < troop.radius + data.radius + 3 for troop in self.troops):
                continue
            troop = Troop(kind, pos, pos)
            self.troops.append(troop)
            self.spawn_burst(troop.pos, 8, 46)
            return True
        return False

    def spawn_free_troop_near(self, kind: str, center: tuple[int, int]) -> bool:
        if kind not in TROOP_DATA:
            return False
        if sum(1 for troop in self.troops if troop.alive) >= self.troop_capacity():
            return False
        data = TROOP_DATA[kind]
        nav_radius = self.grid.navigation_radius(data.radius)
        candidates = [center] + self._spawn_cells_around(center, max_radius=4)
        for cell in candidates:
            pos = self.grid.world_center(cell)
            if not self.grid.circle_clear(pos, nav_radius):
                continue
            if any(troop.alive and troop.pos.distance_to(pos) < troop.radius + data.radius + 3 for troop in self.troops):
                continue
            troop = Troop(kind, pos, pos)
            self.troops.append(troop)
            return True
        return False

    def _spawn_cells_around(self, center: tuple[int, int], max_radius: int) -> list[tuple[int, int]]:
        cells = []
        cx, cy = center
        for radius in range(1, max_radius + 1):
            ring = []
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    cell = (cx + dx, cy + dy)
                    if self.grid.in_bounds(cell):
                        ring.append(cell)
            random.shuffle(ring)
            cells.extend(ring)
        return cells

    def begin_station_selected(self) -> None:
        if self.selected_troops:
            self.station_mode = True
            self.build_mode = None
            self.message("SET STATION")

    def toggle_selected_troop_engagement(self) -> None:
        troops = [troop for troop in self.selected_troops if troop.alive]
        if not troops:
            return
        should_hold = any(troop.attack_enabled for troop in troops)
        for troop in troops:
            troop.attack_enabled = not should_hold
            if should_hold:
                troop.target = None
        self.message("HOLD FIRE" if should_hold else "ENGAGE")

    def set_selected_troop_station(self, world: pygame.Vector2) -> None:
        if self.selected_troop is None:
            return
        self.selected_troop.set_station(pygame.Vector2(world), self.grid)
        self.station_mode = False
        self.message("STATION SET")

    def set_selected_troops_station(self, world: pygame.Vector2) -> None:
        if not self.selected_troops:
            return
        center = pygame.Vector2(world)
        offsets = self._formation_offsets(len(self.selected_troops), spacing=24)
        for troop, offset in zip(self.selected_troops, offsets):
            troop.set_station(center + offset, self.grid)
        self.station_mode = False
        self.message("GROUP STATION")

    def _formation_offsets(self, count: int, spacing: float) -> list[pygame.Vector2]:
        if count <= 1:
            return [pygame.Vector2(0, 0)]
        columns = math.ceil(math.sqrt(count))
        rows = math.ceil(count / columns)
        offsets = []
        for index in range(count):
            col = index % columns
            row = index // columns
            x = (col - (columns - 1) / 2) * spacing
            y = (row - (rows - 1) / 2) * spacing
            offsets.append(pygame.Vector2(x, y))
        return offsets

    def adjacent_towers(self, tower: Tower) -> list[Tower]:
        neighbors = []
        x, y = tower.cell
        for cell in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            other = self.grid.towers.get(cell)
            if isinstance(other, Tower) and other.alive:
                neighbors.append(other)
        return neighbors

    def shield_generators(self) -> list[ShieldGenerator]:
        return [building for building in self.buildings if isinstance(building, ShieldGenerator) and building.alive]

    def hero_halls(self) -> list[HeroHall]:
        return [building for building in self.buildings if isinstance(building, HeroHall) and building.alive]

    def expedition_campsites(self) -> list[ExpeditionCampsite]:
        return [building for building in self.buildings if isinstance(building, ExpeditionCampsite) and building.alive and self.has_arcane_power(building)]

    def expedition_available(self) -> bool:
        return bool(self.expedition_campsites())

    def register_expedition_control_group(self, index: int) -> bool:
        if not self.expedition_available():
            self.message("NEED EXPEDITION CAMP")
            return False
        troops = self.control_group_troops(index)
        troops = [troop for troop in troops if troop.alive]
        if not troops:
            self.message(f"GROUP {index + 1} EMPTY")
            return False
        if len(troops) > 5:
            troops = troops[:5]
            self.message("PARTY CAPPED AT 5")
        self.expedition_setup_party = list(troops)
        self.expedition_setup_group = index
        self.build_mode = None
        self.station_mode = False
        self.clear_selection()
        return True

    def reorder_expedition_party(self, source_index: int, target_index: int) -> bool:
        party = self.expedition_setup_party
        if not 0 <= source_index < len(party) or not 0 <= target_index < len(party) or source_index == target_index:
            return False
        troop = party.pop(source_index)
        party.insert(target_index, troop)
        self.message("PARTY ORDER")
        return True

    def cancel_expedition_setup(self) -> None:
        self.expedition_setup_party = []
        self.expedition_setup_group = None
        self.message("EXPEDITION CANCELLED")

    def start_expedition_from_setup(self) -> bool:
        if self.expedition_run is not None or self.expedition_recap is not None:
            return False
        if not self.expedition_available():
            self.message("NEED EXPEDITION CAMP")
            return False
        party = [troop for troop in self.expedition_setup_party if troop.alive]
        if not party:
            self.message("REGISTER PARTY")
            return False
        party = party[:5]
        self.expedition_setup_party = list(party)
        self.expedition_run = ExpeditionRun(self, party)
        self.expedition_setup_party = []
        self.expedition_setup_group = None
        self.paused = False
        self.build_mode = None
        self.station_mode = False
        self.clear_selection()
        self.message("EXPEDITION STARTED")
        return True

    def finish_expedition_run(self, result: ExpeditionResult) -> None:
        run = self.expedition_run
        if run is not None:
            run.restore_party_to_base()
        self.expedition_run = None
        self.expedition_recap = result
        self.paused = True
        self.message("EXPEDITION RETURN")

    def abort_expedition_as_loss(self) -> bool:
        run = self.expedition_run
        if run is None:
            return False
        run.close_as_loss()
        if run.finished_result is not None:
            self.finish_expedition_run(run.finished_result)
        return True

    def accept_expedition_recap(self) -> bool:
        result = self.expedition_recap
        if result is None:
            return False
        if result.victory:
            self.gold += result.gold
            for item_id in result.items:
                if not self.add_item(item_id):
                    self.drop_item_at(item_id, pygame.Vector2(self.core_target.pos))
            for troop in result.party:
                if troop.alive:
                    gained = int(result.xp_by_troop_id.get(id(troop), 0))
                    if gained > 0 and troop.add_xp(gained):
                        self.texts.append(FloatingText(pygame.Vector2(troop.pos), "READY", 0.9))
        self.troops = [troop for troop in self.troops if troop.alive]
        self.selected_troops = [troop for troop in self.selected_troops if troop.alive]
        self.selected_troop = self.selected_troops[0] if self.selected_troops else None
        self._prune_control_groups()
        self.expedition_recap = None
        self.paused = False
        self.message("EXPEDITION COMPLETE" if result.victory else "EXPEDITION LOST")
        return True

    def purchase_hero_node_for_selected(self, node_id: str) -> bool:
        troop = self.selected_troop if len(self.selected_troops) == 1 else None
        if troop is None or not troop.alive or not troop.has_hero_tree():
            self.message("SELECT TROOP")
            return False
        if not self.hero_halls():
            self.message("NEED HERO HALL")
            return False
        tree = troop.hero_tree()
        node = tree.node(node_id) if tree is not None else None
        if node is None:
            self.message("LOCKED")
            return False
        if troop.hero_orbs < node.cost:
            self.message("NO ORBS")
            return False
        if node.max_rank is not None and troop.hero_node_rank(node.node_id) >= node.max_rank:
            self.message("MAXED")
            return False
        if node.requires is not None and troop.hero_node_rank(node.requires) <= 0:
            self.message("LOCKED")
            return False
        if troop.purchase_hero_node(node_id):
            label = node.name.upper()[:18] if node is not None else "HERO NODE"
            self.message(label)
            self.spawn_burst(troop.pos, 14, 64)
            self.texts.append(FloatingText(pygame.Vector2(troop.pos), "ASCEND", 0.8))
            return True
        self.message("LOCKED")
        return False

    def connected_structure_cells(self, start: tuple[int, int]) -> set[tuple[int, int]]:
        if start not in self.grid.towers:
            return {start}
        visited = {start}
        stack = [start]
        while stack:
            x, y = stack.pop()
            for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbor in visited:
                    continue
                structure = self.grid.towers.get(neighbor)
                if structure is None or not getattr(structure, "alive", False):
                    continue
                visited.add(neighbor)
                stack.append(neighbor)
        return visited

    def shield_for_target(self, target):
        cell = getattr(target, "cell", None)
        if cell is None:
            return None
        best = None
        best_size = -1
        for generator in self.shield_generators():
            generator.set_network(self.connected_structure_cells(generator.cell))
            if cell not in generator.network_cells:
                continue
            size = len(generator.network_cells)
            if size > best_size:
                best = generator
                best_size = size
        return best

    def restore_nearby_shield(self, pos: pygame.Vector2, radius: float, amount: float) -> float:
        generators = [
            generator
            for generator in self.shield_generators()
            if generator.pos.distance_to(pos) <= radius + generator.radius and generator.shield < generator.shield_max
        ]
        if not generators:
            return 0.0
        target = min(generators, key=lambda generator: (generator.shield / max(1.0, generator.shield_max), generator.pos.distance_to(pos)))
        restored = target.restore_shield(amount)
        if restored > 0:
            self.spawn_hit(target.pos, 1)
        return restored

    def connected_tower_group(self, tower: Tower) -> list[Tower]:
        group = []
        pending = [tower]
        seen = {tower.cell}
        while pending:
            current = pending.pop()
            group.append(current)
            for neighbor in self.adjacent_towers(current):
                if neighbor.cell in seen:
                    continue
                seen.add(neighbor.cell)
                pending.append(neighbor)
        return group

    def award_tower_xp(self, tower: Tower, amount: int) -> list[Tower]:
        group = self.connected_tower_group(tower)
        recipients = group if any(member.has_mod("exp_share") for member in group) else [tower]
        leveled = []
        for recipient in recipients:
            if recipient.add_xp(amount):
                leveled.append(recipient)
        self.refresh_tower_mod_health()
        return leveled

    def refresh_tower_mod_health(self) -> None:
        for tower in self.towers:
            if tower.alive:
                tower.recalculate_mod_health(self)

    def find_enemy_attack_target(self, pos: pygame.Vector2):
        structures = [tower for tower in self.towers if tower.alive] + [building for building in self.buildings if building.alive]
        if not structures:
            return None
        return min(structures, key=lambda target: target.pos.distance_to(pos))

    def damage_friendly(
        self,
        target,
        amount: float,
        source_pos: pygame.Vector2 | None = None,
        element: str = "physical",
        source=None,
    ) -> None:
        if not getattr(target, "alive", False):
            return
        if getattr(target, "target_class", "") == "core":
            self.damage_core(amount, source_pos, target)
            return
        if getattr(target, "target_class", "") == "wall":
            self.damage_wall(target.cell, amount, source_pos)
            return
        if isinstance(target, Troop) and self.item_flag("troop_invincible"):
            self.spawn_hit(target.pos, 2)
            return
        actual_amount = amount * damage_multiplier(target, element)
        if isinstance(target, Troop):
            actual_amount = target.reduce_damage_by_armor(actual_amount)
        if actual_amount <= 0:
            return
        if not isinstance(target, Troop):
            shield = self.shield_for_target(target)
            if shield is not None and shield.shield_active:
                actual_amount = shield.absorb_damage(actual_amount)
                self.spawn_hit(target.pos, 2)
                self.show_damage_impact(target.pos, "single", 0.0)
                if actual_amount <= 0:
                    return
        actual_amount = self._modify_friendly_incoming_damage(target, actual_amount, source, source_pos, element)
        if actual_amount <= 0:
            self.spawn_hit(target.pos, 2)
            return
        redirected_target = self._redirect_fatal_friendly_damage(target, actual_amount, source, source_pos, element)
        if redirected_target is not target:
            target = redirected_target
            actual_amount = self._modify_friendly_incoming_damage(target, actual_amount, source, source_pos, element)
            if isinstance(target, Troop):
                actual_amount = target.reduce_damage_by_armor(actual_amount)
            if actual_amount <= 0:
                self.spawn_hit(target.pos, 2)
                return
        killed = target.take_damage(actual_amount, self) if isinstance(target, Troop) else target.take_damage(actual_amount)
        if hasattr(target, "abilities"):
            target.abilities.on_owner_damaged(actual_amount, source, source_pos, element, self)
        self.spawn_hit(target.pos, min(9, 3 + int(actual_amount / 8)))
        if killed:
            if isinstance(target, Troop):
                self.kill_troop(target)
            else:
                self.destroy_structure(target)

    def _modify_friendly_incoming_damage(self, target, amount: float, source, source_pos: pygame.Vector2 | None, element: str) -> float:
        if amount <= 0 or not hasattr(target, "abilities"):
            return amount
        return target.abilities.modify_incoming_damage(amount, source, source_pos, element, self)

    def _redirect_fatal_friendly_damage(self, target, amount: float, source, source_pos: pygame.Vector2 | None, element: str):
        if amount <= 0 or not getattr(target, "alive", False) or not hasattr(target, "health"):
            return target
        if amount < float(getattr(target, "health", 0.0)):
            return target
        if not self.troops:
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
        self.spawn_burst(troop.pos, 18, 85)
        self.texts.append(FloatingText(pygame.Vector2(troop.pos), "DOWN", 0.75))
        if self.selected_troop is troop:
            self.selected_troop = None
            self.station_mode = False
        if troop in self.selected_troops:
            self.selected_troops.remove(troop)
            self.selected_troop = self.selected_troops[0] if self.selected_troops else None
            if not self.selected_troops:
                self.station_mode = False

    def destroy_structure(self, structure, quiet: bool = False) -> None:
        if not getattr(structure, "alive", False):
            return
        self.release_arcane_link(structure)
        if isinstance(structure, MineralExtractor):
            structure.release_deposit()
        structure.alive = False
        self.grid.remove_tower(structure.cell)
        if isinstance(structure, Tower) and structure in self.towers:
            self.towers.remove(structure)
        if isinstance(structure, (Barracks, House, MineralExtractor, Torch, TrainingGrounds, ExpeditionCampsite, HeroHall, ResearchBuilding, Library, ShieldGenerator)) and structure in self.buildings:
            self.buildings.remove(structure)
        if self.selected_tower is structure:
            self.selected_tower = None
        if self.selected_barracks is structure:
            self.selected_barracks = None
        if self.selected_house is structure:
            self.selected_house = None
        if self.selected_extractor is structure:
            self.selected_extractor = None
        if self.selected_torch is structure:
            self.selected_torch = None
        if self.selected_training_grounds is structure:
            self.selected_training_grounds = None
        if self.selected_expedition_campsite is structure:
            self.selected_expedition_campsite = None
        if self.selected_hero_hall is structure:
            self.selected_hero_hall = None
        if self.selected_research is structure:
            self.selected_research = None
        if self.selected_library is structure:
            self.selected_library = None
        if self.selected_shield is structure:
            self.selected_shield = None
        if isinstance(structure, Tower):
            self.refresh_tower_mod_health()
        if not quiet:
            self.spawn_burst(structure.pos, 28, 92)
            self.texts.append(FloatingText(pygame.Vector2(structure.pos), "DESTROYED", 0.9))

    def specialize_selected(self, option: str) -> None:
        if self.selected_tower and self.selected_tower.specialize(option):
            self.message(self.selected_tower.display_name.upper())
            self.spawn_burst(self.selected_tower.pos, 24, 80)

    def start_wave(self) -> None:
        self.start_night()

    def start_night(self) -> None:
        if self.round_events.awaiting_choice:
            return
        self.wave_manager.start_next_wave(self)

    def draw_world(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        fonts: dict[str, pygame.font.Font],
        mouse_pos: tuple[int, int],
        hover_target=None,
    ) -> None:
        shake_offset = pygame.Vector2(0, 0)
        if self.shake > 0:
            shake_offset = pygame.Vector2(random.uniform(-3, 3), random.uniform(-3, 3)) * self.shake
        original_viewport = viewport.copy()
        viewport = viewport.move(int(shake_offset.x), int(shake_offset.y))
        surface.fill(config.PALETTE.world_bg, original_viewport)
        previous_clip = surface.get_clip()
        surface.set_clip(original_viewport)

        self._draw_terrain(surface, camera, viewport)
        self._draw_map_boundary(surface, camera, viewport)

        mouse_in_world = original_viewport.collidepoint(mouse_pos)
        if mouse_in_world:
            self._draw_core_reserve_hover(surface, camera, viewport, mouse_pos)
        if mouse_in_world:
            self._draw_build_preview(surface, camera, viewport, mouse_pos)

        if self.selected_tower:
            self.selected_tower.draw_range(surface, camera, viewport, self)

        hover_kind = hover_target[0] if isinstance(hover_target, tuple) and hover_target else None
        hover_value = hover_target[1] if isinstance(hover_target, tuple) and len(hover_target) > 1 else None
        hover_wall = hover_value if hover_kind == "wall" else None

        self._draw_walls(surface, camera, viewport, hover_wall)
        self._draw_arcane_network(surface, camera, viewport)
        self.ambient_mobs.draw_base_arcane_networks(surface, camera, viewport, self)
        self._draw_shield_networks(surface, camera, viewport)

        render_jobs = []
        for core in self.core_targets:
            render_jobs.append((
                *self._render_sort_key(core.pos, 30),
                lambda core=core: self._draw_shadowed_structure(
                    surface,
                    camera,
                    viewport,
                    core,
                    lambda: self._draw_core(surface, camera, viewport, fonts["tiny"], core),
                    world_size=CORE_SPRITE_WORLD_SIZE,
                ),
            ))

        for deposit in self.resource_deposits:
            if not self.is_world_explored(deposit.pos, deposit.radius):
                continue
            deposit.harvest_enabled = self.resource_is_connected(deposit)
            render_jobs.append((*self._render_sort_key(deposit.pos, 18), lambda deposit=deposit: deposit.draw(surface, camera, viewport)))

        for dropped_item in self.dropped_items:
            if self.is_world_explored(dropped_item.pos, 12):
                render_jobs.append((*self._render_sort_key(dropped_item.pos, 19), lambda dropped_item=dropped_item: dropped_item.draw(surface, camera, viewport)))

        for zone in self.ability_zones:
            if self.is_world_explored(zone.pos, zone.radius):
                render_jobs.append((*self._render_sort_key(zone.pos, 20), lambda zone=zone: zone.draw(surface, camera, viewport)))

        for building in self.buildings:
            selected = (
                building is self.selected_barracks
                or building is self.selected_house
                or building is self.selected_extractor
                or building is self.selected_torch
                or building is self.selected_training_grounds
                or building is self.selected_expedition_campsite
                or building is self.selected_hero_hall
                or building is self.selected_research
                or building is self.selected_library
                or building is self.selected_shield
            )
            hovered = hover_kind == "structure" and hover_value == id(building)
            render_jobs.append((
                *self._render_sort_key(building.pos, 32),
                lambda building=building, selected=selected, hovered=hovered: self._draw_shadowed_structure(
                    surface,
                    camera,
                    viewport,
                    building,
                    lambda: self._draw_building_structure(surface, camera, viewport, fonts["tiny"], building, selected, hovered),
                    world_size=BUILDING_SPRITE_WORLD_SIZE,
                ),
            ))
        for tower in self.towers:
            hovered = hover_kind == "structure" and hover_value == id(tower)
            render_jobs.append((
                *self._render_sort_key(tower.pos, 33),
                lambda tower=tower, hovered=hovered: self._draw_shadowed_structure(
                    surface,
                    camera,
                    viewport,
                    tower,
                    lambda: tower.draw(surface, camera, viewport, tower is self.selected_tower, hovered),
                    world_size=BUILDING_SPRITE_WORLD_SIZE,
                ),
            ))
        for troop in self.troops:
            hovered = hover_kind == "troop" and hover_value == id(troop)
            render_jobs.append((
                *self._render_sort_key(troop.pos, 42),
                lambda troop=troop, hovered=hovered: self._draw_shadowed_unit(
                    surface,
                    camera,
                    viewport,
                    troop,
                    lambda: troop.draw(surface, camera, viewport, troop in self.selected_troops, hovered),
                ),
            ))
        for enemy in self.enemies:
            if self.is_world_explored(enemy.pos, enemy.radius):
                if getattr(enemy, "target_class", "") == "enemy_structure":
                    render_jobs.append((
                        *self._render_sort_key(enemy.pos, 43),
                        lambda enemy=enemy: self._draw_shadowed_structure(
                            surface,
                            camera,
                            viewport,
                            enemy,
                            lambda: enemy.draw(surface, camera, viewport),
                            world_size=self._structure_shadow_world_size(enemy),
                        ),
                    ))
                else:
                    render_jobs.append((
                        *self._render_sort_key(enemy.pos, 43),
                        lambda enemy=enemy: self._draw_shadowed_unit(
                            surface,
                            camera,
                            viewport,
                            enemy,
                            lambda: enemy.draw(surface, camera, viewport),
                        ),
                    ))
        for projectile in self.projectiles:
            if self.is_world_explored(projectile.pos, 8):
                render_jobs.append((*self._render_sort_key(projectile.pos, 60), lambda projectile=projectile: projectile.draw(surface, camera, viewport)))
        for projectile in self.enemy_projectiles:
            if self.is_world_explored(projectile.pos, 8):
                render_jobs.append((*self._render_sort_key(projectile.pos, 60), lambda projectile=projectile: projectile.draw(surface, camera, viewport)))
        for pulse in self.damage_pulses:
            if self.is_world_explored(pulse.pos, pulse.radius):
                render_jobs.append((*self._render_sort_key(pulse.pos, 70), lambda pulse=pulse: pulse.draw(surface, camera, viewport)))
        for beam in self.beams:
            if self.is_world_explored(beam.start, 4) and self.is_world_explored(beam.end, 4):
                render_jobs.append((*self._render_sort_key(beam.end, 71), lambda beam=beam: beam.draw(surface, camera, viewport)))
        for particle in self.particles:
            if self.is_world_explored(particle.pos, particle.radius):
                render_jobs.append((*self._render_sort_key(particle.pos, 72), lambda particle=particle: particle.draw(surface, camera, viewport)))
        for text in self.texts:
            if self.is_world_explored(text.pos, 8):
                render_jobs.append((*self._render_sort_key(text.pos, 80), lambda text=text: text.draw(surface, camera, viewport, fonts["tiny"])))

        for *_key, draw_job in sorted(render_jobs, key=lambda item: item[:-1]):
            draw_job()

        self.fog.draw(surface, camera, viewport)

        surface.set_clip(previous_clip)
        pygame.draw.rect(surface, config.PALETTE.line_bright, original_viewport, 1)

    def _draw_map_boundary(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        top_left = camera.world_to_screen((0, 0), viewport)
        bottom_right = camera.world_to_screen(self.grid.world_size, viewport)
        rect = pygame.Rect(
            math.floor(top_left.x),
            math.floor(top_left.y),
            math.ceil(bottom_right.x - top_left.x),
            math.ceil(bottom_right.y - top_left.y),
        )
        pygame.draw.rect(surface, config.PALETTE.line, rect, 1)

    def _draw_shadowed_unit(self, surface: pygame.Surface, camera, viewport: pygame.Rect, actor, draw_job) -> None:
        self._draw_unit_shadow(surface, camera, viewport, actor)
        draw_job()

    def _draw_shadowed_structure(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        structure,
        draw_job,
        *,
        world_size: float | None = None,
    ) -> None:
        self._draw_structure_shadow(surface, camera, viewport, structure, world_size=world_size)
        draw_job()

    def _draw_unit_shadow(self, surface: pygame.Surface, camera, viewport: pygame.Rect, actor) -> None:
        if not getattr(actor, "alive", True):
            return
        pos = getattr(actor, "pos", None)
        if pos is None:
            return
        radius = max(4.0, float(getattr(actor, "radius", self.grid.tile_size * 0.4)))
        center = camera.world_to_screen(pos, viewport)
        rect = pygame.Rect(
            0,
            0,
            max(7, int(round(radius * 1.75 * camera.zoom))),
            max(4, int(round(radius * 0.58 * camera.zoom))),
        )
        rect.center = (
            int(round(center.x)),
            int(round(center.y + radius * 0.58 * camera.zoom)),
        )
        if rect.colliderect(viewport):
            draw_ellipse_alpha(surface, rect, config.PALETTE.black, config.ENTITY_SHADOW_ALPHA)

    def _draw_structure_shadow(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        structure,
        *,
        world_size: float | None = None,
    ) -> None:
        if not getattr(structure, "alive", True):
            return
        pos = getattr(structure, "pos", None)
        if pos is None:
            return
        base_size = float(world_size) if world_size is not None else max(
            self.grid.tile_size * 0.82,
            float(getattr(structure, "radius", self.grid.tile_size * 0.5)) * 1.85,
        )
        size = max(8, int(round(base_size * 0.84 * camera.zoom)))
        center = camera.world_to_screen(pos, viewport)
        rect = pygame.Rect(0, 0, size, size)
        rect.center = (
            int(round(center.x)),
            int(round(center.y + size * 0.06)),
        )
        if rect.colliderect(viewport):
            draw_rect_alpha(surface, rect, config.PALETTE.black, config.ENTITY_SHADOW_ALPHA)

    def _structure_shadow_world_size(self, structure) -> float:
        kind = getattr(structure, "kind", "")
        if kind in {"core", "enemy_core"}:
            return CORE_SPRITE_WORLD_SIZE
        return max(BUILDING_SPRITE_WORLD_SIZE, float(getattr(structure, "radius", self.grid.tile_size * 0.5)) * 1.85)

    def _draw_terrain(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        x0, y0, x1, y1 = camera.visible_tile_bounds(viewport, self.grid.tile_size, self.grid.width, self.grid.height)
        reference_elevation = self.terrain_shadows.reference_elevation(self.grid.terrain)

        for y in range(y0, y1):
            for x in range(x0, x1):
                cell = (x, y)
                terrain_cell = self.grid.terrain.cell(cell)
                frame_index = terrain_sprite_frame(cell)
                rect = draw_terrain_tile(surface, camera, viewport, cell, terrain_cell.tile_name, self.grid.tile_size, frame_index=frame_index)
                if rect is None:
                    rect = self._cell_screen_rect(cell, camera, viewport)
                    shade = max(18, min(68, 28 + terrain_cell.elevation * 15))
                    pygame.draw.rect(surface, (shade, shade, shade), rect)
                shadow_opacity = self.terrain_shadows.opacity_for(
                    self.grid.terrain,
                    cell,
                    reference_elevation=reference_elevation,
                )
                if shadow_opacity > 0 and draw_terrain_shadow_overlay(
                    surface,
                    camera,
                    viewport,
                    cell,
                    terrain_cell.tile_name,
                    self.grid.tile_size,
                    shadow_opacity,
                    frame_index=frame_index,
                ) is None:
                    draw_rect_alpha(surface, rect, config.PALETTE.black, int(round(shadow_opacity * 255)))

        for y in range(y0, y1):
            for x in range(x0, x1):
                terrain_cell = self.grid.terrain.cell((x, y))
                if terrain_cell.cliff_tile_name is not None:
                    draw_terrain_tile(surface, camera, viewport, (x, y), terrain_cell.cliff_tile_name, self.grid.tile_size)

        for y in range(y0, y1):
            for x in range(x0, x1):
                terrain_cell = self.grid.terrain.cell((x, y))
                if terrain_cell.feature_tile_name is not None:
                    draw_terrain_tile(surface, camera, viewport, (x, y), terrain_cell.feature_tile_name, self.grid.tile_size, phase_owner=(x, y))

    def _render_sort_key(self, pos: pygame.Vector2 | tuple[float, float], layer: int) -> tuple[int, float, float]:
        point = pygame.Vector2(pos)
        cell = self.grid.cell_from_world(point)
        elevation = self.grid.terrain.elevation_at(cell) if self.grid.in_bounds(cell) else 0
        return layer, point.y - elevation * (self.grid.tile_size * 0.18), point.x

    def _draw_building_structure(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        font: pygame.font.Font,
        building,
        selected: bool,
        hovered: bool,
    ) -> None:
        center = camera.world_to_screen(building.pos, viewport)
        scale = hover_feedback.hover_scale(hovered)

        if isinstance(building, Torch):
            draw_circle_alpha(surface, center, building.aggro_radius * camera.zoom, config.PALETTE.white, 34 if selected else 18, 1)
            rect = draw_tower_sprite(
                surface,
                camera,
                viewport,
                building,
                "torch",
                world_size=BUILDING_SPRITE_WORLD_SIZE,
                scale=scale,
                bounce=True,
                pulse=True,
            )
        elif isinstance(building, ShieldGenerator):
            rect = draw_tower_sprite(
                surface,
                camera,
                viewport,
                building,
                "shield_generator",
                world_size=BUILDING_SPRITE_WORLD_SIZE,
                scale=scale,
                bounce=True,
                pulse=building.shield_active or building.recharging,
            )
            if building.shield_active or building.recharging:
                phase = pygame.time.get_ticks() * 0.005 + building.pulse
                alpha = 34 if building.shield_active else 18
                draw_circle_alpha(surface, center, BUILDING_SPRITE_WORLD_SIZE * camera.zoom * (0.52 + 0.04 * math.sin(phase * 2.0)), config.PALETTE.white, alpha, 1)
        else:
            if isinstance(building, TrainingGrounds):
                draw_circle_alpha(surface, center, building.training_radius * camera.zoom, config.PALETTE.white, 32 if selected else 14, 1)
            variant = None
            if isinstance(building, MineralExtractor):
                variant = "gold" if getattr(building.deposit, "kind", "") == "gold" else "mineral"
            rect = draw_building_sprite(
                surface,
                camera,
                viewport,
                building,
                building.kind,
                variant=variant,
                world_size=BUILDING_SPRITE_WORLD_SIZE,
                scale=scale,
            )

        if rect is None:
            building.draw(surface, camera, viewport, font, selected, hovered)
            return

        if isinstance(building, MineralExtractor) and not building.deposit.active:
            pygame.draw.line(surface, config.PALETTE.line_bright, rect.topleft, rect.bottomright, max(1, int(camera.zoom)))
            pygame.draw.line(surface, config.PALETTE.line_bright, rect.topright, rect.bottomleft, max(1, int(camera.zoom)))

        if selected:
            draw_circle_alpha(surface, center, max(rect.width, rect.height) * 0.58, config.PALETTE.white, 58, 1)

        self._draw_building_progress(surface, camera, rect, building)
        self._draw_structure_health_bar(surface, rect, getattr(building, "health", 0.0), getattr(building, "max_health", 0.0), camera)

    def _draw_building_progress(self, surface: pygame.Surface, camera, rect: pygame.Rect, building) -> None:
        progress: float | None = None
        if isinstance(building, Barracks) and building.train_queue:
            order = building.train_queue[0]
            progress = 1.0 - max(0.0, order.remaining / max(0.01, order.total))
        elif isinstance(building, ResearchBuilding) and building.active_order is not None:
            progress = 1.0 - max(0.0, building.active_order.remaining / max(0.01, building.active_order.total))
        elif isinstance(building, Library) and building.active_order is not None:
            progress = 1.0
            if building.active_order.ready_item_id is None:
                progress = 1.0 - max(0.0, building.active_order.remaining / max(0.01, building.active_order.total))

        if progress is not None:
            self._draw_progress_bar(surface, rect, progress, camera, below=True)

        if isinstance(building, ShieldGenerator) and building.shield_max > 0:
            self._draw_progress_bar(surface, rect, max(0.0, min(1.0, building.shield / building.shield_max)), camera, below=True, y_offset=8)
            if building.recharging:
                bar = pygame.Rect(rect.left, rect.bottom + 8, rect.width, max(2, int(3 * camera.zoom)))
                draw_rect_alpha(surface, bar.inflate(2, 2), config.PALETTE.white, 34)

        if isinstance(building, MineralExtractor) and building.deposit.active and building.deposit.amount < building.deposit.max_amount:
            self._draw_progress_bar(surface, rect, max(0.0, building.deposit.amount / max(1, building.deposit.max_amount)), camera, below=True)

    def _draw_progress_bar(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        progress: float,
        camera,
        *,
        below: bool,
        y_offset: int = 4,
    ) -> None:
        height = max(2, int(3 * camera.zoom))
        bar = pygame.Rect(rect.left, rect.bottom + y_offset if below else rect.top - y_offset - height, rect.width, height)
        pygame.draw.rect(surface, config.PALETTE.black, bar)
        fill = bar.copy()
        fill.width = int(bar.width * max(0.0, min(1.0, progress)))
        pygame.draw.rect(surface, config.PALETTE.white, fill)

    def _draw_structure_health_bar(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        health: float,
        max_health: float,
        camera,
    ) -> None:
        if max_health <= 0 or health >= max_health:
            return
        self._draw_progress_bar(surface, rect, max(0.0, health / max_health), camera, below=False, y_offset=6)

    def _draw_walls(self, surface: pygame.Surface, camera, viewport: pygame.Rect, hover_wall: tuple[int, int] | None = None) -> None:
        edge_width = max(1, int(2 * camera.zoom))
        sprite_walls = False
        for cell in self.grid.walls:
            rect = self._cell_screen_rect(cell, camera, viewport)
            if not rect.colliderect(viewport):
                continue
            hovered = cell == hover_wall
            draw_rect = hover_feedback.scaled_rect(rect, hovered)
            fill = config.PALETTE.white if hovered else config.PALETTE.dark
            mark = config.PALETTE.black if hovered else config.PALETTE.white
            sprite_rect = draw_building_sprite_at(
                surface,
                camera,
                viewport,
                self.grid.world_center(cell),
                cell,
                "wall",
                world_size=WALL_SPRITE_WORLD_SIZE,
                scale=hover_feedback.hover_scale(hovered),
            )
            if sprite_rect is not None:
                sprite_walls = True
                if hovered:
                    draw_rect_alpha(surface, sprite_rect, config.PALETTE.white, 36, max(1, int(camera.zoom)))
                draw_rect = sprite_rect
            else:
                pygame.draw.rect(surface, fill, draw_rect)
            health = self.grid.wall_health.get(cell, self.grid.wall_max_health)
            if health < self.grid.wall_max_health:
                damage = 1.0 - max(0.0, health / self.grid.wall_max_health)
                inset = draw_rect.inflate(-max(3, int(draw_rect.width * 0.28)), -max(3, int(draw_rect.height * 0.28)))
                draw_rect_alpha(surface, inset, mark, int(24 + damage * 76))

        if not sprite_walls:
            for cell in self.grid.walls:
                rect = self._cell_screen_rect(cell, camera, viewport)
                if not rect.colliderect(viewport):
                    continue
                hovered = cell == hover_wall
                draw_rect = hover_feedback.scaled_rect(rect, hovered)
                mark = config.PALETTE.black if hovered else config.PALETTE.white
                x, y = cell
                if (x, y - 1) not in self.grid.walls:
                    pygame.draw.line(surface, mark, draw_rect.topleft, draw_rect.topright, edge_width)
                if (x + 1, y) not in self.grid.walls:
                    pygame.draw.line(surface, mark, draw_rect.topright, draw_rect.bottomright, edge_width)
                if (x, y + 1) not in self.grid.walls:
                    pygame.draw.line(surface, mark, draw_rect.bottomleft, draw_rect.bottomright, edge_width)
                if (x - 1, y) not in self.grid.walls:
                    pygame.draw.line(surface, mark, draw_rect.topleft, draw_rect.bottomleft, edge_width)

        if self.selected_wall is not None:
            self._draw_wall_component_highlight(surface, camera, viewport, self.selected_wall)

    def _draw_wall_component_highlight(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        start: tuple[int, int],
    ) -> None:
        if start not in self.grid.walls:
            return
        component = self._wall_component(start)
        for cell in component:
            rect = self._cell_screen_rect(cell, camera, viewport)
            if rect.colliderect(viewport):
                draw_rect_alpha(surface, rect, config.PALETTE.white, 34)

    def _draw_shield_networks(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        for generator in self.shield_generators():
            generator.set_network(self.connected_structure_cells(generator.cell))
            if generator.shield <= 0 and not generator.recharging:
                continue
            alpha = 32 if generator.shield_active else 18
            for cell in generator.network_cells:
                rect = self._cell_screen_rect(cell, camera, viewport).inflate(max(3, int(5 * camera.zoom)), max(3, int(5 * camera.zoom)))
                if not rect.colliderect(viewport):
                    continue
                draw_rect_alpha(surface, rect, config.PALETTE.white, alpha)
                pygame.draw.rect(surface, config.PALETTE.white, rect, max(1, int(camera.zoom)))

    def _draw_arcane_network(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        self._prune_arcane_links()
        for link in self.arcane_links:
            if not getattr(link.structure, "alive", False) or not link.core.alive:
                continue
            self._draw_arcane_path_trace(surface, camera, viewport, link.path, link.phase)

    def _draw_arcane_path_trace(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        path: list[tuple[int, int]],
        phase: float,
        preview: bool = False,
    ) -> None:
        if len(path) < 2:
            return

        points = [camera.world_to_screen(self.grid.world_center(cell), viewport) for cell in path]
        width = max(1, int((3 if preview else 2) * camera.zoom))
        alpha = 68 if preview else 48
        trace_color = (138, 138, 138)
        for start, end in zip(points, points[1:]):
            segment_rect = pygame.Rect(
                min(start.x, end.x),
                min(start.y, end.y),
                max(1, abs(end.x - start.x)),
                max(1, abs(end.y - start.y)),
            ).inflate(10, 10)
            if not segment_rect.colliderect(viewport):
                continue
            draw_line_alpha(surface, start, end, trace_color, alpha, width)

        node_size = max(2, int(4 * camera.zoom))
        for index, point in enumerate(points):
            if index not in (0, len(points) - 1) and index % 5 != 0:
                previous = path[index - 1]
                current = path[index]
                next_cell = path[index + 1]
                if (previous[0] == current[0] == next_cell[0]) or (previous[1] == current[1] == next_cell[1]):
                    continue
            node = pygame.Rect(0, 0, node_size, node_size)
            node.center = (round(point.x), round(point.y))
            if node.colliderect(viewport):
                draw_rect_alpha(surface, node, trace_color, 72 if preview else 42)

        pulse = self._point_on_polyline(points, (pygame.time.get_ticks() * 0.001 * 0.72 + phase) % 1.0)
        if pulse is None:
            return
        draw_circle_alpha(surface, pulse, max(5, 7 * camera.zoom), config.PALETTE.white, 34 if not preview else 54, 1)
        draw_circle_alpha(surface, pulse, max(2, 3.4 * camera.zoom), config.PALETTE.white, 138 if not preview else 176)

    def _point_on_polyline(self, points: list[pygame.Vector2], progress: float) -> pygame.Vector2 | None:
        if len(points) < 2:
            return None
        lengths = [start.distance_to(end) for start, end in zip(points, points[1:])]
        total = sum(lengths)
        if total <= 0:
            return pygame.Vector2(points[0])
        target = (progress % 1.0) * total
        traversed = 0.0
        for start, end, length in zip(points, points[1:], lengths):
            if traversed + length >= target:
                ratio = 0.0 if length <= 0 else (target - traversed) / length
                return pygame.Vector2(start).lerp(end, ratio)
            traversed += length
        return pygame.Vector2(points[-1])

    def _wall_component(self, start: tuple[int, int]) -> set[tuple[int, int]]:
        pending = [start]
        visited = {start}
        while pending:
            x, y = pending.pop()
            for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbor in visited or neighbor not in self.grid.walls:
                    continue
                visited.add(neighbor)
                pending.append(neighbor)
        return visited

    def _draw_townhalls(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font) -> None:
        for core in self.core_targets:
            self._draw_core(surface, camera, viewport, font, core)

    def _draw_core(self, surface: pygame.Surface, camera, viewport: pygame.Rect, font: pygame.font.Font, core: CoreTarget) -> None:
        center = camera.world_to_screen(core.pos, viewport)
        size = int(self.grid.tile_size * 3.15 * camera.zoom)
        load = self.arcane_core_load(core)
        load_ratio = min(1.0, load / max(1, core.arcane_capacity))
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.004 + core.index)
        draw_circle_alpha(surface, center, size * (0.57 + pulse * 0.06), config.PALETTE.white, int(22 + load_ratio * 34), 1)
        sprite_rect = draw_building_sprite(
            surface,
            camera,
            viewport,
            core,
            "core",
            world_size=CORE_SPRITE_WORLD_SIZE,
        )
        if sprite_rect is not None:
            load_label = font.render(f"{load}/{core.arcane_capacity}", True, config.PALETTE.text_dim)
            surface.blit(load_label, load_label.get_rect(center=(sprite_rect.centerx, sprite_rect.bottom + max(8, int(8 * camera.zoom)))))
            if core.health < core.max_health:
                bar = pygame.Rect(sprite_rect.left, sprite_rect.top - 7, sprite_rect.width, max(2, int(4 * camera.zoom)))
                pygame.draw.rect(surface, config.PALETTE.black, bar)
                fill = bar.copy()
                fill.width = int(bar.width * max(0.0, core.health / core.max_health))
                pygame.draw.rect(surface, config.PALETTE.white, fill)
            return
        rect = pygame.Rect(0, 0, size, size)
        rect.center = (center.x, center.y)
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, max(2, int(2 * camera.zoom)))
        inner = rect.inflate(-int(size * 0.28), -int(size * 0.28))
        pygame.draw.rect(surface, config.PALETTE.white, inner, max(1, int(2 * camera.zoom)))
        label_text = "CORE" if core.index == 1 else f"C{core.index}"
        label = font.render(label_text, True, config.PALETTE.white)
        surface.blit(label, label.get_rect(center=rect.center))
        load_label = font.render(f"{load}/{core.arcane_capacity}", True, config.PALETTE.text_dim)
        surface.blit(load_label, load_label.get_rect(center=(rect.centerx, rect.bottom + max(8, int(8 * camera.zoom)))))
        if core.health < core.max_health:
            bar = pygame.Rect(rect.left, rect.top - 7, rect.width, max(2, int(4 * camera.zoom)))
            pygame.draw.rect(surface, config.PALETTE.black, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, core.health / core.max_health))
            pygame.draw.rect(surface, config.PALETTE.white, fill)

    def _draw_core_reserve_hover(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        mouse_pos: tuple[int, int],
    ) -> None:
        world = camera.screen_to_world(mouse_pos, viewport)
        hovered = next((core for core in self.core_targets if world.distance_to(core.pos) <= self.grid.tile_size * 1.75), None)
        if hovered is None:
            return

        tx, ty = hovered.cell
        for x in range(tx - 3, tx + 4):
            for y in range(ty - 3, ty + 4):
                cell = (x, y)
                if not self.grid.in_bounds(cell):
                    continue
                rect = self._cell_screen_rect(cell, camera, viewport)
                draw_rect_alpha(surface, rect, config.PALETTE.white, 24)
                edge = max(1, int(camera.zoom))
                if x == tx - 3:
                    pygame.draw.line(surface, config.PALETTE.line_bright, rect.topleft, rect.bottomleft, edge)
                if x == tx + 3:
                    pygame.draw.line(surface, config.PALETTE.line_bright, rect.topright, rect.bottomright, edge)
                if y == ty - 3:
                    pygame.draw.line(surface, config.PALETTE.line_bright, rect.topleft, rect.topright, edge)
                if y == ty + 3:
                    pygame.draw.line(surface, config.PALETTE.line_bright, rect.bottomleft, rect.bottomright, edge)

    def _draw_build_preview(self, surface: pygame.Surface, camera, viewport: pygame.Rect, mouse_pos: tuple[int, int]) -> None:
        if not self.build_mode:
            return
        mode = self.build_mode
        world = camera.screen_to_world(mouse_pos, viewport)
        cell = self.grid.cell_from_world(world)
        preview_cell = cell
        if mode == "extractor":
            deposit = self.resource_for_extractor_cell(cell)
            if deposit is not None:
                preview_cell = deposit.cell
        rect = self._cell_screen_rect(preview_cell, camera, viewport)
        affordable = self.gold >= BUILD_COSTS[mode] and self.minerals >= MINERAL_BUILD_COSTS.get(mode, 0)
        valid = (self.can_build_extractor_on(preview_cell) if mode == "extractor" else self.can_build_on(preview_cell)) and affordable
        preview_path: list[tuple[int, int]] = []
        structure_modes = ("barracks", "house", "extractor", "torch", "training_grounds", "expedition_campsite", "hero_hall", "research", "library", "shield_generator")
        if valid and (mode == "wall" or mode == "core" or mode in TOWER_BLUEPRINTS or mode in structure_modes):
            blocker = "wall" if mode == "wall" else "tower"
            valid = self.grid.would_keep_paths_open(preview_cell, blocker)
        if valid and mode != "wall" and mode != "core":
            _core, preview_path, _reason = self.arcane_source_for_cell(preview_cell)
            valid = bool(preview_path)
        color = config.PALETTE.white if valid else config.PALETTE.mid
        alpha = 72 if valid else 36
        if valid and preview_path:
            self._draw_arcane_path_trace(surface, camera, viewport, preview_path, 0.0, preview=True)
        draw_rect_alpha(surface, rect, color, alpha)
        pygame.draw.rect(surface, color, rect, max(1, int(2 * camera.zoom)))
        if mode == "core":
            self._draw_core_reserve_preview(surface, camera, viewport, preview_cell, valid)
        if mode in TOWER_BLUEPRINTS:
            stats = stats_for(mode, 1)
            center = camera.world_to_screen(self.grid.world_center(preview_cell), viewport)
            draw_circle_alpha(surface, center, float(stats["range"]) * camera.zoom, config.PALETTE.white, 22, 1)
        if mode == "torch":
            center = camera.world_to_screen(self.grid.world_center(preview_cell), viewport)
            draw_circle_alpha(surface, center, Torch.aggro_radius * camera.zoom, config.PALETTE.white, 22, 1)
        if mode == "training_grounds":
            center = camera.world_to_screen(self.grid.world_center(preview_cell), viewport)
            draw_circle_alpha(surface, center, TrainingGrounds.training_radius * camera.zoom, config.PALETTE.white, 22, 1)
        if mode == "expedition_campsite":
            center = camera.world_to_screen(self.grid.world_center(preview_cell), viewport)
            for index in range(5):
                angle = -math.pi / 2 + index * math.tau / 5
                orb = center + pygame.Vector2(math.cos(angle), math.sin(angle)) * 32 * camera.zoom
                draw_circle_alpha(surface, orb, 5 * camera.zoom, config.PALETTE.white, 42, 1)
        if not valid:
            pygame.draw.line(surface, color, rect.topleft, rect.bottomright, max(1, int(camera.zoom)))
            pygame.draw.line(surface, color, rect.topright, rect.bottomleft, max(1, int(camera.zoom)))

    def _draw_core_reserve_preview(
        self,
        surface: pygame.Surface,
        camera,
        viewport: pygame.Rect,
        center_cell: tuple[int, int],
        valid: bool,
    ) -> None:
        color = config.PALETTE.white if valid else config.PALETTE.mid
        alpha = 20 if valid else 12
        cx, cy = center_cell
        for x in range(cx - 3, cx + 4):
            for y in range(cy - 3, cy + 4):
                cell = (x, y)
                if not self.grid.in_bounds(cell):
                    continue
                rect = self._cell_screen_rect(cell, camera, viewport)
                draw_rect_alpha(surface, rect, color, alpha)

    def _cell_screen_rect(self, cell: tuple[int, int], camera, viewport: pygame.Rect) -> pygame.Rect:
        top_left = camera.world_to_screen((cell[0] * self.grid.tile_size, cell[1] * self.grid.tile_size), viewport)
        bottom_right = camera.world_to_screen(((cell[0] + 1) * self.grid.tile_size, (cell[1] + 1) * self.grid.tile_size), viewport)
        left = math.floor(top_left.x)
        top = math.floor(top_left.y)
        right = math.ceil(bottom_right.x)
        bottom = math.ceil(bottom_right.y)
        return pygame.Rect(left, top, max(1, right - left), max(1, bottom - top))
