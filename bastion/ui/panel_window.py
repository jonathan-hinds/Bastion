from __future__ import annotations

import ctypes
from collections import deque
from dataclasses import dataclass
from typing import ClassVar, Deque

import pygame

from bastion import config

try:
    import tkinter as tk
except ImportError:
    tk = None


@dataclass
class PanelEvent:
    kind: str
    pos: tuple[int, int] = (0, 0)
    button: int = 0
    rel: tuple[int, int] = (0, 0)
    wheel_y: int = 0


class _TkHost:
    root: tk.Tk | None = None if tk is not None else None
    failed = False
    panel_windows: ClassVar[dict[str, tk.Toplevel]] = {}

    @classmethod
    def available(cls) -> bool:
        if tk is None or cls.failed:
            return False
        if cls.root is not None:
            return True
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
            cls.root.title("Bastion Panels")
        except Exception:
            cls.failed = True
            cls.root = None
            return False
        return True

    @classmethod
    def pump(cls) -> None:
        if cls.root is None:
            return
        try:
            cls.root.update_idletasks()
            cls.root.update()
        except tk.TclError:
            cls.failed = True
            cls.root = None

    @classmethod
    def register(cls, panel_id: str, window: tk.Toplevel) -> None:
        cls.destroy_duplicates(panel_id, keep=window)
        cls.panel_windows[panel_id] = window

    @classmethod
    def unregister(cls, panel_id: str, window: tk.Toplevel | None = None) -> None:
        if window is None or cls.panel_windows.get(panel_id) is window:
            cls.panel_windows.pop(panel_id, None)

    @classmethod
    def destroy_duplicates(cls, panel_id: str, keep: tk.Toplevel | None = None) -> None:
        stale = cls.panel_windows.get(panel_id)
        if stale is not None and stale is not keep:
            cls._destroy_window(stale)
            cls.panel_windows.pop(panel_id, None)
        if cls.root is None:
            return
        try:
            children = list(cls.root.winfo_children())
        except tk.TclError:
            return
        for child in children:
            if child is keep or getattr(child, "_bastion_panel_id", None) != panel_id:
                continue
            cls._destroy_window(child)

    @classmethod
    def _destroy_window(cls, window: tk.Toplevel) -> None:
        try:
            if int(window.winfo_exists()):
                window.destroy()
        except tk.TclError:
            pass


class PanelWindow:
    """A lightweight external OS window that presents a pygame-rendered panel.

    Tk owns the native window and mouse input. Pygame owns the pixels. This keeps
    the HUD renderer data driven while avoiding the unstable pygame._sdl2 window
    surface path that varies between pygame builds.
    """

    min_size = (320, 260)
    frame_interval_ms = 33
    resize_margin = 8

    def __init__(self, panel_id: str, title: str, size: tuple[int, int], position: tuple[int, int]) -> None:
        self.panel_id = panel_id
        self.title = title
        self.default_size = size
        self.default_position = position
        self.window: tk.Toplevel | None = None if tk is not None else None
        self.label: tk.Label | None = None if tk is not None else None
        self.image: tk.PhotoImage | None = None if tk is not None else None
        self.parent_hwnd: int | None = None
        self.buffer = pygame.Surface(size)
        self.dragging = False
        self.drag_start_cursor: tuple[int, int] | None = None
        self.drag_start_position: tuple[int, int] | None = None
        self.resizing = False
        self.resize_edges = ""
        self.resize_start_cursor: tuple[int, int] | None = None
        self.resize_start_geometry: tuple[int, int, int, int] | None = None
        self.last_mouse_pos = (0, 0)
        self.mouse_pos = (-1, -1)
        self.event_queue: Deque[PanelEvent] = deque()
        self.minimized = False
        self.maximized = False
        self.restore_geometry: tuple[int, int, int, int] | None = None
        self.last_upload_ms = -self.frame_interval_ms

    @property
    def available(self) -> bool:
        return _TkHost.available()

    @property
    def visible(self) -> bool:
        if self.window is None or self.minimized:
            return False
        if not self._window_exists():
            self._clear_window_refs()
            return False
        return True

    @property
    def id(self) -> int | None:
        if self.window is None:
            return None
        try:
            return int(self.window.winfo_id())
        except tk.TclError:
            return None

    @property
    def position(self) -> tuple[int, int]:
        if self.window is None:
            return self.default_position
        try:
            return int(self.window.winfo_x()), int(self.window.winfo_y())
        except tk.TclError:
            return self.default_position

    @property
    def size(self) -> tuple[int, int]:
        if self.window is None:
            return self.buffer.get_size()
        try:
            width = max(self.min_size[0], int(self.window.winfo_width()))
            height = max(self.min_size[1], int(self.window.winfo_height()))
            return width, height
        except tk.TclError:
            return self.buffer.get_size()

    @classmethod
    def pump_events(cls) -> None:
        _TkHost.pump()

    def set_parent_window(self, parent_hwnd: int | None) -> None:
        self.parent_hwnd = parent_hwnd
        self._keep_in_front()

    def show(self) -> None:
        if self.window is not None and not self._window_exists():
            self._clear_window_refs()
        if self.window is not None:
            self.minimized = False
            _TkHost.destroy_duplicates(self.panel_id, keep=self.window)
            try:
                self.window.deiconify()
                self._keep_in_front()
            except tk.TclError:
                self.close()
            return
        if not self.available or _TkHost.root is None:
            return
        width, height = self.default_size
        x, y = self.default_position
        try:
            _TkHost.destroy_duplicates(self.panel_id)
            self.window = tk.Toplevel(_TkHost.root)
            self.window._bastion_panel_id = self.panel_id
            self.window.title(self.title)
            self.window.overrideredirect(True)
            self.window.geometry(f"{width}x{height}+{x}+{y}")
            self.window.minsize(*self.min_size)
            self.window.configure(bg="#000000", highlightthickness=0, bd=0)
            self.window.protocol("WM_DELETE_WINDOW", self.close)
            self.label = tk.Label(self.window, bg="#000000", bd=0, highlightthickness=0)
            self.label.pack(fill="both", expand=True)
            self._bind_events(self.label)
            self.window.bind("<Destroy>", self._handle_destroy, add="+")
            self.window.bind("<Escape>", lambda _event: self.close())
            self.window.update_idletasks()
            self._keep_in_front()
            _TkHost.register(self.panel_id, self.window)
            self._resize_buffer(*self.size)
            self.minimized = False
            self.maximized = False
            self.last_upload_ms = -self.frame_interval_ms
        except tk.TclError:
            self.close()

    def close(self) -> None:
        if self.window is None:
            return
        window = self.window
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        _TkHost.unregister(self.panel_id, window)
        self._clear_window_refs()

    def _clear_window_refs(self) -> None:
        self.window = None
        self.label = None
        self.image = None
        self.dragging = False
        self.drag_start_cursor = None
        self.drag_start_position = None
        self.resizing = False
        self.resize_edges = ""
        self.resize_start_cursor = None
        self.resize_start_geometry = None
        self.minimized = False
        self.maximized = False
        self.event_queue.clear()

    def _window_exists(self) -> bool:
        if self.window is None:
            return False
        try:
            return bool(int(self.window.winfo_exists()))
        except tk.TclError:
            return False

    def _handle_destroy(self, event) -> None:
        if self.window is not None and event.widget is self.window:
            _TkHost.unregister(self.panel_id, self.window)
            self._clear_window_refs()

    def minimize(self) -> None:
        if self.window is None:
            return
        try:
            self.minimized = True
            self.window.withdraw()
        except tk.TclError:
            self.close()

    def maximize(self) -> None:
        if self.window is None or _TkHost.root is None:
            return
        try:
            self.dragging = False
            self.resizing = False
            if not self.maximized:
                x, y = self.position
                w, h = self.size
                self.restore_geometry = (x, y, w, h)
                screen_w = self.window.winfo_screenwidth()
                screen_h = self.window.winfo_screenheight()
                self.window.geometry(f"{screen_w}x{screen_h}+0+0")
                self.maximized = True
            elif self.restore_geometry is not None:
                x, y, w, h = self.restore_geometry
                self.window.geometry(f"{w}x{h}+{x}+{y}")
                self.maximized = False
            self.window.update_idletasks()
            self._keep_in_front()
            self._resize_buffer(*self.size)
            self.last_upload_ms = -self.frame_interval_ms
        except tk.TclError:
            self.close()

    def matches_event(self, event: pygame.event.Event) -> bool:
        return False

    def surface(self) -> pygame.Surface | None:
        if self.window is None or self.minimized:
            return None
        width, height = self.size
        if self.buffer.get_size() != (width, height):
            self._resize_buffer(width, height)
        return self.buffer

    def flip(self) -> None:
        if self.window is None or self.label is None or self.minimized:
            return
        now = pygame.time.get_ticks()
        if now - self.last_upload_ms < self.frame_interval_ms:
            return
        self.last_upload_ms = now
        try:
            width, height = self.buffer.get_size()
            ppm = b"P6\n%d %d\n255\n" % (width, height) + pygame.image.tobytes(self.buffer, "RGB")
            self.image = tk.PhotoImage(data=ppm, format="PPM")
            self.label.configure(image=self.image)
            self.window.update_idletasks()
        except (pygame.error, tk.TclError):
            self.close()

    def title_rect(self) -> pygame.Rect:
        return pygame.Rect(0, 0, self.buffer.get_width(), config.TITLE_BAR_HEIGHT)

    def control_rects(self) -> dict[str, pygame.Rect]:
        title_bar = self.title_rect()
        top = title_bar.top + 5
        return {
            "minimize": pygame.Rect(title_bar.right - 86, top, 22, 20),
            "maximize": pygame.Rect(title_bar.right - 58, top, 22, 20),
            "close": pygame.Rect(title_bar.right - 30, top, 22, 20),
        }

    def handle_title_mouse_down(self, pos: tuple[int, int]) -> str | None:
        for name, rect in self.control_rects().items():
            if not rect.collidepoint(pos):
                continue
            if name == "close":
                self.close()
            elif name == "minimize":
                self.minimize()
            elif name == "maximize":
                self.maximize()
            return name
        edges = self._resize_edges_at(pos)
        if edges:
            self.resizing = True
            self.resize_edges = edges
            self.resize_start_cursor = global_mouse_position()
            x, y = self.position
            w, h = self.size
            self.resize_start_geometry = (x, y, w, h)
            return "resize"
        if self.title_rect().collidepoint(pos):
            self.dragging = True
            self.drag_start_cursor = global_mouse_position()
            self.drag_start_position = self.position
            return "drag"
        return None

    def handle_mouse_motion(self, rel: tuple[int, int]) -> bool:
        if self.window is None or self.maximized:
            return False
        if self.resizing:
            return self._resize_from_cursor()
        if not self.dragging:
            return False
        try:
            if self.drag_start_cursor is not None and self.drag_start_position is not None:
                cursor = global_mouse_position()
                dx = cursor[0] - self.drag_start_cursor[0]
                dy = cursor[1] - self.drag_start_cursor[1]
                x = self.drag_start_position[0] + dx
                y = self.drag_start_position[1] + dy
            else:
                x = self.position[0] + rel[0]
                y = self.position[1] + rel[1]
            width, height = self.size
            self.window.geometry(f"{width}x{height}+{int(x)}+{int(y)}")
        except tk.TclError:
            return False
        return True

    def handle_mouse_up(self) -> bool:
        if not self.dragging and not self.resizing:
            return False
        self.dragging = False
        self.drag_start_cursor = None
        self.drag_start_position = None
        self.resizing = False
        self.resize_edges = ""
        self.resize_start_cursor = None
        self.resize_start_geometry = None
        return True

    def pop_events(self) -> list[PanelEvent]:
        events = list(self.event_queue)
        self.event_queue.clear()
        return events

    def _resize_buffer(self, width: int, height: int) -> None:
        width = max(self.min_size[0], int(width))
        height = max(self.min_size[1], int(height))
        if self.buffer.get_size() != (width, height):
            self.buffer = pygame.Surface((width, height))

    def _keep_in_front(self) -> None:
        if self.window is None:
            return
        try:
            self.window.attributes("-topmost", True)
            self.window.lift()
        except tk.TclError:
            pass

    def _resize_edges_at(self, pos: tuple[int, int]) -> str:
        width, height = self.size
        x, y = pos
        margin = self.resize_margin
        edges = ""
        if x <= margin:
            edges += "l"
        elif x >= width - margin:
            edges += "r"
        if y <= margin:
            edges += "t"
        elif y >= height - margin:
            edges += "b"
        return edges

    def _resize_from_cursor(self) -> bool:
        if self.window is None or self.resize_start_cursor is None or self.resize_start_geometry is None:
            return False
        cursor = global_mouse_position()
        start_x, start_y, start_w, start_h = self.resize_start_geometry
        dx = cursor[0] - self.resize_start_cursor[0]
        dy = cursor[1] - self.resize_start_cursor[1]
        x, y, width, height = start_x, start_y, start_w, start_h
        min_w, min_h = self.min_size

        if "l" in self.resize_edges:
            width = max(min_w, start_w - dx)
            x = start_x + start_w - width
        elif "r" in self.resize_edges:
            width = max(min_w, start_w + dx)

        if "t" in self.resize_edges:
            height = max(min_h, start_h - dy)
            y = start_y + start_h - height
        elif "b" in self.resize_edges:
            height = max(min_h, start_h + dy)

        try:
            self.window.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
            self.window.update_idletasks()
            self._resize_buffer(width, height)
            self.last_upload_ms = -self.frame_interval_ms
        except tk.TclError:
            self.close()
            return False
        return True

    def _bind_events(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", lambda event: self._queue_pointer("down", event, button=1))
        widget.bind("<ButtonRelease-1>", lambda event: self._queue_pointer("up", event, button=1))
        widget.bind("<Motion>", lambda event: self._queue_pointer("motion", event))
        widget.bind("<MouseWheel>", self._queue_wheel)
        widget.bind("<Button-4>", lambda event: self._queue_wheel(event, wheel_y=1))
        widget.bind("<Button-5>", lambda event: self._queue_wheel(event, wheel_y=-1))
        widget.bind("<Leave>", self._handle_leave)

    def _queue_pointer(self, kind: str, event, button: int = 0) -> None:
        pos = (int(event.x), int(event.y))
        rel = (pos[0] - self.last_mouse_pos[0], pos[1] - self.last_mouse_pos[1])
        self.last_mouse_pos = pos
        self.mouse_pos = pos
        if kind == "motion" and not self.dragging and not self.resizing:
            self._set_cursor(self._cursor_for_edges(self._resize_edges_at(pos)))
        self.event_queue.append(PanelEvent(kind=kind, pos=pos, button=button, rel=rel))

    def _handle_leave(self, _event) -> None:
        self._set_cursor("")
        self.last_mouse_pos = (-1, -1)
        self.mouse_pos = (-1, -1)
        self.event_queue.append(PanelEvent(kind="motion", pos=(-1, -1)))

    def _set_cursor(self, cursor: str) -> None:
        if self.label is None:
            return
        try:
            self.label.configure(cursor=cursor)
        except tk.TclError:
            self.close()

    def _cursor_for_edges(self, edges: str) -> str:
        if edges in ("lt", "rb"):
            return "size_nw_se"
        if edges in ("rt", "lb"):
            return "size_ne_sw"
        if edges in ("l", "r"):
            return "size_we"
        if edges in ("t", "b"):
            return "size_ns"
        return ""

    def _queue_wheel(self, event, wheel_y: int | None = None) -> None:
        pos = (int(event.x), int(event.y))
        self.mouse_pos = pos
        if wheel_y is None:
            wheel_y = 1 if int(getattr(event, "delta", 0)) > 0 else -1
        self.event_queue.append(PanelEvent(kind="wheel", pos=pos, wheel_y=wheel_y))


def global_mouse_position() -> tuple[int, int]:
    try:
        point = _WinPoint()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return int(point.x), int(point.y)
    except (AttributeError, OSError, NameError):
        pass
    return pygame.mouse.get_pos()


class _WinPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
