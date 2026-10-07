"""生成测试用的像素画与帧序列（确定性，可重复运行）。"""

from __future__ import annotations

import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")


def make_pixel_art() -> str:
    """16x16 逻辑像素 → 8 倍放大 128x128，用于测试逻辑网格推断。"""
    size, scale = 16, 8
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    outline = (38, 34, 62, 255)
    body = (198, 78, 84, 255)
    light = (240, 152, 148, 255)
    eye = (250, 250, 250, 255)
    for y in range(size):
        for x in range(size):
            d = (x - 7.5) ** 2 + (y - 7.5) ** 2
            if d <= 26:
                px[x, y] = light if (x + y) < 12 else body
            elif d <= 42:
                px[x, y] = outline
    for (ex, ey) in ((5, 6), (10, 6)):
        px[ex, ey] = eye
        px[ex, ey + 1] = eye
    px[7, 11] = outline
    px[8, 11] = outline
    big = img.resize((size * scale, size * scale), Image.NEAREST)
    os.makedirs(FIXTURES, exist_ok=True)
    path = os.path.join(FIXTURES, "ref_pixel.png")
    big.save(path)
    return path


def make_big_pixel_art() -> str:
    """32x32 逻辑像素（逐像素确定性 LCG 取色）→ 4 倍放大 128x128。

    每个逻辑像素颜色独立，记录数多到足以体现压缩收益：
    未压缩 DRAWS 表很长，压缩流 + 解码器明显更短。
    """
    size, scale = 32, 4
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    palette = [
        (230, 60, 60, 255),
        (60, 200, 120, 255),
        (70, 120, 240, 255),
        (240, 210, 80, 255),
        (190, 90, 220, 255),
        (40, 44, 60, 255),
    ]
    seed = 12345

    def rnd() -> int:
        nonlocal seed
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        return seed

    for y in range(size):
        for x in range(size):
            px[x, y] = palette[rnd() % len(palette)]
    os.makedirs(FIXTURES, exist_ok=True)
    path = os.path.join(FIXTURES, "ref_big.png")
    img.resize((size * scale, size * scale), Image.NEAREST).save(path)
    return path


def logical_frames() -> list:
    """8 帧 16x16 逻辑画面：静态背景条 + 右移方块 + 静态星 + 变色方块。"""
    size = 16
    frames = []
    for f in range(8):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        px = img.load()
        bar = (58, 60, 92, 255)
        for y in range(13, 16):
            for x in range(size):
                px[x, y] = bar
        mover = (242, 200, 62, 255)
        for y in range(8, 12):
            for x in range(2 + f, 6 + f):
                px[x, y] = mover
        star = (250, 250, 250, 255)
        for y in range(2, 5):
            for x in range(12, 15):
                px[x, y] = star
        cycle = [(80, 190, 255, 255), (255, 120, 200, 255)][(f // 2) % 2]
        for y in range(2, 4):
            for x in range(2, 4):
                px[x, y] = cycle
        frames.append(img)
    return frames


def make_frames() -> tuple:
    scale = 6
    os.makedirs(FIXTURES, exist_ok=True)
    out_dir = os.path.join(FIXTURES, "frames")
    os.makedirs(out_dir, exist_ok=True)
    frames = logical_frames()
    for i, img in enumerate(frames, start=1):
        img.resize((16 * scale, 16 * scale), Image.NEAREST).save(
            os.path.join(out_dir, "frame_%02d.png" % i)
        )
    gif_frames = []
    for img in frames:
        flat = Image.new("RGBA", img.size, (24, 24, 36, 255))
        flat.alpha_composite(img)
        gif_frames.append(flat.convert("RGB").resize((16 * scale, 16 * scale), Image.NEAREST))
    gif_path = os.path.join(FIXTURES, "anim.gif")
    gif_frames[0].save(
        gif_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=80,
        loop=0,
        optimize=False,
    )
    return out_dir, gif_path


def make_photo() -> str:
    """96x96 的"照片感"素材：柔和径向渐变 + 实心圆 + 斜带，用于图元拟合测试。"""
    from PIL import Image, ImageDraw

    size = 96
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    for y in range(size):
        for x in range(size):
            d = ((x - 48) ** 2 + (y - 40) ** 2) ** 0.5
            if d < 44:
                t = max(0.0, 1.0 - d / 44)
                r = int(40 + 180 * t)
                g = int(90 + 120 * t)
                b = int(200 - 60 * t)
                px[x, y] = (min(255, r), min(255, g), min(255, b), 255)
    draw = ImageDraw.Draw(img)
    draw.ellipse((28, 20, 68, 60), fill=(250, 210, 80, 255))
    draw.polygon([(10, 86), (86, 66), (86, 90), (10, 90)], fill=(200, 60, 90, 255))
    os.makedirs(FIXTURES, exist_ok=True)
    path = os.path.join(FIXTURES, "photo.png")
    img.save(path)
    return path


def make_keyframes() -> str:
    """关键帧文档：位移 + 旋转 + 淡入 + 显隐 + 事件，用于动画编辑测试。"""
    doc = {
        "schema": "qx2d.keyframes",
        "fps": 12,
        "duration_ms": 1200,
        "loop": True,
        "canvas": {"width": 320, "height": 180},
        "palette": [[240, 200, 80, 255], [90, 160, 240, 255], [250, 250, 250, 255]],
        "targets": [
            {"name": "panel", "x": 60, "y": 90, "width": 80, "height": 40, "colorIndex": 1},
            {"name": "badge", "x": 260, "y": 40, "width": 28, "height": 28, "colorIndex": 2, "visible": False},
        ],
        "tracks": [
            {"target": "panel", "field": "x",
             "keys": [{"time": 0, "value": 60, "ease": "OutCubic"}, {"time": 1200, "value": 260}]},
            {"target": "panel", "field": "rotation",
             "keys": [{"time": 0, "value": 0}, {"time": 1200, "value": 30, "ease": "InOutQuad"}]},
            {"target": "panel", "field": "opacity",
             "keys": [{"time": 0, "value": 0}, {"time": 300, "value": 1, "ease": "OutSine"}]},
            {"target": "badge", "field": "visible",
             "keys": [{"time": 0, "value": False}, {"time": 600, "value": True, "interpolation": "step"}]},
            {"target": "badge", "field": "y",
             "keys": [{"time": 600, "value": 40}, {"time": 1200, "value": 140, "ease": "OutBounce"}]},
        ],
        "events": [{"time": 600, "name": "BadgeShown", "target": "badge", "params": "hello"}],
    }
    import json

    os.makedirs(FIXTURES, exist_ok=True)
    path = os.path.join(FIXTURES, "keyframes.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return path


def make_duplicate_frames() -> str:
    """3 帧里前两帧完全相同，用于测试 --merge-identical。"""
    import shutil

    out_dir = os.path.join(FIXTURES, "frames_dup")
    os.makedirs(out_dir, exist_ok=True)
    src = os.path.join(FIXTURES, "frames")
    shutil.copyfile(os.path.join(src, "frame_01.png"), os.path.join(out_dir, "frame_01.png"))
    shutil.copyfile(os.path.join(src, "frame_01.png"), os.path.join(out_dir, "frame_02.png"))
    shutil.copyfile(os.path.join(src, "frame_02.png"), os.path.join(out_dir, "frame_03.png"))
    return out_dir


def main() -> int:
    ref = make_pixel_art()
    big = make_big_pixel_art()
    frames_dir, gif = make_frames()
    dup = make_duplicate_frames()
    photo = make_photo()
    keys = make_keyframes()
    print("fixture: %s" % ref)
    print("fixture: %s" % big)
    print("fixture: %s" % frames_dir)
    print("fixture: %s" % gif)
    print("fixture: %s" % dup)
    print("fixture: %s" % photo)
    print("fixture: %s" % keys)
    return 0


if __name__ == "__main__":
    sys.exit(main())
