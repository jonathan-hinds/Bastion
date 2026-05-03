from __future__ import annotations

import array
import math
import re
import sys
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import pyaudiowpatch as pyaudio
except Exception as exc:  # pragma: no cover - handled by the UI at runtime.
    pyaudio = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


APP_TITLE = "Audio Recorder"
DEFAULT_SAMPLE_RATE = 48_000
FRAMES_PER_BUFFER = 1024

BG = "#0d0f14"
PANEL = "#141821"
PANEL_ALT = "#10131a"
FIELD = "#0b0d12"
BORDER = "#262d3a"
TEXT = "#f5f7fb"
MUTED = "#8f9aac"
SUBTLE = "#5f6878"
ACCENT = "#35d0a0"
ACCENT_2 = "#78a7ff"
WARN = "#e5b453"
DANGER = "#ff5d73"


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    display_name: str
    channels: int
    sample_rate: int
    is_default: bool = False


def slugify(value: str, fallback: str = "recording") -> str:
    value = re.sub(r"[^a-zA-Z0-9._ -]+", "", value).strip().lower()
    value = re.sub(r"[\s-]+", "-", value)
    value = value.strip(".-_")
    return value or fallback


def clean_device_name(name: str) -> str:
    cleaned = re.sub(r"\s*\[loopback\]\s*$", "", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\(loopback\)\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or name


def level_to_db(level: float) -> str:
    if level <= 0.00002:
        return "-inf dB"
    return f"{20.0 * math.log10(max(level, 0.000001)):.0f} dB"


def pcm16_rms(data: bytes) -> float:
    if not data:
        return 0.0

    usable = len(data) - (len(data) % 2)
    if usable <= 0:
        return 0.0

    samples = array.array("h")
    samples.frombytes(data[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0

    total = 0
    for sample in samples:
        total += sample * sample
    return min(1.0, math.sqrt(total / len(samples)) / 32768.0)


class AudioBackend:
    def __init__(self) -> None:
        if pyaudio is None:
            raise RuntimeError(f"PyAudioWPatch is not installed: {IMPORT_ERROR}")
        self.audio = pyaudio.PyAudio()

    def close(self) -> None:
        try:
            self.audio.terminate()
        except Exception:
            pass

    def default_loopback_index(self) -> int | None:
        getter = getattr(self.audio, "get_default_wasapi_loopback", None)
        if callable(getter):
            try:
                info = getter()
                return int(info["index"])
            except Exception:
                return None
        return None

    def default_output_name(self) -> str:
        try:
            host_api = self.audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            output_index = int(host_api.get("defaultOutputDevice", -1))
            if output_index < 0:
                return ""
            info = self.audio.get_device_info_by_index(output_index)
            return str(info.get("name") or "")
        except Exception:
            return ""

    def devices(self) -> list[AudioDevice]:
        infos: list[dict] = []
        generator = getattr(self.audio, "get_loopback_device_info_generator", None)

        if callable(generator):
            try:
                infos = list(generator())
            except Exception:
                infos = []

        if not infos:
            for index in range(self.audio.get_device_count()):
                try:
                    info = self.audio.get_device_info_by_index(index)
                except Exception:
                    continue
                if info.get("isLoopbackDevice") and int(info.get("maxInputChannels") or 0) > 0:
                    infos.append(info)

        default_loopback = self.default_loopback_index()
        default_output = self.default_output_name().lower()
        devices: list[AudioDevice] = []
        seen: set[int] = set()

        for info in infos:
            try:
                index = int(info["index"])
            except Exception:
                continue
            if index in seen:
                continue
            seen.add(index)

            max_channels = int(info.get("maxInputChannels") or 0)
            channels = max(1, min(2, max_channels or 2))
            sample_rate = int(float(info.get("defaultSampleRate") or DEFAULT_SAMPLE_RATE))
            name = str(info.get("name") or f"Device {index}")
            display_name = clean_device_name(name)
            is_default = index == default_loopback
            if not is_default and default_output:
                is_default = default_output in name.lower()

            devices.append(
                AudioDevice(
                    index=index,
                    name=name,
                    display_name=display_name,
                    channels=channels,
                    sample_rate=sample_rate,
                    is_default=is_default,
                )
            )

        devices.sort(key=lambda device: (not device.is_default, device.display_name.lower()))
        return devices


class DeviceMonitor:
    def __init__(self, audio, device: AudioDevice) -> None:
        self.audio = audio
        self.device = device
        self.level = 0.0
        self.peak = 0.0
        self.state = "starting"
        self.error = ""
        self.current_recording_path: Path | None = None
        self.last_recording_path: Path | None = None

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._wave_file: wave.Wave_write | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=f"Monitor-{self.device.index}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.stop_recording()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.2)

    def start_recording(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        wave_file = wave.open(str(output_path), "wb")
        wave_file.setnchannels(self.device.channels)
        wave_file.setsampwidth(2)
        wave_file.setframerate(self.device.sample_rate)

        with self._lock:
            self._close_recording_locked()
            self._wave_file = wave_file
            self.current_recording_path = output_path
            self.last_recording_path = output_path

    def stop_recording(self) -> None:
        with self._lock:
            self._close_recording_locked()

    def _close_recording_locked(self) -> None:
        wave_file = self._wave_file
        self._wave_file = None
        self.current_recording_path = None
        if wave_file is not None:
            try:
                wave_file.close()
            except Exception:
                pass

    def _write_recording(self, data: bytes) -> None:
        with self._lock:
            if self._wave_file is not None:
                self._wave_file.writeframes(data)

    def _set_level(self, raw_level: float) -> None:
        with self._lock:
            self.level = max(raw_level, self.level * 0.82)
            self.peak = max(raw_level, self.peak * 0.96)

    def _open_stream(self, channels: int):
        return self.audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=self.device.sample_rate,
            input=True,
            input_device_index=self.device.index,
            frames_per_buffer=FRAMES_PER_BUFFER,
        )

    def _run(self) -> None:
        stream = None
        try:
            self.state = "opening"
            stream = self._open_stream(self.device.channels)
            self.state = "live"
            self.error = ""

            while not self._stop_event.is_set():
                try:
                    data = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
                except Exception as exc:
                    self.state = "error"
                    self.error = str(exc)
                    self._set_level(0.0)
                    break

                self._set_level(pcm16_rms(data))
                self._write_recording(data)
        except Exception as exc:
            self.state = "error"
            self.error = str(exc)
            self._set_level(0.0)
        finally:
            self.stop_recording()
            if stream is not None:
                try:
                    if stream.is_active():
                        stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if self.state != "error":
                self.state = "stopped"


class Meter(tk.Canvas):
    def __init__(self, parent, height: int = 10, **kwargs) -> None:
        super().__init__(
            parent,
            height=height,
            bg=PANEL_ALT,
            bd=0,
            highlightthickness=0,
            relief="flat",
            **kwargs,
        )
        self.value = 0.0
        self.bind("<Configure>", lambda _event: self._draw())

    def set(self, value: float) -> None:
        self.value = max(0.0, min(1.0, value))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        self.create_rectangle(0, 0, width, height, fill="#202633", outline="")

        visible = 0.0 if self.value <= 0 else min(1.0, self.value ** 0.45)
        fill_width = int(width * visible)
        if self.value > 0.22:
            color = DANGER
        elif self.value > 0.11:
            color = WARN
        else:
            color = ACCENT

        if fill_width > 0:
            self.create_rectangle(0, 0, fill_width, height, fill=color, outline="")
        self.create_rectangle(0, 0, width - 1, height - 1, outline=BORDER)


class DeviceRow(tk.Frame):
    def __init__(self, parent, device: AudioDevice, on_select) -> None:
        super().__init__(parent, bg=PANEL_ALT, highlightthickness=1, highlightbackground=BORDER)
        self.device = device
        self.on_select = on_select
        self.selected = False

        self.grid_columnconfigure(1, weight=1)

        self.name_label = tk.Label(
            self,
            text=device.display_name,
            bg=PANEL_ALT,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )
        self.name_label.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(10, 2))

        meta = f"{device.channels} ch  {device.sample_rate:,} Hz"
        self.meta_label = tk.Label(self, text=meta, bg=PANEL_ALT, fg=MUTED, font=("Segoe UI", 8), anchor="w")
        self.meta_label.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))

        self.badge_label = tk.Label(
            self,
            text="DEFAULT" if device.is_default else "",
            bg=PANEL_ALT,
            fg=ACCENT_2,
            font=("Segoe UI", 8, "bold"),
            anchor="e",
        )
        self.badge_label.grid(row=1, column=1, sticky="e", padx=(4, 8), pady=(0, 10))

        self.level_label = tk.Label(self, text="-inf dB", bg=PANEL_ALT, fg=MUTED, font=("Segoe UI", 8), width=8)
        self.level_label.grid(row=1, column=2, sticky="e", padx=(0, 14), pady=(0, 10))

        self.meter = Meter(self, height=6)
        self.meter.grid(row=2, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 12))

        self._bind_clicks(self)

    def _bind_clicks(self, widget) -> None:
        widget.bind("<Button-1>", lambda _event: self.on_select(self.device.index))
        for child in widget.winfo_children():
            self._bind_clicks(child)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        bg = "#18202b" if selected else PANEL_ALT
        border = ACCENT_2 if selected else BORDER
        self.configure(bg=bg, highlightbackground=border)
        for widget in (self.name_label, self.meta_label, self.badge_label, self.level_label):
            widget.configure(bg=bg)
        self.meter.configure(bg=bg)

    def update_level(self, level: float, state: str, error: str) -> None:
        self.meter.set(level if state == "live" else 0.0)
        if state == "error":
            self.level_label.configure(text="offline", fg=DANGER)
            return
        if state != "live":
            self.level_label.configure(text="opening", fg=SUBTLE)
            return
        self.level_label.configure(text=level_to_db(level), fg=ACCENT if level > 0.002 else MUTED)


class ScrollFrame(tk.Frame):
    def __init__(self, parent) -> None:
        super().__init__(parent, bg=PANEL)
        self.canvas = tk.Canvas(self, bg=PANEL, bd=0, highlightthickness=0)
        self.inner = tk.Frame(self.canvas, bg=PANEL)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_inner_configure(self, _event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.window, width=event.width)


class RecordingApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.geometry("980x620")
        self.minsize(840, 520)

        self.backend: AudioBackend | None = None
        self.monitors: dict[int, DeviceMonitor] = {}
        self.rows: dict[int, DeviceRow] = {}
        self.selected_index: int | None = None
        self.recording_monitor: DeviceMonitor | None = None
        self.record_started_at: float | None = None
        self.auto_stem = ""

        self.save_dir_var = tk.StringVar(value=str(Path.home() / "Music" / "Recordings"))
        self.file_stem_var = tk.StringVar(value="webpage-audio")
        self.status_var = tk.StringVar(value="Starting audio engine")
        self.selected_name_var = tk.StringVar(value="No device selected")
        self.selected_meta_var = tk.StringVar(value="")
        self.elapsed_var = tk.StringVar(value="00:00")
        self.last_file_var = tk.StringVar(value="")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(80, self.refresh_devices)
        self.after(60, self.poll_levels)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=26, pady=(22, 14))
        header.grid_columnconfigure(1, weight=1)

        title = tk.Label(header, text="Audio Recorder", bg=BG, fg=TEXT, font=("Segoe UI", 19, "bold"))
        title.grid(row=0, column=0, sticky="w")

        self.status_label = tk.Label(header, textvariable=self.status_var, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="e")
        self.status_label.grid(row=0, column=1, sticky="e", padx=(14, 0))

        refresh_button = self._button(header, "Refresh", self.refresh_devices, accent=False)
        refresh_button.grid(row=0, column=2, sticky="e", padx=(14, 0))

        devices_panel = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        devices_panel.grid(row=1, column=0, sticky="nsew", padx=(26, 12), pady=(0, 26))
        devices_panel.grid_rowconfigure(1, weight=1)
        devices_panel.grid_columnconfigure(0, weight=1)

        devices_header = tk.Frame(devices_panel, bg=PANEL)
        devices_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))
        devices_header.grid_columnconfigure(0, weight=1)
        tk.Label(
            devices_header,
            text="Output Devices",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.device_count_label = tk.Label(devices_header, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.device_count_label.grid(row=0, column=1, sticky="e")

        self.device_list = ScrollFrame(devices_panel)
        self.device_list.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        controls_panel = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        controls_panel.grid(row=1, column=1, sticky="nsew", padx=(12, 26), pady=(0, 26))
        controls_panel.grid_columnconfigure(0, weight=1)

        tk.Label(
            controls_panel,
            text="Selected Source",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 4))

        tk.Label(
            controls_panel,
            textvariable=self.selected_name_var,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 15, "bold"),
            anchor="w",
            wraplength=320,
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(6, 2))

        tk.Label(
            controls_panel,
            textvariable=self.selected_meta_var,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))

        self.selected_meter = Meter(controls_panel, height=12)
        self.selected_meter.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 18))

        path_frame = tk.Frame(controls_panel, bg=PANEL)
        path_frame.grid(row=4, column=0, sticky="ew", padx=20, pady=(2, 0))
        path_frame.grid_columnconfigure(0, weight=1)

        tk.Label(path_frame, text="Save Folder", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        save_entry = tk.Entry(
            path_frame,
            textvariable=self.save_dir_var,
            bg=FIELD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT_2,
            font=("Segoe UI", 9),
        )
        save_entry.grid(row=1, column=0, sticky="ew", ipady=8)
        self._button(path_frame, "Browse", self.choose_folder, accent=False).grid(row=1, column=1, padx=(10, 0), ipady=3)

        file_frame = tk.Frame(controls_panel, bg=PANEL)
        file_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=(16, 0))
        file_frame.grid_columnconfigure(0, weight=1)
        tk.Label(file_frame, text="File Prefix", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        tk.Entry(
            file_frame,
            textvariable=self.file_stem_var,
            bg=FIELD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT_2,
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="ew", ipady=8)

        record_frame = tk.Frame(controls_panel, bg=PANEL)
        record_frame.grid(row=6, column=0, sticky="ew", padx=20, pady=(24, 0))
        record_frame.grid_columnconfigure(0, weight=1)
        self.record_button = self._button(record_frame, "Record", self.toggle_recording, accent=True)
        self.record_button.grid(row=0, column=0, sticky="ew", ipady=8)

        self.elapsed_label = tk.Label(
            record_frame,
            textvariable=self.elapsed_var,
            bg=PANEL,
            fg=ACCENT,
            font=("Segoe UI", 18, "bold"),
        )
        self.elapsed_label.grid(row=1, column=0, sticky="ew", pady=(16, 0))

        self.last_file_label = tk.Label(
            controls_panel,
            textvariable=self.last_file_var,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
            wraplength=340,
            justify="left",
        )
        self.last_file_label.grid(row=7, column=0, sticky="ew", padx=20, pady=(24, 18))

    def _button(self, parent, text: str, command, accent: bool) -> tk.Button:
        bg = ACCENT if accent else "#202737"
        active = "#43e2b3" if accent else "#2c3547"
        fg = "#06100d" if accent else TEXT
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=fg,
            bd=0,
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=8,
            font=("Segoe UI", 9, "bold"),
        )

    def refresh_devices(self) -> None:
        if self.recording_monitor is not None:
            messagebox.showinfo(APP_TITLE, "Stop the current recording before refreshing devices.")
            return

        self.status_var.set("Refreshing devices")
        self.selected_index = None
        self._clear_rows()
        self._stop_monitors()

        if self.backend is not None:
            self.backend.close()
            self.backend = None

        try:
            self.backend = AudioBackend()
            devices = self.backend.devices()
        except Exception as exc:
            self.status_var.set("Audio engine unavailable")
            self.device_count_label.configure(text="0 found")
            messagebox.showerror(APP_TITLE, str(exc))
            return

        for device in devices:
            row = DeviceRow(self.device_list.inner, device, self.select_device)
            row.pack(fill="x", padx=4, pady=5)
            self.rows[device.index] = row

            monitor = DeviceMonitor(self.backend.audio, device)
            self.monitors[device.index] = monitor
            monitor.start()

        self.device_count_label.configure(text=f"{len(devices)} found")
        if devices:
            default = next((device for device in devices if device.is_default), devices[0])
            self.select_device(default.index)
            self.status_var.set("Listening for output levels")
        else:
            self.selected_name_var.set("No loopback devices found")
            self.selected_meta_var.set("Windows WASAPI output loopback is required")
            self.status_var.set("No output devices found")

    def _clear_rows(self) -> None:
        for row in self.rows.values():
            row.destroy()
        self.rows.clear()

    def _stop_monitors(self) -> None:
        for monitor in list(self.monitors.values()):
            monitor.stop()
        self.monitors.clear()

    def select_device(self, index: int) -> None:
        self.selected_index = index
        for row_index, row in self.rows.items():
            row.set_selected(row_index == index)

        monitor = self.monitors.get(index)
        if monitor is None:
            return

        device = monitor.device
        self.selected_name_var.set(device.display_name)
        self.selected_meta_var.set(f"{device.channels} channel WAV  |  {device.sample_rate:,} Hz")

        new_stem = slugify(device.display_name, "webpage-audio")
        current_stem = self.file_stem_var.get().strip()
        if not current_stem or current_stem == self.auto_stem:
            self.file_stem_var.set(new_stem)
            self.auto_stem = new_stem

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.save_dir_var.get() or str(Path.home()))
        if folder:
            self.save_dir_var.set(folder)

    def toggle_recording(self) -> None:
        if self.recording_monitor is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self) -> None:
        if self.selected_index is None:
            messagebox.showinfo(APP_TITLE, "Select an output device first.")
            return

        monitor = self.monitors.get(self.selected_index)
        if monitor is None:
            messagebox.showerror(APP_TITLE, "Selected output device is not available.")
            return
        if monitor.state == "error":
            messagebox.showerror(APP_TITLE, f"This device is offline:\n\n{monitor.error}")
            return

        folder = Path(self.save_dir_var.get()).expanduser()
        stem = slugify(self.file_stem_var.get(), "recording")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = folder / f"{stem}_{timestamp}.wav"

        try:
            monitor.start_recording(output_path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Could not start recording:\n\n{exc}")
            return

        self.recording_monitor = monitor
        self.record_started_at = time.monotonic()
        self.record_button.configure(text="Stop", bg=DANGER, activebackground="#ff788a", fg="#140609", activeforeground="#140609")
        self.last_file_var.set(str(output_path))
        self.status_var.set("Recording")

    def stop_recording(self) -> None:
        monitor = self.recording_monitor
        if monitor is None:
            return
        monitor.stop_recording()
        self.recording_monitor = None
        self.record_started_at = None
        self.record_button.configure(text="Record", bg=ACCENT, activebackground="#43e2b3", fg="#06100d", activeforeground="#06100d")
        self.elapsed_var.set("00:00")
        if monitor.last_recording_path is not None:
            self.last_file_var.set(f"Saved: {monitor.last_recording_path}")
            self.status_var.set("Saved WAV")
        else:
            self.status_var.set("Ready")

    def poll_levels(self) -> None:
        for index, row in self.rows.items():
            monitor = self.monitors.get(index)
            if monitor is None:
                continue
            row.update_level(monitor.level, monitor.state, monitor.error)

        selected = self.monitors.get(self.selected_index) if self.selected_index is not None else None
        if selected is not None:
            self.selected_meter.set(selected.level if selected.state == "live" else 0.0)
            if selected.state == "error":
                self.selected_meta_var.set(f"Unavailable  |  {selected.error}")

        if self.recording_monitor is not None and self.record_started_at is not None:
            elapsed = int(time.monotonic() - self.record_started_at)
            minutes, seconds = divmod(elapsed, 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                self.elapsed_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
            else:
                self.elapsed_var.set(f"{minutes:02d}:{seconds:02d}")

        self.after(60, self.poll_levels)

    def on_close(self) -> None:
        self.stop_recording()
        self._stop_monitors()
        if self.backend is not None:
            self.backend.close()
        self.destroy()


class MissingDependencyApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.geometry("620x320")
        self.minsize(560, 280)

        frame = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        frame.pack(fill="both", expand=True, padx=28, pady=28)
        frame.grid_columnconfigure(0, weight=1)

        tk.Label(frame, text="Audio Recorder", bg=PANEL, fg=TEXT, font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, sticky="w", padx=24, pady=(24, 8)
        )
        tk.Label(
            frame,
            text="PyAudioWPatch is required for Windows output loopback capture.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
        ).grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 18))

        requirements_path = Path(__file__).with_name("requirements.txt")
        interpreter = Path(sys.executable or "python")
        command = f'"{interpreter}" -m pip install -r "{requirements_path}"'
        command_label = tk.Label(
            frame,
            text=command,
            bg=FIELD,
            fg=ACCENT,
            font=("Consolas", 11),
            anchor="w",
            padx=14,
            pady=12,
        )
        command_label.grid(row=2, column=0, sticky="ew", padx=24)

        detail = f"Import error: {IMPORT_ERROR}"
        tk.Label(
            frame,
            text=detail,
            bg=PANEL,
            fg=SUBTLE,
            font=("Segoe UI", 8),
            anchor="w",
            wraplength=520,
            justify="left",
        ).grid(row=3, column=0, sticky="ew", padx=24, pady=(16, 0))

        tk.Label(
            frame,
            text="Tip: install into the same Python environment used to launch this window.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        ).grid(row=4, column=0, sticky="ew", padx=24, pady=(12, 0))


def main() -> None:
    app = MissingDependencyApp() if pyaudio is None else RecordingApp()
    app.mainloop()


if __name__ == "__main__":
    main()
