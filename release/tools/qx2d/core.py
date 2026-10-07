"""qx2d.core —— 图像解码、逻辑网格推断、调色板、矩形拟合、栅格化与安全 ASCII 编码。

坐标约定（与两份技能文档一致）：
    左上角 (0,0)，x 向右，y 向下，矩形 = (x, y, width, height)，
    覆盖区间 x <= X < x+width、y <= Y < y+height。
"""

from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Optional, Sequence, Tuple

try:  # Pillow 只用于解码/编码图片，其余算法不依赖它
    from PIL import Image
except Exception:  # pragma: no cover - 运行环境缺少 Pillow 时给出明确错误
    Image = None

RGBA = Tuple[int, int, int, int]
Rect = Tuple[int, int, int, int]

# 安全 ASCII：ASCII 33..126，排除双引号与反斜杠 -> 92 个字符
SAFE_ALPHABET = "".join(chr(c) for c in range(33, 127) if chr(c) not in ('"', "\\"))
RADIX = len(SAFE_ALPHABET) // 2  # 46：低 46 个为终止位，高 46 个为延续位


# --------------------------------------------------------------------------
# 安全 ASCII 变长整数
# --------------------------------------------------------------------------

def encode_uint(value: int) -> str:
    """把一个非负整数编码成安全 ASCII 变长字符串。"""
    if value < 0:
        raise ValueError("encode_uint 只接受非负整数")
    out: List[str] = []
    while True:
        value, digit = divmod(value, RADIX)
        if value:
            out.append(SAFE_ALPHABET[RADIX + digit])
        else:
            out.append(SAFE_ALPHABET[digit])
            return "".join(out)


def encode_uints(values: Sequence[int]) -> str:
    return "".join(encode_uint(int(v)) for v in values)


def zigzag(value: int) -> int:
    return value * 2 if value >= 0 else -value * 2 - 1


def encode_delta(value: int) -> str:
    return encode_uint(zigzag(int(value)))


# --------------------------------------------------------------------------
# 栅格
# --------------------------------------------------------------------------

class Raster:
    """RGBA 栅格。buf 为 width*height*4 的 bytearray。"""

    __slots__ = ("width", "height", "buf")

    def __init__(self, width: int, height: int, buf: Optional[bytearray] = None):
        self.width = int(width)
        self.height = int(height)
        if buf is None:
            self.buf = bytearray(self.width * self.height * 4)
        else:
            if len(buf) != self.width * self.height * 4:
                raise ValueError("缓冲区长度与宽高不符")
            self.buf = bytearray(buf)

    # -- 基本访问 ---------------------------------------------------------
    def get(self, x: int, y: int) -> RGBA:
        i = (y * self.width + x) * 4
        b = self.buf
        return (b[i], b[i + 1], b[i + 2], b[i + 3])

    def set(self, x: int, y: int, rgba: RGBA) -> None:
        i = (y * self.width + x) * 4
        b = self.buf
        b[i] = rgba[0] & 255
        b[i + 1] = rgba[1] & 255
        b[i + 2] = rgba[2] & 255
        b[i + 3] = rgba[3] & 255

    def copy(self) -> "Raster":
        return Raster(self.width, self.height, bytearray(self.buf))

    def crop(self, x0: int, y0: int, w: int, h: int) -> "Raster":
        out = Raster(w, h)
        for y in range(h):
            src = ((y0 + y) * self.width + x0) * 4
            dst = y * w * 4
            out.buf[dst:dst + w * 4] = self.buf[src:src + w * 4]
        return out

    def filled(self, x0: int, y0: int, w: int, h: int, rgba: RGBA) -> None:
        for y in range(y0, y0 + h):
            if y < 0 or y >= self.height:
                continue
            base = y * self.width
            for x in range(x0, x0 + w):
                if 0 <= x < self.width:
                    self.set(x, y, rgba)

    def content_bounds(self) -> Optional[Tuple[int, int, int, int]]:
        """非透明内容的联合边界 (x0,y0,x1,y1)，全部透明时返回 None。"""
        w, h, b = self.width, self.height, self.buf
        x0, y0, x1, y1 = w, h, -1, -1
        for y in range(h):
            row = y * w * 4
            for x in range(w):
                if b[row + x * 4 + 3]:
                    if x < x0:
                        x0 = x
                    if x > x1:
                        x1 = x
                    if y < y0:
                        y0 = y
                    if y > y1:
                        y1 = y
        if x1 < 0:
            return None
        return (x0, y0, x1 + 1, y1 + 1)

    def fingerprint(self) -> str:
        return hashlib.sha256(bytes(self.buf)).hexdigest()

    def to_png(self, path: str) -> None:
        require_pillow()
        img = Image.frombytes("RGBA", (self.width, self.height), bytes(self.buf))
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        img.save(path)


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------

def require_pillow() -> None:
    if Image is None:
        raise RuntimeError(
            "未找到 Pillow，无法解码图片。请先安装：python -m pip install pillow"
        )


def load_raster(path: str) -> Raster:
    require_pillow()
    with Image.open(path) as img:
        rgba = img.convert("RGBA")
        return Raster(rgba.width, rgba.height, bytearray(rgba.tobytes()))


def load_frames(path: str) -> Tuple[List[Raster], List[int], Dict[str, object]]:
    """读取帧序列。

    path 可以是：
      * 目录：目录内的图片按文件名自然排序，每帧 duration 默认 None；
      * 多帧文件（GIF/WebP）：按原始逐帧 duration 读取。
    返回 (frames, durations_ms, meta)。
    """
    require_pillow()
    frames: List[Raster] = []
    durations: List[int] = []

    if os.path.isdir(path):
        names = [
            n for n in os.listdir(path)
            if os.path.splitext(n)[1].lower() in (".png", ".webp", ".gif", ".bmp", ".jpg", ".jpeg")
        ]
        names.sort(key=natural_key)
        if not names:
            raise ValueError("目录里没有可用的图片文件：%s" % path)
        for name in names:
            full = os.path.join(path, name)
            frames.append(load_raster(full))
        durations = [0] * len(frames)
        meta = {"source": os.path.abspath(path), "kind": "directory", "files": names}
        return frames, durations, meta

    with Image.open(path) as img:
        n = getattr(img, "n_frames", 1)
        for i in range(n):
            img.seek(i)
            rgba = img.convert("RGBA")
            frames.append(Raster(rgba.width, rgba.height, bytearray(rgba.tobytes())))
            durations.append(int(img.info.get("duration", 0) or 0))
        meta = {"source": os.path.abspath(path), "kind": "multiframe", "n_frames": n}
    return frames, durations, meta


def natural_key(name: str):
    import re

    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# 逻辑网格推断
# --------------------------------------------------------------------------

def infer_cell(
    raster: Raster,
    max_cell: int = 64,
    min_consistency: float = 0.995,
    max_samples: int = 4096,
) -> Dict[str, object]:
    """推断整数倍放大像素画的原生逻辑网格。

    对每个能整除宽高的候选单元尺寸，抽样检查“单元内是否同色”，
    取一致率达标的最大单元；没有达标候选时返回 1（原始网格）。
    """
    w, h = raster.width, raster.height
    limit = min(int(max_cell), max(1, min(w, h) // 2))
    candidates: List[Dict[str, object]] = []
    best = None

    for c in range(2, limit + 1):
        if w % c or h % c:
            continue
        cw, ch = w // c, h // c
        if cw < 2 or ch < 2:
            continue
        total_cells = cw * ch
        stride = max(1, total_cells // max_samples)
        checked = 0
        uniform_cells = 0
        b = raster.buf
        for cy in range(ch):
            for cx in range(cw):
                if ((cy * cw + cx) % stride) != 0:
                    continue
                x0, y0 = cx * c, cy * c
                i0 = (y0 * w + x0) * 4
                r0, g0, b0, a0 = b[i0], b[i0 + 1], b[i0 + 2], b[i0 + 3]
                uniform = True
                for yy in range(y0, y0 + c):
                    row = yy * w * 4
                    for xx in range(x0, x0 + c):
                        i = row + xx * 4
                        if b[i] != r0 or b[i + 1] != g0 or b[i + 2] != b0 or b[i + 3] != a0:
                            uniform = False
                            break
                    if not uniform:
                        break
                checked += 1
                if uniform:
                    uniform_cells += 1
        if not checked:
            continue
        consistency = uniform_cells / checked
        candidates.append({"cell": c, "consistency": round(consistency, 6)})
        if consistency >= min_consistency:
            if best is None or c > best:
                best = c

    if best is None:
        return {
            "cell": 1,
            "consistency": None,
            "logical_width": w,
            "logical_height": h,
            "evidence": "没有一致率达标的整数倍单元，保留原始网格",
            "candidates": candidates[-8:],
        }
    return {
        "cell": best,
        "consistency": next(c["consistency"] for c in candidates if c["cell"] == best),
        "logical_width": w // best,
        "logical_height": h // best,
        "evidence": "单元内同色率达标的最大整数倍单元",
        "candidates": candidates[-8:],
    }


def to_logical(raster: Raster, cell: int) -> Raster:
    """按 cell×cell 单元取众数色，得到逻辑点阵。"""
    if cell <= 1:
        return raster.copy()
    w, h = raster.width, raster.height
    cw, ch = w // cell, h // cell
    out = Raster(cw, ch)
    b = raster.buf
    for cy in range(ch):
        for cx in range(cw):
            counts: Dict[RGBA, int] = {}
            for yy in range(cy * cell, cy * cell + cell):
                row = yy * w * 4
                for xx in range(cx * cell, cx * cell + cell):
                    i = row + xx * 4
                    key = (b[i], b[i + 1], b[i + 2], b[i + 3])
                    counts[key] = counts.get(key, 0) + 1
            # 众数；并列时取数值最小者，保证确定性
            best = max(sorted(counts.items(), key=lambda kv: kv[0]), key=lambda kv: kv[1])[0]
            out.set(cx, cy, best)
    return out


def upscale(raster: Raster, cell: int) -> Raster:
    if cell <= 1:
        return raster.copy()
    out = Raster(raster.width * cell, raster.height * cell)
    for y in range(raster.height):
        for x in range(raster.width):
            out.filled(x * cell, y * cell, cell, cell, raster.get(x, y))
    return out


# --------------------------------------------------------------------------
# 调色板
# --------------------------------------------------------------------------

def color_counts(raster: Raster) -> Dict[RGBA, int]:
    counts: Dict[RGBA, int] = {}
    b = raster.buf
    for i in range(0, len(b), 4):
        key = (b[i], b[i + 1], b[i + 2], b[i + 3])
        counts[key] = counts.get(key, 0) + 1
    return counts


def normalize_transparent(raster: Raster) -> Raster:
    """把完全透明的像素统一成 (0,0,0,0)。

    全透明像素不携带任何可见信息，但 RGB 通道常带噪声（GIF/WebP 尤其明显）。
    归一化后：调色板不会为不可见颜色分配控件，逐像素比较也不会被这些噪声干扰。
    """
    out = raster.copy()
    b = out.buf
    for i in range(0, len(b), 4):
        if b[i + 3] == 0:
            b[i] = 0
            b[i + 1] = 0
            b[i + 2] = 0
    return out


def apply_alpha_threshold(raster: Raster, threshold: int) -> Raster:
    """alpha <= threshold 的像素按透明处理（RGB 一并归一化）。

    threshold=0 等价于只处理完全透明的像素；抠图残留的半透明杂边可以调高这个值去掉。
    """
    if threshold <= 0:
        return normalize_transparent(raster)
    out = raster.copy()
    b = out.buf
    for i in range(0, len(b), 4):
        if b[i + 3] <= threshold:
            b[i] = 0
            b[i + 1] = 0
            b[i + 2] = 0
            b[i + 3] = 0
    return out


def drop_transparent(palette: Sequence[RGBA], idx: Sequence[int]) -> Tuple[List[RGBA], List[int]]:
    """去掉完全透明的调色板项；对应像素的索引置为 -1（不会被任何颜色匹配到）。"""
    keep = [i for i, c in enumerate(palette) if c[3] != 0]
    remap = {old: new for new, old in enumerate(keep)}
    new_palette = [palette[i] for i in keep]
    new_idx = [remap[v] if v in remap else -1 for v in idx]
    return new_palette, new_idx


def palette_from_counts(
    counts: Dict[RGBA, int],
    tau: int = 0,
    max_colors: int = 0,
) -> Dict[str, object]:
    """从颜色统计建立调色板与映射表（供单图与多帧共用）。

    tau > 0 时按技能文档 3.3：按使用次数从高到低选代表色，每个源颜色直接与
    最终代表色比较，保证逐通道误差不超过 tau；alpha 必须一致。
    max_colors > 0 时在 tau 合并之后再按最近代表色做有损量化。
    """
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    reps: List[RGBA] = []
    mapping: Dict[RGBA, RGBA] = {}

    for color, _count in ordered:
        target = None
        for rep in reps:
            if rep[3] != color[3]:
                continue
            if max(abs(rep[0] - color[0]), abs(rep[1] - color[1]), abs(rep[2] - color[2])) <= tau:
                target = rep
                break
        if target is None:
            reps.append(color)
            mapping[color] = color
        else:
            mapping[color] = target

    lossy = False
    freq: Dict[RGBA, int] = {rep: 0 for rep in reps}
    for color, cnt in ordered:
        freq[mapping[color]] = freq.get(mapping[color], 0) + cnt

    if max_colors and len(reps) > max_colors:
        keep = sorted(reps, key=lambda r: (-freq.get(r, 0), r))[:max_colors]
        keep_set = set(keep)
        for color in list(mapping.keys()):
            if mapping[color] not in keep_set:
                mapping[color] = min(
                    keep,
                    key=lambda r: (
                        0 if r[3] == color[3] else 1,
                        max(abs(r[0] - color[0]), abs(r[1] - color[1]), abs(r[2] - color[2])),
                        (r[0] - color[0]) ** 2 + (r[1] - color[1]) ** 2 + (r[2] - color[2]) ** 2,
                        r,
                    ),
                )
        reps = keep
        freq = {rep: 0 for rep in reps}
        for color, cnt in ordered:
            freq[mapping[color]] = freq.get(mapping[color], 0) + cnt
        lossy = True

    reps = sorted(reps, key=lambda r: (-freq.get(r, 0), r))
    return {
        "palette": reps,
        "mapping": mapping,
        "lossy": lossy,
        "max_colors": max_colors,
        "tau": tau,
        "source_colors": len(counts),
        "merged_colors": sum(1 for k, v in mapping.items() if k != v),
    }


def index_of_palette(raster: Raster, mapping: Dict[RGBA, RGBA], reps: Sequence[RGBA]) -> List[int]:
    index_of = {rep: i for i, rep in enumerate(reps)}
    w, h, b = raster.width, raster.height, raster.buf
    idx = [0] * (w * h)
    for p in range(w * h):
        i = p * 4
        idx[p] = index_of[mapping[(b[i], b[i + 1], b[i + 2], b[i + 3])]]
    return idx


def build_palette(
    raster: Raster,
    tau: int = 0,
    max_colors: int = 0,
) -> Dict[str, object]:
    """单张点阵的调色板结果（含每像素索引与 T -> Q 误差）。"""
    info = palette_from_counts(color_counts(raster), tau=tau, max_colors=max_colors)
    reps = info["palette"]  # type: ignore[assignment]
    idx = index_of_palette(raster, info["mapping"], reps)  # type: ignore[arg-type]
    out = dict(info)
    out["index"] = idx
    out["color_error"] = compare_by_palette(raster, idx, reps)  # type: ignore[arg-type]
    return out


def raster_from_index(idx: Sequence[int], palette: Sequence[RGBA], w: int, h: int) -> Raster:
    out = Raster(w, h)
    for p in range(w * h):
        out.set(p % w, p // w, palette[idx[p]])
    return out


def compare_by_palette(raster: Raster, idx: Sequence[int], palette: Sequence[RGBA]) -> Dict[str, float]:
    """参考点阵 vs 调色板映射结果（对应技能文档的 T -> Q）。"""
    w, h, b = raster.width, raster.height, raster.buf
    diff = 0
    max_err = 0
    total = 0.0
    sq = 0.0
    for p in range(w * h):
        i = p * 4
        src = (b[i], b[i + 1], b[i + 2], b[i + 3])
        dst = palette[idx[p]]
        e = max(abs(src[0] - dst[0]), abs(src[1] - dst[1]), abs(src[2] - dst[2]), abs(src[3] - dst[3]))
        if e:
            diff += 1
            if e > max_err:
                max_err = e
        total += (abs(src[0] - dst[0]) + abs(src[1] - dst[1]) + abs(src[2] - dst[2]) + abs(src[3] - dst[3])) / 4.0
        sq += ((src[0] - dst[0]) ** 2 + (src[1] - dst[1]) ** 2 + (src[2] - dst[2]) ** 2 + (src[3] - dst[3]) ** 2) / 4.0
    n = max(1, w * h)
    return {
        "different_pixels": diff,
        "maximum_channel_error": max_err,
        "mae": round(total / n, 6),
        "rmse": round((sq / n) ** 0.5, 6),
    }


# --------------------------------------------------------------------------
# 矩形拟合
# --------------------------------------------------------------------------

def greedy_max_rects(mask: bytearray, w: int, h: int) -> List[Rect]:
    """贪心最大矩形分解：每个未访问像素先向右扩展，再整行向下扩展。"""
    visited = bytearray(w * h)
    rects: List[Rect] = []
    for y in range(h):
        base = y * w
        x = 0
        while x < w:
            i = base + x
            if mask[i] and not visited[i]:
                x2 = x
                while x2 + 1 < w and mask[base + x2 + 1] and not visited[base + x2 + 1]:
                    x2 += 1
                rw = x2 - x + 1
                y2 = y
                while y2 + 1 < h:
                    nbase = (y2 + 1) * w
                    ok = True
                    for xx in range(x, x + rw):
                        if not mask[nbase + xx] or visited[nbase + xx]:
                            ok = False
                            break
                    if not ok:
                        break
                    y2 += 1
                rh = y2 - y + 1
                for yy in range(y, y + rh):
                    vb = yy * w
                    for xx in range(x, x + rw):
                        visited[vb + xx] = 1
                rects.append((x, y, rw, rh))
            x += 1
    return rects


def row_run_rects(mask: bytearray, w: int, h: int) -> List[Rect]:
    """行段 + 相邻同起点同宽行段垂直合并。"""
    rects: List[Rect] = []
    prev: Dict[Tuple[int, int], int] = {}
    for y in range(h):
        base = y * w
        runs: List[Tuple[int, int]] = []
        x = 0
        while x < w:
            if mask[base + x]:
                x0 = x
                while x < w and mask[base + x]:
                    x += 1
                runs.append((x0, x - x0))
            else:
                x += 1
        cur: Dict[Tuple[int, int], int] = {}
        for (x0, rw) in runs:
            key = (x0, rw)
            if key in prev:
                i = prev[key]
                rx, ry, rww, rh = rects[i]
                rects[i] = (rx, ry, rww, rh + 1)
                cur[key] = i
            else:
                rects.append((x0, y, rw, 1))
                cur[key] = len(rects) - 1
        prev = cur
    return rects


def merge_rects(rects: List[Rect], rounds: int = 8) -> List[Rect]:
    """反复做左右/上下相邻合并，直到一轮没有新合并。"""
    work = [r for r in rects if r[2] > 0 and r[3] > 0]
    for _ in range(rounds):
        changed = False
        # 左右合并：同 y、同高、x 相接
        groups: Dict[Tuple[int, int], List[int]] = {}
        for i, (_x, y, _wd, ht) in enumerate(work):
            if _wd > 0:
                groups.setdefault((y, ht), []).append(i)
        for _key, ids in groups.items():
            ids.sort(key=lambda i: work[i][0])
            for a, b in zip(ids, ids[1:]):
                ra, rb = work[a], work[b]
                if ra[2] > 0 and rb[2] > 0 and ra[0] + ra[2] == rb[0] and ra[1] == rb[1] and ra[3] == rb[3]:
                    work[a] = (ra[0], ra[1], ra[2] + rb[2], ra[3])
                    work[b] = (0, 0, -1, -1)
                    changed = True
        # 上下合并：同 x、同宽、y 相接
        groups = {}
        for i, (x, _y, wd, _ht) in enumerate(work):
            if wd > 0:
                groups.setdefault((x, wd), []).append(i)
        for _key, ids in groups.items():
            ids.sort(key=lambda i: work[i][1])
            for a, b in zip(ids, ids[1:]):
                ra, rb = work[a], work[b]
                if ra[2] > 0 and rb[2] > 0 and ra[1] + ra[3] == rb[1] and ra[0] == rb[0] and ra[2] == rb[2]:
                    work[a] = (ra[0], ra[1], ra[2], ra[3] + rb[3])
                    work[b] = (0, 0, -1, -1)
                    changed = True
        work = [r for r in work if r[2] > 0]
        if not changed:
            break
    return work


def mask_to_rects(
    mask: bytearray,
    w: int,
    h: int,
    big_pixels: int = 300_000,
    strategy: str = "both",
) -> List[Rect]:
    """按策略做矩形分解，并保证覆盖精确。

    strategy:
      greedy —— 只用贪心最大矩形（通常记录更少，大图较慢）
      runs   —— 只用行段 + 垂直合并（快）
      both   —— 两种都跑，取记录数少的（默认）
    """
    candidates: List[List[Rect]] = []
    if strategy in ("greedy", "both") and w * h <= big_pixels:
        candidates.append(greedy_max_rects(mask, w, h))
    if strategy in ("runs", "both") or not candidates:
        candidates.append(row_run_rects(mask, w, h))
    best: Optional[List[Rect]] = None
    for cand in candidates:
        merged = merge_rects(cand)
        if best is None or len(merged) < len(best):
            best = merged
    assert best is not None
    return best


def rects_by_color(
    idx: Sequence[int],
    w: int,
    h: int,
    palette_size: int,
    strategy: str = "both",
) -> List[Tuple[int, Rect]]:
    """按颜色逐个做 mask 拟合，返回 (colorIndex, rect) 列表。"""
    out: List[Tuple[int, Rect]] = []
    n = w * h
    for c in range(palette_size):
        mask = bytearray(n)
        hit = False
        for p in range(n):
            if idx[p] == c:
                mask[p] = 1
                hit = True
        if not hit:
            continue
        for r in mask_to_rects(mask, w, h, strategy=strategy):
            out.append((c, r))
    return out


def records_from_rects(rects: Sequence[Tuple[int, Rect]], kind: int = 0) -> List[Dict[str, int]]:
    """转成规范化记录；colorIndex 采用 Lua 习惯的 1 基准；painter order = 大面积先画，再按 y、x。"""
    recs = []
    for color_index, (x, y, w, h) in rects:
        recs.append(
            {
                "kind": kind,
                "x": int(x),
                "y": int(y),
                "width": int(w),
                "height": int(h),
                "angle": 0,
                "colorIndex": int(color_index) + 1,
            }
        )
    recs.sort(key=lambda r: (-(r["width"] * r["height"]), r["y"], r["x"], r["colorIndex"]))
    return recs


def rasterize_records(
    records: Sequence[Dict[str, int]],
    palette: Sequence[RGBA],
    w: int,
    h: int,
) -> Raster:
    """按 painter order 把记录铺回栅格（不透明覆盖）。"""
    out = Raster(w, h)
    for rec in records:
        color = palette[rec["colorIndex"] - 1]
        if color[3] == 0:
            continue
        x0, y0 = rec["x"], rec["y"]
        out.filled(x0, y0, rec["width"], rec["height"], color)
    return out


def unit_stats(records: Sequence[Dict[str, int]]) -> Dict[str, int]:
    unit = sum(1 for r in records if r["width"] == 1 and r["height"] == 1)
    covered = sum(r["width"] * r["height"] for r in records)
    return {
        "records": len(records),
        "unit_controls": unit,
        "scaled_controls": len(records) - unit,
        "covered_pixels": covered,
        "saved_vs_pixel_controls": covered - len(records),
    }


# --------------------------------------------------------------------------
# 尺寸字典
# --------------------------------------------------------------------------

def build_size_dict(sizes: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """(width,height) 去重并按出现次数从多到少排序，高频项索引更小。"""
    counts: Dict[Tuple[int, int], int] = {}
    for s in sizes:
        counts[(int(s[0]), int(s[1]))] = counts.get((int(s[0]), int(s[1])), 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    return [k for k, _ in ordered]
