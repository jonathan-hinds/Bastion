from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "settings.json"
SETTINGS_FILENAME = "settings.json"


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    setting_id: str
    label: str
    description: str
    value_type: str
    default: bool
    on_label: str = "ON"
    off_label: str = "OFF"


@dataclass(frozen=True, slots=True)
class SettingsTabDefinition:
    tab_id: str
    label: str
    settings: tuple[SettingDefinition, ...]


@dataclass(frozen=True, slots=True)
class SettingsDefinition:
    tabs: tuple[SettingsTabDefinition, ...]

    def setting(self, tab_id: str, setting_id: str) -> SettingDefinition | None:
        for tab in self.tabs:
            if tab.tab_id != tab_id:
                continue
            return next((item for item in tab.settings if item.setting_id == setting_id), None)
        return None


@dataclass(frozen=True, slots=True)
class GameplaySettings:
    tutorial_enabled: bool = True


class GameSettings:
    def __init__(
        self,
        definition: SettingsDefinition | None = None,
        values: dict[str, dict[str, bool]] | None = None,
        path: Path | None = None,
    ) -> None:
        self.definition = definition or load_settings_definition()
        self.path = path or default_settings_path()
        self._values = self._defaults()
        if values:
            self._apply_values(values)

    @classmethod
    def load(cls, path: Path | None = None, definition: SettingsDefinition | None = None) -> "GameSettings":
        resolved_path = path or default_settings_path()
        if not resolved_path.exists():
            return cls(definition=definition, path=resolved_path)
        try:
            with resolved_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            raw = {}
        values = raw.get("values", raw) if isinstance(raw, dict) else {}
        return cls(definition=definition, values=values, path=resolved_path)

    @property
    def gameplay(self) -> GameplaySettings:
        return GameplaySettings(tutorial_enabled=self.get_bool("gameplay", "tutorial_enabled"))

    def get_bool(self, tab_id: str, setting_id: str) -> bool:
        definition = self.definition.setting(tab_id, setting_id)
        if definition is None:
            return False
        return bool(self._values.get(tab_id, {}).get(setting_id, definition.default))

    def set_bool(self, tab_id: str, setting_id: str, value: bool) -> bool:
        definition = self.definition.setting(tab_id, setting_id)
        if definition is None or definition.value_type != "bool":
            return False
        self._values.setdefault(tab_id, {})[setting_id] = bool(value)
        return True

    def toggle_bool(self, tab_id: str, setting_id: str) -> bool:
        value = not self.get_bool(tab_id, setting_id)
        self.set_bool(tab_id, setting_id, value)
        return value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"values": self._values}
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def _defaults(self) -> dict[str, dict[str, bool]]:
        values: dict[str, dict[str, bool]] = {}
        for tab in self.definition.tabs:
            values[tab.tab_id] = {setting.setting_id: setting.default for setting in tab.settings}
        return values

    def _apply_values(self, values: dict[str, Any]) -> None:
        for tab in self.definition.tabs:
            tab_values = values.get(tab.tab_id, {})
            if not isinstance(tab_values, dict):
                continue
            for setting in tab.settings:
                if setting.value_type == "bool" and setting.setting_id in tab_values:
                    self._values[tab.tab_id][setting.setting_id] = bool(tab_values[setting.setting_id])


def default_settings_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "BastionOfTheCore" / SETTINGS_FILENAME
    return Path.home() / ".bastion_of_the_core" / SETTINGS_FILENAME


def load_settings_definition(path: Path | None = None) -> SettingsDefinition:
    resolved_path = path or DATA_PATH
    with resolved_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Settings data must be a JSON object.")
    tabs = tuple(_load_tab(record) for record in raw.get("tabs", ()))
    return SettingsDefinition(tabs=tabs)


def _load_tab(raw: Any) -> SettingsTabDefinition:
    if not isinstance(raw, dict):
        raise ValueError("Settings tab data must be a JSON object.")
    settings = tuple(_load_setting(record) for record in raw.get("settings", ()))
    return SettingsTabDefinition(
        tab_id=str(raw.get("id", "")),
        label=str(raw.get("label", "")),
        settings=settings,
    )


def _load_setting(raw: Any) -> SettingDefinition:
    if not isinstance(raw, dict):
        raise ValueError("Settings item data must be a JSON object.")
    return SettingDefinition(
        setting_id=str(raw.get("id", "")),
        label=str(raw.get("label", "")),
        description=str(raw.get("description", "")),
        value_type=str(raw.get("type", "bool")),
        default=bool(raw.get("default", False)),
        on_label=str(raw.get("on_label", "ON")),
        off_label=str(raw.get("off_label", "OFF")),
    )
