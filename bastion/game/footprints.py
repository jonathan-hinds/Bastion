from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


GridCell = tuple[int, int]


@dataclass(frozen=True, slots=True)
class StructureFootprint:
    width: int = 1
    height: int = 1

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("Structure footprints must be at least 1x1")

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def cells(self, anchor: GridCell) -> tuple[GridCell, ...]:
        x, y = anchor
        return tuple((x + dx, y + dy) for dy in range(self.height) for dx in range(self.width))

    def adjacent_cells(self, anchor: GridCell) -> Iterable[GridCell]:
        x, y = anchor
        for dx in range(self.width):
            yield (x + dx, y - 1)
            yield (x + dx, y + self.height)
        for dy in range(self.height):
            yield (x - 1, y + dy)
            yield (x + self.width, y + dy)


ONE_BY_ONE = StructureFootprint(1, 1)
TWO_BY_TWO = StructureFootprint(2, 2)


BUILD_MODE_FOOTPRINTS: dict[str, StructureFootprint] = {
    "wall": ONE_BY_ONE,
    "core": ONE_BY_ONE,
    "archer": ONE_BY_ONE,
    "cannon": ONE_BY_ONE,
    "wizard": ONE_BY_ONE,
    "torch": ONE_BY_ONE,
    "shield_generator": ONE_BY_ONE,
    "barracks": TWO_BY_TWO,
    "house": TWO_BY_TWO,
    "extractor": TWO_BY_TWO,
    "training_grounds": TWO_BY_TWO,
    "expedition_campsite": TWO_BY_TWO,
    "hero_hall": TWO_BY_TWO,
    "research": TWO_BY_TWO,
    "library": TWO_BY_TWO,
}


def footprint_for_mode(mode: str) -> StructureFootprint:
    return BUILD_MODE_FOOTPRINTS.get(mode, ONE_BY_ONE)


def footprint_for_structure(structure: object) -> StructureFootprint:
    raw = getattr(structure, "footprint", None)
    if isinstance(raw, StructureFootprint):
        return raw
    if isinstance(raw, tuple) and len(raw) == 2:
        return StructureFootprint(int(raw[0]), int(raw[1]))
    return footprint_for_mode(str(getattr(structure, "kind", "")))
