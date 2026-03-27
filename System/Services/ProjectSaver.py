from __future__ import annotations

import os
import json
import copy
import random
import shutil
import ffmpeg

from collections.abc import Callable

from System.Common import (
    Utils
)

from System.Interface import (
    Windows
)

from System.Services import (
    Porter,
    Encoder,
    GlyphEffects,
    RealTimeVisualizer,
)

from System.Common.Constants import (
    DEVICES,
    FFMPEG_PATH,
    FFPROBE_PATH,
    PortVariants,
    CurrentSettings,
    DEFAULT_DURATION,
    DEFAULT_BRIGHTNESS,
    number_model_to_code
)

def get_metadata(file_path: str) -> tuple[str | None, str]:
    try:
        probe = ffmpeg.probe(file_path, cmd=FFPROBE_PATH)
    except ffmpeg.Error:
        return None, "Unknown Artist"

    tags   = probe.get("format", {}).get("tags", {})
    title  = tags.get("title") or tags.get("TITLE")
    artist = tags.get("artist") or tags.get("ARTIST") or "Unknown Artist"

    return title, artist

class SyncedDict(dict):
    def __init__(
        self,
        *args,
        sync_callback: Callable,
        composition:   Composition,
        **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)

        self.composition    = composition
        self._sync_callback = sync_callback

        self._glyph_id_to_track: dict[int, str] = {}
        self.visualizator_data:  dict           = {}
        self._is_batching:       bool           = False
        self._pending_keys:      set[int]       = set()

        self._process_initial_data()

    def start_batching(self) -> None:
        self._is_batching = True
        self._pending_keys.clear()

    def mark_dirty(self, key: int) -> None:
        if self._is_batching:
            self._pending_keys.add(key)

    def stop_batching(self) -> None:
        if not self._is_batching:
            return

        self._is_batching = False

        if self._pending_keys:
            for key in self._pending_keys:
                if key in self:
                    self._sync_item_logic(key, self[key])

            self._finalize_sync()

        self._pending_keys.clear()

    def _process_initial_data(self) -> None:
        for glyph_id, glyph_data in self.items():
            if track := glyph_data.get("track"):
                self._glyph_id_to_track[glyph_id] = track

            self._process_glyph_effect(glyph_id, glyph_data)
            self._add_glyph_to_visualizator(glyph_id, glyph_data)

    def _process_glyph_effect(
        self,
        glyph_id:   int,
        glyph_data: dict
    ) -> None:
        
        effect = glyph_data.get("effect")

        if effect and effect["name"] != "None":
            self.composition.cached_effects[glyph_id] = GlyphEffects.effect_to_glyph(
                glyph_data,
                self.composition.bpm,
                self.composition.model,
            )
        
        else:
            self.composition.cached_effects.pop(glyph_id, None)

    def _add_glyph_to_visualizator(
        self,
        glyph_id:   int,
        glyph_data: dict
    ) -> None:
        
        track = glyph_data["track"]

        if track not in self.visualizator_data:
            self.visualizator_data[track] = {}

        effect = glyph_data.get("effect")

        if not effect or effect["name"] == "None":
            self.visualizator_data[track][glyph_id] = glyph_data

        elif glyph_id in self.composition.cached_effects:
            for idx, effect_glyph in enumerate(self.composition.cached_effects[glyph_id]):
                self.visualizator_data[track][f"effect_{glyph_id}_{idx}"] = effect_glyph

    def _remove_glyph_from_visualizator(self, glyph_id: int) -> None:
        track = self._glyph_id_to_track.get(glyph_id)

        if not track or track not in self.visualizator_data:
            return

        track_data = self.visualizator_data[track]
        prefix     = f"effect_{glyph_id}_"

        track_data.pop(glyph_id, None)

        for k in [k for k in track_data if str(k).startswith(prefix)]:
            track_data.pop(k)

        if not track_data:
            self.visualizator_data.pop(track)

    def _sync_item_logic(
        self,
        key:   int,
        value: dict
    ) -> None:
        
        if key in self._glyph_id_to_track:
            self._remove_glyph_from_visualizator(key)

        self._process_glyph_effect(key, value)
        self._add_glyph_to_visualizator(key, value)

        if track := value.get("track"):
            self._glyph_id_to_track[key] = track

    def _finalize_sync(self) -> None:
        self._sync_callback(self)
        self.composition.save()

    def __setitem__(
        self,
        key:   int,
        value: dict
    ) -> None:
        
        super().__setitem__(key, value)

        if self._is_batching:
            self._pending_keys.add(key)
        
        else:
            self._sync_item_logic(key, value)
            self._finalize_sync()

    def __delitem__(self, key: int) -> None:
        self.composition.cached_effects.pop(key, None)
        self._remove_glyph_from_visualizator(key)
        super().pop(key, None)
        self._glyph_id_to_track.pop(key, None)

        if not self._is_batching:
            self._finalize_sync()

    def update(self, *args, **kwargs) -> None:
        data = dict(*args, **kwargs)

        if self._is_batching:
            for k, v in data.items():
                self.__setitem__(k, v)
        
        else:
            for k, v in data.items():
                super().__setitem__(k, v)
                self._sync_item_logic(k, v)

            self._finalize_sync()

    def delete_keys(self, keys: list[int]) -> None:
        for key in keys:
            self.composition.cached_effects.pop(key, None)
            self._remove_glyph_from_visualizator(key)
            super().pop(key, None)
            self._glyph_id_to_track.pop(key, None)

        self._finalize_sync()

class BaseComposition:
    def __init__(
        self,
        id:       int,
        settings: dict
    ) -> None:
        self.id                = id if id is not None else random.randint(10000000, 99999999)
        self.model             = settings.get("model")
        
        self.audio_settings    = settings.get("audio", {})
        self.bpm               = self.audio_settings.get("bpm")
        self.start_ms          = self.audio_settings.get("start_ms")
        self.end_ms            = self.audio_settings.get("end_ms")
        self.fade_in_duration  = self.audio_settings.get("fade_in", 0)
        self.fade_out_duration = self.audio_settings.get("fade_out", 0)
        self.beats             = self.audio_settings.get("beats", [])
        
        self.glyphs            = settings.get("glyphs", {})
        
        self.cropped_song_path = Utils.get_songs_path(f"{self.id}/cropped_song.ogg")
        self.full_song_path    = Utils.get_songs_path(f"{self.id}/full_song.ogg")

    def export_segment(
        self,
        input_path:  str,
        output_path: str,
        start_ms:    int,
        end_ms:      int,
        fade_in:     int = 0,
        fade_out:    int = 0
    ) -> None:
        
        start_time   = start_ms / 1000.0
        end_time     = end_ms   / 1000.0
        duration_sec = (end_ms - start_ms) / 1000.0

        stream = ffmpeg.input(input_path)
        stream = ffmpeg.filter(stream, "atrim",      start=start_time, end=end_time)
        stream = ffmpeg.filter(stream, "asetpts",    "PTS-STARTPTS")
        stream = ffmpeg.filter(stream, "dynaudnorm")

        if fade_in:
            stream = ffmpeg.filter(stream, "afade",
                type       = "in",
                start_time = 0,
                duration   = fade_in / 1000.0,
            )

        if fade_out:
            fade_start = max(0.0, duration_sec - fade_out / 1000.0)
            stream     = ffmpeg.filter(stream, "afade",
                type       = "out",
                start_time = fade_start,
                duration   = fade_out / 1000.0,
            )

        (
            ffmpeg
            .output(stream, output_path, acodec="libopus", ar=48000)
            .overwrite_output()
            .run(cmd = FFMPEG_PATH, quiet = True)
        )

    def sorted_glyphs(self) -> tuple[list[dict], list[dict]]:
        singles: list[dict] = []
        effects: list[dict] = []

        for glyph in self.glyphs.values():
            if "effect" in glyph:
                effects.append(copy.deepcopy(glyph))
            
            else:
                singles.append(copy.deepcopy(glyph))

        return singles, effects

    def prepare_cropped_audio(self, audio_path: str) -> None:
        tmp_path = self.cropped_song_path.replace(".ogg", ".opus")

        self.export_segment(
            audio_path,
            tmp_path,
            self.start_ms,
            self.end_ms,
            self.fade_in_duration,
            self.fade_out_duration,
        )

        if os.path.exists(self.cropped_song_path):
            os.remove(self.cropped_song_path)

        os.rename(tmp_path, self.cropped_song_path)

    def export(
        self,
        watermark:   str        = "Cassette",
        model:       str | None = None,
        open_folder: bool       = False
    ) -> None:
        
        if model and model != self.model:
            ported_glyphs = Porter.Port.port_glyphs(model, self)

            Encoder.glyphs_to_ogg(
                Utils.get_songs_path(f"{self.id}/cropped_song.ogg"),
                Utils.get_songs_path(f"{self.id}/Composed_{model}.ogg"),
                ported_glyphs,
                model,
                watermark,
            )
        
        else:
            singles, effects = self.sorted_glyphs()

            for effect in effects:
                singles.extend(GlyphEffects.effect_to_glyph(effect, self.bpm, self.model))

            Encoder.glyphs_to_ogg(
                self.cropped_song_path,
                Utils.get_songs_path(f"{self.id}/Composed.ogg"),
                singles,
                self.model,
                watermark,
            )

        if open_folder:
            Utils.open_file(os.path.abspath(Utils.get_songs_path(str(self.id))))
            Utils.ui_sound("App/Export")

    def export_all(self, watermark: str = "Cassette") -> None:
        Utils.ui_sound("App/ExportLong")
        self.export(watermark)

        for model in PortVariants[self.model]:
            self.export(watermark, number_model_to_code(model))

        Utils.open_file(os.path.abspath(Utils.get_songs_path(str(self.id))))

class Composition(BaseComposition):
    def __init__(
        self,
        audiofile_path: str  | None  = None,
        settings:       dict | None = None,
        id:             int  | None = None
    ) -> None:
        
        settings = settings or {}

        if id is not None:
            settings = json.load(open(Utils.get_songs_path(f"{id}/Save.json"), "r", encoding="utf-8"))

        super().__init__(id, settings)

        self.syncer         = RealTimeVisualizer.GlyphSyncer(self)
        self.version        = open("version").read()
        self.song_path      = audiofile_path
        self.brightness     = DEFAULT_BRIGHTNESS
        self.duration_ms    = DEFAULT_DURATION
        self.track_number   = DEVICES[self.model].base_tracks
        self.save_version   = settings.get("version", self.version)
        self.default_effect = "None"

        self.cached_effects: dict[int, list] = {}

        raw_glyphs: dict            = settings.get("glyphs", {})
        int_glyphs: dict[int, dict] = {int(k): v for k, v in raw_glyphs.items()}

        self.glyphs        = SyncedDict(int_glyphs, sync_callback=self.syncer.sync, composition=self)
        self.last_glyph_id = max(self.glyphs.keys()) if self.glyphs else 0

        if CurrentSettings["auto_search"]:
            self.syncer.start_scanning_loop()

        self.syncer.full_load(self.glyphs)

        os.makedirs(Utils.get_songs_path(str(self.id)), exist_ok=True)

        if audiofile_path:
            shutil.copyfile(audiofile_path, self.full_song_path)

        if not os.path.exists(self.cropped_song_path):
            self.prepare_cropped_audio(self.full_song_path)

    def new_glyph(
        self,
        track:      str,
        start:      int,
        duration:   int | None = None,
        brightness: int | None = None
    ) -> tuple[int, dict]:
        
        self.last_glyph_id += 1

        glyph: dict = {
            "track":      track,
            "start":      start,
            "duration":   duration   or self.duration_ms,
            "brightness": brightness or self.brightness,
        }

        if self.default_effect != "None":
            glyph["effect"] = {"name": self.default_effect, "settings": {}}

        self.glyphs[self.last_glyph_id] = glyph
        return self.last_glyph_id, glyph

    def get_glyph(self, glyph_id: int) -> dict | None:
        return self.glyphs.get(glyph_id)

    def copy_glyph(
        self,
        glyph:    dict,
        offset:   int        = 0,
        audio_ms: int | None = None
    ) -> tuple[int, dict] | tuple[None, None]:
        
        new_glyph = copy.deepcopy(glyph)
        start     = glyph["start"] + offset
        duration  = glyph["duration"]

        if audio_ms is not None:
            audio_ms = int(audio_ms)

            if start >= audio_ms:
                return None, None

            end = start + duration

            if end > audio_ms:
                duration = audio_ms - start

        if duration < 10:
            return None, None

        new_glyph["start"]    = start
        new_glyph["duration"] = duration

        self.last_glyph_id             += 1
        self.glyphs[self.last_glyph_id] = new_glyph

        return self.last_glyph_id, new_glyph

    def save(self) -> None:
        save_path = Utils.get_songs_path(f"{self.id}/Save.json")
        os.makedirs(Utils.get_songs_path(str(self.id)), exist_ok=True)

        if os.path.exists(save_path):
            with open(save_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["glyphs"] = self.glyphs

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        
        else:
            title, author = get_metadata(self.full_song_path)
            title  = title  or os.path.basename(self.song_path)
            author = author or "Unknown Artist"

            data = {
                "audio": {
                    "title":    title,
                    "artist":   author,
                    "start_ms": self.start_ms,
                    "end_ms":   self.end_ms,
                    "bpm":      self.bpm,
                    "beats":    self.beats,
                    "fade_in":  self.fade_in_duration,
                    "fade_out": self.fade_out_duration,
                },
                "progress": 0,
                "model":    self.model,
                "version":  self.version,
                "glyphs":   self.glyphs,
            }

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

    def update_bunch_of_glyphs(self, data: dict[int, dict]) -> None:
        self.glyphs.update(data)

    def delete_bunch_of_glyphs(self, keys: list[int]) -> None:
        self.glyphs.delete_keys(keys)

    def replace_glyph(
        self,
        id:   int,
        data: dict
    ) -> None:
        
        self.glyphs[id] = data

    def delete_glyph(self, id: int) -> None:
        del self.glyphs[id]

    def delete_glyphs(self, keys: list[int]) -> None:
        self.glyphs.delete_keys(keys)

    def set_brightness(self, brightness: int) -> None:
        self.brightness = brightness

    def set_duration(self, duration: int) -> None:
        self.duration_ms = duration

    def set_default_effect(self, effect_name: str) -> None:
        self.default_effect = effect_name

    def all_glyphs(self) -> dict[int, dict]:
        return self.glyphs

    @property
    def batching_mode(self) -> bool:
        return self.glyphs._is_batching

    def start_batching(self) -> None:
        if self.batching_mode:
            return
        
        self.glyphs.start_batching()

    def stop_batching(self) -> None:
        if not self.batching_mode:
            return
        
        self.glyphs.stop_batching()

class MinimalComposition(BaseComposition):
    def __init__(self, id: int) -> None:
        settings = json.load(open(Utils.get_songs_path(f"{id}/Save.json"), "r", encoding="utf-8"))

        super().__init__(id, settings)

        if os.path.exists(self.cropped_song_path):
            return
        
        if not os.path.exists(self.full_song_path):
            Windows.ErrorWindow("Corrupted!", "This save is corrupted.").exec_()
            return

        self.prepare_cropped_audio(self.full_song_path)

class QuickComposition(Composition):
    def __init__(
        self,
        audio_path: str,
        glyphs:     dict[int, dict],
        settings:   dict
    ) -> None:
        
        settings["glyphs"] = glyphs
        
        super().__init__(
            audiofile_path = audio_path,
            settings = settings
        )

        self.save()