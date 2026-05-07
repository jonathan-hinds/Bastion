import random
import unittest

import pygame

from bastion.game.state import GameState
from bastion.game.resources import MineralExtractor
from bastion.game.tutorial import TutorialObjectiveState, load_tutorial_definition


class TutorialSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        random.seed(31)

    def test_tutorial_definition_is_data_driven_and_ordered(self):
        definition = load_tutorial_definition()
        step_ids = [step.step_id for step in definition.steps]

        self.assertTrue(definition.scenario["block_standard_waves"])
        self.assertEqual(step_ids[0], "intro_grunts")
        self.assertLess(step_ids.index("watch_core_damage"), step_ids.index("archer_towers"))
        self.assertLess(step_ids.index("archer_towers"), step_ids.index("build_barracks"))
        self.assertLess(step_ids.index("watch_towers_clear_attack"), step_ids.index("build_barracks"))
        self.assertLess(step_ids.index("build_research"), step_ids.index("build_library"))
        self.assertLess(step_ids.index("build_library"), step_ids.index("build_hero_hall"))
        self.assertEqual(step_ids[-1], "explain_hero_hall")

        steps = {step.step_id: step for step in definition.steps}
        self.assertTrue(steps["watch_core_damage"].lock_input_during_objective)
        self.assertTrue(steps["watch_core_damage"].lock_camera_during_objective)
        self.assertTrue(steps["archer_towers"].pause_during_objective)
        self.assertEqual(steps["archer_towers"].toolbar_hint, "build")

    def test_tutorial_starts_once_per_game_state_launch(self):
        state = GameState()

        self.assertTrue(state.tutorial.active)
        self.assertTrue(state.tutorial.pauses_game)
        self.assertTrue(state.tutorial_played_this_launch)
        self.assertIsNotNone(state.tutorial.target_gold_deposit)
        self.assertIsNone(state.build_mode)

        state.tutorial.complete(aborted=True)
        state.reset()

        self.assertFalse(state.tutorial.active)
        self.assertFalse(state.tutorial.pauses_game)
        self.assertTrue(state.tutorial_played_this_launch)

    def test_dialogue_next_advances_to_following_prompt(self):
        state = GameState()
        viewport = pygame.Rect(58, 86, 1000, 600)
        screen = pygame.Rect(0, 0, 1280, 760)

        self.assertEqual(state.tutorial.current_step.step_id, "intro_grunts")
        handled = state.tutorial.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN),
            screen,
            viewport,
        )

        self.assertTrue(handled)
        self.assertEqual(state.tutorial.current_step.step_id, "intro_core")
        self.assertTrue(state.tutorial.pauses_game)

    def test_select_grunts_objective_moves_flow_forward(self):
        state = GameState()
        while state.tutorial.current_step.step_id != "select_grunts":
            state.tutorial.advance_dialogue()
        state.tutorial.advance_dialogue()

        grunt = next(troop for troop in state.troops if troop.kind == "grunt")
        state.select_troops([grunt])
        state.tutorial.update(0.0)

        self.assertEqual(state.tutorial.current_step.step_id, "assign_grunts")
        self.assertTrue(state.tutorial.pauses_game)

    def test_tutorial_gold_deposit_is_guaranteed(self):
        state = GameState()
        deposit = state.tutorial.target_gold_deposit

        self.assertIsNotNone(deposit)
        assert deposit is not None
        self.assertEqual(deposit.kind, "gold")
        self.assertTrue(deposit.active)
        self.assertIn(deposit, state.resource_deposits)

    def test_first_attack_waits_for_core_damage_before_tower_prompt(self):
        state = GameState()
        step_ids = [step.step_id for step in state.tutorial.definition.steps]
        state.tutorial.current_index = step_ids.index("watch_core_damage")
        state.tutorial._enter_current_step()

        self.assertEqual(state.tutorial.current_step.step_id, "watch_core_damage")
        self.assertFalse(state.tutorial.pauses_game)
        self.assertTrue(state.tutorial.blocks_player_input)
        self.assertTrue(state.tutorial.locks_camera)

        state.tutorial.notify_core_damaged(state.core_target, 5)

        self.assertEqual(state.tutorial.current_step.step_id, "archer_towers")
        self.assertTrue(state.tutorial.pauses_game)
        self.assertIsNotNone(state.tutorial.dialogue)

    def test_archer_objective_keeps_game_paused_after_dialogue(self):
        state = GameState()
        step_ids = [step.step_id for step in state.tutorial.definition.steps]
        state.tutorial.current_index = step_ids.index("archer_towers")
        state.tutorial._enter_current_step()

        self.assertTrue(state.tutorial.pauses_game)
        state.tutorial.advance_dialogue()

        self.assertEqual(state.tutorial.current_step.step_id, "archer_towers")
        self.assertIsNone(state.tutorial.dialogue)
        self.assertTrue(state.tutorial.pauses_game)
        self.assertFalse(state.tutorial.blocks_player_input)

    def test_gold_extractor_build_counts_before_worker_assignment(self):
        state = GameState()
        deposit = state.tutorial.target_gold_deposit
        assert deposit is not None
        extractor = MineralExtractor(deposit.cell, state.grid, deposit)
        state.buildings.append(extractor)
        objective = next(
            step.objective
            for step in state.tutorial.definition.steps
            if step.step_id == "claim_gold"
        )
        assert objective is not None

        progress = TutorialObjectiveState(objective, state.tutorial).progress(state.tutorial)

        self.assertTrue(progress.complete)
        self.assertEqual(progress.current, 1)


if __name__ == "__main__":
    unittest.main()
