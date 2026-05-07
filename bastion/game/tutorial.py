from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any

import pygame

from bastion import config
from bastion.engine.drawing import draw_circle_alpha, draw_rect_alpha
from bastion.game.resources import GoldDeposit, MineralExtractor


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "tutorial.json"


@dataclass(frozen=True)
class TutorialDialoguePage:
    title: str
    body: tuple[str, ...]
    focus: str | None = None


@dataclass(frozen=True)
class TutorialObjectiveDefinition:
    objective_id: str
    title: str
    description: str
    condition: str
    required: int = 1
    kind: str | None = None
    target: str | None = None
    radius: float = 0.0
    near_target: str | None = None
    near_required: int = 0
    near_radius: float = 0.0
    resource_kind: str | None = None


@dataclass(frozen=True)
class TutorialActionDefinition:
    action_type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TutorialStepDefinition:
    step_id: str
    phase: str
    focus: str | None
    dialogue: tuple[TutorialDialoguePage, ...]
    objective: TutorialObjectiveDefinition | None
    actions: tuple[TutorialActionDefinition, ...]
    pause_during_objective: bool = False
    lock_input_during_objective: bool = False
    lock_camera_during_objective: bool = False
    toolbar_hint: str | None = None


@dataclass(frozen=True)
class TutorialDefinition:
    scenario: dict[str, Any]
    steps: tuple[TutorialStepDefinition, ...]


@dataclass
class TutorialProgress:
    current: int
    required: int
    complete: bool
    secondary_current: int = 0
    secondary_required: int = 0
    secondary_label: str = ""


class TutorialDialogueState:
    def __init__(self, pages: tuple[TutorialDialoguePage, ...]) -> None:
        self.pages = pages
        self.index = 0
        self.next_button_rect = pygame.Rect(0, 0, 0, 0)

    @property
    def current(self) -> TutorialDialoguePage | None:
        if 0 <= self.index < len(self.pages):
            return self.pages[self.index]
        return None

    @property
    def remaining_label(self) -> str:
        if len(self.pages) <= 1:
            return "NEXT"
        return f"NEXT {self.index + 1}/{len(self.pages)}"

    def advance(self) -> bool:
        self.index += 1
        return self.index >= len(self.pages)


class TutorialObjectiveState:
    def __init__(self, definition: TutorialObjectiveDefinition, manager: "TutorialManager") -> None:
        self.definition = definition
        self.baseline = manager.baseline_count(definition)

    def progress(self, manager: "TutorialManager") -> TutorialProgress:
        return manager.objective_progress(self.definition, self.baseline)


class TutorialManager:
    def __init__(self, game, definition: TutorialDefinition | None = None) -> None:
        self.game = game
        self.definition = definition or load_tutorial_definition()
        self.active = False
        self.completed = False
        self.current_index = -1
        self.current_step: TutorialStepDefinition | None = None
        self.dialogue: TutorialDialogueState | None = None
        self.objective: TutorialObjectiveState | None = None
        self.target_gold_deposit: GoldDeposit | None = None
        self.gold_hint_visible = False
        self.tutorial_enemy_ids: set[int] = set()
        self.resource_delivered: dict[str, int] = {"gold": 0, "mineral": 0}
        self.core_damage_events = 0
        self._pulse_time = 0.0
        self._last_focus_key: str | None = None
        self._last_focus_page = -1
        self.pending_hud_panel: str | None = None

    @property
    def blocks_standard_waves(self) -> bool:
        return self.active and bool(self.definition.scenario.get("block_standard_waves", True))

    @property
    def blocks_ambient_updates(self) -> bool:
        return self.active and bool(self.definition.scenario.get("block_ambient_updates", True))

    @property
    def pauses_game(self) -> bool:
        return self.active and (
            self.dialogue is not None
            or bool(self.current_step is not None and self.objective is not None and self.current_step.pause_during_objective)
        )

    @property
    def blocks_player_input(self) -> bool:
        return self.active and (
            self.dialogue is not None
            or bool(self.current_step is not None and self.objective is not None and self.current_step.lock_input_during_objective)
        )

    @property
    def locks_camera(self) -> bool:
        return self.active and (
            self.dialogue is not None
            or bool(self.current_step is not None and self.objective is not None and self.current_step.lock_camera_during_objective)
        )

    @property
    def active_phase(self) -> str:
        return self.current_step.phase if self.current_step is not None else "Tutorial"

    def start(self) -> None:
        if self.completed or not self.definition.steps:
            return
        self.active = True
        self.current_index = 0
        self._apply_starting_scenario()
        self._enter_current_step()

    def update(self, dt: float) -> None:
        if not self.active:
            return
        self._pulse_time += dt
        if getattr(self.game, "game_over", False):
            self.complete(aborted=True)
            return
        self._refresh_dialogue_focus()
        if self.dialogue is not None:
            return
        if self.objective is None:
            return
        progress = self.objective.progress(self)
        if progress.complete:
            self._advance_step()

    def handle_event(self, event: pygame.event.Event, screen_rect: pygame.Rect, viewport: pygame.Rect) -> bool:
        if not self.blocks_player_input:
            return False
        if hasattr(event, "pos") and event.pos[1] < config.TITLE_BAR_HEIGHT:
            return False
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_TAB):
            self.advance_dialogue()
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.dialogue is not None and self.dialogue.next_button_rect.collidepoint(event.pos):
                self.game.play_sound("menu_select")
                self.advance_dialogue()
            return True
        if event.type == pygame.MOUSEBUTTONDOWN:
            return True
        if event.type in (pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION, pygame.MOUSEWHEEL, pygame.KEYUP):
            return True
        return False

    def hover_target_at(self, pos: tuple[int, int]) -> tuple[str, str] | None:
        if self.dialogue is None:
            return None
        if self.dialogue.next_button_rect.collidepoint(pos):
            return ("tutorial", "next")
        return None

    def advance_dialogue(self) -> None:
        if self.dialogue is None:
            return
        finished = self.dialogue.advance()
        if not finished:
            self._refresh_dialogue_focus(force=True)
            return
        self.dialogue = None
        self._last_focus_key = None
        self._last_focus_page = -1
        if self.objective is None:
            self._advance_step()
        else:
            self._focus_current_step()

    def complete(self, aborted: bool = False) -> None:
        self.active = False
        self.completed = True
        self.dialogue = None
        self.objective = None
        self.gold_hint_visible = False
        self._release_tutorial_enemies(remove=aborted)
        if not aborted:
            self.game.message("TUTORIAL COMPLETE")

    def consume_hud_panel_request(self) -> str | None:
        panel = self.pending_hud_panel
        self.pending_hud_panel = None
        return panel

    def notify_building_created(self, _building) -> None:
        self.update(0.0)

    def notify_tower_created(self, _tower) -> None:
        self.update(0.0)

    def notify_troop_spawned(self, _troop) -> None:
        self.update(0.0)

    def notify_workers_stationed(self) -> None:
        self.update(0.0)

    def notify_resource_delivered(self, kind: str, amount: int, _source=None) -> None:
        key = "gold" if kind == "gold" else "mineral"
        self.resource_delivered[key] = self.resource_delivered.get(key, 0) + max(0, int(amount))
        self.update(0.0)

    def notify_core_damaged(self, _target, _amount: float) -> None:
        if not self.active:
            return
        self.core_damage_events += 1
        self.update(0.0)

    def baseline_count(self, objective: TutorialObjectiveDefinition) -> int:
        if objective.condition == "troop_trained":
            return self._count_troops(objective.kind)
        if objective.condition == "resource_delivered":
            key = "gold" if objective.resource_kind == "gold" else "mineral"
            return self.resource_delivered.get(key, 0)
        if objective.condition == "core_damaged":
            return self.core_damage_events
        return 0

    def objective_progress(self, objective: TutorialObjectiveDefinition, baseline: int = 0) -> TutorialProgress:
        condition = objective.condition
        if condition == "selected_troops":
            current = sum(1 for troop in self.game.selected_troops if troop.alive and self._kind_matches(troop, objective.kind))
            return TutorialProgress(current, objective.required, current >= objective.required)

        if condition == "worker_station_near":
            current = self._worker_count_near(objective.target, objective.kind, objective.radius)
            return TutorialProgress(current, objective.required, current >= objective.required)

        if condition == "towers_built":
            current = sum(1 for tower in self.game.towers if tower.alive and self._kind_matches(tower, objective.kind))
            secondary = 0
            if objective.near_target:
                secondary = self._tower_count_near(objective.kind, objective.near_target, objective.near_radius)
            complete = current >= objective.required and secondary >= objective.near_required
            return TutorialProgress(
                current,
                objective.required,
                complete,
                secondary_current=secondary,
                secondary_required=objective.near_required,
                secondary_label="Near extractor",
            )

        if condition == "building_built":
            current = self._count_buildings(objective.kind)
            return TutorialProgress(current, objective.required, current >= objective.required)

        if condition == "troop_trained":
            current = max(0, self._count_troops(objective.kind) - baseline)
            return TutorialProgress(current, objective.required, current >= objective.required)

        if condition == "gold_discovered":
            deposit = self._target_gold()
            discovered = bool(deposit is not None and self.game.is_world_explored(deposit.pos, deposit.radius))
            return TutorialProgress(1 if discovered else 0, 1, discovered)

        if condition == "gold_extractor_worker":
            extractor = self._gold_extractor()
            extractor_ready = isinstance(extractor, MineralExtractor) and extractor.alive
            current = 0
            if extractor_ready:
                current = self._worker_count_near_object(extractor, objective.kind, objective.radius)
            return TutorialProgress(
                current,
                objective.required,
                extractor_ready and current >= objective.required,
                secondary_current=1 if extractor_ready else 0,
                secondary_required=1,
                secondary_label="Extractor built",
            )

        if condition == "gold_extractor_built":
            extractor = self._gold_extractor()
            current = 1 if isinstance(extractor, MineralExtractor) and extractor.alive else 0
            return TutorialProgress(current, objective.required, current >= objective.required)

        if condition == "resource_delivered":
            key = "gold" if objective.resource_kind == "gold" else "mineral"
            current = max(0, self.resource_delivered.get(key, 0) - baseline)
            return TutorialProgress(current, objective.required, current >= objective.required)

        if condition == "core_damaged":
            current = max(0, self.core_damage_events - baseline)
            return TutorialProgress(current, objective.required, current >= objective.required)

        if condition == "tutorial_enemies_cleared":
            alive = self._tutorial_enemies_alive()
            complete = alive <= 0
            return TutorialProgress(1 if complete else 0, 1, complete)

        return TutorialProgress(0, objective.required, False)

    def draw_overlay(
        self,
        surface: pygame.Surface,
        screen_rect: pygame.Rect,
        viewport: pygame.Rect,
        fonts: dict[str, pygame.font.Font],
    ) -> None:
        if not self.active:
            return
        if self.dialogue is not None:
            self._draw_dialogue(surface, screen_rect, viewport, fonts)
        elif self.objective is not None:
            self._draw_tracker(surface, viewport, fonts)

    def draw_toolbar_hint(
        self,
        surface: pygame.Surface,
        buttons: list,
        fonts: dict[str, pygame.font.Font],
    ) -> None:
        if not self.active or self.current_step is None or self.current_step.toolbar_hint is None:
            return
        target = self.current_step.toolbar_hint
        button = next(
            (item for item in buttons if getattr(item, "command", None) == "tool" and getattr(item, "value", None) == target),
            None,
        )
        if button is None:
            return
        rect = button.rect
        pulse = 0.5 + 0.5 * math.sin(self._pulse_time * 7.2)
        alpha = int(112 + 118 * pulse)
        bounds = pygame.Rect(rect.right + 2, rect.centery - 15, 54, 30)
        arrow = [
            (rect.right + 2, rect.centery),
            (rect.right + 24, rect.centery - 13),
            (rect.right + 24, rect.centery - 5),
            (rect.right + 52, rect.centery - 5),
            (rect.right + 52, rect.centery + 5),
            (rect.right + 24, rect.centery + 5),
            (rect.right + 24, rect.centery + 13),
        ]
        temp = pygame.Surface(bounds.size, pygame.SRCALPHA)
        local = [(x - bounds.left, y - bounds.top) for x, y in arrow]
        pygame.draw.polygon(temp, (*config.PALETTE.white, alpha), local)
        pygame.draw.polygon(temp, (*config.PALETTE.black, min(255, alpha + 25)), local, 1)
        surface.blit(temp, bounds.topleft)
        label = fonts["tiny"].render(target.upper(), True, config.PALETTE.white)
        surface.blit(label, (bounds.right + 6, bounds.centery - label.get_height() // 2))

    def draw_world_guidance(self, surface: pygame.Surface, camera, viewport: pygame.Rect) -> None:
        if not self.active:
            return
        targets = self._world_guidance_targets()
        if not targets:
            return
        pulse = 0.5 + 0.5 * math.sin(self._pulse_time * 5.2)
        for target in targets:
            if not self.game.is_world_explored(target, 16):
                continue
            screen = camera.world_to_screen(target, viewport)
            if not viewport.inflate(80, 80).collidepoint(screen):
                continue
            radius = (28.0 + 16.0 * pulse) * camera.zoom
            draw_circle_alpha(surface, screen, radius, config.PALETTE.white, 78, 2)
            draw_circle_alpha(surface, screen, radius * 0.44, config.PALETTE.white, 44, 1)

    def draw_minimap_guidance(self, surface: pygame.Surface, map_rect: pygame.Rect, grid) -> None:
        if not self.active or not self.gold_hint_visible:
            return
        deposit = self._target_gold()
        if deposit is None or not deposit.active:
            return
        x = map_rect.left + deposit.pos.x / max(1, grid.world_size[0]) * map_rect.width
        y = map_rect.top + deposit.pos.y / max(1, grid.world_size[1]) * map_rect.height
        pulse = 0.5 + 0.5 * math.sin(self._pulse_time * 6.0)
        radius = int(5 + pulse * 4)
        center = (round(x), round(y))
        pygame.draw.circle(surface, config.PALETTE.white, center, radius, 1)
        pygame.draw.line(surface, config.PALETTE.white, (center[0] - radius - 2, center[1]), (center[0] + radius + 2, center[1]), 1)
        pygame.draw.line(surface, config.PALETTE.white, (center[0], center[1] - radius - 2), (center[0], center[1] + radius + 2), 1)

    def _apply_starting_scenario(self) -> None:
        scenario = self.definition.scenario
        self.game.build_mode = None
        self.game.station_mode = False
        self.game.gold = max(self.game.gold, int(scenario.get("starting_gold", self.game.gold)))
        self.game.minerals = max(self.game.minerals, int(scenario.get("starting_minerals", self.game.minerals)))
        self.target_gold_deposit = self._ensure_tutorial_gold_deposit()

    def _enter_current_step(self) -> None:
        if self.current_index >= len(self.definition.steps):
            self.complete()
            return
        self.current_step = self.definition.steps[self.current_index]
        self.dialogue = TutorialDialogueState(self.current_step.dialogue) if self.current_step.dialogue else None
        self.objective = (
            TutorialObjectiveState(self.current_step.objective, self)
            if self.current_step.objective is not None
            else None
        )
        for action in self.current_step.actions:
            self._execute_action(action)
        self._refresh_dialogue_focus(force=True)
        if self.dialogue is None:
            self._focus_current_step()
        if self.dialogue is None and self.objective is None:
            self._advance_step()

    def _advance_step(self) -> None:
        self.current_index += 1
        self.objective = None
        self.dialogue = None
        self._enter_current_step()

    def _execute_action(self, action: TutorialActionDefinition) -> None:
        params = action.params
        if action.action_type == "clear_build_mode":
            self.game.build_mode = None
            self.game.station_mode = False
        elif action.action_type == "set_build_mode":
            self.game.set_build_mode(str(params.get("mode", "")))
        elif action.action_type == "ensure_minimum_resources":
            self.game.gold = max(self.game.gold, int(params.get("gold", self.game.gold)))
            self.game.minerals = max(self.game.minerals, int(params.get("minerals", self.game.minerals)))
        elif action.action_type == "unpause_game":
            self.game.paused = False
        elif action.action_type == "open_panel":
            self.pending_hud_panel = str(params.get("panel", ""))
        elif action.action_type == "reveal_gold_hint":
            self.gold_hint_visible = True
        elif action.action_type == "spawn_tutorial_attack":
            self._spawn_tutorial_attack()
        elif action.action_type == "clear_tutorial_enemies":
            self._release_tutorial_enemies(remove=True)

    def _focus_current_step(self) -> None:
        if self.current_step is None or self.current_step.focus is None:
            return
        focus = self.resolve_focus(self.current_step.focus)
        if focus is not None:
            self.game.pending_camera_focus = focus

    def _refresh_dialogue_focus(self, force: bool = False) -> None:
        if self.dialogue is None or self.current_step is None:
            return
        page = self.dialogue.current
        if page is None:
            return
        focus_key = page.focus or self.current_step.focus
        if focus_key is None:
            return
        if not force and focus_key == self._last_focus_key and self._last_focus_page == self.dialogue.index:
            return
        focus = self.resolve_focus(focus_key)
        if focus is None:
            return
        self.game.pending_camera_focus = focus
        self._last_focus_key = focus_key
        self._last_focus_page = self.dialogue.index

    def resolve_focus(self, key: str | None) -> pygame.Vector2 | None:
        targets = self.resolve_targets(key)
        if not targets:
            return None
        focus = pygame.Vector2(0, 0)
        for target in targets:
            focus += target
        return focus / len(targets)

    def resolve_targets(self, key: str | None) -> list[pygame.Vector2]:
        if key is None:
            return []
        if key == "core":
            return [pygame.Vector2(self.game.core_target.pos)]
        if key == "starting_grunts":
            grunts = [troop for troop in self.game.troops if troop.alive and troop.kind == "grunt"]
            return [pygame.Vector2(troop.pos) for troop in grunts[:3]]
        if key == "starting_house":
            house = self._first_building("house")
            return [pygame.Vector2(house.pos)] if house is not None else []
        if key == "starting_extractor":
            extractor = self._starting_extractor()
            return [pygame.Vector2(extractor.pos)] if extractor is not None else []
        if key == "core_and_extractor":
            targets = [pygame.Vector2(self.game.core_target.pos)]
            extractor = self._starting_extractor()
            if extractor is not None:
                targets.append(pygame.Vector2(extractor.pos))
            return targets
        if key == "barracks":
            building = self._first_building("barracks")
            return [pygame.Vector2(building.pos)] if building is not None else []
        if key == "research":
            building = self._first_building("research")
            return [pygame.Vector2(building.pos)] if building is not None else []
        if key == "library":
            building = self._first_building("library")
            return [pygame.Vector2(building.pos)] if building is not None else []
        if key == "hero_hall":
            building = self._first_building("hero_hall")
            return [pygame.Vector2(building.pos)] if building is not None else []
        if key == "tutorial_gold":
            deposit = self._target_gold()
            return [pygame.Vector2(deposit.pos)] if deposit is not None else []
        if key == "gold_extractor":
            extractor = self._gold_extractor()
            return [pygame.Vector2(extractor.pos)] if getattr(extractor, "alive", False) else []
        return []

    def _world_guidance_targets(self) -> list[pygame.Vector2]:
        if self.dialogue is not None and self.current_step is not None:
            page = self.dialogue.current
            return self.resolve_targets((page.focus if page is not None else None) or self.current_step.focus)
        if self.objective is None:
            return []
        objective = self.objective.definition
        if objective.condition == "towers_built" and objective.near_target:
            return self.resolve_targets(objective.near_target)
        if objective.condition in {"worker_station_near", "gold_discovered", "gold_extractor_worker"}:
            return self.resolve_targets(objective.target)
        return []

    def _draw_dialogue(
        self,
        surface: pygame.Surface,
        screen_rect: pygame.Rect,
        viewport: pygame.Rect,
        fonts: dict[str, pygame.font.Font],
    ) -> None:
        dialogue = self.dialogue
        if dialogue is None:
            return
        page = dialogue.current
        if page is None:
            return
        draw_rect_alpha(surface, viewport, config.PALETTE.black, 112)
        width = min(680, max(420, viewport.width - 120))
        rect = pygame.Rect(0, 0, width, 190)
        rect.centerx = viewport.centerx
        rect.bottom = min(screen_rect.bottom - 32, viewport.bottom - 26)
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)

        phase = fonts["tiny"].render(self.active_phase.upper(), True, config.PALETTE.text_dim)
        surface.blit(phase, (rect.left + 18, rect.top + 14))
        title = fonts["large"].render(page.title.upper(), True, config.PALETTE.white)
        surface.blit(title, (rect.left + 18, rect.top + 34))

        y = rect.top + 74
        for paragraph in page.body:
            for line in _wrap(paragraph, fonts["small"], rect.width - 36):
                surface.blit(fonts["small"].render(line, True, config.PALETTE.text), (rect.left + 18, y))
                y += 22
            y += 6

        button = pygame.Rect(rect.right - 132, rect.bottom - 44, 104, 30)
        dialogue.next_button_rect = button
        hovered = button.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(surface, config.PALETTE.white if hovered else config.PALETTE.black, button)
        pygame.draw.rect(surface, config.PALETTE.black if hovered else config.PALETTE.white, button, 1)
        color = config.PALETTE.black if hovered else config.PALETTE.white
        label = fonts["small"].render(dialogue.remaining_label, True, color)
        surface.blit(label, label.get_rect(center=button.center))

    def _draw_tracker(self, surface: pygame.Surface, viewport: pygame.Rect, fonts: dict[str, pygame.font.Font]) -> None:
        if self.objective is None:
            return
        definition = self.objective.definition
        progress = self.objective.progress(self)
        rect = pygame.Rect(viewport.left + 16, viewport.top + 16, min(390, viewport.width - 32), 116)
        pygame.draw.rect(surface, config.PALETTE.black, rect)
        pygame.draw.rect(surface, config.PALETTE.white, rect, 1)
        phase = fonts["tiny"].render(self.active_phase.upper(), True, config.PALETTE.text_dim)
        surface.blit(phase, (rect.left + 14, rect.top + 10))
        title = fonts["medium"].render(definition.title.upper(), True, config.PALETTE.white)
        surface.blit(title, (rect.left + 14, rect.top + 28))
        y = rect.top + 55
        for line in _wrap(definition.description, fonts["tiny"], rect.width - 28)[:2]:
            surface.blit(fonts["tiny"].render(line, True, config.PALETTE.text), (rect.left + 14, y))
            y += 16
        self._draw_progress_bar(surface, pygame.Rect(rect.left + 14, rect.bottom - 19, rect.width - 28, 6), progress.current, progress.required)
        count = fonts["tiny"].render(f"{min(progress.current, progress.required)}/{progress.required}", True, config.PALETTE.text_dim)
        surface.blit(count, (rect.right - 14 - count.get_width(), rect.bottom - 34))
        if progress.secondary_required > 0:
            secondary = f"{progress.secondary_label.upper()} {min(progress.secondary_current, progress.secondary_required)}/{progress.secondary_required}"
            secondary_img = fonts["tiny"].render(secondary, True, config.PALETTE.text_dim)
            surface.blit(secondary_img, (rect.left + 14, rect.bottom - 34))

    def _draw_progress_bar(self, surface: pygame.Surface, rect: pygame.Rect, current: int, required: int) -> None:
        pygame.draw.rect(surface, config.PALETTE.panel_2, rect)
        fill = rect.copy()
        fill.width = int(rect.width * max(0.0, min(1.0, current / max(1, required))))
        pygame.draw.rect(surface, config.PALETTE.white, fill)
        pygame.draw.rect(surface, config.PALETTE.line_bright, rect, 1)

    def _ensure_tutorial_gold_deposit(self) -> GoldDeposit | None:
        existing = [
            deposit
            for deposit in self.game.resource_deposits
            if getattr(deposit, "active", False) and getattr(deposit, "kind", "") == "gold"
        ]
        cell = self._tutorial_gold_cell()
        if cell is None:
            return min(existing, key=lambda deposit: deposit.pos.distance_to(self.game.core_target.pos), default=None)
        for deposit in existing:
            if deposit.cell == cell:
                return deposit
        deposit = GoldDeposit(cell)
        deposit.place(cell, self.game.grid)
        self.game.resource_deposits.append(deposit)
        return deposit

    def _tutorial_gold_cell(self) -> tuple[int, int] | None:
        scenario = self.definition.scenario
        offset = scenario.get("gold_deposit_offset", [18, -9])
        try:
            dx, dy = int(offset[0]), int(offset[1])
        except (TypeError, ValueError, IndexError):
            dx, dy = 18, -9
        cx, cy = self.game.grid.townhall_cell
        target = (cx + dx, cy + dy)
        max_radius = max(1, int(scenario.get("gold_deposit_search_radius", 14)))
        candidates: list[tuple[int, int]] = []
        for radius in range(0, max_radius + 1):
            for x in range(target[0] - radius, target[0] + radius + 1):
                for y in range(target[1] - radius, target[1] + radius + 1):
                    if max(abs(x - target[0]), abs(y - target[1])) != radius:
                        continue
                    cell = (x, y)
                    if not self.game.grid.buildable(cell) or self.game.is_core_reserve(cell):
                        continue
                    if self.game.active_resource_at(cell) is not None:
                        continue
                    candidates.append(cell)
            if candidates:
                candidates.sort(key=lambda item: (item[0] - target[0]) ** 2 + (item[1] - target[1]) ** 2)
                return candidates[0]
        return None

    def _spawn_tutorial_attack(self) -> None:
        if any(enemy.alive and id(enemy) in self.tutorial_enemy_ids for enemy in self.game.enemies):
            return
        target = self.game.core_target
        for offset in range(3):
            pos = self._spawn_point_for_target(target, 0, offset)
            enemy = self.game.spawn_enemy_at("small", pos, 1, spawn_group="tutorial")
            self.tutorial_enemy_ids.add(id(enemy))
            enemy.aggro.add_threat(target, 500.0, "taunt")
        self.game.message("ENEMIES APPROACH")

    def _spawn_point_for_target(self, target, group_index: int, offset_index: int) -> pygame.Vector2:
        core = pygame.Vector2(self.game.core_target.pos)
        target_pos = pygame.Vector2(target.pos)
        direction = target_pos - core
        if direction.length_squared() <= 0.001:
            angle = -math.pi / 3
        else:
            angle = math.atan2(direction.y, direction.x)
        angle += (group_index * 0.9 - 0.45) + (offset_index - 0.5) * 0.22
        distance = 360 + group_index * 70 + offset_index * 18
        desired = target_pos + pygame.Vector2(math.cos(angle), math.sin(angle)) * distance
        return self.game.grid.nearest_clear_world(desired, 14, max_radius=14)

    def _release_tutorial_enemies(self, remove: bool = False) -> None:
        for enemy in list(self.game.enemies):
            if id(enemy) not in self.tutorial_enemy_ids:
                continue
            if remove:
                enemy.alive = False
            else:
                enemy.spawn_group = "wave"
                enemy.behavior = "assault"
                enemy.aggro.threat.clear()
        self.tutorial_enemy_ids.clear()

    def _tutorial_enemies_alive(self) -> int:
        return sum(1 for enemy in self.game.enemies if id(enemy) in self.tutorial_enemy_ids and enemy.alive)

    def _target_gold(self) -> GoldDeposit | None:
        if self.target_gold_deposit is not None and self.target_gold_deposit.active:
            return self.target_gold_deposit
        self.target_gold_deposit = self._ensure_tutorial_gold_deposit()
        return self.target_gold_deposit

    def _gold_extractor(self) -> MineralExtractor | None:
        deposit = self._target_gold()
        claimed = getattr(deposit, "claimed_by", None) if deposit is not None else None
        if isinstance(claimed, MineralExtractor) and claimed.alive and getattr(claimed.deposit, "kind", "") == "gold":
            return claimed
        gold_extractors = [
            building
            for building in self.game.buildings
            if isinstance(building, MineralExtractor)
            and building.alive
            and getattr(getattr(building, "deposit", None), "kind", "") == "gold"
        ]
        if not gold_extractors:
            return None
        if deposit is not None:
            return min(gold_extractors, key=lambda item: item.pos.distance_to(deposit.pos))
        return min(gold_extractors, key=lambda item: item.pos.distance_to(self.game.core_target.pos))

    def _first_building(self, kind: str):
        return next(
            (building for building in self.game.buildings if building.alive and getattr(building, "kind", "") == kind),
            None,
        )

    def _starting_extractor(self):
        mineral_extractors = [
            building
            for building in self.game.buildings
            if building.alive
            and getattr(building, "kind", "") == "extractor"
            and getattr(getattr(building, "deposit", None), "kind", "") == "mineral"
        ]
        if mineral_extractors:
            return min(mineral_extractors, key=lambda item: item.pos.distance_to(self.game.core_target.pos))
        return next(
            (building for building in self.game.buildings if building.alive and getattr(building, "kind", "") == "extractor"),
            None,
        )

    def _count_buildings(self, kind: str | None) -> int:
        return sum(1 for building in self.game.buildings if building.alive and self._kind_matches(building, kind))

    def _count_troops(self, kind: str | None) -> int:
        return sum(1 for troop in self.game.troops if troop.alive and self._kind_matches(troop, kind))

    def _tower_count_near(self, kind: str | None, target_key: str | None, radius: float) -> int:
        target = self.resolve_focus(target_key)
        if target is None:
            return 0
        return sum(
            1
            for tower in self.game.towers
            if tower.alive and self._kind_matches(tower, kind) and tower.pos.distance_to(target) <= radius
        )

    def _worker_count_near(self, target_key: str | None, kind: str | None, radius: float) -> int:
        target = self.resolve_focus(target_key)
        if target is None:
            return 0
        return sum(
            1
            for troop in self.game.troops
            if troop.alive and self._kind_matches(troop, kind) and troop.station.distance_to(target) <= radius
        )

    def _worker_count_near_object(self, target, kind: str | None, radius: float) -> int:
        return sum(
            1
            for troop in self.game.troops
            if troop.alive and self._kind_matches(troop, kind) and troop.station.distance_to(target.pos) <= radius
        )

    def _kind_matches(self, entity, kind: str | None) -> bool:
        return kind is None or getattr(entity, "kind", None) == kind


def load_tutorial_definition(path: Path | None = None) -> TutorialDefinition:
    path = DATA_PATH if path is None else path
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Tutorial data must be a JSON object.")
    scenario = _as_dict(raw.get("scenario"))
    steps = tuple(_load_steps(raw.get("steps", [])))
    return TutorialDefinition(scenario=scenario, steps=steps)


def _load_steps(records: Any) -> list[TutorialStepDefinition]:
    if not isinstance(records, list):
        return []
    steps: list[TutorialStepDefinition] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        focus = _as_optional_str(record.get("focus"))
        dialogue = tuple(_load_dialogue(record.get("dialogue"), focus))
        objective = _load_objective(record.get("objective"))
        actions = tuple(_load_actions(record.get("actions", [])))
        steps.append(
            TutorialStepDefinition(
                step_id=str(record.get("id", f"step_{len(steps)}")),
                phase=str(record.get("phase", "Tutorial")),
                focus=focus,
                dialogue=dialogue,
                objective=objective,
                actions=actions,
                pause_during_objective=bool(record.get("pause_during_objective", False)),
                lock_input_during_objective=bool(record.get("lock_input_during_objective", False)),
                lock_camera_during_objective=bool(record.get("lock_camera_during_objective", False)),
                toolbar_hint=_as_optional_str(record.get("toolbar_hint")),
            )
        )
    return steps


def _load_dialogue(raw: Any, default_focus: str | None) -> list[TutorialDialoguePage]:
    if not isinstance(raw, dict):
        return []
    pages = raw.get("pages")
    if isinstance(pages, list):
        return [_dialogue_page(page, default_focus) for page in pages if isinstance(page, dict)]
    return [_dialogue_page(raw, default_focus)]


def _dialogue_page(raw: dict[str, Any], default_focus: str | None) -> TutorialDialoguePage:
    body = raw.get("body", ())
    if isinstance(body, str):
        body_lines = (body,)
    elif isinstance(body, list):
        body_lines = tuple(str(line) for line in body)
    else:
        body_lines = ()
    return TutorialDialoguePage(
        title=str(raw.get("title", "Tutorial")),
        body=body_lines,
        focus=_as_optional_str(raw.get("focus")) or default_focus,
    )


def _load_objective(raw: Any) -> TutorialObjectiveDefinition | None:
    if not isinstance(raw, dict):
        return None
    return TutorialObjectiveDefinition(
        objective_id=str(raw.get("id", "objective")),
        title=str(raw.get("title", "Objective")),
        description=str(raw.get("description", "")),
        condition=str(raw.get("condition", "")),
        required=max(1, int(raw.get("required", 1))),
        kind=_as_optional_str(raw.get("kind")),
        target=_as_optional_str(raw.get("target")),
        radius=float(raw.get("radius", 0.0)),
        near_target=_as_optional_str(raw.get("near_target")),
        near_required=max(0, int(raw.get("near_required", 0))),
        near_radius=float(raw.get("near_radius", 0.0)),
        resource_kind=_as_optional_str(raw.get("resource_kind")),
    )


def _load_actions(records: Any) -> list[TutorialActionDefinition]:
    if not isinstance(records, list):
        return []
    actions: list[TutorialActionDefinition] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        params = dict(record)
        action_type = str(params.pop("type", ""))
        if action_type:
            actions.append(TutorialActionDefinition(action_type, params))
    return actions


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_optional_str(value: Any) -> str | None:
    return str(value) if value is not None and str(value) else None


def _wrap(text: str, font: pygame.font.Font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
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
