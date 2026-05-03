import unittest

import pygame

from bastion.game.expedition_defs import EXPEDITION_DEFINITIONS, default_expedition_definition
from bastion.game.expeditions import ExpeditionResult
from bastion.game.entities import Enemy
from bastion.game.state import GameState
from bastion.game.units import ExpeditionCampsite
from bastion.ui.hud import HUD


class PressedKeys:
    def __init__(self, *keys: int) -> None:
        self.keys = set(keys)

    def __getitem__(self, key: int) -> bool:
        return key in self.keys


class ExpeditionDefinitionTests(unittest.TestCase):
    def test_default_expedition_has_three_data_driven_bosses(self):
        definition = default_expedition_definition()

        self.assertIn(definition.expedition_id, EXPEDITION_DEFINITIONS)
        self.assertEqual(definition.max_party_size, 5)
        self.assertEqual({boss.boss_id for boss in definition.bosses}, {"stormcaller", "emberlord", "frostwarden"})
        for boss in definition.bosses:
            self.assertGreaterEqual(len(boss.abilities), 3)


class ExpeditionStateTests(unittest.TestCase):
    def make_state_with_camp(self):
        state = GameState()
        campsite = ExpeditionCampsite((state.grid.townhall_cell[0] + 5, state.grid.townhall_cell[1]), state.grid)
        state.buildings.append(campsite)
        state.has_arcane_power = lambda _structure: True
        return state

    def test_register_reorder_and_start_expedition(self):
        state = self.make_state_with_camp()
        troops = state.troops[:3]
        state.selected_troops = troops
        self.assertTrue(state.assign_control_group(0))

        self.assertTrue(state.register_expedition_control_group(0))
        self.assertEqual(state.expedition_setup_party, troops)
        self.assertTrue(state.reorder_expedition_party(0, 2))
        self.assertEqual(state.expedition_setup_party[2], troops[0])

        self.assertTrue(state.start_expedition_from_setup())
        self.assertIsNotNone(state.expedition_run)
        self.assertFalse(state.paused)
        self.assertEqual(len(state.expedition_run.party), 3)

    def test_expedition_movement_stays_authoritative_during_combat(self):
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:3]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())
        run = state.expedition_run
        self.assertIsNotNone(run)

        viewport = pygame.Rect(220, 32, 900, 620)
        run.camera.center_on(run.party_center, viewport)
        run.camera.clamp_to_world(viewport)
        run.enemies.append(Enemy("small", run.party_center + pygame.Vector2(56, 0), 0, behavior="expedition"))
        before_center = pygame.Vector2(run.party_center)

        for _ in range(12):
            run.update(1 / 60, PressedKeys(pygame.K_d), viewport.center, viewport)

        self.assertGreater(run.party_center.x, before_center.x + 10)

    def test_expedition_idle_party_magnetizes_into_attack_position(self):
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:1]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())
        run = state.expedition_run
        self.assertIsNotNone(run)

        viewport = pygame.Rect(220, 32, 900, 620)
        troop = run.alive_troops[0]
        enemy = Enemy("small", pygame.Vector2(troop.pos) + pygame.Vector2(88, 0), 0, behavior="expedition")
        run.enemies.append(enemy)
        before_troop = pygame.Vector2(troop.pos)
        before_distance = before_troop.distance_to(enemy.pos)

        for _ in range(18):
            run.update(1 / 60, PressedKeys(), viewport.center, viewport)

        self.assertGreater(troop.pos.distance_to(before_troop), 1.0)
        self.assertLess(troop.pos.distance_to(enemy.pos), before_distance)

    def test_expedition_metrics_track_core_combat_events(self):
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:1]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())
        run = state.expedition_run
        self.assertIsNotNone(run)

        troop = run.alive_troops[0]
        troop.roll_critical_hit = lambda: True
        enemy = Enemy("small", pygame.Vector2(troop.pos) + pygame.Vector2(48, 0), 0, behavior="expedition")
        run.enemies.append(enemy)

        run.damage_enemy(enemy, 5, troop)
        run.damage_friendly(troop, 6, source=enemy, source_pos=enemy.pos)
        run.restore_friendly(troop, 3, source=troop, reason="heal", element="holy")
        run.record_ability_activation(troop, type("Ability", (), {"name": "Test Strike"})())
        run.record_stun(troop, enemy, 1.0)

        metrics = run.metrics_by_troop_id[id(troop)]
        self.assertGreater(metrics.damage_done, 0)
        self.assertGreater(metrics.damage_taken, 0)
        self.assertGreater(metrics.healing_done, 0)
        self.assertGreater(metrics.aggro, 0)
        self.assertEqual(metrics.criticals, 1)
        self.assertEqual(metrics.stuns, 1)
        self.assertEqual(metrics.abilities_fired, 1)
        self.assertEqual(run.metric_rows("damage_done")[0]["troop"], troop)

    def test_expedition_metrics_panel_draws(self):
        pygame.display.init()
        pygame.display.set_mode((1, 1))
        pygame.font.init()
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:3]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())

        fonts = {
            "tiny": pygame.font.Font(None, 16),
            "small": pygame.font.Font(None, 20),
            "medium": pygame.font.Font(None, 24),
            "body": pygame.font.Font(None, 24),
            "large": pygame.font.Font(None, 32),
            "title": pygame.font.Font(None, 42),
        }
        hud = HUD(fonts)
        hud.active_panel = "expedition_metrics"
        surface = pygame.Surface((1280, 720))
        screen_rect = surface.get_rect()
        viewport = pygame.Rect(220, 32, 900, 620)

        buttons = hud.layout_buttons(screen_rect, viewport, state)
        hud.draw(surface, screen_rect, viewport, state)

        self.assertTrue(any(button.command == "tool" and button.value == "expedition_metrics" for button in buttons))

    def test_accept_victory_recap_applies_staged_rewards(self):
        state = self.make_state_with_camp()
        party = tuple(state.troops[:2])
        before_gold = state.gold
        before_xp = party[0].xp
        result = ExpeditionResult(
            victory=True,
            reason="test",
            definition_name="Test",
            boss_name="Boss",
            party=party,
            gold=25,
            items=(),
            xp_by_troop_id={id(party[0]): 15},
            dead_troop_ids=frozenset({id(party[1])}),
        )
        party[1].alive = False
        state.expedition_recap = result

        self.assertTrue(state.accept_expedition_recap())

        self.assertEqual(state.gold, before_gold + 25)
        self.assertEqual(party[0].xp, before_xp + 15)
        self.assertNotIn(party[1], state.troops)
        self.assertFalse(state.paused)


if __name__ == "__main__":
    unittest.main()
