from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pygame

from bastion import config
from bastion.engine.drawing import draw_circle_alpha

ITEM_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "items.json"


@dataclass(frozen=True)
class ItemDefinition:
    id: str
    name: str
    type: str
    description: str
    duration: float
    effects: dict[str, Any]
    glyph: str = "?"
    rarity: str = "common"
    tags: tuple[str, ...] = ()
    weight: float = 1.0
    drop_weight: float = 0.0
    stack_limit: int = 1


@dataclass
class InventorySlot:
    item_id: str
    quantity: int = 1


@dataclass
class ActiveItemBuff:
    item_id: str
    name: str
    remaining: float
    total: float
    effects: dict[str, Any]
    glyph: str = "?"
    tags: tuple[str, ...] = ()

    def update(self, dt: float) -> None:
        self.remaining = max(0.0, self.remaining - dt)

    @property
    def alive(self) -> bool:
        return self.remaining > 0.0


class Inventory:
    def __init__(self, capacity: int = 16) -> None:
        self.capacity = capacity
        self.slots: list[InventorySlot | None] = [None for _ in range(capacity)]

    def slot(self, index: int) -> InventorySlot | None:
        if 0 <= index < self.capacity:
            return self.slots[index]
        return None

    def add_item(self, item_id: str, quantity: int = 1) -> bool:
        definition = ITEM_DEFINITIONS.get(item_id)
        if definition is None or quantity <= 0:
            return False

        remaining = quantity
        stack_limit = max(1, definition.stack_limit)
        if stack_limit > 1:
            for slot in self.slots:
                if slot is None or slot.item_id != item_id or slot.quantity >= stack_limit:
                    continue
                space = stack_limit - slot.quantity
                added = min(space, remaining)
                slot.quantity += added
                remaining -= added
                if remaining <= 0:
                    return True

        for index, slot in enumerate(self.slots):
            if slot is not None:
                continue
            added = min(stack_limit, remaining)
            self.slots[index] = InventorySlot(item_id, added)
            remaining -= added
            if remaining <= 0:
                return True

        return False

    def add_item_to_slot(self, index: int, item_id: str, quantity: int = 1) -> bool:
        definition = ITEM_DEFINITIONS.get(item_id)
        if definition is None or quantity <= 0 or not 0 <= index < self.capacity:
            return False
        stack_limit = max(1, definition.stack_limit)
        slot = self.slots[index]
        if slot is None:
            self.slots[index] = InventorySlot(item_id, min(quantity, stack_limit))
            return quantity <= stack_limit
        if slot.item_id != item_id or slot.quantity + quantity > stack_limit:
            return False
        slot.quantity += quantity
        return True

    def consume_slot(self, index: int, quantity: int = 1) -> str | None:
        slot = self.slot(index)
        if slot is None or quantity <= 0:
            return None
        item_id = slot.item_id
        slot.quantity -= quantity
        if slot.quantity <= 0:
            self.slots[index] = None
        return item_id

    def has_space_for(self, item_id: str) -> bool:
        definition = ITEM_DEFINITIONS.get(item_id)
        if definition is None:
            return False
        stack_limit = max(1, definition.stack_limit)
        if stack_limit > 1:
            for slot in self.slots:
                if slot is not None and slot.item_id == item_id and slot.quantity < stack_limit:
                    return True
        return any(slot is None for slot in self.slots)

    def has_space_for_at(self, index: int, item_id: str) -> bool:
        definition = ITEM_DEFINITIONS.get(item_id)
        if definition is None or not 0 <= index < self.capacity:
            return False
        slot = self.slots[index]
        return slot is None or (slot.item_id == item_id and slot.quantity < max(1, definition.stack_limit))


class DroppedItem:
    def __init__(self, item_id: str, pos: pygame.Vector2, *, magnet_radius: float = 46.0) -> None:
        self.item_id = item_id
        self.pos = pygame.Vector2(pos)
        self.magnet_radius = magnet_radius
        self.pickup_radius = 10.0
        self.alive = True
        self.age = 0.0
        self.phase = random.random() * 6.28318
        angle = random.random() * 6.28318
        self.vel = pygame.Vector2(math.cos(angle), math.sin(angle)) * random.uniform(18.0, 48.0)

    def update(self, dt: float, game) -> None:
        if not self.alive:
            return
        self.age += dt
        self.vel *= max(0.0, 1.0 - dt * 2.6)
        troop = self._nearest_troop(game)
        if troop is not None:
            offset = troop.pos - self.pos
            distance = offset.length()
            if distance <= self.pickup_radius + troop.radius:
                if game.add_item(self.item_id):
                    self.alive = False
                    game.spawn_hit(troop.pos, 2)
                return
            if distance > 0:
                pull = 210.0 * (1.0 - min(1.0, distance / max(1.0, self.magnet_radius)))
                self.vel += offset.normalize() * pull * dt
        self.pos += self.vel * dt

    def _nearest_troop(self, game):
        troops = game.nearby_troops(self.pos, self.magnet_radius + 24) if hasattr(game, "nearby_troops") else getattr(game, "troops", [])
        candidates = [
            troop
            for troop in troops
            if getattr(troop, "alive", False) and troop.pos.distance_to(self.pos) <= self.magnet_radius + troop.radius
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda troop: troop.pos.distance_to(self.pos))

    def draw(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        definition = ITEM_DEFINITIONS.get(self.item_id)
        if definition is None:
            return
        screen = camera.world_to_screen(self.pos, viewport)
        if not viewport.inflate(30, 30).collidepoint(screen):
            return
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.006 + self.phase)
        size = max(11, int((12.0 + pulse * 2.0) * camera.zoom))
        rect = pygame.Rect(0, 0, size, size)
        rect.center = (round(screen.x), round(screen.y))
        draw_circle_alpha(surface, screen, (self.magnet_radius * 0.42 + pulse * 4.0) * camera.zoom, config.PALETTE.white, 18, 1)
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, max(1, int(camera.zoom)))
        inner = rect.inflate(-max(4, int(size * 0.35)), -max(4, int(size * 0.35)))
        if definition.type == "equipment":
            pygame.draw.line(surface, config.PALETTE.white, inner.bottomleft, inner.topright, max(1, int(camera.zoom)))
        elif definition.type == "consumable":
            pygame.draw.circle(surface, config.PALETTE.white, rect.center, max(2, size // 5), max(1, int(camera.zoom)))
        else:
            pygame.draw.rect(surface, config.PALETTE.white, inner, max(1, int(camera.zoom)))


def load_items(path: Path = ITEM_DATA_PATH) -> dict[str, ItemDefinition]:
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    entries = raw.get("items", raw) if isinstance(raw, dict) else raw
    items: dict[str, ItemDefinition] = {}
    for entry in entries:
        item = ItemDefinition(
            id=str(entry["id"]),
            name=str(entry["name"]),
            type=str(entry.get("type", "item")),
            description=str(entry.get("description", "")),
            duration=float(entry.get("duration", 0.0)),
            effects=dict(entry.get("effects", {})),
            glyph=str(entry.get("glyph", "?")),
            rarity=str(entry.get("rarity", "common")),
            tags=tuple(str(tag) for tag in entry.get("tags", ()) if str(tag)),
            weight=float(entry.get("weight", 1.0)),
            drop_weight=float(entry.get("drop_weight", 0.0)),
            stack_limit=int(entry.get("stack_limit", 1)),
        )
        items[item.id] = item
    return items


ITEM_DEFINITIONS = load_items()

INSTANT_EFFECT_KEYS = {
    "heal_towers_buildings",
    "heal_troops",
    "tower_xp_min",
    "tower_xp_max",
    "troop_xp_min",
    "troop_xp_max",
}


def random_scroll_id(rng=random) -> str:
    scrolls = [item for item in ITEM_DEFINITIONS.values() if item.type == "scroll" and item.weight > 0]
    if not scrolls:
        raise RuntimeError("No scroll definitions are available.")
    total = sum(item.weight for item in scrolls)
    roll = rng.uniform(0.0, total)
    cursor = 0.0
    for item in scrolls:
        cursor += item.weight
        if roll <= cursor:
            return item.id
    return scrolls[-1].id


def random_drop_item_id(rng=random) -> str | None:
    drops = [item for item in ITEM_DEFINITIONS.values() if item.drop_weight > 0]
    if not drops:
        return None
    total = sum(item.drop_weight for item in drops)
    roll = rng.uniform(0.0, total)
    cursor = 0.0
    for item in drops:
        cursor += item.drop_weight
        if roll <= cursor:
            return item.id
    return drops[-1].id


def apply_item(game, item_id: str) -> bool:
    definition = ITEM_DEFINITIONS.get(item_id)
    if definition is None:
        return False

    applied = False
    timed_effects = {key: value for key, value in definition.effects.items() if key not in INSTANT_EFFECT_KEYS}
    if definition.duration > 0 and timed_effects:
        game.active_item_buffs.append(
            ActiveItemBuff(
                item_id=definition.id,
                name=definition.name,
                remaining=definition.duration,
                total=definition.duration,
                effects=timed_effects,
                glyph=definition.glyph,
                tags=definition.tags,
            )
        )
        applied = True

    if definition.effects.get("heal_towers_buildings"):
        game.heal_towers_and_buildings_full()
        applied = True
    if definition.effects.get("heal_troops"):
        game.heal_troops_full()
        applied = True

    tower_xp_min = definition.effects.get("tower_xp_min")
    tower_xp_max = definition.effects.get("tower_xp_max")
    if tower_xp_min is not None and tower_xp_max is not None:
        game.grant_tower_xp_all(int(tower_xp_min), int(tower_xp_max))
        applied = True

    troop_xp_min = definition.effects.get("troop_xp_min")
    troop_xp_max = definition.effects.get("troop_xp_max")
    if troop_xp_min is not None and troop_xp_max is not None:
        game.grant_troop_xp_all(int(troop_xp_min), int(troop_xp_max))
        applied = True

    return applied


def item_rarity_label(item_id: str) -> str:
    definition = ITEM_DEFINITIONS.get(item_id)
    return "COMMON" if definition is None else definition.rarity.upper()

