from __future__ import annotations

import math
from dataclasses import dataclass

import pygame

from bastion import config
from bastion.engine import hover_feedback
from bastion.ui.widgets import Button


@dataclass
class Slider:
    rect: pygame.Rect
    label: str
    key: str


@dataclass
class ToggleControl:
    rect: pygame.Rect
    tab_id: str
    setting_id: str


class PauseMenu:
    def __init__(self, fonts: dict[str, pygame.font.Font]) -> None:
        self.fonts = fonts
        self.open = False
        self.view = "main"
        self.was_paused = False
        self.buttons: list[Button] = []
        self.player_buttons: list[Button] = []
        self.sliders: list[Slider] = []
        self.setting_toggles: list[ToggleControl] = []
        self.dragging_slider: str | None = None
        self.visual_energy = 0.0
        self.visual_impact = 0.0
        self.visual_levels: list[float] = []

    def show(self, state, audio) -> None:
        if self.open:
            return
        self.open = True
        self.view = "main"
        self.was_paused = state.paused
        state.paused = True
        audio.refresh_music_library()

    def hide(self, state) -> None:
        if not self.open:
            return
        self.open = False
        state.paused = False
        self.dragging_slider = None

    def toggle(self, state, audio) -> None:
        if self.open:
            self.hide(state)
        else:
            self.show(state, audio)

    def handle_event(self, event: pygame.event.Event, screen_rect: pygame.Rect, state, audio, settings=None):
        if not self.open:
            return None
        self.layout(screen_rect, settings)

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.hide(state)
                return "resume"
            if event.key in (pygame.K_LEFT, pygame.K_a):
                audio.previous_music()
                audio.play("menu_select")
                return True
            if event.key in (pygame.K_RIGHT, pygame.K_d):
                audio.next_music()
                audio.play("menu_select")
                return True
            if event.key in (pygame.K_SPACE, pygame.K_p):
                if self.view == "settings":
                    self._toggle_first_visible_setting(state, audio, settings)
                else:
                    audio.toggle_music_pause()
                    audio.play("menu_select")
                return True
            return True

        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging_slider = None
            return True

        if event.type == pygame.MOUSEMOTION and self.dragging_slider is not None:
            self._set_slider_from_pos(self.dragging_slider, event.pos[0], audio)
            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            for slider in self.sliders:
                if slider.rect.inflate(0, 24).collidepoint(pos):
                    self.dragging_slider = slider.key
                    self._set_slider_from_pos(slider.key, pos[0], audio)
                    audio.play("menu_select")
                    return True

            for toggle in self.setting_toggles:
                if toggle.rect.collidepoint(pos):
                    self._toggle_setting(toggle.tab_id, toggle.setting_id, state, settings)
                    audio.play("menu_select")
                    return True

            for button in self.player_buttons:
                if button.contains(pos):
                    self._execute_player(button.command, audio)
                    audio.play("menu_select")
                    return True

            for button in self.buttons:
                if button.contains(pos):
                    audio.play("menu_select")
                    return self._execute_button(button.command, state, audio)
            return True

        return True

    def _execute_button(self, command: str, state, audio):
        if command == "resume":
            self.hide(state)
            return "resume"
        if command == "audio":
            self.view = "audio"
            return True
        if command == "settings":
            self.view = "settings"
            return True
        if command.startswith("settings_tab:"):
            self.view = "settings"
            return True
        if command == "back":
            self.view = "main"
            return True
        if command == "quit":
            return "quit"
        return True

    def _execute_player(self, command: str, audio) -> None:
        if command == "music_prev":
            audio.previous_music()
        elif command == "music_play":
            audio.toggle_music_pause()
        elif command == "music_next":
            audio.next_music()

    def _set_slider_from_pos(self, key: str, x: int, audio) -> None:
        slider = next((item for item in self.sliders if item.key == key), None)
        if slider is None:
            return
        value = (x - slider.rect.left) / max(1, slider.rect.width)
        value = max(0.0, min(1.0, value))
        if key == "master":
            audio.set_master_volume(value)
        elif key == "music":
            audio.set_music_volume(value)
        elif key == "sfx":
            audio.set_sfx_volume(value)

    def _toggle_first_visible_setting(self, state, audio, settings) -> None:
        if not self.setting_toggles:
            return
        toggle = self.setting_toggles[0]
        self._toggle_setting(toggle.tab_id, toggle.setting_id, state, settings)
        audio.play("menu_select")

    def _toggle_setting(self, tab_id: str, setting_id: str, state, settings) -> None:
        if settings is None:
            return
        value = settings.toggle_bool(tab_id, setting_id)
        if tab_id == "gameplay" and setting_id == "tutorial_enabled":
            state.set_tutorial_enabled(value)
            state.message("TUTORIAL ENABLED" if value else "TUTORIAL DISABLED")
        settings.save()

    def layout(self, screen_rect: pygame.Rect, settings=None) -> None:
        panel = self.panel_rect(screen_rect)
        left_x = panel.left + 32
        menu_y = panel.top + 206
        button_w = 202
        button_h = 38
        self.buttons = []
        if self.view == "main":
            entries = (("RESUME", "resume"), ("SETTINGS", "settings"), ("AUDIO", "audio"), ("CLOSE GAME", "quit"))
            for index, (label, command) in enumerate(entries):
                rect = pygame.Rect(left_x, menu_y + index * 50, button_w, button_h)
                self.buttons.append(Button(rect, label, command))
        else:
            rect = pygame.Rect(left_x, panel.bottom - 72, 122, 36)
            self.buttons.append(Button(rect, "BACK", "back"))

        self.player_buttons = []
        player = self.player_rect(panel)
        size = 42
        center_y = player.bottom - 46
        for cx, command, label in (
            (player.centerx - 62, "music_prev", "PREV"),
            (player.centerx, "music_play", "PLAY"),
            (player.centerx + 62, "music_next", "NEXT"),
        ):
            self.player_buttons.append(Button(pygame.Rect(cx - size // 2, center_y - size // 2, size, size), label, command))

        self.sliders = []
        self.setting_toggles = []
        if self.view == "audio":
            sx = left_x
            sy = panel.top + 188
            width = 214
            for index, (label, key) in enumerate((("MASTER", "master"), ("MUSIC", "music"), ("SFX", "sfx"))):
                self.sliders.append(Slider(pygame.Rect(sx, sy + index * 76, width, 4), label, key))
        elif self.view == "settings":
            tab = None
            if settings is not None:
                tab = next((item for item in settings.definition.tabs if item.tab_id == "gameplay"), None)
            setting_ids = (
                tuple(setting.setting_id for setting in tab.settings if setting.value_type == "bool")
                if tab is not None
                else ("tutorial_enabled",)
            )
            for index, setting_id in enumerate(setting_ids):
                self.setting_toggles.append(
                    ToggleControl(
                        pygame.Rect(panel.left + 300, panel.top + 174 + index * 74, player.width - 72, 62),
                        "gameplay",
                        setting_id,
                    )
                )

    def panel_rect(self, screen_rect: pygame.Rect) -> pygame.Rect:
        width = min(920, screen_rect.width - 64)
        height = min(560, screen_rect.height - 64)
        rect = pygame.Rect(0, 0, width, height)
        rect.center = screen_rect.center
        return rect

    def player_rect(self, panel: pygame.Rect) -> pygame.Rect:
        left_column = 250
        gap = 28
        margin = 32
        left = panel.left + left_column + gap
        width = panel.right - left - margin
        return pygame.Rect(left, panel.top + 78, max(360, width), panel.height - 132)

    def hover_target_at(self, pos: tuple[int, int], screen_rect: pygame.Rect, settings=None):
        if not self.open:
            return None
        self.layout(screen_rect, settings)
        for slider in self.sliders:
            if slider.rect.inflate(0, 24).collidepoint(pos):
                return ("pause_slider", slider.key)
        for toggle in self.setting_toggles:
            if toggle.rect.collidepoint(pos):
                return ("pause_setting", f"{toggle.tab_id}:{toggle.setting_id}")
        for button in self.player_buttons:
            if button.contains(pos):
                return ("pause_player", button.command)
        for button in self.buttons:
            if button.contains(pos):
                return ("pause_button", button.command)
        return None

    def draw(self, surface: pygame.Surface, screen_rect: pygame.Rect, audio, settings=None) -> None:
        if not self.open:
            return
        self.layout(screen_rect, settings)
        palette = config.PALETTE
        veil = pygame.Surface(screen_rect.size, pygame.SRCALPHA)
        veil.fill((0, 0, 0, 212))
        surface.blit(veil, (0, 0))

        panel = self.panel_rect(screen_rect)
        self._alpha_rect(surface, panel, (0, 0, 0, 236))
        pygame.draw.rect(surface, palette.line_bright, panel, 1)
        self._draw_corner_marks(surface, panel, palette.white)

        divider_x = panel.left + 250
        pygame.draw.line(surface, palette.line, (divider_x, panel.top + 30), (divider_x, panel.bottom - 30), 1)
        pygame.draw.line(surface, palette.white, (panel.left + 32, panel.top + 82), (panel.left + 216, panel.top + 82), 1)

        eyebrow = self.fonts["tiny"].render("BASTION CORE", True, palette.text_dim)
        surface.blit(eyebrow, (panel.left + 32, panel.top + 30))
        title_text = {"main": "PAUSED", "audio": "AUDIO", "settings": "SETTINGS"}.get(self.view, "PAUSED")
        title = self.fonts["large"].render(title_text, True, palette.white)
        surface.blit(title, (panel.left + 30, panel.top + 46))

        if self.view == "main":
            self._draw_session_summary(surface, panel, audio)
        elif self.view == "audio":
            self._draw_audio_settings(surface, panel, audio)
        elif self.view == "settings":
            self._draw_gameplay_settings(surface, panel, settings)

        for button in self.buttons:
            self._draw_menu_button(surface, button)

        self._draw_player(surface, self.player_rect(panel), audio)

    def _draw_player(self, surface: pygame.Surface, rect: pygame.Rect, audio) -> None:
        palette = config.PALETTE
        self._alpha_rect(surface, rect, (0, 0, 0, 212))
        pygame.draw.rect(surface, palette.line_bright, rect, 1)
        pygame.draw.line(surface, palette.white, (rect.left + 1, rect.top + 1), (rect.right - 2, rect.top + 1), 1)

        header = self.fonts["tiny"].render("MP3 PLAYER", True, palette.text_dim)
        surface.blit(header, (rect.left + 24, rect.top + 20))

        state_label = getattr(audio, "music_state", "stopped").upper()
        if not audio.music_track_count():
            state_label = "NO TRACK"
        status = self.fonts["tiny"].render(state_label, True, palette.text)
        status_pos = (rect.right - 24 - status.get_width(), rect.top + 20)
        surface.blit(status, status_pos)
        indicator = (status_pos[0] - 13, rect.top + 25)
        indicator_color = palette.white if audio.is_music_playing else palette.line_bright
        pygame.draw.circle(surface, indicator_color, indicator, 3)

        track = self._fit_text(audio.current_track_name.upper(), self.fonts["medium"], rect.width - 48)
        track_text = self.fonts["medium"].render(track, True, palette.white)
        surface.blit(track_text, (rect.left + 24, rect.top + 48))

        count = audio.music_track_count()
        meta_label = f"{count:02d} TRACKS" if count != 1 else "01 TRACK"
        meta = self.fonts["tiny"].render(meta_label, True, palette.text_dim)
        surface.blit(meta, (rect.left + 24, rect.top + 74))

        progress = audio.current_music_progress()
        self._draw_progress(surface, pygame.Rect(rect.left + 24, rect.top + 100, rect.width - 48, 8), progress)

        controls_top = rect.bottom - 78
        viz = pygame.Rect(rect.left + 24, rect.top + 128, rect.width - 48, max(112, controls_top - rect.top - 154))
        self._draw_visualizer(surface, viz, audio.music_energy(), progress, audio.is_music_playing)

        for button in self.player_buttons:
            self._draw_transport_button(surface, button, audio)

    def _draw_visualizer(self, surface: pygame.Surface, rect: pygame.Rect, energy: float, progress: float, playing: bool) -> None:
        palette = config.PALETTE
        energy = max(0.0, min(1.0, energy))
        previous = self.visual_energy
        if self.visual_energy < 0.04 and energy > 0.1:
            self.visual_energy = energy * 0.45
        attack = 0.34 if energy > self.visual_energy else 0.08
        self.visual_energy += (energy - self.visual_energy) * attack
        self.visual_impact = max(self.visual_impact * 0.86, max(0.0, energy - previous) * 1.75)
        eased = self.visual_energy
        impact = min(1.0, self.visual_impact)
        now = pygame.time.get_ticks() / 1000.0

        pygame.draw.rect(surface, palette.black, rect)
        pygame.draw.rect(surface, palette.line_bright, rect, 1)
        inner = rect.inflate(-20, -18)
        if inner.width <= 0 or inner.height <= 0:
            return

        for index in range(1, 6):
            x = inner.left + inner.width * index // 6
            pygame.draw.line(surface, (25, 25, 25), (x, inner.top), (x, inner.bottom), 1)
        for index in range(1, 4):
            y = inner.top + inner.height * index // 4
            pygame.draw.line(surface, (22, 22, 22), (inner.left, y), (inner.right, y), 1)

        baseline = inner.centery
        pygame.draw.line(surface, palette.line, (inner.left, baseline), (inner.right, baseline), 1)

        bar_count = max(26, min(64, inner.width // 8))
        if len(self.visual_levels) != bar_count:
            self.visual_levels = [0.08 + eased * 0.12 for _ in range(bar_count)]

        step = inner.width / bar_count
        outline: list[tuple[int, int]] = []
        for index in range(bar_count):
            t = (index + 0.5) / bar_count
            seed = 0.5 + 0.5 * math.sin(index * 2.137 + math.sin(index * 0.73) * 3.0)
            carrier = 0.5 + 0.5 * math.sin(now * (1.55 + seed * 2.1) + index * 0.47)
            fold = 0.5 + 0.5 * math.sin(now * (0.78 + seed * 1.3) - index * 0.91)
            center_weight = math.sin(math.pi * t) ** 0.58
            target = (0.07 + eased * 0.88) * (0.34 + carrier * 0.66) * (0.58 + fold * 0.42)
            target = target * center_weight + impact * center_weight * 0.18
            smoothing = 0.28 if target > self.visual_levels[index] else 0.12
            self.visual_levels[index] += (target - self.visual_levels[index]) * smoothing
            level = self.visual_levels[index] if playing else self.visual_levels[index] * 0.35
            half_height = max(2, int(inner.height * 0.47 * level))
            x = int(inner.left + index * step + step * 0.25)
            width = max(2, int(step * 0.5))
            brightness = 76 + int(166 * min(1.0, level * 1.8 + eased * 0.25))
            color = (brightness, brightness, brightness)
            pygame.draw.rect(surface, color, pygame.Rect(x, baseline - half_height, width, half_height * 2))
            outline.append((x + width // 2, baseline - half_height))

        if len(outline) > 1:
            pygame.draw.aalines(surface, palette.white, False, outline)
            lower = [(x, baseline + (baseline - y)) for x, y in outline]
            pygame.draw.aalines(surface, palette.line_bright, False, lower)

        play_x = inner.left + int(inner.width * max(0.0, min(1.0, progress)))
        pygame.draw.line(surface, palette.white, (play_x, rect.top + 5), (play_x, rect.bottom - 6), 1)

    def _draw_progress(self, surface: pygame.Surface, rect: pygame.Rect, progress: float) -> None:
        palette = config.PALETTE
        progress = max(0.0, min(1.0, progress))
        y = rect.centery
        pygame.draw.line(surface, palette.line, (rect.left, y), (rect.right, y), 1)
        fill_right = rect.left + int(rect.width * progress)
        pygame.draw.line(surface, palette.white, (rect.left, y), (fill_right, y), 2)
        pygame.draw.circle(surface, palette.white, (fill_right, y), 4)

    def _draw_transport_button(self, surface: pygame.Surface, button: Button, audio) -> None:
        palette = config.PALETTE
        hovered = button.contains(pygame.mouse.get_pos())
        rect = hover_feedback.scaled_rect(button.rect, hovered)
        active = button.command == "music_play" and audio.is_music_playing
        fill = palette.white if hovered or active else palette.black
        stroke = palette.white if hovered or active else palette.line_bright
        icon = palette.black if hovered or active else palette.white
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, stroke, rect, 1)
        center = rect.center

        if button.command == "music_prev":
            pygame.draw.line(surface, icon, (center[0] - 11, center[1] - 10), (center[0] - 11, center[1] + 10), 2)
            pygame.draw.polygon(surface, icon, [(center[0] - 8, center[1]), (center[0] + 4, center[1] - 10), (center[0] + 4, center[1] + 10)])
            pygame.draw.polygon(surface, icon, [(center[0] + 2, center[1]), (center[0] + 14, center[1] - 10), (center[0] + 14, center[1] + 10)])
        elif button.command == "music_next":
            pygame.draw.line(surface, icon, (center[0] + 11, center[1] - 10), (center[0] + 11, center[1] + 10), 2)
            pygame.draw.polygon(surface, icon, [(center[0] + 8, center[1]), (center[0] - 4, center[1] - 10), (center[0] - 4, center[1] + 10)])
            pygame.draw.polygon(surface, icon, [(center[0] - 2, center[1]), (center[0] - 14, center[1] - 10), (center[0] - 14, center[1] + 10)])
        elif audio.is_music_playing:
            pygame.draw.rect(surface, icon, pygame.Rect(center[0] - 8, center[1] - 11, 5, 22))
            pygame.draw.rect(surface, icon, pygame.Rect(center[0] + 3, center[1] - 11, 5, 22))
        else:
            pygame.draw.polygon(surface, icon, [(center[0] - 6, center[1] - 12), (center[0] - 6, center[1] + 12), (center[0] + 12, center[1])])

    def _draw_menu_button(self, surface: pygame.Surface, button: Button) -> None:
        if not button.visible:
            return
        palette = config.PALETTE
        hovered = button.contains(pygame.mouse.get_pos())
        rect = hover_feedback.scaled_rect(button.rect, hovered)
        fill = palette.white if hovered else palette.black
        text_color = palette.black if hovered else palette.text
        border = palette.white if hovered else palette.line_bright
        pygame.draw.rect(surface, fill, rect)
        pygame.draw.rect(surface, border, rect, 1)

        label = self.fonts["small"].render(button.label, True, text_color)
        surface.blit(label, (rect.left + 14, rect.centery - label.get_height() // 2))
        chevron_x = rect.right - 18
        chevron_y = rect.centery
        pygame.draw.line(surface, text_color, (chevron_x - 4, chevron_y - 6), (chevron_x + 3, chevron_y), 1)
        pygame.draw.line(surface, text_color, (chevron_x + 3, chevron_y), (chevron_x - 4, chevron_y + 6), 1)

    def _draw_audio_settings(self, surface: pygame.Surface, panel: pygame.Rect, audio) -> None:
        palette = config.PALETTE
        x = panel.left + 32
        title = self.fonts["small"].render("OUTPUT", True, palette.text)
        surface.blit(title, (x, panel.top + 122))
        values = {
            "master": audio.master_volume,
            "music": audio.music_volume,
            "sfx": audio.sfx_volume,
        }
        for slider in self.sliders:
            value = values[slider.key]
            hovered = slider.rect.inflate(0, 24).collidepoint(pygame.mouse.get_pos())
            track = hover_feedback.scaled_rect(slider.rect, hovered)
            hover_plate = hover_feedback.scaled_rect(slider.rect.inflate(10, 34), hovered)
            fill_color = palette.white if hovered else palette.black
            mark_color = palette.black if hovered else palette.white
            text_color = palette.black if hovered else palette.text
            dim_color = palette.black if hovered else palette.text_dim
            line_color = palette.black if hovered else palette.line
            if hovered:
                pygame.draw.rect(surface, fill_color, hover_plate)
                pygame.draw.rect(surface, palette.white, hover_plate, 1)
            label = self.fonts["tiny"].render(slider.label, True, dim_color)
            surface.blit(label, (slider.rect.left, slider.rect.top - 28))
            percent = self.fonts["tiny"].render(f"{int(value * 100):3d}%", True, text_color)
            surface.blit(percent, (slider.rect.right - percent.get_width(), slider.rect.top - 28))

            y = track.centery
            pygame.draw.line(surface, line_color, (track.left, y), (track.right, y), 1)
            fill_x = track.left + int(track.width * value)
            pygame.draw.line(surface, mark_color, (track.left, y), (fill_x, y), 3)
            knob = pygame.Rect(0, 0, 10 if hovered else 8, 21 if hovered else 18)
            knob.center = (fill_x, y)
            pygame.draw.rect(surface, mark_color, knob)
            pygame.draw.rect(surface, fill_color, knob, 1)

    def _draw_gameplay_settings(self, surface: pygame.Surface, panel: pygame.Rect, settings) -> None:
        palette = config.PALETTE
        x = panel.left + 32
        tab = self.fonts["small"].render("GAMEPLAY", True, palette.text)
        surface.blit(tab, (x, panel.top + 122))
        underline_y = panel.top + 148
        pygame.draw.line(surface, palette.white, (x, underline_y), (x + tab.get_width(), underline_y), 1)

        if settings is None:
            return
        for toggle in self.setting_toggles:
            definitions = settings.definition.setting(toggle.tab_id, toggle.setting_id)
            if definitions is None:
                continue
            enabled = settings.get_bool(toggle.tab_id, toggle.setting_id)
            hovered = toggle.rect.collidepoint(pygame.mouse.get_pos())
            rect = hover_feedback.scaled_rect(toggle.rect, hovered)
            fill = palette.white if hovered else palette.black
            border = palette.white if hovered else palette.line_bright
            text_color = palette.black if hovered else palette.text
            dim_color = palette.black if hovered else palette.text_dim
            pygame.draw.rect(surface, fill, rect)
            pygame.draw.rect(surface, border, rect, 1)
            pygame.draw.line(surface, dim_color, (rect.left + 1, rect.top + 1), (rect.right - 2, rect.top + 1), 1)

            label = self.fonts["small"].render(definitions.label, True, text_color)
            surface.blit(label, (rect.left + 18, rect.top + 12))
            description = self._fit_text(definitions.description.upper(), self.fonts["tiny"], rect.width - 172)
            desc = self.fonts["tiny"].render(description, True, dim_color)
            surface.blit(desc, (rect.left + 18, rect.top + 36))

            switch = pygame.Rect(0, 0, 108, 30)
            switch.right = rect.right - 18
            switch.centery = rect.centery
            pygame.draw.rect(surface, palette.black if hovered else palette.panel, switch)
            pygame.draw.rect(surface, palette.black if hovered else palette.white, switch, 1)
            active_rect = switch.inflate(-6, -6)
            half = active_rect.width // 2
            knob = pygame.Rect(active_rect.left + (half if enabled else 0), active_rect.top, half, active_rect.height)
            pygame.draw.rect(surface, palette.black if hovered else palette.white, knob)
            status = definitions.on_label if enabled else definitions.off_label
            status_text = self.fonts["tiny"].render(status, True, palette.white if hovered else palette.black)
            surface.blit(status_text, status_text.get_rect(center=knob.center))

    def _draw_session_summary(self, surface: pygame.Surface, panel: pygame.Rect, audio) -> None:
        palette = config.PALETTE
        x = panel.left + 32
        y = panel.top + 122
        label = self.fonts["small"].render("SESSION", True, palette.text)
        surface.blit(label, (x, y))
        y += 36
        rows = (
            ("STATE", "HOLD"),
            ("AUDIO", getattr(audio, "music_state", "STOPPED").upper()),
            ("LIBRARY", f"{audio.music_track_count():02d} TRACKS"),
        )
        for name, value in rows:
            name_text = self.fonts["tiny"].render(name, True, palette.text_dim)
            value_text = self.fonts["tiny"].render(value, True, palette.text)
            surface.blit(name_text, (x, y))
            surface.blit(value_text, (panel.left + 136, y))
            y += 18

    def _draw_corner_marks(self, surface: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int]) -> None:
        length = 18
        corners = (
            ((rect.left, rect.top + length), (rect.left, rect.top), (rect.left + length, rect.top)),
            ((rect.right - length, rect.top), (rect.right, rect.top), (rect.right, rect.top + length)),
            ((rect.left, rect.bottom - length), (rect.left, rect.bottom), (rect.left + length, rect.bottom)),
            ((rect.right - length, rect.bottom), (rect.right, rect.bottom), (rect.right, rect.bottom - length)),
        )
        for points in corners:
            pygame.draw.lines(surface, color, False, points, 2)

    def _fit_text(self, text: str, font: pygame.font.Font, max_width: int) -> str:
        if font.size(text)[0] <= max_width:
            return text
        suffix = "..."
        max_width = max(0, max_width - font.size(suffix)[0])
        while text and font.size(text)[0] > max_width:
            text = text[:-1]
        return text.rstrip() + suffix if text else suffix

    def _alpha_rect(self, surface: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int, int]) -> None:
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        overlay.fill(color)
        surface.blit(overlay, rect.topleft)
