from pathlib import Path

prefix = "System/Assets/Sounds"
base   = Path(prefix)

sounds = []

for path in base.rglob("*.wav"):
    rel  = path.relative_to(base).as_posix()  # нормальные слэши, без Windows-ужаса
    name = rel[:-4]  # убираем .wav

    sounds.append((f"{prefix}/{rel}", name))

print(sounds)