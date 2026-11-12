"""Tests for workers/ocr/preprocessor.py — uses synthetic Pillow images, no real screenshots."""

import io

import numpy as np
import pytest
from PIL import Image

from workers.ocr.preprocessor import preprocess_transcript_image


def _make_jpeg(width: int, height: int, color=(128, 128, 128)) -> bytes:
    """Return JPEG bytes for a solid-colour image."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_png(width: int, height: int, color=(200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Return type and shape
# ---------------------------------------------------------------------------

class TestReturnType:
    def test_returns_ndarray(self):
        result = preprocess_transcript_image(_make_jpeg(800, 600))
        assert isinstance(result, np.ndarray)

    def test_dtype_is_uint8(self):
        result = preprocess_transcript_image(_make_jpeg(800, 600))
        assert result.dtype == np.uint8

    def test_output_is_2d(self):
        result = preprocess_transcript_image(_make_jpeg(800, 600))
        assert result.ndim == 2

    def test_accepts_jpeg_bytes(self):
        result = preprocess_transcript_image(_make_jpeg(1000, 800))
        assert isinstance(result, np.ndarray)

    def test_accepts_png_bytes(self):
        result = preprocess_transcript_image(_make_png(1000, 800))
        assert isinstance(result, np.ndarray)


# ---------------------------------------------------------------------------
# Upscaling behaviour
# ---------------------------------------------------------------------------

class TestUpscaling:
    def test_narrow_image_upscaled_to_1200_wide(self):
        result = preprocess_transcript_image(_make_jpeg(600, 400))
        assert result.shape[1] == 1200

    def test_height_scaled_proportionally(self):
        # 600×400 → 1200×800 (scale = 2)
        result = preprocess_transcript_image(_make_jpeg(600, 400))
        assert result.shape[0] == 800

    def test_image_at_exactly_1200_not_upscaled(self):
        result = preprocess_transcript_image(_make_jpeg(1200, 900))
        assert result.shape[1] == 1200

    def test_wide_image_not_scaled_down(self):
        result = preprocess_transcript_image(_make_jpeg(1600, 900))
        assert result.shape[1] == 1600


# ---------------------------------------------------------------------------
# Binary thresholding
# ---------------------------------------------------------------------------

class TestBinaryOutput:
    def test_output_contains_only_0_and_255(self):
        result = preprocess_transcript_image(_make_jpeg(1200, 100))
        unique = set(np.unique(result))
        assert unique.issubset({0, 255})

    def test_mixed_image_produces_both_values(self):
        # Left half black, right half white → mean ≈ 127 → white pixels → 255, black → 0
        buf = io.BytesIO()
        img = Image.new("RGB", (1200, 100), (0, 0, 0))
        # Paint right half white
        white_patch = Image.new("RGB", (600, 100), (255, 255, 255))
        img.paste(white_patch, (600, 0))
        img.save(buf, format="PNG")
        result = preprocess_transcript_image(buf.getvalue())
        assert 0 in np.unique(result)
        assert 255 in np.unique(result)
