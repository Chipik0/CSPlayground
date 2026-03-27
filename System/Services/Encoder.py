import zlib
import math
import base64
import shutil

from mutagen.oggopus import (
    OggOpus
)

from System.Common.Constants import (
    DEVICES,
    DOT_FONT,
    VISUAL_EASINGS
)

TIME_STEP_MS = 16.666  # 60 FPS

def glyphs_to_ogg(
        path_to_audio: str,
        destination:   str,
        glyphs:        dict,
        model_code:    str,
        watermark:     str = "Cassette"
    ) -> None:
    
    device = DEVICES.get(model_code)

    if not device:
        raise ValueError(f"Unsupported device model: {model_code}")

    audio_duration = get_audio_duration(path_to_audio) * 1000
    author_lines   = math.ceil(float(audio_duration) / TIME_STEP_MS)
    author_data    = [[0 for _ in range(device.columns)] for _ in range(author_lines)]

    parsed_glyphs = parse_glyphs(glyphs, device)
    author_data   = apply_glyphs_to_author(parsed_glyphs, author_data)
    custom1_data  = generate_point_list(audio_duration, watermark)[0]
    nglyph_data   = prepare_nglyph_data(device, author_data, custom1_data)

    author_base64, custom1_base64 = compress_and_encode_data(nglyph_data)
    metadata = prepare_metadata(device, author_base64, custom1_base64)

    run_ffmpeg(path_to_audio, destination, metadata)

def convert_to_glyphs(path: str) -> list[dict] | None:
    file = open(path).read()

    if "\t" in file:
        return labels_to_glyphs(file)

    print("unknown")
    return None

def get_audio_duration(path_to_audio: str) -> float:
    audio = OggOpus(path_to_audio)
    return audio.info.length

def parse_glyphs(glyphs: dict, device: object) -> list[dict]:
    parsed = []

    for glyph in glyphs:
        start, duration = round(glyph["start"]), round(glyph["duration"])
        rounded_start   = get_nearest_divisable_by(start, TIME_STEP_MS)
        rounded_end     = get_nearest_divisable_by(start + duration, TIME_STEP_MS)

        if rounded_end <= rounded_start:
            rounded_end = rounded_start + TIME_STEP_MS

        track_idx = int(glyph["track"])
        segments  = glyph.get("segments")

        if segments:
            indices = [device.get_array_indexes(track_idx, s + 1)[0] for s in segments]
        
        else:
            indices = device.get_array_indexes(track_idx, 0)

        item: dict = {
            "rastered_start": rounded_start,
            "rastered_end":   rounded_end,
            "array_indexes":  indices,
        }

        if "keyframes" in glyph:
            item["keyframes"] = glyph["keyframes"]
            item["easing"]    = glyph["easing"]
        
        else:
            item["brightness"] = glyph["brightness"]

        parsed.append(item)

    return parsed

def get_nearest_divisable_by(number: float, divisor: float) -> float:
    return round(number / divisor) * divisor

def apply_glyphs_to_author(
        parsed_glyphs: list[dict],
        author_data:   list[list[int]],
    ) -> list[list[int]]:
    
    for glyph in parsed_glyphs:
        start_row = round(glyph["rastered_start"] / TIME_STEP_MS)
        end_row   = round(glyph["rastered_end"]   / TIME_STEP_MS)
        rows      = list(range(start_row, end_row))

        if not rows:
            continue

        for i, row_idx in enumerate(rows):
            if not (0 <= row_idx < len(author_data)):
                continue

            br_percent  = get_glyph_brightness(glyph, i, len(rows))
            light_level = max(0, min(4095, round((br_percent / 100.0) * 4095.0)))

            for led_index in glyph["array_indexes"]:
                author_data[row_idx][led_index] = light_level

    return author_data

def get_glyph_brightness(
        parsed_glyph: dict,
        step_idx:     int,
        total_steps:  int
    ) -> float:
    
    if "keyframes" not in parsed_glyph:
        return parsed_glyph["brightness"]

    keyframes   = parsed_glyph["keyframes"]
    easing_func = VISUAL_EASINGS[parsed_glyph["easing"]]
    progress    = step_idx / (total_steps - 1) if total_steps > 1 else 1.0

    if progress <= keyframes[0][0]:  return keyframes[0][1]
    if progress >= keyframes[-1][0]: return keyframes[-1][1]

    for keyframe_index in range(len(keyframes) - 1):
        (time_1, brightness_1), (time_2, brightness_2) = keyframes[keyframe_index], keyframes[keyframe_index + 1]

        if not (time_1 <= progress <= time_2):
            continue

        duration = time_2 - time_1
        local_t  = (progress - time_1) / duration if duration > 0 else 1.0

        return brightness_1 + (brightness_2 - brightness_1) * easing_func(local_t)

    return 100.0

def generate_point_list(
        duration_ms: float,
        text:        str,
        view_width:  int = 24
    ) -> tuple[list[str], list[list[int]]]:
    
    matrix        = get_text_matrix(text, view_width)
    actual_width  = len(matrix[0])
    ms_per_column = duration_ms / (actual_width - 1) if actual_width > 1 else 0
    points_list   = []

    for col in range(actual_width):
        start_ms = int(col * ms_per_column)

        for row in range(5):
            if matrix[row][col] == 1:
                points_list.append(f"{start_ms}-{row}")

    return points_list, matrix

def get_text_matrix(
        text:      str,
        min_width: int = 24
    ) -> list[list[int]]:
    
    font          = DOT_FONT
    matrix_height = 5
    raw_matrix    = [[] for _ in range(matrix_height)]

    for char in text:
        char_data = font.get(char) or font.get(char.upper(), font[" "])

        for row_index in range(matrix_height):
            raw_matrix[row_index].extend(char_data[row_index])
            raw_matrix[row_index].append(0)

    current_width = len(raw_matrix[0])
    final_width   = max(current_width, min_width)
    pad_left      = (final_width - current_width) // 2
    pad_right     = final_width - current_width - pad_left

    return [
        [0] * pad_left + raw_matrix[r] + [0] * pad_right
        for r in range(matrix_height)
    ]

def prepare_nglyph_data(
        device:       object,
        author_data:  list[list[int]],
        custom1_data: list[str],
    ) -> dict:
    
    return {
        "VERSION":     1,
        "PHONE_MODEL": device.code_name,
        "AUTHOR":      [f"{','.join(str(e) for e in line)}," for line in author_data],
        "CUSTOM1":     custom1_data,
    }

def compress_and_encode_data(nglyph_data: dict) -> tuple[str, str]:
    author_raw  = ("\r\n".join(nglyph_data["AUTHOR"]) + "\r\n").encode("utf-8")
    custom1_raw = (",".join(nglyph_data["CUSTOM1"])   + ",").encode("utf-8")

    author_compressed  = zlib.compress(author_raw,  zlib.Z_BEST_COMPRESSION)
    custom1_compressed = zlib.compress(custom1_raw, zlib.Z_BEST_COMPRESSION)

    return encode_base64(author_compressed), encode_base64(custom1_compressed)

def encode_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8").removesuffix("==").removesuffix("=")

def prepare_metadata(
        device:         object,
        author_base64:  str,
        custom1_base64: str
    ) -> dict:
    
    return {
        "TITLE":    "Made with Cassette",
        "ALBUM":    "Made with Cassette",
        "AUTHOR":   author_base64,
        "COMPOSER": f"v1-{device.composer_code_name} Glyph Composer",
        "CUSTOM1":  custom1_base64,
        "CUSTOM2":  device.custom2_str,
    }

def run_ffmpeg(
        path_to_audio: str,
        destination:   str,
        metadata:      dict
    ) -> None:
    
    if path_to_audio != destination:
        shutil.copy2(path_to_audio, destination)

    audio = OggOpus(destination)

    for key, value in metadata.items():
        audio[key] = str(value)

    audio.save()

def labels_to_glyphs(data: str) -> list[dict]:
    labels          = [l for l in data.split("\n") if l.strip()]
    grouped_glyphs: dict = {}

    for line in labels:
        parts_tab = line.split("\t")

        if len(parts_tab) < 3:
            continue

        start_ms    = int(float(parts_tab[0]) * 1000)
        end_ms      = int(float(parts_tab[1]) * 1000)
        duration_ms = end_ms - start_ms
        label       = parts_tab[2]

        parts_label    = label.split("-")
        track_full     = parts_label[0]
        brightness     = parts_label[1]
        end_brightness = parts_label[2] if len(parts_label) == 4 else None

        if "." in track_full:
            track_id, segment_str = track_full.split(".")
            current_segment = int(segment_str) - 1
            has_segment     = True
        
        else:
            track_id        = track_full
            current_segment = None
            has_segment     = False

        group_key = (start_ms, duration_ms, track_id, brightness, end_brightness, has_segment)

        if group_key in grouped_glyphs:
            if has_segment:
                grouped_glyphs[group_key]["segments"].append(current_segment)
            
            continue

        new_glyph: dict = {
            "start":      start_ms,
            "duration":   duration_ms,
            "track":      track_id,
            "brightness": brightness,
        }

        if end_brightness:
            new_glyph["end_brightness"] = end_brightness

        if has_segment:
            new_glyph["segments"] = [current_segment]

        grouped_glyphs[group_key] = new_glyph

    return list(grouped_glyphs.values())