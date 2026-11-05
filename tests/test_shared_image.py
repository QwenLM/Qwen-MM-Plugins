"""shared.image.smart_resize must keep the resized frame inside the token budget.

``max_pixels`` is the budget preset's token count times TOKEN_SIZE² (see ``budget_to_pixels``), so a
result above it sends the model more visual tokens than the preset allows. The docstring promises
``[min_pixels, max_pixels]``; the qwen-vl-utils reference floors to the patch grid when over the cap
so the bound always holds.
"""

import random

import pytest

from shared.env import IMAGE_BUDGET_TOKENS, IMAGE_MIN_PIXELS, TOKEN_SIZE, VIDEO_BUDGET_TOKENS, VIDEO_MIN_PIXELS
from shared.image import budget_to_pixels, process_image, smart_resize


@pytest.mark.parametrize(
    "height,width,budget",
    [
        (1080, 1920, "normal"),  # a 1080p frame: 768x1376 = 1032 tokens on a 1024-token budget
        (1920, 1080, "normal"),
        (3024, 4032, "large"),  # a 12 MP phone photo
    ],
)
def test_smart_resize_stays_within_image_budget(height, width, budget):
    max_pixels = budget_to_pixels(budget, IMAGE_BUDGET_TOKENS)
    h, w = smart_resize(height, width, IMAGE_MIN_PIXELS, max_pixels)
    assert h % TOKEN_SIZE == 0 and w % TOKEN_SIZE == 0
    assert h * w <= max_pixels, f"{h}x{w} = {h * w // TOKEN_SIZE**2} tokens > {max_pixels // TOKEN_SIZE**2}"


@pytest.mark.parametrize(
    "tokens_map,min_pixels",
    [(IMAGE_BUDGET_TOKENS, IMAGE_MIN_PIXELS), (VIDEO_BUDGET_TOKENS, VIDEO_MIN_PIXELS)],
    ids=["image", "video"],
)
def test_smart_resize_downscale_never_exceeds_max_pixels(tokens_map, min_pixels):
    # Every source larger than the budget must come out at or under it, for every preset.
    rng = random.Random(0)
    over = []
    for budget in tokens_map:
        max_pixels = budget_to_pixels(budget, tokens_map)
        for _ in range(300):
            height, width = rng.randint(64, 4096), rng.randint(64, 4096)
            if height * width <= max_pixels:
                continue
            h, w = smart_resize(height, width, min_pixels, max_pixels)
            if h * w > max_pixels:
                over.append((budget, height, width, h, w))
    assert not over, f"{len(over)} sizes exceed max_pixels, e.g. {over[:3]}"


@pytest.mark.parametrize(
    "height,width,tokens_map,min_pixels",
    [
        (200, 300, IMAGE_BUDGET_TOKENS, IMAGE_MIN_PIXELS),  # 416x640 = 260 tokens on a 256-token budget
        (144, 256, VIDEO_BUDGET_TOKENS, VIDEO_MIN_PIXELS),  # 224x384 = 84 tokens on an 80-token budget
        (180, 320, VIDEO_BUDGET_TOKENS, VIDEO_MIN_PIXELS),
    ],
)
def test_smart_resize_upscale_on_small_preset_stays_within_budget(height, width, tokens_map, min_pixels):
    # The small presets set min_pixels == max_pixels, which the 32-px grid usually cannot hit exactly;
    # growing a small source toward min_pixels must still not go over the budget.
    max_pixels = budget_to_pixels("small", tokens_map)
    assert min_pixels == max_pixels
    h, w = smart_resize(height, width, min_pixels, max_pixels)
    assert h % TOKEN_SIZE == 0 and w % TOKEN_SIZE == 0
    assert h * w <= max_pixels, f"{h}x{w} = {h * w // TOKEN_SIZE**2} tokens > {max_pixels // TOKEN_SIZE**2}"


@pytest.mark.parametrize("height,width", [(20, 2000), (2000, 20), (20, 20000), (20000, 20), (1, 2000)])
@pytest.mark.parametrize(
    "tokens_map,min_pixels",
    [(IMAGE_BUDGET_TOKENS, IMAGE_MIN_PIXELS), (VIDEO_BUDGET_TOKENS, VIDEO_MIN_PIXELS)],
    ids=["image", "video"],
)
def test_smart_resize_keeps_thin_sources_within_budget(height, width, tokens_map, min_pixels):
    max_pixels = budget_to_pixels("small", tokens_map)
    h, w = smart_resize(height, width, min_pixels, max_pixels)
    assert h >= TOKEN_SIZE and w >= TOKEN_SIZE
    assert h % TOKEN_SIZE == 0 and w % TOKEN_SIZE == 0
    assert h * w <= max_pixels


def test_process_image_keeps_a_thin_image_within_budget():
    from PIL import Image

    img = Image.new("RGB", (20000, 20), "white")
    max_pixels = budget_to_pixels("small", IMAGE_BUDGET_TOKENS)
    resized, _, width, height, _ = process_image(img, IMAGE_MIN_PIXELS, max_pixels)
    assert resized.size == (width, height)
    assert width * height <= max_pixels


def test_read_video_keeps_thin_frames_within_budget(tmp_path, requires_ffmpeg):
    import base64
    import io
    import subprocess

    from PIL import Image

    from qwen_mm_plugins_core.readers.video import handle

    path = tmp_path / "thin.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=2000x20:r=2:d=1",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    content = handle({"video_path": str(path), "budget": "small", "max_frames": 2})
    frames = [block for block in content if block.get("type") == "image"]
    assert frames, content
    for frame in frames:
        with Image.open(io.BytesIO(base64.b64decode(frame["data"]))) as image:
            assert image.width * image.height <= VIDEO_BUDGET_TOKENS["small"] * TOKEN_SIZE**2


def test_thin_frame_uses_the_available_patch_budget():
    max_pixels = budget_to_pixels("small", VIDEO_BUDGET_TOKENS)
    assert smart_resize(20, 20000, VIDEO_MIN_PIXELS, max_pixels) == (32, 2560)


def test_smart_resize_rejects_budget_smaller_than_one_patch():
    with pytest.raises(ValueError, match="max_pixels"):
        smart_resize(10, 10, 1, TOKEN_SIZE**2 - 1)


@pytest.mark.parametrize("factor", [0, -1])
def test_smart_resize_rejects_nonpositive_patch_size(factor):
    with pytest.raises(ValueError, match="factor"):
        smart_resize(10, 10, 1, 1024, factor=factor)
