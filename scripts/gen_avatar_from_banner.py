"""
Генератор аватарки из баннера channel-banner.svg.

Дизайн НЕ меняется — берётся из bot/assets/banner-channel.svg.
Только перекомпонуется под формат аватарки 512x512:
  - Капля: по центру сверху (увеличена)
  - Текст: по центру снизу
  - Декоративные точки: в углах

Создаёт:
  - bot/assets/avatar-channel.gif  (512x512, 2.5 сек, 24 fps, 60 кадров)
  - bot/assets/avatar-channel.webm (VP9, оптимизированный для TG)
  - bot/assets/avatar-static.png   (статичный кадр для бота)

Анимация (минимальная, чтобы не портить дизайн):
  - Капля пульсирует: scale 0.97 ↔ 1.03
  - Glow дышит: opacity 0.85 ↔ 1.15

Использование:
  python scripts/gen_avatar_from_banner.py
"""
import math
import os
import subprocess
from pathlib import Path

import cairosvg
from PIL import Image


SIZE = 512
CENTER = SIZE // 2
FPS = 24
DURATION_SEC = 2.5
N_FRAMES = int(FPS * DURATION_SEC)  # 60


def make_svg(frame_idx: int) -> str:
    """Генерирует SVG для одного кадра анимации.

    Дизайн взят из bot/assets/banner-channel.svg без изменений.
    Только перекомпонован под квадрат 512x512.
    """
    t = frame_idx / N_FRAMES  # 0..1

    # Анимация: пульсация капли
    scale = 1.0 + 0.03 * math.sin(t * 2 * math.pi)

    # Размеры (пересчитаны из баннера 800x418 → 512x512)
    # В баннере капля: радиус 62, центр (180, 209)
    # В аватарке: центр сверху, увеличенная
    drop_cx = CENTER          # 256
    drop_cy = 200             # сверху по центру
    drop_r = 100 * scale      # 100px радиус (больше чем в баннере, т.к. аватарка = главный элемент)

    # Glow дышит
    glow_intensity = 0.85 + 0.15 * math.sin(t * 2 * math.pi + 0.5)

    # SVG-шаблон (ДИЗАЙН ИЗ БАННЕРА, не меняем)
    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#1a1a2e"/>
      <stop offset="50%" stop-color="#0a0a0f"/>
      <stop offset="100%" stop-color="#1a1a2e"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.4" r="0.5">
      <stop offset="0%" stop-color="#ff1e3c" stop-opacity="{0.3 * glow_intensity}"/>
      <stop offset="100%" stop-color="#ff1e3c" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="droplet" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.95"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0.85"/>
    </linearGradient>
    <linearGradient id="textGrad" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="100%" stop-color="#fbbf24"/>
    </linearGradient>
  </defs>

  <!-- Фон -->
  <rect width="{SIZE}" height="{SIZE}" fill="url(#bg)"/>
  <rect width="{SIZE}" height="{SIZE}" fill="url(#glow)"/>

  <!-- Декоративные точки (бензин) — в углах -->
  <g fill="#ff1e3c" opacity="0.3">
    <circle cx="60" cy="60" r="3"/>
    <circle cx="100" cy="430" r="2"/>
    <circle cx="420" cy="80" r="3"/>
    <circle cx="460" cy="440" r="2"/>
    <circle cx="40" cy="250" r="1.5"/>
    <circle cx="470" cy="220" r="1.5"/>
  </g>

  <!-- Логотип (капля с буквой Б) — по центру сверху -->
  <g transform="translate({drop_cx} {drop_cy})">
    <path
      d="M 0 -{int(drop_r*1.0)} C -{int(drop_r*0.5)} -{int(drop_r*0.7)}, -{int(drop_r*0.95)} -{int(drop_r*0.25)}, -{int(drop_r*0.95)} {int(drop_r*0.17)} C -{int(drop_r*0.95)} {int(drop_r*0.62)}, -{int(drop_r*0.4)} {int(drop_r*0.95)}, 0 {int(drop_r*0.95)} C {int(drop_r*0.4)} {int(drop_r*0.95)}, {int(drop_r*0.95)} {int(drop_r*0.62)}, {int(drop_r*0.95)} {int(drop_r*0.17)} C {int(drop_r*0.95)} -{int(drop_r*0.25)}, {int(drop_r*0.5)} -{int(drop_r*0.7)}, 0 -{int(drop_r*1.0)} Z"
      fill="url(#droplet)"
    />
    <text
      x="0" y="{int(drop_r*0.35)}"
      text-anchor="middle"
      font-family="Inter, system-ui, sans-serif"
      font-weight="900"
      font-size="{int(drop_r*0.95)}"
      fill="#ff1e3c"
    >Б</text>
  </g>

  <!-- Текст по центру снизу -->
  <text
    x="{CENTER}" y="380"
    text-anchor="middle"
    font-family="Inter, system-ui, sans-serif"
    font-weight="900"
    font-size="64"
    fill="url(#textGrad)"
    letter-spacing="-2"
  >Бензин рядом</text>

  <text
    x="{CENTER}" y="420"
    text-anchor="middle"
    font-family="Inter, system-ui, sans-serif"
    font-weight="500"
    font-size="20"
    fill="#ffffff"
    fill-opacity="0.7"
    letter-spacing="6"
  >АЗС · ЦЕНЫ · ЗАВОЗ</text>

  <text
    x="{CENTER}" y="455"
    text-anchor="middle"
    font-family="Inter, system-ui, sans-serif"
    font-weight="400"
    font-size="16"
    fill="#ffffff"
    fill-opacity="0.5"
  >Карта топлива в реальном времени</text>
</svg>'''
    return svg


def main():
    print(f"=== Генерация аватарки из баннера ===")
    print(f"Размер: {SIZE}x{SIZE}, FPS: {FPS}, кадров: {N_FRAMES}")

    out_dir = Path("bot/assets")
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path("/tmp/avatar_frames_banner")
    tmp_dir.mkdir(exist_ok=True)
    for f in tmp_dir.glob("*.png"):
        f.unlink()

    # === Генерируем кадры через cairosvg ===
    print("Рендер кадров через cairosvg...")
    for i in range(N_FRAMES):
        svg = make_svg(i)
        png_path = tmp_dir / f"frame_{i:03d}.png"
        cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            write_to=str(png_path),
            output_width=SIZE,
            output_height=SIZE,
        )
        print(f"  Кадр {i+1}/{N_FRAMES}", end="\r")
    print()

    # === Сохраняем GIF ===
    print("Сборка GIF...")
    frames = [Image.open(tmp_dir / f"frame_{i:03d}.png").convert("RGB") for i in range(N_FRAMES)]
    gif_path = out_dir / "avatar-channel.gif"
    frames_p = [f.quantize(colors=256, method=Image.Quantize.MEDIANCUT) for f in frames]
    frames_p[0].save(
        gif_path,
        save_all=True,
        append_images=frames_p[1:],
        duration=1000 // FPS,
        loop=0,
        optimize=True,
    )
    print(f"✓ GIF: {gif_path} ({gif_path.stat().st_size / 1024:.1f} KB)")

    # === Конвертируем в WebM (через ffmpeg) ===
    webm_path = out_dir / "avatar-channel.webm"
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", str(tmp_dir / "frame_%03d.png"),
        "-c:v", "libvpx-vp9",
        "-crf", "32",
        "-b:v", "0",
        "-pix_fmt", "yuva420p",
        str(webm_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"✓ WebM: {webm_path} ({webm_path.stat().st_size / 1024:.1f} KB)")
    else:
        print(f"⚠ WebM не создан: {result.stderr[:200]}")

    # === Сохраняем статичный кадр (frame 0) для бота ===
    static_path = out_dir / "avatar-static.png"
    frames[0].save(static_path, optimize=True)
    print(f"✓ Static: {static_path} ({static_path.stat().st_size / 1024:.1f} KB)")

    # === Чистим tmp ===
    for f in tmp_dir.glob("*.png"):
        f.unlink()
    tmp_dir.rmdir()

    print()
    print("Готово!")
    print(f"  - {gif_path}")
    print(f"  - {webm_path}")
    print(f"  - {static_path}")


if __name__ == "__main__":
    main()
