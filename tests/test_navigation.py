import unittest

import pygame

from bastion.game.entities import Enemy
from bastion.game.footprints import TWO_BY_TWO
from bastion.game.grid import GameGrid
from bastion.game.units import House, ShieldGenerator


class DummyCore:
    target_class = "core"
    radius = 48.0
    alive = True

    def __init__(self, pos: pygame.Vector2) -> None:
        self.pos = pygame.Vector2(pos)


class DummyGame:
    def __init__(self) -> None:
        self.grid = GameGrid(7, 7, procedural_terrain=False)
        self.enemies = []

    def nearby_enemies(self, _pos: pygame.Vector2, _radius: float):
        return self.enemies


class EnemyNavigationTests(unittest.TestCase):
    def test_core_assault_uses_shared_path_follower_before_hitting_wall(self):
        game = DummyGame()
        game.grid.walls.add((3, 3))
        game.grid.recompute_flow()
        enemy = Enemy("small", game.grid.world_center((1, 3)), wave=1)
        game.enemies.append(enemy)
        target = DummyCore(game.grid.world_center((5, 3)))

        enemy._move_to_core(target, 0.1, game)

        self.assertTrue(enemy.navigator.path)
        self.assertFalse(game.grid.line_clear(enemy.pos, target.pos, enemy.collision_radius))
        self.assertTrue(
            all(
                game.grid.line_clear(start, end, enemy.collision_radius)
                for start, end in zip([enemy.pos] + enemy.navigator.path, enemy.navigator.path)
            )
        )


class StructureFootprintTests(unittest.TestCase):
    def test_two_by_two_structure_occupies_all_grid_cells_and_removes_cleanly(self):
        grid = GameGrid(20, 20, procedural_terrain=False)
        house = House((3, 3), grid)

        ok, reason = grid.try_add_tower(house.cell, house)

        self.assertTrue(ok, reason)
        occupied = set(TWO_BY_TWO.cells((3, 3)))
        self.assertEqual(occupied, {cell for cell, structure in grid.towers.items() if structure is house})
        for cell in occupied:
            self.assertIs(grid.towers.get(cell), house)
            self.assertTrue(grid.blocked(cell))
            self.assertFalse(grid.buildable(cell))

        overlapping = House((4, 4), grid)
        ok, _reason = grid.try_add_tower(overlapping.cell, overlapping)
        self.assertFalse(ok)

        grid.remove_tower((4, 4))
        self.assertFalse(any(structure is house for structure in grid.towers.values()))

    def test_two_by_two_structure_uses_footprint_center_and_radius(self):
        grid = GameGrid(20, 20, procedural_terrain=False)
        house = House((3, 3), grid)

        self.assertEqual(house.pos, grid.footprint_center((3, 3), TWO_BY_TWO))
        self.assertEqual(house.radius, grid.tile_size)

    def test_shield_capacity_counts_large_buildings_as_one_structure(self):
        grid = GameGrid(20, 20, procedural_terrain=False)
        shield = ShieldGenerator((6, 3), grid)
        network_cells = {(6, 3), *TWO_BY_TWO.cells((3, 3))}

        shield.set_network(network_cells, structure_count=2)

        self.assertEqual(shield.network_structure_count, 2)
        self.assertEqual(shield.shield_max, shield.base_shield + shield.shield_per_structure * 2)


if __name__ == "__main__":
    unittest.main()
