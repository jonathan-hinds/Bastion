from __future__ import annotations

import math
from dataclasses import dataclass

import pygame

from bastion import config
from bastion.engine import hover_feedback
from bastion.engine.drawing import draw_circle_alpha, draw_line_alpha, draw_rect_alpha
from bastion.game.abilities import AbilityCard
from bastion.game.build_catalog import BUILD_CATEGORY_BY_ID, BuildMenuEntry, iter_build_categories
from bastion.game.expeditions import EXPEDITION_METRIC_LABELS, EXPEDITION_METRIC_OPTIONS
from bastion.game.hero_trees import HeroNodeDefinition, HeroTreeDefinition
from bastion.game.items import ITEM_DEFINITIONS
from bastion.game.research import RESEARCH_DEFINITIONS
from bastion.game.tower_defs import SPECIALIZATIONS, xp_needed
from bastion.game.tower_mods import TOWER_MODS
from bastion.game.units import ATTRIBUTE_ORDER, ATTRIBUTE_SHORT_LABELS, ExpeditionCampsite, HOUSE_CAPACITY, TROOP_DATA, TROOP_NAMES, troop_ability_cards
from bastion.ui.minimap import MinimapPanel
from bastion.ui.panel_window import PanelWindow
from bastion.ui.widgets import Button


BASE_TOOLBAR_ENTRY = ("build", "B", "Build")
MAP_TOOLBAR_ENTRY = ("map", "M", "Map")
LEVEL_TOOLBAR_ENTRY = ("level", "L", "Levels")
HERO_TOOLBAR_ENTRY = ("hero", "H", "Hero")
INSPECTOR_TOOLBAR_ENTRY = ("inspector", "N", "Inspect")
EXPEDITION_TOOLBAR_ENTRY = ("expedition", "E", "Expedition")
EXPEDITION_METRICS_TOOLBAR_ENTRY = ("expedition_metrics", "M", "Metrics")

RESEARCH_CATEGORIES = (
    ("Tower Systems", ("archer_attack_speed", "cannon_damage", "wizard_tower_range", "wizard_lightning_arc", "wizard_freeze_duration")),
    ("Troop Doctrine", ("warrior_taunt_cooldown", "wizard_lightning_damage", "cleric_healing_cooldown", "grunt_carry_capacity", "grunt_work_speed")),
    ("Infrastructure", ("research_time", "scroll_production_time")),
)

TROOP_DESCRIPTIONS = {
    "grunt": "Worker unit. Harvests mineral and gold deposits and returns them to the nearest core.",
    "warrior": "Heavy melee unit. Taunts enemies and holds attention inside its station radius.",
    "archer": "Long-range troop. Fires precise single shots and prioritizes ranged enemies.",
    "cleric": "Support unit. Heals damaged troops while staying near station.",
    "engineer": "Support unit. Repairs damaged towers when stationed nearby.",
    "wizard": "Short-range caster. Chains lightning between nearby enemies.",
    "rune_mage": "Support caster. Recharges shield generators and casts ice area attacks.",
}


@dataclass
class TooltipRequest:
    card: object
    mouse_pos: tuple[int, int]


class HUD:
    def __init__(self, fonts: dict[str, pygame.font.Font]) -> None:
        self.fonts = fonts
        self.buttons: list[Button] = []
        self.active_panel: str | None = None
        self.dialog_scroll = 0.0
        self.dialog_scroll_max = 0.0
        self.dialog_positions: dict[str, tuple[int, int]] = {}
        self.dragging_panel: str | None = None
        self.drag_offset = pygame.Vector2(0, 0)
        self.units_panel_barracks = None
        self.research_panel_lab = None
        self.last_context_signature: tuple[str, int] | None = None
        self.panel_windows = {
            "build": PanelWindow("build", "Bastion // Build", (780, 560), (90, 120)),
            "units": PanelWindow("units", "Bastion // Units", (700, 560), (130, 150)),
            "research": PanelWindow("research", "Bastion // Research", (780, 620), (160, 130)),
            "items": PanelWindow("items", "Bastion // Inventory", (640, 520), (200, 160)),
            "level": PanelWindow("level", "Bastion // Level Up", (560, 560), (220, 140)),
            "hero": PanelWindow("hero", "Bastion // Hero Hall", (860, 640), (180, 120)),
            "expedition": PanelWindow("expedition", "Bastion // Expedition", (720, 620), (170, 130)),
            "map": PanelWindow("map", "Bastion // Map", (620, 470), (260, 130)),
            "inspector": PanelWindow("inspector", "Bastion // Inspector", (360, 640), (980, 120)),
        }
        self.minimap = MinimapPanel()
        self.window_buttons: dict[str, list[Button]] = {}
        self.window_scrolls: dict[str, float] = {}
        self.window_hover_targets: dict[str, tuple | None] = {}
        self.inspector_enabled = False
        self.render_mouse_pos = (-1, -1)
        self.tooltip_request: TooltipRequest | None = None
        self.item_drag: dict | None = None
        self.item_context_menu: dict | None = None
        self.hero_tree_zoom = 1.0
        self.hero_tree_pan = pygame.Vector2(0, 0)
        self.hero_tree_dragging = False
        self.hero_tree_drag_last = pygame.Vector2(0, 0)
        self.hero_tree_context_signature: tuple[str, int] | None = None
        self.expedition_drag_index: int | None = None
        self.expedition_metric_slots = ["damage_done", "damage_taken", "healing_done", "dps"]
        self.expedition_metric_dropdown: int | None = None

    def _mouse_pos(self) -> tuple[int, int]:
        return self.render_mouse_pos

    def map_pan_direction(self) -> pygame.Vector2:
        return self.minimap.pan_direction()

    def set_parent_window(self, parent_hwnd: int | None) -> None:
        for window in self.panel_windows.values():
            window.set_parent_window(parent_hwnd)

    def layout_buttons(self, screen_rect: pygame.Rect, viewport: pygame.Rect, state) -> list[Button]:
        buttons: list[Button] = []
        choosing_event = state.round_events.awaiting_choice
        self._sync_contextual_panels(state)
        if getattr(state, "expedition_run", None) is not None and self.active_panel not in (None, "expedition_metrics", "inspector"):
            self.active_panel = None

        status_y = config.TITLE_BAR_HEIGHT + 13
        x = max(config.TOOLBAR_WIDTH + 500, screen_rect.right - 344)
        tutorial_paused = bool(getattr(getattr(state, "tutorial", None), "pauses_game", False))
        effective_paused = bool(state.paused or tutorial_paused)
        for label, speed in (("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("3x", 3.0)):
            buttons.append(
                Button(
                    pygame.Rect(x, status_y, 48, 28),
                    label,
                    "speed",
                    speed,
                    selected=(not effective_paused and math.isclose(state.time_scale, speed)),
                )
            )
            x += 53
        buttons.append(Button(pygame.Rect(x, status_y, 38, 28), "||", "pause", selected=effective_paused))
        x += 44
        buttons.append(
                Button(
                    pygame.Rect(x, status_y, 76, 28),
                    "NIGHT",
                    "start",
                    enabled=not state.wave_manager.active
                    and not state.game_over
                    and not choosing_event
                    and not getattr(state.tutorial, "blocks_standard_waves", False),
                )
        )

        tool_y = config.TOP_BAR_HEIGHT + 12
        for panel, glyph, _name in self._toolbar_entries(state):
            selected = self.panel_windows.get(panel).visible if self._using_external_windows() and panel in self.panel_windows else self.active_panel == panel
            buttons.append(
                Button(
                    pygame.Rect(9, tool_y, config.TOOLBAR_WIDTH - 18, 40),
                    glyph,
                    "tool",
                    panel,
                    selected=bool(selected),
                    enabled=not choosing_event,
                )
            )
            tool_y += 48

        if self.active_panel and not choosing_event and (not self._using_external_windows() or self.active_panel == "expedition_metrics"):
            panel_rect = self._active_panel_rect(screen_rect, viewport)
            buttons.append(Button(pygame.Rect(panel_rect.right - 34, panel_rect.top + 8, 22, 22), "X", "close_panel"))
            if self.active_panel == "build":
                buttons.extend(self._layout_build_buttons(panel_rect, state))
            elif self.active_panel == "research":
                buttons.extend(self._layout_research_buttons(panel_rect, state))
            elif self.active_panel == "units":
                buttons.extend(self._layout_unit_buttons(panel_rect, state))
            elif self.active_panel == "items":
                buttons.extend(self._layout_item_buttons(panel_rect, state))
            elif self.active_panel == "level":
                buttons.extend(self._layout_level_buttons(panel_rect, state))
            elif self.active_panel == "hero":
                buttons.extend(self._layout_hero_buttons(panel_rect, state))
            elif self.active_panel == "expedition":
                buttons.extend(self._layout_expedition_buttons(panel_rect, state))
            elif self.active_panel == "expedition_metrics":
                buttons.extend(self._layout_expedition_metric_buttons(panel_rect, state))
            elif self.active_panel == "inspector" and getattr(state, "expedition_run", None) is not None:
                buttons.extend(self._layout_context_buttons_for_rect(panel_rect, state))

        if choosing_event:
            for event, rect in self._event_card_rects(viewport, state):
                button_rect = pygame.Rect(rect.left + 16, rect.bottom - 48, rect.width - 32, 32)
                buttons.append(Button(button_rect, "ACCEPT", "round_event", event.id))
        else:
            if not self._using_external_windows() and getattr(state, "expedition_run", None) is None:
                buttons.extend(self._layout_context_buttons(screen_rect, viewport, state))

        if state.game_over:
            rect = pygame.Rect(0, 0, 168, 38)
            rect.center = (viewport.centerx, viewport.centery + 80)
            buttons.append(Button(rect, "RESTART", "restart"))

        if getattr(state, "expedition_recap", None) is not None:
            rect = pygame.Rect(0, 0, 160, 36)
            rect.center = (viewport.centerx, viewport.centery + 180)
            buttons.append(Button(rect, "ACCEPT", "expedition_accept_recap"))

        self.buttons = buttons
        return buttons

    def _using_external_windows(self) -> bool:
        return self.panel_windows["build"].available

    def open_panel_window(self, panel: str) -> None:
        window = self.panel_windows.get(panel)
        if window is None:
            return
        if panel == "inspector":
            self.inspector_enabled = True
        window.show()

    def toggle_panel_window(self, panel: str) -> None:
        window = self.panel_windows.get(panel)
        if window is None:
            return
        if window.visible:
            window.close()
            if panel == "inspector":
                self.inspector_enabled = False
        else:
            if panel == "inspector":
                self.inspector_enabled = True
            window.show()

    def close_all_windows(self) -> None:
        for window in self.panel_windows.values():
            window.close()
        self.window_buttons.clear()
        self.window_hover_targets.clear()
        self.minimap.release_input()
        self.dragging_panel = None

    def _toolbar_entries(self, state) -> list[tuple[str, str, str]]:
        if getattr(state, "expedition_run", None) is not None:
            return [INSPECTOR_TOOLBAR_ENTRY, EXPEDITION_METRICS_TOOLBAR_ENTRY]
        entries = [BASE_TOOLBAR_ENTRY, MAP_TOOLBAR_ENTRY, LEVEL_TOOLBAR_ENTRY, INSPECTOR_TOOLBAR_ENTRY]
        if self._living_barracks(state):
            entries.append(("units", "U", "Units"))
        if self._living_research_labs(state):
            entries.append(("research", "R", "Research"))
        if self._hero_panel_available(state):
            entries.append(HERO_TOOLBAR_ENTRY)
        if self._living_expedition_campsites(state) or getattr(state, "expedition_setup_party", None):
            entries.append(EXPEDITION_TOOLBAR_ENTRY)
        if any(getattr(building, "kind", "") == "library" and getattr(building, "alive", False) for building in state.buildings) or any(
            slot is not None for slot in getattr(state.inventory, "slots", [])
        ):
            entries.append(("items", "I", "Items"))
        return entries

    def _living_barracks(self, state) -> list:
        return [building for building in state.buildings if getattr(building, "kind", "") == "barracks" and getattr(building, "alive", False)]

    def _living_research_labs(self, state) -> list:
        return [building for building in state.buildings if getattr(building, "kind", "") == "research" and getattr(building, "alive", False)]

    def _living_hero_halls(self, state) -> list:
        return [building for building in state.buildings if getattr(building, "kind", "") == "hero_hall" and getattr(building, "alive", False)]

    def _living_expedition_campsites(self, state) -> list:
        return [building for building in state.buildings if getattr(building, "kind", "") == "expedition_campsite" and getattr(building, "alive", False)]

    def _hero_panel_available(self, state) -> bool:
        selected = getattr(state, "selected_troop", None)
        if selected is not None and getattr(selected, "alive", False) and getattr(selected, "has_hero_tree", lambda: False)():
            return True
        if self._living_hero_halls(state):
            return True
        return any(
            getattr(troop, "alive", False)
            and getattr(troop, "has_hero_tree", lambda: False)()
            and getattr(troop, "hero_orbs", 0) > 0
            for troop in getattr(state, "troops", [])
        )

    def _best_barracks(self, state):
        barracks = self._living_barracks(state)
        if not barracks:
            return None
        available = [building for building in barracks if building.can_queue()]
        return available[0] if available else barracks[0]

    def _best_research_lab(self, state):
        labs = self._living_research_labs(state)
        if not labs:
            return None
        available = [building for building in labs if building.can_research()]
        return available[0] if available else labs[0]

    def _training_barracks(self, state):
        if self.units_panel_barracks is not None and getattr(self.units_panel_barracks, "alive", False):
            return self.units_panel_barracks
        return self._best_barracks(state)

    def _research_lab_for_panel(self, state):
        if self.research_panel_lab is not None and getattr(self.research_panel_lab, "alive", False):
            return self.research_panel_lab
        return self._best_research_lab(state)

    def _sync_contextual_panels(self, state) -> None:
        if self.active_panel == "units" and not self._living_barracks(state):
            self.active_panel = None
            self.units_panel_barracks = None
        if self.active_panel == "research" and not self._living_research_labs(state):
            self.active_panel = None
            self.research_panel_lab = None
        if self.active_panel == "hero" and not self._hero_panel_available(state):
            self.active_panel = None
            self.hero_tree_dragging = False
        if self.active_panel == "expedition" and not self._living_expedition_campsites(state):
            self.active_panel = None
            self.expedition_drag_index = None

        if self.units_panel_barracks is not None and not getattr(self.units_panel_barracks, "alive", False):
            self.units_panel_barracks = None
        if self.research_panel_lab is not None and not getattr(self.research_panel_lab, "alive", False):
            self.research_panel_lab = None

        signature: tuple[str, int] | None = None
        if state.selected_barracks is not None and getattr(state.selected_barracks, "alive", False):
            signature = ("barracks", id(state.selected_barracks))
        elif state.selected_research is not None and getattr(state.selected_research, "alive", False):
            signature = ("research", id(state.selected_research))
        elif getattr(state, "selected_hero_hall", None) is not None and getattr(state.selected_hero_hall, "alive", False):
            signature = ("hero_hall", id(state.selected_hero_hall))
        elif getattr(state, "selected_expedition_campsite", None) is not None and getattr(state.selected_expedition_campsite, "alive", False):
            signature = ("expedition_campsite", id(state.selected_expedition_campsite))

        if signature is None:
            self.last_context_signature = None
            return
        if signature == self.last_context_signature:
            return

        self.last_context_signature = signature
        if signature[0] == "barracks":
            self.active_panel = "units"
            self.units_panel_barracks = state.selected_barracks
            if self._using_external_windows():
                self.open_panel_window("units")
            self.dialog_scroll = 0.0
            self.dialog_scroll_max = 0.0
        elif signature[0] == "research":
            self.active_panel = "research"
            self.research_panel_lab = state.selected_research
            if self._using_external_windows():
                self.open_panel_window("research")
            self.dialog_scroll = 0.0
            self.dialog_scroll_max = 0.0
        elif signature[0] == "hero_hall":
            self.active_panel = "hero"
            if self._using_external_windows():
                self.open_panel_window("hero")
            self.dialog_scroll = 0.0
            self.dialog_scroll_max = 0.0
        elif signature[0] == "expedition_campsite":
            self.active_panel = "expedition"
            if self._using_external_windows():
                self.open_panel_window("expedition")
            self.dialog_scroll = 0.0
            self.dialog_scroll_max = 0.0

    def handle_event(
        self,
        event: pygame.event.Event,
        state,
        screen_rect: pygame.Rect | None = None,
        viewport: pygame.Rect | None = None,
        camera=None,
    ) -> bool:
        if screen_rect is None or viewport is None:
            return False

        if event.type == pygame.MOUSEWHEEL:
            return self._handle_scroll(event, state, screen_rect, viewport)

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.active_panel == "map" and not self._using_external_windows() and self.minimap.handle_mouse_up():
                return True
            if self.hero_tree_dragging:
                self.hero_tree_dragging = False
                return True
            if self.expedition_drag_index is not None:
                target = None
                if self.active_panel == "expedition":
                    target = self._expedition_orb_at(event.pos, self._active_panel_rect(screen_rect, viewport), state)
                if target is not None and state.reorder_expedition_party(self.expedition_drag_index, target):
                    state.play_sound("menu_select")
                self.expedition_drag_index = None
                return True
            if self.item_drag is not None:
                self._finish_item_drag(event.pos, viewport, state)
                return True
            if self.dragging_panel is not None:
                self.dragging_panel = None
                return True

        if event.type == pygame.MOUSEMOTION and self.dragging_panel is not None:
            panel = self.dragging_panel
            rect = self._panel_rect_for(panel, screen_rect, viewport)
            new_pos = pygame.Vector2(event.pos) - self.drag_offset
            rect.topleft = (int(new_pos.x), int(new_pos.y))
            rect = self._clamp_panel_rect(rect, screen_rect, viewport)
            self.dialog_positions[panel] = rect.topleft
            return True

        if event.type == pygame.MOUSEMOTION and self.hero_tree_dragging:
            self._drag_hero_tree(event.pos)
            return True

        if event.type == pygame.MOUSEMOTION and self.item_drag is not None:
            start = pygame.Vector2(self.item_drag["start_pos"])
            self.item_drag["active"] = self.item_drag["active"] or start.distance_to(event.pos) > 4
            return True

        if event.type == pygame.MOUSEMOTION and self.active_panel == "map" and not self._using_external_windows():
            content_rect = self._dialog_content_rect(self._active_panel_rect(screen_rect, viewport))
            if self.minimap.handle_mouse_motion(event.pos, content_rect, state):
                return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            if self._handle_item_context_click(pos, state):
                return True
            if self.item_context_menu is not None:
                self.item_context_menu = None
                return True

            group_index = None if getattr(state, "expedition_run", None) is not None else self._control_group_slot_at(pos, viewport, state)
            if group_index is not None and not state.round_events.awaiting_choice:
                if pygame.key.get_mods() & pygame.KMOD_CTRL:
                    if state.clear_control_group(group_index):
                        state.play_sound("menu_select")
                elif state.focus_control_group(group_index):
                    state.play_sound("menu_select")
                return True

            source = self._item_source_at(pos, viewport, state)
            if source is not None and self._slot_for_source(source, state) is not None and not state.round_events.awaiting_choice:
                self.item_drag = {"source": source, "start_pos": pos, "active": False}
                return True

            for button in self.buttons:
                if button.contains(pos):
                    state.play_sound("menu_select")
                    self._execute(button, state)
                    return True

            if self.active_panel and self._dialog_title_rect(self._active_panel_rect(screen_rect, viewport)).collidepoint(pos):
                self.dragging_panel = self.active_panel
                rect = self._active_panel_rect(screen_rect, viewport)
                self.drag_offset = pygame.Vector2(pos) - pygame.Vector2(rect.topleft)
                return True

            if self.active_panel == "hero":
                panel_rect = self._active_panel_rect(screen_rect, viewport)
                if self._hero_canvas_rect(panel_rect).collidepoint(pos):
                    self._start_hero_drag(pos)
                    return True
            if self.active_panel == "expedition":
                panel_rect = self._active_panel_rect(screen_rect, viewport)
                orb_index = self._expedition_orb_at(pos, panel_rect, state)
                if orb_index is not None:
                    self.expedition_drag_index = orb_index
                    return True
            if self.active_panel == "map" and not self._using_external_windows():
                content_rect = self._dialog_content_rect(self._active_panel_rect(screen_rect, viewport))
                if self.minimap.handle_mouse_down(pos, content_rect, state):
                    return True

            if self._blocks_world_input(pos, screen_rect, viewport, state):
                return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            pos = event.pos
            source = self._item_source_at(pos, viewport, state)
            if source is not None and self._slot_for_source(source, state) is not None and not state.round_events.awaiting_choice:
                self.item_context_menu = {
                    "source": source,
                    "pos": (
                        min(pos[0], screen_rect.right - 120),
                        min(pos[1], screen_rect.bottom - 92),
                    ),
                }
                return True
            if self.item_context_menu is not None:
                self.item_context_menu = None
                return True

        return False

    def hover_target_at(self, pos: tuple[int, int], viewport: pygame.Rect, state):
        for button in self.buttons:
            if button.contains(pos):
                return ("button", button.command, button.value, button.rect.x, button.rect.y)
        if self.item_context_menu is not None:
            option = self._item_context_option_at(pos, state)
            if option is not None:
                return ("item_context", option[0])
        group_index = None if getattr(state, "expedition_run", None) is not None else self._control_group_slot_at(pos, viewport, state)
        if group_index is not None:
            return ("control_group", group_index)
        source = self._item_source_at(pos, viewport, state)
        if source is not None and self._slot_for_source(source, state) is not None:
            return ("inventory", source)
        return None

    def _selected_troop_for_items(self, state):
        troop = getattr(state, "selected_troop", None)
        troops = getattr(state, "selected_troops", [])
        return troop if troop is not None and len(troops) == 1 else None

    def _slot_for_source(self, source: tuple[str, int], state):
        kind, index = source
        if kind == "player":
            return state.inventory.slot(index)
        troop = self._selected_troop_for_items(state)
        if troop is None:
            return None
        if kind == "troop":
            return troop.inventory.slot(index)
        if kind == "equipment" and 0 <= index < len(troop.equipment_slots):
            return troop.equipment_slots[index]
        return None

    def _item_source_at(self, pos: tuple[int, int], viewport: pygame.Rect, state) -> tuple[str, int] | None:
        equipment = self._troop_equipment_slot_at(pos, viewport, state)
        if equipment is not None:
            return ("equipment", equipment)
        troop_slot = self._troop_inventory_slot_at(pos, viewport, state)
        if troop_slot is not None:
            return ("troop", troop_slot)
        slot_index = self._inventory_slot_at(pos, viewport, state)
        if slot_index is not None:
            return ("player", slot_index)
        return None

    def _finish_item_drag(self, pos: tuple[int, int], viewport: pygame.Rect, state) -> None:
        drag = self.item_drag
        self.item_drag = None
        if drag is None:
            return
        source = drag["source"]
        if not drag.get("active"):
            if source[0] == "player" and state.use_inventory_slot(int(source[1])):
                state.play_sound("menu_select")
            return
        target = self._item_source_at(pos, viewport, state)
        if target is None or target == source:
            return
        if self._drop_dragged_item(source, target, state):
            state.play_sound("menu_select")

    def _drop_dragged_item(self, source: tuple[str, int], target: tuple[str, int], state) -> bool:
        source_kind, source_index = source
        target_kind, target_index = target
        if source_kind == "player" and target_kind == "troop":
            return state.move_player_item_to_selected_troop(source_index, target_index)
        if source_kind == "player" and target_kind == "equipment":
            return state.equip_player_item_to_selected_troop(source_index, target_index)
        if source_kind == "troop" and target_kind == "player":
            return state.move_selected_troop_item_to_player(source_index)
        if source_kind == "troop" and target_kind == "equipment":
            return state.equip_selected_troop_item(source_index, target_index)
        if source_kind == "equipment" and target_kind == "troop":
            return state.unequip_selected_troop_item(source_index, target_index)
        return False

    def _item_context_options(self, state) -> list[tuple[str, str]]:
        if self.item_context_menu is None:
            return []
        source = self.item_context_menu["source"]
        slot = self._slot_for_source(source, state)
        if slot is None:
            return []
        definition = ITEM_DEFINITIONS.get(slot.item_id)
        if definition is None:
            return []
        kind, _index = source
        options: list[tuple[str, str]] = []
        troop = self._selected_troop_for_items(state)
        if kind == "player":
            if definition.type == "scroll":
                options.append(("USE", "use_player"))
            elif troop is not None:
                options.append(("GIVE", "give_player"))
                if definition.type == "consumable":
                    options.append(("CONSUME", "consume_player"))
                elif definition.type == "equipment":
                    options.append(("EQUIP", "equip_player"))
        elif kind == "troop":
            if definition.type == "consumable":
                options.append(("CONSUME", "consume_troop"))
            elif definition.type == "equipment":
                options.append(("EQUIP", "equip_troop"))
            options.append(("STORE", "store_troop"))
        elif kind == "equipment":
            options.append(("UNEQUIP", "unequip"))
        return options

    def _item_context_rect(self, state) -> pygame.Rect | None:
        if self.item_context_menu is None:
            return None
        options = self._item_context_options(state)
        if not options:
            return None
        x, y = self.item_context_menu["pos"]
        return pygame.Rect(x, y, 108, 8 + len(options) * 25)

    def _item_context_option_at(self, pos: tuple[int, int], state) -> tuple[str, str] | None:
        rect = self._item_context_rect(state)
        if rect is None or not rect.collidepoint(pos):
            return None
        options = self._item_context_options(state)
        index = (pos[1] - rect.top - 4) // 25
        if 0 <= index < len(options):
            return options[index]
        return None

    def _handle_item_context_click(self, pos: tuple[int, int], state) -> bool:
        option = self._item_context_option_at(pos, state)
        if option is None:
            return False
        source = self.item_context_menu["source"]
        self.item_context_menu = None
        if self._execute_item_context(source, option[1], state):
            state.play_sound("menu_select")
        return True

    def _execute_item_context(self, source: tuple[str, int], action: str, state) -> bool:
        kind, index = source
        if action == "use_player":
            return state.use_inventory_slot(index)
        if action == "give_player":
            return state.move_player_item_to_selected_troop(index)
        if action == "consume_player":
            return state.consume_player_item_on_selected_troop(index)
        if action == "equip_player":
            return state.equip_player_item_to_selected_troop(index)
        if action == "consume_troop":
            return state.consume_selected_troop_item(index)
        if action == "equip_troop":
            return state.equip_selected_troop_item(index)
        if action == "store_troop":
            return state.move_selected_troop_item_to_player(index)
        if action == "unequip" and kind == "equipment":
            return state.unequip_selected_troop_item(index)
        return False

    def _handle_scroll(self, event: pygame.event.Event, state, screen_rect: pygame.Rect, viewport: pygame.Rect) -> bool:
        mouse = pygame.mouse.get_pos()
        if self.active_panel:
            panel_rect = self._active_panel_rect(screen_rect, viewport)
            content_rect = self._dialog_content_rect(panel_rect)
            if panel_rect.collidepoint(mouse):
                if self.active_panel == "hero" and self._hero_canvas_rect(panel_rect).collidepoint(mouse):
                    self._zoom_hero_tree(mouse, event.y, panel_rect)
                    return True
                if content_rect.collidepoint(mouse) and self.dialog_scroll_max > 0:
                    self.dialog_scroll = max(0.0, min(self.dialog_scroll_max, self.dialog_scroll - event.y * 48))
                return True
        return self._context_rect(screen_rect, viewport).collidepoint(mouse)

    def _execute(self, button: Button, state) -> None:
        if button.command == "tool":
            panel = str(button.value)
            if panel == "units":
                self.units_panel_barracks = None
            elif panel == "research":
                self.research_panel_lab = None
            elif panel == "hero":
                self._reset_hero_view_if_context_changed(state, force=True)
            elif panel == "expedition":
                self.expedition_drag_index = None
            if panel == "expedition_metrics":
                self.expedition_metric_dropdown = None
                self.active_panel = None if self.active_panel == panel else panel
            elif self._using_external_windows():
                self.toggle_panel_window(panel)
                self.active_panel = panel if self.panel_windows[panel].visible else None
            else:
                self.active_panel = None if self.active_panel == panel else panel
            self.dialog_scroll = 0.0
            self.dialog_scroll_max = 0.0
        elif button.command == "close_panel":
            if self.active_panel in self.panel_windows:
                self.panel_windows[self.active_panel].close()
            self.active_panel = None
            self.dialog_scroll = 0.0
        elif button.command == "open_context_panel":
            panel = str(button.value)
            if panel == "units":
                self.units_panel_barracks = state.selected_barracks
            elif panel == "research":
                self.research_panel_lab = state.selected_research
            elif panel == "hero":
                self._reset_hero_view_if_context_changed(state, force=True)
            elif panel == "expedition":
                self.expedition_drag_index = None
            if self._using_external_windows():
                self.open_panel_window(panel)
            self.active_panel = panel
            self.dialog_scroll = 0.0
            self.dialog_scroll_max = 0.0
        elif button.command == "build":
            state.set_build_mode(str(button.value))
        elif button.command == "speed":
            state.time_scale = float(button.value)
            state.paused = False
        elif button.command == "pause":
            state.paused = not state.paused
        elif button.command == "start":
            state.start_night()
        elif button.command == "sell":
            state.sell_selected()
        elif button.command == "train":
            state.train_at_barracks(self._training_barracks(state), str(button.value))
        elif button.command == "research":
            state.start_research_at(self._research_lab_for_panel(state), str(button.value))
        elif button.command == "toggle_auto_research":
            state.toggle_auto_research(str(button.value))
        elif button.command == "library_scroll":
            state.start_library_scroll_selected()
        elif button.command == "tower_mod":
            state.install_mod_selected(str(button.value))
        elif button.command == "level_up_tower":
            state.level_up_selected_tower()
        elif button.command == "level_up_troop":
            state.level_up_selected_troop()
        elif button.command == "troop_attribute":
            state.allocate_selected_troop_attribute(str(button.value))
        elif button.command == "hero_node":
            state.purchase_hero_node_for_selected(str(button.value))
        elif button.command == "focus_level_tower":
            if state.select_tower_for_upgrade(button.value):
                if self._using_external_windows():
                    self.open_panel_window("inspector")
        elif button.command == "focus_level_troop":
            if state.select_troop_for_upgrade(button.value):
                if self._using_external_windows():
                    self.open_panel_window("inspector")
        elif button.command == "level_up_all":
            state.level_up_all_ready_towers()
        elif button.command == "station":
            state.begin_station_selected()
        elif button.command == "toggle_attack":
            state.toggle_selected_troop_engagement()
        elif button.command == "inspect_expedition_troop":
            troop = button.value
            if getattr(troop, "alive", False):
                state.select_troop(troop)
        elif button.command == "specialize":
            state.specialize_selected(str(button.value))
        elif button.command == "use_item":
            state.use_inventory_slot(int(button.value))
        elif button.command == "restart":
            state.reset()
        elif button.command == "round_event":
            state.choose_round_event(str(button.value))
        elif button.command == "expedition_register":
            state.register_expedition_control_group(int(button.value))
        elif button.command == "expedition_cancel":
            state.cancel_expedition_setup()
        elif button.command == "expedition_start":
            if state.start_expedition_from_setup():
                self.active_panel = None
        elif button.command == "expedition_accept_recap":
            state.accept_expedition_recap()
        elif button.command == "expedition_metric_dropdown":
            index = int(button.value)
            self.expedition_metric_dropdown = None if self.expedition_metric_dropdown == index else index
        elif button.command == "expedition_metric_select":
            slot, metric_id = button.value
            if 0 <= int(slot) < len(self.expedition_metric_slots) and str(metric_id) in EXPEDITION_METRIC_LABELS:
                self.expedition_metric_slots[int(slot)] = str(metric_id)
            self.expedition_metric_dropdown = None

    def draw(self, surface: pygame.Surface, screen_rect: pygame.Rect, viewport: pygame.Rect, state, camera=None) -> None:
        self.render_mouse_pos = pygame.mouse.get_pos()
        self.tooltip_request = None
        self.layout_buttons(screen_rect, viewport, state)
        self._draw_workspace_shell(surface, screen_rect, viewport, state)
        expedition_active = getattr(state, "expedition_run", None) is not None

        if self.active_panel and not state.round_events.awaiting_choice and (not self._using_external_windows() or self.active_panel == "expedition_metrics"):
            self._draw_active_panel(surface, screen_rect, viewport, state, camera)

        if not state.round_events.awaiting_choice:
            if not expedition_active:
                if not self._using_external_windows():
                    self._draw_context_inspector(surface, screen_rect, viewport, state)
                self._draw_control_groups(surface, viewport, state)
            self._draw_inventory(surface, viewport, state)
            self._draw_troop_inventory(surface, viewport, state)
            if not expedition_active:
                self._draw_loot_banner(surface, viewport, state)

        if state.round_events.awaiting_choice:
            self._draw_round_event_modal(surface, viewport, state)

        if getattr(state, "expedition_recap", None) is not None:
            self._draw_expedition_recap(surface, viewport, state)

        for button in self.buttons:
            button.draw(surface, self.fonts["small"], self._mouse_pos())

        self._draw_toolbar_labels(surface, state)
        tutorial = getattr(state, "tutorial", None)
        if tutorial is not None:
            tutorial.draw_toolbar_hint(surface, self.buttons, self.fonts)

        if state.notice_timer > 0 and state.notice:
            image = self.fonts["large"].render(state.notice, True, config.PALETTE.white)
            image.set_alpha(int(255 * min(1.0, state.notice_timer)))
            surface.blit(image, image.get_rect(center=(viewport.centerx, config.TOP_BAR_HEIGHT + 40)))

        if not state.round_events.awaiting_choice:
            if not expedition_active:
                self._draw_control_group_tooltip(surface, viewport, state, pygame.mouse.get_pos())
            self._draw_item_tooltip(surface, viewport, state, pygame.mouse.get_pos())
            self._draw_pending_tooltip(surface, surface.get_rect())
            self._draw_item_context_menu(surface, state, surface.get_rect())
            self._draw_item_drag(surface, state)
        elif not state.round_events.awaiting_choice and self.active_panel == "inspector":
            self._draw_pending_tooltip(surface, surface.get_rect())

        if state.game_over:
            self._draw_game_over(surface, viewport)

        if self._using_external_windows():
            self._draw_external_windows(state, camera, viewport)

    def handle_external_event(self, event: pygame.event.Event, state) -> bool:
        if not self._using_external_windows():
            return False
        for panel, window in self.panel_windows.items():
            if not window.visible or not window.matches_event(event):
                continue
            if event.type in (getattr(pygame, "WINDOWCLOSE", -1), getattr(pygame, "WINDOWHIDDEN", -2)):
                if panel != "inspector":
                    window.close()
                return True
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                return window.handle_mouse_up()
            if event.type == pygame.MOUSEMOTION:
                return window.handle_mouse_motion(event.rel)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                pos = event.pos
                title_action = window.handle_title_mouse_down(pos)
                if title_action is not None:
                    if panel == "inspector" and title_action == "close":
                        self.inspector_enabled = False
                    return True
                for button in self.window_buttons.get(panel, []):
                    if button.contains(pos):
                        state.play_sound("menu_select")
                        self._execute(button, state)
                        return True
                return True
        return False

    def _draw_external_windows(self, state, camera, viewport: pygame.Rect) -> None:
        if not self._using_external_windows():
            self.close_all_windows()
            return
        PanelWindow.pump_events()
        if self.inspector_enabled and not self.panel_windows["inspector"].visible:
            self.panel_windows["inspector"].show()
        for panel, window in self.panel_windows.items():
            if not window.visible:
                self.window_buttons[panel] = []
                if panel == "map":
                    self.minimap.release_input()
                    if self.active_panel == "map":
                        self.active_panel = None
                continue
            self._handle_panel_window_events(panel, window, state, camera, viewport)
            surface = window.surface()
            if surface is None:
                self.window_buttons[panel] = []
                continue
            old_scroll = self.dialog_scroll
            old_scroll_max = self.dialog_scroll_max
            old_mouse_pos = self.render_mouse_pos
            self.render_mouse_pos = window.mouse_pos
            self.tooltip_request = None
            self.dialog_scroll = self.window_scrolls.get(panel, 0.0)
            surface.fill(config.PALETTE.bg)
            self._draw_panel_chrome(surface, window)
            content_rect = pygame.Rect(0, config.TITLE_BAR_HEIGHT, surface.get_width(), surface.get_height() - config.TITLE_BAR_HEIGHT)
            if panel == "build":
                self._draw_build_panel(surface, content_rect, state)
                buttons = self._layout_build_buttons(content_rect, state)
            elif panel == "units":
                self._draw_units_panel(surface, content_rect, state)
                buttons = self._layout_unit_buttons(content_rect, state)
            elif panel == "research":
                self._draw_research_panel(surface, content_rect, state)
                buttons = self._layout_research_buttons(content_rect, state)
            elif panel == "items":
                self._draw_items_panel(surface, content_rect, state)
                buttons = self._layout_item_buttons(content_rect, state)
            elif panel == "level":
                self._draw_level_panel(surface, content_rect, state)
                buttons = self._layout_level_buttons(content_rect, state)
            elif panel == "hero":
                self._draw_hero_panel(surface, content_rect, state)
                buttons = self._layout_hero_buttons(content_rect, state)
            elif panel == "expedition":
                self._draw_expedition_panel(surface, content_rect, state)
                buttons = self._layout_expedition_buttons(content_rect, state)
            elif panel == "map":
                self._draw_map_panel(surface, content_rect, state, camera, viewport)
                buttons = []
            elif panel == "inspector":
                self._draw_context_inspector_panel(surface, content_rect, state)
                buttons = self._layout_context_buttons_for_rect(content_rect, state)
            else:
                buttons = []
            self.window_buttons[panel] = buttons
            for button in buttons:
                button.draw(surface, self.fonts["small"], self._mouse_pos())
            if self.tooltip_request is not None:
                self._draw_fixed_tooltip(surface, content_rect)
            self.window_scrolls[panel] = self.dialog_scroll
            self.dialog_scroll = old_scroll
            self.dialog_scroll_max = old_scroll_max
            self.render_mouse_pos = old_mouse_pos
            window.flip()
        PanelWindow.pump_events()

    def _handle_panel_window_events(self, panel: str, window: PanelWindow, state, camera, viewport: pygame.Rect) -> None:
        for event in window.pop_events():
            if event.kind == "focus_out":
                if panel == "map":
                    self.minimap.release_input()
                continue
            if event.kind == "key":
                if panel == "map":
                    self.minimap.set_key(event.key, event.pressed)
                continue
            if event.kind == "motion":
                window.handle_mouse_motion(event.rel)
                if panel == "hero" and self.hero_tree_dragging:
                    self._drag_hero_tree(event.pos)
                if panel == "map":
                    surface = window.surface()
                    if event.pos == (-1, -1):
                        self.minimap.handle_mouse_up()
                    elif surface is not None:
                        panel_rect = pygame.Rect(0, config.TITLE_BAR_HEIGHT, surface.get_width(), surface.get_height() - config.TITLE_BAR_HEIGHT)
                        self.minimap.handle_mouse_motion(event.pos, self._dialog_content_rect(panel_rect), state)
                self._update_panel_window_hover(panel, event.pos, state)
                continue
            if event.kind == "up" and event.button == 1:
                window.handle_mouse_up()
                if panel == "hero":
                    self.hero_tree_dragging = False
                if panel == "map":
                    self.minimap.handle_mouse_up()
                if panel == "expedition" and self.expedition_drag_index is not None:
                    surface = window.surface()
                    if surface is not None:
                        panel_rect = pygame.Rect(0, config.TITLE_BAR_HEIGHT, surface.get_width(), surface.get_height() - config.TITLE_BAR_HEIGHT)
                        target = self._expedition_orb_at(event.pos, panel_rect, state)
                        if target is not None and state.reorder_expedition_party(self.expedition_drag_index, target):
                            state.play_sound("menu_select")
                    self.expedition_drag_index = None
                continue
            if event.kind == "wheel":
                self._scroll_panel_window(panel, window, event.pos, event.wheel_y, state)
                continue
            if event.kind != "down" or event.button != 1:
                continue
            pos = event.pos
            title_action = window.handle_title_mouse_down(pos)
            if title_action is not None:
                state.play_sound("menu_select")
                if panel == "inspector" and title_action == "close":
                    self.inspector_enabled = False
                if panel == "map" and title_action == "close":
                    self.minimap.release_input()
                if title_action == "close" and self.active_panel == panel:
                    self.active_panel = None
                continue
            for button in self.window_buttons.get(panel, []):
                if button.contains(pos):
                    state.play_sound("menu_select")
                    self._execute(button, state)
                    break
            else:
                if panel == "hero":
                    surface = window.surface()
                    if surface is not None:
                        panel_rect = pygame.Rect(0, config.TITLE_BAR_HEIGHT, surface.get_width(), surface.get_height() - config.TITLE_BAR_HEIGHT)
                        if self._hero_canvas_rect(panel_rect).collidepoint(pos):
                            self._start_hero_drag(pos)
                elif panel == "expedition":
                    surface = window.surface()
                    if surface is not None:
                        panel_rect = pygame.Rect(0, config.TITLE_BAR_HEIGHT, surface.get_width(), surface.get_height() - config.TITLE_BAR_HEIGHT)
                        orb_index = self._expedition_orb_at(pos, panel_rect, state)
                        if orb_index is not None:
                            self.expedition_drag_index = orb_index
                elif panel == "map":
                    surface = window.surface()
                    if surface is not None:
                        panel_rect = pygame.Rect(0, config.TITLE_BAR_HEIGHT, surface.get_width(), surface.get_height() - config.TITLE_BAR_HEIGHT)
                        if self.minimap.handle_mouse_down(pos, self._dialog_content_rect(panel_rect), state):
                            state.play_sound("menu_select")

    def _update_panel_window_hover(self, panel: str, pos: tuple[int, int], state) -> None:
        window = self.panel_windows.get(panel)
        target = None
        if window is not None:
            for name, rect in window.control_rects().items():
                if rect.collidepoint(pos):
                    target = (panel, "control", name)
                    break
        if target is None:
            for button in self.window_buttons.get(panel, []):
                if button.contains(pos):
                    target = (panel, button.command, button.value)
                    break
        if target != self.window_hover_targets.get(panel):
            if target is not None:
                state.play_sound("menu_hover")
            hover_feedback.set_hover_target(target)
            self.window_hover_targets[panel] = target

    def _scroll_panel_window(self, panel: str, window: PanelWindow, pos: tuple[int, int], wheel_y: int, state) -> bool:
        surface = window.surface()
        if surface is None:
            return False
        panel_rect = pygame.Rect(0, config.TITLE_BAR_HEIGHT, surface.get_width(), surface.get_height() - config.TITLE_BAR_HEIGHT)
        content = self._dialog_content_rect(panel_rect)
        if not content.collidepoint(pos):
            return False
        if panel == "hero":
            self._zoom_hero_tree(pos, wheel_y, panel_rect)
            return True
        max_scroll = max(0.0, float(self._external_panel_content_height(panel, content, state) - content.height))
        if max_scroll <= 0:
            return False
        current = self.window_scrolls.get(panel, 0.0)
        self.window_scrolls[panel] = max(0.0, min(max_scroll, current - wheel_y * 48))
        return True

    def _external_panel_content_height(self, panel: str, content: pygame.Rect, state) -> int:
        if panel == "build":
            return self._build_content_height(content)
        if panel == "research":
            return self._research_content_height(content)
        if panel == "units":
            return self._unit_content_height(content)
        if panel == "items":
            slots = self._panel_inventory_rects(content, state)
            if not slots:
                return 0
            return slots[-1].bottom - content.top + 44 + max(1, len(state.active_item_buffs)) * 34
        if panel == "level":
            return self._level_content_height(content, state) + 42
        if panel == "hero":
            return content.height
        if panel == "expedition":
            return content.height
        if panel == "map":
            return content.height
        return content.height

    def _draw_panel_chrome(self, surface: pygame.Surface, window: PanelWindow) -> None:
        palette = config.PALETTE
        title_bar = pygame.Rect(0, 0, surface.get_width(), config.TITLE_BAR_HEIGHT)
        pygame.draw.rect(surface, palette.black, title_bar)
        pygame.draw.line(surface, palette.line_bright, title_bar.bottomleft, title_bar.bottomright)
        title = self.fonts["small"].render(window.title.upper(), True, palette.text)
        surface.blit(title, (12, 7))
        for name, rect in window.control_rects().items():
            hover = rect.collidepoint(self._mouse_pos())
            draw_rect = hover_feedback.scaled_rect(rect, hover)
            fill = palette.white if hover else palette.black
            mark = palette.black if hover else palette.text
            pygame.draw.rect(surface, fill, draw_rect)
            pygame.draw.rect(surface, palette.white if hover else palette.line_bright, draw_rect, 1)
            if name == "minimize":
                pygame.draw.line(surface, mark, (draw_rect.left + 5, draw_rect.centery + 4), (draw_rect.right - 5, draw_rect.centery + 4), 1)
            elif name == "maximize":
                pygame.draw.rect(surface, mark, draw_rect.inflate(-8, -8), 1)
            else:
                pygame.draw.line(surface, mark, (draw_rect.left + 6, draw_rect.top + 6), (draw_rect.right - 6, draw_rect.bottom - 6), 1)
                pygame.draw.line(surface, mark, (draw_rect.right - 6, draw_rect.top + 6), (draw_rect.left + 6, draw_rect.bottom - 6), 1)
        grip_right = surface.get_width() - 7
        grip_bottom = surface.get_height() - 7
        for offset in (0, 5, 10):
            pygame.draw.line(
                surface,
                palette.line_bright,
                (grip_right - offset, grip_bottom),
                (grip_right, grip_bottom - offset),
                1,
            )

    def _draw_workspace_shell(self, surface: pygame.Surface, screen_rect: pygame.Rect, viewport: pygame.Rect, state) -> None:
        palette = config.PALETTE
        title_bar = pygame.Rect(0, 0, screen_rect.width, config.TITLE_BAR_HEIGHT)
        status_bar = pygame.Rect(0, config.TITLE_BAR_HEIGHT, screen_rect.width, config.TOP_BAR_HEIGHT - config.TITLE_BAR_HEIGHT)
        tool_bar = pygame.Rect(0, config.TOP_BAR_HEIGHT, config.TOOLBAR_WIDTH, screen_rect.height - config.TOP_BAR_HEIGHT)

        pygame.draw.rect(surface, palette.black, title_bar)
        pygame.draw.rect(surface, palette.panel, status_bar)
        pygame.draw.rect(surface, palette.black, tool_bar)
        pygame.draw.line(surface, palette.line_bright, title_bar.bottomleft, title_bar.bottomright)
        pygame.draw.line(surface, palette.line_bright, status_bar.bottomleft, status_bar.bottomright)
        pygame.draw.line(surface, palette.line_bright, tool_bar.topright, tool_bar.bottomright)

        self._draw_window_title(surface, title_bar)
        self._draw_top_status(surface, status_bar, viewport, state)

        for y in range(tool_bar.top + 6, tool_bar.bottom, 24):
            pygame.draw.line(surface, palette.dark, (tool_bar.right - 6, y), (tool_bar.right - 3, y + 8), 1)

    def _draw_window_title(self, surface: pygame.Surface, title_bar: pygame.Rect) -> None:
        palette = config.PALETTE
        title = self.fonts["small"].render("BASTION CORE  //  TACTICAL WORKSPACE", True, palette.text)
        surface.blit(title, (14, 7))

        controls = self._window_control_rects(title_bar)
        for name, rect in controls.items():
            hover = rect.collidepoint(self._mouse_pos())
            draw_rect = hover_feedback.scaled_rect(rect, hover)
            fill = palette.white if hover else palette.black
            mark = palette.black if hover else palette.text
            pygame.draw.rect(surface, fill, draw_rect)
            pygame.draw.rect(surface, palette.white if hover else palette.line_bright, draw_rect, 1)
            if name == "minimize":
                pygame.draw.line(surface, mark, (draw_rect.left + 5, draw_rect.centery + 4), (draw_rect.right - 5, draw_rect.centery + 4), 1)
            elif name == "maximize":
                pygame.draw.rect(surface, mark, draw_rect.inflate(-8, -8), 1)
            else:
                pygame.draw.line(surface, mark, (draw_rect.left + 6, draw_rect.top + 6), (draw_rect.right - 6, draw_rect.bottom - 6), 1)
                pygame.draw.line(surface, mark, (draw_rect.right - 6, draw_rect.top + 6), (draw_rect.left + 6, draw_rect.bottom - 6), 1)

    def _window_control_rects(self, title_bar: pygame.Rect) -> dict[str, pygame.Rect]:
        top = title_bar.top + 5
        return {
            "minimize": pygame.Rect(title_bar.right - 86, top, 22, 20),
            "maximize": pygame.Rect(title_bar.right - 58, top, 22, 20),
            "close": pygame.Rect(title_bar.right - 30, top, 22, 20),
        }

    def _draw_top_status(self, surface: pygame.Surface, status_bar: pygame.Rect, viewport: pygame.Rect, state) -> None:
        palette = config.PALETTE
        core_hp = int(sum(max(0, core.health) for core in state.core_targets))
        core_max = int(sum(core.max_health for core in state.core_targets))
        arcane_used, arcane_capacity = state.arcane_usage()
        active_enemies = sum(1 for enemy in state.enemies if enemy.alive and getattr(enemy, "spawn_group", "wave") == "wave")
        day_remaining = max(0, int(math.ceil(state.wave_manager.intermission)))
        cycle_text = f"NIGHT {state.wave_manager.night_number}" if state.wave_manager.active else f"DAY {day_remaining // 60}:{day_remaining % 60:02d}"
        queued = state.queued_troop_count()
        status_items = [
            ("Gold", str(state.gold)),
            ("Minerals", str(state.minerals)),
            ("Arcane", f"{arcane_used}/{arcane_capacity}"),
            ("Core", f"{core_hp}/{core_max}"),
            ("Cycle", cycle_text),
            ("Night Foes", str(active_enemies)),
            ("Supply", f"{state.troop_supply_committed()}/{state.troop_capacity()}"),
        ]
        if queued:
            status_items.append(("Queue", str(queued)))

        x = config.TOOLBAR_WIDTH + 14
        y = status_bar.top + 8
        for label, value in status_items:
            width = max(92, min(148, self.fonts["small"].size(label + value)[0] + 34))
            rect = pygame.Rect(x, y, width, status_bar.height - 16)
            pygame.draw.rect(surface, palette.black, rect)
            pygame.draw.rect(surface, palette.line, rect, 1)
            label_img = self.fonts["tiny"].render(label.upper(), True, palette.text_dim)
            value_img = self.fonts["small"].render(value, True, palette.text)
            surface.blit(label_img, (rect.left + 10, rect.top + 6))
            surface.blit(value_img, (rect.left + 10, rect.top + 23))
            x += rect.width + 8
            if x > viewport.right - 390:
                break

        transport = self.fonts["tiny"].render("TRANSPORT", True, palette.text_dim)
        surface.blit(transport, (max(config.TOOLBAR_WIDTH + 500, status_bar.right - 344), status_bar.top + 2))

    def _draw_toolbar_labels(self, surface: pygame.Surface, state) -> None:
        palette = config.PALETTE
        y = config.TOP_BAR_HEIGHT + 51
        for _panel, _glyph, name in self._toolbar_entries(state):
            label = self.fonts["tiny"].render(name.upper()[:8], True, palette.text_dim)
            surface.blit(label, label.get_rect(center=(config.TOOLBAR_WIDTH // 2, y)))
            y += 48

    def _draw_active_panel(self, surface: pygame.Surface, screen_rect: pygame.Rect, viewport: pygame.Rect, state, camera=None) -> None:
        rect = self._active_panel_rect(screen_rect, viewport)
        if self.active_panel == "build":
            self._draw_build_panel(surface, rect, state)
        elif self.active_panel == "research":
            self._draw_research_panel(surface, rect, state)
        elif self.active_panel == "units":
            self._draw_units_panel(surface, rect, state)
        elif self.active_panel == "items":
            self._draw_items_panel(surface, rect, state)
        elif self.active_panel == "level":
            self._draw_level_panel(surface, rect, state)
        elif self.active_panel == "hero":
            self._draw_hero_panel(surface, rect, state)
        elif self.active_panel == "expedition":
            self._draw_expedition_panel(surface, rect, state)
        elif self.active_panel == "map":
            self._draw_map_panel(surface, rect, state, camera, viewport)
        elif self.active_panel == "expedition_metrics":
            self._draw_expedition_metrics_panel(surface, rect, state)
        elif self.active_panel == "inspector" and getattr(state, "expedition_run", None) is not None:
            self._draw_context_inspector_panel(surface, rect, state)

    def _active_panel_rect(self, screen_rect: pygame.Rect, viewport: pygame.Rect) -> pygame.Rect:
        if self.active_panel is None:
            return pygame.Rect(0, 0, 0, 0)
        return self._panel_rect_for(self.active_panel, screen_rect, viewport)

    def _panel_rect_for(self, panel: str, screen_rect: pygame.Rect, viewport: pygame.Rect) -> pygame.Rect:
        if panel == "inspector":
            rect = self._context_rect(screen_rect, viewport)
            if panel in self.dialog_positions:
                rect.topleft = self.dialog_positions[panel]
            rect = self._clamp_panel_rect(rect, screen_rect, viewport)
            self.dialog_positions[panel] = rect.topleft
            return rect
        if panel == "research":
            width = min(780, max(520, viewport.width - 390))
            height = min(620, viewport.height - 34)
        elif panel == "units":
            width = min(700, max(500, viewport.width - 390))
            height = min(560, viewport.height - 34)
        elif panel == "items":
            width = min(640, max(480, viewport.width - 420))
            height = min(520, viewport.height - 34)
        elif panel == "level":
            width = min(560, max(460, viewport.width - 430))
            height = min(560, viewport.height - 34)
        elif panel == "hero":
            width = min(840, max(560, viewport.width - 390))
            height = min(640, viewport.height - 34)
        elif panel == "expedition":
            width = min(720, max(520, viewport.width - 390))
            height = min(620, viewport.height - 34)
        elif panel == "map":
            width = min(620, max(460, viewport.width - 430))
            height = min(470, viewport.height - 34)
        elif panel == "expedition_metrics":
            width = min(560, max(460, viewport.width - 520))
            height = min(650, viewport.height - 34)
        else:
            width = min(720, max(500, viewport.width - 390))
            height = min(560, viewport.height - 34)
        width = min(width, max(360, screen_rect.width - config.TOOLBAR_WIDTH - 44))
        height = min(height, max(280, screen_rect.height - config.TOP_BAR_HEIGHT - 28))
        rect = pygame.Rect(config.TOOLBAR_WIDTH + 18, config.TOP_BAR_HEIGHT + 16, width, height)
        if panel in self.dialog_positions:
            rect.topleft = self.dialog_positions[panel]
        rect = self._clamp_panel_rect(rect, screen_rect, viewport)
        self.dialog_positions[panel] = rect.topleft
        return rect

    def _clamp_panel_rect(self, rect: pygame.Rect, screen_rect: pygame.Rect, viewport: pygame.Rect) -> pygame.Rect:
        bounds = pygame.Rect(config.TOOLBAR_WIDTH + 8, config.TOP_BAR_HEIGHT + 8, screen_rect.width - config.TOOLBAR_WIDTH - 16, screen_rect.height - config.TOP_BAR_HEIGHT - 16)
        if rect.width > bounds.width:
            rect.width = bounds.width
        if rect.height > bounds.height:
            rect.height = bounds.height
        rect.clamp_ip(bounds)
        return rect

    def _dialog_title_rect(self, panel_rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel_rect.left, panel_rect.top, panel_rect.width, 42)

    def _dialog_content_rect(self, panel_rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel_rect.left + 16, panel_rect.top + 58, panel_rect.width - 32, panel_rect.height - 74)

    def _draw_dialog_shell(self, surface: pygame.Surface, rect: pygame.Rect, title: str, subtitle: str) -> None:
        palette = config.PALETTE
        self._alpha_rect(surface, rect, (0, 0, 0, 232))
        pygame.draw.rect(surface, palette.panel_2, pygame.Rect(rect.left, rect.top, rect.width, 42))
        pygame.draw.rect(surface, palette.line_bright, rect, 1)
        pygame.draw.line(surface, palette.line, (rect.left, rect.top + 42), (rect.right, rect.top + 42), 1)
        surface.blit(self.fonts["medium"].render(title.upper(), True, palette.text), (rect.left + 16, rect.top + 10))
        surface.blit(self.fonts["tiny"].render(subtitle.upper(), True, palette.text_dim), (rect.left + 16, rect.top + 34))
        for index in range(5):
            dot_x = rect.centerx - 18 + index * 9
            pygame.draw.line(surface, palette.line_bright, (dot_x, rect.top + 11), (dot_x + 3, rect.top + 11), 1)

    def _draw_map_panel(self, surface: pygame.Surface, rect: pygame.Rect, state, camera, viewport: pygame.Rect) -> None:
        self._draw_dialog_shell(surface, rect, "Map", "Fog Recon")
        content = self._dialog_content_rect(rect)
        pygame.draw.rect(surface, config.PALETTE.black, content)
        self.minimap.draw(surface, content, state, camera, viewport, self._mouse_pos())

    def _draw_build_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        self._draw_dialog_shell(surface, rect, "Build Palette", "Grouped construction tools for compact placement.")
        content = self._dialog_content_rect(rect)
        self._clamp_dialog_scroll(self._build_content_height(content), content.height)

        previous_clip = surface.get_clip()
        surface.set_clip(content)
        for kind, payload in self._build_draw_items(content):
            if kind == "category":
                category, header = payload
                if header.colliderect(content):
                    title = self.fonts["small"].render(category.label.upper(), True, palette.white)
                    surface.blit(title, (header.left, header.top + 5))
                    pygame.draw.line(surface, palette.line_bright, (header.left + 132, header.centery), (content.right - 8, header.centery), 1)
            else:
                entry, card = payload
                if card.colliderect(content):
                    self._draw_build_card(surface, card, entry, state)
        surface.set_clip(previous_clip)
        self._draw_scrollbar(surface, content, self.dialog_scroll, self.dialog_scroll_max)

    def _layout_build_buttons(self, panel_rect: pygame.Rect, state) -> list[Button]:
        content = self._dialog_content_rect(panel_rect)
        self._clamp_dialog_scroll(self._build_content_height(content), content.height)
        buttons: list[Button] = []
        for kind, payload in self._build_draw_items(content):
            if kind != "build":
                continue
            entry, rect = payload
            click_rect = rect.clip(content)
            if click_rect.width <= 0 or click_rect.height <= 0:
                continue
            enabled = entry.can_afford(state.gold, state.minerals) and not state.game_over
            buttons.append(Button(click_rect, "", "build", entry.mode, enabled=enabled, selected=state.build_mode == entry.mode, visible=False))
        return buttons

    def _draw_build_card(self, surface: pygame.Surface, card: pygame.Rect, entry: BuildMenuEntry, state) -> None:
        palette = config.PALETTE
        affordable = entry.can_afford(state.gold, state.minerals) and not state.game_over
        selected = state.build_mode == entry.mode
        hovered = card.collidepoint(self._mouse_pos())
        interactive_hover = hovered and affordable
        draw_card = hover_feedback.scaled_rect(card, hovered)
        fill = palette.white if interactive_hover else (palette.panel_2 if selected else palette.black)
        border = palette.white if hovered or selected else (palette.line_bright if affordable else palette.line)
        text_color = palette.black if interactive_hover else (palette.text if affordable else palette.text_dim)
        dim_color = palette.black if interactive_hover else palette.text_dim
        mark_color = palette.black if interactive_hover else (palette.white if affordable else palette.text_dim)

        pygame.draw.rect(surface, fill, draw_card)
        pygame.draw.rect(surface, border, draw_card, 1)

        icon_rect = pygame.Rect(draw_card.left + 10, draw_card.top + 10, 44, 44)
        self._draw_build_icon(surface, entry.mode, icon_rect, inverted=interactive_hover, muted=not affordable)

        cost_image = self.fonts["small"].render(entry.cost_label(), True, text_color)
        cost_rect = cost_image.get_rect(midtop=(icon_rect.centerx, icon_rect.bottom + 6))
        if cost_rect.left < draw_card.left + 6:
            cost_rect.left = draw_card.left + 6
        surface.blit(cost_image, cost_rect)

        if hovered:
            category = BUILD_CATEGORY_BY_ID[entry.category_id]
            self.tooltip_request = TooltipRequest(entry.tooltip_card(category), self._mouse_pos())

        ability_x = draw_card.left + 66
        ability_w = max(32, draw_card.right - ability_x - 10)
        self._draw_build_ability_chips(surface, entry.ability_cards, ability_x, draw_card.top + 11, ability_w)

        if not affordable:
            shortage = self._build_shortage_label(entry, state)
            if shortage:
                short_img = self.fonts["tiny"].render(shortage, True, dim_color)
                surface.blit(short_img, (ability_x, draw_card.bottom - 18))

        if selected:
            self._draw_corner_brackets(surface, draw_card.inflate(-5, -5), mark_color)

    def _draw_build_ability_chips(self, surface: pygame.Surface, cards, x: int, y: int, max_width: int, max_rows: int = 2) -> None:
        cursor_x = x
        cursor_y = y
        row_h = 19
        gap = 5
        rows_used = 1
        drawn = 0
        for card in cards:
            label = str(getattr(card, "name", "Ability")).upper()
            width = min(max_width, max(56, self.fonts["tiny"].size(label)[0] + 18))
            if cursor_x + width > x + max_width and cursor_x > x:
                rows_used += 1
                if rows_used > max_rows:
                    break
                cursor_x = x
                cursor_y += row_h + gap
            rect = pygame.Rect(cursor_x, cursor_y, width, row_h)
            self._draw_ability_card(surface, rect, card, compact=True)
            cursor_x += width + gap
            drawn += 1

        hidden = len(cards) - drawn
        if hidden <= 0 or rows_used > max_rows:
            return
        more = self.fonts["tiny"].render(f"+{hidden}", True, config.PALETTE.text_dim)
        surface.blit(more, (min(cursor_x, x + max_width - more.get_width()), cursor_y + 4))

    def _build_shortage_label(self, entry: BuildMenuEntry, state) -> str:
        missing: list[str] = []
        if state.gold < entry.gold_cost:
            missing.append(f"{entry.gold_cost - state.gold}G")
        if state.minerals < entry.mineral_cost:
            missing.append(f"{entry.mineral_cost - state.minerals}M")
        if state.game_over:
            return "LOCKED"
        return "NEED " + " ".join(missing) if missing else ""

    def _build_draw_items(self, content: pygame.Rect):
        columns, gap, card_w, card_h = self._build_layout_metrics(content)
        y = content.top - int(self.dialog_scroll)
        for category, entries in iter_build_categories():
            header = pygame.Rect(content.left, y, content.width, 30)
            yield "category", (category, header)
            y += 38
            for index, entry in enumerate(entries):
                col = index % columns
                row = index // columns
                rect = pygame.Rect(content.left + col * (card_w + gap), y + row * (card_h + gap), card_w, card_h)
                yield "build", (entry, rect)
            rows = max(1, math.ceil(len(entries) / columns))
            y += rows * (card_h + gap) + 12

    def _build_content_height(self, content: pygame.Rect) -> int:
        _columns, gap, _card_w, card_h = self._build_layout_metrics(content)
        height = 0
        for _category, entries in iter_build_categories():
            rows = max(1, math.ceil(len(entries) / _columns))
            height += 38 + rows * (card_h + gap) + 12
        return height + 4

    def _build_layout_metrics(self, content: pygame.Rect) -> tuple[int, int, int, int]:
        columns = 4 if content.width >= 700 else (3 if content.width >= 540 else 2)
        gap = 10
        card_w = (content.width - 8 - gap * (columns - 1)) // columns
        card_h = 82
        return columns, gap, card_w, card_h

    def _draw_build_icon(self, surface: pygame.Surface, mode: str, rect: pygame.Rect, inverted: bool = False, muted: bool = False) -> None:
        palette = config.PALETTE
        fill = palette.white if inverted else palette.black
        mark = palette.black if inverted else (palette.text_dim if muted else palette.white)
        border = palette.black if inverted else (palette.line if muted else palette.line_bright)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, border, rect, 1)

        center = pygame.Vector2(rect.center)
        size = min(rect.width, rect.height)
        line = max(1, int(size / 18))
        tile = size * 0.78
        body = pygame.Rect(0, 0, int(tile), int(tile))
        body.center = center

        if mode == "wall":
            wall_fill = palette.white if inverted else (palette.text_dim if muted else palette.dark)
            wall_mark = palette.black if inverted else (palette.text_dim if muted else palette.white)
            pygame.draw.rect(surface, wall_fill, body)
            pygame.draw.line(surface, wall_mark, body.topleft, body.topright, line)
            pygame.draw.line(surface, wall_mark, body.topright, body.bottomright, line)
            pygame.draw.line(surface, wall_mark, body.bottomleft, body.bottomright, line)
            pygame.draw.line(surface, wall_mark, body.topleft, body.bottomleft, line)
        elif mode in ("archer", "cannon", "wizard"):
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            if mode == "archer":
                glyph = int(tile * 0.26)
                points = [(center.x, center.y - glyph), (center.x + glyph, center.y + glyph), (center.x - glyph, center.y + glyph)]
                pygame.draw.polygon(surface, mark, points, line)
            elif mode == "cannon":
                pygame.draw.circle(surface, mark, center, max(3, int(tile * 0.18)), line)
                pygame.draw.line(surface, mark, center, (center.x + tile * 0.26, center.y), max(1, line + 1))
            else:
                r = tile * 0.23
                points = [(center.x, center.y - r), (center.x + r, center.y), (center.x, center.y + r), (center.x - r, center.y)]
                pygame.draw.polygon(surface, mark, points, line)
                pygame.draw.line(surface, mark, (center.x - r, center.y), (center.x + r, center.y), line)
        elif mode == "barracks":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            inset = body.inflate(-max(4, int(tile * 0.32)), -max(4, int(tile * 0.32)))
            pygame.draw.rect(surface, mark, inset, 1)
        elif mode == "house":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            roof = [
                (body.left + body.width * 0.12, body.centery),
                (body.centerx, body.top + body.height * 0.12),
                (body.right - body.width * 0.12, body.centery),
            ]
            pygame.draw.polygon(surface, mark, roof, line)
            door = pygame.Rect(0, 0, max(3, int(tile * 0.18)), max(5, int(tile * 0.28)))
            door.midbottom = (body.centerx, body.bottom - max(2, int(tile * 0.08)))
            pygame.draw.rect(surface, mark, door, 1)
        elif mode == "extractor":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            inner = body.inflate(-max(5, int(tile * 0.34)), -max(5, int(tile * 0.34)))
            pygame.draw.rect(surface, mark, inner, 1)
            pygame.draw.line(surface, mark, body.midleft, body.midright, 1)
            pygame.draw.line(surface, mark, body.midtop, body.midbottom, 1)
        elif mode == "torch":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            pole_top = pygame.Vector2(body.centerx, body.top + tile * 0.18)
            pole_bottom = pygame.Vector2(body.centerx, body.bottom - tile * 0.16)
            pygame.draw.line(surface, mark, pole_top, pole_bottom, line)
            flame_r = max(3, int(tile * 0.14))
            flame = pygame.Vector2(body.centerx, body.top + tile * 0.24)
            pygame.draw.circle(surface, mark, flame, flame_r, 1)
            pygame.draw.line(surface, mark, flame + pygame.Vector2(-flame_r, flame_r), flame + pygame.Vector2(flame_r, -flame_r), 1)
        elif mode == "training_grounds":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            for offset in (-0.24, 0.24):
                x = body.centerx + tile * offset
                pygame.draw.line(surface, mark, (x, body.top + tile * 0.24), (x, body.bottom - tile * 0.18), 1)
            pygame.draw.line(surface, mark, (body.left + tile * 0.20, body.centery), (body.right - tile * 0.20, body.centery), line)
            pygame.draw.circle(surface, mark, center + pygame.Vector2(tile * 0.20, 0), max(2, int(tile * 0.08)))
        elif mode == "expedition_campsite":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            tent = [
                (body.left + tile * 0.16, body.top + tile * 0.68),
                (body.centerx, body.top + tile * 0.18),
                (body.right - tile * 0.16, body.top + tile * 0.68),
            ]
            pygame.draw.polygon(surface, mark, tent, line)
            for index in range(5):
                angle = -math.pi / 2 + index * math.tau / 5
                p = center + pygame.Vector2(math.cos(angle), math.sin(angle)) * tile * 0.31
                pygame.draw.circle(surface, mark, p, max(1, int(tile * 0.045)), 1)
        elif mode == "hero_hall":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            ring = max(5, int(tile * 0.28))
            pygame.draw.circle(surface, mark, center, ring, 1)
            pygame.draw.line(surface, mark, (body.centerx, body.top + tile * 0.18), (body.centerx, body.bottom - tile * 0.18), 1)
            pygame.draw.line(surface, mark, (body.left + tile * 0.20, body.centery), (body.right - tile * 0.20, body.centery), 1)
            for index in range(3):
                angle = index * math.tau / 3
                p = center + pygame.Vector2(math.cos(angle), math.sin(angle)) * ring
                pygame.draw.circle(surface, mark, p, max(1, int(tile * 0.06)), 1)
        elif mode == "research":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            ring_r = max(4, int(tile * 0.22))
            pygame.draw.circle(surface, mark, center, ring_r, 1)
            pygame.draw.line(surface, mark, (body.left + tile * 0.18, body.bottom - tile * 0.24), (body.right - tile * 0.18, body.top + tile * 0.24), 1)
            pygame.draw.line(surface, mark, (body.left + tile * 0.28, body.top + tile * 0.28), (body.right - tile * 0.28, body.bottom - tile * 0.28), 1)
        elif mode == "library":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            shelf_y = body.top + tile * 0.32
            pygame.draw.line(surface, mark, (body.left + tile * 0.18, shelf_y), (body.right - tile * 0.18, shelf_y), 1)
            pygame.draw.line(surface, mark, (body.left + tile * 0.18, body.bottom - tile * 0.28), (body.right - tile * 0.18, body.bottom - tile * 0.28), 1)
            for index in range(3):
                x = body.left + tile * (0.28 + index * 0.22)
                y = body.top + tile * (0.45 + (index % 2) * 0.15)
                scroll = pygame.Rect(0, 0, max(4, int(tile * 0.13)), max(3, int(tile * 0.22)))
                scroll.center = (x, y)
                pygame.draw.rect(surface, mark, scroll, 1)
                pygame.draw.circle(surface, mark, (scroll.centerx, scroll.top), max(1, int(2 * line)), 1)
        elif mode == "shield_generator":
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)
            ring = max(5, int(tile * 0.29))
            pygame.draw.circle(surface, mark, center, ring, 1)
            for index in range(4):
                angle = index * math.tau / 4
                p = center + pygame.Vector2(math.cos(angle), math.sin(angle)) * ring
                pygame.draw.circle(surface, mark, p, max(1, int(tile * 0.07)))
        elif mode == "core":
            core = pygame.Rect(0, 0, int(size * 0.80), int(size * 0.80))
            core.center = center
            pygame.draw.rect(surface, fill, core)
            pygame.draw.rect(surface, mark, core, line)
            inner = core.inflate(-int(core.width * 0.28), -int(core.height * 0.28))
            pygame.draw.rect(surface, mark, inner, line)
            label = self.fonts["tiny"].render("CORE", True, mark)
            surface.blit(label, label.get_rect(center=core.center))
        else:
            pygame.draw.rect(surface, fill, body)
            pygame.draw.rect(surface, mark, body, line)

    def _draw_expedition_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        self.dialog_scroll = 0.0
        self.dialog_scroll_max = 0.0
        self._draw_dialog_shell(surface, rect, "Expeditions", "Registered control groups enter compact dungeon runs.")
        content = self._dialog_content_rect(rect)
        camps = self._living_expedition_campsites(state)
        left = pygame.Rect(content.left, content.top, min(230, content.width // 2 - 10), content.height)
        right = pygame.Rect(left.right + 14, content.top, content.right - left.right - 14, content.height)

        self._section(surface, "CONTROL GROUPS", left.left, left.top)
        y = left.top + 30
        for index, row in enumerate(self._expedition_group_rects(content, state)):
            troops = state.control_group_troops(index)
            selected = getattr(state, "expedition_setup_group", None) == index
            hovered = row.collidepoint(self._mouse_pos())
            fill = palette.panel_2 if selected else palette.black
            pygame.draw.rect(surface, fill, row)
            pygame.draw.rect(surface, palette.white if hovered or selected else palette.line, row, 1)
            label = self.fonts["small"].render(f"G{index + 1}", True, palette.text)
            surface.blit(label, (row.left + 8, row.top + 8))
            count = self.fonts["tiny"].render(f"{len(troops[:5])}/5", True, palette.text_dim if troops else palette.line_bright)
            surface.blit(count, (row.left + 44, row.top + 11))
            icon_x = row.left + 86
            for troop in troops[:5]:
                icon = pygame.Rect(icon_x, row.top + 7, 20, 20)
                self._draw_troop_mini_icon(surface, icon, troop, muted=not troop.alive)
                icon_x += 23
            y = row.bottom + 8

        status = "CAMP ONLINE" if camps else "NO CAMP"
        status_img = self.fonts["tiny"].render(status, True, palette.text_dim)
        surface.blit(status_img, (left.left, max(y, left.bottom - 22)))

        party = [troop for troop in getattr(state, "expedition_setup_party", []) if getattr(troop, "alive", False)]
        self._section(surface, "PARTY FORMATION", right.left, right.top)
        formation = self._expedition_formation_rect(content)
        pygame.draw.rect(surface, palette.black, formation)
        pygame.draw.rect(surface, palette.line, formation, 1)
        self._draw_expedition_links(surface, formation, len(party))
        for index, troop in enumerate(party):
            center = self._expedition_orb_positions(content, state)[index]
            orb_rect = pygame.Rect(0, 0, 46, 46)
            orb_rect.center = center
            hovered = orb_rect.collidepoint(self._mouse_pos())
            draw_circle_alpha(surface, pygame.Vector2(center), 28, palette.white, 28 if not hovered else 62, 1)
            pygame.draw.circle(surface, palette.black if not hovered else palette.white, center, 20)
            pygame.draw.circle(surface, palette.white if not hovered else palette.black, center, 20, 1)
            self._draw_troop_mini_icon(surface, pygame.Rect(center[0] - 10, center[1] - 10, 20, 20), troop, inverted=hovered)
            if hovered:
                self.tooltip_request = TooltipRequest(self._troop_tooltip_card(troop, state), self._mouse_pos())

        if not party:
            empty = self.fonts["small"].render("REGISTER A GROUP", True, palette.text_dim)
            surface.blit(empty, empty.get_rect(center=formation.center))

        actions = self._expedition_action_rects(content)
        cancel_label = self.fonts["small"].render("CANCEL", True, palette.text_dim)
        surface.blit(cancel_label, cancel_label.get_rect(center=actions["cancel"].center))
        start_label = self.fonts["small"].render("START EXPEDITION", True, palette.text if party and camps else palette.text_dim)
        surface.blit(start_label, start_label.get_rect(center=actions["start"].center))

    def _layout_expedition_buttons(self, panel_rect: pygame.Rect, state) -> list[Button]:
        content = self._dialog_content_rect(panel_rect)
        buttons: list[Button] = []
        for index, rect in enumerate(self._expedition_group_rects(content, state)):
            troops = state.control_group_troops(index)
            buttons.append(Button(rect, "", "expedition_register", index, enabled=bool(troops) and self._living_expedition_campsites(state), visible=False))
        actions = self._expedition_action_rects(content)
        buttons.append(Button(actions["cancel"], "", "expedition_cancel", enabled=bool(getattr(state, "expedition_setup_party", [])), visible=False))
        buttons.append(
            Button(
                actions["start"],
                "",
                "expedition_start",
                enabled=bool(getattr(state, "expedition_setup_party", [])) and self._living_expedition_campsites(state) and getattr(state, "expedition_run", None) is None,
                visible=False,
            )
        )
        return buttons

    def _expedition_group_rects(self, content: pygame.Rect, state) -> list[pygame.Rect]:
        left_w = min(230, content.width // 2 - 10)
        x = content.left
        y = content.top + 30
        return [pygame.Rect(x, y + index * 40, left_w, 32) for index in range(len(getattr(state, "control_groups", [])))]

    def _expedition_formation_rect(self, content: pygame.Rect) -> pygame.Rect:
        left_w = min(230, content.width // 2 - 10)
        x = content.left + left_w + 14
        return pygame.Rect(x, content.top + 30, content.right - x, max(250, content.height - 96))

    def _expedition_action_rects(self, content: pygame.Rect) -> dict[str, pygame.Rect]:
        formation = self._expedition_formation_rect(content)
        y = min(content.bottom - 34, formation.bottom + 14)
        half = max(120, (formation.width - 10) // 2)
        return {
            "cancel": pygame.Rect(formation.left, y, half, 30),
            "start": pygame.Rect(formation.right - half, y, half, 30),
        }

    def _expedition_orb_positions(self, content: pygame.Rect, state) -> list[tuple[int, int]]:
        party = getattr(state, "expedition_setup_party", [])
        formation = self._expedition_formation_rect(content)
        center = pygame.Vector2(formation.center)
        radius = min(formation.width, formation.height) * 0.31
        points = [
            pygame.Vector2(0, -radius),
            pygame.Vector2(radius * 0.95, -radius * 0.30),
            pygame.Vector2(radius * 0.58, radius * 0.88),
            pygame.Vector2(-radius * 0.58, radius * 0.88),
            pygame.Vector2(-radius * 0.95, -radius * 0.30),
        ]
        if len(party) == 1:
            points = [pygame.Vector2(0, 0)]
        elif len(party) == 2:
            points = [points[0], pygame.Vector2(0, radius * 0.74)]
        elif len(party) == 3:
            points = [points[0], points[2], points[3]]
        elif len(party) == 4:
            points = [points[0], points[1], points[3], pygame.Vector2(0, radius * 0.78)]
        return [(int(center.x + point.x), int(center.y + point.y)) for point in points[: len(party)]]

    def _expedition_orb_at(self, pos: tuple[int, int], panel_rect: pygame.Rect, state) -> int | None:
        content = self._dialog_content_rect(panel_rect)
        for index, center in enumerate(self._expedition_orb_positions(content, state)):
            if pygame.Vector2(pos).distance_to(center) <= 28:
                return index
        return None

    def _draw_expedition_links(self, surface: pygame.Surface, formation: pygame.Rect, count: int) -> None:
        if count < 2:
            return
        positions = []
        center = pygame.Vector2(formation.center)
        radius = min(formation.width, formation.height) * 0.31
        for index in range(count):
            angle = -math.pi / 2 + index * math.tau / max(5, count)
            positions.append(center + pygame.Vector2(math.cos(angle), math.sin(angle)) * radius)
        for start, end in zip(positions, positions[1:] + positions[:1]):
            draw_line_alpha(surface, start, end, config.PALETTE.white, 32, 1)

    def _draw_troop_mini_icon(self, surface: pygame.Surface, rect: pygame.Rect, troop, inverted: bool = False, muted: bool = False) -> None:
        fill = config.PALETTE.white if inverted else config.PALETTE.black
        mark = config.PALETTE.black if inverted else (config.PALETTE.text_dim if muted else config.PALETTE.white)
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, 1)
        center = pygame.Vector2(rect.center)
        r = max(4, min(rect.width, rect.height) // 3)
        kind = getattr(troop, "kind", "")
        if kind == "warrior":
            points = [(center.x, center.y - r), (center.x + r, center.y), (center.x, center.y + r), (center.x - r, center.y)]
            pygame.draw.polygon(surface, mark, points, 1)
        elif kind == "archer":
            pygame.draw.polygon(surface, mark, [(center.x, center.y - r), (center.x + r, center.y + r), (center.x - r, center.y + r)], 1)
        elif kind == "cleric":
            pygame.draw.line(surface, mark, (center.x - r, center.y), (center.x + r, center.y), 1)
            pygame.draw.line(surface, mark, (center.x, center.y - r), (center.x, center.y + r), 1)
        elif kind in {"wizard", "rune_mage"}:
            pygame.draw.circle(surface, mark, center, r, 1)
        else:
            pygame.draw.circle(surface, mark, center, r, 1)

    def _troop_tooltip_card(self, troop, state) -> AbilityCard:
        stats = troop.stats(state) if hasattr(troop, "stats") else {}
        details = [
            f"LVL {getattr(troop, 'level', 1)}  HP {int(getattr(troop, 'health', 0))}/{int(getattr(troop, 'max_health', 0))}",
            f"DMG {float(stats.get('damage', 0.0)):0.1f}  RNG {int(float(stats.get('range', 0.0)))}",
            f"SPD {float(stats.get('movement_speed', getattr(troop, 'speed', 0.0))):0.0f}  XP {getattr(troop, 'xp', 0)}",
        ]
        ability_names = [card.name for card in troop.abilities.cards(state)] if hasattr(troop, "abilities") else []
        if ability_names:
            details.append("ABIL " + ", ".join(ability_names[:3]))
        return AbilityCard(getattr(troop, "kind", "troop"), getattr(troop, "display_name", "Troop"), "Expedition party member.", tuple(details), passive=True, state="PARTY")

    def _draw_expedition_recap(self, surface: pygame.Surface, viewport: pygame.Rect, state) -> None:
        result = getattr(state, "expedition_recap", None)
        if result is None:
            return
        palette = config.PALETTE
        draw_rect_alpha(surface, viewport, palette.black, 128)
        rect = pygame.Rect(0, 0, min(620, viewport.width - 80), min(430, viewport.height - 80))
        rect.center = viewport.center
        self._alpha_rect(surface, rect, (0, 0, 0, 238))
        pygame.draw.rect(surface, palette.line_bright, rect, 1)
        title = "EXPEDITION COMPLETE" if result.victory else "EXPEDITION LOST"
        surface.blit(self.fonts["large"].render(title, True, palette.white), (rect.left + 24, rect.top + 22))
        subtitle = self.fonts["small"].render(f"{result.definition_name.upper()}  //  {result.boss_name.upper()}", True, palette.text_dim)
        surface.blit(subtitle, (rect.left + 24, rect.top + 58))

        reward_y = rect.top + 100
        rewards = [
            ("GOLD", str(result.gold if result.victory else 0)),
            ("ITEMS", str(len(result.items) if result.victory else 0)),
            ("XP", str(sum(result.xp_by_troop_id.values()) if result.victory else 0)),
        ]
        for index, (label, value) in enumerate(rewards):
            box = pygame.Rect(rect.left + 24 + index * 104, reward_y, 92, 52)
            pygame.draw.rect(surface, palette.black, box)
            pygame.draw.rect(surface, palette.line, box, 1)
            surface.blit(self.fonts["tiny"].render(label, True, palette.text_dim), (box.left + 10, box.top + 8))
            surface.blit(self.fonts["medium"].render(value, True, palette.text), (box.left + 10, box.top + 25))

        formation = pygame.Rect(rect.left + rect.width - 250, rect.top + 94, 208, 208)
        pygame.draw.rect(surface, palette.black, formation)
        pygame.draw.rect(surface, palette.line, formation, 1)
        center = pygame.Vector2(formation.center)
        radius = 72
        party = list(result.party)
        positions = []
        base_points = [
            pygame.Vector2(0, -radius),
            pygame.Vector2(radius * 0.95, -radius * 0.30),
            pygame.Vector2(radius * 0.58, radius * 0.88),
            pygame.Vector2(-radius * 0.58, radius * 0.88),
            pygame.Vector2(-radius * 0.95, -radius * 0.30),
        ]
        if len(party) == 1:
            base_points = [pygame.Vector2(0, 0)]
        elif len(party) == 2:
            base_points = [base_points[0], pygame.Vector2(0, radius * 0.74)]
        elif len(party) == 3:
            base_points = [base_points[0], base_points[2], base_points[3]]
        elif len(party) == 4:
            base_points = [base_points[0], base_points[1], base_points[3], pygame.Vector2(0, radius * 0.78)]
        for point in base_points[: len(party)]:
            positions.append((int(center.x + point.x), int(center.y + point.y)))
        for start, end in zip(positions, positions[1:] + positions[:1]):
            draw_line_alpha(surface, pygame.Vector2(start), pygame.Vector2(end), palette.white, 26, 1)
        for troop, pos in zip(party, positions):
            dead = id(troop) in result.dead_troop_ids or not getattr(troop, "alive", False)
            hovered = pygame.Vector2(self._mouse_pos()).distance_to(pos) <= 24
            pygame.draw.circle(surface, palette.black, pos, 21)
            pygame.draw.circle(surface, palette.white if not dead else palette.text_dim, pos, 21, 1)
            self._draw_troop_mini_icon(surface, pygame.Rect(pos[0] - 10, pos[1] - 10, 20, 20), troop, muted=dead)
            if dead:
                pygame.draw.line(surface, palette.white, (pos[0] - 16, pos[1] - 16), (pos[0] + 16, pos[1] + 16), 2)
                pygame.draw.line(surface, palette.white, (pos[0] + 16, pos[1] - 16), (pos[0] - 16, pos[1] + 16), 2)
            if hovered:
                self.tooltip_request = TooltipRequest(self._troop_tooltip_card(troop, state), self._mouse_pos())

        y = rect.top + 176
        if result.victory and result.items:
            surface.blit(self.fonts["small"].render("ITEMS", True, palette.text), (rect.left + 24, y))
            y += 28
            hovered_item = None
            for index, item_id in enumerate(result.items[:10]):
                definition = ITEM_DEFINITIONS.get(item_id)
                if definition is None:
                    continue
                item_rect = pygame.Rect(rect.left + 24 + (index % 5) * 42, y + (index // 5) * 42, 32, 32)
                hovered = item_rect.collidepoint(self._mouse_pos())
                self._draw_item_icon(surface, item_rect, definition, 1, hovered)
                if hovered:
                    hovered_item = definition
            if hovered_item is not None:
                self._draw_item_definition_tooltip(
                    surface,
                    viewport,
                    hovered_item,
                    self._mouse_pos(),
                    quantity=1,
                    source_label="EXPEDITION REWARD",
                )
        else:
            line = "NO REWARDS RECOVERED" if not result.victory else "NO ITEMS RECOVERED"
            surface.blit(self.fonts["small"].render(line, True, palette.text_dim), (rect.left + 24, y))

    def _draw_expedition_metrics_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        run = getattr(state, "expedition_run", None)
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        pygame.draw.line(surface, config.PALETTE.white, (rect.left, rect.top + 42), (rect.right, rect.top + 42), 1)
        surface.blit(self.fonts["medium"].render("EXPEDITION METRICS", True, config.PALETTE.white), (rect.left + 14, rect.top + 10))
        if run is None:
            surface.blit(self.fonts["small"].render("NO ACTIVE RUN", True, config.PALETTE.white), (rect.left + 16, rect.top + 64))
            return

        elapsed = max(0.1, float(getattr(run, "metrics_elapsed", 0.0)))
        status = f"{elapsed:0.1f}S  //  {len(getattr(run, 'alive_troops', []))}/{len(getattr(run, 'party', []))} PARTY"
        label = self.fonts["tiny"].render(status, True, config.PALETTE.white)
        surface.blit(label, (rect.right - label.get_width() - 42, rect.top + 16))

        content = pygame.Rect(rect.left + 12, rect.top + 54, rect.width - 24, rect.height - 66)
        chart_rects = self._expedition_metric_chart_rects(content)
        hovered_row = None
        for index, chart_rect in enumerate(chart_rects):
            metric_id = self.expedition_metric_slots[index % len(self.expedition_metric_slots)]
            row = self._draw_expedition_metric_chart(surface, chart_rect, run, metric_id, index)
            hovered_row = row or hovered_row

        if self.expedition_metric_dropdown is not None and 0 <= self.expedition_metric_dropdown < len(chart_rects):
            self._draw_expedition_metric_dropdown(surface, chart_rects[self.expedition_metric_dropdown])
        if hovered_row is not None and self.expedition_metric_dropdown is None:
            self._draw_expedition_metric_tooltip(surface, rect, hovered_row)

    def _layout_expedition_metric_buttons(self, panel_rect: pygame.Rect, state) -> list[Button]:
        if getattr(state, "expedition_run", None) is None:
            return []
        content = pygame.Rect(panel_rect.left + 12, panel_rect.top + 54, panel_rect.width - 24, panel_rect.height - 66)
        buttons: list[Button] = []
        charts = self._expedition_metric_chart_rects(content)
        for index, chart in enumerate(charts):
            header = self._expedition_metric_header_rect(chart)
            buttons.append(Button(header, "", "expedition_metric_dropdown", index, visible=False))
        if self.expedition_metric_dropdown is not None and 0 <= self.expedition_metric_dropdown < len(charts):
            for metric_id, option_rect in self._expedition_metric_option_rects(charts[self.expedition_metric_dropdown]):
                buttons.append(Button(option_rect, "", "expedition_metric_select", (self.expedition_metric_dropdown, metric_id), visible=False))
        return buttons

    def _expedition_metric_chart_rects(self, content: pygame.Rect) -> list[pygame.Rect]:
        gap = 10
        width = max(140, (content.width - gap) // 2)
        height = max(150, (content.height - gap) // 2)
        return [
            pygame.Rect(content.left + (index % 2) * (width + gap), content.top + (index // 2) * (height + gap), width, height)
            for index in range(4)
        ]

    def _expedition_metric_header_rect(self, chart: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(chart.left + 8, chart.top + 8, chart.width - 16, 24)

    def _expedition_metric_option_rects(self, chart: pygame.Rect) -> list[tuple[str, pygame.Rect]]:
        menu = pygame.Rect(chart.left + 8, chart.top + 33, chart.width - 16, 0)
        col_w = max(90, menu.width // 2)
        row_h = 22
        rects: list[tuple[str, pygame.Rect]] = []
        for index, metric_id in enumerate(EXPEDITION_METRIC_OPTIONS):
            col = index % 2
            row = index // 2
            rects.append((metric_id, pygame.Rect(menu.left + col * col_w, menu.top + row * row_h, col_w, row_h)))
        return rects

    def _draw_expedition_metric_chart(self, surface: pygame.Surface, rect: pygame.Rect, run, metric_id: str, slot_index: int):
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        header = self._expedition_metric_header_rect(rect)
        hovered_header = header.collidepoint(self._mouse_pos())
        pygame.draw.rect(surface, config.PALETTE.white if hovered_header else config.PALETTE.black, header)
        pygame.draw.rect(surface, config.PALETTE.white, header, 1)
        title_color = config.PALETTE.black if hovered_header else config.PALETTE.white
        title = EXPEDITION_METRIC_LABELS.get(metric_id, metric_id).upper()
        text = self.fonts["tiny"].render(title + "  V", True, title_color)
        surface.blit(text, (header.left + 7, header.top + 6))

        rows = run.metric_rows(metric_id) if hasattr(run, "metric_rows") else []
        y = header.bottom + 12
        max_value = max((float(row["value"]) for row in rows), default=0.0)
        hovered_row = None
        for rank, row in enumerate(rows[:5], start=1):
            row_rect = pygame.Rect(rect.left + 10, y, rect.width - 20, 34)
            hovered = row_rect.collidepoint(self._mouse_pos())
            if hovered:
                hovered_row = (metric_id, row)
                pygame.draw.rect(surface, config.PALETTE.white, row_rect, 1)
            troop = row["troop"]
            value = float(row["value"])
            percent = float(row.get("percent", 0.0))
            name = f"{rank}. {getattr(troop, 'display_name', 'Troop').upper()}"
            value_text = f"{self._format_metric_value(metric_id, value)}  {int(percent * 100)}%"
            surface.blit(self.fonts["tiny"].render(name[:18], True, config.PALETTE.white), (row_rect.left + 4, row_rect.top + 2))
            rendered_value = self.fonts["tiny"].render(value_text, True, config.PALETTE.white)
            surface.blit(rendered_value, (row_rect.right - rendered_value.get_width() - 4, row_rect.top + 2))
            bar = pygame.Rect(row_rect.left + 4, row_rect.bottom - 11, row_rect.width - 8, 6)
            pygame.draw.rect(surface, config.PALETTE.white, bar, 1)
            fill = bar.copy()
            fill.width = int(bar.width * (0.0 if max_value <= 0 else min(1.0, value / max_value)))
            if fill.width > 0:
                pygame.draw.rect(surface, config.PALETTE.white, fill)
            y += 39
        if not rows:
            empty = self.fonts["tiny"].render("NO DATA", True, config.PALETTE.white)
            surface.blit(empty, empty.get_rect(center=rect.center))
        return hovered_row

    def _draw_expedition_metric_dropdown(self, surface: pygame.Surface, chart: pygame.Rect) -> None:
        options = self._expedition_metric_option_rects(chart)
        if not options:
            return
        bounds = options[0][1].unionall([rect for _metric, rect in options])
        pygame.draw.rect(surface, config.PALETTE.black, bounds.inflate(6, 6))
        pygame.draw.rect(surface, config.PALETTE.white, bounds.inflate(6, 6), 1)
        mouse = self._mouse_pos()
        for metric_id, rect in options:
            hovered = rect.collidepoint(mouse)
            pygame.draw.rect(surface, config.PALETTE.white if hovered else config.PALETTE.black, rect)
            pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
            color = config.PALETTE.black if hovered else config.PALETTE.white
            label = self.fonts["tiny"].render(EXPEDITION_METRIC_LABELS.get(metric_id, metric_id).upper()[:15], True, color)
            surface.blit(label, (rect.left + 5, rect.top + 6))

    def _format_metric_value(self, metric_id: str, value: float) -> str:
        if metric_id in {"criticals", "stuns", "abilities_fired", "kills", "deaths"}:
            return str(int(round(value)))
        if metric_id in {"dps", "hps"}:
            return f"{value:0.1f}"
        return str(int(round(value)))

    def _draw_expedition_metric_tooltip(self, surface: pygame.Surface, bounds: pygame.Rect, hover_data) -> None:
        metric_id, row = hover_data
        troop = row["troop"]
        metrics = row["metrics"]
        value = float(row["value"])
        percent = float(row.get("percent", 0.0))
        lines = [
            EXPEDITION_METRIC_LABELS.get(metric_id, metric_id).upper(),
            f"VALUE {self._format_metric_value(metric_id, value)}",
            f"CONTRIBUTION {int(percent * 100)}%",
            f"DMG {int(metrics.damage_done)}  TAKEN {int(metrics.damage_taken)}",
            f"HEAL {int(metrics.healing_done)}  BLOCK {int(metrics.blocks)}",
            f"CRIT {metrics.criticals}  STUN {metrics.stuns}  AGGRO {int(metrics.aggro)}",
        ]
        lines.extend(metrics.ability_summary()[:4])
        width = 280
        height = 18 + len(lines) * 17
        mouse = self._mouse_pos()
        rect = pygame.Rect(mouse[0] + 16, mouse[1] - 8, width, height)
        if rect.right > bounds.right - 8:
            rect.right = mouse[0] - 14
        if rect.bottom > bounds.bottom - 8:
            rect.bottom = mouse[1] - 14
        if rect.top < bounds.top + 8:
            rect.top = bounds.top + 8
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        y = rect.top + 8
        surface.blit(self.fonts["small"].render(getattr(troop, "display_name", "Troop").upper(), True, config.PALETTE.white), (rect.left + 10, y))
        y += 22
        for line in lines:
            surface.blit(self.fonts["tiny"].render(line, True, config.PALETTE.white), (rect.left + 10, y))
            y += 17

    def _draw_hero_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        self._reset_hero_view_if_context_changed(state)
        self.dialog_scroll = 0.0
        self.dialog_scroll_max = 0.0
        troop = self._selected_hero_troop(state)
        halls_ready = bool(self._living_hero_halls(state))
        subtitle = "Ascension web for the selected troop." if troop is not None else "Select a troop with a hero tree."
        self._draw_dialog_shell(surface, rect, "Hero Hall", subtitle)
        content = self._dialog_content_rect(rect)
        pygame.draw.rect(surface, config.PALETTE.black, content)
        pygame.draw.rect(surface, config.PALETTE.line, content, 1)

        if troop is None:
            self._draw_hero_empty_state(surface, content, state)
            return

        tree = troop.hero_tree()
        if tree is None:
            self._draw_hero_empty_state(surface, content, state)
            return

        header = pygame.Rect(content.left + 14, content.top + 12, content.width - 28, 48)
        self._draw_hero_header(surface, header, troop, halls_ready)
        canvas = self._hero_canvas_rect(rect)
        pygame.draw.rect(surface, config.PALETTE.black, canvas)
        pygame.draw.rect(surface, config.PALETTE.line_bright, canvas, 1)

        previous_clip = surface.get_clip()
        surface.set_clip(canvas)
        self._draw_hero_tree(surface, canvas, troop, tree, halls_ready)
        surface.set_clip(previous_clip)

    def _layout_hero_buttons(self, panel_rect: pygame.Rect, state) -> list[Button]:
        self._reset_hero_view_if_context_changed(state)
        troop = self._selected_hero_troop(state)
        tree = troop.hero_tree() if troop is not None else None
        if troop is None or tree is None:
            return []
        canvas = self._hero_canvas_rect(panel_rect)
        halls_ready = bool(self._living_hero_halls(state))
        buttons: list[Button] = []
        positions = self._hero_node_positions(canvas, tree)
        for node in tree.nodes():
            pos = positions.get(node.node_id)
            if pos is None:
                continue
            button_rect = self._hero_node_rect(pos, canvas)
            if not button_rect.colliderect(canvas):
                continue
            enabled = halls_ready and not state.game_over and troop.can_purchase_hero_node(node.node_id)
            buttons.append(Button(button_rect.clip(canvas), "", "hero_node", node.node_id, enabled=enabled, visible=False))
        return buttons

    def _draw_hero_header(self, surface: pygame.Surface, rect: pygame.Rect, troop, halls_ready: bool) -> None:
        palette = config.PALETTE
        pygame.draw.rect(surface, palette.panel_2, rect)
        pygame.draw.rect(surface, palette.line_bright if halls_ready else palette.line, rect, 1)
        self._draw_unit_glyph(surface, troop.kind, pygame.Rect(rect.left + 8, rect.top + 7, 34, 34))
        title = f"{troop.display_name.upper()}  LVL {troop.level}"
        surface.blit(self.fonts["small"].render(title, True, palette.text), (rect.left + 54, rect.top + 8))
        meta = f"ORBS {troop.hero_orbs}   SPENT {troop.hero_spent_orbs()}   {'HALL ONLINE' if halls_ready else 'NEED HERO HALL'}"
        surface.blit(self.fonts["tiny"].render(meta, True, palette.text_dim), (rect.left + 54, rect.top + 29))
        zoom = self.fonts["tiny"].render(f"{int(self.hero_tree_zoom * 100)}%", True, palette.text_dim)
        surface.blit(zoom, (rect.right - zoom.get_width() - 10, rect.top + 17))

    def _draw_hero_empty_state(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        hero_troops = [
            troop
            for troop in getattr(state, "troops", [])
            if getattr(troop, "alive", False) and getattr(troop, "has_hero_tree", lambda: False)()
        ]
        self._section(surface, "ASCENSION", rect.left + 16, rect.top + 16)
        entries = [
            ("HALLS", str(len(self._living_hero_halls(state)))),
            ("TROOPS", str(len(hero_troops))),
            ("READY ORBS", str(sum(getattr(troop, "hero_orbs", 0) for troop in hero_troops))),
            ("SPENT", str(sum(getattr(troop, "hero_spent_orbs", lambda: 0)() for troop in hero_troops))),
        ]
        self._draw_stat_grid(surface, rect.left + 16, rect.top + 48, rect.width - 32, entries)
        y = rect.top + 164
        for troop in hero_troops[:6]:
            line = f"{troop.display_name.upper()}  LVL {troop.level}  ORBS {troop.hero_orbs}"
            surface.blit(self.fonts["tiny"].render(line, True, palette.text_dim), (rect.left + 16, y))
            y += 18

    def _draw_hero_tree(self, surface: pygame.Surface, canvas: pygame.Rect, troop, tree: HeroTreeDefinition, halls_ready: bool) -> None:
        palette = config.PALETTE
        positions = self._hero_node_positions(canvas, tree)
        root = self._hero_root_position(canvas)
        phase = pygame.time.get_ticks() * 0.004
        root_radius = max(13, int(17 * self.hero_tree_zoom))
        pygame.draw.circle(surface, palette.black, root, root_radius)
        pygame.draw.circle(surface, palette.white, root, root_radius, 1)
        pygame.draw.circle(surface, palette.line_bright, root, max(3, int(root_radius * (0.55 + 0.08 * math.sin(phase)))), 1)

        for branch in tree.branches:
            previous = root
            for node in branch.nodes:
                current = positions[node.node_id]
                lit = troop.hero_node_rank(node.node_id) > 0
                available = halls_ready and troop.can_purchase_hero_node(node.node_id)
                color = palette.white if lit else (palette.line_bright if available else palette.line)
                width = 2 if lit else 1
                pygame.draw.line(surface, color, previous, current, width)
                previous = current

        for branch_index, branch in enumerate(tree.branches):
            if not branch.nodes:
                continue
            end = positions[branch.nodes[-1].node_id]
            direction = self._hero_branch_direction(branch_index)
            label_pos = end + direction * (34 * self.hero_tree_zoom)
            label = self.fonts["tiny"].render(branch.name.upper(), True, palette.text_dim)
            surface.blit(label, label.get_rect(center=(int(label_pos.x), int(label_pos.y))))
            for node in branch.nodes:
                self._draw_hero_node(surface, canvas, troop, node, positions[node.node_id], halls_ready)

    def _draw_hero_node(self, surface: pygame.Surface, canvas: pygame.Rect, troop, node: HeroNodeDefinition, pos: pygame.Vector2, halls_ready: bool) -> None:
        palette = config.PALETTE
        rank = troop.hero_node_rank(node.node_id)
        available = halls_ready and troop.can_purchase_hero_node(node.node_id)
        purchased = rank > 0
        rect = self._hero_node_rect(pos, canvas)
        hovered = rect.collidepoint(self._mouse_pos())
        radius = rect.width // 2 - 2
        fill = palette.white if purchased or (hovered and available) else palette.black
        mark = palette.black if fill == palette.white else (palette.white if available else palette.text_dim)
        border = palette.white if purchased or available or hovered else palette.line

        if available:
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() * 0.008 + node.tier)
            pygame.draw.circle(surface, palette.line_bright, pos, radius + 5 + int(pulse * 3), 1)
        pygame.draw.circle(surface, fill, pos, radius)
        pygame.draw.circle(surface, border, pos, radius, 2 if purchased else 1)
        inner = max(3, radius // 3)
        pygame.draw.circle(surface, mark, pos, inner, 1)

        center_label = f"x{rank}" if rank > 0 else str(node.tier)
        label_image = self.fonts["tiny"].render(center_label, True, mark)
        surface.blit(label_image, label_image.get_rect(center=(int(pos.x), int(pos.y))))

        name = node.name.upper()
        while self.fonts["tiny"].size(name)[0] > 92 and len(name) > 4:
            name = name[:-2] + "."
        text_color = palette.text if purchased or available else palette.text_dim
        name_image = self.fonts["tiny"].render(name, True, text_color)
        surface.blit(name_image, name_image.get_rect(center=(int(pos.x), int(pos.y + radius + 15))))

        if hovered:
            self.tooltip_request = TooltipRequest(self._hero_node_card(node, troop), self._mouse_pos())

    def _hero_node_card(self, node: HeroNodeDefinition, troop) -> AbilityCard:
        rank = troop.hero_node_rank(node.node_id)
        state = f"RANK {rank}" if rank > 0 else "LOCKED"
        details = [f"Cost {node.cost} orb", "Repeatable" if node.repeatable else "Single unlock"]
        for effect, value in node.effects.items():
            details.append(self._format_hero_effect(effect, value))
        if node.ability_id:
            details.append("Unlocks ability")
        if node.requires and troop.hero_node_rank(node.requires) <= 0:
            details.append("Requires previous node")
        return AbilityCard(node.node_id, node.name, node.description, tuple(details), passive=True, state=state)

    def _format_hero_effect(self, effect: str, value: float) -> str:
        labels = {
            "damage_multiplier": "Damage",
            "crit_chance": "Crit chance",
            "max_health_multiplier": "Max HP",
            "armor": "Armor",
            "visibility_range_multiplier": "Visibility",
            "aggro_generation_multiplier": "Aggro",
            "movement_speed_multiplier": "Move speed",
            "healing_amount_multiplier": "Healing",
            "cooldown_reduction": "Cooldown",
            "repair_amount_multiplier": "Repair",
            "range_multiplier": "Range",
            "attack_speed_multiplier": "Attack speed",
            "shield_repair_amount_multiplier": "Shield repair",
        }
        if effect == "chain_lightning_jumps":
            return f"Chain jumps +{int(value)}"
        label = labels.get(effect, effect.replace("_", " ").title())
        if effect == "cooldown_reduction":
            return f"{label} -{int(round(value * 100))}%"
        return f"{label} {value * 100:+.0f}%"

    def _hero_canvas_rect(self, panel_rect: pygame.Rect) -> pygame.Rect:
        content = self._dialog_content_rect(panel_rect)
        top = content.top + 74
        return pygame.Rect(content.left + 12, top, content.width - 24, max(1, content.bottom - top - 12))

    def _hero_root_position(self, canvas: pygame.Rect) -> pygame.Vector2:
        return pygame.Vector2(canvas.center) + self.hero_tree_pan

    def _hero_branch_direction(self, index: int) -> pygame.Vector2:
        angle = -math.pi / 2 + index * math.tau / 3
        return pygame.Vector2(math.cos(angle), math.sin(angle))

    def _hero_node_positions(self, canvas: pygame.Rect, tree: HeroTreeDefinition) -> dict[str, pygame.Vector2]:
        root = self._hero_root_position(canvas)
        positions: dict[str, pygame.Vector2] = {}
        tick = pygame.time.get_ticks() * 0.001
        spacing = 108 * self.hero_tree_zoom
        for branch_index, branch in enumerate(tree.branches):
            direction = self._hero_branch_direction(branch_index)
            tangent = pygame.Vector2(-direction.y, direction.x)
            for node in branch.nodes:
                seed = (sum(ord(char) for char in node.node_id) % 997) * 0.013
                wiggle = (3.0 + node.tier * 0.8) * self.hero_tree_zoom
                base = root + direction * (spacing * node.tier)
                positions[node.node_id] = base + tangent * math.sin(tick * 2.2 + seed) * wiggle + direction * math.cos(tick * 1.6 + seed) * wiggle * 0.35
        return positions

    def _hero_node_rect(self, pos: pygame.Vector2, canvas: pygame.Rect) -> pygame.Rect:
        radius = max(18, int(22 * self.hero_tree_zoom))
        rect = pygame.Rect(0, 0, radius * 2 + 8, radius * 2 + 8)
        rect.center = (int(pos.x), int(pos.y))
        return rect

    def _selected_hero_troop(self, state):
        troop = getattr(state, "selected_troop", None)
        if (
            troop is not None
            and len(getattr(state, "selected_troops", [troop])) == 1
            and getattr(troop, "alive", False)
            and getattr(troop, "has_hero_tree", lambda: False)()
        ):
            return troop
        candidates = [
            candidate
            for candidate in getattr(state, "troops", [])
            if getattr(candidate, "alive", False) and getattr(candidate, "has_hero_tree", lambda: False)()
        ]
        candidates.sort(key=lambda candidate: (getattr(candidate, "hero_orbs", 0) <= 0, -getattr(candidate, "hero_orbs", 0), getattr(candidate, "kind", "")))
        return candidates[0] if candidates else None

    def _reset_hero_view_if_context_changed(self, state, force: bool = False) -> None:
        troop = self._selected_hero_troop(state)
        signature = ("troop", id(troop)) if troop is not None else ("none", 0)
        if not force and signature == self.hero_tree_context_signature:
            return
        self.hero_tree_context_signature = signature
        self.hero_tree_zoom = 1.0
        self.hero_tree_pan = pygame.Vector2(0, 0)
        self.hero_tree_dragging = False

    def _start_hero_drag(self, pos: tuple[int, int]) -> None:
        self.hero_tree_dragging = True
        self.hero_tree_drag_last = pygame.Vector2(pos)

    def _drag_hero_tree(self, pos: tuple[int, int]) -> None:
        current = pygame.Vector2(pos)
        self.hero_tree_pan += current - self.hero_tree_drag_last
        self.hero_tree_drag_last = current

    def _zoom_hero_tree(self, pos: tuple[int, int], wheel_y: int, panel_rect: pygame.Rect) -> None:
        canvas = self._hero_canvas_rect(panel_rect)
        if not canvas.collidepoint(pos):
            return
        old_zoom = self.hero_tree_zoom
        factor = 1.12 if wheel_y > 0 else 1.0 / 1.12
        self.hero_tree_zoom = max(0.55, min(2.2, self.hero_tree_zoom * factor))
        if math.isclose(old_zoom, self.hero_tree_zoom):
            return
        center = pygame.Vector2(canvas.center)
        mouse = pygame.Vector2(pos)
        world_from_center = mouse - center - self.hero_tree_pan
        self.hero_tree_pan -= world_from_center * (self.hero_tree_zoom / old_zoom - 1.0)

    def _draw_research_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        research = self._research_lab_for_panel(state)
        if self.research_panel_lab is not None:
            subtitle = "Specific lab console. This lab handles the selected project."
        elif research is not None:
            subtitle = "Auto console. Projects route to the first idle research lab."
        else:
            subtitle = "Build a Research Lab to unlock this console."
        self._draw_dialog_shell(surface, rect, "Research Console", subtitle)
        content = self._dialog_content_rect(rect)
        self._clamp_dialog_scroll(self._research_content_height(content), content.height)

        previous_clip = surface.get_clip()
        surface.set_clip(content)
        for kind, payload in self._research_draw_items(content):
            if kind == "category":
                title, item_rect = payload
                if item_rect.colliderect(content):
                    surface.blit(self.fonts["small"].render(str(title).upper(), True, palette.white), (item_rect.left, item_rect.top + 5))
                    pygame.draw.line(surface, palette.line_bright, (item_rect.left + 170, item_rect.centery), (content.right - 8, item_rect.centery), 1)
            else:
                research_id, card = payload
                if card.colliderect(content):
                    self._draw_research_card(surface, card, research_id, research, state)
        surface.set_clip(previous_clip)
        self._draw_scrollbar(surface, content, self.dialog_scroll, self.dialog_scroll_max)

    def _layout_research_buttons(self, panel_rect: pygame.Rect, state) -> list[Button]:
        content = self._dialog_content_rect(panel_rect)
        self._clamp_dialog_scroll(self._research_content_height(content), content.height)
        buttons: list[Button] = []
        research = self._research_lab_for_panel(state)
        for kind, payload in self._research_draw_items(content):
            if kind != "research":
                continue
            research_id, card = payload
            auto_rect = pygame.Rect(card.right - 214, card.bottom - 35, 92, 24)
            start_rect = pygame.Rect(card.right - 112, card.bottom - 35, 92, 24)
            if start_rect.top < content.top or start_rect.bottom > content.bottom:
                continue
            busy = research is not None and research.active_order is not None
            active = busy and research.active_order.research_id == research_id
            enabled = (
                research is not None
                and research.can_research()
                and state.research.can_afford(state, research_id)
                and not state.game_over
            )
            label = "ACTIVE" if active else ("BUSY" if busy else "START")
            auto_label = "AUTO ON" if state.research.auto_enabled(research_id) else "AUTO"
            buttons.append(Button(auto_rect, auto_label, "toggle_auto_research", research_id, enabled=not state.game_over, selected=state.research.auto_enabled(research_id)))
            buttons.append(Button(start_rect, label, "research", research_id, enabled=enabled))
        return buttons

    def _research_draw_items(self, content: pygame.Rect):
        y = content.top - int(self.dialog_scroll)
        for category, research_ids in RESEARCH_CATEGORIES:
            header = pygame.Rect(content.left, y, content.width, 34)
            yield "category", (category, header)
            y += 42
            for research_id in research_ids:
                card = pygame.Rect(content.left, y, content.width - 8, 112)
                yield "research", (research_id, card)
                y += 124
            y += 10

    def _research_content_height(self, content: pygame.Rect) -> int:
        count = sum(len(ids) for _category, ids in RESEARCH_CATEGORIES)
        categories = len(RESEARCH_CATEGORIES)
        return categories * 52 + count * 124 + 12

    def _draw_research_card(self, surface: pygame.Surface, rect: pygame.Rect, research_id: str, research, state) -> None:
        palette = config.PALETTE
        definition = RESEARCH_DEFINITIONS[research_id]
        active = research is not None and research.active_order is not None and research.active_order.research_id == research_id
        affordable = state.research.can_afford(state, research_id)
        auto = state.research.auto_enabled(research_id)
        pygame.draw.rect(surface, palette.panel_2 if active or auto else palette.black, rect)
        pygame.draw.rect(surface, palette.white if active else (palette.line_bright if affordable else palette.line), rect, 1)
        x = rect.left + 14
        y = rect.top + 10
        surface.blit(self.fonts["small"].render(definition.name.upper(), True, palette.text), (x, y))
        y += 23
        for line in self._wrap(definition.description, self.fonts["tiny"], rect.width - 160)[:2]:
            surface.blit(self.fonts["tiny"].render(line, True, palette.text_dim), (x, y))
            y += 15

        level = state.research.level(research_id)
        next_bonus = state.research.bonus_percent(research_id) + int(round(definition.increment * 100))
        gold, minerals = state.research.cost(research_id)
        time = state.research.time(research_id)
        cost = f"{gold}G"
        if minerals:
            cost += f"  {minerals}M"
        meta = [
            f"RANK {level} -> {level + 1}",
            f"EFFECT {state.research.bonus_percent(research_id)}% -> {next_bonus}%",
            f"COST {cost}",
            f"TIME {time:0.1f}S",
        ]
        mx = rect.right - 226
        my = rect.top + 11
        for line in meta:
            surface.blit(self.fonts["tiny"].render(line, True, palette.text if affordable else palette.text_dim), (mx, my))
            my += 17

        if active and research.active_order is not None:
            progress = 1.0 - max(0.0, research.active_order.remaining / max(0.01, research.active_order.total))
            self._draw_bar(surface, pygame.Rect(x, rect.bottom - 18, rect.width - 150, 5), progress)
        elif auto:
            surface.blit(self.fonts["tiny"].render("AUTO RESEARCH", True, palette.text), (x, rect.bottom - 21))

    def _draw_units_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        barracks = self._training_barracks(state)
        if self.units_panel_barracks is not None:
            subtitle = "Specific barracks roster. Orders stay on this queue."
        elif barracks is not None:
            subtitle = "Auto roster. Orders route to the first barracks with open queue."
        else:
            subtitle = "Build a Barracks to unlock troop production."
        self._draw_dialog_shell(surface, rect, "Unit Roster", subtitle)
        content = self._dialog_content_rect(rect)
        self._clamp_dialog_scroll(self._unit_content_height(content), content.height)

        queue_note = "NO BARRACKS"
        if barracks is not None:
            queue_note = f"QUEUE {len(barracks.train_queue)}/{barracks.queue_limit}"
            if barracks.train_queue:
                order = barracks.train_queue[0]
                queue_note += f"  ACTIVE {TROOP_NAMES[order.kind].upper()} {order.remaining:0.1f}S"
        note = self.fonts["tiny"].render(queue_note, True, palette.text_dim)
        surface.blit(note, (content.left, rect.top + 44))

        previous_clip = surface.get_clip()
        surface.set_clip(content)
        for kind, card in self._unit_card_rects(content):
            if not card.colliderect(content):
                continue
            data = TROOP_DATA[kind]
            affordable = state.gold >= data.cost and state.troop_supply_committed() < state.troop_capacity()
            pygame.draw.rect(surface, palette.black, card)
            pygame.draw.rect(surface, palette.line_bright if affordable else palette.line, card, 1)
            self._draw_unit_glyph(surface, kind, pygame.Rect(card.left + 14, card.top + 16, 44, 44))
            x = card.left + 72
            surface.blit(self.fonts["small"].render(TROOP_NAMES[kind].upper(), True, palette.text), (x, card.top + 12))
            desc = TROOP_DESCRIPTIONS.get(kind, "Stationable troop.")
            y = card.top + 36
            for line in self._wrap(desc, self.fonts["tiny"], card.width - 230)[:2]:
                surface.blit(self.fonts["tiny"].render(line, True, palette.text_dim), (x, y))
                y += 15
            ability_cards = troop_ability_cards(kind, state)
            self._draw_ability_chip_row(surface, ability_cards, x, card.top + 69, max(120, card.width - 198), max_count=4)
            stats = f"{data.cost}G   {data.train_time:0.1f}S   HP {int(data.health)}   DMG {data.damage:0.1f}   RATE {data.fire_rate:0.2f}"
            surface.blit(self.fonts["tiny"].render(stats, True, palette.text), (x, card.bottom - 22))
        surface.set_clip(previous_clip)
        self._draw_scrollbar(surface, content, self.dialog_scroll, self.dialog_scroll_max)

    def _layout_unit_buttons(self, panel_rect: pygame.Rect, state) -> list[Button]:
        content = self._dialog_content_rect(panel_rect)
        self._clamp_dialog_scroll(self._unit_content_height(content), content.height)
        buttons: list[Button] = []
        barracks = self._training_barracks(state)
        for kind, card in self._unit_card_rects(content):
            button_rect = pygame.Rect(card.right - 106, card.bottom - 36, 88, 25)
            if button_rect.top < content.top or button_rect.bottom > content.bottom:
                continue
            data = TROOP_DATA[kind]
            enabled = (
                barracks is not None
                and barracks.can_queue()
                and state.gold >= data.cost
                and state.troop_supply_committed() < state.troop_capacity()
            )
            buttons.append(Button(button_rect, "TRAIN", "train", kind, enabled=enabled))
        return buttons

    def _unit_card_rects(self, content: pygame.Rect):
        y = content.top - int(self.dialog_scroll)
        for kind in TROOP_DATA:
            yield kind, pygame.Rect(content.left, y, content.width - 8, 126)
            y += 138

    def _unit_content_height(self, content: pygame.Rect) -> int:
        return len(TROOP_DATA) * 138 + 8

    def _draw_items_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        self._draw_dialog_shell(surface, rect, "Inventory", "Use scrolls from a compact workspace grid.")
        content = self._dialog_content_rect(rect)
        slots = self._panel_inventory_rects(content, state)
        for index, slot_rect in enumerate(slots):
            slot = state.inventory.slot(index)
            hovered = slot is not None and slot_rect.collidepoint(self._mouse_pos())
            draw_slot = hover_feedback.scaled_rect(slot_rect, hovered)
            pygame.draw.rect(surface, palette.white if hovered else palette.bg, draw_slot)
            pygame.draw.rect(surface, palette.black if hovered else (palette.line_bright if slot else palette.line), draw_slot, 1)
            if slot is None:
                pygame.draw.line(surface, palette.line, (draw_slot.left + 7, draw_slot.bottom - 7), (draw_slot.right - 7, draw_slot.top + 7), 1)
            else:
                definition = ITEM_DEFINITIONS.get(slot.item_id)
                if definition is not None:
                    self._draw_item_icon(surface, draw_slot.inflate(-8, -8), definition, slot.quantity, hovered)

        x = content.left
        y = slots[-1].bottom + 24 if slots else content.top
        surface.blit(self.fonts["small"].render("ACTIVE BUFFS", True, palette.text), (x, y))
        y += 26
        if not state.active_item_buffs:
            surface.blit(self.fonts["tiny"].render("NONE", True, palette.text_dim), (x, y))
        for buff in state.active_item_buffs[:8]:
            row = pygame.Rect(x, y, content.width - 10, 28)
            pygame.draw.rect(surface, palette.black, row)
            pygame.draw.rect(surface, palette.line, row, 1)
            surface.blit(self.fonts["tiny"].render(buff.name.upper(), True, palette.text), (row.left + 10, row.top + 7))
            self._draw_bar(surface, pygame.Rect(row.right - 154, row.top + 11, 92, 5), buff.remaining / max(0.01, buff.total))
            surface.blit(self.fonts["tiny"].render(f"{buff.remaining:0.0f}S", True, palette.text_dim), (row.right - 52, row.top + 7))
            y += 34

    def _layout_item_buttons(self, panel_rect: pygame.Rect, state) -> list[Button]:
        content = self._dialog_content_rect(panel_rect)
        buttons: list[Button] = []
        for index, rect in enumerate(self._panel_inventory_rects(content, state)):
            if state.inventory.slot(index) is not None:
                buttons.append(Button(rect, "", "use_item", index, visible=False))
        return buttons

    def _draw_level_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        ready_towers = self._level_ready_towers(state)
        ready_troops = self._level_ready_troops(state)
        total_levels = sum(self._tower_level_plan(tower)[2] for tower in ready_towers)
        ready_total = len(ready_towers) + len(ready_troops)
        self._draw_dialog_shell(surface, rect, "Level Up", f"Ready {len(ready_towers)} towers  {len(ready_troops)} troops")
        content = self._dialog_content_rect(rect)
        list_rect = pygame.Rect(content.left, content.top + 42, content.width, max(1, content.height - 42))
        self._clamp_dialog_scroll(self._level_content_height(content, state), list_rect.height)

        summary = f"READY {ready_total}"
        if ready_towers:
            summary += f"   AVAILABLE +{total_levels} LVL"
        surface.blit(self.fonts["small"].render(summary, True, palette.text), (content.left, content.top + 5))

        if ready_total <= 0:
            empty = self.fonts["small"].render("NO READY UNITS", True, palette.text_dim)
            surface.blit(empty, empty.get_rect(center=list_rect.center))
            return

        previous_clip = surface.get_clip()
        surface.set_clip(list_rect)
        for kind, payload in self._level_draw_items(list_rect, state):
            if kind == "header":
                title, header = payload
                if header.colliderect(list_rect):
                    surface.blit(self.fonts["small"].render(str(title).upper(), True, palette.white), (header.left, header.top + 5))
                    pygame.draw.line(surface, palette.line_bright, (header.left + 120, header.centery), (list_rect.right - 12, header.centery), 1)
            elif kind == "tower":
                tower, card = payload
                if card.colliderect(list_rect):
                    self._draw_level_tower_card(surface, card, tower, state)
            elif kind == "troop":
                troop, card = payload
                if card.colliderect(list_rect):
                    self._draw_level_troop_card(surface, card, troop, state)
        surface.set_clip(previous_clip)
        self._draw_scrollbar(surface, list_rect, self.dialog_scroll, self.dialog_scroll_max)

    def _layout_level_buttons(self, panel_rect: pygame.Rect, state) -> list[Button]:
        content = self._dialog_content_rect(panel_rect)
        list_rect = pygame.Rect(content.left, content.top + 42, content.width, max(1, content.height - 42))
        self._clamp_dialog_scroll(self._level_content_height(content, state), list_rect.height)
        ready = self._level_ready_towers(state)
        buttons: list[Button] = [
            Button(
                pygame.Rect(content.right - 150, content.top, 142, 28),
                "UPGRADE TOWERS",
                "level_up_all",
                enabled=bool(ready) and not state.game_over,
            )
        ]
        for kind, payload in self._level_draw_items(list_rect, state):
            if kind not in ("tower", "troop"):
                continue
            target, card = payload
            if card.top < list_rect.top or card.bottom > list_rect.bottom:
                continue
            command = "focus_level_tower" if kind == "tower" else "focus_level_troop"
            buttons.append(Button(card, "", command, target, visible=False))
        return buttons

    def _level_ready_towers(self, state) -> list:
        return sorted(
            state.ready_level_towers(),
            key=lambda tower: (
                -self._tower_level_plan(tower)[2],
                tower.level,
                tower.display_name,
                tower.cell,
            ),
        )

    def _level_ready_troops(self, state) -> list:
        return sorted(
            state.ready_level_troops(),
            key=lambda troop: (
                -self._troop_level_plan(troop)[2],
                troop.level,
                troop.display_name,
                id(troop),
            ),
        )

    def _tower_level_plan(self, tower) -> tuple[int, int, int]:
        xp = int(tower.xp)
        level = int(tower.level)
        count = 0
        while xp >= xp_needed(level):
            xp -= xp_needed(level)
            level += 1
            count += 1
        return level, xp, count

    def _troop_level_plan(self, troop) -> tuple[int, int, int]:
        xp = int(troop.xp)
        level = int(troop.level)
        count = 0
        while xp >= xp_needed(level):
            xp -= xp_needed(level)
            level += 1
            count += 1
        return level, xp, count

    def _level_draw_items(self, list_rect: pygame.Rect, state):
        y = list_rect.top - int(self.dialog_scroll)
        towers = self._level_ready_towers(state)
        troops = self._level_ready_troops(state)
        if towers:
            header = pygame.Rect(list_rect.left, y, list_rect.width - 8, 30)
            yield "header", ("Towers", header)
            y += 38
            for tower in towers:
                yield "tower", (tower, pygame.Rect(list_rect.left, y, list_rect.width - 8, 80))
                y += 92
            y += 10
        if troops:
            header = pygame.Rect(list_rect.left, y, list_rect.width - 8, 30)
            yield "header", ("Troops", header)
            y += 38
            for troop in troops:
                yield "troop", (troop, pygame.Rect(list_rect.left, y, list_rect.width - 8, 88))
                y += 100

    def _level_card_rects(self, list_rect: pygame.Rect, state):
        for kind, payload in self._level_draw_items(list_rect, state):
            if kind == "tower":
                yield payload

    def _level_content_height(self, content: pygame.Rect, state) -> int:
        height = 8
        tower_count = len(self._level_ready_towers(state))
        troop_count = len(self._level_ready_troops(state))
        if tower_count:
            height += 38 + tower_count * 92 + 10
        if troop_count:
            height += 38 + troop_count * 100
        return height

    def _draw_level_tower_card(self, surface: pygame.Surface, rect: pygame.Rect, tower, state) -> None:
        palette = config.PALETTE
        selected = tower is state.selected_tower
        hovered = rect.collidepoint(self._mouse_pos())
        draw_rect = hover_feedback.scaled_rect(rect, hovered)
        projected_level, remaining_xp, gained = self._tower_level_plan(tower)
        border = palette.white if hovered or selected else palette.line_bright
        fill = palette.white if hovered else (palette.panel_2 if selected else palette.black)
        text_color = palette.black if hovered else palette.text
        dim_color = palette.black if hovered else palette.text_dim
        mark_color = palette.black if hovered else palette.white
        pygame.draw.rect(surface, fill, draw_rect)
        pygame.draw.rect(surface, border, draw_rect, 1)

        icon = pygame.Rect(draw_rect.left + 12, draw_rect.top + 16, 40, 40)
        pygame.draw.rect(surface, palette.black if hovered else palette.bg, icon)
        pygame.draw.rect(surface, mark_color if selected or hovered else palette.line_bright, icon, 1)
        self._draw_tower_level_icon(surface, icon, tower.kind, selected, mark_color)

        x = icon.right + 14
        name = f"{tower.display_name.upper()}  {tower.cell[0]},{tower.cell[1]}"
        surface.blit(self.fonts["small"].render(name, True, text_color), (x, draw_rect.top + 10))
        surface.blit(self.fonts["tiny"].render(f"LVL {tower.level} -> {projected_level}   +{gained} LVL", True, mark_color), (x, draw_rect.top + 34))
        required = xp_needed(tower.level)
        self._draw_bar(surface, pygame.Rect(x, draw_rect.bottom - 16, min(180, draw_rect.width - 220), 5), min(1.0, tower.xp / max(1, required)))
        xp_text = self.fonts["tiny"].render(f"{tower.xp}XP  NEXT {required}XP  LEFT {remaining_xp}XP", True, dim_color)
        surface.blit(xp_text, (x + min(190, draw_rect.width - 210), draw_rect.bottom - 21))

    def _draw_level_troop_card(self, surface: pygame.Surface, rect: pygame.Rect, troop, state) -> None:
        palette = config.PALETTE
        selected = troop is state.selected_troop
        hovered = rect.collidepoint(self._mouse_pos())
        draw_rect = hover_feedback.scaled_rect(rect, hovered)
        projected_level, remaining_xp, gained = self._troop_level_plan(troop)
        border = palette.white if hovered or selected else palette.line_bright
        fill = palette.white if hovered else (palette.panel_2 if selected else palette.black)
        text_color = palette.black if hovered else palette.text
        dim_color = palette.black if hovered else palette.text_dim
        mark_color = palette.black if hovered else palette.white
        pygame.draw.rect(surface, fill, draw_rect)
        pygame.draw.rect(surface, border, draw_rect, 1)

        icon = pygame.Rect(draw_rect.left + 12, draw_rect.top + 20, 40, 40)
        self._draw_unit_glyph(surface, troop.kind, icon, hovered)

        x = icon.right + 14
        name = f"{troop.display_name.upper()}  HP {int(troop.health)}/{int(troop.max_health)}"
        surface.blit(self.fonts["small"].render(name, True, text_color), (x, draw_rect.top + 10))
        surface.blit(self.fonts["tiny"].render(f"LVL {troop.level} -> {projected_level}   +{gained} LVL   +{gained * 2} ATTR", True, mark_color), (x, draw_rect.top + 34))
        attrs = "  ".join(f"{ATTRIBUTE_SHORT_LABELS[key]} {troop.attribute_value(key)}" for key in ATTRIBUTE_ORDER)
        surface.blit(self.fonts["tiny"].render(attrs, True, dim_color), (x, draw_rect.top + 51))
        required = xp_needed(troop.level)
        self._draw_bar(surface, pygame.Rect(x, draw_rect.bottom - 16, min(180, draw_rect.width - 220), 5), min(1.0, troop.xp / max(1, required)))
        xp_text = self.fonts["tiny"].render(f"{troop.xp}XP  NEXT {required}XP  LEFT {remaining_xp}XP", True, dim_color)
        surface.blit(xp_text, (x + min(190, draw_rect.width - 210), draw_rect.bottom - 21))

    def _draw_tower_level_icon(self, surface: pygame.Surface, rect: pygame.Rect, kind: str, selected: bool, color: tuple[int, int, int] | None = None) -> None:
        palette = config.PALETTE
        color = palette.white if color is None else color
        center = pygame.Vector2(rect.center)
        if kind == "archer":
            pygame.draw.polygon(
                surface,
                color,
                [
                    (rect.centerx, rect.top + 9),
                    (rect.right - 10, rect.bottom - 11),
                    (rect.left + 10, rect.bottom - 11),
                ],
                1,
            )
            pygame.draw.line(surface, color, (rect.left + 12, rect.centery), (rect.right - 12, rect.centery), 1)
        elif kind == "cannon":
            pygame.draw.circle(surface, color, center, max(5, rect.width // 5), 1)
            pygame.draw.line(surface, color, (rect.centerx, rect.top + 8), (rect.centerx, rect.bottom - 8), 1)
            pygame.draw.line(surface, color, (rect.left + 8, rect.centery), (rect.right - 8, rect.centery), 1)
        else:
            pygame.draw.rect(surface, color, rect.inflate(-15, -15), 1)
            pygame.draw.line(surface, color, (rect.left + 12, rect.bottom - 12), (rect.right - 12, rect.top + 12), 1)

    def _panel_inventory_rects(self, content: pygame.Rect, state) -> list[pygame.Rect]:
        capacity = getattr(state.inventory, "capacity", 0)
        if capacity <= 0:
            return []
        cols = min(8, capacity)
        size = 52
        gap = 8
        rects = []
        for index in range(capacity):
            col = index % cols
            row = index // cols
            rects.append(pygame.Rect(content.left + col * (size + gap), content.top + row * (size + gap), size, size))
        return rects

    def _layout_context_buttons(self, screen_rect: pygame.Rect, viewport: pygame.Rect, state) -> list[Button]:
        rect = self._context_rect(screen_rect, viewport)
        return self._layout_context_buttons_for_rect(rect, state)

    def _expedition_party_troops(self, state) -> list:
        run = getattr(state, "expedition_run", None)
        if run is None:
            return []
        return list(getattr(run, "party", ()))

    def _expedition_party_card_rects(self, rect: pygame.Rect, state) -> list[tuple[object, pygame.Rect]]:
        party = self._expedition_party_troops(state)
        if not party:
            return []
        x = rect.left + 16
        width = rect.width - 32
        count = len(party)
        gap = 6
        card_w = max(44, (width - gap * (count - 1)) // count)
        card_h = 52
        top = rect.top + 82
        return [
            (troop, pygame.Rect(x + index * (card_w + gap), top, card_w, card_h))
            for index, troop in enumerate(party)
        ]

    def _inspected_troops(self, state) -> list:
        party = self._expedition_party_troops(state)
        party_ids = {id(troop) for troop in party}
        selected = [
            troop
            for troop in getattr(state, "selected_troops", [])
            if getattr(troop, "alive", False)
        ]
        if party:
            selected = [troop for troop in selected if id(troop) in party_ids]
        if selected:
            return selected
        return []

    def _layout_context_buttons_for_rect(self, rect: pygame.Rect, state) -> list[Button]:
        x = rect.left + 16
        width = rect.width - 32
        buttons: list[Button] = []
        inspected_troops = self._inspected_troops(state)
        expedition_active = getattr(state, "expedition_run", None) is not None
        for troop, card in self._expedition_party_card_rects(rect, state):
            buttons.append(
                Button(
                    card,
                    "",
                    "inspect_expedition_troop",
                    troop,
                    enabled=getattr(troop, "alive", False),
                    selected=troop in inspected_troops,
                    visible=False,
                )
            )
        if state.selected_tower is not None:
            tower = state.selected_tower
            y = rect.top + 462
            if tower.can_level_up():
                buttons.append(Button(pygame.Rect(x, y, width, 28), f"LEVEL UP  {xp_needed(tower.level)}XP", "level_up_tower"))
                y += 36
            if tower.can_specialize():
                for option, label in tower.specialization_options().items():
                    buttons.append(Button(pygame.Rect(x, y, width, 26), label.upper(), "specialize", option))
                    y += 31
            for mod_id, card in self._tower_mod_card_rects(rect):
                enabled = not tower.has_mod(mod_id) and tower.can_install_mod(mod_id)
                buttons.append(Button(card, "", "tower_mod", mod_id, enabled=enabled, visible=False))
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif state.selected_barracks is not None:
            buttons.append(Button(pygame.Rect(x, rect.top + 196, width, 28), "OPEN THIS BARRACKS", "open_context_panel", "units"))
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif state.selected_house is not None:
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif getattr(state, "selected_extractor", None) is not None:
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif getattr(state, "selected_torch", None) is not None:
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif getattr(state, "selected_training_grounds", None) is not None:
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif getattr(state, "selected_expedition_campsite", None) is not None:
            buttons.append(Button(pygame.Rect(x, rect.top + 176, width, 28), "OPEN EXPEDITIONS", "open_context_panel", "expedition"))
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif getattr(state, "selected_hero_hall", None) is not None:
            buttons.append(Button(pygame.Rect(x, rect.top + 176, width, 28), "OPEN HERO HALL", "open_context_panel", "hero"))
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif state.selected_library is not None:
            library = state.selected_library
            busy = library.active_order is not None
            label = "SCRIBE SCROLL"
            if busy:
                label = "SCROLL READY" if library.active_order.ready_item_id is not None else "SCRIBING"
            buttons.append(
                Button(
                    pygame.Rect(x, rect.top + 176, width, 28),
                    label,
                    "library_scroll",
                    enabled=not busy and state.gold >= library.scroll_gold_cost and not state.game_over,
                )
            )
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif state.selected_research is not None:
            buttons.append(Button(pygame.Rect(x, rect.top + 164, width, 28), "OPEN THIS LAB", "open_context_panel", "research"))
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif getattr(state, "selected_shield", None) is not None:
            buttons.append(Button(pygame.Rect(x, rect.bottom - 38, width, 26), "SELL", "sell"))
        elif inspected_troops:
            direct_selection = any(getattr(troop, "alive", False) for troop in getattr(state, "selected_troops", []))
            single = inspected_troops[0] if direct_selection and len(inspected_troops) == 1 else None
            action_height = 30 if expedition_active else (68 if direct_selection else 30)
            if single is not None and single.attribute_points > 0:
                action_height += 32
            if single is not None and single.can_level_up():
                action_height += 36
            if single is not None and getattr(single, "has_hero_tree", lambda: False)():
                action_height += 36
            y = max(rect.top + 192, rect.bottom - action_height - 16)
            if single is not None:
                if single.can_level_up():
                    buttons.append(Button(pygame.Rect(x, y, width, 28), f"LEVEL UP  {xp_needed(single.level)}XP", "level_up_troop"))
                    y += 36
                if single.attribute_points > 0:
                    attr_gap = 4
                    attr_w = max(42, (width - attr_gap * (len(ATTRIBUTE_ORDER) - 1)) // len(ATTRIBUTE_ORDER))
                    for index, attribute in enumerate(ATTRIBUTE_ORDER):
                        attr_rect = pygame.Rect(x + index * (attr_w + attr_gap), y, attr_w, 24)
                        buttons.append(Button(attr_rect, f"+ {ATTRIBUTE_SHORT_LABELS[attribute]}", "troop_attribute", attribute))
                    y += 32
                if getattr(single, "has_hero_tree", lambda: False)():
                    buttons.append(Button(pygame.Rect(x, y, width, 28), f"HERO TREE  {single.hero_orbs} ORB", "open_context_panel", "hero"))
                    y += 36
            if direct_selection and not expedition_active:
                buttons.append(Button(pygame.Rect(x, y, width, 30), "STATION", "station", selected=state.station_mode))
                y += 38
            hold_label = "HOLD FIRE" if any(troop.attack_enabled for troop in inspected_troops) else "ENGAGE"
            buttons.append(Button(pygame.Rect(x, y, width, 30), hold_label, "toggle_attack"))
        elif state.selected_wall is not None:
            buttons.append(Button(pygame.Rect(x, rect.top + 114, width, 28), "SELL WALL", "sell"))
        return buttons

    def _draw_expedition_party_selector(self, surface: pygame.Surface, rect: pygame.Rect, state) -> int:
        cards = self._expedition_party_card_rects(rect, state)
        if not cards:
            return rect.top + 54
        palette = config.PALETTE
        selected_ids = {id(troop) for troop in self._inspected_troops(state)}
        x = rect.left + 16
        self._section(surface, "EXPEDITION PARTY", x, rect.top + 54)
        mouse = self._mouse_pos()
        for troop, card in cards:
            alive = getattr(troop, "alive", False)
            selected = id(troop) in selected_ids
            hovered = alive and card.collidepoint(mouse)
            draw_card = hover_feedback.scaled_rect(card, hovered)
            inverted = selected or hovered
            fill = palette.white if inverted else (palette.black if alive else palette.bg)
            text_color = palette.black if inverted else (palette.text if alive else palette.text_dim)
            dim_color = palette.black if inverted else palette.text_dim
            pygame.draw.rect(surface, fill, draw_card)
            pygame.draw.rect(surface, palette.white if alive else palette.line, draw_card, 1)
            icon = pygame.Rect(draw_card.left + 5, draw_card.top + 5, 20, 20)
            self._draw_unit_glyph(surface, getattr(troop, "kind", ""), icon, inverted=inverted)
            name = str(getattr(troop, "display_name", "Troop")).upper()
            while self.fonts["tiny"].size(name)[0] > draw_card.width - 34 and len(name) > 4:
                name = name[:-2] + "."
            surface.blit(self.fonts["tiny"].render(name, True, text_color), (draw_card.left + 30, draw_card.top + 7))
            hp = max(0.0, float(getattr(troop, "health", 0.0)))
            hp_max = max(1.0, float(getattr(troop, "max_health", 1.0)))
            hp_rect = pygame.Rect(draw_card.left + 6, draw_card.bottom - 13, draw_card.width - 12, 4)
            pygame.draw.rect(surface, palette.black if inverted else palette.panel_2, hp_rect)
            fill_rect = hp_rect.copy()
            fill_rect.width = int(hp_rect.width * min(1.0, hp / hp_max))
            pygame.draw.rect(surface, palette.black if inverted else palette.white, fill_rect)
            stance = "E" if getattr(troop, "attack_enabled", False) else "H"
            surface.blit(self.fonts["tiny"].render(stance, True, dim_color), (draw_card.right - 14, draw_card.top + 25))
        return cards[-1][1].bottom + 16

    def _draw_context_inspector(self, surface: pygame.Surface, screen_rect: pygame.Rect, viewport: pygame.Rect, state) -> None:
        rect = self._context_rect(screen_rect, viewport)
        self._draw_context_inspector_panel(surface, rect, state)

    def _draw_context_inspector_panel(self, surface: pygame.Surface, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        self._alpha_rect(surface, rect, (0, 0, 0, 220))
        pygame.draw.rect(surface, palette.line_bright, rect, 1)
        pygame.draw.rect(surface, palette.panel_2, pygame.Rect(rect.left, rect.top, rect.width, 38))
        pygame.draw.line(surface, palette.line, (rect.left, rect.top + 38), (rect.right, rect.top + 38), 1)
        surface.blit(self.fonts["small"].render("INSPECTOR", True, palette.text), (rect.left + 14, rect.top + 11))

        x = rect.left + 16
        y = rect.top + 54
        expedition_party = [
            troop
            for troop in self._expedition_party_troops(state)
            if getattr(troop, "alive", False)
        ]
        if getattr(state, "expedition_run", None) is not None:
            y = self._draw_expedition_party_selector(surface, rect, state)
        inspected_troops = self._inspected_troops(state)
        if state.selected_tower:
            self._draw_tower_context(surface, x, y, rect, state.selected_tower, state)
        elif state.selected_barracks:
            self._draw_barracks_context(surface, x, y, rect, state.selected_barracks, state)
        elif state.selected_house:
            self._draw_house_context(surface, x, y, rect, state.selected_house, state)
        elif getattr(state, "selected_extractor", None):
            self._draw_extractor_context(surface, x, y, rect, state.selected_extractor, state)
        elif getattr(state, "selected_torch", None):
            self._draw_torch_context(surface, x, y, rect, state.selected_torch, state)
        elif getattr(state, "selected_training_grounds", None):
            self._draw_training_grounds_context(surface, x, y, rect, state.selected_training_grounds, state)
        elif getattr(state, "selected_expedition_campsite", None):
            self._draw_expedition_campsite_context(surface, x, y, rect, state.selected_expedition_campsite, state)
        elif getattr(state, "selected_hero_hall", None):
            self._draw_hero_hall_context(surface, x, y, rect, state.selected_hero_hall, state)
        elif state.selected_library:
            self._draw_library_context(surface, x, y, rect, state.selected_library, state)
        elif state.selected_research:
            self._draw_research_context(surface, x, y, rect, state.selected_research, state)
        elif getattr(state, "selected_shield", None):
            self._draw_shield_context(surface, x, y, rect, state.selected_shield, state)
        elif len(inspected_troops) > 1:
            self._draw_troop_group_context(surface, x, y, rect, inspected_troops, state)
        elif inspected_troops:
            self._draw_troop_context(surface, x, y, rect, inspected_troops[0], state)
        elif expedition_party:
            self._draw_troop_group_context(surface, x, y, rect, expedition_party, state)
        elif state.selected_wall:
            self._draw_wall_context(surface, x, y, rect, state)
        else:
            self._draw_workspace_context(surface, x, y, rect, state)

    def _context_rect(self, screen_rect: pygame.Rect, viewport: pygame.Rect) -> pygame.Rect:
        width = min(360, max(310, viewport.width // 3))
        height = min(640, max(360, viewport.height - 46))
        return pygame.Rect(screen_rect.right - width - 16, config.TOP_BAR_HEIGHT + 16, width, height)

    def _draw_tower_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, tower, state) -> None:
        palette = config.PALETTE
        stats = tower.stats(state)
        self._section(surface, tower.display_name.upper(), x, y)
        y += 26
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("LVL", str(tower.level)),
                ("XP", f"{tower.xp}/{xp_needed(tower.level)}"),
                ("HP", f"{int(tower.health)}/{int(tower.max_health)}"),
                ("KILLS", str(tower.kills)),
                ("DMG", f"{float(stats['damage']):0.1f}"),
                ("RATE", f"{float(stats['fire_rate']):0.2f}"),
                ("RANGE", str(int(float(stats["range"])))),
                ("ACC", f"{int(float(stats['accuracy']) * 100)}%"),
                ("MODE", self._tower_damage_mode_line(tower, stats)),
            ],
        )
        ability_y = rect.top + 336
        if hasattr(tower, "abilities"):
            self._draw_ability_sections(surface, x, ability_y, rect.width - 32, tower.abilities.cards(state), state, max_rows=1)

        level_y = rect.top + 438
        self._section(surface, "LEVELING", x, level_y)
        level_y += 24
        required = xp_needed(tower.level)
        self._draw_bar(surface, pygame.Rect(x, level_y, rect.width - 32, 6), min(1.0, tower.xp / max(1, required)))
        xp_text = self.fonts["tiny"].render(f"XP BANK {tower.xp}/{required}", True, palette.text_dim)
        surface.blit(xp_text, (x, level_y + 11))
        if tower.can_level_up():
            pulse = (math.sin(pygame.time.get_ticks() * 0.008) + 1.0) * 0.5
            ready = pygame.Rect(x + rect.width - 140, level_y + 9, 108, 18)
            pygame.draw.rect(surface, palette.white if pulse > 0.45 else palette.panel_2, ready)
            text_color = palette.black if pulse > 0.45 else palette.white
            surface.blit(self.fonts["tiny"].render("READY", True, text_color), (ready.left + 9, ready.top + 4))

        meta_y = rect.top + 492
        if tower.specialization:
            label = SPECIALIZATIONS.get(tower.kind, {}).get(tower.specialization, tower.specialization)
            surface.blit(self.fonts["tiny"].render(f"SPECIALIZATION {label.upper()}", True, palette.text_dim), (x, meta_y))
        elif tower.can_specialize():
            surface.blit(self.fonts["tiny"].render("SPECIALIZATION AVAILABLE", True, palette.text_dim), (x, meta_y))
        elif stats["effect"]:
            surface.blit(self.fonts["tiny"].render(f"ELEMENT {str(stats['effect']).upper()}", True, palette.text_dim), (x, meta_y))

        self._section(surface, "TOWER MODS", x, rect.top + 522)
        self._draw_tower_mod_cards(surface, rect, tower)

    def _tower_mod_card_rects(self, context_rect: pygame.Rect):
        x = context_rect.left + 16
        y = context_rect.top + 548
        width = context_rect.width - 32
        gap = 5
        card_w = (width - gap) // 2
        card_h = 44
        for index, mod_id in enumerate(TOWER_MODS):
            col = index % 2
            row = index // 2
            rect = pygame.Rect(x + col * (card_w + gap), y + row * (card_h + gap), card_w, card_h)
            if rect.bottom <= context_rect.bottom - 44:
                yield mod_id, rect

    def _draw_tower_mod_cards(self, surface: pygame.Surface, context_rect: pygame.Rect, tower) -> None:
        palette = config.PALETTE
        for mod_id, card in self._tower_mod_card_rects(context_rect):
            definition = TOWER_MODS[mod_id]
            installed = tower.has_mod(mod_id)
            affordable = tower.can_install_mod(mod_id)
            hovered = not installed and affordable and card.collidepoint(self._mouse_pos())
            draw_card = hover_feedback.scaled_rect(card, hovered)
            border = palette.white if hovered or installed else (palette.line_bright if affordable else palette.line)
            fill = palette.white if hovered else (palette.panel_2 if installed else palette.black)
            text_color = palette.black if hovered else (palette.text if not installed else palette.white)
            dim_color = palette.black if hovered else (palette.text_dim if not affordable and not installed else palette.white)
            pygame.draw.rect(surface, fill, draw_card)
            pygame.draw.rect(surface, border, draw_card, 1)
            title = definition.name.upper()
            title_img = self.fonts["tiny"].render(title[:18], True, text_color)
            surface.blit(title_img, (draw_card.left + 8, draw_card.top + 5))
            status = "INSTALLED" if installed else f"{definition.xp_cost}XP"
            status_img = self.fonts["tiny"].render(status, True, dim_color)
            surface.blit(status_img, (draw_card.left + 8, draw_card.top + 19))
            y = draw_card.top + 31
            for line in self._wrap(definition.description, self.fonts["tiny"], draw_card.width - 16)[:1]:
                text = line
                while self.fonts["tiny"].size(text)[0] > draw_card.width - 16 and len(text) > 4:
                    text = text[:-2] + "."
                surface.blit(self.fonts["tiny"].render(text, True, palette.black if hovered else palette.text_dim), (draw_card.left + 8, y))
                y += 14

    def _draw_barracks_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, barracks, state) -> None:
        self._section(surface, "BARRACKS", x, y)
        y += 26
        queue_line = "IDLE"
        if barracks.train_queue:
            order = barracks.train_queue[0]
            queue_line = f"{TROOP_NAMES[order.kind].upper()} {order.remaining:0.1f}S"
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(barracks.health)}/{int(barracks.max_health)}"),
                ("QUEUE", f"{len(barracks.train_queue)}/{barracks.queue_limit}"),
                ("SUPPLY", f"{state.troop_supply_committed()}/{state.troop_capacity()}"),
                ("TRAINING", queue_line),
            ],
        )

    def _draw_house_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, house, state) -> None:
        self._section(surface, "HOUSE", x, y)
        y += 26
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(house.health)}/{int(house.max_health)}"),
                ("CAPACITY", f"+{HOUSE_CAPACITY}"),
                ("SUPPLY", f"{state.troop_supply_committed()}/{state.troop_capacity()}"),
            ],
        )

    def _draw_extractor_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, extractor, state) -> None:
        self._section(surface, "EXTRACTOR", x, y)
        y += 26
        deposit = extractor.deposit
        route = state.arcane_link_for(extractor)
        path_len = len(route.path) if route is not None else 0
        status = "ACTIVE" if deposit.active else f"RESPAWN {deposit.respawn_time:0.0f}S"
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(extractor.health)}/{int(extractor.max_health)}"),
                (deposit.display_name.upper(), f"{deposit.amount}/{deposit.max_amount}"),
                ("STATUS", status),
                ("ROUTE", f"{path_len} TILES"),
            ],
        )
        y += 122
        self._section(surface, "RESOURCE PATH", x, y)
        y += 26
        for line in (
            f"Arcane route marks the core-to-{deposit.display_name.lower()} hauling lane.",
            "Station grunts near the extractor to work this deposit.",
        ):
            for wrapped in self._wrap(line, self.fonts["tiny"], rect.width - 32):
                surface.blit(self.fonts["tiny"].render(wrapped, True, config.PALETTE.text_dim), (x, y))
                y += 16

    def _draw_torch_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, torch, state) -> None:
        self._section(surface, "TORCH", x, y)
        y += 26
        nearby = state.targetable_enemies_near(torch.pos, torch.aggro_radius) if hasattr(state, "targetable_enemies_near") else state.nearby_enemies(torch.pos, torch.aggro_radius)
        enemies = len(nearby)
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(torch.health)}/{int(torch.max_health)}"),
                ("RADIUS", str(int(torch.aggro_radius))),
                ("THREAT", "HEAVY"),
                ("ENEMIES", str(enemies)),
            ],
        )

    def _draw_training_grounds_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, grounds, state) -> None:
        self._section(surface, "TRAINING GROUNDS", x, y)
        y += 26
        trainees = len(state.nearby_troops(grounds.pos, grounds.training_radius))
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(grounds.health)}/{int(grounds.max_health)}"),
                ("RADIUS", str(int(grounds.training_radius))),
                ("TRAINEES", f"{min(trainees, grounds.max_trainees)}/{grounds.max_trainees}"),
                ("XP", f"+{grounds.xp_amount}/{grounds.xp_interval:0.0f}S"),
            ],
        )

    def _draw_expedition_campsite_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, campsite, state) -> None:
        self._section(surface, "EXPEDITION CAMP", x, y)
        y += 26
        ready_groups = sum(1 for index in range(len(getattr(state, "control_groups", []))) if state.control_group_troops(index))
        active = getattr(state, "expedition_run", None) is not None
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(campsite.health)}/{int(campsite.max_health)}"),
                ("GROUPS", str(ready_groups)),
                ("PARTY", f"{len(getattr(state, 'expedition_setup_party', []))}/5"),
                ("STATUS", "ACTIVE" if active else "READY"),
            ],
        )

    def _draw_hero_hall_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, hall, state) -> None:
        self._section(surface, "HERO HALL", x, y)
        y += 26
        hero_troops = [
            troop
            for troop in getattr(state, "troops", [])
            if getattr(troop, "alive", False) and getattr(troop, "has_hero_tree", lambda: False)()
        ]
        ready_orbs = sum(int(getattr(troop, "hero_orbs", 0)) for troop in hero_troops)
        spent_orbs = sum(int(getattr(troop, "hero_spent_orbs", lambda: 0)()) for troop in hero_troops)
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(hall.health)}/{int(hall.max_health)}"),
                ("TROOPS", str(len(hero_troops))),
                ("READY ORBS", str(ready_orbs)),
                ("SPENT", str(spent_orbs)),
            ],
        )

    def _draw_library_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, library, state) -> None:
        self._section(surface, "LIBRARY", x, y)
        y += 26
        order_state = "IDLE"
        progress = 0.0
        if library.active_order is not None:
            if library.active_order.ready_item_id is not None:
                order_state = "READY"
                progress = 1.0
            else:
                progress = 1.0 - max(0.0, library.active_order.remaining / max(0.01, library.active_order.total))
                order_state = f"{progress * 100:0.0f}%"
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(library.health)}/{int(library.max_health)}"),
                ("COST", f"{library.scroll_gold_cost}G"),
                ("TIME BONUS", f"{state.research.bonus_percent('scroll_production_time')}%"),
                ("SCRIBE", order_state),
            ],
        )
        if library.active_order is not None:
            self._draw_bar(surface, pygame.Rect(x, y + 92, rect.width - 32, 5), progress)

    def _draw_research_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, research, state) -> None:
        self._section(surface, "RESEARCH LAB", x, y)
        y += 26
        work = "IDLE"
        progress = 0.0
        if research.active_order is not None:
            definition = RESEARCH_DEFINITIONS[research.active_order.research_id]
            progress = 1.0 - max(0.0, research.active_order.remaining / max(0.01, research.active_order.total))
            work = f"{definition.name.upper()} {progress * 100:0.0f}%"
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("HP", f"{int(research.health)}/{int(research.max_health)}"),
                ("TIME BONUS", f"{state.research.bonus_percent('research_time')}%"),
                ("WORK", work),
            ],
        )
        if research.active_order is not None:
            self._draw_bar(surface, pygame.Rect(x, y + 74, rect.width - 32, 5), progress)

    def _draw_shield_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, shield, state) -> None:
        self._section(surface, "SHIELD GENERATOR", x, y)
        y += 26
        network = state.connected_structure_cells(shield.cell)
        shield.set_network(network, state.connected_structure_count(network))
        status = "ACTIVE" if shield.shield_active else ("RECHARGING" if shield.recharging else "BROKEN")
        entries = [
            ("HP", f"{int(shield.health)}/{int(shield.max_health)}"),
            ("SHIELD", f"{int(shield.shield)}/{int(shield.shield_max)}"),
            ("STATUS", status),
            ("LINKED", str(shield.network_structure_count)),
        ]
        if shield.recharging:
            entries.append(("TIME", f"{shield.recharge_remaining:0.1f}S"))
        self._draw_stat_grid(surface, x, y, rect.width - 32, entries)
        y += 122
        self._section(surface, "NETWORK", x, y)
        y += 26
        lines = [
            "Connected structures share one shield pool.",
            "After a break, the generator spends health for 20 seconds to rebuild the shield.",
            "Rune Mages can recharge this pool directly.",
        ]
        for line in lines:
            for wrapped in self._wrap(line, self.fonts["tiny"], rect.width - 32):
                surface.blit(self.fonts["tiny"].render(wrapped, True, config.PALETTE.text_dim), (x, y))
                y += 16

    def _draw_troop_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, troop, state) -> None:
        stats = troop.stats(state)
        self._section(surface, troop.display_name.upper(), x, y)
        y += 26
        entries = [
            ("LVL", str(troop.level)),
            ("XP", f"{troop.xp}/{xp_needed(troop.level)}"),
            ("PTS", str(troop.attribute_points)),
            ("HP", f"{int(troop.health)}/{int(troop.max_health)}"),
            ("STANCE", "ENGAGE" if troop.attack_enabled else "HOLD"),
            ("DMG", f"{float(stats['damage']):0.1f}"),
            ("RATE", f"{float(stats['fire_rate']):0.2f}"),
            ("RANGE", str(int(float(stats["range"])))),
            ("CD", f"{float(stats['ability_cooldown']):0.2f}X"),
            ("MOVE", str(int(float(stats.get("movement_speed", 0.0))))),
        ]
        if getattr(troop, "has_hero_tree", lambda: False)():
            entries.extend(
                [
                    ("ORBS", str(troop.hero_orbs)),
                    ("ASCEND", str(troop.hero_spent_orbs())),
                    ("CRIT", f"{int(float(stats.get('crit_chance', 0.0)) * 100)}%"),
                    ("ARMOR", f"{int(float(stats.get('damage_reduction', 0.0)) * 100)}%"),
                ]
            )
        if troop.harvester is not None:
            entries.extend(
                [
                    ("CARGO", f"{troop.harvester.cargo}/{troop.harvester.carry_capacity_for(state)}"),
                    ("TASK", troop.harvester.state.upper()),
                ]
            )
        self._draw_stat_grid(surface, x, y, rect.width - 32, entries)
        rows = math.ceil(len(entries) / 2)
        y += rows * 50 + 16
        self._section(surface, "ATTRIBUTES", x, y)
        y += 26
        attr_entries = [
            (ATTRIBUTE_SHORT_LABELS[attribute], str(troop.attribute_value(attribute)))
            for attribute in ATTRIBUTE_ORDER
        ]
        attr_entries.append(("LEASH", str(int(float(stats.get("station_range", troop.station_range))))))
        self._draw_stat_grid(surface, x, y, rect.width - 32, attr_entries)
        y += math.ceil(len(attr_entries) / 2) * 50 + 16
        if hasattr(troop, "abilities"):
            self._draw_ability_sections(surface, x, y, rect.width - 32, troop.abilities.cards(state), state, max_rows=2)

    def _draw_troop_group_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, troops, state) -> None:
        self._section(surface, "TROOP GROUP", x, y)
        counts = [(TROOP_NAMES[kind].upper(), sum(1 for troop in troops if troop.kind == kind)) for kind in TROOP_NAMES]
        health = sum(max(0, troop.health) for troop in troops)
        max_health = sum(troop.max_health for troop in troops)
        avg_level = sum(troop.level for troop in troops) / max(1, len(troops))
        entries = [
            ("SELECTED", str(len(troops))),
            ("HP", f"{int(health)}/{int(max_health)}"),
            ("AVG LVL", f"{avg_level:0.1f}"),
            ("ENGAGE", str(sum(1 for troop in troops if troop.attack_enabled))),
            ("HOLD", str(sum(1 for troop in troops if not troop.attack_enabled))),
        ]
        entries.extend((name, str(count)) for name, count in counts if count)
        self._draw_stat_grid(surface, x, y + 26, rect.width - 32, entries)

    def _draw_wall_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, state) -> None:
        self._section(surface, "WALL", x, y)
        health = state.grid.wall_health.get(state.selected_wall, state.grid.wall_max_health)
        self._draw_stat_grid(surface, x, y + 26, rect.width - 32, [("HP", f"{int(health)}/{int(state.grid.wall_max_health)}"), ("TILE", str(state.selected_wall))])

    def _draw_workspace_context(self, surface: pygame.Surface, x: int, y: int, rect: pygame.Rect, state) -> None:
        palette = config.PALETTE
        self._section(surface, "WORKSPACE", x, y)
        y += 28
        mode = state.build_mode.upper() if state.build_mode else ("STATION" if state.station_mode else "SELECT")
        self._draw_stat_grid(
            surface,
            x,
            y,
            rect.width - 32,
            [
                ("TOOL", mode),
                ("PANEL", self.active_panel.upper() if self.active_panel else "NONE"),
                ("NIGHT", str(state.wave_manager.night_number)),
            ],
        )
        y += 100
        text = [
            "Use the left rail to open workspace panels.",
            "Select structures or troops to expose actions here.",
        ]
        for line in text:
            for wrapped in self._wrap(line, self.fonts["tiny"], rect.width - 32):
                surface.blit(self.fonts["tiny"].render(wrapped, True, palette.text_dim), (x, y))
                y += 16

    def _draw_stat_grid(self, surface: pygame.Surface, x: int, y: int, width: int, entries: list[tuple[str, str]]) -> None:
        palette = config.PALETTE
        columns = 2
        gap = 8
        cell_w = (width - gap) // columns
        cell_h = 42
        for index, (label, value) in enumerate(entries):
            col = index % columns
            row = index // columns
            rect = pygame.Rect(x + col * (cell_w + gap), y + row * (cell_h + gap), cell_w, cell_h)
            pygame.draw.rect(surface, palette.black, rect)
            pygame.draw.rect(surface, palette.line, rect, 1)
            surface.blit(self.fonts["tiny"].render(label.upper(), True, palette.text_dim), (rect.left + 8, rect.top + 5))
            value_text = str(value).upper()
            font = self.fonts["small"]
            while font.size(value_text)[0] > rect.width - 14 and len(value_text) > 4:
                value_text = value_text[:-2] + "."
            surface.blit(font.render(value_text, True, palette.text), (rect.left + 8, rect.top + 21))

    def _draw_ability_sections(self, surface: pygame.Surface, x: int, y: int, width: int, cards, state, max_rows: int = 2) -> int:
        active = [card for card in cards if not getattr(card, "passive", False)]
        passive = [card for card in cards if getattr(card, "passive", False)]
        if active:
            self._section(surface, "ABILITIES", x, y)
            y = self._draw_ability_card_grid(surface, active, x, y + 25, width, max_rows=max_rows) + 12
        if passive:
            self._section(surface, "PASSIVES", x, y)
            y = self._draw_ability_card_grid(surface, passive, x, y + 25, width, max_rows=max_rows) + 12
        return y

    def _draw_ability_card_grid(self, surface: pygame.Surface, cards, x: int, y: int, width: int, max_rows: int = 2) -> int:
        columns = 2
        gap = 6
        card_h = 34
        card_w = (width - gap) // columns
        max_cards = columns * max_rows
        for index, card in enumerate(cards[:max_cards]):
            col = index % columns
            row = index // columns
            rect = pygame.Rect(x + col * (card_w + gap), y + row * (card_h + gap), card_w, card_h)
            self._draw_ability_card(surface, rect, card)
        hidden = len(cards) - max_cards
        rows = max(1, math.ceil(min(len(cards), max_cards) / columns))
        if hidden > 0:
            text = self.fonts["tiny"].render(f"+{hidden} MORE", True, config.PALETTE.text_dim)
            surface.blit(text, (x, y + rows * (card_h + gap) - 2))
            rows += 1
        return y + rows * (card_h + gap)

    def _draw_ability_chip_row(self, surface: pygame.Surface, cards, x: int, y: int, max_width: int, max_count: int = 4) -> int:
        cursor = x
        row_h = 19
        for card in cards[:max_count]:
            label = str(getattr(card, "name", "Ability")).upper()
            width = min(max_width, max(58, self.fonts["tiny"].size(label)[0] + 18))
            if cursor + width > x + max_width:
                break
            rect = pygame.Rect(cursor, y, width, row_h)
            self._draw_ability_card(surface, rect, card, compact=True)
            cursor += width + 5
        hidden = len(cards) - max_count
        if hidden > 0 and cursor + 36 <= x + max_width:
            surface.blit(self.fonts["tiny"].render(f"+{hidden}", True, config.PALETTE.text_dim), (cursor + 2, y + 4))
        return row_h

    def _draw_ability_card(self, surface: pygame.Surface, rect: pygame.Rect, card, compact: bool = False) -> None:
        palette = config.PALETTE
        hovered = rect.collidepoint(self._mouse_pos())
        passive = bool(getattr(card, "passive", False))
        fill = palette.white if hovered else (palette.panel_2 if passive else palette.black)
        border = palette.white if hovered else (palette.line_bright if not passive else palette.line)
        text_color = palette.black if hovered else palette.text
        dim_color = palette.black if hovered else palette.text_dim
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, border, rect, 1)
        label = str(getattr(card, "name", "Ability")).upper()
        font = self.fonts["tiny"]
        while font.size(label)[0] > rect.width - 12 and len(label) > 4:
            label = label[:-2] + "."
        surface.blit(font.render(label, True, text_color), (rect.left + 6, rect.top + (5 if compact else 6)))
        if not compact:
            state = str(getattr(card, "state", ""))
            if state:
                while font.size(state)[0] > rect.width - 12 and len(state) > 4:
                    state = state[:-2] + "."
                surface.blit(font.render(state.upper(), True, dim_color), (rect.left + 6, rect.top + 19))
        if hovered:
            self.tooltip_request = TooltipRequest(card, self._mouse_pos())

    def _draw_pending_tooltip(self, surface: pygame.Surface, bounds: pygame.Rect) -> None:
        if self.tooltip_request is None:
            return
        card = self.tooltip_request.card
        mouse_pos = self.tooltip_request.mouse_pos
        width, height, details, description_lines = self._tooltip_content(card, bounds.width - 16)
        rect = pygame.Rect(mouse_pos[0] + 16, mouse_pos[1] - 8, width, height)
        self._fit_tooltip_rect(rect, mouse_pos, bounds)
        self._draw_tooltip_box(surface, rect, card, details, description_lines)

    def _draw_fixed_tooltip(self, surface: pygame.Surface, bounds: pygame.Rect) -> None:
        if self.tooltip_request is None:
            return
        card = self.tooltip_request.card
        width, height, details, description_lines = self._tooltip_content(card, bounds.width - 16)
        rect = pygame.Rect(bounds.left + 8, bounds.top + 8, width, height)
        rect.right = min(rect.right, bounds.right - 8)
        rect.left = max(bounds.left + 8, rect.left)
        if rect.bottom > bounds.bottom - 8:
            rect.height = max(1, bounds.bottom - rect.top - 8)
        self._draw_tooltip_box(surface, rect, card, details, description_lines)

    def _tooltip_content(self, card, max_width: int = 300) -> tuple[int, int, list[str], list[str]]:
        width = max(1, min(300, int(max_width)))
        details = [str(line).upper() for line in getattr(card, "details", ())]
        tags = tuple(getattr(card, "tags", ()))
        if tags:
            details.append("TAGS " + ", ".join(str(tag).upper() for tag in tags[:3]))
        description = str(getattr(card, "description", ""))
        description_lines = self._wrap(description, self.fonts["tiny"], max(1, width - 24))
        height = 34 + 18 * len(details) + 16 * len(description_lines) + 18
        return width, height, details, description_lines

    def _fit_tooltip_rect(self, rect: pygame.Rect, anchor: tuple[int, int], bounds: pygame.Rect) -> None:
        if rect.right > bounds.right - 8:
            rect.right = anchor[0] - 14
        if rect.bottom > bounds.bottom - 8:
            rect.bottom = anchor[1] - 14
        if rect.top < bounds.top + 8:
            rect.top = bounds.top + 8
        if rect.left < bounds.left + 8:
            rect.left = bounds.left + 8

    def _draw_tooltip_box(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        card,
        details: list[str],
        description_lines: list[str],
    ) -> None:
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        previous_clip = surface.get_clip()
        surface.set_clip(rect.clip(previous_clip))
        y = rect.top + 10
        title = self.fonts["small"].render(str(getattr(card, "name", "Ability")).upper(), True, config.PALETTE.white)
        surface.blit(title, (rect.left + 12, y))
        y += 24
        for line in details:
            surface.blit(self.fonts["tiny"].render(line, True, config.PALETTE.white), (rect.left + 12, y))
            y += 18
        y += 2
        for line in description_lines:
            surface.blit(self.fonts["tiny"].render(line, True, config.PALETTE.white), (rect.left + 12, y))
            y += 16
        surface.set_clip(previous_clip)

    def _tower_damage_mode_line(self, tower, stats: dict) -> str:
        aoe = float(stats["aoe"])
        projectile_count = max(1, int(round(tower.mod_effect("projectile_count_multiplier", 1.0))))
        if aoe > 0:
            return f"AOE {int(aoe)}"
        if projectile_count > 1 or stats.get("effect") == "chain":
            return f"MULTI {projectile_count}"
        return "SINGLE"

    def _section(self, surface: pygame.Surface, text: str, x: int, y: int) -> None:
        surface.blit(self.fonts["small"].render(text, True, config.PALETTE.text), (x, y))
        pygame.draw.line(surface, config.PALETTE.line_bright, (x, y + 20), (x + 118, y + 20), 1)

    def _draw_unit_glyph(self, surface: pygame.Surface, kind: str, rect: pygame.Rect, inverted: bool = False) -> None:
        palette = config.PALETTE
        fill = palette.white if inverted else palette.black
        mark = palette.black if inverted else palette.white
        border = palette.black if inverted else palette.line_bright
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, border, rect, 1)
        center = pygame.Vector2(rect.center)
        r = min(rect.width, rect.height) // 3
        if kind == "warrior":
            points = [(center.x, center.y - r), (center.x + r, center.y), (center.x + r * 0.45, center.y + r), (center.x - r * 0.45, center.y + r), (center.x - r, center.y)]
            pygame.draw.polygon(surface, mark, points, 1)
        elif kind == "archer":
            pygame.draw.arc(surface, mark, pygame.Rect(center.x - r, center.y - r, r * 1.35, r * 2), -math.pi * 0.48, math.pi * 0.48, 2)
            pygame.draw.line(surface, mark, (center.x - r * 0.38, center.y - r * 0.78), (center.x - r * 0.38, center.y + r * 0.78), 1)
            pygame.draw.line(surface, mark, (center.x - r * 0.78, center.y), (center.x + r * 0.78, center.y), 1)
        elif kind == "cleric":
            pygame.draw.circle(surface, mark, center, r, 1)
            pygame.draw.line(surface, mark, (center.x - r * 0.6, center.y), (center.x + r * 0.6, center.y), 2)
            pygame.draw.line(surface, mark, (center.x, center.y - r * 0.6), (center.x, center.y + r * 0.6), 2)
        elif kind == "wizard":
            points = [(center.x, center.y - r), (center.x + r, center.y), (center.x, center.y + r), (center.x - r, center.y)]
            pygame.draw.polygon(surface, mark, points, 1)
            pygame.draw.line(surface, mark, (center.x - r * 0.4, center.y + r * 0.25), (center.x + r * 0.45, center.y - r * 0.35), 1)
        elif kind == "rune_mage":
            points = [
                (center.x, center.y - r),
                (center.x + r, center.y - r * 0.1),
                (center.x + r * 0.45, center.y + r),
                (center.x - r * 0.45, center.y + r),
                (center.x - r, center.y - r * 0.1),
            ]
            pygame.draw.polygon(surface, mark, points, 1)
            box = pygame.Rect(0, 0, max(5, r), max(5, r))
            box.center = center
            pygame.draw.rect(surface, mark, box, 1)
        elif kind == "engineer":
            box = pygame.Rect(0, 0, r * 2, r * 2)
            box.center = center
            pygame.draw.rect(surface, mark, box, 1)
            pygame.draw.line(surface, mark, (box.left + 4, box.bottom - 5), (box.right - 4, box.top + 5), 2)
        else:
            pygame.draw.circle(surface, mark, center, r, 1)
            pygame.draw.line(surface, mark, (center.x - r * 0.8, center.y), (center.x + r * 0.8, center.y), 1)

    def _event_card_rects(self, viewport: pygame.Rect, state):
        choices = state.round_events.current_choices
        count = max(1, len(choices))
        gap = 18
        margin = 34
        card_w = min(270, max(170, (viewport.width - margin * 2 - gap * (count - 1)) // count))
        card_h = 176
        total_w = card_w * count + gap * (count - 1)
        start_x = viewport.centerx - total_w // 2
        y = viewport.centery - card_h // 2 + 18
        for index, event in enumerate(choices):
            yield event, pygame.Rect(start_x + index * (card_w + gap), y, card_w, card_h)

    def _draw_round_event_modal(self, surface: pygame.Surface, viewport: pygame.Rect, state) -> None:
        palette = config.PALETTE
        overlay = pygame.Surface(viewport.size, pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 172))
        surface.blit(overlay, viewport.topleft)

        title = self.fonts["medium"].render("THE VEIL SPEAKS", True, palette.white)
        surface.blit(title, title.get_rect(center=(viewport.centerx, viewport.centery - 126)))
        subtitle = self.fonts["small"].render("The bargain will not name its price.", True, palette.text_dim)
        surface.blit(subtitle, subtitle.get_rect(center=(viewport.centerx, viewport.centery - 100)))

        for event, rect in self._event_card_rects(viewport, state):
            pygame.draw.rect(surface, palette.black, rect)
            pygame.draw.rect(surface, palette.white, rect, 1)
            self._draw_corner_brackets(surface, rect.inflate(-6, -6), palette.white)
            heading = self.fonts["tiny"].render("OMEN", True, palette.text_dim)
            surface.blit(heading, (rect.left + 16, rect.top + 16))
            y = rect.top + 52
            omen = getattr(event, "omen", getattr(event, "title", "A nameless door waits."))
            for line in self._wrap(str(omen).upper(), self.fonts["small"], rect.width - 32):
                image = self.fonts["small"].render(line, True, palette.text)
                surface.blit(image, (rect.left + 16, y))
                y += 22

    def _inventory_slot_rects(self, viewport: pygame.Rect, state) -> list[pygame.Rect]:
        capacity = getattr(state.inventory, "capacity", 0)
        if capacity <= 0:
            return []
        cols = min(8, capacity)
        rows = (capacity + cols - 1) // cols
        size = 34
        gap = 5
        total_w = cols * size + (cols - 1) * gap
        total_h = rows * size + (rows - 1) * gap
        left = viewport.centerx - total_w // 2
        left = max(viewport.left + 14, min(left, viewport.right - total_w - 14))
        top = viewport.bottom - total_h - 14
        rects = []
        for index in range(capacity):
            col = index % cols
            row = index // cols
            rects.append(pygame.Rect(left + col * (size + gap), top + row * (size + gap), size, size))
        return rects

    def _control_group_slot_rects(self, viewport: pygame.Rect, state) -> list[pygame.Rect]:
        groups = getattr(state, "control_groups", [])
        if not groups:
            return []
        inventory_rects = self._inventory_slot_rects(viewport, state)
        size = 34
        gap = 5
        side_gap = 18
        count = min(5, len(groups))
        total_w = count * size + (count - 1) * gap
        top = inventory_rects[0].top if inventory_rects else viewport.bottom - size - 14
        if inventory_rects:
            left = inventory_rects[0].left - side_gap - total_w
            if left < viewport.left + 14:
                left = inventory_rects[-1].right + side_gap
            if left + total_w > viewport.right - 14:
                left = max(viewport.left + 14, viewport.right - total_w - 14)
        else:
            left = viewport.centerx - total_w // 2
        return [pygame.Rect(left + index * (size + gap), top, size, size) for index in range(count)]

    def _control_group_slot_at(self, pos: tuple[int, int], viewport: pygame.Rect, state) -> int | None:
        for index, rect in enumerate(self._control_group_slot_rects(viewport, state)):
            if rect.collidepoint(pos):
                return index
        return None

    def _inventory_slot_at(self, pos: tuple[int, int], viewport: pygame.Rect, state) -> int | None:
        for index, rect in enumerate(self._inventory_slot_rects(viewport, state)):
            if rect.collidepoint(pos):
                return index
        return None

    def _troop_item_layout(self, viewport: pygame.Rect, state):
        troop = self._selected_troop_for_items(state)
        if troop is None:
            return None
        player_rects = self._inventory_slot_rects(viewport, state)
        if not player_rects:
            return None
        size = 30
        gap = 5
        bottom_cols = 5
        total_w = bottom_cols * size + (bottom_cols - 1) * gap
        top = player_rects[0].top
        left = player_rects[-1].right + 18
        if left + total_w > viewport.right - 14:
            left = player_rects[0].left - 18 - total_w
        if left < viewport.left + 14:
            left = max(viewport.left + 14, viewport.right - total_w - 14)
            top = max(viewport.top + 14, player_rects[0].top - 74)
        equipment_left = left + (total_w - (3 * size + 2 * gap)) // 2
        equipment = [pygame.Rect(equipment_left + index * (size + gap), top, size, size) for index in range(3)]
        inventory_top = top + size + gap
        inventory = [pygame.Rect(left + index * (size + gap), inventory_top, size, size) for index in range(5)]
        grid = equipment[0].copy()
        for rect in equipment[1:] + inventory:
            grid.union_ip(rect)
        return troop, equipment, inventory, grid

    def _troop_equipment_slot_rects(self, viewport: pygame.Rect, state) -> list[pygame.Rect]:
        layout = self._troop_item_layout(viewport, state)
        return [] if layout is None else layout[1]

    def _troop_inventory_slot_rects(self, viewport: pygame.Rect, state) -> list[pygame.Rect]:
        layout = self._troop_item_layout(viewport, state)
        return [] if layout is None else layout[2]

    def _troop_equipment_slot_at(self, pos: tuple[int, int], viewport: pygame.Rect, state) -> int | None:
        for index, rect in enumerate(self._troop_equipment_slot_rects(viewport, state)):
            if rect.collidepoint(pos):
                return index
        return None

    def _troop_inventory_slot_at(self, pos: tuple[int, int], viewport: pygame.Rect, state) -> int | None:
        for index, rect in enumerate(self._troop_inventory_slot_rects(viewport, state)):
            if rect.collidepoint(pos):
                return index
        return None

    def _draw_control_groups(self, surface: pygame.Surface, viewport: pygame.Rect, state) -> None:
        rects = self._control_group_slot_rects(viewport, state)
        if not rects:
            return
        palette = config.PALETTE
        grid_rect = rects[0].copy()
        for rect in rects[1:]:
            grid_rect.union_ip(rect)
        back = grid_rect.inflate(12, 28)
        back.top -= 18
        self._alpha_rect(surface, back, (0, 0, 0, 218))
        pygame.draw.rect(surface, palette.line_bright, back, 1)
        label = self.fonts["tiny"].render("GROUPS", True, palette.text_dim)
        surface.blit(label, (grid_rect.left, back.top + 5))

        for index, rect in enumerate(rects):
            troops = state.control_group_troops(index)
            hovered = rect.collidepoint(self._mouse_pos())
            draw_rect = hover_feedback.scaled_rect(rect, hovered)
            pygame.draw.rect(surface, palette.white if hovered else palette.bg, draw_rect)
            pygame.draw.rect(surface, palette.black if hovered else (palette.line_bright if troops else palette.line), draw_rect, 1)
            number = self.fonts["small"].render(str(index + 1), True, palette.black if hovered else (palette.white if troops else palette.text_dim))
            surface.blit(number, number.get_rect(center=draw_rect.center))
            if not troops:
                pygame.draw.line(surface, palette.black if hovered else palette.line, (draw_rect.left + 7, draw_rect.bottom - 7), (draw_rect.right - 7, draw_rect.top + 7), 1)
                continue
            count = self.fonts["tiny"].render(str(len(troops)), True, palette.black if hovered else palette.white)
            surface.blit(count, count.get_rect(bottomright=(draw_rect.right - 3, draw_rect.bottom - 2)))

    def _draw_inventory(self, surface: pygame.Surface, viewport: pygame.Rect, state) -> None:
        rects = self._inventory_slot_rects(viewport, state)
        if not rects:
            return
        palette = config.PALETTE
        grid_rect = rects[0].copy()
        for rect in rects[1:]:
            grid_rect.union_ip(rect)
        back = grid_rect.inflate(12, 28)
        back.top -= 18
        self._alpha_rect(surface, back, (0, 0, 0, 218))
        pygame.draw.rect(surface, palette.line_bright, back, 1)
        label = self.fonts["tiny"].render("INVENTORY", True, palette.text_dim)
        surface.blit(label, (grid_rect.left, back.top + 5))

        for index, rect in enumerate(rects):
            slot = state.inventory.slot(index)
            hovered = slot is not None and rect.collidepoint(self._mouse_pos())
            draw_rect = hover_feedback.scaled_rect(rect, hovered)
            pygame.draw.rect(surface, palette.white if hovered else palette.bg, draw_rect)
            pygame.draw.rect(surface, palette.black if hovered else (palette.line_bright if slot else palette.line), draw_rect, 1)
            if slot is None:
                pygame.draw.line(surface, palette.line, (draw_rect.left + 7, draw_rect.bottom - 7), (draw_rect.right - 7, draw_rect.top + 7), 1)
                continue
            definition = ITEM_DEFINITIONS.get(slot.item_id)
            if definition is not None:
                self._draw_item_icon(surface, draw_rect, definition, slot.quantity, hovered)

    def _draw_troop_inventory(self, surface: pygame.Surface, viewport: pygame.Rect, state) -> None:
        layout = self._troop_item_layout(viewport, state)
        if layout is None:
            return
        troop, equipment_rects, inventory_rects, grid_rect = layout
        palette = config.PALETTE
        buff_h = 30 if troop.active_item_buffs else 0
        back = grid_rect.inflate(12, 28 + buff_h)
        back.top -= 18 + buff_h
        self._alpha_rect(surface, back, (0, 0, 0, 218))
        pygame.draw.rect(surface, palette.line_bright, back, 1)
        label = self.fonts["tiny"].render(troop.display_name.upper()[:18], True, palette.text_dim)
        surface.blit(label, (grid_rect.left, back.top + 5))

        if troop.active_item_buffs:
            self._draw_troop_buff_icons(surface, pygame.Rect(grid_rect.left, back.top + 20, grid_rect.width, 24), troop)

        for index, rect in enumerate(equipment_rects):
            slot = troop.equipment_slots[index]
            hovered = rect.collidepoint(self._mouse_pos())
            draw_rect = hover_feedback.scaled_rect(rect, hovered)
            pygame.draw.rect(surface, palette.white if hovered else palette.panel_2, draw_rect)
            pygame.draw.rect(surface, palette.black if hovered else (palette.line_bright if slot else palette.line), draw_rect, 1)
            if slot is None:
                surface.blit(self.fonts["tiny"].render("EQ", True, palette.black if hovered else palette.text_dim), (draw_rect.left + 8, draw_rect.top + 9))
                pygame.draw.line(surface, palette.black if hovered else palette.line, (draw_rect.left + 7, draw_rect.bottom - 7), (draw_rect.right - 7, draw_rect.top + 7), 1)
                continue
            definition = ITEM_DEFINITIONS.get(slot.item_id)
            if definition is not None:
                self._draw_item_icon(surface, draw_rect, definition, slot.quantity, hovered)

        for index, rect in enumerate(inventory_rects):
            slot = troop.inventory.slot(index)
            hovered = rect.collidepoint(self._mouse_pos())
            draw_rect = hover_feedback.scaled_rect(rect, hovered)
            pygame.draw.rect(surface, palette.white if hovered else palette.bg, draw_rect)
            pygame.draw.rect(surface, palette.black if hovered else (palette.line_bright if slot else palette.line), draw_rect, 1)
            if slot is None:
                pygame.draw.line(surface, palette.line, (draw_rect.left + 7, draw_rect.bottom - 7), (draw_rect.right - 7, draw_rect.top + 7), 1)
                continue
            definition = ITEM_DEFINITIONS.get(slot.item_id)
            if definition is not None:
                self._draw_item_icon(surface, draw_rect, definition, slot.quantity, hovered)

    def _draw_troop_buff_icons(self, surface: pygame.Surface, rect: pygame.Rect, troop) -> None:
        palette = config.PALETTE
        size = 23
        gap = 5
        for index, buff in enumerate(troop.active_item_buffs[:5]):
            icon = pygame.Rect(rect.left + index * (size + gap), rect.top, size, size)
            hovered = icon.collidepoint(self._mouse_pos())
            pygame.draw.rect(surface, palette.white if hovered else palette.black, icon)
            pygame.draw.rect(surface, palette.black if hovered else palette.line_bright, icon, 1)
            mark = palette.black if hovered else palette.white
            text = self.fonts["tiny"].render(str(buff.glyph)[:2].upper(), True, mark)
            surface.blit(text, text.get_rect(center=(icon.centerx, icon.centery - 3)))
            progress = buff.remaining / max(0.01, buff.total)
            bar = pygame.Rect(icon.left + 3, icon.bottom - 5, icon.width - 6, 2)
            pygame.draw.rect(surface, palette.line, bar)
            fill = bar.copy()
            fill.width = int(bar.width * max(0.0, min(1.0, progress)))
            pygame.draw.rect(surface, mark, fill)
            time = self.fonts["tiny"].render(f"{buff.remaining:0.0f}", True, mark)
            surface.blit(time, time.get_rect(center=(icon.centerx, icon.bottom + 6)))

    def _draw_item_icon(self, surface: pygame.Surface, rect: pygame.Rect, definition, quantity: int, inverted: bool = False) -> None:
        palette = config.PALETTE
        fill = palette.white if inverted else palette.black
        mark = palette.black if inverted else palette.white
        detail = palette.black if inverted else palette.line_bright
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, mark, rect, 1)
        inner = rect.inflate(-8, -8)
        if definition.type == "scroll":
            body = pygame.Rect(inner.left + 2, inner.top + 4, inner.width - 4, inner.height - 8)
            pygame.draw.rect(surface, mark, body, 1)
            pygame.draw.circle(surface, mark, (body.left, body.centery), max(2, body.height // 3), 1)
            pygame.draw.circle(surface, mark, (body.right, body.centery), max(2, body.height // 3), 1)
            pygame.draw.line(surface, detail, (body.left + 5, body.top + 5), (body.right - 5, body.top + 5), 1)
            pygame.draw.line(surface, detail, (body.left + 5, body.bottom - 5), (body.right - 5, body.bottom - 5), 1)
        elif definition.type == "equipment":
            pygame.draw.line(surface, mark, inner.bottomleft, inner.topright, 2)
            pygame.draw.line(surface, detail, (inner.left + 3, inner.top + 3), (inner.right - 3, inner.bottom - 3), 1)
        elif definition.type == "consumable":
            pygame.draw.circle(surface, mark, rect.center, max(4, min(inner.width, inner.height) // 3), 1)
            pygame.draw.line(surface, detail, (rect.centerx, inner.top + 2), (rect.centerx, inner.bottom - 2), 1)
        glyph = self.fonts["tiny"].render(definition.glyph[:3].upper(), True, mark)
        surface.blit(glyph, glyph.get_rect(center=rect.center))
        if getattr(definition, "rarity", "common") != "common":
            pygame.draw.line(surface, mark, (rect.left + 3, rect.top + 3), (rect.right - 4, rect.top + 3), 1)
        if quantity > 1:
            amount = self.fonts["tiny"].render(str(quantity), True, mark)
            surface.blit(amount, amount.get_rect(bottomright=(rect.right - 3, rect.bottom - 2)))

    def _draw_loot_banner(self, surface: pygame.Surface, viewport: pygame.Rect, state) -> None:
        if state.loot_banner_timer <= 0 or state.loot_banner_item_id not in ITEM_DEFINITIONS:
            return
        definition = ITEM_DEFINITIONS[state.loot_banner_item_id]
        rects = self._inventory_slot_rects(viewport, state)
        inventory_top = rects[0].top if rects else viewport.bottom - 16
        width = min(430, viewport.width - 80)
        height = 58
        rect = pygame.Rect(0, 0, width, height)
        rect.centerx = viewport.centerx
        rect.bottom = inventory_top - 14
        if rect.top < viewport.top + 10:
            rect.top = viewport.top + 10
        alpha = int(245 * min(1.0, state.loot_banner_timer / 0.75))
        banner = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(banner, config.PALETTE.black, banner.get_rect())
        pygame.draw.rect(banner, config.PALETTE.white, banner.get_rect(), 1)
        icon_rect = pygame.Rect(12, 12, 34, 34)
        self._draw_item_icon(banner, icon_rect, definition, 1)
        heading = self.fonts["tiny"].render("LOOT ACQUIRED", True, config.PALETTE.text_dim)
        banner.blit(heading, (58, 10))
        name = self.fonts["small"].render(definition.name.upper(), True, config.PALETTE.white)
        banner.blit(name, (58, 28))
        banner.set_alpha(alpha)
        surface.blit(banner, rect)

    def _draw_item_definition_tooltip(
        self,
        surface: pygame.Surface,
        bounds: pygame.Rect,
        definition,
        mouse_pos: tuple[int, int],
        quantity: int = 1,
        source_label: str = "ITEM",
    ) -> None:
        width = 292
        description_lines = self._wrap(definition.description, self.fonts["tiny"], width - 24)
        detail_lines = [f"{definition.rarity.upper()} {definition.type.upper()}", source_label]
        if definition.type == "scroll":
            detail_lines.append(f"USE: LASTS {definition.duration:0.0f}S" if definition.duration > 0 else "USE: INSTANT")
        elif definition.type == "consumable":
            detail_lines.append(f"CONSUME: {definition.duration:0.0f}S" if definition.duration > 0 else "CONSUME: INSTANT")
        elif definition.type == "equipment":
            detail_lines.append("EQUIP: PASSIVE")
        if definition.tags:
            detail_lines.append("TAGS " + ", ".join(tag.upper() for tag in definition.tags[:2]))
        if quantity > 1:
            detail_lines.append(f"STACK {quantity}")
        height = 22 + 18 * len(detail_lines) + 16 * len(description_lines) + 24
        rect = pygame.Rect(mouse_pos[0] + 16, mouse_pos[1] - 8, width, height)
        if rect.right > bounds.right - 8:
            rect.right = mouse_pos[0] - 14
        if rect.bottom > bounds.bottom - 8:
            rect.bottom = mouse_pos[1] - 14
        if rect.top < bounds.top + 8:
            rect.top = bounds.top + 8

        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        y = rect.top + 10
        title = self.fonts["small"].render(definition.name.upper(), True, config.PALETTE.white)
        surface.blit(title, (rect.left + 12, y))
        y += 24
        for line in detail_lines:
            surface.blit(self.fonts["tiny"].render(line, True, config.PALETTE.text_dim), (rect.left + 12, y))
            y += 18
        y += 2
        for line in description_lines:
            surface.blit(self.fonts["tiny"].render(line, True, config.PALETTE.text), (rect.left + 12, y))
            y += 16

    def _draw_item_tooltip(self, surface: pygame.Surface, viewport: pygame.Rect, state, mouse_pos: tuple[int, int]) -> None:
        if self.item_context_menu is not None or self.item_drag is not None or getattr(state, "expedition_recap", None) is not None:
            return
        if self._control_group_slot_at(mouse_pos, viewport, state) is not None:
            return
        source = self._item_source_at(mouse_pos, viewport, state)
        if source is None:
            return
        slot = self._slot_for_source(source, state)
        if slot is None:
            return
        definition = ITEM_DEFINITIONS.get(slot.item_id)
        if definition is None:
            return
        source_label = {"player": "PLAYER", "troop": "TROOP BAG", "equipment": "EQUIPPED"}.get(source[0], "ITEM")
        self._draw_item_definition_tooltip(surface, viewport, definition, mouse_pos, slot.quantity, source_label)

    def _draw_item_context_menu(self, surface: pygame.Surface, state, bounds: pygame.Rect) -> None:
        rect = self._item_context_rect(state)
        if rect is None:
            return
        options = self._item_context_options(state)
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        mouse = self._mouse_pos()
        for index, (label, _action) in enumerate(options):
            row = pygame.Rect(rect.left + 4, rect.top + 4 + index * 25, rect.width - 8, 23)
            hovered = row.collidepoint(mouse)
            pygame.draw.rect(surface, config.PALETTE.white if hovered else config.PALETTE.black, row)
            color = config.PALETTE.black if hovered else config.PALETTE.text
            surface.blit(self.fonts["tiny"].render(label, True, color), (row.left + 8, row.top + 6))

    def _draw_item_drag(self, surface: pygame.Surface, state) -> None:
        if self.item_drag is None or not self.item_drag.get("active"):
            return
        slot = self._slot_for_source(self.item_drag["source"], state)
        if slot is None:
            return
        definition = ITEM_DEFINITIONS.get(slot.item_id)
        if definition is None:
            return
        rect = pygame.Rect(0, 0, 34, 34)
        rect.center = self._mouse_pos()
        ghost = pygame.Surface(rect.size, pygame.SRCALPHA)
        self._draw_item_icon(ghost, ghost.get_rect(), definition, slot.quantity)
        ghost.set_alpha(210)
        surface.blit(ghost, rect.topleft)

    def _draw_control_group_tooltip(self, surface: pygame.Surface, viewport: pygame.Rect, state, mouse_pos: tuple[int, int]) -> None:
        index = self._control_group_slot_at(mouse_pos, viewport, state)
        if index is None:
            return
        troops = state.control_group_troops(index)
        if not troops:
            return

        width = 300
        health = sum(max(0, troop.health) for troop in troops)
        max_health = sum(troop.max_health for troop in troops)
        avg_level = sum(troop.level for troop in troops) / max(1, len(troops))
        lines = [
            f"{len(troops)} TROOPS",
            f"HP {int(health)}/{int(max_health)}",
            f"AVG LVL {avg_level:0.1f}",
        ]
        for troop in troops[:8]:
            lines.append(f"{troop.display_name.upper()} L{troop.level} HP {int(max(0, troop.health))}/{int(troop.max_health)}")
        if len(troops) > 8:
            lines.append(f"+{len(troops) - 8} MORE")

        height = 34 + 18 * len(lines)
        rect = pygame.Rect(mouse_pos[0] + 16, mouse_pos[1] - 8, width, height)
        if rect.right > viewport.right - 8:
            rect.right = mouse_pos[0] - 14
        if rect.bottom > viewport.bottom - 8:
            rect.bottom = mouse_pos[1] - 14
        if rect.top < viewport.top + 8:
            rect.top = viewport.top + 8

        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        y = rect.top + 10
        title = self.fonts["small"].render(f"CONTROL GROUP {index + 1}", True, config.PALETTE.white)
        surface.blit(title, (rect.left + 12, y))
        y += 24
        for line_number, line in enumerate(lines):
            color = config.PALETTE.text_dim if line_number < 3 else config.PALETTE.text
            surface.blit(self.fonts["tiny"].render(line, True, color), (rect.left + 12, y))
            y += 18

    def _draw_game_over(self, surface: pygame.Surface, viewport: pygame.Rect) -> None:
        rect = pygame.Rect(0, 0, min(420, viewport.width - 80), 130)
        rect.center = viewport.center
        self._alpha_rect(surface, rect, (0, 0, 0, 232))
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        title = self.fonts["large"].render("CORE LOST", True, config.PALETTE.white)
        surface.blit(title, title.get_rect(center=(rect.centerx, rect.top + 42)))
        subtitle = self.fonts["small"].render("The workspace has gone dark.", True, config.PALETTE.text_dim)
        surface.blit(subtitle, subtitle.get_rect(center=(rect.centerx, rect.top + 74)))

    def _blocks_world_input(self, pos: tuple[int, int], screen_rect: pygame.Rect, viewport: pygame.Rect, state) -> bool:
        if state.round_events.awaiting_choice:
            return viewport.collidepoint(pos)
        context_rect = self._item_context_rect(state)
        if context_rect is not None and context_rect.inflate(4, 4).collidepoint(pos):
            return True
        if pos[1] < config.TOP_BAR_HEIGHT or pos[0] < config.TOOLBAR_WIDTH:
            return True
        if self.active_panel and (not self._using_external_windows() or self.active_panel == "expedition_metrics") and self._active_panel_rect(screen_rect, viewport).collidepoint(pos):
            return True
        if not state.build_mode and not state.station_mode and self._context_rect(screen_rect, viewport).collidepoint(pos):
            return True
        for rect in self._inventory_slot_rects(viewport, state):
            if rect.inflate(8, 8).collidepoint(pos):
                return True
        for rect in self._troop_equipment_slot_rects(viewport, state):
            if rect.inflate(8, 8).collidepoint(pos):
                return True
        for rect in self._troop_inventory_slot_rects(viewport, state):
            if rect.inflate(8, 8).collidepoint(pos):
                return True
        for rect in self._control_group_slot_rects(viewport, state):
            if rect.inflate(8, 8).collidepoint(pos):
                return True
        return False

    def _clamp_dialog_scroll(self, content_height: int, view_height: int) -> None:
        self.dialog_scroll_max = max(0.0, float(content_height - view_height))
        self.dialog_scroll = max(0.0, min(self.dialog_scroll, self.dialog_scroll_max))

    def _draw_scrollbar(self, surface: pygame.Surface, rect: pygame.Rect, scroll: float, max_scroll: float) -> None:
        if max_scroll <= 0:
            return
        palette = config.PALETTE
        bar = pygame.Rect(rect.right - 5, rect.top + 3, 2, rect.height - 6)
        pygame.draw.rect(surface, palette.line, bar)
        thumb_h = max(24, int(bar.height * rect.height / (rect.height + max_scroll)))
        thumb_y = bar.top + int((bar.height - thumb_h) * (scroll / max_scroll))
        pygame.draw.rect(surface, palette.white, pygame.Rect(bar.left, thumb_y, bar.width, thumb_h))

    def _draw_bar(self, surface: pygame.Surface, rect: pygame.Rect, progress: float) -> None:
        progress = max(0.0, min(1.0, progress))
        pygame.draw.rect(surface, config.PALETTE.bg, rect)
        fill = rect.copy()
        fill.width = int(rect.width * progress)
        pygame.draw.rect(surface, config.PALETTE.white, fill)
        pygame.draw.rect(surface, config.PALETTE.line, rect, 1)

    def _draw_corner_brackets(self, surface: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int]) -> None:
        length = 14
        points = (
            ((rect.left, rect.top + length), (rect.left, rect.top), (rect.left + length, rect.top)),
            ((rect.right - length, rect.top), (rect.right, rect.top), (rect.right, rect.top + length)),
            ((rect.right, rect.bottom - length), (rect.right, rect.bottom), (rect.right - length, rect.bottom)),
            ((rect.left + length, rect.bottom), (rect.left, rect.bottom), (rect.left, rect.bottom - length)),
        )
        for a, b, c in points:
            pygame.draw.line(surface, color, a, b, 1)
            pygame.draw.line(surface, color, b, c, 1)

    def _alpha_rect(self, surface: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int, int]) -> None:
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        overlay.fill(color)
        surface.blit(overlay, rect.topleft)

    def _wrap(self, text: str, font: pygame.font.Font, max_width: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if font.size(candidate)[0] <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
        if current:
            lines.append(current)
        return lines
