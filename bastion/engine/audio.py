from __future__ import annotations

import math
import random
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

import pygame


SOUND_DIR = Path(__file__).resolve().parents[2] / "Sounds"
MUSIC_FOLDER = "Songs"
MUSIC_EXTENSIONS = {".wav", ".ogg", ".mp3"}
MUSIC_FALLBACK_FILE = "SongLoop.wav"
MUSIC_VOLUME = 0.32
MUSIC_PLAYS_PER_TRACK = 2
MUSIC_FADE_MS = 2500
MUSIC_CHANNELS = (0, 1)
MUSIC_SCAN_MS = 5000

SOUND_FILES = {
    "tower_archer": "ArcherTower.wav",
    "tower_cannon": "CannonTower.wav",
    "tower_mage": "MageTower.wav",
    "menu_hover": "MenuHover.wav",
    "menu_select": "MenuSelect.wav",
    "level_up_blink": "LevelUpBlinking.wav",
}

TOWER_SOUNDS = {"tower_archer", "tower_cannon", "tower_mage"}
PITCH_VARIANTS = (0.94, 0.97, 1.0, 1.03, 1.06)


@dataclass(frozen=True)
class MusicTrack:
    path: Path
    sound: pygame.mixer.Sound
    duration_ms: int
    amplitudes: tuple[float, ...] = ()


class AudioSystem:
    def __init__(self, sound_dir: Path = SOUND_DIR) -> None:
        self.sound_dir = sound_dir
        self.enabled = False
        self.mixer_ready = False
        self.music_enabled = False
        self.music_tracks: list[MusicTrack] = []
        self.music_index = 0
        self.music_state = "stopped"
        self.music_channel_index = 0
        self.music_channel: pygame.mixer.Channel | None = None
        self.music_fade_start_ms = 0
        self.music_started_ms = 0
        self.music_paused_at_ms = 0
        self.last_music_scan_ms = 0
        self.master_volume = 1.0
        self.music_volume = MUSIC_VOLUME
        self.sfx_volume = 1.0
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        self.sound_base_volumes: dict[str, float] = {}
        self.pitch_variants: dict[str, list[pygame.mixer.Sound]] = {}
        self._initialize()

    def _initialize(self) -> None:
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=48000, size=-16, channels=2, buffer=512)
            self.mixer_ready = True
            pygame.mixer.set_num_channels(48)
            pygame.mixer.set_reserved(len(MUSIC_CHANNELS))
            self._load_sounds()
            self._load_music()
        except (OSError, pygame.error, wave.Error):
            self.enabled = False
            self.mixer_ready = False
            self.music_enabled = False
            self.music_tracks.clear()
            self.sounds.clear()
            self.pitch_variants.clear()
            return
        self.enabled = bool(self.sounds)

    def _load_sounds(self) -> None:
        for sound_id, filename in SOUND_FILES.items():
            path = self.sound_dir / filename
            if not path.exists():
                continue
            sound = pygame.mixer.Sound(str(path))
            base_volume = 0.58 if sound_id in TOWER_SOUNDS else 0.42
            sound.set_volume(base_volume * self.master_volume * self.sfx_volume)
            self.sounds[sound_id] = sound
            self.sound_base_volumes[sound_id] = base_volume
            if sound_id in TOWER_SOUNDS:
                variants = self._build_pitch_variants(path)
                self.pitch_variants[sound_id] = variants or [sound]

    def _load_music(self) -> None:
        tracks = self._load_music_tracks()
        if not tracks:
            return
        self.music_tracks = tracks
        self.music_enabled = True

    def _scan_music_paths(self) -> list[Path]:
        music_dir = self.sound_dir / MUSIC_FOLDER
        paths = []
        if music_dir.exists():
            paths = [
                path
                for path in sorted(music_dir.iterdir())
                if path.is_file() and path.suffix.lower() in MUSIC_EXTENSIONS
            ]

        fallback_path = self.sound_dir / MUSIC_FALLBACK_FILE
        if not paths and fallback_path.exists():
            paths = [fallback_path]
        return paths

    def _load_music_tracks(self) -> list[MusicTrack]:
        paths = self._scan_music_paths()
        random.shuffle(paths)
        tracks = []
        for path in paths:
            try:
                sound = pygame.mixer.Sound(str(path))
            except (OSError, pygame.error):
                continue
            sound.set_volume(self.effective_music_volume)
            duration_ms = int(sound.get_length() * 1000)
            if duration_ms > MUSIC_FADE_MS:
                tracks.append(MusicTrack(path, sound, duration_ms, _build_amplitude_envelope(sound)))
        return tracks

    def refresh_music_library(self) -> None:
        if not self.mixer_ready:
            return
        current_paths = {track.path for track in self.music_tracks}
        scanned_paths = set(self._scan_music_paths())
        if current_paths == scanned_paths:
            return

        current_path = self.current_music_path
        tracks = self._load_music_tracks()
        if not tracks:
            self.music_enabled = False
            self.music_tracks.clear()
            return

        self.music_tracks = tracks
        self.music_enabled = True
        if current_path is not None:
            for index, track in enumerate(self.music_tracks):
                if track.path == current_path:
                    self.music_index = index
                    break
            else:
                self.music_index %= len(self.music_tracks)
        else:
            self.music_index %= len(self.music_tracks)

    def _build_pitch_variants(self, path: Path) -> list[pygame.mixer.Sound]:
        variants: list[pygame.mixer.Sound] = []
        for pitch in PITCH_VARIANTS:
            try:
                sound = _load_pitch_shifted_wav(path, pitch)
            except (OSError, pygame.error, wave.Error):
                continue
            sound.set_volume(0.58 * self.master_volume * self.sfx_volume)
            variants.append(sound)
        return variants

    def play(self, sound_id: str, random_pitch: bool = False, volume: float | None = None) -> None:
        if not self.enabled:
            return
        sound: pygame.mixer.Sound | None
        if random_pitch:
            variants = self.pitch_variants.get(sound_id)
            sound = random.choice(variants) if variants else self.sounds.get(sound_id)
        else:
            sound = self.sounds.get(sound_id)
        if sound is None:
            return
        base_volume = self.sound_base_volumes.get(sound_id, 1.0) if volume is None else max(0.0, min(1.0, volume))
        sound.set_volume(base_volume * self.master_volume * self.sfx_volume)
        sound.play()

    def play_music(self) -> None:
        self.refresh_music_library()
        if not self.mixer_ready or not self.music_enabled or self.music_state != "stopped":
            return
        self._start_current_music(fade_in=True)

    def update_music(self) -> None:
        if not self.mixer_ready or not self.music_enabled:
            return
        now = pygame.time.get_ticks()
        if now - self.last_music_scan_ms >= MUSIC_SCAN_MS:
            self.last_music_scan_ms = now
            self.refresh_music_library()
        if self.music_state == "playing":
            if now >= self.music_fade_start_ms:
                self._crossfade_to_next_music()
            elif self.music_channel is not None and not self.music_channel.get_busy():
                self._start_current_music(fade_in=True)

    def _start_current_music(self, fade_in: bool = False) -> None:
        if not self.music_tracks:
            self.music_enabled = False
            self.music_state = "stopped"
            return

        attempts = 0
        max_attempts = len(self.music_tracks)
        while attempts < max_attempts and self.music_tracks:
            track = self.music_tracks[self.music_index]
            channel = pygame.mixer.Channel(MUSIC_CHANNELS[self.music_channel_index])
            try:
                channel.play(track.sound, loops=-1, fade_ms=MUSIC_FADE_MS if fade_in else 0)
                channel.set_volume(self.effective_music_volume)
            except (OSError, pygame.error):
                self.music_tracks.pop(self.music_index)
                if not self.music_tracks:
                    self.music_enabled = False
                    self.music_state = "stopped"
                    return
                if self.music_index >= len(self.music_tracks):
                    self.music_index = 0
                attempts += 1
                continue

            now = pygame.time.get_ticks()
            self.music_state = "playing"
            self.music_channel = channel
            self.music_started_ms = now
            self.music_paused_at_ms = 0
            self.music_fade_start_ms = now + (track.duration_ms * MUSIC_PLAYS_PER_TRACK)
            return

        self.music_enabled = False
        self.music_state = "stopped"

    def _crossfade_to_next_music(self) -> None:
        outgoing = self.music_channel
        self.music_index = (self.music_index + 1) % len(self.music_tracks)
        self.music_channel_index = (self.music_channel_index + 1) % len(MUSIC_CHANNELS)
        self._start_current_music(fade_in=True)
        if outgoing is not None:
            outgoing.fadeout(MUSIC_FADE_MS)

    def stop_music(self) -> None:
        if not self.mixer_ready:
            return
        for channel_id in MUSIC_CHANNELS:
            pygame.mixer.Channel(channel_id).stop()
        self.music_channel = None
        self.music_state = "stopped"
        self.music_paused_at_ms = 0

    @property
    def effective_music_volume(self) -> float:
        return self.master_volume * self.music_volume

    @property
    def current_music_path(self) -> Path | None:
        if not self.music_tracks:
            return None
        return self.music_tracks[self.music_index].path

    @property
    def current_track_name(self) -> str:
        path = self.current_music_path
        return path.stem if path is not None else "NO TRACK"

    @property
    def is_music_playing(self) -> bool:
        return self.music_state == "playing"

    def set_master_volume(self, value: float) -> None:
        self.master_volume = max(0.0, min(1.0, value))
        self._apply_all_volumes()

    def set_music_volume(self, value: float) -> None:
        self.music_volume = max(0.0, min(1.0, value))
        self._apply_music_volume()

    def set_sfx_volume(self, value: float) -> None:
        self.sfx_volume = max(0.0, min(1.0, value))
        self._apply_sfx_volumes()

    def _apply_all_volumes(self) -> None:
        self._apply_music_volume()
        self._apply_sfx_volumes()

    def _apply_music_volume(self) -> None:
        for track in self.music_tracks:
            track.sound.set_volume(self.effective_music_volume)
        for channel_id in MUSIC_CHANNELS:
            pygame.mixer.Channel(channel_id).set_volume(self.effective_music_volume)

    def _apply_sfx_volumes(self) -> None:
        for sound_id, sound in self.sounds.items():
            sound.set_volume(self.sound_base_volumes.get(sound_id, 1.0) * self.master_volume * self.sfx_volume)
        for sound_id, variants in self.pitch_variants.items():
            base_volume = self.sound_base_volumes.get(sound_id, 1.0)
            for sound in variants:
                sound.set_volume(base_volume * self.master_volume * self.sfx_volume)

    def toggle_music_pause(self) -> None:
        if self.music_state == "playing":
            self.pause_music()
        elif self.music_state == "paused":
            self.resume_music()
        else:
            self.play_music()

    def pause_music(self) -> None:
        if self.music_state != "playing" or self.music_channel is None:
            return
        self.music_channel.pause()
        self.music_state = "paused"
        self.music_paused_at_ms = pygame.time.get_ticks()

    def resume_music(self) -> None:
        if self.music_state != "paused" or self.music_channel is None:
            return
        now = pygame.time.get_ticks()
        paused_ms = now - self.music_paused_at_ms
        self.music_started_ms += paused_ms
        self.music_fade_start_ms += paused_ms
        self.music_paused_at_ms = 0
        self.music_channel.unpause()
        self.music_state = "playing"

    def next_music(self) -> None:
        self._jump_music(1)

    def previous_music(self) -> None:
        self._jump_music(-1)

    def _jump_music(self, delta: int) -> None:
        self.refresh_music_library()
        if not self.mixer_ready or not self.music_tracks:
            return
        was_paused = self.music_state == "paused"
        if self.music_channel is not None:
            self.music_channel.fadeout(250)
        self.music_index = (self.music_index + delta) % len(self.music_tracks)
        self.music_channel_index = (self.music_channel_index + 1) % len(MUSIC_CHANNELS)
        self.music_state = "stopped"
        self._start_current_music(fade_in=True)
        if was_paused:
            self.pause_music()

    def current_music_progress(self) -> float:
        if not self.music_tracks or self.music_state not in {"playing", "paused"}:
            return 0.0
        now = self.music_paused_at_ms if self.music_state == "paused" and self.music_paused_at_ms else pygame.time.get_ticks()
        elapsed = max(0, now - self.music_started_ms)
        duration = max(1, self.music_tracks[self.music_index].duration_ms)
        return (elapsed % duration) / duration

    def music_energy(self) -> float:
        if self.music_state != "playing" or not self.music_tracks:
            return 0.0
        track = self.music_tracks[self.music_index]
        if not track.amplitudes:
            return 0.35 + 0.3 * random.random()
        position = self.current_music_progress() * len(track.amplitudes)
        left = int(position) % len(track.amplitudes)
        right = (left + 1) % len(track.amplitudes)
        frac = position - int(position)
        value = track.amplitudes[left] + (track.amplitudes[right] - track.amplitudes[left]) * frac
        pulse = 0.94 + 0.06 * math.sin(pygame.time.get_ticks() * 0.012)
        return max(0.05, min(1.0, value * pulse))

    def music_track_count(self) -> int:
        return len(self.music_tracks)


def _load_pitch_shifted_wav(path: Path, pitch: float) -> pygame.mixer.Sound:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frames = wav.getnframes()
        raw = wav.readframes(frames)

    if channels != 2 or sample_width != 2:
        return pygame.mixer.Sound(str(path))

    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()

    shifted = _resample_interleaved_16(samples, channels, pitch)
    if sys.byteorder != "little":
        shifted.byteswap()
    return pygame.mixer.Sound(buffer=shifted.tobytes())


def _build_amplitude_envelope(sound: pygame.mixer.Sound, buckets: int = 720) -> tuple[float, ...]:
    try:
        raw = sound.get_raw()
    except pygame.error:
        return ()
    if not raw:
        return ()

    samples = array("h")
    try:
        samples.frombytes(raw)
    except ValueError:
        return ()
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return ()

    stride = max(1, len(samples) // buckets)
    amplitudes: list[float] = []
    peak = 1.0
    for start in range(0, len(samples), stride):
        window = samples[start : start + stride]
        if not window:
            continue
        value = sum(abs(sample) for sample in window) / (len(window) * 32768.0)
        amplitudes.append(value)
        peak = max(peak, value)
        if len(amplitudes) >= buckets:
            break
    if not amplitudes:
        return ()
    return tuple(min(1.0, (value / peak) ** 0.65) for value in amplitudes)


def _resample_interleaved_16(samples: array, channels: int, pitch: float) -> array:
    source_frames = len(samples) // channels
    if source_frames <= 1 or pitch <= 0:
        return samples
    output_frames = max(1, int(source_frames / pitch))
    output = array("h")
    for frame in range(output_frames):
        source_pos = min(source_frames - 1, frame * pitch)
        left = int(source_pos)
        right = min(source_frames - 1, left + 1)
        frac = source_pos - left
        for channel in range(channels):
            a = samples[left * channels + channel]
            b = samples[right * channels + channel]
            output.append(int(a + (b - a) * frac))
    return output
