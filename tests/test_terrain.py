import random
import unittest

from bastion import config
from bastion.game.grid import GameGrid
from bastion.game.state import GameState
from bastion.game.terrain import STAIR_SOUTH, TerrainMap
from bastion.game.terrain_shadows import TerrainShadowCalculator, TerrainShadowSettings


def _elevations(width: int, height: int, high_cells: set[tuple[int, int]]) -> list[list[int]]:
    return [[1 if (x, y) in high_cells else 0 for y in range(height)] for x in range(width)]


class TerrainGenerationTests(unittest.TestCase):
    def assert_tile_names(self, terrain: TerrainMap, expected: dict[tuple[int, int], str]) -> None:
        for cell, tile_name in expected.items():
            with self.subTest(cell=cell):
                self.assertEqual(terrain.cell(cell).tile_name, tile_name)

    def test_outer_platform_tiles_follow_main_9_rules(self):
        high_cells = {(x, y) for x in range(1, 4) for y in range(1, 4)}
        terrain = TerrainMap.from_elevations(_elevations(5, 5, high_cells))

        self.assert_tile_names(
            terrain,
            {
                (1, 1): "main_outer_top_left_corner",
                (2, 1): "main_top_edge",
                (3, 1): "main_outer_top_right_corner",
                (1, 2): "main_left_edge",
                (2, 2): "main_center",
                (3, 2): "main_right_edge",
                (1, 3): "main_outer_bottom_left_corner",
                (2, 3): "main_bottom_edge",
                (3, 3): "main_outer_bottom_right_corner",
            },
        )

    def test_high_elevation_cells_keep_lower_visual_layers(self):
        elevations = [[0 for _ in range(5)] for _ in range(5)]
        for x in range(1, 4):
            for y in range(1, 4):
                elevations[x][y] = 1
        elevations[2][2] = 2
        terrain = TerrainMap.from_elevations(elevations)

        center = terrain.cell((2, 2))

        self.assertEqual(center.tile_name, "single_platform_dot")
        self.assertEqual(center.layer_tile_names, ("main_center", "main_center", "single_platform_dot"))

    def test_enclosed_cutout_tiles_follow_inner_9_rules(self):
        elevations = [[1 for _ in range(5)] for _ in range(5)]
        elevations[2][2] = 0
        terrain = TerrainMap.from_elevations(elevations)

        self.assert_tile_names(
            terrain,
            {
                (1, 1): "inner_cutout_top_left_surround",
                (2, 1): "inner_cutout_top_edge",
                (3, 1): "inner_cutout_top_right_surround",
                (1, 2): "inner_cutout_left_edge",
                (3, 2): "inner_cutout_right_edge",
                (1, 3): "inner_cutout_bottom_left_surround",
                (2, 3): "inner_cutout_bottom_edge",
                (3, 3): "inner_cutout_bottom_right_surround",
            },
        )

    def test_one_tile_loop_tiles_follow_mixed_inner_outer_rules(self):
        high_cells = {(x, y) for x in range(1, 4) for y in range(1, 4)} - {(2, 2)}
        terrain = TerrainMap.from_elevations(_elevations(5, 5, high_cells))

        self.assert_tile_names(
            terrain,
            {
                (1, 1): "mixed_loop_top_left",
                (2, 1): "mixed_loop_top",
                (3, 1): "mixed_loop_top_right",
                (1, 2): "mixed_loop_left",
                (3, 2): "mixed_loop_right",
                (1, 3): "mixed_loop_bottom_left",
                (2, 3): "mixed_loop_bottom",
                (3, 3): "mixed_loop_bottom_right",
            },
        )

    def test_single_tile_path_caps_follow_single_path_rules(self):
        terrain = TerrainMap.from_elevations(_elevations(5, 5, {(2, 2)}))
        self.assertEqual(terrain.cell((2, 2)).tile_name, "single_platform_dot")

        terrain = TerrainMap.from_elevations(_elevations(5, 5, {(2, 2), (2, 3)}))
        self.assertEqual(terrain.cell((2, 2)).tile_name, "single_path_end_north")
        self.assertEqual(terrain.cell((2, 3)).tile_name, "single_path_end_south")

        terrain = TerrainMap.from_elevations(_elevations(5, 5, {(2, 2), (3, 2)}))
        self.assertEqual(terrain.cell((2, 2)).tile_name, "single_path_end_west")
        self.assertEqual(terrain.cell((3, 2)).tile_name, "single_path_end_east")

    def test_stair_feature_uses_stair_sprite_not_south_end_cap(self):
        terrain = TerrainMap.from_elevations(_elevations(5, 5, {(2, 2)}), {(2, 3): STAIR_SOUTH})

        self.assertEqual(terrain.cell((2, 2)).tile_name, "single_platform_dot")
        self.assertEqual(terrain.cell((2, 3)).feature_tile_name, "stairs_south")

    def test_cliff_faces_render_on_lower_side_of_south_facing_ledge(self):
        high_cells = {(x, y) for x in range(1, 4) for y in range(1, 4)}
        terrain = TerrainMap.from_elevations(_elevations(5, 5, high_cells))

        self.assertIsNone(terrain.cell((2, 3)).cliff_tile_name)
        self.assertEqual(terrain.cell((1, 4)).cliff_tile_name, "cliff_bottom_left")
        self.assertEqual(terrain.cell((2, 4)).cliff_tile_name, "cliff_bottom_center")
        self.assertEqual(terrain.cell((3, 4)).cliff_tile_name, "cliff_bottom_right")

    def test_stairs_break_cliff_face_segments(self):
        elevations = [[0 for _ in range(5)] for _ in range(7)]
        for x in range(1, 6):
            elevations[x][1] = 1
        terrain = TerrainMap.from_elevations(elevations, {(3, 2): STAIR_SOUTH})

        self.assertEqual(terrain.cell((1, 2)).cliff_tile_name, "cliff_bottom_left")
        self.assertEqual(terrain.cell((2, 2)).cliff_tile_name, "cliff_bottom_right")
        self.assertIsNone(terrain.cell((3, 2)).cliff_tile_name)
        self.assertEqual(terrain.cell((4, 2)).cliff_tile_name, "cliff_bottom_left")
        self.assertEqual(terrain.cell((5, 2)).cliff_tile_name, "cliff_bottom_right")

    def test_unit_radius_can_overlap_stair_corners_but_not_plain_cliffs(self):
        elevations = [[0 for _ in range(5)] for _ in range(5)]
        for x in range(1, 4):
            elevations[x][2] = 1

        blocked = GameGrid(5, 5, config.TILE_SIZE, terrain=TerrainMap.from_elevations(elevations))
        stair = GameGrid(5, 5, config.TILE_SIZE, terrain=TerrainMap.from_elevations(elevations, {(2, 3): STAIR_SOUTH}))
        corner_point = stair.world_center((2, 3)) + (-12, -14)

        self.assertFalse(blocked.circle_clear(corner_point, 12))
        self.assertTrue(stair.circle_clear(corner_point, 12))

    def test_units_can_exit_stairs_from_corner_overlap(self):
        elevations = [[0 for _ in range(5)] for _ in range(5)]
        for x in range(1, 4):
            elevations[x][2] = 1

        blocked = GameGrid(5, 5, config.TILE_SIZE, terrain=TerrainMap.from_elevations(elevations))
        stair = GameGrid(5, 5, config.TILE_SIZE, terrain=TerrainMap.from_elevations(elevations, {(2, 3): STAIR_SOUTH}))
        lower_apron = stair.world_center((2, 3)) + (-10, -12)
        upper_apron = stair.world_center((2, 2)) + (-10, 14)

        self.assertFalse(blocked.line_clear(lower_apron, upper_apron, 12))
        self.assertTrue(stair.line_clear(lower_apron, upper_apron, 12))

    def test_stair_overlap_does_not_turn_adjacent_cliffs_into_stairs(self):
        elevations = [[0 for _ in range(5)] for _ in range(5)]
        for x in range(1, 4):
            elevations[x][2] = 1

        grid = GameGrid(5, 5, config.TILE_SIZE, terrain=TerrainMap.from_elevations(elevations, {(2, 3): STAIR_SOUTH}))
        lower_cliff_side = grid.world_center((1, 3)) + (10, -12)
        upper_cliff_side = grid.world_center((1, 2)) + (10, 14)

        self.assertTrue(grid.circle_clear(lower_cliff_side, 12))
        self.assertTrue(grid.circle_clear(upper_cliff_side, 12))
        self.assertFalse(grid.line_clear(lower_cliff_side, upper_cliff_side, 12))

    def test_procedural_terrain_has_elevation_stairs_and_reachable_spawns(self):
        random.seed(11)
        grid = GameGrid(64, 48, config.TILE_SIZE, terrain_seed=1234)

        elevations = [
            grid.terrain.elevation_at((x, y))
            for x in range(grid.width)
            for y in range(grid.height)
        ]
        stair_count = sum(
            1
            for x in range(grid.width)
            for y in range(grid.height)
            if grid.terrain.cell((x, y)).feature == STAIR_SOUTH
        )

        self.assertGreater(max(elevations), 0)
        self.assertGreater(stair_count, 0)
        self.assertTrue(grid.all_spawns_reachable())

    def test_starting_area_is_flat_for_core_and_initial_setup(self):
        random.seed(17)
        state = GameState()
        grid = state.grid
        core_elevation = grid.terrain.elevation_at(grid.townhall_cell)
        cx, cy = grid.townhall_cell

        for x in range(cx - 3, cx + 4):
            for y in range(cy - 3, cy + 4):
                self.assertEqual(grid.terrain.elevation_at((x, y)), core_elevation)
                self.assertIsNone(grid.terrain.cell((x, y)).feature)

        self.assertIsNotNone(state.selected_house)
        assert state.selected_house is not None
        self.assertTrue(grid.terrain.is_buildable(state.selected_house.cell))
        self.assertEqual(grid.terrain.elevation_at(state.selected_house.cell), core_elevation)
        for troop in state.troops:
            cell = grid.cell_from_world(troop.pos)
            self.assertTrue(grid.passable(cell))
            self.assertIsNotNone(grid.distance_at(cell))

    def test_elevation_changes_require_south_facing_stairs(self):
        elevations = [[0 for _ in range(5)] for _ in range(5)]
        elevations[2][2] = 1

        no_stairs = TerrainMap.from_elevations(elevations)
        self.assertFalse(no_stairs.can_traverse((2, 3), (2, 2)))
        self.assertFalse(no_stairs.can_traverse((2, 2), (2, 3)))

        with_stairs = TerrainMap.from_elevations(elevations, {(2, 3): STAIR_SOUTH})
        self.assertTrue(with_stairs.can_traverse((2, 3), (2, 2)))
        self.assertTrue(with_stairs.can_traverse((2, 2), (2, 3)))

    def test_grid_flow_respects_stair_transitions(self):
        elevations = [[0 for _ in range(7)] for _ in range(7)]
        elevations[3][3] = 1

        blocked = GameGrid(7, 7, config.TILE_SIZE, terrain=TerrainMap.from_elevations(elevations))
        self.assertIsNone(blocked.distance_at((3, 6)))

        connected = GameGrid(7, 7, config.TILE_SIZE, terrain=TerrainMap.from_elevations(elevations, {(3, 4): STAIR_SOUTH}))
        self.assertIsNotNone(connected.distance_at((3, 6)))

    def test_circle_resolution_slides_along_walls_instead_of_snapping_back(self):
        grid = GameGrid(7, 7, config.TILE_SIZE, procedural_terrain=False)
        grid.walls.add((3, 3))
        grid.recompute_flow()
        radius = 10.0
        start = grid.world_center((2, 3))
        intended = start + (20, 14)

        self.assertFalse(grid.line_clear(start, intended, radius))
        resolved, collided = grid.resolve_circle_blockers(intended, radius, start)

        self.assertTrue(collided)
        self.assertTrue(grid.circle_clear(resolved, radius))
        self.assertGreater(resolved.y, start.y + 4.0)
        self.assertLessEqual(resolved.x, grid.cell_rect((3, 3)).left - radius + 0.5)

    def test_circle_resolution_recovers_from_embedded_wall_position(self):
        grid = GameGrid(7, 7, config.TILE_SIZE, procedural_terrain=False)
        grid.walls.add((3, 3))
        grid.recompute_flow()
        radius = 10.0
        inside_wall = grid.world_center((3, 3))

        resolved, collided = grid.resolve_circle_blockers(inside_wall, radius)

        self.assertTrue(collided)
        self.assertTrue(grid.circle_clear(resolved, radius))


class TerrainShadowTests(unittest.TestCase):
    def test_shadow_map_includes_underlying_elevation_layers(self):
        elevations = [[0 for _ in range(3)] for _ in range(3)]
        elevations[1][1] = 1
        terrain = TerrainMap.from_elevations(elevations)
        calculator = TerrainShadowCalculator(
            TerrainShadowSettings(
                max_opacity=1.0,
                elevation_step_opacity=0.4,
                cardinal_higher_opacity=0.0,
                diagonal_higher_opacity=0.0,
                front_exposure_opacity=0.0,
                depth_opacity=0.0,
                bands=0,
            )
        )

        layer_opacities = calculator.layer_opacity_map(terrain)[1][1]

        self.assertEqual(len(layer_opacities), 2)
        self.assertAlmostEqual(layer_opacities[0], 0.4)
        self.assertAlmostEqual(layer_opacities[1], calculator.opacity_for(terrain, (1, 1), reference_elevation=1))

    def test_lower_tiles_get_shadow_from_reference_elevation(self):
        elevations = [[0 for _ in range(5)] for _ in range(5)]
        elevations[0][0] = 2
        terrain = TerrainMap.from_elevations(elevations)
        calculator = TerrainShadowCalculator(
            TerrainShadowSettings(
                max_opacity=1.0,
                elevation_step_opacity=0.2,
                cardinal_higher_opacity=0.0,
                diagonal_higher_opacity=0.0,
                front_exposure_opacity=0.0,
                depth_opacity=0.0,
                bands=0,
            )
        )

        self.assertAlmostEqual(calculator.opacity_for(terrain, (2, 2)), 0.4)
        self.assertEqual(calculator.opacity_for(terrain, (0, 0)), 0.0)

    def test_higher_neighbors_contribute_with_weaker_diagonals(self):
        elevations = [[1 for _ in range(3)] for _ in range(3)]
        elevations[2][1] = 2
        elevations[2][0] = 2
        terrain = TerrainMap.from_elevations(elevations)
        calculator = TerrainShadowCalculator(
            TerrainShadowSettings(
                max_opacity=1.0,
                elevation_step_opacity=0.0,
                cardinal_higher_opacity=0.2,
                diagonal_higher_opacity=0.05,
                front_exposure_opacity=0.0,
                depth_opacity=0.0,
                bands=0,
            )
        )

        self.assertAlmostEqual(calculator.opacity_for(terrain, (1, 1), reference_elevation=1), 0.25)

    def test_lower_front_neighbors_reduce_shadow_on_exposed_tiles(self):
        sheltered_elevations = [[1 for _ in range(3)] for _ in range(3)]
        exposed_elevations = [[1 for _ in range(3)] for _ in range(3)]
        exposed_elevations[0][2] = 0
        exposed_elevations[2][2] = 0
        calculator = TerrainShadowCalculator(
            TerrainShadowSettings(
                max_opacity=1.0,
                elevation_step_opacity=0.5,
                cardinal_higher_opacity=0.0,
                diagonal_higher_opacity=0.0,
                front_exposure_opacity=0.1,
                depth_opacity=0.0,
                bands=0,
            )
        )

        sheltered = calculator.opacity_for(TerrainMap.from_elevations(sheltered_elevations), (1, 1), reference_elevation=2)
        exposed = calculator.opacity_for(TerrainMap.from_elevations(exposed_elevations), (1, 1), reference_elevation=2)

        self.assertAlmostEqual(sheltered, 0.5)
        self.assertAlmostEqual(exposed, 0.3)
        self.assertLess(exposed, sheltered)

    def test_shadow_opacity_quantizes_before_max_opacity(self):
        elevations = [[0 for _ in range(3)] for _ in range(3)]
        elevations[0][0] = 1
        terrain = TerrainMap.from_elevations(elevations)
        calculator = TerrainShadowCalculator(
            TerrainShadowSettings(
                max_opacity=0.5,
                elevation_step_opacity=0.26,
                cardinal_higher_opacity=0.0,
                diagonal_higher_opacity=0.0,
                front_exposure_opacity=0.0,
                depth_opacity=0.0,
                bands=10,
            )
        )

        self.assertAlmostEqual(calculator.opacity_for(terrain, (2, 2)), 0.15)

    def test_shadow_calculation_does_not_change_navigation_or_buildability(self):
        elevations = [[0 for _ in range(5)] for _ in range(5)]
        elevations[2][2] = 1
        terrain = TerrainMap.from_elevations(elevations, {(2, 3): STAIR_SOUTH})
        grid = GameGrid(5, 5, config.TILE_SIZE, terrain=terrain)
        before = (
            grid.terrain.can_traverse((2, 3), (2, 2)),
            grid.terrain.is_buildable((1, 1)),
            grid.distance_at((2, 4)),
        )

        TerrainShadowCalculator().opacity_for(terrain, (2, 3))

        after = (
            grid.terrain.can_traverse((2, 3), (2, 2)),
            grid.terrain.is_buildable((1, 1)),
            grid.distance_at((2, 4)),
        )
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
