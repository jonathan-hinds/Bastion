from dataclasses import replace
import random
import unittest

import pygame

from bastion.game.ambient_mobs import AmbientMobManager, AmbientMobSettings, MobCamp, MobRespawnSettings, MobSpawnSettings, MobTemplate
from bastion.game.abilities import TauntAbility
from bastion.game.enemy_camps import EnemyBaseCamp, EnemyBaseCampSettings, EnemyCampStructure
from bastion.game.enemy_defs import ENEMY_DATA
from bastion.game.entities import Enemy
from bastion.game.state import GameState
from bastion.game.units import TROOP_DATA, Troop


class EnemyCampTests(unittest.TestCase):
    def setUp(self) -> None:
        random.seed(7)
        self.state = GameState()

    def test_camp_production_uses_enemy_definitions_and_wave_scaling(self):
        camp = self.state.ambient_mobs.camps[0]
        settings = replace(self.state.ambient_mobs.settings.base_camps, starting_gold=120, starting_minerals=120)
        base = EnemyBaseCamp(camp.center, camp, settings)
        self.assertTrue(base.start(self.state))

        self.state.wave_manager.wave_number = 5
        self.state.wave_manager.active = False
        enemy = base._spawn_unit(self.state, "medium", behavior="ambient")

        self.assertIsInstance(enemy, Enemy)
        self.assertNotIsInstance(enemy, Troop)
        self.assertIn(enemy.kind, ENEMY_DATA)
        self.assertNotIn(enemy.kind, TROOP_DATA)
        expected_wave = 6
        expected_health = ENEMY_DATA["medium"]["health"] * (1.14 + expected_wave * 0.22)
        self.assertAlmostEqual(enemy.max_health, expected_health)

    def test_ambient_camp_can_escalate_into_base(self):
        center = pygame.Vector2(self.state.ambient_mobs.camps[0].center)
        template = MobTemplate("test", 1.0, 1, 1, 20.0, 60.0, 220.0, {"small": 1.0})
        settings = AmbientMobSettings(
            initial_camps=0,
            spawn=MobSpawnSettings(0.0, 0.0, 1),
            respawn=MobRespawnSettings(60.0, 60.0, 5.0, 100.0),
            base_camps=EnemyBaseCampSettings(
                min_night_for_escalation=0,
                escalation_min_seconds=1.0,
                escalation_max_seconds=1.0,
                escalation_chance=1.0,
                max_active_camps=1,
                starting_gold=80,
                starting_minerals=40,
            ),
            templates=(template,),
        )
        manager = AmbientMobManager(self.state.grid, settings)
        camp = MobCamp(center=center, template=template)
        enemy = self.state.spawn_enemy_at("small", center, 1, behavior="ambient", home_pos=center, spawn_group="ambient")
        camp.enemies.append(enemy)
        manager.camps = [camp]

        manager.update(1.25, self.state)

        self.assertIsNotNone(camp.base)
        self.assertTrue(any(isinstance(enemy, EnemyCampStructure) and enemy.kind == "enemy_core" for enemy in self.state.enemies))

    def test_enemy_base_build_order_requires_arcane_routes(self):
        camp = self.state.ambient_mobs.camps[0]
        settings = replace(
            self.state.ambient_mobs.settings.base_camps,
            starting_gold=300,
            starting_minerals=300,
            core_defense_towers=1,
            extractor_defense_towers=1,
        )
        base = EnemyBaseCamp(camp.center, camp, settings)
        self.assertTrue(base.start(self.state))
        self.assertIsNotNone(base.core)
        assert base.core is not None
        self.assertEqual(base.core.arcane_capacity, 18)
        self.assertGreaterEqual(base.core_defense_count(), 1)

        self.assertTrue(base._build_next(self.state))
        extractor = base.live_structures("enemy_extractor")[0]
        self.assertIsNotNone(base.arcane_link_for(extractor))

        self.assertTrue(base._build_next(self.state))
        self.assertIsNone(base.extractor_needing_tower())

        self.assertTrue(base._build_next(self.state))
        self.assertTrue(base.live_structures("enemy_house"))

        self.assertTrue(base._build_next(self.state))
        self.assertTrue(base.live_structures("enemy_barracks"))
        for structure in base.live_structures():
            if structure.kind != "enemy_core":
                link = base.arcane_link_for(structure)
                self.assertIsNotNone(link)
                assert link is not None
                self.assertIs(link.core, base.core)
                self.assertTrue(link.path)

    def test_direct_enemy_structure_placement_still_reserves_arcane_path(self):
        camp = self.state.ambient_mobs.camps[0]
        settings = replace(self.state.ambient_mobs.settings.base_camps, starting_gold=300, starting_minerals=300)
        base = EnemyBaseCamp(camp.center, camp, settings)
        self.assertTrue(base.start(self.state))
        cell = base._find_build_cell(self.state, base.center, max_radius=5)
        self.assertIsNotNone(cell)
        assert cell is not None

        house = base._place_structure(self.state, "enemy_house", cell)

        self.assertIsNotNone(house)
        assert house is not None
        link = base.arcane_link_for(house)
        self.assertIsNotNone(link)
        assert link is not None
        self.assertIs(link.core, base.core)
        self.assertTrue(link.path)

    def test_destroying_enemy_core_releases_its_grid_cell(self):
        camp = self.state.ambient_mobs.camps[0]
        settings = replace(self.state.ambient_mobs.settings.base_camps, starting_gold=120, starting_minerals=120)
        base = EnemyBaseCamp(camp.center, camp, settings)
        self.assertTrue(base.start(self.state))
        core = base.core
        self.assertIsNotNone(core)
        assert core is not None
        self.assertIs(self.state.grid.towers.get(core.cell), core)

        self.state.kill_enemy(core)

        self.assertIsNone(self.state.grid.towers.get(core.cell))
        self.assertFalse(base.alive)

    def test_camp_garrison_joins_normal_night_cleanup(self):
        camp = self.state.ambient_mobs.camps[0]
        settings = replace(self.state.ambient_mobs.settings.base_camps, starting_gold=120, starting_minerals=120)
        base = EnemyBaseCamp(camp.center, camp, settings)
        self.assertTrue(base.start(self.state))
        barracks_cell = base._find_build_cell(self.state, base.center, max_radius=5)
        self.assertIsNotNone(barracks_cell)
        assert barracks_cell is not None
        self.assertIsNotNone(base._place_structure(self.state, "enemy_barracks", barracks_cell))
        garrison = base._spawn_unit(self.state, "small", behavior="ambient")
        surplus_worker = base._spawn_unit(self.state, "small", behavior="ambient")
        second = base._spawn_unit(self.state, "medium", behavior="ambient")
        self.assertIsNotNone(garrison)
        self.assertIsNotNone(surplus_worker)
        self.assertIsNotNone(second)
        assert garrison is not None
        assert surplus_worker is not None
        assert second is not None
        base._make_worker(garrison)
        base._make_worker(surplus_worker)
        garrison.camp_worker.cargo = 1
        self.assertEqual(garrison.spawn_group, "enemy_camp")
        self.assertEqual(surplus_worker.spawn_group, "enemy_camp")
        self.assertEqual(second.spawn_group, "enemy_camp")
        self.assertIsNotNone(getattr(garrison, "camp_worker", None))
        self.assertIsNotNone(getattr(surplus_worker, "camp_worker", None))

        self.state.wave_manager.active = True
        self.state.wave_manager.wave_number = 3
        self.state.wave_manager.spawn_events = []
        base._update_raids(self.state)

        self.assertEqual(garrison.behavior, "ambient")
        self.assertEqual(garrison.spawn_group, "enemy_camp")
        self.assertIsNotNone(getattr(garrison, "camp_worker", None))
        self.assertEqual(surplus_worker.behavior, "assault")
        self.assertEqual(surplus_worker.spawn_group, "wave")
        self.assertIsNone(getattr(surplus_worker, "camp_worker", None))
        self.assertEqual(second.behavior, "assault")
        self.assertEqual(second.spawn_group, "wave")
        self.state.wave_manager.update(0.2, self.state)
        self.assertTrue(self.state.wave_manager.active)

        surplus_worker.alive = False
        second.alive = False
        self.state.wave_manager.update(0.2, self.state)
        self.assertFalse(self.state.wave_manager.active)

    def test_camp_structures_accept_taunt_effects(self):
        camp = self.state.ambient_mobs.camps[0]
        base = EnemyBaseCamp(camp.center, camp, self.state.ambient_mobs.settings.base_camps)
        self.assertTrue(base.start(self.state))
        core = base.core
        self.assertIsNotNone(core)
        assert core is not None
        self.state.fog = None
        warrior = Troop("warrior", core.pos + pygame.Vector2(42, 0), core.pos + pygame.Vector2(42, 0))
        self.state.troops.append(warrior)

        self.assertTrue(TauntAbility(warrior, radius=96.0).activate(self.state))
        self.assertIs(core.taunt_target, warrior)


if __name__ == "__main__":
    unittest.main()
