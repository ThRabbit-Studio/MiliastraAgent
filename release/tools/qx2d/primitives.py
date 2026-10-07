"""qx2d.primitives —— 抗锯齿图元光栅化与迭代优化拟合。

图元类型（与数据契约里的 kind 对应）：
    0 = 矩形（可旋转）
    1 = 椭圆
    2 = 三角形（等腰，质心为轴心）

拟合方式是迭代随机优化：先按当前误差采样焦点，再在焦点附近生成候选图元，
对候选求加权最小二乘最优颜色，按"误差下降量 − 溢出惩罚"打分，最后做爬山微调。

颜色与误差一律在 0..1 归一化空间里计算，避免 8bit 量化放大误差。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

KIND_RECT = 0
KIND_ELLIPSE = 1
KIND_TRIANGLE = 2

KIND_BY_NAME = {"rect": KIND_RECT, "ellipse": KIND_ELLIPSE, "triangle": KIND_TRIANGLE}
NAME_BY_KIND = {v: k for k, v in KIND_BY_NAME.items()}


# --------------------------------------------------------------------------
# 光栅化（覆盖度 0..1）
# --------------------------------------------------------------------------

def _local_coords(
    xs: np.ndarray, ys: np.ndarray, cx: float, cy: float, rotation_deg: float
) -> Tuple[np.ndarray, np.ndarray]:
    theta = math.radians(rotation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    dx = xs - cx
    dy = ys - cy
    return dx * cos_t + dy * sin_t, -dx * sin_t + dy * cos_t


def _triangle_coverage(u: np.ndarray, v: np.ndarray, hw: float, hh: float) -> np.ndarray:
    """等腰三角形：顶点 (0,-hh)、左下 (-hw,hh)、右下 (hw,hh)。"""
    ax, ay = 0.0, -hh
    bx, by = -hw, hh
    cx, cy = hw, hh

    def edge(px: float, py: float, qx: float, qy: float) -> np.ndarray:
        return (qx - px) * (v - py) - (qy - py) * (u - px)

    e1 = edge(bx, by, cx, cy)
    e2 = edge(cx, cy, ax, ay)
    e3 = edge(ax, ay, bx, by)
    # 统一成"内部为正"
    sign = 1.0 if ((cx - bx) * (ay - by) - (cy - by) * (ax - bx)) > 0 else -1.0
    l1 = math.hypot(cx - bx, cy - by)
    l2 = math.hypot(ax - cx, ay - cy)
    l3 = math.hypot(bx - ax, by - ay)
    d1 = sign * e1 / max(l1, 1e-6)
    d2 = sign * e2 / max(l2, 1e-6)
    d3 = sign * e3 / max(l3, 1e-6)
    return np.clip(d1 + 0.5, 0.0, 1.0) * np.clip(d2 + 0.5, 0.0, 1.0) * np.clip(d3 + 0.5, 0.0, 1.0)


def coverage_map(
    kind: int,
    xs: np.ndarray,
    ys: np.ndarray,
    cx: float,
    cy: float,
    hw: float,
    hh: float,
    rotation_deg: float,
) -> np.ndarray:
    """给定像素坐标网格，返回该图元的抗锯齿覆盖度（0..1）。"""
    u, v = _local_coords(xs, ys, cx, cy, rotation_deg)
    if kind == KIND_RECT:
        cu = np.clip(hw - np.abs(u) + 0.5, 0.0, 1.0)
        cv = np.clip(hh - np.abs(v) + 0.5, 0.0, 1.0)
        return cu * cv
    if kind == KIND_ELLIPSE:
        d = np.sqrt((u / max(hw, 1e-6)) ** 2 + (v / max(hh, 1e-6)) ** 2)
        edge = max(1.0, min(hw, hh))
        return np.clip((1.0 - d) * edge + 0.5, 0.0, 1.0)
    return _triangle_coverage(u, v, hw, hh)


def bounds(kind: int, cx: float, cy: float, hw: float, hh: float, rotation_deg: float) -> Tuple[int, int, int, int]:
    """图元的轴对齐包围盒（画布像素，含抗锯齿外扩 1 像素）。"""
    theta = math.radians(rotation_deg)
    cos_t, sin_t = abs(math.cos(theta)), abs(math.sin(theta))
    if kind == KIND_TRIANGLE:
        ex = hw * cos_t + hh * sin_t
        ey = hw * sin_t + hh * cos_t
    else:
        ex = hw * cos_t + hh * sin_t
        ey = hw * sin_t + hh * cos_t
    x0 = int(math.floor(cx - ex)) - 1
    y0 = int(math.floor(cy - ey)) - 1
    x1 = int(math.ceil(cx + ex)) + 1
    y1 = int(math.ceil(cy + ey)) + 1
    return x0, y0, x1, y1


# --------------------------------------------------------------------------
# 拟合器
# --------------------------------------------------------------------------

class FitParams:
    """拟合参数（全部有默认值，命令行可覆盖）。"""

    def __init__(
        self,
        num_shapes: int = 200,
        candidates: int = 16,
        climb_iterations: int = 32,
        allowed_kinds: Sequence[int] = (KIND_RECT, KIND_ELLIPSE, KIND_TRIANGLE),
        min_size: float = 0.0,
        max_size: float = 0.0,
        allow_rotation: bool = True,
        spill_penalty: float = 4.0,
        alpha_max: float = 1.0,
        alpha_min: float = 0.15,
        seed: int = 12345,
        scale: float = 1.0,
    ):
        self.num_shapes = int(num_shapes)
        self.candidates = int(candidates)
        self.climb_iterations = int(climb_iterations)
        self.allowed_kinds = tuple(int(k) for k in allowed_kinds)
        self.min_size = float(min_size)
        self.max_size = float(max_size)
        self.allow_rotation = bool(allow_rotation)
        self.spill_penalty = float(spill_penalty)
        self.alpha_max = float(alpha_max)
        self.alpha_min = float(alpha_min)
        self.seed = int(seed)
        self.scale = float(scale)

    def as_dict(self) -> Dict[str, object]:
        return {
            "num_shapes": self.num_shapes,
            "candidates": self.candidates,
            "climb_iterations": self.climb_iterations,
            "allowed_kinds": [NAME_BY_KIND[k] for k in self.allowed_kinds],
            "min_size": self.min_size,
            "max_size": self.max_size,
            "allow_rotation": self.allow_rotation,
            "spill_penalty": self.spill_penalty,
            "alpha_range": [self.alpha_min, self.alpha_max],
            "seed": self.seed,
        }


class Fitter:
    """把目标图像用图元迭代拟合出来。

    target: (H, W, 3) float32，0..1
    weight: (H, W) float32，0..1（alpha/255 或二值蒙版）
    """

    def __init__(self, target: np.ndarray, weight: np.ndarray, params: FitParams):
        self.target = target.astype(np.float32)
        self.weight = weight.astype(np.float32)
        self.h, self.w = weight.shape
        self.params = params
        self.canvas = np.zeros_like(self.target)
        self.rng = np.random.default_rng(params.seed)
        base = min(self.w, self.h)
        self.min_half = params.min_size / 2.0 if params.min_size > 0 else max(1.0, base * 0.02)
        self.max_half = params.max_size / 2.0 if params.max_size > 0 else max(self.min_half + 1.0, base * 0.22)
        self.hard_mask = (self.weight > 0.0).astype(np.float32)
        self.shapes: List[Dict[str, float]] = []

    # -- 误差 -------------------------------------------------------------
    def error_map(self) -> np.ndarray:
        diff = self.canvas - self.target
        return (diff * diff).sum(axis=2) * self.weight

    def quality(self) -> Dict[str, float]:
        mask = self.weight > 0
        if not np.any(mask):
            return {"mae": 0.0, "rmse": 0.0, "psnr": 99.0, "different_pixels": 0}
        diff = np.abs(self.canvas - self.target)[mask]
        mae = float(diff.mean())
        mse = float((diff ** 2).mean())
        psnr = 99.0 if mse <= 1e-12 else float(10.0 * math.log10(1.0 / mse))
        differing = np.any(np.abs(self.canvas - self.target) > (1.0 / 255.0), axis=2) & mask
        return {
            "mae": round(mae, 6),
            "rmse": round(math.sqrt(mse), 6),
            "psnr": round(psnr, 3),
            "different_pixels": int(differing.sum()),
        }

    # -- 候选评估 ---------------------------------------------------------
    def _region(self, kind: int, cx: float, cy: float, hw: float, hh: float, rot: float):
        x0, y0, x1, y1 = bounds(kind, cx, cy, hw, hh, rot)
        x0 = max(0, min(self.w - 1, x0))
        y0 = max(0, min(self.h - 1, y0))
        x1 = max(x0 + 1, min(self.w, x1))
        y1 = max(y0 + 1, min(self.h, y1))
        xs = np.arange(x0, x1, dtype=np.float32) + 0.5
        ys = np.arange(y0, y1, dtype=np.float32) + 0.5
        gx, gy = np.meshgrid(xs, ys)
        return (x0, y0, x1, y1), gx, gy

    def evaluate(
        self,
        kind: int,
        cx: float,
        cy: float,
        hw: float,
        hh: float,
        rot: float,
        opacity: float,
    ) -> Tuple[float, np.ndarray, np.ndarray]:
        """返回 (delta, 颜色 RGB 0..1, 覆盖度)。delta 为归一化平方误差变化量。"""
        (x0, y0, x1, y1), gx, gy = self._region(kind, cx, cy, hw, hh, rot)
        cov = coverage_map(kind, gx, gy, cx, cy, hw, hh, rot)
        if not np.any(cov > 0.0):
            return float("inf"), np.zeros(3, dtype=np.float32), cov
        w = self.weight[y0:y1, x0:x1]
        v = self.canvas[y0:y1, x0:x1]
        t = self.target[y0:y1, x0:x1]
        a = (cov * opacity).astype(np.float32)
        wa = w * a
        denom = float((wa * a).sum())
        if denom <= 1e-9:
            return float("inf"), np.zeros(3, dtype=np.float32), cov
        # 加权最小二乘：min Σ w (v(1-a) + C a - t)²
        numer = ((wa[..., None]) * (t - v * (1.0 - a)[..., None])).sum(axis=(0, 1))
        color = np.clip(numer / denom, 0.0, 1.0).astype(np.float32)
        rendered = v * (1.0 - a)[..., None] + color[None, None, :] * a[..., None]
        diff_new = rendered - t
        diff_old = v - t
        delta = float(((diff_new * diff_new - diff_old * diff_old).sum(axis=2) * w).sum())
        # 溢出惩罚：覆盖到蒙版以外（或低权重）区域的面积
        spill = float((cov * (1.0 - self.hard_mask[y0:y1, x0:x1])).sum())
        delta += self.params.spill_penalty * spill / max(1.0, cov.size)
        return delta, color, cov

    def _commit(self, shape: Dict[str, float]) -> None:
        (x0, y0, x1, y1), gx, gy = self._region(
            int(shape["kind"]), shape["cx"], shape["cy"], shape["hw"], shape["hh"], shape["rotation"]
        )
        cov = coverage_map(
            int(shape["kind"]), gx, gy, shape["cx"], shape["cy"], shape["hw"], shape["hh"], shape["rotation"]
        )
        a = (cov * shape["opacity"]).astype(np.float32)
        color = np.array(shape["color"], dtype=np.float32)
        v = self.canvas[y0:y1, x0:x1]
        self.canvas[y0:y1, x0:x1] = v * (1.0 - a)[..., None] + color[None, None, :] * a[..., None]
        self.shapes.append(shape)

    # -- 主循环 -----------------------------------------------------------
    def _random_shape(self, cx: float, cy: float, scale_hint: float) -> Dict[str, float]:
        p = self.params
        kind = int(self.rng.choice(p.allowed_kinds))
        base = max(self.min_half, min(self.max_half, scale_hint))
        size = float(np.exp(self.rng.uniform(math.log(max(self.min_half, 1e-3)), math.log(max(base, self.min_half + 1e-3)))))
        hw = size
        hh = size if p.allow_rotation else size
        if self.rng.random() < 0.4:
            hw = float(np.clip(size * self.rng.uniform(0.5, 2.0), self.min_half, self.max_half))
        if self.rng.random() < 0.4:
            hh = float(np.clip(size * self.rng.uniform(0.5, 2.0), self.min_half, self.max_half))
        if kind == KIND_TRIANGLE:
            hh = float(np.clip(hh * 1.2, self.min_half, self.max_half * 1.5))
        rotation = float(self.rng.uniform(0.0, 180.0)) if p.allow_rotation else 0.0
        opacity = float(self.rng.uniform(p.alpha_min, p.alpha_max))
        return {
            "kind": float(kind),
            "cx": float(cx),
            "cy": float(cy),
            "hw": hw,
            "hh": hh,
            "rotation": rotation,
            "opacity": opacity,
            "color": [0.0, 0.0, 0.0],
        }

    def fit(self, progress=None) -> List[Dict[str, float]]:
        p = self.params
        for step in range(p.num_shapes):
            err = self.error_map()
            total = float(err.sum())
            if total <= 1e-9:
                break
            flat = err.ravel()
            probs = flat / total
            idx = int(self.rng.choice(flat.size, p=probs))
            cy0, cx0 = divmod(idx, self.w)
            # 焦点附近的尺度提示：按该点误差强度（0..3）映射到尺寸
            local = float(err[cy0, cx0])
            mean_err = total / max(1, int((self.weight > 0).sum()))
            ratio = min(3.0, (local / mean_err) if mean_err > 1e-9 else 1.0)
            scale_hint = self.min_half + (self.max_half - self.min_half) * (ratio / 3.0)

            best = None
            for _ in range(p.candidates):
                cand = self._random_shape(cx0 + 0.5, cy0 + 0.5, scale_hint)
                delta, color, _cov = self.evaluate(
                    int(cand["kind"]), cand["cx"], cand["cy"], cand["hw"], cand["hh"], cand["rotation"], cand["opacity"]
                )
                if not math.isfinite(delta):
                    continue
                cand["color"] = [float(c) for c in color]
                cand["delta"] = delta
                if best is None or delta < best["delta"]:
                    best = cand
            if best is None:
                continue

            # 爬山微调
            sigma = np.array([max(1.0, best["hw"] * 0.25), max(1.0, best["hh"] * 0.25), 12.0, 0.12])
            for it in range(p.climb_iterations):
                cand = dict(best)
                damp = 1.0 - it / max(1, p.climb_iterations)
                cand["cx"] = best["cx"] + float(self.rng.normal(0.0, sigma[0] * damp))
                cand["cy"] = best["cy"] + float(self.rng.normal(0.0, sigma[1] * damp))
                if p.allow_rotation:
                    cand["rotation"] = (best["rotation"] + float(self.rng.normal(0.0, sigma[2] * damp))) % 180.0
                cand["hw"] = float(np.clip(best["hw"] * (1.0 + self.rng.normal(0.0, sigma[3] * damp)), self.min_half, self.max_half * 2.0))
                cand["hh"] = float(np.clip(best["hh"] * (1.0 + self.rng.normal(0.0, sigma[3] * damp)), self.min_half, self.max_half * 2.0))
                cand["opacity"] = float(np.clip(best["opacity"] + self.rng.normal(0.0, 0.08 * damp), p.alpha_min, p.alpha_max))
                delta, color, _cov = self.evaluate(
                    int(cand["kind"]), cand["cx"], cand["cy"], cand["hw"], cand["hh"], cand["rotation"], cand["opacity"]
                )
                if not math.isfinite(delta):
                    continue
                cand["color"] = [float(c) for c in color]
                cand["delta"] = delta
                if delta < best["delta"]:
                    best = cand

            if best["delta"] >= 0.0 and step > 0:
                # 没有改进：换一个焦点继续，不强行落笔
                continue
            self._commit(best)
            if progress is not None:
                progress(step + 1, len(self.shapes), self.quality())
        return self.shapes
