from __future__ import annotations

import argparse

import pygame
try:
    from pygame._sdl2 import Window
except (ImportError, pygame.error):
    Window = None

from bastion import config
from bastion.engine.audio import AudioSystem
from bastion.engine.drawing import draw_rect_alpha
from bastion.engine import hover_feedback
from bastion.engine.camera import Camera
from bastion.game.state import GameState
from bastion.ui.hud import HUD
from bastion.ui.pause_menu import PauseMenu


class BastionApp:
    def __init__(self) -> None:
        pygame.mixer.pre_init(48000, -16, 2, 512)
        pygame.init()
        pygame.display.set_caption("Bastion of the Core")
        self.display_flags = pygame.NOFRAME | pygame.RESIZABLE | pygame.DOUBLEBUF
        self.screen = pygame.display.set_mode(config.WINDOW_SIZE, self.display_flags)
        self.window = self.get_sdl_window()
        self.clock = pygame.time.Clock()
        self.audio = AudioSystem()
        self.audio.play_music()
        self.state = GameState()
        self.state.audio = self.audio
        self.camera = Camera(self.state.grid.world_size)
        self.fonts = {
            "tiny": pygame.font.SysFont("Consolas", 12),
            "small": pygame.font.SysFont("Consolas", 15),
            "medium": pygame.font.SysFont("Consolas", 19, bold=True),
            "large": pygame.font.SysFont("Consolas", 28, bold=True),
        }
        self.hud = HUD(self.fonts)
        self.pause_menu = PauseMenu(self.fonts)
        self.hud.set_parent_window(self.get_native_window_id())
        self.dragging = False
        self.left_selecting = False
        self.select_start = pygame.Vector2(0, 0)
        self.select_current = pygame.Vector2(0, 0)
        self.last_mouse = pygame.Vector2(0, 0)
        self.double_click_ms = 360
        self.last_unit_click_time = 0
        self.last_unit_click_kind: str | None = None
        self.hover_audio_target = None
        self.title_dragging = False
        self.maximized = False
        self.restore_size = config.WINDOW_SIZE
        self.restore_position: tuple[int, int] | None = None
        self.running = True
        self.camera.center_on(self.state.grid.world_center(self.state.grid.townhall_cell), self.viewport)

    @property
    def screen_rect(self) -> pygame.Rect:
        return self.screen.get_rect()

    @property
    def viewport(self) -> pygame.Rect:
        rect = self.screen.get_rect()
        return pygame.Rect(config.TOOLBAR_WIDTH, config.TOP_BAR_HEIGHT, rect.width - config.TOOLBAR_WIDTH, rect.height - config.TOP_BAR_HEIGHT)

    def get_sdl_window(self):
        if Window is None:
            return None
        try:
            return Window.from_display_module()
        except (pygame.error, RuntimeError):
            return None

    def get_native_window_id(self) -> int | None:
        try:
            window_id = pygame.display.get_wm_info().get("window")
            return int(window_id) if window_id else None
        except (pygame.error, RuntimeError, TypeError, ValueError):
            return None

    def run(self, max_frames: int | None = None) -> int:
        frames = 0
        try:
            while self.running:
                raw_dt = self.clock.tick(config.FPS) / 1000.0
                self.handle_events()
                self.consume_camera_focus()
                self.update_hover_audio()
                self.audio.update_music()
                if not self.pause_menu.open and self.state.expedition_run is None:
                    self.handle_keyboard_pan(raw_dt)
                sim_dt = min(0.05, raw_dt) * self.state.time_scale
                if self.state.expedition_run is not None:
                    if not self.pause_menu.open and not self.state.paused:
                        self.state.expedition_run.update(sim_dt, pygame.key.get_pressed(), pygame.mouse.get_pos(), self.viewport)
                        if self.state.expedition_run.finished_result is not None:
                            self.state.finish_expedition_run(self.state.expedition_run.finished_result)
                else:
                    self.state.update(sim_dt)
                self.draw()
                frames += 1
                if max_frames is not None and frames >= max_frames:
                    self.running = False
        finally:
            self.hud.close_all_windows()
        pygame.quit()
        return 0

    def handle_events(self) -> None:
        viewport = self.viewport
        self.hud.layout_buttons(self.screen_rect, viewport, self.state)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                if self.state.expedition_run is not None:
                    self.state.abort_expedition_as_loss()
                else:
                    self.running = False
                    self.hud.close_all_windows()
            elif self.hud.handle_external_event(event, self.state):
                continue
            elif event.type == pygame.VIDEORESIZE:
                self.screen = pygame.display.set_mode((max(960, event.w), max(600, event.h)), self.display_flags)
                self.window = self.get_sdl_window()
                self.hud.set_parent_window(self.get_native_window_id())
                self.camera.clamp_to_world(self.viewport)
            elif event.type == pygame.KEYDOWN:
                self.handle_keydown(event)
            elif self.pause_menu.open:
                result = self.pause_menu.handle_event(event, self.screen_rect, self.state, self.audio)
                if result == "quit":
                    self.running = False
                    self.hud.close_all_windows()
                continue
            elif event.type == pygame.MOUSEWHEEL:
                if self.hud.handle_event(event, self.state, self.screen_rect, viewport):
                    continue
                mouse = pygame.mouse.get_pos()
                if viewport.collidepoint(mouse):
                    self.camera.zoom_at(1.1 if event.y > 0 else 0.9, mouse, viewport)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and event.pos[1] < config.TITLE_BAR_HEIGHT:
                    self.handle_title_bar_click(event.pos)
                    continue
                if self.hud.handle_event(event, self.state, self.screen_rect, viewport):
                    continue
                if self.state.expedition_run is not None:
                    if self.state.expedition_run.handle_event(event, viewport):
                        continue
                    if viewport.collidepoint(event.pos):
                        continue
                if viewport.collidepoint(event.pos):
                    if event.button == 2:
                        self.dragging = True
                        self.last_mouse = pygame.Vector2(event.pos)
                    elif event.button == 1:
                        if self.state.build_mode or self.state.station_mode:
                            world = self.camera.screen_to_world(event.pos, viewport)
                            self.state.handle_world_click(world, event.button)
                        else:
                            self.left_selecting = True
                            self.select_start = pygame.Vector2(event.pos)
                            self.select_current = pygame.Vector2(event.pos)
                    elif event.button == 3:
                        world = self.camera.screen_to_world(event.pos, viewport)
                        self.state.handle_world_click(world, event.button)
            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1:
                    self.title_dragging = False
                if self.hud.handle_event(event, self.state, self.screen_rect, viewport):
                    continue
                if self.state.expedition_run is not None and viewport.collidepoint(event.pos):
                    continue
                if event.button == 2:
                    self.dragging = False
                elif event.button == 1 and self.left_selecting:
                    self.finish_left_selection(event.pos, viewport)
            elif event.type == pygame.MOUSEMOTION and self.title_dragging:
                self.move_window(event.rel)
            elif event.type == pygame.MOUSEMOTION:
                if self.hud.handle_event(event, self.state, self.screen_rect, viewport):
                    continue
                if self.dragging:
                    current = pygame.Vector2(event.pos)
                    self.camera.pan_screen_delta(current - self.last_mouse, viewport)
                    self.last_mouse = current
                elif self.left_selecting:
                    self.select_current = pygame.Vector2(event.pos)

    def title_control_rects(self) -> dict[str, pygame.Rect]:
        title_bar = pygame.Rect(0, 0, self.screen_rect.width, config.TITLE_BAR_HEIGHT)
        top = title_bar.top + 5
        return {
            "minimize": pygame.Rect(title_bar.right - 86, top, 22, 20),
            "maximize": pygame.Rect(title_bar.right - 58, top, 22, 20),
            "close": pygame.Rect(title_bar.right - 30, top, 22, 20),
        }

    def handle_title_bar_click(self, pos: tuple[int, int]) -> None:
        for name, rect in self.title_control_rects().items():
            if not rect.collidepoint(pos):
                continue
            if name == "close":
                if self.state.expedition_run is not None:
                    self.state.abort_expedition_as_loss()
                else:
                    self.running = False
                    self.hud.close_all_windows()
            elif name == "minimize":
                pygame.display.iconify()
            elif name == "maximize":
                self.toggle_maximized()
            return
        self.title_dragging = True

    def move_window(self, rel: tuple[int, int]) -> None:
        if self.window is None or self.maximized:
            return
        try:
            x, y = self.window.position
            self.window.position = (x + rel[0], y + rel[1])
        except (pygame.error, RuntimeError, AttributeError):
            return

    def toggle_maximized(self) -> None:
        if not self.maximized:
            self.restore_size = self.screen.get_size()
            if self.window is not None:
                try:
                    self.restore_position = self.window.position
                except (pygame.error, RuntimeError, AttributeError):
                    self.restore_position = None
            desktop = pygame.display.get_desktop_sizes()[0]
            self.screen = pygame.display.set_mode(desktop, self.display_flags)
            self.window = self.get_sdl_window()
            self.hud.set_parent_window(self.get_native_window_id())
            if self.window is not None:
                try:
                    self.window.position = (0, 0)
                except (pygame.error, RuntimeError, AttributeError):
                    pass
            self.maximized = True
        else:
            self.screen = pygame.display.set_mode(self.restore_size, self.display_flags)
            self.window = self.get_sdl_window()
            self.hud.set_parent_window(self.get_native_window_id())
            if self.window is not None and self.restore_position is not None:
                try:
                    self.window.position = self.restore_position
                except (pygame.error, RuntimeError, AttributeError):
                    pass
            self.maximized = False
        self.camera.clamp_to_world(self.viewport)

    def finish_left_selection(self, pos: tuple[int, int], viewport: pygame.Rect) -> None:
        self.left_selecting = False
        end = pygame.Vector2(pos)
        distance = end.distance_to(self.select_start)
        if distance <= 6:
            if viewport.collidepoint(pos):
                world = self.camera.screen_to_world(pos, viewport)
                troop = self.state.find_troop_at(world)
                now = pygame.time.get_ticks()
                if (
                    troop is not None
                    and self.last_unit_click_kind == troop.kind
                    and now - self.last_unit_click_time <= self.double_click_ms
                ):
                    self.state.select_troops_by_kind(troop.kind)
                    self.last_unit_click_time = 0
                    self.last_unit_click_kind = None
                else:
                    self.state.handle_world_click(world, 1)
                    self.last_unit_click_time = now if troop is not None else 0
                    self.last_unit_click_kind = troop.kind if troop is not None else None
            return

        start_world = self.camera.screen_to_world(self.select_start, viewport)
        end_world = self.camera.screen_to_world(end, viewport)
        left = min(start_world.x, end_world.x)
        top = min(start_world.y, end_world.y)
        width = abs(start_world.x - end_world.x)
        height = abs(start_world.y - end_world.y)
        self.state.select_troops_in_rect(pygame.Rect(left, top, width, height))

    def handle_keydown(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_ESCAPE:
            self.pause_menu.toggle(self.state, self.audio)
            self.state.play_sound("menu_select")
        elif self.pause_menu.open:
            result = self.pause_menu.handle_event(event, self.screen_rect, self.state, self.audio)
            if result == "quit":
                self.running = False
                self.hud.close_all_windows()
        elif event.key in (pygame.K_BACKSPACE, pygame.K_DELETE):
            if self.hud.active_panel is not None:
                self.hud.active_panel = None
            else:
                self.state.cancel()
        elif event.key in (pygame.K_SPACE, pygame.K_p):
            self.state.paused = not self.state.paused
        elif event.key == pygame.K_b:
            self.toggle_hud_panel("build")
        elif event.key == pygame.K_u:
            if self.hud._living_barracks(self.state):
                self.toggle_hud_panel("units")
        elif event.key == pygame.K_i:
            if any(slot is not None for slot in self.state.inventory.slots) or any(getattr(building, "kind", "") == "library" and getattr(building, "alive", False) for building in self.state.buildings):
                self.toggle_hud_panel("items")
        elif event.key == pygame.K_t:
            if self.hud._living_research_labs(self.state):
                self.toggle_hud_panel("research")
        elif event.key == pygame.K_r and self.state.game_over:
            self.state.reset()
            self.state.audio = self.audio
            self.audio.play_music()
            self.camera.center_on(self.state.grid.world_center(self.state.grid.townhall_cell), self.viewport)
        elif event.key == pygame.K_1:
            if self.state.selected_troops:
                self.state.assign_control_group(0)
            elif not self.state.select_control_group(0):
                self.state.set_build_mode("wall")
            else:
                return
            self.state.play_sound("menu_select")
        elif event.key == pygame.K_2:
            if self.state.selected_troops:
                self.state.assign_control_group(1)
            elif not self.state.select_control_group(1):
                self.state.set_build_mode("archer")
            else:
                return
            self.state.play_sound("menu_select")
        elif event.key == pygame.K_3:
            if self.state.selected_troops:
                self.state.assign_control_group(2)
            elif not self.state.select_control_group(2):
                self.state.set_build_mode("cannon")
            else:
                return
            self.state.play_sound("menu_select")
        elif event.key == pygame.K_4:
            if self.state.selected_troops:
                self.state.assign_control_group(3)
            elif not self.state.select_control_group(3):
                self.state.set_build_mode("wizard")
            else:
                return
            self.state.play_sound("menu_select")
        elif event.key == pygame.K_5:
            if self.state.selected_troops:
                self.state.assign_control_group(4)
            elif not self.state.select_control_group(4):
                self.state.set_build_mode("barracks")
            else:
                return
            self.state.play_sound("menu_select")
        elif event.key == pygame.K_6:
            self.state.set_build_mode("house")
            self.state.play_sound("menu_select")
        elif event.key == pygame.K_7:
            self.state.set_build_mode("research")
            self.state.play_sound("menu_select")
        elif event.key == pygame.K_8:
            self.state.set_build_mode("library")
            self.state.play_sound("menu_select")
        elif event.key == pygame.K_9:
            self.state.set_build_mode("core")
            self.state.play_sound("menu_select")
        elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_RIGHTBRACKET):
            self.state.time_scale = min(3.0, self.state.time_scale + 0.5)
            self.state.paused = False
        elif event.key in (pygame.K_MINUS, pygame.K_LEFTBRACKET):
            self.state.time_scale = max(0.5, self.state.time_scale - 0.5)
            self.state.paused = False

    def toggle_hud_panel(self, panel: str) -> None:
        if panel == "units":
            self.hud.units_panel_barracks = None
        elif panel == "research":
            self.hud.research_panel_lab = None
        if self.hud._using_external_windows():
            self.hud.toggle_panel_window(panel)
            self.hud.active_panel = panel if self.hud.panel_windows[panel].visible else None
        else:
            self.hud.active_panel = None if self.hud.active_panel == panel else panel
        self.hud.dialog_scroll = 0.0
        self.hud.dialog_scroll_max = 0.0
        self.state.play_sound("menu_select")

    def consume_camera_focus(self) -> None:
        focus = self.state.consume_camera_focus()
        if focus is not None:
            self.camera.center_on(focus, self.viewport)

    def handle_keyboard_pan(self, dt: float) -> None:
        keys = pygame.key.get_pressed()
        direction = pygame.Vector2(0, 0)
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            direction.x -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            direction.x += 1
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            direction.y -= 1
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            direction.y += 1
        if direction.length_squared() > 0:
            direction = direction.normalize()
            self.camera.pan_world_delta(direction * config.PAN_SPEED * dt / self.camera.zoom, self.viewport)

    def update_hover_audio(self) -> None:
        viewport = self.viewport
        self.hud.layout_buttons(self.screen_rect, viewport, self.state)
        pos = pygame.mouse.get_pos()
        target = self.pause_menu.hover_target_at(pos, self.screen_rect)
        if self.pause_menu.open:
            pass
        elif target is None:
            target = self.title_hover_target_at(pos)
        if not self.pause_menu.open and target is None:
            target = self.hud.hover_target_at(pos, viewport, self.state)
        if not self.pause_menu.open and target is None:
            target = self.world_hover_target_at(pos, viewport)
        if target != self.hover_audio_target:
            if target is not None:
                self.state.play_sound("menu_hover")
            hover_feedback.set_hover_target(target)
            self.hover_audio_target = target

    def title_hover_target_at(self, pos: tuple[int, int]):
        for name, rect in self.title_control_rects().items():
            if rect.collidepoint(pos):
                return ("window", name)
        return None

    def world_hover_target_at(self, pos: tuple[int, int], viewport: pygame.Rect):
        if (
            not viewport.collidepoint(pos)
            or self.state.expedition_run is not None
            or self.state.round_events.awaiting_choice
            or self.state.build_mode
            or self.state.station_mode
        ):
            return None
        world = self.camera.screen_to_world(pos, viewport)
        troop = self.state.find_troop_at(world)
        if troop is not None:
            return ("troop", id(troop))
        cell = self.state.grid.cell_from_world(world)
        structure = self.state.grid.towers.get(cell)
        if getattr(structure, "alive", False) and getattr(structure, "target_class", "") != "core":
            if getattr(structure, "kind", "") in {"archer", "cannon", "wizard", "barracks", "house", "extractor", "torch", "training_grounds", "expedition_campsite", "research", "library", "shield_generator"}:
                return ("structure", id(structure))
        if cell in self.state.grid.walls:
            return ("wall", cell)
        return None

    def draw(self) -> None:
        self.screen.fill(config.PALETTE.bg)
        viewport = self.viewport
        if self.state.expedition_run is not None:
            self.state.expedition_run.draw(self.screen, viewport, self.fonts, pygame.mouse.get_pos())
        else:
            self.camera.clamp_to_world(viewport)
            self.state.draw_world(self.screen, self.camera, viewport, self.fonts, pygame.mouse.get_pos(), self.hover_audio_target)
            self.draw_selection_box(viewport)
        self.hud.draw(self.screen, self.screen_rect, viewport, self.state)
        self.pause_menu.draw(self.screen, self.screen_rect, self.audio)
        pygame.display.flip()

    def draw_selection_box(self, viewport: pygame.Rect) -> None:
        if not self.left_selecting or self.select_current.distance_to(self.select_start) <= 6:
            return
        start = self.select_start
        current = self.select_current
        left = max(viewport.left, min(start.x, current.x))
        top = max(viewport.top, min(start.y, current.y))
        right = min(viewport.right, max(start.x, current.x))
        bottom = min(viewport.bottom, max(start.y, current.y))
        rect = pygame.Rect(left, top, max(1, right - left), max(1, bottom - top))
        draw_rect_alpha(self.screen, rect, config.PALETTE.white, 28)
        pygame.draw.rect(self.screen, config.PALETTE.white, rect, 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="Run a few frames and exit.")
    args = parser.parse_args(argv)
    app = BastionApp()
    return app.run(max_frames=8 if args.smoke else None)


if __name__ == "__main__":
    raise SystemExit(main())
