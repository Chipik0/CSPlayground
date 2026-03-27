import os
import re
import sys
import json
import time
import random
import platform
import subprocess

from System.Services import (
    Player
)

from urllib.request import (
    urlopen
)

from PyQt5.QtGui import (
    QFont,
    QPainterPath
)

from PyQt5.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QParallelAnimationGroup
)

import numpy as np

from System.Common.Constants import *

def get_fox_image(url="https://randomfox.ca/floof/"):
    try:
        with urlopen(url, timeout = 2) as r:
            info = json.loads(r.read())
            return info.get("image")
    
    except:
        return None

def gaussian_filter1d_np(data, sigma):
    radius = int(3 * sigma)
    x = np.arange(-radius, radius + 1)
    
    kernel = np.exp(-(x**2) / (2 * sigma**2))
    kernel /= kernel.sum()
    
    return np.convolve(data, kernel, mode='same')

def medfilt_np(data, kernel_size):
    if kernel_size % 2 == 0:
        raise ValueError("kernel_size must be odd")
    
    pad_width = kernel_size // 2
    padded = np.pad(data, pad_width, mode='edge')
    shape = (data.size, kernel_size)
    strides = (padded.strides[0], padded.strides[0])
    windows = np.lib.stride_tricks.as_strided(padded, shape=shape, strides=strides)
    
    return np.median(windows, axis=1)

def get_time():
    t = time.localtime()
    hours = t.tm_hour
    
    if 19 <= hours <= 21:
        return random.choice(
            [
                "Good evening.",
                "Evening vibes.",
                "Time to unwind."
            ]
        )
    
    elif hours >= 22 or hours <= 5:
        return random.choice(
            [
                "Sleep tight.",
                "Sweet dreams.",
                "Nighty night."
            ]
        )
    
    elif 6 <= hours <= 11:
        return random.choice(
            [
                "Good morning.",
                "Rise and shine.",
                "You should get a coffee."
            ]
        )
    
    elif 12 <= hours <= 18:
        return random.choice(
            [
                "Good afternoon.",
                "A great day.",
                "Music time."
            ]
        )
    
    else:
        return "what the fuck"

def NDot(size):
    Ndot = QFont("Ndot 57")
    px_size = round(size * 120 / 72)
    Ndot.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    Ndot.setPixelSize(px_size)
    
    return Ndot

def NType(size):
    Ntype = QFont("NType 82")
    px_size = round(size * 120 / 72)
    Ntype.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    Ntype.setPixelSize(px_size)
    
    return Ntype

def open_file(path):
    if platform.system() == "Windows":
        os.startfile(path)
    
    elif platform.system() == "Darwin":
        subprocess.run(["open", path])
    
    else:
        subprocess.run(["xdg-open", path])

def get_songs_path(relative_path: str) -> str:
    normalized_parts = os.path.normpath(relative_path).split(os.sep)
    full_path = os.path.join(os.path.expanduser("~"), "Songs", *normalized_parts)
    
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    return full_path

def run(*args, **kwargs):
    if os.name == "nt":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    
    return subprocess.run(*args, **kwargs)

def ui_sound(name, tone = None, volume = 1.0, random_spread = 0.03, loop = False):
    if CurrentSettings["disable_sounds"]:
        return
    
    if not tone:
        tone = random.uniform(1 - random_spread, 1 + random_spread)
    
    return Player.ui_player.play_sound(
        name,
        loop,
        tone,
        volume
    )

def auto_cast(value: str):
    if value is None:
        return None

    v = str(value).strip()

    if v.lower() in {"true", "yes", "1"}:
        return True
    
    if v.lower() in {"false", "no", "0"}:
        return False

    try:
        return int(v)
    
    except ValueError:
        pass

    try:
        return float(v)
    
    except ValueError:
        pass

    return value

class Animations:
    def make_animation(object, keyframes: list, property: bytes, duration: int, curve: QEasingCurve = QEasingCurve.OutCubic, loop = False, finished = None):
        anim = QPropertyAnimation(object, property)
        anim.setDuration(duration)
        anim.setKeyValues(keyframes)
        anim.setEasingCurve(curve)
        
        if loop:
            anim.setLoopCount(-1)
        
        if finished:
            anim.finished.connect(finished)

        return anim
    
    def group_animate(animations, finished = None, valueChanged = None, multiplier = 1.0):
        anim_group = QParallelAnimationGroup()

        if multiplier == 1.0:
            multiplier = float(CurrentSettings["animation_multiplier"])

        if multiplier != 1.0:
            for animation in animations:
                animation.setDuration(int(animation.duration() * multiplier))

        for animation in animations:
            if valueChanged:
                animation.valueChanged.connect(valueChanged)
            
            anim_group.addAnimation(animation)
        
        if finished:
            anim_group.finished.connect(finished)

        return anim_group

def normalize_size(width, height, max_ref = 1500):
    return min(max(width, height) / max_ref, 1.0)

def get_scale(width, height, base_scale=1.4, min_scale=1.1, max_ref=1000):
    norm = normalize_size(width, height, max_ref)
    return min_scale + (base_scale - min_scale) * (1 - norm)

def get_rotation(width, height, base_angle=50, min_angle=10, max_ref=1600):
    norm = normalize_size(width, height, max_ref)
    max_angle = int(min_angle + (base_angle - min_angle) * (1 - norm))

    return random.choice(
        [
            random.randint(-max_angle, -int(max_angle / 2)),
            random.randint(int(max_angle / 2), max_angle)
        ]
    )

def get_optimal_tilt(width, height):
    coeff_w = 900 / width
    coeff_h = 900 / height

    tilt = int((coeff_h + coeff_w) * 7)
    return tilt

def parse_svg_path_data(d_string: str) -> QPainterPath:
    path = QPainterPath()
    tokens = re.findall(r'([a-zA-Z]|-?[\d\.]+)', d_string)
    
    current_x = 0.0
    current_y = 0.0
    
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        
        if not cmd[0].isalpha():
            pass
        
        else:
            i += 1
        
        if cmd == 'M':
            x = float(tokens[i]); y = float(tokens[i+1])
            path.moveTo(x, y)
            current_x, current_y = x, y
            i += 2
        
        elif cmd == 'L':
            x = float(tokens[i]); y = float(tokens[i+1])
            path.lineTo(x, y)
            current_x, current_y = x, y
            i += 2
        
        elif cmd == 'H':
            x = float(tokens[i])
            path.lineTo(x, current_y)
            current_x = x
            i += 1
        
        elif cmd == 'V':
            y = float(tokens[i])
            path.lineTo(current_x, y)
            current_y = y
            i += 1
        
        elif cmd == 'C':
            c1x = float(tokens[i]);   c1y = float(tokens[i+1])
            c2x = float(tokens[i+2]); c2y = float(tokens[i+3])
            ex = float(tokens[i+4]);  ey = float(tokens[i+5])
            path.cubicTo(c1x, c1y, c2x, c2y, ex, ey)
            current_x, current_y = ex, ey
            i += 6
        
        elif cmd == 'Z' or cmd == 'z':
            path.closeSubpath()
        
        else:
            pass
    
    return path