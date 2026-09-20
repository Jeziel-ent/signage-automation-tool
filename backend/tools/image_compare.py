"""Greyscale SSIM between two rendered boards, as a second, independent
signal alongside the object-geometry diff % - two images can look nearly
identical to a human while our object-matching reports a large "diff" (a
few badly-matched fragments among ~130 packed curves), or vice versa
(everything matches within tolerance but a colour/font substitution makes
it look wrong) - this catches what the geometry comparison can't.

Windowed SSIM (Wang et al. 2004) via scipy.ndimage.uniform_filter, on
images downscaled to the same small size first (fast, and matches the
"perceptual similarity at a glance" the metric name suggests - not meant to
catch pixel-level differences).
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter

DOWNSCALE_WIDTH = 300


def load_gray_downscaled(path, width: int = DOWNSCALE_WIDTH) -> np.ndarray:
    img = Image.open(path).convert("L")
    w, h = img.size
    new_h = max(1, round(h * width / w))
    img = img.resize((width, new_h), Image.LANCZOS)
    return np.asarray(img, dtype=np.float64)


def ssim(a: np.ndarray, b: np.ndarray, window: int = 7) -> float:
    """Global-average windowed SSIM, images already the same size."""
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a, b = a[:h, :w], b[:h, :w]

    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2

    mu_a = uniform_filter(a, window)
    mu_b = uniform_filter(b, window)
    mu_a2, mu_b2, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b

    sigma_a2 = uniform_filter(a * a, window) - mu_a2
    sigma_b2 = uniform_filter(b * b, window) - mu_b2
    sigma_ab = uniform_filter(a * b, window) - mu_ab

    numerator = (2 * mu_ab + C1) * (2 * sigma_ab + C2)
    denominator = (mu_a2 + mu_b2 + C1) * (sigma_a2 + sigma_b2 + C2)
    return float(np.mean(numerator / denominator))


def ssim_between_files(path_a, path_b, width: int = DOWNSCALE_WIDTH) -> float:
    return ssim(load_gray_downscaled(path_a, width), load_gray_downscaled(path_b, width))
