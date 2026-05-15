import tempfile
import unittest
from pathlib import Path

from bastion.game.settings import GameSettings, load_settings_definition
from bastion.game.state import GameState


class GameSettingsTests(unittest.TestCase):
    def test_settings_definition_exposes_gameplay_tutorial_toggle(self):
        definition = load_settings_definition()
        tutorial = definition.setting("gameplay", "tutorial_enabled")

        self.assertIsNotNone(tutorial)
        assert tutorial is not None
        self.assertEqual(tutorial.value_type, "bool")
        self.assertTrue(tutorial.default)

    def test_boolean_settings_round_trip_to_disk(self):
        definition = load_settings_definition()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            settings = GameSettings.load(path=path, definition=definition)

            self.assertTrue(settings.gameplay.tutorial_enabled)
            settings.set_bool("gameplay", "tutorial_enabled", False)
            settings.save()

            loaded = GameSettings.load(path=path, definition=definition)
            self.assertFalse(loaded.gameplay.tutorial_enabled)

    def test_disabling_tutorial_aborts_active_tutorial_and_next_reset_skips_it(self):
        state = GameState()

        self.assertTrue(state.tutorial.active)
        state.set_tutorial_enabled(False)

        self.assertFalse(state.tutorial.active)
        state.reset()
        self.assertFalse(state.tutorial.active)


if __name__ == "__main__":
    unittest.main()
