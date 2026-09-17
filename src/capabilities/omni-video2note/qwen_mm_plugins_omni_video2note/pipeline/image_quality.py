"""Deterministic Pillow-based frame quality and near-duplicate metrics."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL.Image import Image
else:
    Image = Any


@dataclass(frozen=True)
class QualityMetrics:
    width: int
    height: int
    brightness: float
    contrast: float
    dark_clip: float
    bright_clip: float
    laplacian_variance: float
    edge_density: float
    entropy: float
    score: float
    reject_reasons: tuple[str, ...]
    dhash: str
    histogram: tuple[float, ...]

    @property
    def grid_entropy(self) -> float:
        return self.entropy

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reject_reasons"] = list(self.reject_reasons)
        value["histogram"] = list(self.histogram)
        return value


def _entropy(values: list[int]) -> float:
    if not values:
        return 0.0
    bins = [0] * 16
    for value in values:
        bins[min(15, value // 16)] += 1
    total = len(values)
    result = 0.0
    for count in bins:
        if count:
            probability = count / total
            result -= probability * math.log2(probability)
    return result / 4.0


def _laplacian_and_edges(gray: Image) -> tuple[float, float]:
    from PIL import Image as PillowImage

    sample = gray.copy()
    sample.thumbnail((320, 180), PillowImage.Resampling.LANCZOS)
    pixels = list(sample.getdata())
    width, height = sample.size
    if width < 3 or height < 3:
        return 0.0, 0.0
    responses: list[int] = []
    edge_count = 0
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            index = row + x
            response = abs(
                4 * pixels[index]
                - pixels[index - 1]
                - pixels[index + 1]
                - pixels[index - width]
                - pixels[index + width]
            )
            responses.append(response)
            edge_count += response >= 32
    mean = sum(responses) / len(responses)
    variance = sum((response - mean) ** 2 for response in responses) / len(responses)
    return variance, edge_count / len(responses)


def _grid_entropy(gray: Image) -> float:
    from PIL import Image as PillowImage

    sample = gray.copy()
    sample.thumbnail((256, 256), PillowImage.Resampling.LANCZOS)
    width, height = sample.size
    pixels = list(sample.getdata())
    values = []
    for grid_y in range(4):
        for grid_x in range(4):
            x0, x1 = grid_x * width // 4, (grid_x + 1) * width // 4
            y0, y1 = grid_y * height // 4, (grid_y + 1) * height // 4
            cell = [pixels[y * width + x] for y in range(y0, y1) for x in range(x0, x1)]
            values.append(_entropy(cell))
    return sum(values) / len(values) if values else 0.0


def dhash(image: Image, size: int = 8) -> str:
    from PIL import Image as PillowImage

    if size < 2:
        raise ValueError("dHash size must be at least 2")
    gray = image.convert("L").resize((size + 1, size), PillowImage.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    bits = 0
    for y in range(size):
        for x in range(size):
            left = pixels[y * (size + 1) + x]
            right = pixels[y * (size + 1) + x + 1]
            bits = (bits << 1) | int(left > right)
    return f"{bits:0{(size * size + 3) // 4}x}"


def normalized_histogram(image: Image) -> tuple[float, ...]:
    from PIL import Image as PillowImage

    reduced = image.convert("RGB").resize((64, 64), PillowImage.Resampling.BILINEAR)
    raw = reduced.histogram()
    grouped: list[int] = []
    for channel in range(3):
        section = raw[channel * 256 : (channel + 1) * 256]
        grouped.extend(sum(section[index : index + 16]) for index in range(0, 256, 16))
    total = sum(grouped) or 1
    return tuple(value / total for value in grouped)


def hamming_distance(left: str, right: str) -> int:
    if len(left) != len(right) or not left:
        raise ValueError("dHash values must be non-empty and equally sized")
    return (int(left, 16) ^ int(right, 16)).bit_count()


def histogram_similarity(left: tuple[float, ...] | list[float], right: tuple[float, ...] | list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(min(a, b) for a, b in zip(left, right))


def are_near_duplicates(
    left: QualityMetrics,
    right: QualityMetrics,
    *,
    hash_distance: int = 6,
    histogram_threshold: float = 0.94,
) -> bool:
    if hash_distance < 0 or not 0 <= histogram_threshold <= 1:
        raise ValueError("invalid near-duplicate thresholds")
    return (
        hamming_distance(left.dhash, right.dhash) <= hash_distance
        and histogram_similarity(left.histogram, right.histogram) >= histogram_threshold
    )


def evaluate_image(
    path: str | Path,
    *,
    min_width: int = 320,
    min_height: int = 180,
    min_score: float = 0.35,
) -> QualityMetrics:
    """Measure exposure, contrast, clipping, sharpness, edges, entropy, and similarity features."""
    from PIL import Image as PillowImage
    from PIL import ImageStat

    source = Path(path)
    if min_width < 1 or min_height < 1 or not 0 <= min_score <= 1:
        raise ValueError("invalid image quality thresholds")
    with PillowImage.open(source) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        gray = image.convert("L")
        sample = gray.copy()
        sample.thumbnail((320, 180), PillowImage.Resampling.LANCZOS)
        values = list(sample.getdata())
        statistics = ImageStat.Stat(sample)
        brightness = float(statistics.mean[0])
        contrast = float(statistics.stddev[0])
        dark_clip = sum(value <= 5 for value in values) / max(1, len(values))
        bright_clip = sum(value >= 250 for value in values) / max(1, len(values))
        laplacian_variance, edge_density = _laplacian_and_edges(gray)
        entropy = _grid_entropy(gray)
        sharpness = min(1.0, laplacian_variance / 1200.0)
        exposure = max(0.0, 1.0 - abs(brightness - 127.5) / 127.5)
        contrast_score = min(1.0, contrast / 55.0)
        edge_score = min(1.0, edge_density / 0.18)
        information = max(0.0, 1.0 - abs(entropy - 0.62) / 0.62)
        score = (
            0.35 * sharpness
            + 0.22 * exposure
            + 0.18 * contrast_score
            + 0.15 * edge_score
            + 0.10 * information
        )
        reasons = []
        if width < min_width or height < min_height:
            reasons.append("resolution")
        if brightness < 18 or dark_clip > 0.92:
            reasons.append("too_dark")
        if brightness > 238 or bright_clip > 0.92:
            reasons.append("too_bright")
        if laplacian_variance < 18:
            reasons.append("blur")
        if score < min_score:
            reasons.append("low_score")
        return QualityMetrics(
            width=width,
            height=height,
            brightness=round(brightness, 6),
            contrast=round(contrast, 6),
            dark_clip=round(dark_clip, 6),
            bright_clip=round(bright_clip, 6),
            laplacian_variance=round(laplacian_variance, 6),
            edge_density=round(edge_density, 6),
            entropy=round(entropy, 6),
            score=round(score, 6),
            reject_reasons=tuple(sorted(set(reasons))),
            dhash=dhash(image),
            histogram=normalized_histogram(image),
        )


histogram = normalized_histogram
