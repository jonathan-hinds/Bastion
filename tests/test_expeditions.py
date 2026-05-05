from collections import deque
from dataclasses import replace
import random
import unittest

import pygame

from bastion import config
from bastion.game.abilities import configure_troop_abilities
from bastion.game.expedition_defs import EXPEDITION_DEFINITIONS, default_expedition_definition
from bastion.game.expeditions import EXPEDITION_TILE_SIZE, ExpeditionDungeonGenerator, ExpeditionResult, ExpeditionRun
from bastion.game.combat_stats import ATTRIBUTE_ORDER
from bastion.game.entities import Enemy
from bastion.game.enemy_defs import enemy_ids_by_role
from bastion.game.state import GameState
from bastion.game.units import ExpeditionCampsite, Troop
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

    def test_normal_role_pools_do_not_include_bosses(self):
        self.assertFalse(any(enemy_id.startswith("boss_") for enemy_id in enemy_ids_by_role("ranged")))

    def test_expedition_difficulty_multiplier_is_baseline(self):
        self.assertEqual(default_expedition_definition().enemy_stat_budget_multiplier, 1.0)

    def test_scaled_expedition_dungeon_keeps_world_size_with_smaller_tiles(self):
        definition = default_expedition_definition()
        layout = ExpeditionDungeonGenerator(definition, random.Random(1234)).generate()

        self.assertEqual(layout.grid.tile_size, EXPEDITION_TILE_SIZE)
        self.assertEqual(EXPEDITION_TILE_SIZE, config.TILE_SIZE // 2)
        self.assertEqual(
            layout.grid.world_size,
            (definition.dungeon.width * config.TILE_SIZE, definition.dungeon.height * config.TILE_SIZE),
        )
        self.assertGreater(layout.grid.width, definition.dungeon.width)
        self.assertGreater(layout.grid.height, definition.dungeon.height)

    def test_generated_expedition_floor_is_fully_connected(self):
        layout = ExpeditionDungeonGenerator(default_expedition_definition(), random.Random(1234)).generate()
        floor = set(layout.floor_cells)
        queue = deque([layout.start_room.rect.center])
        visited = {layout.start_room.rect.center}

        while queue:
            x, y = queue.popleft()
            for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbor not in floor or neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)

        self.assertEqual(visited, floor)
        self.assertIn(layout.boss_room.rect.center, visited)
        self.assertIn(layout.exit_cell, visited)


class ExpeditionStateTests(unittest.TestCase):
    def make_state_with_camp(self):
        state = GameState()
        campsite = ExpeditionCampsite((state.grid.townhall_cell[0] + 5, state.grid.townhall_cell[1]), state.grid)
        state.buildings.append(campsite)
        state.has_arcane_power = lambda _structure: True
        return state

    def make_fonts(self):
        pygame.font.init()
        return {
            "tiny": pygame.font.Font(None, 16),
            "small": pygame.font.Font(None, 20),
            "medium": pygame.font.Font(None, 24),
            "body": pygame.font.Font(None, 24),
            "large": pygame.font.Font(None, 32),
            "title": pygame.font.Font(None, 42),
        }

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

    def test_expedition_enemy_stats_scale_from_party_budget(self):
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:3]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())
        run = state.expedition_run
        self.assertIsNotNone(run)

        enemy = run.spawn_enemy_at("ranged", run.party_center + pygame.Vector2(80, 0))
        self.assertIsNotNone(enemy.attributes)
        expected_budget = int(round(run.enemy_stat_budget * run.definition.enemy_stat_budget_multiplier))
        actual_budget = sum(int(getattr(enemy.attributes, key)) for key in ATTRIBUTE_ORDER)
        self.assertEqual(actual_budget, expected_budget)
        self.assertEqual(enemy.attack_stat, "intellect")
        self.assertAlmostEqual(enemy.damage, enemy.stats()["magic_damage"])

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
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:3]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())

        hud = HUD(self.make_fonts())
        hud.active_panel = "expedition_metrics"
        surface = pygame.Surface((1280, 720))
        screen_rect = surface.get_rect()
        viewport = pygame.Rect(220, 32, 900, 620)

        buttons = hud.layout_buttons(screen_rect, viewport, state)
        hud.draw(surface, screen_rect, viewport, state)

        self.assertTrue(any(button.command == "tool" and button.value == "expedition_metrics" for button in buttons))

    def test_expedition_toolbar_exposes_inspector_panel(self):
        pygame.display.init()
        pygame.display.set_mode((1, 1))
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:2]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())

        hud = HUD(self.make_fonts())
        hud._using_external_windows = lambda: False
        surface = pygame.Surface((1280, 720))
        screen_rect = surface.get_rect()
        viewport = pygame.Rect(220, 32, 900, 620)

        buttons = hud.layout_buttons(screen_rect, viewport, state)
        tool_values = [button.value for button in buttons if button.command == "tool"]
        self.assertIn("inspector", tool_values)
        self.assertIn("expedition_metrics", tool_values)

        hud.active_panel = "inspector"
        buttons = hud.layout_buttons(screen_rect, viewport, state)
        hud.draw(surface, screen_rect, viewport, state)

        party_buttons = [button for button in buttons if button.command == "inspect_expedition_troop"]
        self.assertEqual(len(party_buttons), 2)
        self.assertFalse(any(button.command == "toggle_attack" for button in buttons))

        hud._execute(party_buttons[1], state)
        buttons = hud.layout_buttons(screen_rect, viewport, state)

        self.assertEqual(state.selected_troops, [party_buttons[1].value])
        self.assertTrue(any(button.command == "toggle_attack" for button in buttons))

    def test_clicking_expedition_troop_selects_for_inventory(self):
        pygame.display.init()
        pygame.display.set_mode((1, 1))
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:2]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())
        run = state.expedition_run
        self.assertIsNotNone(run)

        viewport = pygame.Rect(220, 32, 900, 620)
        run.camera.center_on(run.party_center, viewport)
        troop = run.alive_troops[0]
        screen = run.camera.world_to_screen(troop.pos, viewport)
        event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (int(screen.x), int(screen.y))})

        self.assertTrue(run.handle_event(event, viewport))
        self.assertEqual(state.selected_troops, [troop])

        hud = HUD(self.make_fonts())
        surface = pygame.Surface((1280, 720))
        hud.draw(surface, surface.get_rect(), viewport, state)
        self.assertIsNotNone(hud._troop_item_layout(viewport, state))

    def test_expedition_hold_fire_is_individual_and_persists_after_update(self):
        state = self.make_state_with_camp()
        state.selected_troops = state.troops[:2]
        self.assertTrue(state.assign_control_group(0))
        self.assertTrue(state.register_expedition_control_group(0))
        self.assertTrue(state.start_expedition_from_setup())
        run = state.expedition_run
        self.assertIsNotNone(run)

        troop = run.alive_troops[0]
        other = run.alive_troops[1]
        state.select_troop(troop)
        state.toggle_selected_troop_engagement()
        self.assertFalse(troop.attack_enabled)
        self.assertTrue(other.attack_enabled)

        viewport = pygame.Rect(220, 32, 900, 620)
        run.update(1 / 60, PressedKeys(), viewport.center, viewport)

        self.assertFalse(troop.attack_enabled)
        self.assertTrue(other.attack_enabled)

    def test_hero_hall_abilities_run_inside_expeditions(self):
        state = self.make_state_with_camp()
        origin = pygame.Vector2(state.grid.world_center(state.grid.townhall_cell))
        cleric = Troop("cleric", origin, origin)
        ally = Troop("warrior", origin + pygame.Vector2(12, 0), origin + pygame.Vector2(12, 0))
        cleric.hero_node_ranks["cleric:sage:holy_aura"] = 1
        configure_troop_abilities(cleric)
        state.troops = [cleric, ally]
        run = state.expedition_run = ExpeditionRun(state, [cleric, ally], rng=random.Random(12))

        ally.health = max(1.0, ally.health - 30.0)
        before = ally.health
        viewport = pygame.Rect(220, 32, 900, 620)
        for _ in range(90):
            run.update(1 / 60, PressedKeys(), viewport.center, viewport)

        self.assertGreater(ally.health, before)
        self.assertTrue(any(getattr(ability, "ability_id", "") == "catalog_holy_aura" for ability in cleric.abilities.abilities))

    def test_guardian_intercept_redirects_fatal_damage_in_expeditions(self):
        state = self.make_state_with_camp()
        origin = pygame.Vector2(state.grid.world_center(state.grid.townhall_cell))
        guardian = Troop("warrior", origin, origin)
        ally = Troop("archer", origin + pygame.Vector2(10, 0), origin + pygame.Vector2(10, 0))
        guardian.hero_node_ranks["warrior:savior:guardian_intercept"] = 1
        configure_troop_abilities(guardian)
        run = ExpeditionRun(state, [guardian, ally], rng=random.Random(14))
        ally.health = 3.0
        before_guardian = guardian.health

        run.damage_friendly(ally, 12.0, source=None, source_pos=ally.pos)

        self.assertTrue(ally.alive)
        self.assertLess(guardian.health, before_guardian)

    def test_completion_xp_is_split_between_survivors(self):
        state = self.make_state_with_camp()
        origin = pygame.Vector2(state.grid.world_center(state.grid.townhall_cell))
        party = [Troop("grunt", origin + pygame.Vector2(index * 8, 0), origin) for index in range(5)]
        definition = default_expedition_definition()
        definition = replace(definition, rewards=replace(definition.rewards, completion_xp=500))
        run = ExpeditionRun(state, party, definition=definition, rng=random.Random(16))

        result = run._build_result(True, "test")
        self.assertEqual([result.xp_by_troop_id[id(troop)] for troop in party], [100, 100, 100, 100, 100])

        for troop in party[2:]:
            troop.alive = False
        result = run._build_result(True, "test")
        self.assertEqual([result.xp_by_troop_id[id(troop)] for troop in party[:2]], [250, 250])

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
