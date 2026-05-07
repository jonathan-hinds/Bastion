import unittest

import pygame

from bastion.game.entities import Enemy
from bastion.game.grid import GameGrid


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


if __name__ == "__main__":
    unittest.main()
