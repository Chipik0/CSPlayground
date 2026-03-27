"""
Audio system — PlaybackManager + UISoundPlayer
miniaudio + LoomEngine AnimationEngine

pip install miniaudio numpy aubio loguru PyQt5
"""
import soundfile as sf
import sys
import time
import math
import queue
import aubio
import threading
import traceback
from typing import Optional

import numpy as np
import miniaudio

from loguru import logger
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from System.Interface.Animation.LoomEngine import AnimationEngine, Easing, MixMode


# ── Константы ──────────────────────────────────────────────────────────────

SAMPLE_RATE = 44100
CHANNELS    = 2
BLOCK_SIZE  = 512 if sys.platform == "linux" else 256


def _thread_excepthook(args):
    logger.exception(
        "Unhandled exception in thread %s", args.thread.name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )

threading.excepthook = _thread_excepthook


# ══════════════════════════════════════════════════════════════════════════════
#  _AudioMixer — один miniaudio.PlaybackDevice на всё приложение
# ══════════════════════════════════════════════════════════════════════════════

class _AudioMixer:
    """
    Синглтон. Держит один PlaybackDevice и микширует все источники
    в одном генераторе. Источник — любой объект с методом render(frames).
    """

    _instance: Optional["_AudioMixer"] = None

    def __init__(self):
        self._lock    = threading.Lock()
        self._sources = []

        self._device = miniaudio.PlaybackDevice(
            output_format   = miniaudio.SampleFormat.FLOAT32,
            nchannels       = CHANNELS,
            sample_rate     = SAMPLE_RATE,
            buffersize_msec = 40,
        )
        gen = self._mixer()
        next(gen)                  # прокручиваем до первого yield
        self._device.start(gen)

    @classmethod
    def get(cls) -> "_AudioMixer":
        if cls._instance is None:
            cls._instance = _AudioMixer()
        return cls._instance

    def add_source(self, src):
        with self._lock:
            if src not in self._sources:
                self._sources.append(src)

    def remove_source(self, src):
        with self._lock:
            try:
                self._sources.remove(src)
            except ValueError:
                pass

    def _mixer(self):
        # miniaudio шлёт framecount через .send() — принимаем как значение yield
        frames = yield b""
        while True:
            if not frames:
                frames = BLOCK_SIZE

            with self._lock:
                sources = list(self._sources)

            if not sources:
                frames = yield bytes(frames * CHANNELS * 4)
                continue

            mix = np.zeros((frames, CHANNELS), dtype="float32")
            for src in sources:
                try:
                    mix += src.render(frames)
                except Exception:
                    logger.error(f"[AudioMixer] render error: {traceback.format_exc()}")

            np.clip(mix, -1.0, 1.0, out=mix)
            frames = yield mix.flatten().tobytes()


# ══════════════════════════════════════════════════════════════════════════════
#  PlaybackManager
# ══════════════════════════════════════════════════════════════════════════════

class PlaybackManager(QObject):
    playback_state_changed = pyqtSignal(bool)
    audio_loaded           = pyqtSignal(np.ndarray, int, float)
    beat_normal            = pyqtSignal(float)
    beat_heavy             = pyqtSignal(float)
    speed_changed          = pyqtSignal(float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._lock  = threading.Lock()
        self._mixer = _AudioMixer.get()

        # ── данные ──────────────────────────────────────────────────────────
        self.data:     Optional[np.ndarray] = None
        self.fs:       int                  = SAMPLE_RATE
        self.duration_ms: float             = 0.0

        # ── состояние воспроизведения ────────────────────────────────────────
        self.is_playing              = False
        self.position                = 0.0
        self._playback_start_ms      = 0.0   # аудио-позиция в момент последнего sync
        self._playback_start_wall    = 0.0   # wall-time в момент последнего sync

        # ── DSP-состояния ────────────────────────────────────────────────────
        self._filter_states:  Optional[np.ndarray] = None
        self._bitcrush_state: Optional[np.ndarray] = None
        self._track_peak  = 1.0
        self._audio_level = 0.0

        # ── beat detection ───────────────────────────────────────────────────
        self._onset:      Optional[aubio.onset]       = None
        self._beat_queue: Optional[queue.Queue]        = None
        self._beat_thread: Optional[threading.Thread] = None
        self._last_heavy_time     = 0.0
        self._heavy_cooldown      = 0.3
        self._heavy_rms_threshold = 0.2
        self._win_s = 512
        self._hop_s = 256 if sys.platform != "linux" else 512

        # ── AnimationEngine ──────────────────────────────────────────────────
        # Заменяет всю кучу QTimer-ов и _step/_steps переменных
        self.anim = AnimationEngine(fps=120)

        props = [
            ("volume",      1.0,    MixMode.MULTIPLY),
            ("speed",       1.0,    MixMode.MULTIPLY),
            ("delay_l",     0.0,    MixMode.MULTIPLY),
            ("delay_r",     0.0,    MixMode.MULTIPLY),
            ("mid_mix",     0.0,    MixMode.MULTIPLY),
            ("mid_center",  1000.0, MixMode.MULTIPLY),
            ("mid_q",       1.0,    MixMode.MULTIPLY),
            ("mid_gain",    1.0,    MixMode.MULTIPLY),
            ("bc_mix",      0.0,    MixMode.MULTIPLY),
            ("bc_bits",     24.0,   MixMode.MULTIPLY),
            ("bc_down",     1.0,    MixMode.MULTIPLY),
        ]
        for name, base, mode in props:
            self.anim.add_property(name, base, mode)

        # speed отдельно — нужен колбэк для синхронизации позиции и сигнала
        self.anim.properties["speed"].updated.connect(self._on_speed_updated)

    # ── загрузка ─────────────────────────────────────────────────────────────

    def load_audio(self, path: str):
        try:
            self._unregister()
            
            # Попытка №1: Стандартный miniaudio
            try:
                decoded = miniaudio.decode_file(
                    path,
                    output_format=miniaudio.SampleFormat.FLOAT32,
                    nchannels=CHANNELS,
                    sample_rate=SAMPLE_RATE,
                )
                data = np.frombuffer(decoded.samples, dtype="float32") \
                         .reshape(-1, CHANNELS).copy()
                self._init_playback(data, SAMPLE_RATE)
                logger.success(f"[PlaybackManager] Loaded via miniaudio: {path}")
                
            except miniaudio.DecodeError:
                # Попытка №2: Soundfile (спасает на Ogg/Opus)
                logger.info(f"[PlaybackManager] miniaudio failed, trying soundfile for {path}")
                data, fs = sf.read(path, dtype='float32')
                
                # Приводим к стерео, если файл моно
                if data.ndim == 1:
                    data = np.stack([data, data], axis=-1)
                
                # Если частота дискретизации отличается от системной, 
                # в идеале нужен ресэмплинг, но пока просто пробросим данные
                self._init_playback(data, fs)
                logger.success(f"[PlaybackManager] Loaded via soundfile: {path}")

        except Exception:
            logger.error(f"[PlaybackManager] load_audio critical failure: {traceback.format_exc()}")
    
    @property
    def speed(self):
        return self.anim.get_property_value("speed") or 1.0
    def load_audio_from_data(self, data: np.ndarray, fs: int):
        try:
            self._unregister()
            if data.ndim == 1:
                data = np.stack([data, data], axis=-1)
            self._init_playback(data.astype("float32"), fs)
        except Exception:
            logger.error(f"[PlaybackManager] load_from_data: {traceback.format_exc()}")

    def _init_playback(self, data: np.ndarray, fs: int):
        with self._lock:
            self.data        = data
            self.fs          = fs
            self.position    = 0.0
            self.is_playing  = False
            self.duration_ms = len(data) / fs * 1000.0

            self._track_peak     = max(float(np.max(np.abs(data))), 1e-6)
            self._filter_states  = np.zeros((CHANNELS, 4), dtype="float64")
            self._bitcrush_state = np.zeros((CHANNELS, 2), dtype="float64")

        # сброс параметров анимации
        for name, val in [("volume", 1.0), ("speed", 1.0)]:
            self.anim.properties[name].base_value = val
            self.anim.properties[name]._cached_value = val
            self.anim.properties[name]._target_value = val
            self.anim.properties[name].animations.clear()
            self.anim.properties[name]._is_targeting = False

        # beat detection
        self._onset = aubio.onset("specflux", self._win_s, self._hop_s, fs)
        self._onset.set_threshold(0.38)
        self._beat_queue = queue.Queue(maxsize=100)
        self._beat_thread = threading.Thread(
            target=self._beat_emitter_worker, daemon=True
        )
        self._beat_thread.start()

        self._mixer.add_source(self)
        self.audio_loaded.emit(data, fs, len(data) / fs)

    def _unregister(self):
        self._mixer.remove_source(self)
        if self._beat_queue:
            self._beat_queue.put(None)
        with self._lock:
            self.is_playing = False

    # ── управление воспроизведением ──────────────────────────────────────────

    def play(self, start_ms: float = 0.0):
        if self.data is None:
            return
        with self._lock:
            self.position = start_ms * self.fs / 1000.0
            self.is_playing = True
        self._sync_wall(start_ms)
        self.playback_state_changed.emit(True)

    def stop(self):
        with self._lock:
            self.is_playing = False
        self.playback_state_changed.emit(False)

    def toggle_playback(self, ms: float = None):
        if self.is_playing:
            self.stop()
        else:
            self.play(ms or 0.0)

    def get_position_ms(self) -> float:
        # Интерполируем по wall-time для плавности при 120fps UI
        elapsed = (time.time() - self._playback_start_wall) * 1000.0
        speed   = self.anim.get_property_value("speed") or 1.0
        return self._playback_start_ms + elapsed * speed

    def _sync_wall(self, pos_ms: float = None):
        """Обновляет wall-time точку отсчёта для get_position_ms."""
        if pos_ms is None:
            with self._lock:
                pos_ms = self.position / self.fs * 1000.0
        self._playback_start_ms   = pos_ms
        self._playback_start_wall = time.time()

    def _on_speed_updated(self):
        # Вызывается из AnimationEngine (главный тред) при каждом тике
        self._sync_wall()
        speed = self.anim.get_property_value("speed") or 1.0
        self.speed_changed.emit(speed)

    # ── параметры через AnimationEngine ──────────────────────────────────────

    def set_volume(self, volume: float, duration_ms: int = 0,
                   easing=Easing.ease_out_cubic):
        self._anim_set("volume", max(0.0, min(1.0, volume)), duration_ms, easing)

    def set_speed(self, speed: float, duration_ms: int = 0,
                  easing=Easing.ease_out_cubic, stop_on_end: bool = False):
        self._sync_wall()
        self._anim_set("speed", max(0.01, speed), duration_ms, easing)
        if stop_on_end and duration_ms > 0:
            QTimer.singleShot(duration_ms, self.stop)

    def set_channel_delay(self, left_ms: float, right_ms: float,
                          duration_ms: int = 0, easing=Easing.smooth):
        self._anim_set("delay_l", max(0.0, left_ms),  duration_ms, easing)
        self._anim_set("delay_r", max(0.0, right_ms), duration_ms, easing)

    def enable_midpass(self, center_hz: float = 1000.0, q: float = 1.0,
                       mix: float = 1.0, gain: float = 1.0,
                       duration_ms: int = 0, easing=Easing.smooth):
        self._anim_set("mid_center", center_hz,             duration_ms, easing)
        self._anim_set("mid_q",      max(0.001, q),         duration_ms, easing)
        self._anim_set("mid_mix",    max(0.0, min(1.0, mix)), duration_ms, easing)
        self._anim_set("mid_gain",   gain,                  duration_ms, easing)

    def disable_midpass(self, duration_ms: int = 0, easing=Easing.smooth):
        self._anim_set("mid_mix",  0.0, duration_ms, easing)
        self._anim_set("mid_gain", 0.0, duration_ms, easing)

    def enable_bitcrush(self, bits: int = 8, downsample: int = 4, mix: float = 1.0,
                        duration_ms: int = 0, easing=Easing.smooth):
        self._anim_set("bc_bits", float(bits),       duration_ms, easing)
        self._anim_set("bc_down", float(downsample), duration_ms, easing)
        self._anim_set("bc_mix",  float(mix),        duration_ms, easing)

    def disable_bitcrush(self, duration_ms: int = 0, easing=Easing.smooth):
        self._anim_set("bc_bits", 24.0, duration_ms, easing)
        self._anim_set("bc_down",  1.0, duration_ms, easing)
        self._anim_set("bc_mix",   0.0, duration_ms, easing)

    def tape(
        self,
        start_volume: float = None,
        end_volume:   float = None,
        start_speed:  float = None,
        end_speed:    float = None,
        start_ms:     float = None,
        duration_ms:  int   = 1500,
        easing              = Easing.ease_out_cubic,
        stop_on_finish: bool = True,
        shutdown_on_finish: bool = False,
    ):
        if not self.is_playing:
            self.play(start_ms or 0.0)
        if start_volume is not None: self.set_volume(start_volume)
        if start_speed  is not None: self.set_speed(start_speed)
        if end_volume   is not None: self.set_volume(end_volume, duration_ms, easing)
        if end_speed    is not None: self.set_speed(end_speed, duration_ms, easing,
                                                    stop_on_end=stop_on_finish)
        if shutdown_on_finish:
            QTimer.singleShot(duration_ms + 50, self.cleanup)
        print("yo")

    def _anim_set(self, name: str, value: float, duration_ms: int, easing):
        print("called")
        self.anim.animate(name, [(0.0, 1.0), (1.0, value)], duration_ms, easing)

    # ── render (вызывается из аудио-треда _AudioMixer) ───────────────────────

    def render(self, frames: int) -> np.ndarray:
        with self._lock:
            if not self.is_playing or self.data is None:
                return np.zeros((frames, CHANNELS), dtype="float32")
            pos     = self.position
            data    = self.data
            max_idx = len(data) - 1

        # чтение параметров анимации (CPython GIL — float-чтения атомарны)
        speed    = self.anim.get_property_value("speed")
        print(speed)
        volume   = self.anim.get_property_value("volume")     or 1.0
        delay_l  = self.anim.get_property_value("delay_l")    or 0.0
        delay_r  = self.anim.get_property_value("delay_r")    or 0.0
        mid_mix  = self.anim.get_property_value("mid_mix")    or 0.0
        mid_c    = self.anim.get_property_value("mid_center") or 1000.0
        mid_q    = self.anim.get_property_value("mid_q")      or 1.0
        mid_gain = self.anim.get_property_value("mid_gain")   or 1.0
        bc_mix   = self.anim.get_property_value("bc_mix")     or 0.0
        bc_bits  = self.anim.get_property_value("bc_bits")    or 24.0
        bc_down  = self.anim.get_property_value("bc_down")    or 1.0

        # ресэмплинг с интерполяцией + channel delay
        delay_samples = np.array([delay_l, delay_r], dtype="float32") \
                        * self.fs / 1000.0
        t           = np.arange(frames, dtype="float32")
        base_indices = pos + t * speed
        block       = np.empty((frames, CHANNELS), dtype="float32")

        for ch in range(CHANNELS):
            ch_pos = base_indices - delay_samples[ch]
            idx_i  = ch_pos.astype(np.int32)
            idx_f  = ch_pos - idx_i

            row = np.zeros(frames, dtype="float32")
            valid = (idx_i >= 0) & (idx_i < max_idx)
            si    = idx_i[valid]
            row[valid] = data[si, ch] + idx_f[valid] * (data[si + 1, ch] - data[si, ch])
            row[idx_i >= max_idx] = data[max_idx, ch]
            block[:, ch] = row

        self._process_beat(block)

        if mid_mix > 0.0 and self._filter_states is not None:
            block = self._apply_midpass(block, mid_c, mid_q, mid_gain, mid_mix)

        if bc_mix > 0.0:
            block = self._apply_bitcrush(block, bc_bits, bc_down, bc_mix)

        block *= volume

        with self._lock:
            self.position += frames * speed
            self._audio_level = float(np.max(np.abs(block))) / self._track_peak
            if self.position >= max_idx:
                self.is_playing = False
                QTimer.singleShot(0, lambda: self.playback_state_changed.emit(False))

        return block

    # ── DSP ──────────────────────────────────────────────────────────────────

    def _biquad_bandpass(self, center_hz: float, q: float):
        omega = 2.0 * math.pi * center_hz / self.fs
        sn, cs = math.sin(omega), math.cos(omega)
        alpha  = sn / (2.0 * max(q, 1e-4))
        b0, b2 = alpha, -alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * cs
        a2 = 1.0 - alpha
        return (b0 / a0, 0.0, b2 / a0), (a1 / a0, a2 / a0)

    def _apply_midpass(self, block: np.ndarray,
                       center: float, q: float, gain: float, mix: float) -> np.ndarray:
        (b0, b1, b2), (a1, a2) = self._biquad_bandpass(center, q)
        filtered = np.empty_like(block)
        states   = self._filter_states

        for ch in range(block.shape[1]):
            x              = block[:, ch]
            y              = np.empty(len(x), dtype="float32")
            x1, x2, y1, y2 = states[ch]
            for n in range(len(x)):
                yn   = b0*x[n] + b1*x1 + b2*x2 - a1*y1 - a2*y2
                y[n] = yn
                x2, x1 = x1, x[n]
                y2, y1 = y1, yn
            states[ch]      = [x1, x2, y1, y2]
            filtered[:, ch] = y

        return (1.0 - mix) * block + mix * filtered * gain

    def _apply_bitcrush(self, block: np.ndarray,
                        bits: float, down: float, mix: float) -> np.ndarray:
        down   = max(1, int(down))
        bits   = max(1, min(24, int(bits)))
        levels = (1 << bits) - 1
        inv    = 1.0 / levels

        if down > 1:
            nl = (len(block) // down) * down
            crushed = np.repeat(block[:nl].reshape(-1, down, CHANNELS)[:, 0, :], down, axis=0)
            if len(block) > nl:
                crushed = np.vstack([crushed,
                    np.repeat(block[nl:nl+1], len(block) - nl, axis=0)])
        else:
            crushed = block.copy()

        crushed = np.round((crushed + 1.0) * 0.5 * levels) * inv * 2.0 - 1.0
        return (1.0 - mix) * block + mix * crushed

    # ── beat detection ────────────────────────────────────────────────────────

    def _beat_emitter_worker(self):
        while True:
            item = self._beat_queue.get()
            if item is None:
                break
            is_heavy, rms = item
            if is_heavy:
                self.beat_heavy.emit(rms)
            self.beat_normal.emit(rms)

    def _process_beat(self, block: np.ndarray):
        return
        if self._onset is None:
            return
        mono = np.mean(block, axis=1).astype(np.float32)
        if self._onset(mono):
            now = time.time()
            rms = float(np.sqrt(np.mean(mono ** 2)))
            is_heavy = (rms > self._heavy_rms_threshold and
                        now - self._last_heavy_time > self._heavy_cooldown)
            if is_heavy:
                self._last_heavy_time = now
            try:
                self._beat_queue.put_nowait((is_heavy, rms))
            except queue.Full:
                pass

    # ── утилиты ──────────────────────────────────────────────────────────────

    def get_current_audio_level(self) -> float:
        with self._lock:
            return self._audio_level

    def cleanup(self):
        self._unregister()
        self.anim.clear()


# ══════════════════════════════════════════════════════════════════════════════
#  UISoundPlayer
# ══════════════════════════════════════════════════════════════════════════════

class _UISound:
    """Один активный UI-звук внутри миксера."""

    def __init__(self, data: np.ndarray, volume: float, speed: float, loop: bool):
        self.data     = data
        self.position = 0.0
        self.volume   = float(volume)
        self.speed    = float(speed)
        self.loop     = loop
        self.done     = False
        self._lock    = threading.Lock()

    def set_speed(self, speed: float):
        with self._lock:
            self.speed = max(0.01, float(speed))

    def set_volume(self, volume: float):
        with self._lock:
            self.volume = max(0.0, min(1.0, float(volume)))

    def stop(self):
        with self._lock:
            self.done = True

    def render(self, frames: int) -> np.ndarray:
        with self._lock:
            if self.done:
                return np.zeros((frames, CHANNELS), dtype="float32")
            pos  = self.position
            spd  = self.speed
            vol  = self.volume
            loop = self.loop
            n    = len(self.data)

        t       = np.arange(frames, dtype="float32")
        indices = pos + t * spd

        if loop:
            indices = indices % (n - 1)

        idx_i   = indices.astype(np.int32)
        idx_f   = (indices - idx_i)[:, None]
        max_idx = n - 1

        block = np.zeros((frames, CHANNELS), dtype="float32")
        valid = (idx_i >= 0) & (idx_i < max_idx)
        si    = idx_i[valid]
        block[valid] = self.data[si] + idx_f[valid] * (self.data[si + 1] - self.data[si])

        if not loop:
            block[idx_i >= max_idx] = self.data[max_idx]

        new_pos = pos + frames * spd
        with self._lock:
            self.position = new_pos % (n - 1) if loop else new_pos
            if not loop and new_pos >= max_idx:
                self.done = True

        return block * vol


class UISoundHandle:
    """
    Возвращается из UISoundPlayer.play().
    Позволяет менять скорость и громкость во время воспроизведения.
    """

    def __init__(self, sound: _UISound):
        self._s = sound

    @property
    def is_playing(self) -> bool:
        return not self._s.done

    def set_speed(self, speed: float):
        """Изменить скорость/тон на лету (1.0 = нормальный)."""
        self._s.set_speed(speed)

    def set_volume(self, volume: float):
        self._s.set_volume(volume)

    def stop(self):
        self._s.stop()


class UISoundPlayer:
    """
    Плеер для коротких UI-звуков. Использует тот же _AudioMixer —
    никаких отдельных стримов.

    Использование:
        ui = UISoundPlayer()
        ui.preload("click",  "assets/click.wav")
        ui.preload("whoosh", "assets/whoosh.wav")

        ui.play("click")
        h = ui.play("whoosh", pitch=0.8, loop=False)
        h.set_speed(0.4)   # замедлить на лету
        h.stop()
    """

    def __init__(self, max_simultaneous: int = 32):
        self._cache:  dict[str, np.ndarray] = {}
        self._sounds: list[_UISound]        = []
        self._max    = max_simultaneous
        self._lock   = threading.Lock()
        self._mixer  = _AudioMixer.get()
        self._mixer.add_source(self)

    def preload(self, name: str, path: str):
        """Загружает файл в память. Вызывать при старте приложения."""
        try:
            decoded = miniaudio.decode_file(
                path,
                output_format = miniaudio.SampleFormat.FLOAT32,
                nchannels     = CHANNELS,
                sample_rate   = SAMPLE_RATE,
            )
            data = np.frombuffer(decoded.samples, dtype="float32") \
                     .reshape(-1, CHANNELS).copy()
            self._cache[name] = data
            logger.debug(f"[UISoundPlayer] preloaded '{name}'")
        except Exception:
            logger.error(f"[UISoundPlayer] preload '{name}': {traceback.format_exc()}")

    def play(
        self,
        name:   str,
        pitch:  float = 1.0,
        volume: float = 1.0,
        loop:   bool  = False,
    ) -> Optional[UISoundHandle]:
        """
        Воспроизводит звук мгновенно (данные уже в памяти).

        pitch: 1.0 = нормальная скорость, 0.5 = вдвое медленнее / ниже тоном,
               2.0 = вдвое быстрее / выше тоном.
        """
        data = self._cache.get(name)
        if data is None:
            logger.warning(f"[UISoundPlayer] '{name}' not preloaded")
            return None

        sound = _UISound(data, volume, pitch, loop)

        with self._lock:
            self._sounds = [s for s in self._sounds if not s.done]
            while len(self._sounds) >= self._max:
                self._sounds.pop(0).stop()
            self._sounds.append(sound)

        return UISoundHandle(sound)

    def render(self, frames: int) -> np.ndarray:
        with self._lock:
            self._sounds = [s for s in self._sounds if not s.done]
            active = list(self._sounds)

        if not active:
            return np.zeros((frames, CHANNELS), dtype="float32")

        mix = np.zeros((frames, CHANNELS), dtype="float32")
        for s in active:
            mix += s.render(frames)
        return mix

    def stop_all(self):
        with self._lock:
            for s in self._sounds:
                s.stop()

    def cleanup(self):
        self.stop_all()
        self._mixer.remove_source(self)


# ── Синглтоны ─────────────────────────────────────────────────────────────────

import time

player    = PlaybackManager()
ui_player = UISoundPlayer()

player.load_audio("C:/Users/Chips/Desktop/charge.wav")

player.play()
player.tape(end_speed = 0.1)

time.sleep(100)