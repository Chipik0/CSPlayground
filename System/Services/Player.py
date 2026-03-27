import math
import time
import queue
import threading

import aubio
import numpy
import miniaudio

import soundfile as sf

from loguru import (
    logger
)

from PyQt5.QtCore import (
    QTimer,
    QObject,
    pyqtSignal
)

from System.Interface.Animation.LoomEngine import (
    AnimationEngine,
    Easing,
    MixMode
)

def thread_excepthook(arguments) -> None:
    logger.exception(
        "Unhandled exception in thread %s",
        arguments.thread.name,
        exc_info = (arguments.exc_type, arguments.exc_value, arguments.exc_traceback)
    )


threading.excepthook = thread_excepthook


# Audio Playback

class PlaybackManager(QObject):
    playback_state_changed = pyqtSignal(bool)
    audio_loaded = pyqtSignal(numpy.ndarray, int, float)
    speed_changed = pyqtSignal(float)

    beat_normal = pyqtSignal(float)
    beat_heavy = pyqtSignal(float)

    def __init__(
            self,
            *arguments,
            **keywords
        ) -> None:

        super().__init__(*arguments, **keywords)

        self.is_playing = False
        self.playback_start_audio_ms = 0
        self.playback_start_wall_time = 0

        self.thread = None
        self.stream = None
        self.mix_generator = None
        self.lock = threading.RLock()
        self.beat_queue = None
        self.beat_thread = None

        self.loom = AnimationEngine(fps = 60)
        self.loom.updated.connect(self.on_engine_update)

        self.setup_effect_properties()
        self.reset_playback_state()
        self.setup_beat_detection()

    # Beat Detection

    def beat_emitter_worker(self) -> None:
        while True:
            item = self.beat_queue.get()

            if item is None:
                break

            is_heavy, rms = item

            if is_heavy:
                self.beat_heavy.emit(rms)

            self.beat_normal.emit(rms)

    # Setup

    def setup_beat_detection(self) -> None:
        self.win_s = 512
        self.hop_s = 256

        self.last_heavy_time = 0
        self.heavy_cooldown = 0.3
        self.heavy_rms_threshold = 0.2

        self.onset_detector = aubio.onset("specflux", self.win_s, self.hop_s, self.fs or 44100)
        self.onset_detector.set_threshold(0.38)

        self.beat_queue = queue.Queue(maxsize = 100)

        self.beat_thread = threading.Thread(
            target = self.beat_emitter_worker,
            daemon = True
        )

        self.beat_thread.start()

    def setup_effect_properties(self) -> None:
        self.loom.add_properties(
            [
                ("speed",               1.0,                                     MixMode.NOMIX),
                ("volume",              1.0,                                     MixMode.NOMIX),
                ("channel_delay_left",  0.0,                                     MixMode.NOMIX),
                ("channel_delay_right", 0.0,                                     MixMode.NOMIX),
                ("midpass_center",      1000.0,                                  MixMode.NOMIX),
                ("midpass_q",           1.0,                                     MixMode.NOMIX),
                ("midpass_mix",         0.0,                                     MixMode.NOMIX),
                ("midpass_gain",        1.0,                                     MixMode.NOMIX),
                ("bitcrush_bits",       16,                                      MixMode.NOMIX),
                ("bitcrush_downsample", 1,                                       MixMode.NOMIX),
                ("bitcrush_mix",        0.0,                                     MixMode.NOMIX)
            ]
        )

    def reset_playback_state(self) -> None:
        defaults = {
            "speed": 1.0,
            "volume": 1.0,
            "channel_delay_left": 0.0,
            "channel_delay_right": 0.0,
            "midpass_center": 1000.0,
            "midpass_q": 1.0,
            "midpass_mix": 0.0,
            "midpass_gain": 1.0,
            "bitcrush_bits": 16,
            "bitcrush_downsample": 1,
            "bitcrush_mix": 0.0
        }

        for property, value in defaults.items():
            self.loom.set_property_base_value(property, value)

        self.fade_factor = 1.0
        self.position = 0.0
        self.speed = 1.0
        self.fs = 44100
        self.is_playing = False
        self.duration_ms = 0
        self.volume = 1.0

        self.midpass_enabled = False
        self.midpass_center = 1000.0
        self.midpass_q = 1.0
        self.midpass_gain = 1.0
        self.midpass_mix = 0.0
        self.b = [1.0, 0.0, 0.0]
        self.a = [1.0, 0.0]

        self.bitcrush_enabled = False
        self.bitcrush_bits = 16
        self.bitcrush_downsample = 1
        self.bitcrush_mix = 0.0

        self.channel_delays_ms = numpy.array([0.0, 0.0], dtype = 'float64')

        self.track_peak_level = 1.0
        self.current_audio_level = 0.0
        self.filter_states = None
        self.bitcrush_state = None

    # Loom Engine Callbacks

    def on_engine_update(self) -> None:
        channel_delay_left = self.loom.get_property_value("channel_delay_left")
        channel_delay_right = self.loom.get_property_value("channel_delay_right")

        speed = self.loom.get_property_value("speed")
        volume = self.loom.get_property_value("volume")

        center = self.loom.get_property_value("midpass_center")
        q = self.loom.get_property_value("midpass_q")
        mix = self.loom.get_property_value("midpass_mix")
        gain = self.loom.get_property_value("midpass_gain")

        bits = self.loom.get_property_value("bitcrush_bits")
        downsample = self.loom.get_property_value("bitcrush_downsample")
        bitcrush_mix = self.loom.get_property_value("bitcrush_mix")

        with self.lock:
            self.midpass_center = float(center)
            self.midpass_q = float(max(0.001, q))
            self.midpass_mix = float(max(0.0, min(1.0, mix)))
            self.midpass_gain = float(gain)

            self.b, self.a = self.compute_biquad_bandpass(self.midpass_center, self.midpass_q)

            self.channel_delays_ms[0] = float(max(0.0, channel_delay_left))
            self.channel_delays_ms[1] = float(max(0.0, channel_delay_right))

            self.speed = speed
            self.volume = volume

            self.bitcrush_bits = int(round(bits))
            self.bitcrush_downsample = max(1, int(round(downsample)))
            self.bitcrush_mix = float(bitcrush_mix)

            self.update_playback_start()

    # Audio Loading

    def load_audio(self, path: str) -> None:
        data, fs = sf.read(path, dtype = 'float32')

        if data.ndim == 1:
            data = numpy.column_stack((data, data))

        self.load_audio_from_data(data, fs)

    def load_audio_from_data(
            self,
            data: numpy.ndarray,
            fs: int
        ) -> None:

        fs_changed = self.fs != fs

        self.cleanup()
        self.reset_playback_state()

        self.fs = fs
        self.data = data
        self.duration_ms = len(self.data) / self.fs * 1000

        if self.data.ndim == 1:
            self.data = numpy.stack([self.data, self.data], axis = -1)

        if fs_changed or not self.stream:
            print("OPENING STREAM AGAIN")
            self.open_stream()
        
        else:
            print("RET STREAM")

        self.audio_loaded.emit(self.data, self.fs, len(self.data) / self.fs)

    def open_stream(self) -> None:
        self.close_stream()

        max_abs = numpy.max(numpy.abs(self.data))

        with self.lock:
            self.track_peak_level = max(max_abs, 1e-6)
            channels = self.data.shape[1]
            self.filter_states = numpy.zeros((channels, 4), dtype = 'float64')

        self.stream = miniaudio.PlaybackDevice(
            output_format = miniaudio.SampleFormat.SIGNED16,
            nchannels = self.data.shape[1],
            sample_rate = self.fs,
            buffersize_msec = 15,
            callback_periods = 4,
            thread_prio = miniaudio.ThreadPriority.HIGHEST
        )

        self.mix_generator = self.create_playback_generator()
        next(self.mix_generator)
        self.stream.start(self.mix_generator)
    
    def close_stream(self) -> None:
        try:
            if not self.stream:
                return

            self.stream.stop()
            self.stream.close()
            self.stream = None
            self.mix_generator = None
        
        except Exception as error:
            logger.error(f"Failed to close the stream: {error}")

    # Channel Delay

    def smooth_channel_delay(
            self,
            left_from_ms: float = None,
            left_to_ms: float = None,
            right_from_ms: float = None,
            right_to_ms: float = None,
            duration: float = 0.5
        ) -> None:

        if left_from_ms is None:
            left_from_ms = self.channel_delays_ms[0]

        if left_to_ms is None:
            left_to_ms = self.channel_delays_ms[0]

        if right_from_ms is None:
            right_from_ms = self.channel_delays_ms[1]

        if right_to_ms is None:
            right_to_ms = self.channel_delays_ms[1]

        left_to_ms = max(0.0, float(left_to_ms))
        right_to_ms = max(0.0, float(right_to_ms))

        if duration == 0.0:
            with self.lock:
                self.channel_delays_ms[0] = left_to_ms
                self.channel_delays_ms[1] = right_to_ms

            return

        left_from_ms = max(0.0, float(left_from_ms))
        right_from_ms = max(0.0, float(right_from_ms))

        self.loom.set_property_base_value("channel_delay_left", left_from_ms)
        self.loom.set_property_base_value("channel_delay_right", right_from_ms)
        
        self.loom.set_target_value("channel_delay_left", left_to_ms, int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("channel_delay_right", right_to_ms, int(duration * 1000), Easing.smooth)

    # Biquad Filter

    def compute_biquad_bandpass(
            self,
            center_hz: float,
            q: float
        ) -> tuple:

        fs = float(self.fs)

        if fs is None or fs <= 0:
            return (1.0, 0.0, 0.0), (0.0, 0.0)

        omega = 2.0 * math.pi * (center_hz / fs)
        sn = math.sin(omega)
        cs = math.cos(omega)
        alpha = sn / (2.0 * q)

        b0 = alpha
        b1 = 0.0
        b2 = -alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * cs
        a2 = 1.0 - alpha

        b0 /= a0
        b1 /= a0
        b2 /= a0
        a1 /= a0
        a2 /= a0

        return (b0, b1, b2), (a1, a2)

    # Position Tracking

    def get_position_ms(self) -> float:
        elapsed_ms = (time.time() - self.playback_start_wall_time) * 1000

        return self.playback_start_audio_ms + elapsed_ms * self.speed

    def update_playback_start(self) -> None:
        self.playback_start_audio_ms = self.get_position_ms()
        self.playback_start_wall_time = time.time()

    # Playback Control

    def toggle_playback(self, ms: float = None) -> None:
        if self.is_playing:
            self.stop()
        else:
            self.play(ms)

    def stop(self) -> None:
        with self.lock:
            self.is_playing = False

        self.playback_state_changed.emit(False)

    def play(self, start_position_ms: float) -> None:
        self.playback_state_changed.emit(True)

        with self.lock:
            self.position = int(start_position_ms * self.fs / 1000)
            self.is_playing = True

        self.playback_start_audio_ms = start_position_ms
        self.playback_start_wall_time = time.time()

    # Speed Control

    def set_speed(
            self,
            new_speed: float,
            duration: float = 0.0,
            cleanup_on_finish: bool = False,
            shutdown_on_finish: bool = False
        ) -> None:

        self.update_playback_start()

        if duration == 0.0:
            with self.lock:
                self.speed = new_speed

            self.loom.set_property_base_value("speed", new_speed)

            self.speed_changed.emit(new_speed)

            if cleanup_on_finish:
                self.cleanup()
            
            elif shutdown_on_finish:
                self.full_shutdown()

            return

        self.loom.set_target_value("speed", new_speed, int(duration * 1000), Easing.ease_out_cubic)

        if cleanup_on_finish:
            QTimer.singleShot(int(duration * 1000), self.cleanup)
        
        elif shutdown_on_finish:
            QTimer.singleShot(int(duration * 1000), self.full_shutdown)

    # Volume Control

    def set_volume(
            self,
            volume: float,
            duration: float = 0.0
        ) -> None:

        if duration == 0.0:
            with self.lock:
                self.volume = max(0.0, min(volume, 1.0))

            return

        self.loom.set_target_value("volume", max(0.0, min(volume, 1.0)), int(duration * 1000), Easing.ease_out_cubic)

    # Audio Processing

    def generate_resampled_block(
            self,
            frames: int,
            context: dict
        ) -> numpy.ndarray:

        channels = self.data.shape[1]
        t = numpy.arange(frames, dtype = 'float32')

        base_indices = context["position"] + t * context["speed"]

        delay_samples = (context["delays"] * context["fs"] / 1000.0).astype('float32')

        result = numpy.empty((frames, channels), dtype = 'float32')
        max_index_minus_one = context["max_index"] - 1

        for channel in range(channels):
            channel_position = base_indices - delay_samples[channel]

            index_integer = channel_position.astype(numpy.int32)
            index_float = channel_position - index_integer

            valid_mask = (index_integer >= 0) & (index_integer < max_index_minus_one)

            safe_index = index_integer[valid_mask]
            sample_0 = self.data[safe_index, channel]
            sample_1 = self.data[safe_index + 1, channel]

            channel_result = numpy.zeros(frames, dtype = 'float32')
            channel_result[valid_mask] = sample_0 + index_float[valid_mask] * (sample_1 - sample_0)

            end_mask = index_integer >= max_index_minus_one
            channel_result[end_mask] = self.data[context["max_index"], channel]

            result[:, channel] = channel_result

        return result

    def apply_midpass_filter(
            self,
            block: numpy.ndarray,
            context: dict,
            states: object
        ) -> numpy.ndarray:

        mix = context["filter_mix"]

        if mix <= 0:
            return block

        b = context["filter_b"]
        a = context["filter_a"]
        gain = context["filter_gain"]

        b0, b1, b2 = b
        a1, a2 = a

        filtered = numpy.empty_like(block)

        for channel in range(block.shape[1]):
            x = block[:, channel]
            y = numpy.empty(len(x), dtype = 'float32')

            x1, x2, y1, y2 = states[channel]

            for n in range(len(x)):
                yn = b0 * x[n] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
                y[n] = yn
                x2 = x1
                x1 = x[n]
                y2 = y1
                y1 = yn

            states[channel] = [x1, x2, y1, y2]
            filtered[:, channel] = y

        return (1.0 - mix) * block + (mix * filtered * gain)

    def apply_bitcrush(
            self,
            block: numpy.ndarray,
            context: dict,
            states: object
        ) -> numpy.ndarray:

        mix = context["bc_mix"]

        if mix <= 0:
            return block

        down = max(1, int(context["bc_down"]))
        bits = max(1, min(24, context["bc_bits"]))
        levels = (1 << int(bits)) - 1
        inv_levels = 1.0 / levels

        if down > 1:
            original_shape = block.shape
            new_len = (len(block) // down) * down
            reduced = block[:new_len].reshape(-1, down, original_shape[1])
            crushed = numpy.repeat(reduced[:, 0, :], down, axis = 0)

            if len(block) > new_len:
                last_val = block[new_len:new_len + 1]
                padding = numpy.repeat(last_val, len(block) - new_len, axis = 0)
                crushed = numpy.vstack([crushed, padding])

        else:
            crushed = block.copy()

        crushed = numpy.round((crushed + 1.0) * 0.5 * levels) * inv_levels
        crushed = crushed * 2.0 - 1.0

        return (1.0 - mix) * block + mix * crushed

    def audio_callback(
            self,
            outdata: object,
            frames: int,
            time_info: object,
            status: object
        ) -> None:

        if status:
            logger.warning(f"Status: {status}")

        with self.lock:
            if not self.is_playing or self.data is None:
                outdata.fill(0)
                return

            context = {
                "position": self.position,
                "speed": self.speed,
                "volume": self.volume * self.fade_factor,
                "delays": self.channel_delays_ms,
                "fs": self.fs,
                "max_index": len(self.data) - 1,
                "do_mid": self.midpass_enabled,
                "do_bit": self.bitcrush_enabled,
                "filter_b": self.b,
                "filter_a": self.a,
                "filter_mix": self.midpass_mix,
                "filter_gain": self.midpass_gain,
                "bc_bits": self.bitcrush_bits,
                "bc_down": self.bitcrush_downsample,
                "bc_mix": self.bitcrush_mix
            }

            filter_states = self.filter_states
            bitcrush_states = self.bitcrush_state

        block = self.generate_resampled_block(frames, context)

        self.process_beat_detection(block)

        if context["do_mid"] and filter_states is not None:
            block = self.apply_midpass_filter(block, context, filter_states)

        if context["do_bit"]:
            block = self.apply_bitcrush(block, context, bitcrush_states)

        block *= context["volume"]
        outdata[:] = block

        with self.lock:
            self.position += frames * context["speed"]
            self.current_audio_level = float(numpy.max(numpy.abs(block)) / self.track_peak_level)

            if self.position < len(self.data):
                return
            
            self.is_playing = False

    def create_playback_generator(self) -> object:
        data = yield b""

        while True:
            frames = data
            
            with self.lock:
                if not self.is_playing or self.data is None:
                    block = numpy.zeros((frames, self.data.shape[1] if self.data is not None else 2), dtype = 'float32')
                    
                else:
                    context = {
                        "position": self.position,
                        "speed": self.speed,
                        "volume": self.volume * self.fade_factor,
                        "delays": self.channel_delays_ms,
                        "fs": self.fs,
                        "max_index": len(self.data) - 1,
                        "do_mid": self.midpass_enabled,
                        "do_bit": self.bitcrush_enabled,
                        "filter_b": self.b,
                        "filter_a": self.a,
                        "filter_mix": self.midpass_mix,
                        "filter_gain": self.midpass_gain,
                        "bc_bits": self.bitcrush_bits,
                        "bc_down": self.bitcrush_downsample,
                        "bc_mix": self.bitcrush_mix
                    }

                    filter_states = self.filter_states
                    bitcrush_states = self.bitcrush_state

                    block = self.generate_resampled_block(frames, context)

                    self.process_beat_detection(block)

                    if context["do_mid"] and filter_states is not None:
                        block = self.apply_midpass_filter(block, context, filter_states)

                    if context["do_bit"]:
                        block = self.apply_bitcrush(block, context, bitcrush_states)

                    block *= context["volume"]

                    self.position += frames * context["speed"]
                    self.current_audio_level = float(numpy.max(numpy.abs(block)) / self.track_peak_level)

                    if self.position >= len(self.data):
                        self.is_playing = False

            block = numpy.clip(block, -1.0, 1.0)
            pcm = (block * 32767.0).astype(numpy.int16)
            data = yield pcm.tobytes()

    # Beat Detection

    def process_beat_detection(self, block: numpy.ndarray) -> None:
        return
        mono = numpy.mean(block, axis = 1).astype('float32')

        for i in range(0, len(mono), self.hop_s):
            segment = mono[i:i + self.win_s]

            if len(segment) < self.win_s:
                break

            is_onset = self.onset_detector(segment)

            if not is_onset:
                continue

            rms = numpy.sqrt(numpy.mean(segment ** 2))
            current_time = time.time()
            is_heavy = False

            if (current_time - self.last_heavy_time) > self.heavy_cooldown:
                if rms > self.heavy_rms_threshold:
                    is_heavy = True
                    self.last_heavy_time = current_time

            try:
                self.beat_queue.put_nowait((is_heavy, float(rms)))

            except queue.Full:
                pass

    def get_current_audio_level(self) -> float:
        with self.lock:
            return self.current_audio_level

    # Scratch Effect

    def tape(
            self,
            start_speed: float = None,
            end_speed: float = None,
            duration: float = 0.5,
            cleanup_on_finish: bool = False,
            shutdown_on_finish: bool = False
        ) -> None:

        if start_speed is None:
            start_speed = self.speed

        self.set_speed(end_speed if end_speed is not None else self.speed, duration, cleanup_on_finish, shutdown_on_finish)

    # Channel Delay Control

    def set_channel_delay_ms(
            self,
            left_ms: float,
            right_ms: float
        ) -> None:

        with self.lock:
            self.channel_delays_ms[0] = float(max(0.0, left_ms))
            self.channel_delays_ms[1] = float(max(0.0, right_ms))
            
            self.loom.set_property_base_value("channel_delay_left", float(max(0.0, left_ms)))
            self.loom.set_property_base_value("channel_delay_right", float(max(0.0, right_ms)))

    # Bitcrush Effect

    def enable_bitcrush(
            self,
            bits: int = 8,
            downsample: int = 4,
            mix: float = 1.0,
            duration: float = 0.0
        ) -> None:

        with self.lock:
            if self.bitcrush_state is None and self.data is not None:
                channels = self.data.shape[1]
                self.bitcrush_state = numpy.zeros((channels, 2), dtype = 'float64')

            if duration == 0.0:
                self.bitcrush_bits = bits
                self.bitcrush_downsample = downsample
                self.bitcrush_mix = mix
                self.bitcrush_enabled = True
                return

            self.bitcrush_enabled = True

        self.loom.set_target_value("bitcrush_bits", float(bits), int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("bitcrush_downsample", float(max(1, int(downsample))), int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("bitcrush_mix", float(max(0.0, min(1.0, mix))), int(duration * 1000), Easing.smooth)

    def disable_bitcrush(
            self,
            duration: float = 0.0
        ) -> None:

        with self.lock:
            if not self.bitcrush_enabled:
                return

            if duration == 0.0:
                self.bitcrush_bits = 24.0
                self.bitcrush_downsample = 1.0
                self.bitcrush_mix = 0.0
                self.bitcrush_enabled = False
                return

        self.loom.set_target_value("bitcrush_bits", 24.0, int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("bitcrush_downsample", 1.0, int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("bitcrush_mix", 0.0, int(duration * 1000), Easing.smooth)

    # Midpass Effect

    def enable_midpass(
            self,
            center_hz: float = 1000.0,
            q: float = 1.0,
            mix: float = 1.0,
            gain: float = 1.0,
            duration: float = 0.0
        ) -> None:

        with self.lock:
            if duration == 0.0:
                self.midpass_enabled = True
                self.midpass_center = float(center_hz)
                self.midpass_q = float(q)
                self.midpass_mix = float(min(max(mix, 0.0), 1.0))
                self.midpass_gain = float(gain)
                self.b, self.a = self.compute_biquad_bandpass(self.midpass_center, self.midpass_q)

                return

            self.b, self.a = self.compute_biquad_bandpass(self.midpass_center, self.midpass_q)

            if self.filter_states is None and self.data is not None:
                channels = self.data.shape[1]
                self.filter_states = numpy.zeros((channels, 4), dtype = 'float64')

            self.midpass_enabled = True

        self.loom.set_target_value("midpass_center", float(center_hz), int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("midpass_q", float(q), int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("midpass_mix", float(max(0.0, min(mix, 1.0))), int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("midpass_gain", float(gain), int(duration * 1000), Easing.smooth)

    def disable_midpass(
            self,
            duration: float = 0.0
        ) -> None:

        if duration == 0:
            with self.lock:
                self.midpass_enabled = False

                if self.filter_states is not None:
                    self.filter_states.fill(0.0)

            return

        self.loom.set_target_value("midpass_mix", 0.0, int(duration * 1000), Easing.smooth)
        self.loom.set_target_value("midpass_gain", 0.0, int(duration * 1000), Easing.smooth)

    # Cleanup

    def cleanup(self) -> None:
        with self.lock:
            self.is_playing = False

            self.data = None
            self.position = 0.0
            self.filter_states = None
            self.bitcrush_state = None

    def full_shutdown(self) -> None:
        self.cleanup()
        self.loom.clear()

        with self.lock:
            self.close_stream()

            if self.beat_queue:
                self.beat_queue.put(None)

# UI Sounds

class UISound:
    def __init__(
            self,
            sound_id: int,
            manager: object
        ) -> None:

        self.sound_id = sound_id
        self.manager = manager

    def set_speed(self, new_speed: float) -> None:
        self.manager.set_speed(self.sound_id, new_speed)

    def set_volume(self, new_volume: float) -> None:
        self.manager.set_volume(self.sound_id, new_volume)

    def stop(self) -> None:
        self.manager.stop_sound(self.sound_id)

class UISoundManager:
    def __init__(self) -> None:

        self.preloaded = {}
        self.active_sounds = []
        self.next_sound_id = 0
        self.lock = threading.RLock()
        self.sample_rate = 44100
        self.channels = 2

        self.device = None

    def ensure_device(self):
        if self.device is None:
            self.device = miniaudio.PlaybackDevice(
                output_format = miniaudio.SampleFormat.SIGNED16,
                nchannels = self.channels,
                sample_rate = self.sample_rate,
                buffersize_msec = 15,
                callback_periods = 4,
                thread_prio = miniaudio.ThreadPriority.HIGHEST
            )
            
            self.mix_generator = self.create_mix_generator()
            next(self.mix_generator)
            
            self.device.start(self.mix_generator)

    def preload(
            self,
            path: str,
            name: str
        ) -> None:

        if name in self.preloaded:
            return

        data, fs = sf.read(path, dtype = 'float32')

        if data.ndim == 1:
            data = numpy.column_stack((data, data))

        if fs != self.sample_rate:
            ratio = self.sample_rate / fs
            num_samples = int(len(data) * ratio + 0.5)

            if num_samples < 1:
                num_samples = 1

            indices = numpy.linspace(0, len(data) - 1, num_samples, endpoint = False)
            resampled = numpy.empty((num_samples, self.channels), dtype = 'float32')

            for channel in range(self.channels):
                resampled[:, channel] = numpy.interp(indices, numpy.arange(len(data)), data[:, channel])

            data = resampled

        self.preloaded[name] = data

    def play_sound(
            self,
            name: str,
            loop: bool = False,
            speed: float = 1.0,
            volume: float = 1.0
        ) -> UISound:

        if name not in self.preloaded:
            return UISound(-1, self)

        with self.lock:
            if len(self.active_sounds) > 60:
                return UISound(-1, self)

        self.ensure_device()

        sound_id = self.next_sound_id
        self.next_sound_id += 1

        instance = {
            "id": sound_id,
            "data": self.preloaded[name],
            "position": 0.0,
            "speed": max(0.01, float(speed)),
            "volume": max(0.0, min(1.0, float(volume))),
            "loop": bool(loop),
            "active": True
        }

        with self.lock:
            self.active_sounds.append(instance)

        return UISound(sound_id, self)

    def stop_sound(self, sound_id: int) -> None:
        with self.lock:
            i = 0

            while i < len(self.active_sounds):
                if self.active_sounds[i]["id"] == sound_id:
                    self.active_sounds[i]["active"] = False
                    del self.active_sounds[i]
                    return

                i += 1

    def set_speed(
            self,
            sound_id: int,
            new_speed: float
        ) -> None:

        with self.lock:
            for sound in self.active_sounds:
                if sound["id"] == sound_id:
                    sound["speed"] = max(0.01, float(new_speed))
                    return

    def set_volume(
            self,
            sound_id: int,
            new_volume: float
        ) -> None:

        with self.lock:
            for sound in self.active_sounds:
                if sound["id"] == sound_id:
                    sound["volume"] = max(0.0, min(1.0, float(new_volume)))
                    return

    def stop_all(self) -> None:
        with self.lock:
            self.active_sounds.clear()

    def cleanup(self) -> None:
        if self.device:
            self.device.stop()
            self.device.close()
            self.device = None

    def create_mix_generator(self) -> object:
        data = yield b""

        while True:
            frames = data
            block = numpy.zeros((frames, self.channels), dtype = 'float32')

            with self.lock:
                i = 0

                while i < len(self.active_sounds):
                    sound = self.active_sounds[i]

                    if not sound["active"]:
                        del self.active_sounds[i]
                        continue

                    sound_block = self.get_resampled_block(sound, frames)

                    if sound_block is None:
                        sound["active"] = False
                        del self.active_sounds[i]
                        continue

                    block += sound_block * sound["volume"]
                    i += 1

            block = numpy.clip(block, -1.0, 1.0)
            pcm = (block * 32767.0).astype(numpy.int16)
            data = yield pcm.tobytes()

    def get_resampled_block(
            self,
            sound: dict,
            frames: int
        ) -> numpy.ndarray:

        data = sound["data"]

        if len(data) == 0:
            return None

        position = sound["position"]
        speed = sound["speed"]
        max_index = len(data) - 1

        if position >= max_index and not sound["loop"]:
            return None

        t = numpy.arange(frames, dtype = 'float32')
        base_indices = position + t * speed
        result = numpy.empty((frames, self.channels), dtype = 'float32')

        for channel in range(self.channels):
            channel_position = base_indices
            index_integer = channel_position.astype(numpy.int32)
            index_float = channel_position - index_integer
            valid = (index_integer >= 0) & (index_integer < max_index)
            channel_result = numpy.zeros(frames, dtype = 'float32')

            if numpy.any(valid):
                safe = index_integer[valid]
                sample_0 = data[safe, channel]
                sample_1 = data[safe + 1, channel]

                channel_result[valid] = sample_0 + index_float[valid] * (sample_1 - sample_0)

            beyond = index_integer >= max_index
            channel_result[beyond] = data[max_index, channel] if max_index >= 0 else 0.0
            result[:, channel] = channel_result

        new_position = position + frames * speed

        if new_position >= len(data):
            if sound["loop"]:
                new_position = new_position % len(data)

            else:
                new_position = len(data)

        sound["position"] = new_position

        return result

# Globals

player = PlaybackManager()
ui_player = UISoundManager()

prefix = "System/Assets/Sounds"
import sys, os
def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    
    else:
        base_path = os.path.abspath(".")
    
    return os.path.join(base_path, relative_path)

sounds = [
    (f"{prefix}/App/Startup.wav", "App/Startup"),
    (f"{prefix}/Tick.wav", "Tick"),
    (f"{prefix}/ArrowTick.wav", "ArrowTick"),
    (f"{prefix}/Rewind2.wav", "Rewind")
]

for sound_path, sound_id in sounds:
    absolute_path = get_resource_path(sound_path)
    ui_player.preload(absolute_path, sound_id)