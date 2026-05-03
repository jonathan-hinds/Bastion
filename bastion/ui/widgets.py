from __future__ import annotations

from dataclasses import dataclass

import pygame

from bastion import config
from bastion.engine import hover_feedback


@dataclass
class Button:
    rect: pygame.Rect
    label: str
    command: str
    value: object = None
    enabled: bool = True
    selected: bool = False
    visible: bool = True

    def draw(self, surface: pygame.Surface, font: pygame.font.Font, mouse_pos: tuple[int, int] | None = None) -> None:
        if not self.visible:
            return
        palette = config.PALETTE
        pos = pygame.mouse.get_pos() if mouse_pos is None else mouse_pos
        hovered = self.enabled and self.rect.collidepoint(pos)
        rect = hover_feedback.scaled_rect(self.rect, hovered)
        fill = palette.panel_2 if self.enabled else palette.bg
        if self.selected:
            fill = palette.white
        elif hovered:
            fill = palette.white
        pygame.draw.rect(surface, fill, rect)
        border = palette.white if self.enabled else palette.line_bright
        pygame.draw.rect(surface, border, rect, 1)
        if self.enabled and not self.selected:
            shine = palette.black if hovered else palette.line_bright
            pygame.draw.line(surface, shine, (rect.left + 1, rect.top + 1), (rect.right - 2, rect.top + 1), 1)
        text_color = palette.black if self.selected or hovered else (palette.text if self.enabled else palette.text_dim)
        text = self.label
        while text and font.size(text)[0] > rect.width - 10:
            text = text[:-2] + "."
        label = font.render(text, True, text_color)
        surface.blit(label, label.get_rect(center=rect.center))

    def contains(self, pos: tuple[int, int]) -> bool:
        return self.enabled and self.rect.collidepoint(pos)
